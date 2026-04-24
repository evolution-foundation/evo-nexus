"""Wave 2.2r — in-process heartbeat handler for plugin integration health checks.

Called by heartbeat_runner when heartbeat config has ``handler: plugin_integration_health.tick``.
Zero Claude CLI invocations — pure Python HTTP polling.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PLUGINS_DIR = WORKSPACE / "plugins"
DB_PATH = WORKSPACE / "dashboard" / "data" / "dashboard.db"

# Hard cap on HTTP timeout regardless of what the manifest declares (ADR decision 4)
_MAX_TIMEOUT_SECONDS = 10
_MIN_TIMEOUT_SECONDS = 1


def _resolve_vars(url: str, declared_names: set[str]) -> Optional[str]:
    """Resolve ${VAR} placeholders in url using os.environ.

    Returns None if any declared var is missing from env (skip instead of
    exposing empty credentials to a remote endpoint).
    Only vars declared in the integration's env_vars are resolved.
    """
    def _replace(m: "re.Match[str]") -> str:
        var = m.group(1)
        if var not in declared_names:
            raise ValueError(f"${{{var}}} not declared in integration env_vars")
        val = os.environ.get(var)
        if not val:
            raise LookupError(f"env var {var} not set")
        return val

    try:
        return re.sub(r"\$\{([^}]+)\}", _replace, url)
    except (ValueError, LookupError) as e:
        log.debug("plugin_integration_health: var resolution failed: %s", e)
        return None


def _upsert_health(
    conn: sqlite3.Connection,
    plugin_slug: str,
    integration_slug: str,
    status: str,
    error: Optional[str],
) -> None:
    conn.execute(
        """INSERT INTO integration_health_cache
           (plugin_slug, integration_slug, last_status, last_checked_at, last_error)
           VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'), ?)
           ON CONFLICT(plugin_slug, integration_slug) DO UPDATE SET
             last_status=excluded.last_status,
             last_checked_at=excluded.last_checked_at,
             last_error=excluded.last_error""",
        (plugin_slug, integration_slug, status, error),
    )
    conn.commit()


def tick() -> dict:
    """Main handler — iterates all installed plugins and runs HTTP health checks.

    Returns a summary dict for logging purposes.
    """
    try:
        import yaml  # type: ignore
        from plugin_schema import PluginIntegration  # type: ignore
        import requests as _http  # type: ignore
    except ImportError as e:
        log.error("plugin_integration_health.tick: missing dependency: %s", e)
        return {"error": str(e), "checked": 0, "ok": 0, "failed": 0}

    if not PLUGINS_DIR.is_dir():
        return {"checked": 0, "ok": 0, "failed": 0}

    conn = sqlite3.connect(str(DB_PATH))
    checked = 0
    ok_count = 0
    fail_count = 0

    try:
        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin_slug = plugin_dir.name
            manifest_path = plugin_dir / "plugin.yaml"
            if not manifest_path.exists():
                continue

            try:
                raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            except Exception as e:
                log.warning("plugin_integration_health: failed to parse %s: %s", manifest_path, e)
                continue

            declared = raw.get("integrations") or []
            for integ_raw in declared:
                if not isinstance(integ_raw, dict):
                    continue
                try:
                    integ = PluginIntegration.model_validate(integ_raw)
                except Exception as e:
                    log.warning("plugin_integration_health: invalid integration in %s: %s", plugin_slug, e)
                    continue

                if not integ.health_check:
                    continue

                hc = integ.health_check
                declared_names = {spec.name for spec in integ.env_vars}
                resolved_url = _resolve_vars(hc.url, declared_names)
                if resolved_url is None:
                    # Required var not set — record as pending, not error
                    _upsert_health(conn, plugin_slug, integ.slug, "pending",
                                   "required env var not configured")
                    continue

                timeout = max(_MIN_TIMEOUT_SECONDS, min(_MAX_TIMEOUT_SECONDS, hc.timeout_seconds))
                checked += 1
                error_msg: Optional[str] = None
                is_ok = False

                try:
                    t0 = time.time()
                    resp = _http.get(resolved_url, timeout=timeout)
                    duration_ms = round((time.time() - t0) * 1000)
                    is_ok = (resp.status_code == hc.expect_status)
                    if not is_ok:
                        error_msg = f"expected {hc.expect_status}, got {resp.status_code}"
                    log.debug(
                        "plugin_integration_health: %s/%s -> %s %dms",
                        plugin_slug, integ.slug, resp.status_code, duration_ms,
                    )
                except _http.exceptions.Timeout:
                    error_msg = f"timeout after {timeout}s"
                except Exception as e:
                    error_msg = str(e)

                _upsert_health(
                    conn, plugin_slug, integ.slug,
                    "ok" if is_ok else "error",
                    error_msg,
                )
                if is_ok:
                    ok_count += 1
                else:
                    fail_count += 1

    finally:
        conn.close()

    log.info(
        "plugin_integration_health.tick: checked=%d ok=%d failed=%d",
        checked, ok_count, fail_count,
    )
    return {"checked": checked, "ok": ok_count, "failed": fail_count}
