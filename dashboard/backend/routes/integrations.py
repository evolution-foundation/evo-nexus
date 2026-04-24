"""Integrations endpoint — check configured integrations via env vars."""

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

import requests as http
from flask import Blueprint, jsonify, request
from flask_login import current_user

from models import audit
from routes.knowledge import _require_xhr

bp = Blueprint("integrations", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
SKILLS_DIR = WORKSPACE / ".claude" / "skills"
PLUGINS_DIR = WORKSPACE / "plugins"
DB_PATH = WORKSPACE / "dashboard" / "data" / "dashboard.db"

#
# Each entry declares the env vars that must all be set for the integration to
# be considered "configured". When only one key matters (e.g. a bare token),
# pass a single-element list. `prefix: True` switches the semantics so that
# configured = at least one env var starts with any of the listed prefixes —
# used for multi-account integrations (YouTube/Instagram/LinkedIn/AI Image).
#
# Keep this list in sync with `dashboard/frontend/src/lib/integrationMeta.ts`
# — that file owns the UI schema (labels, hints, ordering) and declares the
# same env keys for each integration.
INTEGRATIONS = [
    {"name": "Omie", "keys": ["OMIE_APP_KEY", "OMIE_APP_SECRET"], "category": "erp"},
    {"name": "Bling", "keys": ["BLING_CLIENT_ID", "BLING_CLIENT_SECRET"], "category": "erp"},
    {"name": "Stripe", "keys": ["STRIPE_SECRET_KEY"], "category": "payments"},
    {"name": "Asaas", "keys": ["ASAAS_API_KEY"], "category": "payments"},
    {"name": "Todoist", "keys": ["TODOIST_API_TOKEN"], "category": "productivity"},
    {"name": "Fathom", "keys": ["FATHOM_API_KEY"], "category": "meetings"},
    {"name": "Discord", "keys": ["DISCORD_BOT_TOKEN"], "category": "community"},
    {"name": "Telegram", "keys": ["TELEGRAM_BOT_TOKEN"], "category": "messaging"},
    {"name": "YouTube", "keys": ["SOCIAL_YOUTUBE_"], "category": "social", "prefix": True},
    {"name": "Instagram", "keys": ["SOCIAL_INSTAGRAM_"], "category": "social", "prefix": True},
    {"name": "LinkedIn", "keys": ["SOCIAL_LINKEDIN_"], "category": "social", "prefix": True},
    {"name": "Evolution API", "keys": ["EVOLUTION_API_KEY", "EVOLUTION_API_URL"], "category": "messaging"},
    {"name": "Evolution Go", "keys": ["EVOLUTION_GO_KEY", "EVOLUTION_GO_URL"], "category": "messaging"},
    {"name": "Evo CRM", "keys": ["EVO_CRM_TOKEN", "EVO_CRM_URL"], "category": "crm"},
    {"name": "AI Image Creator", "keys": ["AI_IMG_CREATOR_"], "category": "creative", "prefix": True},
    # Note: LLM providers (OpenAI, Anthropic, Gemini) are NOT listed here.
    # Agents/classifiers use Claude Code as the runner (subprocess); Knowledge
    # embedder accepts OpenAI as an opt-in via Knowledge Settings.
]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter between --- markers."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fm_text = "\n".join(lines[1:end])
    try:
        return yaml.safe_load(fm_text) or {}
    except Exception:
        return {}


def _quote_env_value(value: str) -> str:
    """Double-quote an env value, escaping internal quotes and backslashes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _upsert_env_vars(
    env_path: Path,
    kvs: dict,
    section_comment: str | None = None,
) -> None:
    """Upsert key=value pairs into a .env file atomically.

    - Updates existing KEY= lines in-place.
    - Appends new keys with an optional section comment before the first new one.
    - Writes atomically via tmp-file + rename.
    - Never logs values — only key names.
    """
    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)

    updated: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        matched = False
        for key in kvs:
            if re.match(rf"^{re.escape(key)}\s*=", line):
                new_lines.append(f"{key}={_quote_env_value(kvs[key])}\n")
                updated.add(key)
                log.info("upsert_env_vars: updated key %s", key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    # Append new keys not yet present
    first_new = True
    for key, value in kvs.items():
        if key not in updated:
            if first_new and section_comment:
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines.append("\n")
                new_lines.append(f"# {section_comment}\n")
                first_new = False
            new_lines.append(f"{key}={_quote_env_value(value)}\n")
            log.info("upsert_env_vars: appended key %s", key)

    content = "".join(new_lines)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=env_path.parent, prefix=".env.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, env_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _get_health_cache(plugin_slug: str, integration_slug: str) -> dict | None:
    """Return the latest health cache row for a plugin integration, or None."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT last_status, last_checked_at, last_error "
            "FROM integration_health_cache WHERE plugin_slug = ? AND integration_slug = ?",
            (plugin_slug, integration_slug),
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


def _scan_custom_integrations() -> list:
    """Scan SKILLS_DIR for custom-int-* subdirs and installed plugins for integration entries."""
    results = []

    # --- legacy custom-int-* skills ---
    if SKILLS_DIR.is_dir():
        for d in sorted(SKILLS_DIR.iterdir()):
            if not d.is_dir() or not d.name.startswith("custom-int-"):
                continue
            slug = d.name[len("custom-int-"):]
            skill_md = d / "SKILL.md"
            fm: dict = {}
            if skill_md.exists():
                fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))

            display_name = fm.get("displayName") or slug.replace("-", " ").title()
            description = fm.get("description") or ""
            category = fm.get("category") or "other"
            env_keys = fm.get("envKeys") or []

            results.append({
                "slug": slug,
                "name": display_name,
                "category": category,
                "description": description,
                "envKeys": env_keys,
                "configured": any(bool(os.environ.get(k)) for k in env_keys) if env_keys else False,
                "status": "ok" if (env_keys and any(bool(os.environ.get(k)) for k in env_keys)) else "pending",
                "type": category,
                "kind": "custom",
            })

    # --- Wave 2.2r: plugin integrations ---
    if PLUGINS_DIR.is_dir():
        try:
            import yaml  # type: ignore
        except ImportError:
            yaml = None  # type: ignore

        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin_slug = plugin_dir.name
            manifest_path = plugin_dir / "plugin.yaml"
            if not manifest_path.exists() or yaml is None:
                continue
            try:
                raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            declared = raw.get("integrations") or []
            if not declared:
                continue

            try:
                from plugin_schema import PluginIntegration  # type: ignore
            except ImportError:
                continue

            for integ_raw in declared:
                if not isinstance(integ_raw, dict):
                    continue
                try:
                    integ = PluginIntegration.model_validate(integ_raw)
                except Exception:
                    continue

                global_slug = f"plugin-{plugin_slug}-{integ.slug}"
                required_vars = [v for v in integ.env_vars if v.required]
                all_required_set = all(bool(os.environ.get(v.name)) for v in required_vars)
                any_set = any(bool(os.environ.get(v.name)) for v in integ.env_vars) if integ.env_vars else False
                last_health = _get_health_cache(plugin_slug, integ.slug)

                # Compute status: not_configured / connected / error
                if not all_required_set:
                    ui_status = "not_configured"
                elif last_health and last_health.get("last_status") == "error":
                    ui_status = "error"
                else:
                    ui_status = "connected" if all_required_set else "pending"

                results.append({
                    "slug": global_slug,
                    "name": integ.label,
                    "category": integ.category,
                    "description": "",
                    "envKeys": [v.name for v in integ.env_vars],
                    "env_specs": [v.model_dump() for v in integ.env_vars],
                    "configured": all_required_set,
                    "status": ui_status,
                    "type": integ.category,
                    "kind": "plugin",
                    "source_plugin": plugin_slug,
                    "integration_slug": integ.slug,
                    "health_check": integ.health_check.model_dump() if integ.health_check else None,
                    "last_health": last_health,
                })

    return results


def _remove_env_section(env_path: Path, comment: str) -> list[str]:
    """Remove lines belonging to a named section from a .env file.

    A section starts with the marker line ``# {comment}`` and ends at the next
    blank line OR the next line starting with ``# `` (another section header)
    OR EOF.  Returns the list of KEY names removed (for audit).

    A timestamped backup (.env.bak-{ts}) is written before any mutation.
    Only the 5 most recent backups are kept.  Writes atomically via tmp+rename.
    """
    if not env_path.exists():
        return []

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    marker = f"# {comment}\n"

    # Find the marker
    marker_idx = None
    for i, line in enumerate(lines):
        if line == marker:
            marker_idx = i
            break

    if marker_idx is None:
        return []  # section not present — idempotent no-op

    # Consume section lines until next blank or next section header or EOF
    end_idx = marker_idx + 1
    while end_idx < len(lines):
        line = lines[end_idx]
        # Stop at blank line (but include one trailing blank if present)
        if line.strip() == "":
            end_idx += 1  # consume the trailing blank
            break
        # Stop at another section header
        if line.startswith("# ") and end_idx > marker_idx + 1:
            break
        end_idx += 1

    # Collect removed KEY names
    removed_keys: list[str] = []
    for line in lines[marker_idx:end_idx]:
        m = re.match(r"^([A-Z][A-Z0-9_]*)\s*=", line)
        if m:
            removed_keys.append(m.group(1))

    # Write backup before mutation
    import datetime
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    bak_path = env_path.parent / f".env.bak-{ts}"
    try:
        import shutil as _shutil
        _shutil.copy2(env_path, bak_path)
        # Retention: keep only 5 most recent backups
        baks = sorted(env_path.parent.glob(".env.bak-*"), key=lambda p: p.name)
        for old_bak in baks[:-5]:
            try:
                old_bak.unlink()
            except OSError:
                pass
    except Exception as e:
        log.warning("_remove_env_section: backup failed (continuing): %s", e)

    # Write new content without the section
    new_lines = lines[:marker_idx] + lines[end_idx:]
    content = "".join(new_lines)
    fd, tmp_path = tempfile.mkstemp(dir=env_path.parent, prefix=".env.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, env_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    log.info("_remove_env_section: removed section '%s', keys=%s", comment, removed_keys)
    return removed_keys


@bp.route("/api/integrations")
def list_integrations():
    results = []
    for integ in INTEGRATIONS:
        keys = integ["keys"]
        if integ.get("prefix"):
            # At least one env var starts with any of the declared prefixes.
            configured = any(
                any(name.startswith(p) for p in keys) for name in os.environ
            )
        else:
            # All declared keys must be non-empty. Evolution Go / Evo CRM need
            # both the token and the base URL — a half-configured integration
            # is not "connected".
            configured = all(bool(os.environ.get(k)) for k in keys)

        results.append({
            "name": integ["name"],
            "category": integ["category"],
            "configured": configured,
            "status": "ok" if configured else "pending",
            "type": integ["category"],
            "kind": "core",
        })

    custom = _scan_custom_integrations()
    all_integrations = results + custom

    configured_count = sum(1 for r in all_integrations if r.get("configured"))
    return jsonify({
        "integrations": all_integrations,
        "configured_count": configured_count,
        "total_count": len(all_integrations),
    })


@bp.route("/api/integrations/custom", methods=["POST"])
def create_custom_integration():
    _require_xhr()
    data = request.get_json(silent=True) or {}
    slug = (data.get("slug") or "").strip().lower()
    display_name = (data.get("displayName") or "").strip()
    description = (data.get("description") or "").strip()
    category = (data.get("category") or "other").strip()
    env_keys = data.get("envKeys") or []
    env_values: dict = data.get("envValues") or {}

    if not slug:
        return jsonify({"error": "slug is required"}), 400
    if not SLUG_RE.match(slug):
        return jsonify({"error": "slug must be lowercase alphanumeric and hyphens only"}), 400
    if not display_name:
        return jsonify({"error": "displayName is required"}), 400

    target_dir = SKILLS_DIR / f"custom-int-{slug}"
    if target_dir.exists():
        return jsonify({"error": f"Integration '{slug}' already exists"}), 409

    # Build env block for the SKILL.md template (names only — no values)
    env_block_lines = [f"{k}=" for k in env_keys] if env_keys else ["# Add your env vars here"]
    env_block = "\n".join(env_block_lines)

    skill_content = f"""---
name: custom-int-{slug}
displayName: "{display_name}"
description: "{description}"
category: "{category}"
envKeys: {env_keys!r}
---
# {display_name}

Custom integration for {display_name}.

## Setup

Add these to your `.env`:

```
{env_block}
```

## Usage

Use `from dashboard.backend.sdk_client import evo` for any internal API calls.
Document the public endpoints, auth method, and example calls here.
"""

    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")

    # Write env values to .env (values never go into SKILL.md or the response)
    if env_values:
        env_path = WORKSPACE / ".env"
        _upsert_env_vars(env_path, env_values, section_comment=f"custom-int-{slug}")

    try:
        audit(
            current_user,
            "create_custom_integration",
            "integrations",
            f"slug={slug} keys={sorted(env_values.keys()) if env_values else []}",
        )
    except Exception:
        log.warning("integrations.create_custom_integration: audit() failed (non-fatal)", exc_info=True)

    entry = {
        "slug": slug,
        "name": display_name,
        "category": category,
        "description": description,
        "envKeys": env_keys,
        "configured": False,
        "status": "pending",
        "type": category,
        "kind": "custom",
    }
    return jsonify(entry), 201


@bp.route("/api/integrations/custom/<slug>", methods=["PATCH"])
def update_custom_integration(slug: str):
    _require_xhr()
    target_dir = SKILLS_DIR / f"custom-int-{slug}"
    if not target_dir.exists():
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(silent=True) or {}
    skill_md = target_dir / "SKILL.md"
    fm: dict = {}
    if skill_md.exists():
        fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))

    display_name = (data.get("displayName") or fm.get("displayName") or slug.replace("-", " ").title()).strip()
    description = (data.get("description") or fm.get("description") or "").strip()
    category = (data.get("category") or fm.get("category") or "other").strip()
    env_keys = data.get("envKeys") if "envKeys" in data else (fm.get("envKeys") or [])
    env_values: dict = data.get("envValues") or {}

    # Preserve existing body (content after frontmatter) if present
    existing_body = ""
    if skill_md.exists():
        raw = skill_md.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        if len(parts) == 3:
            existing_body = parts[2].strip()

    if not existing_body:
        env_block_lines = [f"{k}=" for k in env_keys] if env_keys else ["# Add your env vars here"]
        env_block = "\n".join(env_block_lines)
        existing_body = f"""# {display_name}

Custom integration for {display_name}.

## Setup

Add these to your `.env`:

```
{env_block}
```

## Usage

Use `from dashboard.backend.sdk_client import evo` for any internal API calls.
Document the public endpoints, auth method, and example calls here."""

    skill_content = f"""---
name: custom-int-{slug}
displayName: "{display_name}"
description: "{description}"
category: "{category}"
envKeys: {env_keys!r}
---
{existing_body}
"""
    skill_md.write_text(skill_content, encoding="utf-8")

    # Write env values to .env (values never go into SKILL.md or the response)
    if env_values:
        env_path = WORKSPACE / ".env"
        _upsert_env_vars(env_path, env_values, section_comment=f"custom-int-{slug}")

    try:
        audit(
            current_user,
            "update_custom_integration",
            "integrations",
            f"slug={slug} keys={sorted(env_values.keys()) if env_values else []}",
        )
    except Exception:
        log.warning("integrations.update_custom_integration: audit() failed (non-fatal)", exc_info=True)

    configured = any(bool(os.environ.get(k)) for k in env_keys) if env_keys else False
    entry = {
        "slug": slug,
        "name": display_name,
        "category": category,
        "description": description,
        "envKeys": env_keys,
        "configured": configured,
        "status": "ok" if configured else "pending",
        "type": category,
        "kind": "custom",
    }
    return jsonify(entry), 200


@bp.route("/api/integrations/custom/<slug>", methods=["DELETE"])
def delete_custom_integration(slug: str):
    _require_xhr()
    target_dir = SKILLS_DIR / f"custom-int-{slug}"
    if not target_dir.exists():
        return jsonify({"error": "Not found"}), 404
    shutil.rmtree(target_dir)
    try:
        audit(current_user, "delete_custom_integration", "integrations", f"slug={slug}")
    except Exception:
        log.warning("integrations.delete_custom_integration: audit() failed (non-fatal)", exc_info=True)
    return jsonify({"ok": True}), 200


@bp.route("/api/integrations/plugin/<path:global_slug>", methods=["POST"])
def configure_plugin_integration(global_slug: str):
    """Save env vars for a plugin integration.

    global_slug format: plugin-{plugin_slug}-{integration_slug}
    Body: {"env_vars": {"KEY": "value", ...}}
    """
    _require_xhr()
    # Parse global_slug: plugin-{plugin_slug}-{integration_slug}
    if not global_slug.startswith("plugin-"):
        return jsonify({"error": "invalid plugin integration slug"}), 400
    remainder = global_slug[len("plugin-"):]
    # plugin_slug may contain hyphens — try each split point until we find
    # a matching integration in the plugin manifest
    plugin_slug = None
    integration_slug = None

    if PLUGINS_DIR.is_dir():
        try:
            import yaml  # type: ignore
        except ImportError:
            return jsonify({"error": "yaml not available"}), 500

        parts = remainder.split("-")
        for i in range(1, len(parts)):
            candidate_plugin = "-".join(parts[:i])
            candidate_integ = "-".join(parts[i:])
            plugin_dir = PLUGINS_DIR / candidate_plugin
            if not (plugin_dir / "plugin.yaml").exists():
                continue
            try:
                raw = yaml.safe_load((plugin_dir / "plugin.yaml").read_text(encoding="utf-8")) or {}
                declared = raw.get("integrations") or []
                for integ_raw in declared:
                    if isinstance(integ_raw, dict) and integ_raw.get("slug") == candidate_integ:
                        plugin_slug = candidate_plugin
                        integration_slug = candidate_integ
                        break
            except Exception:
                pass
            if plugin_slug:
                break

    if not plugin_slug:
        return jsonify({"error": f"plugin integration '{global_slug}' not found"}), 404

    data = request.get_json(silent=True) or {}
    env_vars: dict = data.get("env_vars") or {}
    if not env_vars:
        return jsonify({"error": "env_vars required"}), 400

    # Validate that submitted keys are declared in the integration
    try:
        import yaml  # type: ignore
        from plugin_schema import PluginIntegration  # type: ignore
        raw = yaml.safe_load((PLUGINS_DIR / plugin_slug / "plugin.yaml").read_text(encoding="utf-8")) or {}
        declared = raw.get("integrations") or []
        integ_obj = None
        for ir in declared:
            if isinstance(ir, dict) and ir.get("slug") == integration_slug:
                integ_obj = PluginIntegration.model_validate(ir)
                break
    except Exception as e:
        return jsonify({"error": f"could not load integration schema: {e}"}), 500

    if integ_obj is None:
        return jsonify({"error": "integration not found in manifest"}), 404

    declared_names = {spec.name for spec in integ_obj.env_vars}
    unknown = set(env_vars.keys()) - declared_names
    if unknown:
        return jsonify({"error": f"unknown env var keys: {sorted(unknown)}"}), 400

    env_path = WORKSPACE / ".env"
    _upsert_env_vars(env_path, env_vars, section_comment=f"plugin-{plugin_slug}")

    # Reload dotenv in-process so heartbeats/health-checks pick up new values immediately
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path, override=True)
    except Exception:
        pass

    try:
        audit(
            current_user,
            "configure_plugin_integration",
            "integrations",
            f"plugin={plugin_slug} integration={integration_slug} keys={sorted(env_vars.keys())}",
        )
    except Exception:
        log.warning("configure_plugin_integration: audit() failed (non-fatal)", exc_info=True)

    return jsonify({"ok": True, "plugin": plugin_slug, "integration": integration_slug}), 200


@bp.route("/api/integrations/plugin/<path:global_slug>/test", methods=["POST"])
def test_plugin_integration(global_slug: str):
    """Run the declared HTTP health check for a plugin integration.

    Returns: {"ok": bool, "status_code": int|null, "duration_ms": int, "error": str|null}
    """
    _require_xhr()
    t0 = time.time()

    # Parse global_slug: plugin-{plugin_slug}-{integration_slug}
    if not global_slug.startswith("plugin-"):
        return jsonify({"ok": False, "error": "invalid plugin integration slug"}), 400
    remainder = global_slug[len("plugin-"):]

    plugin_slug = None
    integration_slug = None
    integ_obj = None

    if PLUGINS_DIR.is_dir():
        try:
            import yaml  # type: ignore
            from plugin_schema import PluginIntegration  # type: ignore
        except ImportError:
            return jsonify({"ok": False, "error": "yaml/schema not available"}), 500

        parts = remainder.split("-")
        for i in range(1, len(parts)):
            candidate_plugin = "-".join(parts[:i])
            candidate_integ = "-".join(parts[i:])
            plugin_dir = PLUGINS_DIR / candidate_plugin
            if not (plugin_dir / "plugin.yaml").exists():
                continue
            try:
                raw = yaml.safe_load((plugin_dir / "plugin.yaml").read_text(encoding="utf-8")) or {}
                for ir in (raw.get("integrations") or []):
                    if isinstance(ir, dict) and ir.get("slug") == candidate_integ:
                        integ_obj = PluginIntegration.model_validate(ir)
                        plugin_slug = candidate_plugin
                        integration_slug = candidate_integ
                        break
            except Exception:
                pass
            if plugin_slug:
                break

    if integ_obj is None:
        return jsonify({"ok": False, "error": f"integration '{global_slug}' not found"}), 404

    if not integ_obj.health_check:
        return jsonify({"ok": None, "error": "no health_check declared for this integration"}), 200

    hc = integ_obj.health_check

    # Resolve ${VAR} — restricted to env vars declared in this integration
    declared_names = {spec.name for spec in integ_obj.env_vars}
    url = hc.url

    def _resolve_var(m: "re.Match[str]") -> str:
        var = m.group(1)
        if var not in declared_names:
            # Should not reach here — schema validator blocks it; guard anyway
            raise ValueError(f"${{{var}}} not declared in integration env_vars")
        val = os.environ.get(var, "")
        return val

    try:
        url = re.sub(r"\$\{([^}]+)\}", _resolve_var, url)
    except ValueError as e:
        duration_ms = round((time.time() - t0) * 1000)
        return jsonify({"ok": False, "status_code": None, "duration_ms": duration_ms, "error": str(e)}), 200

    timeout = max(1, min(10, hc.timeout_seconds))
    status_code = None
    error_msg = None
    ok = False
    try:
        resp = http.get(url, timeout=timeout)
        status_code = resp.status_code
        ok = (status_code == hc.expect_status)
        if not ok:
            error_msg = f"expected {hc.expect_status}, got {status_code}"
    except http.exceptions.Timeout:
        error_msg = f"request timed out after {timeout}s"
    except Exception as exc:
        error_msg = str(exc)

    duration_ms = round((time.time() - t0) * 1000)

    # Update health cache
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO integration_health_cache
               (plugin_slug, integration_slug, last_status, last_checked_at, last_error)
               VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'), ?)
               ON CONFLICT(plugin_slug, integration_slug) DO UPDATE SET
                 last_status=excluded.last_status,
                 last_checked_at=excluded.last_checked_at,
                 last_error=excluded.last_error""",
            (plugin_slug, integration_slug, "ok" if ok else "error", error_msg),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return jsonify({
        "ok": ok,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "error": error_msg,
    }), 200


@bp.route("/api/integrations/<name>/test", methods=["POST"])
def test_integration(name: str):
    """Basic connectivity test for an integration."""
    t0 = time.time()

    def ok(message: str = "Conexão OK") -> "tuple[object, int]":
        latency = round((time.time() - t0) * 1000)
        return jsonify({"ok": True, "message": message, "latency_ms": latency}), 200

    def fail(error: str) -> "tuple[object, int]":
        return jsonify({"ok": False, "error": error}), 200

    slug = name.lower().replace(" ", "-").replace("_", "-")

    # --- Stripe ---
    if slug == "stripe":
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not key:
            return fail("STRIPE_SECRET_KEY não configurado")
        try:
            r = http.get(
                "https://api.stripe.com/v1/charges",
                params={"limit": 1},
                auth=(key, ""),
                timeout=8,
            )
            if r.status_code == 200:
                return ok("Stripe conectado com sucesso")
            return fail(f"Stripe retornou {r.status_code}")
        except Exception as e:
            return fail(str(e))

    # --- Omie ---
    if slug == "omie":
        app_key = os.environ.get("OMIE_APP_KEY", "")
        app_secret = os.environ.get("OMIE_APP_SECRET", "")
        if not app_key or not app_secret:
            return fail("OMIE_APP_KEY e OMIE_APP_SECRET não configurados")
        try:
            r = http.post(
                "https://app.omie.com.br/api/v1/geral/clientes/",
                json={
                    "call": "ListarClientes",
                    "app_key": app_key,
                    "app_secret": app_secret,
                    "param": [{"pagina": 1, "registros_por_pagina": 1}],
                },
                timeout=10,
            )
            data = r.json()
            if "faultstring" in data:
                return fail(data["faultstring"])
            return ok("Omie conectado com sucesso")
        except Exception as e:
            return fail(str(e))

    # --- Evolution API ---
    if slug == "evolution-api":
        api_key = os.environ.get("EVOLUTION_API_KEY", "")
        api_url = os.environ.get("EVOLUTION_API_URL", "").rstrip("/")
        if not api_key or not api_url:
            return fail("EVOLUTION_API_KEY e EVOLUTION_API_URL não configurados")
        try:
            r = http.get(
                f"{api_url}/instance/fetchInstances",
                headers={"apikey": api_key},
                timeout=8,
            )
            if r.status_code == 200:
                return ok("Evolution API conectada com sucesso")
            return fail(f"Evolution API retornou {r.status_code}")
        except Exception as e:
            return fail(str(e))

    # --- Todoist ---
    if slug == "todoist":
        token = os.environ.get("TODOIST_API_TOKEN", "")
        if not token:
            return fail("TODOIST_API_TOKEN não configurado")
        try:
            r = http.get(
                "https://api.todoist.com/rest/v2/projects",
                headers={"Authorization": f"Bearer {token}"},
                timeout=8,
            )
            if r.status_code == 200:
                return ok("Todoist conectado com sucesso")
            return fail(f"Todoist retornou {r.status_code}")
        except Exception as e:
            return fail(str(e))

    # Passthrough for integrations without a dedicated test
    return jsonify({"ok": True, "message": "Nenhum teste disponível para esta integração"}), 200
