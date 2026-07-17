"""Panorama 2026-07-17, item 4 — in-process heartbeat handler that checks for
overdue Goals and Tickets and alerts via Telegram.

Called by heartbeat_runner when heartbeat config has
``handler: deadline_check.tick``. Zero Claude CLI invocations — pure SQL +
an HTTP POST to Telegram, same shape as plugin_integration_health.tick.

Before this, nothing proactively surfaced a Goal or Ticket past its
due_date — the human only found out by opening /goals or /kanban. Weekly
Review (scheduler.py) covers this once a week; this closes the gap between
runs with a cheap, LLM-free check every few hours.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"

_MAX_ITEMS_PER_ALERT = 8


def _overdue_goals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, title, due_date FROM goals "
        "WHERE status = 'active' AND due_date IS NOT NULL AND due_date < date('now') "
        "ORDER BY due_date ASC"
    ).fetchall()


def _overdue_tickets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, title, due_date FROM tickets "
        "WHERE status IN ('open', 'in_progress', 'blocked') "
        "AND due_date IS NOT NULL AND due_date < date('now') "
        "ORDER BY due_date ASC"
    ).fetchall()


def _build_alert(goals: list[sqlite3.Row], tickets: list[sqlite3.Row]) -> str:
    lines = [f"⏰ <b>{len(goals)} Meta(s) e {len(tickets)} Ticket(s) vencidos</b>"]
    if goals:
        lines.append("\n🎯 Metas:")
        for g in goals[:_MAX_ITEMS_PER_ALERT]:
            lines.append(f"  • #{g['id']} {g['title']} — venceu {g['due_date']}")
        if len(goals) > _MAX_ITEMS_PER_ALERT:
            lines.append(f"  … e mais {len(goals) - _MAX_ITEMS_PER_ALERT}")
    if tickets:
        lines.append("\n🎫 Tickets:")
        for t in tickets[:_MAX_ITEMS_PER_ALERT]:
            lines.append(f"  • {t['title']} — venceu {t['due_date']}")
        if len(tickets) > _MAX_ITEMS_PER_ALERT:
            lines.append(f"  … e mais {len(tickets) - _MAX_ITEMS_PER_ALERT}")
    return "\n".join(lines)


def tick() -> dict:
    """Main handler — checks for overdue Goals/Tickets, alerts if any exist.

    Returns a summary dict for logging purposes. Deliberately re-alerts every
    run while something stays overdue (no dedup table) — an in-process check
    every few hours is cheap, and a repeated nudge is the point, not a bug.
    """
    if not DB_PATH.exists():
        return {"error": "db not found", "overdue_goals": 0, "overdue_tickets": 0, "alerted": False}

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        goals = _overdue_goals(conn)
        tickets = _overdue_tickets(conn)
    finally:
        conn.close()

    alerted = False
    if goals or tickets:
        from notifications import send_telegram_alert
        alerted = send_telegram_alert(_build_alert(goals, tickets))

    log.info(
        "deadline_check.tick: overdue_goals=%d overdue_tickets=%d alerted=%s",
        len(goals), len(tickets), alerted,
    )
    return {"overdue_goals": len(goals), "overdue_tickets": len(tickets), "alerted": alerted}
