"""Ticket janitor — auto-releases timed-out ticket locks every 5 minutes (Feature 1.3)."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

JANITOR_INTERVAL_SECONDS = int(os.getenv("TICKET_JANITOR_INTERVAL", "300"))  # 5 min default

# Approval lifecycle policy (goal-ticket-unification ADR §3i) — proposed
# numbers, Felipe-confirmed as assumption: 8h auto-reject covers an overnight
# unattended run, two re-nudges at 2h/4h so an unanswered approval doesn't
# silently sit until the deadline.
APPROVAL_RENUDGE_WINDOWS_SECONDS = (2 * 3600, 4 * 3600)

_janitor_started = False
_janitor_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(ts: str) -> datetime:
    return datetime.strptime(ts.rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def release_expired_locks(app=None) -> int:
    """Find and release all expired ticket locks.

    Returns the number of tickets released.
    Must run inside a Flask app context (pass app for context push, or call
    from an already-active context).
    """
    released = 0
    try:
        from models import db, Ticket, TicketActivity

        # Find all tickets whose lock has expired
        expired = db.session.execute(
            db.text("""
                SELECT id, locked_by, COALESCE(lock_timeout_seconds, 1800) as timeout_secs
                FROM tickets
                WHERE locked_at IS NOT NULL
                  AND datetime(locked_at, '+' || COALESCE(lock_timeout_seconds, 1800) || ' seconds')
                      < datetime('now')
            """)
        ).fetchall()

        now = _now()
        for row in expired:
            ticket_id = row[0]
            locked_by = row[1]

            # Update via raw SQL to avoid SQLAlchemy CHECK constraint issues
            db.session.execute(
                db.text(
                    "UPDATE tickets SET locked_at = NULL, locked_by = NULL, updated_at = :now "
                    "WHERE id = :id AND locked_at IS NOT NULL"
                ),
                {"id": ticket_id, "now": now},
            )

            activity = TicketActivity(
                id=str(uuid.uuid4()),
                ticket_id=ticket_id,
                actor="system:janitor",
                action="auto_release",
                payload=json.dumps({"previously_locked_by": locked_by}),
                created_at=now,
            )
            db.session.add(activity)
            released += 1

        if released > 0:
            db.session.commit()
            print(f"[ticket_janitor] auto-released {released} expired lock(s)", flush=True)

    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[ticket_janitor] ERROR in release_expired_locks: {exc}", flush=True)

    return released


def sweep_pending_approvals(app=None) -> int:
    """TTL / re-nudge / auto-reject sweep for pending_approvals (ADR §3i).

    Runs on the same 5-min cadence as the ticket-lock sweep so a pending
    Telegram approval never blocks the pipeline indefinitely: two re-nudges
    (2h, 4h) via a fresh send_approval_request, then auto-reject at 8h. On
    auto-reject, publish parks unwind the ticket back to in_progress (nothing
    was ever published — the approval never fired); decomposition parks mark
    the sub-goal rejected (ticket creation for it is Step 7's job, so there's
    nothing else to unwind here).

    `payload` is expected to be a JSON object with "title"/"body" strings
    (the same content the Step 7 caller passed to send_approval_request when
    the row was created) — re-nudges resend that content verbatim so the
    approver sees the same prompt again.
    """
    swept = 0
    try:
        from models import db
        from notifications import send_approval_request

        now_dt = datetime.now(timezone.utc)
        now = _now()

        rows = db.session.execute(
            db.text(
                "SELECT id, gate_type, ticket_id, goal_id, payload, created_at, nudged_at, expires_at "
                "FROM pending_approvals WHERE status = 'pending'"
            )
        ).fetchall()

        for row in rows:
            try:
                expired = row.expires_at and _parse_iso(row.expires_at) <= now_dt
            except (ValueError, TypeError):
                expired = False

            if expired:
                cur = db.session.execute(
                    db.text("UPDATE pending_approvals SET status='expired', decided_at=:t WHERE id=:id AND status='pending'"),
                    {"t": now, "id": row.id},
                )
                if cur.rowcount == 0:
                    continue  # decided by a human in the race window — leave it alone
                if row.gate_type == "publish" and row.ticket_id:
                    db.session.execute(
                        db.text(
                            "UPDATE tickets SET status='in_progress', requires_human_approval=0, "
                            "blocked_reason=NULL, updated_at=:t WHERE id=:tid"
                        ),
                        {"t": now, "tid": row.ticket_id},
                    )
                    db.session.execute(
                        db.text(
                            "INSERT INTO ticket_comments (id, ticket_id, author, body, mentions, created_at) "
                            "VALUES (:id, :tid, 'system:approval', :body, '[]', :t)"
                        ),
                        {
                            "id": str(uuid.uuid4()), "tid": row.ticket_id,
                            "body": "Aprovação expirou (8h sem resposta) — nada foi publicado.",
                            "t": now,
                        },
                    )
                elif row.gate_type == "decomposition" and row.goal_id:
                    db.session.execute(
                        db.text("UPDATE goals SET decomposition_state='rejected', updated_at=:t WHERE id=:id"),
                        {"t": now, "id": row.goal_id},
                    )
                db.session.commit()
                swept += 1
                continue

            # Re-nudge windows — send again once elapsed crosses a window
            # that the last nudge (if any) hadn't crossed yet.
            try:
                elapsed = (now_dt - _parse_iso(row.created_at)).total_seconds()
            except (ValueError, TypeError):
                continue
            last_nudge_elapsed = None
            if row.nudged_at:
                try:
                    last_nudge_elapsed = (_parse_iso(row.nudged_at) - _parse_iso(row.created_at)).total_seconds()
                except (ValueError, TypeError):
                    last_nudge_elapsed = None

            for window in APPROVAL_RENUDGE_WINDOWS_SECONDS:
                if elapsed >= window and (last_nudge_elapsed is None or last_nudge_elapsed < window):
                    title, body = "Aprovação pendente", f"Ticket {row.ticket_id}" if row.ticket_id else f"Goal {row.goal_id}"
                    try:
                        payload = json.loads(row.payload) if row.payload else {}
                        title = payload.get("title") or title
                        body = payload.get("body") or body
                    except (ValueError, TypeError):
                        pass
                    message_id = send_approval_request(row.id, title, body)
                    update = {"t": now, "id": row.id}
                    if message_id is not None:
                        db.session.execute(
                            db.text("UPDATE pending_approvals SET nudged_at=:t, telegram_message_id=:mid WHERE id=:id"),
                            {**update, "mid": message_id},
                        )
                    else:
                        db.session.execute(
                            db.text("UPDATE pending_approvals SET nudged_at=:t WHERE id=:id"), update,
                        )
                    db.session.commit()
                    swept += 1
                    break

    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[ticket_janitor] ERROR in sweep_pending_approvals: {exc}", flush=True)

    return swept


def _janitor_loop(app):
    """Background loop — reclaims expired ticket AND brain-repo locks.

    Both share the same 5-min cadence because both reclaim "busy" flags that
    should never stay set after a crash or OOM-kill. Keeping them in one
    thread avoids a second daemon with identical semantics.
    """
    while True:
        time.sleep(JANITOR_INTERVAL_SECONDS)
        try:
            with app.app_context():
                release_expired_locks()
                sweep_pending_approvals()
        except Exception as exc:
            print(f"[ticket_janitor] loop error: {exc}", flush=True)
        # Brain-repo stale-lock sweep. Runs in the same context; failures are
        # isolated so a broken brain_repo import doesn't stop ticket cleanup.
        try:
            from brain_repo.job_runner import reclaim_stale_locks
            reclaim_stale_locks(app)
        except ImportError:
            pass  # brain_repo not installed / disabled
        except Exception as exc:
            print(f"[ticket_janitor] brain-repo sweep error: {exc}", flush=True)


def start_janitor_thread():
    """Start the janitor background thread (idempotent — safe to call multiple times)."""
    global _janitor_started

    with _janitor_lock:
        if _janitor_started:
            return
        _janitor_started = True

    # Import here to avoid circular import at module load
    from flask import current_app
    app = current_app._get_current_object()  # type: ignore[attr-defined]

    t = threading.Thread(
        target=_janitor_loop,
        args=(app,),
        daemon=True,
        name="ticket-janitor",
    )
    t.start()
    print(f"[ticket_janitor] started (interval={JANITOR_INTERVAL_SECONDS}s)", flush=True)
