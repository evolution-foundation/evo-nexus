"""Heartbeat Dispatcher — schedules and dispatches heartbeat wake triggers.

Manages:
- interval: APScheduler jobs per heartbeat
- manual: triggered by POST /api/heartbeats/{id}/run
- new_task / mention / approval_decision: stubs (F1.2/F1.3)

Debounce: same heartbeat cannot trigger twice within 30s.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

import schedule
from sqlalchemy import text

WORKSPACE = Path(__file__).resolve().parent.parent.parent

# Thread pool for async heartbeat runs (size 4)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hb-worker")
_schedule_lock = threading.Lock()

# Debounce window (seconds)
DEBOUNCE_SECONDS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_db():
    """Return a SQLAlchemy Connection (replaces raw sqlite3.connect)."""
    from db.engine import get_engine
    return get_engine().connect()


def _is_debounced(heartbeat_id: str) -> tuple[bool, str | None]:
    """Check if a heartbeat was triggered within the debounce window.

    Returns (is_debounced, existing_trigger_id).
    """
    conn = _get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=DEBOUNCE_SECONDS)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        row = conn.execute(
            text("""SELECT id FROM heartbeat_triggers
               WHERE heartbeat_id = :hbid AND created_at > :cutoff AND consumed_at IS NULL
               AND coalesced_into IS NULL
               ORDER BY created_at DESC LIMIT 1"""),
            {"hbid": heartbeat_id, "cutoff": cutoff},
        ).fetchone()
        if row:
            return True, row.id
        return False, None
    finally:
        conn.close()


def _record_trigger(heartbeat_id: str, trigger_type: str, payload: dict | None = None, coalesced_into: str | None = None) -> str:
    """Insert a trigger event and return its id."""
    trigger_id = str(uuid.uuid4())
    now = _now_iso()
    conn = _get_db()
    try:
        conn.execute(
            text("""INSERT INTO heartbeat_triggers
               (id, heartbeat_id, trigger_type, payload, created_at, coalesced_into)
               VALUES (:id, :hbid, :ttype, :payload, :now, :cinto)"""),
            {"id": trigger_id, "hbid": heartbeat_id, "ttype": trigger_type,
             "payload": json.dumps(payload or {}), "now": now, "cinto": coalesced_into},
        )
        conn.commit()
        return trigger_id
    finally:
        conn.close()


def _mark_trigger_consumed(trigger_id: str):
    """Mark trigger as consumed (run dispatched)."""
    conn = _get_db()
    try:
        conn.execute(
            text("UPDATE heartbeat_triggers SET consumed_at = :now WHERE id = :id"),
            {"now": _now_iso(), "id": trigger_id},
        )
        conn.commit()
    finally:
        conn.close()


def dispatch(heartbeat_id: str, trigger_type: str, payload: dict | None = None) -> tuple[bool, str | None]:
    """Dispatch a heartbeat run with debounce protection.

    Returns (dispatched, run_id).
    Returns (False, None) if debounced or disabled.
    """
    # Check if heartbeat is enabled in DB
    conn = _get_db()
    try:
        row = conn.execute(
            text("SELECT enabled FROM heartbeats WHERE id = :id"),
            {"id": heartbeat_id},
        ).fetchone()
        if not row or not row.enabled:
            print(f"[dispatcher] heartbeat {heartbeat_id} is disabled, skipping", flush=True)
            return False, None
    finally:
        conn.close()

    # Debounce check
    debounced, existing_id = _is_debounced(heartbeat_id)
    if debounced:
        # Record coalesced trigger
        _record_trigger(heartbeat_id, trigger_type, payload, coalesced_into=existing_id)
        print(f"[dispatcher] {heartbeat_id} debounced (coalesced into {existing_id})", flush=True)
        return False, None

    # Record the trigger
    trigger_id = _record_trigger(heartbeat_id, trigger_type, payload)

    # Generate run_id
    run_id = str(uuid.uuid4())

    def _run():
        from heartbeat_runner import run_heartbeat
        try:
            _mark_trigger_consumed(trigger_id)
            run_heartbeat(
                heartbeat_id=heartbeat_id,
                triggered_by=trigger_type,
                trigger_id=trigger_id,
                run_id=run_id,
            )
        except Exception as exc:
            print(f"[dispatcher] ERROR running {heartbeat_id} run_id={run_id}: {exc}", flush=True)

    print(f"[dispatcher] dispatching {heartbeat_id} trigger_type={trigger_type} run_id={run_id}", flush=True)
    _executor.submit(_run)
    return True, run_id


# ── Interval scheduler ────────────────────────────────────────────────────────

def _load_enabled_heartbeats() -> list[dict]:
    """Load all heartbeats from DB (synced from YAML at startup)."""
    conn = _get_db()
    try:
        rows = conn.execute(text("SELECT * FROM heartbeats")).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        conn.close()


def _get_dialect() -> str:
    """Return active SQLAlchemy dialect name ('postgresql' or 'sqlite')."""
    from db.engine import get_engine
    return get_engine().dialect.name


def _sync_heartbeats_to_db():
    """Mirror config/heartbeats.yaml into heartbeats table (SQLite mode only).

    In PostgreSQL mode this is a no-op: the DB is the source of truth and YAML
    is never read at runtime (PG-NC-3 / ADR pg-native-configs).
    """
    import sys

    if _get_dialect() == "postgresql":
        # PG mode: DB is source of truth — no YAML sync needed.
        print("[dispatcher] PG mode: skipping YAML sync (DB is source of truth)", flush=True)
        return

    # SQLite mode: mirror YAML → DB as before.
    # Ensure backend dir is in path
    backend_dir = Path(__file__).resolve().parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    try:
        from heartbeat_schema import load_heartbeats_yaml
        cfg = load_heartbeats_yaml()
    except Exception as exc:
        print(f"[dispatcher] WARNING could not load heartbeats.yaml: {exc}", flush=True)
        return

    now = _now_iso()
    conn = _get_db()
    try:
        for hb in cfg.heartbeats:
            existing = conn.execute(
                text("SELECT id FROM heartbeats WHERE id = :id"),
                {"id": hb.id},
            ).fetchone()
            if not existing:
                conn.execute(
                    text("""INSERT INTO heartbeats
                       (id, agent, interval_seconds, max_turns, timeout_seconds,
                        lock_timeout_seconds, wake_triggers, enabled, goal_id,
                        required_secrets, decision_prompt, source_plugin,
                        handler, created_at, updated_at)
                       VALUES (:id, :agent, :ivs, :mt, :ts, :lts, :wt, :en, :gid,
                               :rs, :dp, :sp, :handler, :cat, :uat)"""),
                    {
                        "id": hb.id, "agent": hb.agent, "ivs": hb.interval_seconds,
                        "mt": hb.max_turns, "ts": hb.timeout_seconds,
                        "lts": hb.lock_timeout_seconds,
                        "wt": json.dumps(hb.wake_triggers), "en": int(hb.enabled),
                        "gid": hb.goal_id, "rs": json.dumps(hb.required_secrets),
                        "dp": hb.decision_prompt, "sp": hb.source_plugin,
                        "handler": hb.handler,
                        "cat": now, "uat": now,
                    },
                )
            else:
                # Update mutable fields but preserve enabled state set via UI
                conn.execute(
                    text("""UPDATE heartbeats SET
                       agent=:agent, interval_seconds=:ivs, max_turns=:mt,
                       timeout_seconds=:ts, lock_timeout_seconds=:lts,
                       wake_triggers=:wt, goal_id=:gid,
                        required_secrets=:rs, decision_prompt=:dp,
                       source_plugin=:sp, handler=:handler, updated_at=:uat
                       WHERE id=:id"""),
                    {
                        "agent": hb.agent, "ivs": hb.interval_seconds, "mt": hb.max_turns,
                        "ts": hb.timeout_seconds, "lts": hb.lock_timeout_seconds,
                        "wt": json.dumps(hb.wake_triggers), "gid": hb.goal_id,
                        "rs": json.dumps(hb.required_secrets), "dp": hb.decision_prompt,
                        "sp": hb.source_plugin, "handler": hb.handler, "uat": now,
                        "id": hb.id,
                    },
                )
        conn.commit()
        print(f"[dispatcher] synced {len(cfg.heartbeats)} heartbeats from YAML to DB", flush=True)
    finally:
        conn.close()


def _reload_definitions() -> None:
    """Reload heartbeat definitions from DB and re-register interval jobs.

    Called by the LISTEN thread when a 'config_changed' notification arrives
    for the heartbeats table.  Protected by _schedule_lock to avoid races
    with the running schedule loop.
    """
    print("[dispatcher] reloading heartbeat definitions from DB", flush=True)
    with _schedule_lock:
        schedule.clear()
    register_interval_jobs()


def _start_listen_thread() -> None:
    """Start a background thread that LISTENs on 'config_changed' (PG mode only).

    On receiving a notification payload with table='heartbeats', calls
    _reload_definitions() so the dispatcher picks up additions/removals without
    a restart.

    Architecture note:
        This implements a *per-dispatcher* LISTEN connection (1 extra PG conn).
        ADR PG-NC-8 v2 specifies a single Redis-backed multiplexer as the
        target architecture to cap PG connections at 1 across all processes.
        NOTE(PG-NC-8): Redis pub-sub multiplexer deferred — see workspace/development/features/pg-native-configs/[C]known-deferrals.md.

    SQLite: no-op — YAML file changes are not watched here.
    """
    if _get_dialect() != "postgresql":
        return  # SQLite uses YAML reload on each call; no persistent listener.

    try:
        import psycopg2  # type: ignore[import]
        import select as _select
    except ImportError:
        print(
            "[dispatcher] WARNING: psycopg2 not installed — LISTEN/NOTIFY hot-reload disabled",
            flush=True,
        )
        return

    _stop_event = threading.Event()

    def _listener() -> None:
        from db.engine import get_engine
        raw_url = get_engine().url.render_as_string(hide_password=False)
        # Convert SQLAlchemy URL scheme to psycopg2-compatible DSN.
        # e.g. 'postgresql+psycopg2://...' -> 'postgresql://...'
        dsn = raw_url.replace("postgresql+psycopg2://", "postgresql://")

        conn = None
        while not _stop_event.is_set():
            try:
                conn = psycopg2.connect(dsn)
                conn.set_isolation_level(
                    psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT
                )
                cur = conn.cursor()
                cur.execute("LISTEN config_changed;")
                print("[dispatcher] LISTEN config_changed registered", flush=True)

                while not _stop_event.is_set():
                    ready = _select.select([conn], [], [], 5.0)
                    if ready == ([], [], []):
                        continue  # timeout — loop again
                    try:
                        conn.poll()
                    except Exception as poll_exc:
                        print(
                            f"[dispatcher] LISTEN poll error: {poll_exc} — reconnecting",
                            flush=True,
                        )
                        break  # break inner loop → reconnect

                    while conn.notifies:
                        notif = conn.notifies.pop(0)
                        try:
                            payload = json.loads(notif.payload)
                        except (ValueError, TypeError):
                            payload = {}
                        if payload.get("table") == "heartbeats":
                            _reload_definitions()

            except Exception as exc:
                print(
                    f"[dispatcher] LISTEN connection error: {exc} — retrying in 5s",
                    flush=True,
                )
                threading.Event().wait(5)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                conn = None

    t = threading.Thread(target=_listener, daemon=True, name="hb-listen")
    t.start()
    print("[dispatcher] LISTEN thread started (PG mode)", flush=True)


def register_interval_jobs():
    """Register schedule jobs for all heartbeats with 'interval' wake trigger."""
    _sync_heartbeats_to_db()
    heartbeats = _load_enabled_heartbeats()

    registered = 0
    for hb in heartbeats:
        try:
            triggers = json.loads(hb.get("wake_triggers", "[]"))
        except Exception:
            triggers = []

        if "interval" not in triggers:
            continue

        if not hb.get("enabled"):
            continue

        interval_secs = hb["interval_seconds"]
        hb_id = hb["id"]

        # Use schedule library (same as scheduler.py)
        # Tag the job so we can cancel it if needed
        tag = f"hb-interval-{hb_id}"

        def _make_job(heartbeat_id: str):
            def _job():
                dispatch(heartbeat_id, "interval")
            return _job

        with _schedule_lock:
            # Remove any existing job for this heartbeat
            schedule.clear(tag)

            if interval_secs < 60:
                interval_secs = 60  # safety floor

            if interval_secs % 3600 == 0:
                hours = interval_secs // 3600
                schedule.every(hours).hours.do(_make_job(hb_id)).tag(tag)
                print(f"[dispatcher] registered interval job for {hb_id} every {hours}h", flush=True)
            elif interval_secs % 60 == 0:
                minutes = interval_secs // 60
                schedule.every(minutes).minutes.do(_make_job(hb_id)).tag(tag)
                print(f"[dispatcher] registered interval job for {hb_id} every {minutes}m", flush=True)
            else:
                schedule.every(interval_secs).seconds.do(_make_job(hb_id)).tag(tag)
                print(f"[dispatcher] registered interval job for {hb_id} every {interval_secs}s", flush=True)

            registered += 1

    print(f"[dispatcher] {registered} interval jobs registered", flush=True)


def start_dispatcher_thread():
    """Start the heartbeat dispatcher and (in PG mode) the LISTEN thread."""
    def _loop():
        import time
        register_interval_jobs()
        while True:
            schedule.run_pending()
            time.sleep(5)

    t = threading.Thread(target=_loop, name="heartbeat-dispatcher", daemon=True)
    t.start()
    print("[dispatcher] dispatcher thread started", flush=True)

    # PG mode: start LISTEN thread for hot-reload on DB changes.
    _start_listen_thread()


# ── Config reload (called by plugin_loader after install/uninstall) ──────────

def reload_config() -> dict:
    """Re-sync heartbeats and re-register interval jobs.

    In SQLite mode: re-syncs from YAML (called by plugin_loader after install/uninstall).
    In PG mode: re-reads from DB directly (YAML is never consulted).

    Safe to call while the dispatcher is running — uses _schedule_lock.

    Returns:
        Dict with keys: heartbeats_loaded (int), jobs_registered (int).
    """
    import logging
    logger = logging.getLogger(__name__)

    if _get_dialect() == "postgresql":
        logger.info("[reload_config] PG mode: reloading heartbeats from DB")
    else:
        logger.info("[reload_config] SQLite mode: re-syncing heartbeats from YAML")
        _sync_heartbeats_to_db()

    with _schedule_lock:
        schedule.clear()

    count = register_interval_jobs()
    logger.info("[reload_config] Done: %d heartbeats, %d interval jobs", count, count)
    return {"heartbeats_loaded": count, "jobs_registered": count}


# ── Stub hooks for future triggers ───────────────────────────────────────────

def on_new_task(heartbeat_id: str, task_id: str):
    """Hook called when a new task is assigned. F1.3 will implement fully."""
    dispatch(heartbeat_id, "new_task", payload={"task_id": task_id})


def on_mention(heartbeat_id: str, mention_data: dict):
    """Hook called on agent mention. F1.3 will implement fully."""
    dispatch(heartbeat_id, "mention", payload=mention_data)


def on_approval_decision(heartbeat_id: str, approval_id: str, decision: str):
    """Hook called on approval resolution. F1.2 will implement fully."""
    dispatch(heartbeat_id, "approval_decision", payload={"approval_id": approval_id, "decision": decision})
