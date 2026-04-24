"""Plugin install state machine — persistent, crash-safe install orchestration.

ADR-5: Each install step is saved to .install-state.json BEFORE execution.
On completion, renamed to .install-manifest.json.
On crash/restart, _crash_recovery_on_boot() detects orphaned state and rolls back.

B1 (Raven): rollback_from_state() reverses steps in reverse order using
accumulated state — does NOT require .install-manifest.json to exist.
B2 (Raven): per-slug fcntl lock prevents concurrent installs of the same plugin.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PLUGINS_DIR = WORKSPACE / "plugins"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------

def _state_path(slug: str) -> Path:
    return PLUGINS_DIR / slug / ".install-state.json"


def _manifest_path(slug: str) -> Path:
    return PLUGINS_DIR / slug / ".install-manifest.json"


def _lock_path(slug: str) -> Path:
    """Per-slug file lock (B2 Raven: prevents concurrent installs)."""
    return PLUGINS_DIR / slug / ".lock"


class InstallLock:
    """Context manager for per-slug install lock (fcntl LOCK_EX | LOCK_NB)."""

    def __init__(self, slug: str):
        self._slug = slug
        self._lock_path = _lock_path(slug)
        self._fd: int | None = None

    def __enter__(self) -> "InstallLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(
                f"Plugin '{self._slug}' is already being installed or uninstalled. "
                "Try again in a moment."
            )
        return self

    def __exit__(self, *_):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


def save_state(slug: str, state: dict) -> None:
    """Atomically save install state to .install-state.json (ADR-5)."""
    state_path = _state_path(slug)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_path)


def load_state(slug: str) -> dict | None:
    """Load install state from .install-state.json if it exists."""
    state_path = _state_path(slug)
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read install state for '%s': %s", slug, exc)
        return None


def finalize_install(slug: str, final_manifest: dict) -> None:
    """Rename .install-state.json to .install-manifest.json on successful completion."""
    state_path = _state_path(slug)
    manifest_path = _manifest_path(slug)
    final_manifest["completed_at"] = _now_iso()
    manifest_path.write_text(
        json.dumps(final_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    state_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def rollback_from_state(slug: str, state: dict, db_path: Path) -> list[str]:
    """Reverse the steps that were completed, in reverse order.

    B1 (Raven): rollback is driven by the accumulated state — does NOT require
    .install-manifest.json. Called both on explicit failure and on crash recovery.

    Returns list of rollback log messages.
    """
    import shutil
    from plugin_file_ops import remove_rules_index

    log: list[str] = []
    completed = state.get("completed_steps", [])

    # Reverse order
    for step in reversed(completed):
        step_name = step.get("step")
        try:
            _rollback_step(slug, step_name, step, db_path, log)
        except Exception as exc:
            log.append(f"WARN: rollback of step '{step_name}' raised: {exc}")

    # Clean up plugin directory
    plugin_dir = PLUGINS_DIR / slug
    if plugin_dir.exists():
        try:
            shutil.rmtree(plugin_dir)
            log.append(f"Removed plugin dir: {plugin_dir}")
        except OSError as exc:
            log.append(f"WARN: could not remove plugin dir {plugin_dir}: {exc}")

    # Clean up state file
    _state_path(slug).unlink(missing_ok=True)

    return log


def _rollback_step(slug: str, step_name: str, step_data: dict, db_path: Path, log: list[str]) -> None:
    """Reverse a single install step."""
    import shutil

    if step_name == "rules_index_marker":
        try:
            from plugin_file_ops import remove_rules_index
            remove_rules_index(slug)
            log.append(f"Rolled back: rules_index_marker for '{slug}'")
        except Exception as exc:
            log.append(f"WARN: rules_index rollback failed: {exc}")

    elif step_name == "sql_migrations":
        uninstall_sql_path = PLUGINS_DIR / slug / "migrations" / "uninstall.sql"
        if uninstall_sql_path.exists() and db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                from plugin_migrator import run_sql_transactional
                run_sql_transactional(conn, uninstall_sql_path.read_text(encoding="utf-8"))
                conn.close()
                log.append("Rolled back: sql_migrations")
            except Exception as exc:
                log.append(f"WARN: sql_migrations rollback failed: {exc}")

    elif step_name == "db_register":
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                conn.execute("DELETE FROM plugins_installed WHERE slug = ?", (slug,))
                conn.commit()
                conn.close()
                log.append("Rolled back: db_register")
            except Exception as exc:
                log.append(f"WARN: db_register rollback failed: {exc}")

    elif step_name in ("copy_agents", "copy_skills", "copy_commands", "copy_rules",
                       "copy_claude_hooks", "copy_widgets"):
        # Use copied_files list from step data if available
        copied = step_data.get("copied_files", [])
        for file_info in reversed(copied):
            dest = Path(file_info.get("dest", ""))
            if dest.exists():
                try:
                    dest.unlink()
                    log.append(f"Rolled back: deleted {dest}")
                except OSError as exc:
                    log.append(f"WARN: could not delete {dest}: {exc}")

    elif step_name == "install_mcp_servers":
        # Rollback: remove injected MCP entries from ~/.claude.json
        installed_records = step_data.get("installed_records", [])
        if installed_records:
            try:
                from plugin_claude_config import remove_mcp_servers
                audit = remove_mcp_servers(slug, installed_records)
                log.append(f"Rolled back: install_mcp_servers — audit: {audit}")
            except Exception as exc:
                log.append(f"WARN: install_mcp_servers rollback failed: {exc}")
        else:
            log.append("Note: install_mcp_servers had no installed_records to roll back")

    elif step_name == "heartbeats_union":
        log.append("Note: heartbeats_union rollback is handled by plugin dir removal")

    elif step_name == "routines_union":
        log.append("Note: routines_union rollback is handled by plugin dir removal + SIGHUP")


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Wave 2.3 — MCP tracking helpers
# ---------------------------------------------------------------------------

def get_plugin_mcp_servers(slug: str, db_path: Path) -> list[dict]:
    """Return the mcp_servers_installed list for an installed plugin.

    Reads manifest_json["mcp_servers_installed"] from plugins_installed.
    Returns [] if the plugin is not found or has no MCP servers.

    Args:
        slug:    Plugin slug.
        db_path: Path to the SQLite database.

    Returns:
        List of installed server records: [{effective_name, command, args_hash, env_keys}]
    """
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT manifest_json FROM plugins_installed WHERE slug = ?", (slug,)
        ).fetchone()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("get_plugin_mcp_servers: DB error for '%s': %s", slug, exc)
        return []

    if not row:
        return []

    try:
        manifest = json.loads(row[0] or "{}")
    except (json.JSONDecodeError, TypeError):
        return []

    return manifest.get("mcp_servers_installed", [])


def all_plugin_mcp_names(db_path: Path) -> set[str]:
    """Return the set of all effective MCP names across all active plugins.

    Used for pre-install collision detection (ADR decision #1).

    Args:
        db_path: Path to the SQLite database.

    Returns:
        Set of effective_name strings (e.g. {"plugin-pm-essentials-sprint-board"}).
    """
    names: set[str] = set()
    if not db_path.exists():
        return names
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT manifest_json FROM plugins_installed WHERE enabled = 1 AND status = 'active'"
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("all_plugin_mcp_names: DB error: %s", exc)
        return names

    for (manifest_json,) in rows:
        try:
            manifest = json.loads(manifest_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for rec in manifest.get("mcp_servers_installed", []):
            eff = rec.get("effective_name")
            if eff:
                names.add(eff)

    return names


def crash_recovery_on_boot(db_path: Path) -> list[str]:
    """Called on Flask app startup — detect and roll back orphaned installs.

    ADR-5: Any plugin with .install-state.json but no .install-manifest.json
    is assumed to have crashed mid-install.
    """
    log: list[str] = []
    if not PLUGINS_DIR.exists():
        return log

    for plugin_dir in PLUGINS_DIR.glob("*/"):
        if plugin_dir.name.startswith("."):
            continue
        state_path = plugin_dir / ".install-state.json"
        manifest_path = plugin_dir / ".install-manifest.json"

        if state_path.exists() and not manifest_path.exists():
            slug = plugin_dir.name
            logger.warning(
                "Found orphaned install state for plugin '%s' — rolling back", slug
            )
            state = load_state(slug)
            if state:
                rollback_log = rollback_from_state(slug, state, db_path)
                log.extend([f"[crash-recovery:{slug}] {msg}" for msg in rollback_log])
            else:
                # State unreadable — just clean up the dir
                import shutil
                try:
                    shutil.rmtree(plugin_dir)
                    log.append(f"[crash-recovery:{slug}] removed unreadable state dir")
                except OSError as exc:
                    log.append(f"[crash-recovery:{slug}] WARN: could not remove dir: {exc}")

    return log
