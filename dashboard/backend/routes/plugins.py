"""Plugins API — install, uninstall, list, health, widget endpoints.

ADR-5: install uses a state-machine with per-slug fcntl lock and crash recovery.
B1 (Raven): rollback_from_state() reverses completed steps on any failure.
B2 (Raven): InstallLock prevents concurrent installs of the same plugin.
Vault C5 (F5): widget serving uses os.path.realpath + startswith containment.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, jsonify, request, send_file
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

bp = Blueprint("plugins", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
PLUGINS_DIR = WORKSPACE / "plugins"
DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _WidgetLimitError(Exception):
    """Raised when a plugin would exceed the per-mount_point widget limit (AC27)."""


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn: sqlite3.Connection, plugin_id: str, action: str, payload: Any = None, success: bool = True) -> None:
    """Write a row to plugin_audit_log."""
    try:
        conn.execute(
            "INSERT INTO plugin_audit_log (plugin_id, action, payload, success, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (plugin_id, action, json.dumps(payload) if payload is not None else None,
             1 if success else 0, _now_iso()),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)


def _plugin_to_dict(row: sqlite3.Row) -> dict:
    """Serialize a plugins_installed row to a dict.

    Wave 2.0: adds ``icon_url`` derived from ``manifest.metadata.icon`` when
    present.  URL follows the existing /plugins/<slug>/ui/<path> pattern.
    """
    d = dict(row)
    # Derive icon_url from manifest metadata (Wave 2.0, additive)
    try:
        manifest = json.loads(d.get("manifest_json") or "{}")
        metadata = manifest.get("metadata") or {}
        icon_path = metadata.get("icon")
        if icon_path and isinstance(icon_path, str) and icon_path.startswith("ui/"):
            slug = d.get("slug", "")
            # icon_path is e.g. "ui/assets/icon.png"; strip the "ui/" prefix for URL
            d["icon_url"] = f"/plugins/{slug}/ui/{icon_path[3:]}"
        else:
            d["icon_url"] = None
    except Exception:
        d["icon_url"] = None
    return d


# ---------------------------------------------------------------------------
# Seed host rows on plugin install: goals / tasks / triggers capabilities.
# Each YAML is optional — a plugin that doesn't ship one of these just skips
# the corresponding pass. All rows inserted here carry `source_plugin` so
# uninstall can delete them with a single DELETE WHERE source_plugin = ?.
# ---------------------------------------------------------------------------

def _seed_plugin_host_rows(conn: sqlite3.Connection, slug: str, plugin_dir: Path) -> None:
    import yaml

    def _load_yaml(name: str) -> dict:
        path = plugin_dir / name / f"{name}.yaml"
        if not path.is_file():
            return {}
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("Plugin %s: failed to parse %s: %s", slug, name, exc)
            return {}

    # ---- goals/goals.yaml: mission + projects + goals ----
    goals_spec = _load_yaml("goals")
    now = _now_iso()

    # Anchor mission — one per plugin that ships seed goals. The /goals
    # frontend only walks mission → project → goal, so orphan projects
    # (mission_id IS NULL) stay invisible. We auto-create a mission named
    # after the plugin unless the YAML explicitly declares missions.
    mission_id: int | None = None
    declared_missions = goals_spec.get("missions") or []
    if goals_spec.get("projects") or goals_spec.get("goals"):
        if declared_missions:
            # Use the first declared mission as the anchor for orphan projects
            m = declared_missions[0]
            mslug = m.get("slug") or f"{slug}-root"
            namespaced_m = f"plugin-{slug}-{mslug}" if not mslug.startswith(f"plugin-{slug}-") else mslug
            conn.execute(
                "INSERT OR IGNORE INTO missions "
                "(slug, title, description, target_metric, target_value, current_value, "
                " due_date, status, created_at, updated_at, source_plugin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    namespaced_m,
                    m.get("title") or m.get("name"),
                    m.get("description"),
                    m.get("target_metric"),
                    float(m.get("target_value") or 0),
                    float(m.get("current_value") or 0),
                    m.get("due_date"),
                    m.get("status") or "active",
                    now, now, slug,
                ),
            )
            row = conn.execute("SELECT id FROM missions WHERE slug = ?", (namespaced_m,)).fetchone()
            if row:
                mission_id = row["id"]
        else:
            # No mission declared — synthesize one so goals render in the UI
            anchor_slug = f"plugin-{slug}-root"
            conn.execute(
                "INSERT OR IGNORE INTO missions "
                "(slug, title, description, target_metric, target_value, current_value, "
                " status, created_at, updated_at, source_plugin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    anchor_slug,
                    f"{slug} — plugin goals",
                    f"Auto-created by the {slug} plugin to group its seed projects. "
                    f"Deletes on uninstall.",
                    None, 0, 0, "active",
                    now, now, slug,
                ),
            )
            row = conn.execute("SELECT id FROM missions WHERE slug = ?", (anchor_slug,)).fetchone()
            if row:
                mission_id = row["id"]
        conn.commit()

    project_slug_to_id: dict[str, int] = {}
    for proj in goals_spec.get("projects", []) or []:
        pslug = proj.get("slug") or proj.get("id")
        title = proj.get("title") or proj.get("name")
        if not pslug or not title:
            continue
        namespaced = f"plugin-{slug}-{pslug}" if not pslug.startswith(f"plugin-{slug}-") else pslug
        # Prefer YAML-declared mission_id; fall back to the auto mission anchor
        proj_mission_id = proj.get("mission_id") if proj.get("mission_id") is not None else mission_id
        conn.execute(
            "INSERT OR IGNORE INTO projects "
            "(slug, mission_id, title, description, status, created_at, updated_at, source_plugin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                namespaced,
                proj_mission_id,
                title,
                proj.get("description"),
                proj.get("status") or "active",
                now, now, slug,
            ),
        )
        row = conn.execute("SELECT id FROM projects WHERE slug = ?", (namespaced,)).fetchone()
        if row:
            project_slug_to_id[pslug] = row["id"]
    conn.commit()

    goal_slug_to_id: dict[str, int] = {}
    for g in goals_spec.get("goals", []) or []:
        gslug = g.get("slug") or g.get("id")
        title = g.get("title")
        proj_ref = g.get("project_slug") or g.get("project_id")
        project_id = project_slug_to_id.get(proj_ref) if isinstance(proj_ref, str) else proj_ref
        if not gslug or not title or not project_id:
            continue
        namespaced = f"plugin-{slug}-{gslug}" if not gslug.startswith(f"plugin-{slug}-") else gslug
        conn.execute(
            "INSERT OR IGNORE INTO goals "
            "(slug, project_id, title, description, target_metric, metric_type, "
            " target_value, current_value, due_date, status, created_at, updated_at, source_plugin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                namespaced,
                project_id,
                title,
                g.get("description"),
                g.get("target_metric"),
                g.get("metric_type") or "count",
                float(g.get("target_value") or 0),
                float(g.get("current_value") or 0),
                g.get("due_date"),
                g.get("status") or "active",
                now, now, slug,
            ),
        )
        row = conn.execute("SELECT id FROM goals WHERE slug = ?", (namespaced,)).fetchone()
        if row:
            goal_slug_to_id[gslug] = row["id"]
    conn.commit()

    # ---- tasks/tasks.yaml: tickets ----
    tasks_spec = _load_yaml("tasks")
    for t in tasks_spec.get("tasks", []) or []:
        title = t.get("title")
        if not title:
            continue
        assignee = t.get("assignee_agent")
        # Auto-prefix the agent slug if the plugin declared a bare name that
        # belongs to this plugin. Matches the file_ops convention.
        if isinstance(assignee, str) and assignee and not assignee.startswith("plugin-"):
            candidate = f"plugin-{slug}-{assignee}"
            if (WORKSPACE / ".claude" / "agents" / f"{candidate}.md").exists():
                assignee = candidate

        goal_ref = t.get("goal_slug") or t.get("goal_id")
        goal_id = goal_slug_to_id.get(goal_ref) if isinstance(goal_ref, str) else goal_ref

        priority = t.get("priority") or "medium"
        priority_rank = {"urgent": 4, "high": 3, "medium": 2, "low": 1}.get(priority, 2)

        import uuid as _uuid
        ticket_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT OR IGNORE INTO tickets "
            "(id, title, description, status, priority, priority_rank, "
            " goal_id, assignee_agent, created_by, created_at, updated_at, source_plugin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                title,
                t.get("description"),
                t.get("status") or "open",
                priority,
                priority_rank,
                goal_id,
                assignee,
                f"plugin:{slug}",
                now, now, slug,
            ),
        )
    conn.commit()

    # ---- triggers/triggers.yaml: triggers (disabled by default for safety) ----
    triggers_spec = _load_yaml("triggers")
    for tr in triggers_spec.get("triggers", []) or []:
        tr_slug = tr.get("slug") or tr.get("id")
        name = tr.get("name") or tr_slug
        if not tr_slug or not name:
            continue
        namespaced = f"plugin-{slug}-{tr_slug}" if not tr_slug.startswith(f"plugin-{slug}-") else tr_slug
        import secrets as _secrets
        conn.execute(
            "INSERT OR IGNORE INTO triggers "
            "(name, slug, type, source, event_filter, action_type, action_payload, "
            " agent, secret, enabled, from_yaml, source_plugin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                namespaced,
                tr.get("type") or "webhook",
                tr.get("source") or "webhook",
                json.dumps(tr.get("event_filter")) if tr.get("event_filter") is not None else None,
                tr.get("action_type") or "skill",
                json.dumps(tr.get("action_payload") or {}),
                tr.get("agent"),
                tr.get("secret") or _secrets.token_urlsafe(32),
                # Safety default: disabled so the user has to review and enable explicitly.
                1 if tr.get("enabled") is True else 0,
                1,  # from_yaml
                slug,
                now, now,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# GET /api/plugins — list installed plugins
# ---------------------------------------------------------------------------

@bp.route("/api/plugins", methods=["GET"])
@login_required
def list_plugins():
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM plugins_installed ORDER BY installed_at DESC"
        ).fetchall()
        return jsonify([_plugin_to_dict(r) for r in rows])
    except sqlite3.OperationalError as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/plugins/<slug> — plugin detail
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>", methods=["GET"])
@login_required
def get_plugin(slug: str):
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM plugins_installed WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Plugin not found"}), 404
        return jsonify(_plugin_to_dict(row))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/plugins/<slug>/audit — recent audit entries for a plugin
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>/audit", methods=["GET"])
@login_required
def get_plugin_audit(slug: str):
    conn = _get_db()
    try:
        # Table may not exist on fresh install; treat absence as empty list.
        try:
            rows = conn.execute(
                "SELECT id, action, success, created_at, payload "
                "FROM plugins_audit WHERE plugin_id = ? "
                "ORDER BY created_at DESC LIMIT 100",
                (slug,),
            ).fetchall()
        except sqlite3.OperationalError:
            return jsonify([])
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /api/plugins/preview — validate without installing
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/preview", methods=["POST"])
@login_required
def preview_plugin():
    data = request.get_json(force=True, silent=True) or {}
    source_url = data.get("source_url", "")
    auth_token = data.get("auth_token") or None
    if not source_url:
        return jsonify({"error": "source_url required"}), 400

    from plugin_loader import PluginInstaller

    installer = PluginInstaller()
    try:
        preview = installer.preview(source_url, auth_token=auth_token)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    # `staged_path` is a pathlib.Path used internally by the scan pipeline;
    # coerce to string before returning so Flask's JSON encoder accepts it.
    if "staged_path" in preview and preview["staged_path"] is not None:
        preview["staged_path"] = str(preview["staged_path"])

    if preview.get("conflicts"):
        return jsonify(preview), 409

    return jsonify(preview)


# ---------------------------------------------------------------------------
# POST /api/plugins/upload — upload and stage a plugin archive (.zip / .tar.gz)
# Returns a temporary source_path that can be passed to /preview and /install
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/upload", methods=["POST"])
@login_required
def upload_plugin_archive():
    """Accept a multipart upload (.zip/.tar.gz), extract into staging, return the path.

    The caller then passes `source_url: <returned_path>` to /preview and /install.
    """
    import time
    import secrets

    if "file" not in request.files:
        return jsonify({"error": "file field required"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400

    # Size cap — 20MB — matches reasonable plugin scope
    max_bytes = 20 * 1024 * 1024
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > max_bytes:
        return jsonify({"error": f"archive too large ({size} bytes, max {max_bytes})"}), 413

    staging_slug = f"upload-{int(time.time())}-{secrets.token_hex(4)}"

    from plugin_loader import PluginInstaller
    try:
        extracted = PluginInstaller.extract_uploaded_archive(f, staging_slug)
    except ValueError as exc:
        return jsonify({"error": "invalid_format", "message": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": "extract_failed", "message": str(exc)}), 500

    return jsonify({
        "source_path": str(extracted),
        "staging_slug": staging_slug,
        "filename": f.filename,
    })


# ---------------------------------------------------------------------------
# Wave 2.5 — POST /api/plugins/scan — synchronous security scan (≤25s)
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/scan", methods=["POST"])
@login_required
def scan_plugin():
    """Run a hybrid regex+LLM security scan on a plugin source URL.

    Request body: {source_url: str, auth_token?: str}

    Returns ADR §5 verdict envelope:
    {verdict, severity, scan_duration_ms, scanners_used, cache_hit,
     tarball_sha256, findings, findings_truncated, llm_used, llm_reasoning,
     scanner_version}

    Timeout: 25s hard. If LLM exceeds 20s, degrades to regex-only (not failure).
    """
    data = request.get_json(force=True, silent=True) or {}
    source_url = data.get("source_url", "")
    auth_token = data.get("auth_token") or None
    if not source_url:
        return jsonify({"error": "source_url required"}), 400

    from plugin_loader import PluginInstaller

    installer = PluginInstaller()

    try:
        preview = installer.preview(source_url, auth_token=auth_token)
    except Exception as exc:
        return jsonify({"error": f"preview failed: {exc}"}), 400

    if preview.get("conflicts"):
        return jsonify({"error": "conflict", "details": preview["conflicts"]}), 409

    staged_path = preview.get("staged_path")
    if staged_path is None:
        return jsonify({"error": "staged_path not available after preview"}), 500
    if not isinstance(staged_path, Path):
        staged_path = Path(staged_path)

    manifest = preview.get("manifest") or {}
    tarball_sha256 = preview.get("tarball_sha256", "")

    from plugin_scan_runner import run_scan

    try:
        result = run_scan(
            staged_path=staged_path,
            manifest=manifest,
            tarball_sha256=tarball_sha256,
            db_path=DB_PATH,
        )
    except Exception as exc:
        logger.error("Scan failed for %s: %s", source_url, exc)
        return jsonify({"error": f"scan_failed: {exc}"}), 500
    finally:
        # Always clean up staged directory after scan — it was created solely for
        # inspection and must not accumulate between requests.
        try:
            from plugin_loader import STAGING_DIR as _STAGING_DIR
            if staged_path.exists() and staged_path.resolve().is_relative_to(
                _STAGING_DIR.resolve()
            ):
                shutil.rmtree(staged_path, ignore_errors=True)
        except Exception:
            pass  # cleanup failure must never mask scan result

    # Log the scan event to audit log
    _audit_scan_event(
        slug=manifest.get("id", "unknown"),
        event="scan_completed",
        verdict=result.get("verdict"),
        actor_user_id=getattr(current_user, "id", None),
        actor_username=getattr(current_user, "username", None),
        detail={"tarball_sha256": tarball_sha256, "scanners_used": result.get("scanners_used")},
    )

    return jsonify(result)


# ---------------------------------------------------------------------------
# Wave 2.5 — GET /api/plugins/audit — audit log for plugin scan decisions
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/audit", methods=["GET"])
@login_required
def get_plugin_audit_log():
    """Return plugin audit log entries.

    Query params:
      slug     — filter by plugin slug (optional)
      limit    — max rows (default 100, max 500)

    Admin: sees all rows.
    Non-admin: sees only own rows (WHERE actor_username = current_user.username).
    """
    slug = request.args.get("slug")
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except (ValueError, TypeError):
        limit = 100

    conn = _get_db()
    try:
        is_admin = getattr(current_user, "role", "viewer") == "admin"
        params: list = []
        where_clauses: list[str] = []

        if slug:
            where_clauses.append("slug = ?")
            params.append(slug)

        if not is_admin:
            where_clauses.append("actor_username = ?")
            params.append(getattr(current_user, "username", ""))

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        params.append(limit)

        rows = conn.execute(
            f"""SELECT id, slug, event, verdict, actor_user_id, actor_username,
                       detail_json, created_at
                FROM plugin_audit_log
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?""",
            params,
        ).fetchall()

        return jsonify({
            "entries": [
                {
                    "id": r["id"],
                    "slug": r["slug"],
                    "event": r["event"],
                    "verdict": r["verdict"],
                    "actor_user_id": r["actor_user_id"],
                    "actor_username": r["actor_username"],
                    "detail": json.loads(r["detail_json"] or "{}"),
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "total": len(rows),
        })
    finally:
        conn.close()


def _audit_scan_event(
    slug: str,
    event: str,
    verdict: str | None,
    actor_user_id: int | None,
    actor_username: str | None,
    detail: dict | None = None,
) -> None:
    """Insert a row into plugin_audit_log. Silently swallows errors."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO plugin_audit_log
               (slug, event, verdict, actor_user_id, actor_username, detail_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                slug,
                event,
                verdict,
                actor_user_id,
                actor_username,
                json.dumps(detail or {}),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Audit log insert failed: %s", exc)


# ---------------------------------------------------------------------------
# POST /api/plugins/install — full install with state machine
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/install", methods=["POST"])
@login_required
def install_plugin():
    data = request.get_json(force=True, silent=True) or {}
    source_url = data.get("source_url", "")
    auth_token = data.get("auth_token") or None
    if not source_url:
        return jsonify({"error": "source_url required"}), 400

    from plugin_loader import PluginInstaller, ConflictError, VersionError
    from plugin_file_ops import (
        copy_with_manifest, append_rules_index, write_manifest,
        register_in_place_asset,
    )
    from plugin_migrator import install_plugin_sql, MigrationError
    from plugin_hook_runner import run_lifecycle_hook, LifecycleHookError
    from plugin_install_state import (
        InstallLock, save_state, finalize_install, rollback_from_state
    )
    from heartbeat_schema import load_heartbeats_yaml
    from plugin_loader import _reload_scheduler

    installer = PluginInstaller()

    # Preview / validate first
    try:
        preview = installer.preview(source_url, auth_token=auth_token)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    if preview.get("conflicts"):
        return jsonify({"error": "conflict", "details": preview["conflicts"]}), 409
    if not preview.get("version_ok", True):
        return jsonify({"error": "version_incompatible", "details": preview["conflicts"]}), 409

    manifest = preview["manifest"]
    slug = manifest["id"]

    # -----------------------------------------------------------------------
    # Wave 2.5 — Security scan gate (inserted between preview and InstallLock)
    # -----------------------------------------------------------------------
    skip_scan = bool(data.get("skip_scan", False))
    confirmed_verdict = data.get("confirmed_verdict")      # "WARN" when user confirmed
    override_reason = data.get("override_reason", "") or "" # admin BLOCK override

    staged_path = preview.get("staged_path")
    tarball_sha256 = preview.get("tarball_sha256", "")

    actor_uid = getattr(current_user, "id", None)
    actor_uname = getattr(current_user, "username", None)
    actor_role = getattr(current_user, "role", "viewer")

    if skip_scan:
        # Admin-only skip
        if actor_role != "admin":
            return jsonify({"error": "scan_skip_forbidden", "detail": "Only admin can skip security scan"}), 403
        _audit_scan_event(
            slug=slug, event="scan_skipped", verdict="SKIPPED",
            actor_user_id=actor_uid, actor_username=actor_uname,
            detail={"reason": data.get("skip_reason", ""), "source_url": source_url},
        )
    else:
        if staged_path is not None:
            from plugin_scan_runner import run_scan as _run_scan
            try:
                scan_result = _run_scan(
                    staged_path=staged_path if isinstance(staged_path, Path) else Path(staged_path),
                    manifest=manifest,
                    tarball_sha256=tarball_sha256,
                    db_path=DB_PATH,
                )
            except Exception as scan_exc:
                logger.error("Security scan crashed for '%s': %s", slug, scan_exc)
                return jsonify({"error": "scan_failed", "detail": str(scan_exc)}), 500

            scan_verdict = scan_result.get("verdict", "APPROVE")

            if scan_verdict == "BLOCK":
                # Admin may override
                if override_reason and len(override_reason.strip()) >= 20 and actor_role == "admin":
                    _audit_scan_event(
                        slug=slug, event="scan_override", verdict="BLOCK",
                        actor_user_id=actor_uid, actor_username=actor_uname,
                        detail={"override_reason": override_reason, "findings": len(scan_result.get("findings", []))},
                    )
                elif actor_role != "admin":
                    _audit_scan_event(
                        slug=slug, event="scan_blocked", verdict="BLOCK",
                        actor_user_id=actor_uid, actor_username=actor_uname,
                        detail={"findings": len(scan_result.get("findings", []))},
                    )
                    findings_summary = {
                        s: sum(1 for f in scan_result.get("findings", []) if f.get("severity") == s)
                        for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
                    }
                    return jsonify({
                        "error": "security_block",
                        "verdict": "BLOCK",
                        "findings_summary": findings_summary,
                        "override_allowed": False,
                        "message": "Plugin blocked by security scan. Contact an admin to override.",
                    }), 422
                else:
                    # Admin, no override_reason or too short
                    findings_summary = {
                        s: sum(1 for f in scan_result.get("findings", []) if f.get("severity") == s)
                        for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
                    }
                    return jsonify({
                        "error": "security_block",
                        "verdict": "BLOCK",
                        "findings_summary": findings_summary,
                        "override_allowed": True,
                        "message": "Plugin blocked by security scan. Provide override_reason (≥20 chars) to proceed as admin.",
                    }), 422

            elif scan_verdict == "WARN":
                if confirmed_verdict != "WARN":
                    return jsonify({
                        "error": "confirmation_required",
                        "verdict": "WARN",
                        "message": "Security scan produced warnings. Re-submit with confirmed_verdict='WARN' to proceed.",
                        "findings": scan_result.get("findings", []),
                    }), 409
                _audit_scan_event(
                    slug=slug, event="scan_warn_accepted", verdict="WARN",
                    actor_user_id=actor_uid, actor_username=actor_uname,
                    detail={"findings": len(scan_result.get("findings", []))},
                )

            else:
                # APPROVE
                _audit_scan_event(
                    slug=slug, event="scan_approved", verdict="APPROVE",
                    actor_user_id=actor_uid, actor_username=actor_uname,
                    detail={"cache_hit": scan_result.get("cache_hit", False)},
                )
        else:
            # preview() always sets staged_path for github:/https:/upload flows,
            # so reaching this branch means something went wrong upstream.
            return jsonify({
                "error": "staged_path_missing",
                "detail": "Plugin source was not staged — cannot run security scan.",
            }), 500
    # -----------------------------------------------------------------------
    # End Wave 2.5 security scan gate
    # -----------------------------------------------------------------------

    # ADR-5 + B2: per-slug lock
    try:
        lock = InstallLock(slug)
        lock.__enter__()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409

    plugin_dir = PLUGINS_DIR / slug
    state: dict[str, Any] = {
        "slug": slug,
        "source_url": source_url,
        "started_at": _now_iso(),
        "completed_steps": [],
    }
    conn = _get_db()

    try:
        # --- Step: copy plugin source to plugins/{slug}/ ---
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # Wave 2.5 — use staged_path from preview() (single-resolve invariant).
        # preview() already downloaded + extracted the source; re-using staged_path
        # avoids a second download. The scan gate above guarantees staged_path
        # is set before we reach this point.
        source_path = preview.get("staged_path")
        if not isinstance(source_path, Path):
            from pathlib import Path as _Path
            source_path = _Path(source_path)

        if source_path.is_dir():
            shutil.copytree(source_path, plugin_dir, dirs_exist_ok=True)
        else:
            return jsonify({"error": f"resolved source is not a directory: {source_path}"}), 400

        # Wave 2.5 — clean up staging dir after successful copy so disk space
        # is not held.  Staging dir lives at PLUGINS_DIR/.staging/<slug>/.
        # Only remove if it is inside STAGING_DIR (guard against local-dir sources).
        try:
            from plugin_loader import STAGING_DIR as _STAGING_DIR
            if source_path.resolve().is_relative_to(_STAGING_DIR.resolve()):
                shutil.rmtree(_STAGING_DIR / slug, ignore_errors=True)
        except Exception as _cleanup_exc:
            logger.debug("Staging cleanup skipped: %s", _cleanup_exc)

        state["completed_steps"].append({"step": "copy_source"})
        save_state(slug, state)

        # --- Step: pre-install hook ---
        pre_hook = plugin_dir / "hooks" / "pre-install.sh"
        if pre_hook.exists():
            try:
                run_lifecycle_hook(plugin_dir, "pre-install", timeout=60)
            except LifecycleHookError as exc:
                raise RuntimeError(f"pre-install hook failed: {exc}") from exc
        state["completed_steps"].append({"step": "pre_install_hook"})
        save_state(slug, state)

        # --- Step: SQL migrations ---
        # Prefer `migrations/install.sql` (canonical). Fall back to the
        # lexically-first `migrations/*.sql` file so plugin authors using the
        # common `NNN_description.sql` convention (e.g. `001_create_tables.sql`)
        # don't have to rename — keeps friction low for community plugins.
        migrations_dir = plugin_dir / "migrations"
        install_sql_path = migrations_dir / "install.sql"
        if not install_sql_path.exists() and migrations_dir.is_dir():
            candidates = sorted(migrations_dir.glob("*.sql"))
            if candidates:
                install_sql_path = candidates[0]
        if install_sql_path.exists():
            try:
                conn2 = sqlite3.connect(str(DB_PATH))
                install_plugin_sql(slug, install_sql_path, conn=conn2)
                conn2.close()
            except MigrationError as exc:
                raise RuntimeError(f"SQL migration failed: {exc}") from exc
        state["completed_steps"].append({"step": "sql_migrations"})
        save_state(slug, state)

        # --- Step: copy agents ---
        agents_src = plugin_dir / "agents"
        agents_dst = WORKSPACE / ".claude" / "agents"
        agent_files: list[dict] = []
        if agents_src.exists():
            copy_with_manifest(agents_src, agents_dst, slug, "agents", agent_files)
        state["completed_steps"].append({"step": "copy_agents", "copied_files": agent_files})
        save_state(slug, state)

        # --- Step: copy skills ---
        skills_src = plugin_dir / "skills"
        skills_dst = WORKSPACE / ".claude" / "skills"
        skill_files: list[dict] = []
        if skills_src.exists():
            copy_with_manifest(skills_src, skills_dst, slug, "skills", skill_files)
        state["completed_steps"].append({"step": "copy_skills", "copied_files": skill_files})
        save_state(slug, state)

        # --- Step: copy commands ---
        commands_src = plugin_dir / "commands"
        commands_dst = WORKSPACE / ".claude" / "commands"
        command_files: list[dict] = []
        if commands_src.exists():
            copy_with_manifest(commands_src, commands_dst, slug, "commands", command_files)
        state["completed_steps"].append({"step": "copy_commands", "copied_files": command_files})
        save_state(slug, state)

        # --- Step: copy rules + append index marker ---
        rules_src = plugin_dir / "rules"
        rules_dst = WORKSPACE / ".claude" / "rules"
        rule_files: list[dict] = []
        if rules_src.exists():
            copy_with_manifest(rules_src, rules_dst, slug, "rules", rule_files)
            rule_names = [f.get("dest_filename", f.get("dest", "").split("/")[-1]) for f in rule_files]
            append_rules_index(slug, rule_names)
        state["completed_steps"].append({
            "step": "rules_index_marker",
            "copied_files": rule_files,
        })
        save_state(slug, state)

        # --- Step: copy claude hook handlers ---
        hooks_src = plugin_dir / "claude-hook-handlers"
        hook_files: list[dict] = []
        if hooks_src.exists():
            # Hook handlers stay inside plugins/{slug}/claude-hook-handlers/
            # (no namespace copy needed — dispatcher discovers them there)
            for h in hooks_src.iterdir():
                if h.is_file():
                    hook_files.append({"dest": str(h), "sha256": _sha256(h)})
        state["completed_steps"].append({"step": "copy_claude_hooks", "copied_files": hook_files})
        save_state(slug, state)

        # --- Step: heartbeats union ---
        # Heartbeat YAML stays in plugins/{slug}/heartbeats.yaml — union happens at load time
        # Sync to DB if heartbeat dispatcher is running
        try:
            import sys
            backend_dir = Path(__file__).resolve().parent.parent
            if str(backend_dir) not in sys.path:
                sys.path.insert(0, str(backend_dir))
            from heartbeat_dispatcher import _sync_heartbeats_to_db
            _sync_heartbeats_to_db()
        except Exception as exc:
            logger.info("Heartbeat sync skipped (dispatcher not running): %s", exc)
        state["completed_steps"].append({"step": "heartbeats_union"})
        save_state(slug, state)

        # --- Step: routines union + SIGHUP ---
        routine_error = _reload_scheduler()
        state["completed_steps"].append({
            "step": "routines_union",
            "routine_error": routine_error,
        })
        save_state(slug, state)

        # --- Step: seed host rows (goals / tasks / triggers) ---
        # Plugin may declare YAMLs under goals/, tasks/, triggers/ to seed
        # workspace-scoped rows on install. Each insert tags `source_plugin`
        # so uninstall deletes WHERE source_plugin = slug (user-created
        # rows stay). All three capabilities are opt-in — a missing YAML
        # just skips the step.
        _seed_plugin_host_rows(conn, slug, plugin_dir)
        state["completed_steps"].append({"step": "seed_host_rows"})
        save_state(slug, state)

        # --- Step: widget mount_point limit check (AC27) ---
        # Max 3 widgets per mount_point across all active plugins (excluding self).
        _WIDGET_MOUNT_LIMIT = 3
        incoming_widgets = (manifest.get("ui_entry_points") or {}).get("widgets") or []
        if incoming_widgets:
            # Count existing widgets per mount_point from DB
            mount_counts: dict[str, int] = {}
            try:
                rows_mp = conn.execute(
                    "SELECT manifest_json FROM plugins_installed WHERE enabled = 1 AND status = 'active' AND slug != ?",
                    (slug,),
                ).fetchall()
                for row_mp in rows_mp:
                    try:
                        existing_manifest = json.loads(row_mp["manifest_json"] or "{}")
                        for wspec in (existing_manifest.get("ui_entry_points") or {}).get("widgets") or []:
                            mp = wspec.get("mount_point")
                            if mp:
                                mount_counts[mp] = mount_counts.get(mp, 0) + 1
                    except Exception:
                        pass
            except sqlite3.OperationalError:
                pass  # Table may not exist in early bootstrap

            for wspec in incoming_widgets:
                mp = wspec.get("mount_point")
                if mp and mount_counts.get(mp, 0) >= _WIDGET_MOUNT_LIMIT:
                    raise _WidgetLimitError(
                        f"Widget mount_point '{mp}' already has {mount_counts[mp]} widgets "
                        f"(limit: {_WIDGET_MOUNT_LIMIT}). Cannot install plugin '{slug}'."
                    )

        # --- Step: copy widgets ---
        widgets_src = plugin_dir / "ui" / "widgets"
        widget_files: list[dict] = []
        if widgets_src.exists():
            for w in widgets_src.glob("*.js"):
                sha = _sha256(w)
                widget_files.append({
                    "filename": w.name,
                    "sha256": sha,
                    "url": f"/plugins/{slug}/ui/widgets/{w.name}",
                })
        state["completed_steps"].append({"step": "copy_widgets", "widget_files": widget_files})
        save_state(slug, state)

        # --- Step: Wave 2.0 — validate + register in-place assets (icon/avatar) ---
        # Assets stay inside plugins/{slug}/ui/assets/; serving uses the existing
        # /plugins/<slug>/ui/<path> endpoint. Size, magic-byte MIME, and optional
        # SHA256 are validated here before the DB is written.
        asset_records: list[dict] = []
        _plugin_manifest_obj = None
        try:
            from plugin_schema import PluginManifest
            _plugin_manifest_obj = PluginManifest.model_validate(manifest)
        except Exception:
            pass  # schema already validated at preview; best-effort here

        if _plugin_manifest_obj is not None:
            # Icon
            if _plugin_manifest_obj.metadata and _plugin_manifest_obj.metadata.icon:
                rec = register_in_place_asset(
                    plugin_dir,
                    _plugin_manifest_obj.metadata.icon,
                    slug,
                    expected_sha256=_plugin_manifest_obj.metadata.icon_sha256,
                )
                asset_records.append(rec)
            # Agent avatars
            for agent_entry in (_plugin_manifest_obj.agents or []):
                if agent_entry.avatar:
                    rec = register_in_place_asset(
                        plugin_dir,
                        agent_entry.avatar,
                        slug,
                        expected_sha256=agent_entry.avatar_sha256,
                    )
                    asset_records.append(rec)
        state["completed_steps"].append({"step": "register_assets", "asset_files": asset_records})
        save_state(slug, state)

        # --- Step: Wave 2.3 — inject MCP servers into ~/.claude.json ---
        mcp_installed_records: list[dict] = []
        _declared_mcp_servers = manifest.get("mcp_servers") or []
        if _declared_mcp_servers:
            try:
                from plugin_claude_config import add_mcp_servers
                from plugin_install_state import all_plugin_mcp_names
                # Pre-install collision check
                _existing_mcp_names = all_plugin_mcp_names(DB_PATH)
                for _srv in _declared_mcp_servers:
                    _eff = f"plugin-{slug}-{_srv.get('name', '')}"
                    if _eff in _existing_mcp_names:
                        raise RuntimeError(
                            f"MCP name collision: effective name '{_eff}' is already "
                            "registered by another installed plugin."
                        )
                mcp_installed_records = add_mcp_servers(
                    slug, _declared_mcp_servers, workspace=WORKSPACE
                )
            except Exception as exc:
                raise RuntimeError(f"MCP server injection failed: {exc}") from exc
        state["completed_steps"].append({
            "step": "install_mcp_servers",
            "installed_records": mcp_installed_records,
        })
        save_state(slug, state)

        # --- Step: post-install hook ---
        post_hook = plugin_dir / "hooks" / "post-install.sh"
        if post_hook.exists():
            try:
                run_lifecycle_hook(plugin_dir, "post-install", timeout=60)
            except LifecycleHookError as exc:
                logger.warning("post-install hook failed (non-blocking): %s", exc)
        state["completed_steps"].append({"step": "post_install_hook"})
        save_state(slug, state)

        # --- Step: DB register ---
        # Wave 2.3: store mcp_servers_installed in manifest_json for uninstall/update
        # Uses a separate key to avoid overwriting the declared mcp_servers field.
        manifest_for_db = dict(manifest)
        if mcp_installed_records:
            manifest_for_db["mcp_servers_installed"] = mcp_installed_records

        manifest_sha = hashlib.sha256(json.dumps(manifest_for_db, sort_keys=True).encode()).hexdigest()

        try:
            conn.execute(
                """INSERT INTO plugins_installed
                   (id, slug, name, version, tier, source_type, source_url,
                    installed_at, enabled, manifest_json, install_sha256, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'active')
                   ON CONFLICT(slug) DO NOTHING""",
                (slug, slug, manifest["name"], manifest["version"],
                 manifest.get("tier", "essential"), "local", source_url,
                 _now_iso(), json.dumps(manifest_for_db), manifest_sha),
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(f"DB register failed: {exc}") from exc

        state["completed_steps"].append({"step": "db_register"})
        save_state(slug, state)

        # --- Finalize: write manifest + rename state file ---
        final_manifest = {
            "slug": slug,
            "manifest": manifest_for_db,
            "installed_at": _now_iso(),
            "steps": state["completed_steps"],
            "agents": agent_files,
            "skills": skill_files,
            "commands": command_files,
            "rules": rule_files,
            "widgets": widget_files,
            "assets": asset_records,
            "routine_activation_pending": routine_error is not None,
        }
        finalize_install(slug, final_manifest)

        _audit(conn, slug, "install", {"source_url": source_url}, success=True)
        invalidate_agent_meta_cache()
        lock.__exit__(None, None, None)

        return jsonify({
            "slug": slug,
            "status": "active",
            "routine_activation_pending": routine_error is not None,
            "warnings": preview.get("warnings", []),
            "mcp_servers_installed": mcp_installed_records,
        })

    except _WidgetLimitError as exc:
        # AC27: widget mount_point limit — no rollback needed (nothing was written yet)
        logger.warning("Plugin install blocked by widget limit for '%s': %s", slug, exc)
        lock.__exit__(None, None, None)
        return jsonify({"error": "widget_limit_exceeded", "detail": str(exc)}), 409

    except Exception as exc:
        logger.error("Plugin install failed for '%s': %s", slug, exc)
        try:
            rollback_log = rollback_from_state(slug, state, DB_PATH)
            logger.info("Rollback log: %s", rollback_log)
        except Exception as rb_exc:
            logger.error("Rollback also failed: %s", rb_exc)
        _audit(conn, slug, "install_failed", {"error": str(exc)}, success=False)
        lock.__exit__(None, None, None)
        return jsonify({"error": str(exc)}), 500

    finally:
        conn.close()
        # Wave 2.5 — ensure staging dir is cleaned up in all paths (AC-W2.5-11).
        # If copy succeeded, it was already cleaned above.  This covers failure paths.
        try:
            _stg = preview.get("staged_path") if "preview" in dir() else None
            if _stg is not None:
                _stg_path = _stg if isinstance(_stg, Path) else Path(_stg)
                from plugin_loader import STAGING_DIR as _STAGING_DIR
                if _stg_path.exists() and _stg_path.resolve().is_relative_to(_STAGING_DIR.resolve()):
                    shutil.rmtree(_stg_path, ignore_errors=True)
        except Exception:
            pass  # staging cleanup is best-effort


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# DELETE /api/plugins/<slug> — uninstall
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>", methods=["DELETE"])
@login_required
def uninstall_plugin(slug: str):
    from plugin_file_ops import remove_rules_index, reverse_remove_from_manifest
    from plugin_migrator import uninstall_plugin_sql
    from plugin_hook_runner import run_lifecycle_hook
    from plugin_loader import _reload_scheduler

    plugin_dir = PLUGINS_DIR / slug
    if not plugin_dir.exists():
        return jsonify({"error": f"Plugin '{slug}' not found"}), 404

    conn = _get_db()
    try:
        # Pre-uninstall hook
        pre_hook = plugin_dir / "hooks" / "pre-uninstall.sh"
        if pre_hook.exists():
            try:
                run_lifecycle_hook(plugin_dir, "pre-uninstall", timeout=60)
            except Exception as exc:
                logger.warning("pre-uninstall hook failed: %s", exc)

        # Remove files from manifest (reverse order)
        manifest_path = plugin_dir / ".install-manifest.json"
        manifest_applied = False
        if manifest_path.exists():
            try:
                reverse_remove_from_manifest(manifest_path)
                manifest_applied = True
            except Exception as exc:
                logger.warning("manifest file removal failed: %s", exc)

        # Safety-net sweep — walks the four namespaced .claude/ subdirs and
        # removes any `plugin-{slug}-*` leftover. Runs unconditionally: even
        # when the manifest did apply, an older install without the rewritten
        # name-in-frontmatter may have left stragglers the manifest doesn't
        # know about. Cheap and idempotent.
        plugin_prefix = f"plugin-{slug}-"
        for category, target_dir in (
            ("agents", WORKSPACE / ".claude" / "agents"),
            ("skills", WORKSPACE / ".claude" / "skills"),
            ("rules", WORKSPACE / ".claude" / "rules"),
            ("commands", WORKSPACE / ".claude" / "commands"),
        ):
            if not target_dir.is_dir():
                continue
            for entry in target_dir.iterdir():
                if not entry.name.startswith(plugin_prefix):
                    continue
                try:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
                    logger.info("Sweep removed %s", entry)
                except Exception as exc:
                    logger.warning("Sweep failed on %s: %s", entry, exc)

        # Remove rules index marker
        try:
            remove_rules_index(slug)
        except Exception as exc:
            logger.warning("rules index removal failed: %s", exc)

        # Delete host rows this plugin seeded (goals/tasks/triggers capabilities).
        # DELETE WHERE source_plugin = ? leaves user-created rows untouched.
        # Order matters because of FKs: children → parents.
        for _tbl in ("triggers", "tickets", "goal_tasks", "goals", "projects", "missions"):
            try:
                conn.execute(f"DELETE FROM {_tbl} WHERE source_plugin = ?", (slug,))
                conn.commit()
            except Exception as exc:
                logger.warning("Uninstall: failed to clean %s: %s", _tbl, exc)

        # SQL uninstall
        uninstall_sql = plugin_dir / "migrations" / "uninstall.sql"
        if uninstall_sql.exists():
            try:
                conn2 = sqlite3.connect(str(DB_PATH))
                uninstall_plugin_sql(slug, uninstall_sql, conn=conn2)
                conn2.close()
            except Exception as exc:
                logger.warning("SQL uninstall failed: %s", exc)

        # Wave 2.2r: remove plugin env vars section from .env
        _removed_env_keys: list[str] = []
        try:
            from routes.integrations import _remove_env_section  # type: ignore
            _env_path = WORKSPACE / ".env"
            _removed_env_keys = _remove_env_section(_env_path, f"plugin-{slug}")
            if _removed_env_keys:
                logger.info("Uninstall: removed env section 'plugin-%s', keys=%s", slug, _removed_env_keys)
        except Exception as exc:
            logger.warning("Uninstall: env section removal failed for '%s': %s", slug, exc)

        # Wave 2.2r: clean integration health cache
        _health_cache_removed = 0
        try:
            _health_rows = conn.execute(
                "DELETE FROM integration_health_cache WHERE plugin_slug = ?", (slug,)
            ).rowcount
            conn.commit()
            _health_cache_removed = _health_rows
        except Exception as exc:
            logger.warning("Uninstall: health cache cleanup failed for '%s': %s", slug, exc)

        # Post-uninstall hook
        post_hook = plugin_dir / "hooks" / "post-uninstall.sh"
        if post_hook.exists():
            try:
                run_lifecycle_hook(plugin_dir, "post-uninstall", timeout=60)
            except Exception as exc:
                logger.warning("post-uninstall hook failed: %s", exc)

        # Wave 2.3: remove MCP servers from ~/.claude.json before removing plugin dir
        _mcp_audit: dict = {}
        try:
            from plugin_claude_config import remove_mcp_servers
            from plugin_install_state import get_plugin_mcp_servers
            _mcp_records = get_plugin_mcp_servers(slug, DB_PATH)
            if _mcp_records:
                _mcp_audit = remove_mcp_servers(slug, _mcp_records)
                logger.info("Uninstall MCP audit for '%s': %s", slug, _mcp_audit)
        except Exception as exc:
            logger.warning("MCP removal failed during uninstall of '%s': %s", slug, exc)

        # Remove plugin directory
        shutil.rmtree(plugin_dir, ignore_errors=True)

        # DB remove
        conn.execute("DELETE FROM plugins_installed WHERE slug = ?", (slug,))
        conn.commit()

        # Reset circuit breaker state
        conn.execute(
            "DELETE FROM plugin_hook_circuit_state WHERE plugin_slug = ?", (slug,)
        )
        conn.commit()

        # Reload scheduler
        _reload_scheduler()

        _audit(conn, slug, "uninstall", {
            "removed_env_keys": _removed_env_keys,
            "removed_health_cache_count": _health_cache_removed,
            "mcp_audit": _mcp_audit,
        }, success=True)
        invalidate_agent_meta_cache()
        return jsonify({
            "slug": slug,
            "status": "uninstalled",
            "mcp_audit": _mcp_audit,
            "removed_env_keys": _removed_env_keys,
        })

    except Exception as exc:
        logger.error("Uninstall failed for '%s': %s", slug, exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PATCH /api/plugins/<slug> — enable/disable
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>", methods=["PATCH"])
@login_required
def update_plugin_status(slug: str):
    data = request.get_json(force=True, silent=True) or {}
    enabled = data.get("enabled")
    if enabled is None:
        return jsonify({"error": "enabled field required"}), 400

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, capabilities_disabled FROM plugins_installed WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Plugin not found"}), 404

        # Wave 1.1 (ADR BN-2): FS rename cascade for skills/agents/commands
        # and rules-index rebuild when plugin is enabled/disabled at the plugin level.
        # capabilities_disabled is NOT mutated — per-capability state is preserved.
        try:
            caps_disabled: dict = json.loads(row["capabilities_disabled"] or "{}")
        except (json.JSONDecodeError, TypeError):
            caps_disabled = {}

        from plugin_file_ops import _toggle_file_disabled
        plugin_prefix = f"plugin-{slug}-"

        if not enabled:
            # Plugin OFF: disable ALL .claude/{agents,skills,commands}/plugin-{slug}-* entries
            # Handles both .md files (agents/commands) and directories (skill bundles)
            for cap_type in ("agents", "skills", "commands"):
                target_dir = WORKSPACE / ".claude" / cap_type
                if not target_dir.is_dir():
                    continue
                for entry in target_dir.iterdir():
                    if not entry.name.startswith(plugin_prefix):
                        continue
                    # Skip already-disabled entries
                    if entry.name.endswith(".md.disabled") or entry.name.endswith(".disabled"):
                        continue
                    try:
                        if entry.is_file() and entry.name.endswith(".md"):
                            entry.rename(entry.with_name(entry.name + ".disabled"))
                        elif entry.is_dir():
                            entry.rename(entry.with_name(entry.name + ".disabled"))
                    except OSError as exc:
                        logger.warning("plugin OFF rename failed for %s: %s", entry, exc)
        else:
            # Plugin ON: re-enable .disabled entries EXCEPT those in capabilities_disabled
            for cap_type in ("agents", "skills", "commands"):
                target_dir = WORKSPACE / ".claude" / cap_type
                if not target_dir.is_dir():
                    continue
                individually_disabled = set(caps_disabled.get(cap_type, []))
                for entry in target_dir.iterdir():
                    if not entry.name.startswith(plugin_prefix):
                        continue
                    # Handle both .md.disabled (files) and .disabled (dirs)
                    if entry.name.endswith(".md.disabled"):
                        stem_no_ext = entry.name[: -len(".md.disabled")]
                        original_name = entry.name[: -len(".disabled")]
                    elif entry.is_dir() and entry.name.endswith(".disabled"):
                        stem_no_ext = entry.name[: -len(".disabled")]
                        original_name = stem_no_ext
                    else:
                        continue
                    if stem_no_ext in individually_disabled:
                        continue  # stay disabled per per-capability state
                    try:
                        entry.rename(entry.with_name(original_name))
                    except OSError as exc:
                        logger.warning("plugin ON rename failed for %s: %s", entry, exc)

        # Rebuild rules index (handles both plugin ON and OFF via the existing filter
        # in regenerate-markers: enabled=1 AND status='active')
        # We do this after the DB write below so the new status is visible.
        status = "active" if enabled else "disabled"
        conn.execute(
            "UPDATE plugins_installed SET enabled = ?, status = ? WHERE slug = ?",
            (1 if enabled else 0, status, slug),
        )
        conn.commit()
        _audit(conn, slug, "enable" if enabled else "disable")

        # Rebuild rules index after status update so enabled/disabled filter is correct
        try:
            disabled_rules = caps_disabled.get("rules", [])
            if enabled:
                _rebuild_rules_index_for_plugin(slug, disabled_rules)
            else:
                # Plugin disabled — remove its block from index entirely
                from plugin_file_ops import remove_rules_index
                remove_rules_index(slug)
        except Exception as exc:
            logger.warning("rules index rebuild failed after plugin toggle: %s", exc)

        return jsonify({"slug": slug, "enabled": enabled, "status": status})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PATCH /api/plugins/<slug>/capabilities — Wave 1.1 per-capability toggle
#
# Body: { "type": "<cap_type>", "id": "<capability-id>", "enabled": <bool> }
# Response: { "slug": "...", "capabilities_disabled": { ... } }
#
# Cap types handled here (DB-only side effects):
#   widgets, readonly_data, claude_hooks, routines
# Cap types handled in Step 3 (FS side effects):
#   skills, agents, commands, rules
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>/capabilities", methods=["PATCH"])
@login_required
def update_plugin_capability(slug: str):
    data = request.get_json(force=True, silent=True) or {}
    cap_type = data.get("type", "")
    cap_id = data.get("id", "")
    enabled = data.get("enabled")

    if not cap_type or not cap_id or enabled is None:
        return jsonify({"error": "type, id, and enabled fields are required"}), 400

    VALID_TYPES = {
        "widgets", "readonly_data", "claude_hooks", "routines",
        "skills", "agents", "commands", "rules",
    }
    if cap_type not in VALID_TYPES:
        return jsonify({"error": f"unsupported capability type: {cap_type}"}), 400

    # Security: reject cap_ids that could be used for path traversal.
    # IDs must follow the namespaced pattern plugin-{slug}-<name>.
    import re as _re
    _CAP_ID_RE = _re.compile(r"^plugin-[a-zA-Z0-9_-]+-[a-zA-Z0-9_.@-]+$")
    if not _CAP_ID_RE.match(cap_id):
        return jsonify({"error": "invalid capability id"}), 400

    conn = _get_db()
    # Use isolation_level=None (autocommit mode) so we can issue an explicit
    # BEGIN IMMEDIATE, preventing lost-update races when concurrent requests
    # read the same capabilities_disabled JSON and each write back their own
    # version (Flask threaded=True is the live scenario).
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, enabled AS plugin_enabled, status, capabilities_disabled "
            "FROM plugins_installed WHERE slug = ?",
            (slug,),
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return jsonify({"error": "Plugin not found"}), 404

        # Parse existing capabilities_disabled JSON
        try:
            caps_disabled: dict = json.loads(row["capabilities_disabled"] or "{}")
        except (json.JSONDecodeError, TypeError):
            caps_disabled = {}

        # Update the set for this capability type
        disabled_set: list = caps_disabled.get(cap_type, [])
        if enabled:
            # Remove from disabled set
            disabled_set = [x for x in disabled_set if x != cap_id]
        else:
            # Add to disabled set (deduplicate)
            if cap_id not in disabled_set:
                disabled_set.append(cap_id)

        if disabled_set:
            caps_disabled[cap_type] = disabled_set
        else:
            caps_disabled.pop(cap_type, None)

        new_caps_json = json.dumps(caps_disabled)

        conn.execute(
            "UPDATE plugins_installed SET capabilities_disabled = ? WHERE slug = ?",
            (new_caps_json, slug),
        )
        conn.execute("COMMIT")

        # --- Side effects per capability type ---
        if cap_type in ("skills", "agents", "commands"):
            # FS rename: .md <-> .md.disabled
            from plugin_file_ops import _toggle_file_disabled
            _toggle_file_disabled(cap_type, slug, cap_id, disable=not enabled)

        elif cap_type == "rules":
            # Rebuild rules index (regenerate-markers logic inline)
            _rebuild_rules_index_for_plugin(slug, caps_disabled.get("rules", []))

        elif cap_type == "routines":
            # SIGHUP scheduler to re-read routines with updated disabled set
            try:
                from plugin_loader import _reload_scheduler
                _reload_scheduler()
            except Exception as exc:
                logger.warning("Could not send SIGHUP after routine toggle: %s", exc)

        # widgets / readonly_data / claude_hooks: DB-only, no FS side effect
        _audit(conn, slug, "capability_toggle", payload={
            "type": cap_type, "id": cap_id, "enabled": enabled,
        })
        return jsonify({"slug": slug, "capabilities_disabled": caps_disabled})
    finally:
        conn.close()


def _rebuild_rules_index_for_plugin(slug: str, disabled_rule_names: list[str]) -> None:
    """Rebuild the rules index block for a plugin, skipping disabled rules."""
    from plugin_file_ops import _atomic_write, _build_block, RULES_INDEX_PATH, _MARKER_START, _MARKER_END
    import re as _re

    plugin_dir = PLUGINS_DIR / slug
    manifest_path = plugin_dir / ".install-manifest.json"
    if not manifest_path.exists():
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("_rebuild_rules_index_for_plugin: cannot read manifest for %s: %s", slug, exc)
        return

    # The manifest has a top-level "rules" key (list of {src, dest, sha256, category}).
    # Fall back to "files" for forward-compatibility with any future manifest shapes
    # that may flatten everything under "files".
    rule_records = manifest.get("rules") or [
        f for f in manifest.get("files", []) if f.get("category") == "rules"
    ]
    all_rule_names = [
        f.get("dest_filename") or Path(f.get("dest", "")).name
        for f in rule_records
        if f.get("dest_filename") or f.get("dest", "")
    ]
    # Filter out disabled rules
    disabled_set = set(disabled_rule_names)
    active_rule_names = [n for n in all_rule_names if n not in disabled_set]

    existing = RULES_INDEX_PATH.read_text(encoding="utf-8") if RULES_INDEX_PATH.exists() else ""
    pattern = _re.compile(
        rf"<!-- PLUGIN:{_re.escape(slug)}:START -->.+?<!-- PLUGIN:{_re.escape(slug)}:END -->",
        _re.DOTALL,
    )

    if active_rule_names:
        new_block = _build_block(slug, active_rule_names)
        if pattern.search(existing):
            updated = pattern.sub(new_block, existing)
        else:
            sep = "\n" if existing and not existing.endswith("\n") else ""
            updated = existing + sep + new_block + "\n"
    else:
        # All rules disabled — remove the block entirely
        pattern2 = _re.compile(
            rf"\n?<!-- PLUGIN:{_re.escape(slug)}:START -->.+?<!-- PLUGIN:{_re.escape(slug)}:END -->\n?",
            _re.DOTALL,
        )
        updated = pattern2.sub("", existing)

    _atomic_write(RULES_INDEX_PATH, updated)


# ---------------------------------------------------------------------------
# GET /api/plugins/<slug>/health — validate SHA256 against disk
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>/health", methods=["GET"])
@login_required
def plugin_health(slug: str):
    plugin_dir = PLUGINS_DIR / slug
    manifest_path = plugin_dir / ".install-manifest.json"

    if not plugin_dir.exists():
        return jsonify({"slug": slug, "status": "not_installed"}), 404

    if not manifest_path.exists():
        return jsonify({"slug": slug, "status": "broken", "reason": "no_manifest"}), 200

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"slug": slug, "status": "broken", "reason": "manifest_unreadable"}), 200

    tampered: list[str] = []
    all_files = (
        manifest.get("agents", []) + manifest.get("skills", []) +
        manifest.get("commands", []) + manifest.get("rules", [])
    )
    for file_info in all_files:
        dest = Path(file_info.get("dest", ""))
        expected_sha = file_info.get("sha256")
        if not dest.exists():
            tampered.append(f"missing:{dest}")
            continue
        # Skills are copied as directories; their sha256 is taken from the inner SKILL.md
        # (see plugin_file_ops._copy_skill_dir). Everything else is a single file.
        hash_target = dest / "SKILL.md" if dest.is_dir() else dest
        if expected_sha and hash_target.is_file() and _sha256(hash_target) != expected_sha:
            tampered.append(f"tampered:{dest}")

    # Wave 2.0: validate in-place assets (icon / avatar) — AC2.0.6
    for asset_info in manifest.get("assets", []):
        rel_path = asset_info.get("rel_path")
        expected_sha = asset_info.get("sha256")
        if not rel_path:
            continue
        asset_path = plugin_dir / rel_path
        if not asset_path.exists():
            tampered.append(f"missing_asset:{rel_path}")
            continue
        if expected_sha and _sha256(asset_path) != expected_sha:
            tampered.append(f"tampered_asset:{rel_path}")

    if tampered:
        return jsonify({"slug": slug, "status": "broken", "tampered_files": tampered}), 200

    return jsonify({"slug": slug, "status": "active"})


# ---------------------------------------------------------------------------
# GET /api/plugins/widgets?mount=overview — list registered widgets
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/widgets", methods=["GET"])
@login_required
def list_widgets():
    mount = request.args.get("mount", "")
    widgets: list[dict] = []

    if not PLUGINS_DIR.exists():
        return jsonify(widgets)

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT slug, capabilities_disabled FROM plugins_installed WHERE enabled = 1 AND status = 'active'"
        ).fetchall()
        # Map slug -> set of disabled widget ids (Wave 1.1 per-capability filter)
        active_slugs = {r["slug"] for r in rows}
        disabled_widgets_by_slug: dict[str, set] = {}
        for r in rows:
            try:
                caps = json.loads(r["capabilities_disabled"] or "{}")
                disabled_widgets_by_slug[r["slug"]] = set(caps.get("widgets", []))
            except (json.JSONDecodeError, TypeError):
                disabled_widgets_by_slug[r["slug"]] = set()
    finally:
        conn.close()

    for plugin_dir in sorted(PLUGINS_DIR.glob("*/")):
        slug = plugin_dir.name
        if slug.startswith(".") or slug not in active_slugs:
            continue
        manifest_path = plugin_dir / ".install-manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Widget specs live in `manifest.manifest.ui_entry_points.widgets`.
        # Installed widget files live in `manifest.widgets` (list of copied files).
        plugin_manifest = manifest.get("manifest", {}) or {}
        installed_files = manifest.get("widgets", []) or []
        widget_specs = ((plugin_manifest.get("ui_entry_points") or {}).get("widgets")) or []
        plugin_disabled_widgets = disabled_widgets_by_slug.get(slug, set())
        for wspec in widget_specs:
            if mount and wspec.get("mount_point") != mount:
                continue
            widget_id = wspec.get("id")
            # Wave 1.1: skip widgets individually disabled via capabilities_disabled
            if widget_id and widget_id in plugin_disabled_widgets:
                continue
            # Derive filename: explicit field wins; otherwise basename of `route`.
            filename = wspec.get("filename")
            if not filename and wspec.get("route"):
                filename = wspec["route"].rsplit("/", 1)[-1]
            if not filename and installed_files:
                filename = installed_files[0].get("name") if isinstance(installed_files[0], dict) else None
            if not filename:
                continue
            widgets.append({
                "slug": slug,
                "widget_id": widget_id,
                "custom_element_name": wspec.get("custom_element_name") or widget_id,
                "bundle_url": f"/plugins/{slug}/ui/widgets/{filename}",
                "mount_point": wspec.get("mount_point"),
                "label": wspec.get("label"),
            })

    return jsonify(widgets)


# ---------------------------------------------------------------------------
# POST /api/plugins/regenerate-markers — rebuild _plugins-index.md (AC33)
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/regenerate-markers", methods=["POST"])
@login_required
def regenerate_markers():
    """Rebuild _plugins-index.md atomically from all active plugins (AC33).

    Builds the full index content in memory, then writes it in a single
    os.replace() call — no unlink() + loop pattern that creates a TOCTOU window.
    """
    from plugin_file_ops import _atomic_write, _build_block, RULES_INDEX_PATH

    conn = _get_db()
    rebuilt: list[str] = []
    try:
        rows = conn.execute(
            "SELECT slug FROM plugins_installed WHERE enabled = 1 AND status = 'active'"
        ).fetchall()
        active_slugs = [r["slug"] for r in rows]
    finally:
        conn.close()

    # Build full content in memory, then write once atomically (AC33)
    blocks: list[str] = []
    for slug in active_slugs:
        plugin_dir = PLUGINS_DIR / slug
        manifest_path = plugin_dir / ".install-manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            rule_names = [
                f.get("dest_filename", "")
                for f in manifest.get("rules", [])
                if f.get("dest_filename")
            ]
            if rule_names:
                blocks.append(_build_block(slug, rule_names))
                rebuilt.append(slug)
        except Exception as exc:
            logger.warning("regenerate-markers: failed for '%s': %s", slug, exc)

    # Single atomic write — replaces old file or creates new one
    full_content = "\n".join(blocks) + ("\n" if blocks else "")
    _atomic_write(RULES_INDEX_PATH, full_content)

    return jsonify({"rebuilt_for": rebuilt})


# ---------------------------------------------------------------------------
# GET /api/plugins/<slug>/readonly-data/<query_name> — ADR-4 safe queries
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>/readonly-data/<query_name>", methods=["GET"])
@login_required
def readonly_data(slug: str, query_name: str):
    """Execute a declared readonly query from the plugin manifest.

    ADR-4: SQL lives in plugin.yaml (not in the widget request). Queries are
    validated on install to only touch {slug_under}_* tables. Hard row cap: 1000.
    """
    plugin_dir = PLUGINS_DIR / slug
    manifest_path = plugin_dir / ".install-manifest.json"

    if not manifest_path.exists():
        return jsonify({"error": "Plugin not found"}), 404

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        plugin_manifest = manifest_data.get("manifest", {})
    except Exception:
        return jsonify({"error": "Manifest unreadable"}), 500

    # Wave 1.1: check if this specific query is individually disabled
    _rd_conn = _get_db()
    try:
        _pi_row = _rd_conn.execute(
            "SELECT capabilities_disabled FROM plugins_installed WHERE slug = ? AND enabled = 1 AND status = 'active'",
            (slug,),
        ).fetchone()
        if _pi_row:
            try:
                _caps = json.loads(_pi_row["capabilities_disabled"] or "{}")
                if query_name in _caps.get("readonly_data", []):
                    return jsonify({"error": "Query disabled"}), 404
            except (json.JSONDecodeError, TypeError):
                pass
        elif not _pi_row:
            return jsonify({"error": "Plugin not found or not active"}), 404
    finally:
        _rd_conn.close()

    # Find query declaration — readonly_data is a list of {id, description, sql}
    rd = plugin_manifest.get("readonly_data") or []
    query_decl = next((q for q in rd if isinstance(q, dict) and q.get("id") == query_name), None)

    if not query_decl:
        return jsonify({"error": f"Query '{query_name}' not declared in plugin manifest"}), 404

    sql = query_decl.get("sql")
    if not sql:
        return jsonify({"error": "Invalid query declaration"}), 500

    # Build query params from request.args — only declared params allowed
    declared_params = query_decl.get("params", {})
    params: dict = {}
    for key, value in request.args.items():
        if key not in declared_params:
            return jsonify({"error": f"Parameter '{key}' not declared in manifest"}), 400
        params[key] = value

    # Fill defaults for missing params
    for key, meta in declared_params.items():
        if key not in params:
            if isinstance(meta, dict):
                params[key] = meta.get("default")
            else:
                params[key] = meta

    # Hard cap 1000 rows (ADR-4)
    if "limit" in params:
        try:
            params["limit"] = min(int(params["limit"]), 1000)
        except (TypeError, ValueError):
            params["limit"] = 1000
    elif ":limit" in sql:
        params["limit"] = 1000

    try:
        conn = _get_db()
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchmany(1000)]
        conn.close()
        return jsonify({"query": query_name, "count": len(rows), "rows": rows})
    except sqlite3.Error as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# POST/PUT/DELETE /api/plugins/<slug>/data/<resource> — writable data (Wave 2.1)
# ADR-4 extension: table name must start with {slug_under}_ (validated at
# install via plugin_schema.py and re-checked at runtime).
# Only allowed_columns declared in the manifest may be written.
# Payload validated against optional json_schema if declared.
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>/data/<resource_id>", methods=["POST", "PUT", "DELETE"])
@login_required
def writable_data(slug: str, resource_id: str):
    """Execute a declared writable-data mutation (POST=insert, PUT=update, DELETE=delete).

    Wave 2.1 / ADR extension:
    - Only columns in allowed_columns are written (whitelist enforcement)
    - Table name prefix guard re-checked at runtime
    - Optional jsonschema validation if json_schema declared in manifest
    - Zero string interpolation — all values via SQLite bind params
    """
    plugin_dir = PLUGINS_DIR / slug
    manifest_path = plugin_dir / ".install-manifest.json"

    if not manifest_path.exists():
        return jsonify({"error": "Plugin not found"}), 404

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        plugin_manifest = manifest_data.get("manifest", {})
    except Exception:
        return jsonify({"error": "Manifest unreadable"}), 500

    # Check plugin is enabled + active; check capability-level disable
    conn = _get_db()
    try:
        pi_row = conn.execute(
            "SELECT capabilities_disabled FROM plugins_installed "
            "WHERE slug = ? AND enabled = 1 AND status = 'active'",
            (slug,),
        ).fetchone()
        if not pi_row:
            return jsonify({"error": "Plugin not found or not active"}), 404
        try:
            caps_disabled = json.loads(pi_row["capabilities_disabled"] or "{}")
            if resource_id in caps_disabled.get("writable_data", []):
                return jsonify({"error": "Resource disabled"}), 404
        except (json.JSONDecodeError, TypeError):
            pass
    finally:
        conn.close()

    # Locate resource declaration
    wd_list = plugin_manifest.get("writable_data") or []
    resource_decl = next(
        (r for r in wd_list if isinstance(r, dict) and r.get("id") == resource_id),
        None,
    )
    if not resource_decl:
        return jsonify({"error": f"Resource '{resource_id}' not declared in plugin manifest"}), 404

    table = resource_decl.get("table", "")
    slug_under = slug.replace("-", "_") + "_"

    # Runtime prefix guard (belt + suspenders — already validated at install)
    if not table.lower().startswith(slug_under):
        logger.error(
            "writable_data runtime guard: table '%s' not prefixed with '%s'",
            table, slug_under,
        )
        return jsonify({"error": "Internal manifest error"}), 500

    allowed_columns: list[str] = resource_decl.get("allowed_columns") or []
    method = request.method

    if method == "DELETE":
        row_id = request.args.get("id") or (request.json or {}).get("id")
        if row_id is None or row_id == "":
            return jsonify({"error": "id required for DELETE"}), 400
        # Accept both integer and string primary keys (plugin tables may use
        # either INTEGER AUTOINCREMENT or TEXT PRIMARY KEY DEFAULT random-hex).
        try:
            conn = _get_db()
            # Parameterised — table name from whitelist, id from bind
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))  # noqa: S608
            conn.commit()
            conn.close()
            return jsonify({"deleted": row_id})
        except sqlite3.Error as exc:
            return jsonify({"error": str(exc)}), 500

    # POST / PUT — parse body
    try:
        body: dict = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400

    # Optional JSON Schema validation — strip routing field `id` so schemas with
    # additionalProperties:false don't reject PUT bodies that carry the row id.
    # Also strip None values from the schema itself (Pydantic emits Optional
    # fields as None, which the jsonschema metaschema rejects for keys like
    # `required` that must be arrays).
    json_schema_decl = resource_decl.get("json_schema")
    if json_schema_decl:
        try:
            import jsonschema
            validate_body = {k: v for k, v in body.items() if k != "id"}
            clean_schema = {k: v for k, v in json_schema_decl.items() if v is not None}
            jsonschema.validate(validate_body, clean_schema)
        except jsonschema.ValidationError as exc:
            return jsonify({"error": f"Payload validation failed: {exc.message}"}), 400

    # Column allowlist filtering — reject unknown columns
    unknown = [c for c in body if c not in allowed_columns and c != "id"]
    if unknown:
        return jsonify({"error": f"Columns not allowed: {unknown}"}), 400

    if method == "POST":
        # INSERT
        cols = [c for c in body if c in allowed_columns]
        if not cols:
            return jsonify({"error": "No valid columns provided"}), 400
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        values = [body[c] for c in cols]
        try:
            conn = _get_db()
            cur = conn.execute(
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",  # noqa: S608
                values,
            )
            conn.commit()
            # Tables may have INTEGER PK (where lastrowid IS the id) or TEXT PK
            # with a DEFAULT expression (where id was generated server-side and
            # lastrowid is just the rowid). Fetch the actual id from the row.
            row = conn.execute(
                f"SELECT id FROM {table} WHERE rowid = ?",  # noqa: S608
                (cur.lastrowid,),
            ).fetchone()
            conn.close()
            new_id = row["id"] if row else cur.lastrowid
            return jsonify({"id": new_id}), 201
        except sqlite3.Error as exc:
            return jsonify({"error": str(exc)}), 500

    # PUT — UPDATE by id (accepts both integer and string primary keys)
    row_id = body.get("id")
    if row_id is None or row_id == "":
        return jsonify({"error": "id required for PUT"}), 400

    cols = [c for c in body if c in allowed_columns]
    if not cols:
        return jsonify({"error": "No valid columns to update"}), 400
    set_clause = ", ".join(f"{c} = ?" for c in cols)
    values = [body[c] for c in cols] + [row_id]
    try:
        conn = _get_db()
        conn.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = ?",  # noqa: S608
            values,
        )
        conn.commit()
        conn.close()
        return jsonify({"updated": row_id})
    except sqlite3.Error as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/plugin-ui-registry — aggregated pages + sidebar_groups (Wave 2.1)
# Called once post-login by hydratePluginUiRegistry() in the frontend.
# Returns only enabled+active plugins; respects capabilities_disabled["ui_pages"].
# ---------------------------------------------------------------------------

@bp.route("/api/plugin-ui-registry", methods=["GET"])
@login_required
def plugin_ui_registry():
    """Return aggregated page + sidebar_group declarations from all active plugins.

    Shape:
    {
      "plugins": [
        {
          "slug": "pm-essentials",
          "version": "0.3.0",
          "pages": [...],          // PluginPage dicts (filtered by disabled list)
          "sidebar_groups": [...]  // PluginSidebarGroup dicts
        }
      ]
    }
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT slug, capabilities_disabled FROM plugins_installed "
            "WHERE enabled = 1 AND status = 'active'",
        ).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        slug_val = row["slug"]
        plugin_dir = PLUGINS_DIR / slug_val
        manifest_path = plugin_dir / ".install-manifest.json"
        if not manifest_path.exists():
            continue

        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            plugin_manifest = manifest_data.get("manifest", {})
        except Exception:
            continue

        ui_ep = plugin_manifest.get("ui_entry_points") or {}
        pages_raw: list[dict] = ui_ep.get("pages") or []
        sidebar_groups_raw: list[dict] = ui_ep.get("sidebar_groups") or []

        if not pages_raw and not sidebar_groups_raw:
            continue

        # Filter disabled pages
        try:
            caps_disabled = json.loads(row["capabilities_disabled"] or "{}")
            disabled_pages: list[str] = caps_disabled.get("ui_pages", [])
        except (json.JSONDecodeError, TypeError):
            disabled_pages = []

        pages_filtered = [p for p in pages_raw if p.get("id") not in disabled_pages]

        # Attach version for cache-busting bundle URLs on the frontend
        version = plugin_manifest.get("version", "0")

        result.append({
            "slug": slug_val,
            "version": version,
            "pages": pages_filtered,
            "sidebar_groups": sidebar_groups_raw,
        })

    return jsonify({"plugins": result})


# ---------------------------------------------------------------------------
# GET /plugins/<slug>/ui/<path:subpath> — widget file serving (Step 10)
# C5 (Vault F5): realpath + startswith containment
# ---------------------------------------------------------------------------

@bp.route("/plugins/<slug>/ui/<path:subpath>", methods=["GET"])
@login_required
def serve_widget(slug: str, subpath: str):
    """Serve widget bundle files with strict CSP and auth (C5 Vault F5)."""
    plugin_dir = PLUGINS_DIR / slug
    plugin_ui_root = os.path.realpath(str(plugin_dir / "ui"))
    requested = os.path.realpath(os.path.join(plugin_ui_root, subpath))

    # C5: containment check — must stay inside plugins/{slug}/ui/
    if not requested.startswith(plugin_ui_root + os.sep):
        abort(404)

    if not os.path.isfile(requested):
        abort(404)

    # MIME whitelist
    ext = os.path.splitext(requested)[1].lower()
    mime_map = {
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".json": "application/json; charset=utf-8",
    }
    mime = mime_map.get(ext)
    if not mime:
        abort(404)  # unsupported file type

    from flask import make_response
    resp = make_response(open(requested, "rb").read())
    resp.headers["Content-Type"] = mime
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "public, max-age=3600, immutable"
    # Strict CSP for widget files — widgets may only connect back to self
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "style-src 'unsafe-inline' 'self'; "
        "img-src 'self' data:"
    )
    return resp


# ---------------------------------------------------------------------------
# GET /api/plugins/<slug>/update/preview — read-only diff before applying update
#
# AC1.2.1: returns added/removed/modified breakdown
# AC1.2.3: sql_migrations_blocked + breaking_changes when install.sql SHA differs
# AC1.2.4: pure read-only — no DB writes, no file writes
# ---------------------------------------------------------------------------

# Module-level preview cache: key=(slug, source_url), value=(fetched_at_float, result_dict)
# Cache key: (slug, source_url, has_auth_token). has_auth_token flag prevents
# leaking private-repo previews to unauthenticated callers — the value of the
# token is deliberately not part of the key (no secrets in memory indices).
_PREVIEW_CACHE: dict[tuple[str, str, bool], tuple[float, dict]] = {}
_PREVIEW_CACHE_LOCK = threading.Lock()
_PREVIEW_CACHE_TTL = 300  # seconds


def _diff_capabilities(
    installed_manifest: dict,
    new_manifest: dict,
) -> tuple[dict, dict, dict]:
    """Compute added / removed / modified capability IDs across all capability types.

    File-backed types (agents, skills, commands, rules): detect 'modified' via
    manifest.files[path].sha256 comparison.

    Entry-backed types (widgets, readonly_data, claude_hooks, heartbeats,
    triggers, routines): detect 'modified' via deep-equal on the manifest entry dict.

    Returns three dicts keyed by capability type, each value is a list of IDs.
    """
    def _extract_ids_and_entries(manifest: dict) -> dict[str, dict[str, Any]]:
        """Return {cap_type: {id: entry_or_sha}} for all known types."""
        result: dict[str, dict[str, Any]] = {}

        # File-backed: IDs are filenames (without path prefix) found under each folder.
        # SHA is pulled from manifest["files"][path]["sha256"].
        files = manifest.get("files") or {}
        for cap_type, prefix in (
            ("agents", ".claude/agents/"),
            ("skills", ".claude/skills/"),
            ("commands", ".claude/commands/"),
            ("rules", ".claude/rules/"),
        ):
            bucket: dict[str, Any] = {}
            for path, info in files.items():
                if path.startswith(prefix):
                    fname = path[len(prefix):]
                    sha = (info or {}).get("sha256", "") if isinstance(info, dict) else ""
                    bucket[fname] = sha
            if bucket:
                result[cap_type] = bucket

        # Widget IDs from ui_entry_points.widgets
        widgets_list = (manifest.get("ui_entry_points") or {}).get("widgets") or []
        if widgets_list:
            result["widgets"] = {(w.get("id") or ""): w for w in widgets_list if w.get("id")}

        # readonly_data
        rd_list = manifest.get("readonly_data") or []
        if rd_list:
            result["readonly_data"] = {(q.get("id") or ""): q for q in rd_list if q.get("id")}

        # claude_hooks — use handler_path as ID
        hooks_list = manifest.get("claude_hooks") or []
        if hooks_list:
            result["claude_hooks"] = {(h.get("handler_path") or ""): h for h in hooks_list if h.get("handler_path")}

        # heartbeats — use id field
        hb_list = manifest.get("heartbeats") or []
        if hb_list:
            result["heartbeats"] = {(h.get("id") or ""): h for h in hb_list if h.get("id")}

        # triggers — use id or name field
        tr_list = manifest.get("triggers") or []
        if tr_list:
            result["triggers"] = {(tr.get("id") or tr.get("name") or ""): tr for tr in tr_list if (tr.get("id") or tr.get("name"))}

        # routines — use name field
        rt_list = manifest.get("routines") or []
        if rt_list:
            result["routines"] = {(r.get("name") or ""): r for r in rt_list if r.get("name")}

        return result

    installed = _extract_ids_and_entries(installed_manifest)
    candidate = _extract_ids_and_entries(new_manifest)

    all_types = set(installed) | set(candidate)
    added: dict[str, list] = {}
    removed: dict[str, list] = {}
    modified: dict[str, list] = {}

    for cap_type in sorted(all_types):
        inst_bucket = installed.get(cap_type, {})
        cand_bucket = candidate.get(cap_type, {})

        inst_ids = set(inst_bucket)
        cand_ids = set(cand_bucket)

        added_ids = sorted(cand_ids - inst_ids)
        removed_ids = sorted(inst_ids - cand_ids)
        common_ids = inst_ids & cand_ids
        modified_ids = sorted(
            id_ for id_ in common_ids
            if inst_bucket[id_] != cand_bucket[id_]
        )

        if added_ids:
            added[cap_type] = added_ids
        if removed_ids:
            removed[cap_type] = removed_ids
        if modified_ids:
            modified[cap_type] = modified_ids

    return added, removed, modified


def _compute_preview(
    slug: str, source_url: str, auth_token: str | None = None
) -> dict:
    """Fetch candidate manifest and compute diff against installed manifest.

    Pure read-only: no DB writes, no file writes to plugins/{slug}/.
    Raises RuntimeError or ValueError (same error codes as update endpoint).

    tarball_sha7 derivation: SHA256 of sorted manifest.files SHAs concatenated.
    If manifest.files is empty, falls back to SHA256 of serialized manifest JSON.
    First 7 chars of the hex digest are returned (deterministic, no re-download needed).

    Args:
        slug: Installed plugin slug.
        source_url: Candidate source (github:..., https://...).
        auth_token: Optional GitHub PAT — required for private repos. Sent as
            ``Authorization: token <pat>`` header by ``resolve_source``.
    """
    from plugin_schema import load_plugin_manifest
    from plugin_loader import PluginInstaller, _parse_version

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT slug, source_url, version, manifest_json, capabilities_disabled "
            "FROM plugins_installed WHERE slug = ?",
            (slug,)
        ).fetchone()
        if not row:
            raise ValueError(f"Plugin '{slug}' is not installed")

        installed_version = row["version"]
        try:
            installed_manifest_dict = json.loads(row["manifest_json"] or "{}")
        except Exception:
            installed_manifest_dict = {}

        # Installed SQL SHA from stored manifest
        installed_sql_sha: str | None = (
            installed_manifest_dict.get("files", {}).get("migrations/install.sql", {}).get("sha256")
            if isinstance(installed_manifest_dict.get("files"), dict)
            else None
        )
        # Fallback: compute from disk
        if installed_sql_sha is None:
            installed_sql_path = PLUGINS_DIR / slug / "migrations" / "install.sql"
            if installed_sql_path.exists():
                from plugin_file_ops import _sha256_file
                installed_sql_sha = _sha256_file(installed_sql_path)

        # capabilities_disabled for breaking-change heuristic
        try:
            caps_disabled: dict = json.loads(row["capabilities_disabled"] or "{}")
        except Exception:
            caps_disabled = {}
    finally:
        conn.close()

    # Resolve candidate source (may hit network / tmp — outside the lock)
    try:
        new_plugin_dir = PluginInstaller.resolve_source(source_url, auth_token=auth_token)
    except ValueError as exc:
        raise ValueError(f"invalid_source: {exc}") from exc
    except RuntimeError as exc:
        raise RuntimeError(f"fetch_failed: {exc}") from exc

    if not new_plugin_dir.is_dir():
        raise RuntimeError(f"fetch_failed: resolved source is not a directory: {new_plugin_dir}")

    try:
        new_manifest = load_plugin_manifest(new_plugin_dir)
    except Exception as exc:
        raise ValueError(f"schema_invalid: {exc}") from exc

    new_version = new_manifest.version
    new_manifest_dict = new_manifest.model_dump()

    # not_newer — return sentinel dict (caller translates to 200 up_to_date)
    if _parse_version(new_version) <= _parse_version(installed_version):
        return {
            "_not_newer": True,
            "from_version": installed_version,
            "to_version": new_version,
        }

    # New SQL SHA
    new_sql_path = new_plugin_dir / "migrations" / "install.sql"
    new_sql_sha: str | None = None
    if new_sql_path.exists():
        from plugin_file_ops import _sha256_file
        new_sql_sha = _sha256_file(new_sql_path)

    sql_migrations_blocked = bool(new_sql_sha != installed_sql_sha and (new_sql_sha or installed_sql_sha))

    # Diff capabilities
    added, removed, modified = _diff_capabilities(installed_manifest_dict, new_manifest_dict)

    # Breaking changes list
    breaking_changes: list[str] = []
    if sql_migrations_blocked:
        breaking_changes.append(
            "install.sql SHA changed — uninstall and reinstall required (v1a limitation)"
        )

    # Capability-rename heuristic: ID in capabilities_disabled+removed set
    # AND a new ID of same type appears in added → state-loss warning
    all_removed_ids_by_type = {cap_type: set(ids) for cap_type, ids in removed.items()}
    all_added_ids_by_type = {cap_type: set(ids) for cap_type, ids in added.items()}
    for cap_type, disabled_ids in caps_disabled.items():
        removed_in_type = all_removed_ids_by_type.get(cap_type, set())
        added_in_type = all_added_ids_by_type.get(cap_type, set())
        for old_id in disabled_ids:
            if old_id in removed_in_type and added_in_type:
                breaking_changes.append(
                    f"capability '{old_id}' ({cap_type}) was disabled by you but is removed "
                    "in the new version — disabled state will be lost"
                )

    # tarball_sha7: deterministic from manifest.files SHAs (sorted keys)
    files_dict = new_manifest_dict.get("files") or {}
    if files_dict:
        sorted_shas = "".join(
            (files_dict[k] or {}).get("sha256", "") if isinstance(files_dict[k], dict) else ""
            for k in sorted(files_dict)
        )
        tarball_sha7 = hashlib.sha256(sorted_shas.encode()).hexdigest()[:7]
    else:
        tarball_sha7 = hashlib.sha256(
            json.dumps(new_manifest_dict, sort_keys=True).encode()
        ).hexdigest()[:7]

    # Wave 2.3: MCP server diff for preview (informational, no write)
    old_mcp_servers = installed_manifest_dict.get("mcp_servers") or []
    new_mcp_servers = new_manifest_dict.get("mcp_servers") or []
    old_mcp_names = {s["name"] for s in old_mcp_servers if isinstance(s, dict) and s.get("name")}
    new_mcp_names = {s["name"] for s in new_mcp_servers if isinstance(s, dict) and s.get("name")}
    mcp_diff = {
        "added": sorted(new_mcp_names - old_mcp_names),
        "removed": sorted(old_mcp_names - new_mcp_names),
        "modified": sorted(
            name for name in old_mcp_names & new_mcp_names
            if next((s for s in old_mcp_servers if s.get("name") == name), {})
            != next((s for s in new_mcp_servers if s.get("name") == name), {})
        ),
    }
    has_mcp_changes = any(mcp_diff[k] for k in ("added", "removed", "modified"))

    return {
        "from_version": installed_version,
        "to_version": new_version,
        "added": added,
        "removed": removed,
        "modified": modified,
        "sql_migrations_blocked": sql_migrations_blocked,
        "breaking_changes": breaking_changes,
        "tarball_sha7": tarball_sha7,
        "mcp_diff": mcp_diff if has_mcp_changes else None,
    }


@bp.route("/api/plugins/<slug>/update/preview", methods=["GET"])
@login_required
def preview_plugin_update(slug: str):
    """Read-only diff preview before applying an update.

    Query param: ?source=<url>  (defaults to installed source_url when omitted)
    Optional header ``X-Plugin-Auth-Token``: GitHub PAT required to fetch
    candidate from a private repository (same semantics as ``auth_token`` on
    ``POST /api/plugins/preview``). Kept out of the query string so it does
    not leak into access logs.

    Returns 200 with diff JSON (or up_to_date: true).
    Never writes to disk or DB.
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT source_url FROM plugins_installed WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not_found"}), 404
        installed_source = row["source_url"] or ""
    finally:
        conn.close()

    source_url = request.args.get("source", installed_source).strip()
    if not source_url:
        return jsonify({"error": "invalid_source", "message": "No source URL provided and none stored"}), 400

    # Optional GitHub PAT for private repos (header only — never logged)
    auth_token = request.headers.get("X-Plugin-Auth-Token") or None

    # Cache key includes auth_token presence (not value) to avoid sharing
    # private-repo results across unauthenticated requests.
    cache_key = (slug, source_url, bool(auth_token))

    # Cache read — lock only around dict access, not network I/O
    with _PREVIEW_CACHE_LOCK:
        entry = _PREVIEW_CACHE.get(cache_key)
        if entry is not None:
            fetched_at, cached_result = entry
            if time.monotonic() - fetched_at <= _PREVIEW_CACHE_TTL:
                return jsonify(_build_preview_response(cached_result))

    # Cache miss — compute outside lock
    try:
        result = _compute_preview(slug, source_url, auth_token=auth_token)
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("invalid_source:"):
            return jsonify({"error": "invalid_source", "message": msg[len("invalid_source: "):]}), 400
        if msg.startswith("schema_invalid:"):
            return jsonify({"error": "schema_invalid", "message": msg[len("schema_invalid: "):]}), 400
        return jsonify({"error": "invalid_source", "message": msg}), 400
    except RuntimeError as exc:
        msg = str(exc)
        return jsonify({"error": "fetch_failed", "message": msg}), 500
    except Exception as exc:
        logger.error("Preview failed for '%s': %s", slug, exc)
        return jsonify({"error": "preview_failed", "message": str(exc)}), 500

    # Write to cache — lock only around dict write
    with _PREVIEW_CACHE_LOCK:
        _PREVIEW_CACHE[cache_key] = (time.monotonic(), result)

    return jsonify(_build_preview_response(result))


def _build_preview_response(result: dict) -> dict:
    """Convert internal _compute_preview result to HTTP response shape."""
    if result.get("_not_newer"):
        return {
            "from_version": result["from_version"],
            "to_version": result["to_version"],
            "up_to_date": True,
            "added": {},
            "removed": {},
            "modified": {},
            "breaking_changes": [],
        }
    return {k: v for k, v in result.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# POST /api/plugins/<slug>/update — in-place knowledge layer update (v1a)
#
# AC10: SQL SHA changed → 409 "migration_chain_not_supported_in_v1a"
# AC11: SQL SHA unchanged → copy new knowledge layer files, bump version, update DB
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/<slug>/update", methods=["POST"])
@login_required
def update_plugin(slug: str):
    """Update a plugin — only allowed when the SQL layer is unchanged.

    AC10: If install.sql SHA differs between installed and candidate → 409.
    AC11: If install.sql SHA is identical → overwrite knowledge layer files and bump version.
    """
    from plugin_schema import load_plugin_manifest
    from plugin_file_ops import (
        copy_with_manifest, append_rules_index, write_manifest, _sha256_file,
    )
    from plugin_loader import _parse_version, _reload_scheduler

    conn = _get_db()
    try:
        # 1. Validate plugin is installed
        row = conn.execute(
            "SELECT slug, source_url, version, manifest_json FROM plugins_installed WHERE slug = ?",
            (slug,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not_found"}), 404

        installed_source = row["source_url"]
        installed_version = row["version"]
        try:
            installed_manifest_dict = json.loads(row["manifest_json"] or "{}")
        except Exception:
            installed_manifest_dict = {}

        # Extract installed SQL SHA from stored manifest (files section if present)
        installed_sql_sha = (
            installed_manifest_dict.get("files", {}).get("migrations/install.sql", {}).get("sha256")
            if isinstance(installed_manifest_dict.get("files"), dict)
            else None
        )
        # Fallback: compute from disk if the manifest doesn't carry it
        if installed_sql_sha is None:
            installed_sql_path = PLUGINS_DIR / slug / "migrations" / "install.sql"
            if installed_sql_path.exists():
                installed_sql_sha = _sha256_file(installed_sql_path)

        # 2. Accept optional source_url override; default to installed
        data = request.get_json(force=True, silent=True) or {}
        source_url = data.get("source_url", installed_source)

        # Optional GitHub PAT for private repos. Mirrors /api/plugins/install —
        # body field first, falling back to ``X-Plugin-Auth-Token`` header for
        # callers that reuse the same header they sent to update/preview.
        auth_token = (
            data.get("auth_token")
            or request.headers.get("X-Plugin-Auth-Token")
            or None
        )

        # 3. Resolve new plugin source (accepts local path, github:..., https://...)
        from plugin_loader import PluginInstaller
        try:
            new_plugin_dir = PluginInstaller.resolve_source(
                source_url, auth_token=auth_token
            )
        except ValueError as exc:
            return jsonify({"error": "invalid_source", "message": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": "fetch_failed", "message": str(exc)}), 500
        if not new_plugin_dir.is_dir():
            return jsonify({"error": "fetch_failed", "message": f"resolved source is not a directory: {new_plugin_dir}"}), 500

        # 4. Load and validate new manifest
        try:
            new_manifest = load_plugin_manifest(new_plugin_dir)
        except Exception as exc:
            return jsonify({"error": "schema_invalid", "message": str(exc)}), 400

        new_version = new_manifest.version

        # 5. Version check — candidate must be strictly newer
        if _parse_version(new_version) <= _parse_version(installed_version):
            return jsonify({
                "error": "not_newer",
                "installed": installed_version,
                "candidate": new_version,
            }), 409

        # 6. Compute new SQL SHA
        new_sql_path = new_plugin_dir / "migrations" / "install.sql"
        new_sql_sha = _sha256_file(new_sql_path) if new_sql_path.exists() else None

        # 7. AC10: Block if SQL SHA changed
        if new_sql_sha != installed_sql_sha:
            return jsonify({
                "error": "migration_chain_not_supported_in_v1a",
                "message": (
                    "install.sql changed between versions. "
                    "In v1a, uninstall and reinstall. "
                    "Migration chain between versions is planned for v1b."
                ),
                "installed_sql_sha": installed_sql_sha,
                "new_sql_sha": new_sql_sha,
            }), 409

        # 8. AC11: Copy new knowledge layer files in place
        # Reuse copy_with_manifest — it handles namespace enforcement and SHA tracking
        plugin_dir = PLUGINS_DIR / slug
        agent_files: list[dict] = []
        skill_files: list[dict] = []
        command_files: list[dict] = []
        rule_files: list[dict] = []
        widget_files: list[dict] = []

        agents_src = new_plugin_dir / "agents"
        if agents_src.exists():
            copy_with_manifest(agents_src, WORKSPACE / ".claude" / "agents", slug, "agents", agent_files)

        skills_src = new_plugin_dir / "skills"
        if skills_src.exists():
            copy_with_manifest(skills_src, WORKSPACE / ".claude" / "skills", slug, "skills", skill_files)

        commands_src = new_plugin_dir / "commands"
        if commands_src.exists():
            copy_with_manifest(commands_src, WORKSPACE / ".claude" / "commands", slug, "commands", command_files)

        rules_src = new_plugin_dir / "rules"
        if rules_src.exists():
            copy_with_manifest(rules_src, WORKSPACE / ".claude" / "rules", slug, "rules", rule_files)
            rule_names = [r.get("dest", "").split("/")[-1] for r in rule_files if r.get("dest")]
            if rule_names:
                append_rules_index(slug, rule_names)

        # Widget files live inside plugins/{slug}/ui/widgets/; copy source into installed dir
        widgets_src = new_plugin_dir / "ui" / "widgets"
        if widgets_src.exists():
            widgets_dst = plugin_dir / "ui" / "widgets"
            widgets_dst.mkdir(parents=True, exist_ok=True)
            for w in widgets_src.glob("*.js"):
                sha = _sha256_file(w)
                shutil.copy2(w, widgets_dst / w.name)
                widget_files.append({
                    "filename": w.name,
                    "sha256": sha,
                    "url": f"/plugins/{slug}/ui/widgets/{w.name}",
                })

        # Wave 2.3: apply MCP delta (tudo-ou-nada) for updated mcp_servers
        from plugin_install_state import get_plugin_mcp_servers as _get_plugin_mcp_servers
        mcp_delta_result: dict = {}
        try:
            from plugin_claude_config import apply_mcp_delta
            _old_mcp_servers = installed_manifest_dict.get("mcp_servers") or []
            _new_mcp_servers = (new_manifest.model_dump().get("mcp_servers") or [])
            _old_mcp_installed = _get_plugin_mcp_servers(slug, DB_PATH)
            if _old_mcp_servers or _new_mcp_servers:
                mcp_delta_result = apply_mcp_delta(
                    slug,
                    _old_mcp_servers,
                    _new_mcp_servers,
                    _old_mcp_installed,
                    workspace=WORKSPACE,
                )
        except Exception as exc:
            logger.warning("MCP delta failed during update of '%s': %s", slug, exc)
            # Non-fatal: log warning but allow update to proceed

        # 9. Heartbeats/routines union — re-reads on next dispatch cycle; trigger reload
        _reload_scheduler()

        # 10. Build updated manifest dict
        new_manifest_dict = new_manifest.model_dump()

        # 11. Wave 1.1: prune capabilities_disabled for IDs that no longer exist in new manifest.
        # IDs that persist keep their disabled state. IDs removed by the new version are pruned.
        # (ADR §7 — capability-id stability is author's contract; renames lose state by design)
        row_caps = conn.execute(
            "SELECT capabilities_disabled FROM plugins_installed WHERE slug = ?", (slug,)
        ).fetchone()
        if row_caps:
            try:
                existing_caps: dict = json.loads(row_caps["capabilities_disabled"] or "{}")
            except (json.JSONDecodeError, TypeError):
                existing_caps = {}

            if existing_caps:
                # Build set of valid IDs from the new manifest for each capability type
                new_manifest_raw = new_manifest.model_dump()
                valid_ids: dict[str, set] = {
                    "widgets": {
                        (w.get("id") or "") for w in (
                            ((new_manifest_raw.get("ui_entry_points") or {}).get("widgets")) or []
                        )
                    },
                    "readonly_data": {
                        (q.get("id") or "") for q in (new_manifest_raw.get("readonly_data") or [])
                    },
                    "claude_hooks": {
                        (h.get("handler_path") or "") for h in (new_manifest_raw.get("claude_hooks") or [])
                    },
                    "routines": set(),  # no stable ID available pre-load; skip pruning
                    "skills": set(),    # file-based; pruning deferred (file ops handle it)
                    "agents": set(),    # same
                    "commands": set(),  # same
                    "rules": set(),     # same
                }
                pruned = {}
                for cap_type, disabled_ids in existing_caps.items():
                    valid = valid_ids.get(cap_type)
                    if valid is not None and valid:
                        # Prune IDs no longer in new manifest (empty string excluded too)
                        kept = [x for x in disabled_ids if x and x in valid]
                    else:
                        # No valid set available — preserve all (FS ops handle cleanup)
                        kept = disabled_ids
                    if kept:
                        pruned[cap_type] = kept
                conn.execute(
                    "UPDATE plugins_installed SET capabilities_disabled = ? WHERE slug = ?",
                    (json.dumps(pruned), slug),
                )

        # 12. Update DB — include new mcp_servers_installed if MCP delta produced records
        # Compute the new installed records: keep unchanged + add new/modified
        _new_installed_records: list[dict] = []
        if mcp_delta_result:
            _new_installed_records.extend(mcp_delta_result.get("added", []))
            _new_installed_records.extend(mcp_delta_result.get("modified", []))
            # Carry over records that were not modified (i.e. unchanged names)
            _new_mcp_names_touched = {
                r["effective_name"] for r in _new_installed_records
            }
            for rec in _get_plugin_mcp_servers(slug, DB_PATH):
                if rec.get("effective_name") not in _new_mcp_names_touched:
                    # Only keep if not in removed set
                    _removed_names = set(mcp_delta_result.get("removed", {}).keys())
                    if rec.get("effective_name") not in _removed_names:
                        _new_installed_records.append(rec)
        else:
            # No MCP delta — preserve existing installed records
            _new_installed_records = _get_plugin_mcp_servers(slug, DB_PATH)

        if _new_installed_records:
            new_manifest_dict["mcp_servers_installed"] = _new_installed_records

        conn.execute(
            "UPDATE plugins_installed SET version = ?, manifest_json = ? WHERE slug = ?",
            (new_version, json.dumps(new_manifest_dict), slug)
        )
        conn.commit()

        # 13. Audit log
        _audit(conn, slug, "update", {"from": installed_version, "to": new_version, "sql_sha_preserved": True})
        invalidate_agent_meta_cache()

        return jsonify({
            "status": "updated",
            "id": slug,
            "from_version": installed_version,
            "to_version": new_version,
            "mcp_delta": mcp_delta_result,
        })

    except Exception as exc:
        logger.error("Plugin update failed for '%s': %s", slug, exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/plugins/marketplace — cached registry fetch (Step 13)
# ---------------------------------------------------------------------------

@bp.route("/api/plugins/marketplace", methods=["GET"])
@login_required
def marketplace():
    try:
        from plugin_registry import fetch_registry
        result = fetch_registry()
        return jsonify(result)
    except ImportError:
        return jsonify({"plugins": [], "error": "registry_module_not_available"})
    except Exception as exc:
        return jsonify({"plugins": [], "error": str(exc)})


# ---------------------------------------------------------------------------
# Wave 2.0: GET /api/agent-meta — merged registry (natives + plugin agents)
#
# Response shape: { slug: { label, avatar_url } }
# Cached in-process; invalidated by install / uninstall / update.
# Frontend hydrates agent-meta.ts once on mount — getAgentMeta() stays sync.
# ---------------------------------------------------------------------------

_AGENT_META_CACHE: dict | None = None
_AGENT_META_LOCK = threading.Lock()


def invalidate_agent_meta_cache() -> None:
    """Clear the agent-meta cache. Call after install / uninstall / update."""
    global _AGENT_META_CACHE
    with _AGENT_META_LOCK:
        _AGENT_META_CACHE = None


def _build_agent_meta_response() -> dict:
    """Build the merged {slug → {label, avatar_url}} dict.

    Merges:
    1. Native agent seed (38 agents, static).
    2. Plugin agents from installed + active plugins that declare ``agents``
       with an ``avatar`` in their manifest.
    """
    from agent_meta_seed import NATIVE_AGENT_SEED

    result: dict = {}

    # 1. Start with native seed
    for slug, entry in NATIVE_AGENT_SEED.items():
        result[slug] = {"label": entry["label"], "avatar_url": entry["avatar_url"]}

    # 2. Merge plugin agents
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT slug, manifest_json FROM plugins_installed WHERE enabled = 1 AND status = 'active'"
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("agent-meta: DB query failed, returning native-only seed: %s", exc)
        return result

    for row in rows:
        plugin_slug = row["slug"]
        try:
            manifest = json.loads(row["manifest_json"] or "{}")
        except Exception:
            continue

        agents_entries = manifest.get("agents") or []
        for agent_entry in agents_entries:
            file_path = agent_entry.get("file", "")
            avatar_path = agent_entry.get("avatar")
            if not file_path:
                continue

            # Derive namespaced slug: agents/pm-nova.md → plugin-pm-essentials-pm-nova
            filename = Path(file_path).stem  # "pm-nova"
            namespaced_slug = f"plugin-{plugin_slug}-{filename}"

            label = f"{manifest.get('name', plugin_slug)} / {filename}"
            avatar_url: str | None = None
            if avatar_path:
                # avatar_path is relative to plugin dir (e.g. ui/assets/avatars/pm-nova.png)
                avatar_url = f"/plugins/{plugin_slug}/ui/{avatar_path[len('ui/') if avatar_path.startswith('ui/') else 0:]}"
                # Always use the clean /plugins/{slug}/ui/{subpath} form
                if avatar_path.startswith("ui/"):
                    avatar_url = f"/plugins/{plugin_slug}/ui/{avatar_path[3:]}"
                else:
                    avatar_url = f"/plugins/{plugin_slug}/ui/{avatar_path}"

            result[namespaced_slug] = {"label": label, "avatar_url": avatar_url}

    return result


@bp.route("/api/agent-meta", methods=["GET"])
@login_required
def get_agent_meta():
    """Return merged agent metadata dict (Wave 2.0).

    Cached in-process; invalidated by install / uninstall / update.
    Response: { slug: { label: str, avatar_url: str | null } }
    """
    global _AGENT_META_CACHE
    with _AGENT_META_LOCK:
        if _AGENT_META_CACHE is None:
            _AGENT_META_CACHE = _build_agent_meta_response()
        return jsonify(_AGENT_META_CACHE)
