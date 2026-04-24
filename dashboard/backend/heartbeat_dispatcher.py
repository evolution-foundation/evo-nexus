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

WORKSPACE = Path(__file__).resolve().parent.parent.parent

# Thread pool for async heartbeat runs (size 4)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hb-worker")
_schedule_lock = threading.Lock()

# Debounce window (seconds)
DEBOUNCE_SECONDS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_db():
    import sqlite3
    from pathlib import Path as _Path
    db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
            """SELECT id FROM heartbeat_triggers
               WHERE heartbeat_id = ? AND created_at > ? AND consumed_at IS NULL
               AND coalesced_into IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (heartbeat_id, cutoff),
        ).fetchone()
        if row:
            return True, row["id"]
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
            """INSERT INTO heartbeat_triggers
               (id, heartbeat_id, trigger_type, payload, created_at, coalesced_into)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (trigger_id, heartbeat_id, trigger_type, json.dumps(payload or {}), now, coalesced_into),
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
            "UPDATE heartbeat_triggers SET consumed_at = ? WHERE id = ?",
            (_now_iso(), trigger_id),
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
        row = conn.execute("SELECT enabled FROM heartbeats WHERE id = ?", (heartbeat_id,)).fetchone()
        if not row or not row["enabled"]:
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
        rows = conn.execute("SELECT * FROM heartbeats").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _sync_heartbeats_to_db():
    """Mirror config/heartbeats.yaml into heartbeats table."""
    import sys
    import importlib

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
                "SELECT id FROM heartbeats WHERE id = ?", (hb.id,)
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO heartbeats
                       (id, agent, interval_seconds, max_turns, timeout_seconds,
                        lock_timeout_seconds, wake_triggers, enabled, goal_id,
                        required_secrets, decision_prompt, source_plugin,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        hb.id, hb.agent, hb.interval_seconds, hb.max_turns,
                        hb.timeout_seconds, hb.lock_timeout_seconds,
                        json.dumps(hb.wake_triggers), int(hb.enabled), hb.goal_id,
                        json.dumps(hb.required_secrets), hb.decision_prompt,
                        hb.source_plugin,
                        now, now,
                    ),
                )
            else:
                # Update mutable fields but preserve enabled state set via UI
                conn.execute(
                    """UPDATE heartbeats SET
                       agent=?, interval_seconds=?, max_turns=?, timeout_seconds=?,
                       lock_timeout_seconds=?, wake_triggers=?, goal_id=?,
                       required_secrets=?, decision_prompt=?, source_plugin=?,
                       updated_at=?
                       WHERE id=?""",
                    (
                        hb.agent, hb.interval_seconds, hb.max_turns, hb.timeout_seconds,
                        hb.lock_timeout_seconds, json.dumps(hb.wake_triggers), hb.goal_id,
                        json.dumps(hb.required_secrets), hb.decision_prompt,
                        hb.source_plugin, now,
                        hb.id,
                    ),
                )
        conn.commit()
        print(f"[dispatcher] synced {len(cfg.heartbeats)} heartbeats from YAML to DB", flush=True)
    finally:
        conn.close()


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
    """Start a background thread that runs the heartbeat schedule loop."""
    def _loop():
        import time
        register_interval_jobs()
        while True:
            schedule.run_pending()
            time.sleep(5)

    t = threading.Thread(target=_loop, name="heartbeat-dispatcher", daemon=True)
    t.start()
    print("[dispatcher] dispatcher thread started", flush=True)


# ── Config reload (called by plugin_loader after install/uninstall) ──────────

def reload_config() -> dict:
    """Re-sync heartbeats from config + plugins and re-register interval jobs.

    Called by plugin_loader.PluginInstaller after copying heartbeats to
    plugins/{slug}/heartbeats.yaml (install) or after removing it (uninstall).
    Safe to call while the dispatcher is running — uses _schedule_lock.

    Returns:
        Dict with keys: heartbeats_loaded (int), jobs_registered (int).
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info("[reload_config] Re-syncing heartbeats (core + plugins)")
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
