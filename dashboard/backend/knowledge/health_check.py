"""Knowledge connection health check — runs every 5 minutes per connection.

Implements ADR-005: drift detection on a heartbeat (5 min interval).
Also verifies connectivity and updates last_health_check timestamp.

Public API:
    check_connection_health(connection_id, connection_string, sqlite_conn) -> dict
    start_health_check_thread(get_app_fn)    — background 5-min scheduler
"""

import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import text

from .connection_pool import get_engine

_INTERVAL_S = 300  # 5 minutes

_hc_lock = threading.Lock()
_hc_started = False
_hc_timer = None


# ---------------------------------------------------------------------------
# Single connection health check
# ---------------------------------------------------------------------------

def check_connection_health(
    connection_id: str,
    connection_string: str,
    sqlite_conn,
) -> Dict[str, Any]:
    """Test connectivity + drift for one connection.

    Returns {"status": str, "latency_ms": float, ...}.
    Updates knowledge_connections in SQLite.
    """
    from .auto_migrator import check_drift, get_alembic_head

    start = time.monotonic()
    now_ts = datetime.now(timezone.utc).isoformat()

    try:
        engine = get_engine(connection_id, connection_string)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = (time.monotonic() - start) * 1000

        # Update last_health_check
        sqlite_conn.execute(
            "UPDATE knowledge_connections SET last_health_check = ?, last_error = NULL WHERE id = ?",
            (now_ts, connection_id),
        )
        sqlite_conn.commit()

        # Drift check (ADR-005)
        drift_result = check_drift(connection_id, connection_string, sqlite_conn)

        return {
            "status": "needs_migration" if drift_result.get("needs_migration") else "ready",
            "latency_ms": round(latency_ms, 2),
            "drift": drift_result,
        }

    except Exception as exc:
        sqlite_conn.execute(
            "UPDATE knowledge_connections SET last_health_check = ?, last_error = ?, status = ? WHERE id = ?",
            (now_ts, str(exc)[:500], "disconnected", connection_id),
        )
        sqlite_conn.commit()
        return {"status": "disconnected", "error": str(exc)}


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

def _run_health_checks(get_app_fn) -> None:
    """Run health checks for all 'ready' or 'needs_migration' connections."""
    try:
        app = get_app_fn()
        with app.app_context():
            import os
            from pathlib import Path
            from .crypto import decrypt_secret

            db_path = str(
                Path(app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", ""))
            )
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT id, connection_string_encrypted FROM knowledge_connections "
                "WHERE status IN ('ready', 'needs_migration', 'disconnected')"
            ).fetchall()
            for cid, cs_enc in rows:
                if cs_enc is None:
                    continue
                try:
                    cs = decrypt_secret(bytes(cs_enc))
                    check_connection_health(cid, cs, conn)
                except Exception:
                    pass
            conn.close()
    except Exception:
        pass


def _hc_loop(get_app_fn) -> None:
    _run_health_checks(get_app_fn)
    _schedule_hc(get_app_fn)


def _schedule_hc(get_app_fn) -> None:
    global _hc_timer
    _hc_timer = threading.Timer(_INTERVAL_S, _hc_loop, args=(get_app_fn,))
    _hc_timer.daemon = True
    _hc_timer.start()


def start_health_check_thread(get_app_fn) -> None:
    """Start the background health check scheduler (idempotent).

    Runs the first pass on a short-delay timer (not inline) so app startup
    isn't blocked by slow/unreachable Postgres connections. Without this
    initial pass, the classify_worker would spam the log for up to
    _INTERVAL_S (5 min) on boot if a connection is offline, because stale
    status='ready' from the last session won't be reconciled until the
    first scheduled tick.
    """
    global _hc_started, _hc_timer
    with _hc_lock:
        if _hc_started:
            return
        _hc_started = True
    _hc_timer = threading.Timer(5.0, _hc_loop, args=(get_app_fn,))
    _hc_timer.daemon = True
    _hc_timer.start()
