"""Usage janitor — deletes knowledge_api_usage rows older than 7 days.

Mirrors ticket_janitor.py pattern: daemon thread, configurable interval,
idempotent start.
"""

from __future__ import annotations

import os
import threading
import time

JANITOR_INTERVAL_SECONDS = int(os.getenv("USAGE_JANITOR_INTERVAL", "86400"))  # 24h default
RETENTION_DAYS = int(os.getenv("USAGE_JANITOR_RETENTION_DAYS", "7"))

_janitor_started = False
_janitor_lock = threading.Lock()


def delete_expired_usage(dsn: str | None = None) -> int:
    """Delete knowledge_api_usage rows older than RETENTION_DAYS.

    Returns the number of rows deleted.
    ``dsn`` may be provided explicitly for testing; otherwise resolved from env.
    """
    deleted = 0
    try:
        import psycopg2

        _dsn = dsn or os.environ.get("KNOWLEDGE_POSTGRES_DSN") or os.environ.get("KNOWLEDGE_TEST_DATABASE_URL")
        if not _dsn:
            print("[usage_janitor] no DSN configured, skipping", flush=True)
            return 0

        conn = psycopg2.connect(_dsn, connect_timeout=5)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM knowledge_api_usage
                    WHERE window_start < now() - INTERVAL '%s days'
                    """,
                    (RETENTION_DAYS,),
                )
                deleted = cur.rowcount
        finally:
            conn.close()

        if deleted > 0:
            print(f"[usage_janitor] deleted {deleted} expired usage row(s)", flush=True)

    except Exception as exc:
        print(f"[usage_janitor] ERROR in delete_expired_usage: {exc}", flush=True)

    return deleted


def _janitor_loop(dsn: str | None = None) -> None:
    """Background loop — runs delete_expired_usage every JANITOR_INTERVAL_SECONDS."""
    while True:
        time.sleep(JANITOR_INTERVAL_SECONDS)
        try:
            delete_expired_usage(dsn=dsn)
        except Exception as exc:
            print(f"[usage_janitor] loop error: {exc}", flush=True)


def start_janitor_thread(dsn: str | None = None) -> None:
    """Start the usage janitor background thread (idempotent)."""
    global _janitor_started

    with _janitor_lock:
        if _janitor_started:
            return
        _janitor_started = True

    t = threading.Thread(
        target=_janitor_loop,
        args=(dsn,),
        daemon=True,
        name="usage-janitor",
    )
    t.start()
    print(f"[usage_janitor] started (interval={JANITOR_INTERVAL_SECONDS}s, retention={RETENTION_DAYS}d)", flush=True)
