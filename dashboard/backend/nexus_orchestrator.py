"""Nexus orchestrator — "cada agente na sua hora, só com task real".

Replaces the old model (18 autopilot heartbeats firing every 15 min, in
parallel, burning tokens to decide "skip"). This is a cheap Python handler
(no LLM) that runs on a heartbeat interval and:

  1. Looks at the real queue — open/in_progress tickets assigned to an agent,
     not locked, highest priority first, preferring those tied to active goals.
  2. Picks ONE and dispatches that agent's heartbeat in the background.
  3. Does nothing (zero cost) when the queue is empty.

So agents only spend tokens when there's actual work, and they run one at a
time instead of stampeding. The agent run itself moves the kanban and reports
the result/blocker (see heartbeat_outcome + heartbeat_runner).

Tunables (env):
  ORCHESTRATOR_DRY_RUN=1     → log what it would dispatch, don't run the agent
  ORCHESTRATOR_MAX_INFLIGHT  → max concurrent agent runs it will start (default 1)
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"

# Agents the orchestrator will dispatch. State monitors (atlas/flux) keep their
# own scheduled heartbeats and are not driven by the queue.
_EXCLUDE_AGENTS = {"system"}

# Track in-flight runs started by the orchestrator (process-local).
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _next_ticket(conn) -> sqlite3.Row | None:
    """Highest-priority actionable ticket assigned to an agent.

    Prefers tickets linked to an active goal, then priority_rank, then age.
    """
    return conn.execute(
        """
        SELECT t.id, t.title, t.assignee_agent, t.priority, t.goal_id
        FROM tickets t
        LEFT JOIN goals g ON g.id = t.goal_id
        WHERE t.status IN ('open', 'in_progress')
          AND t.locked_at IS NULL
          AND t.assignee_agent IS NOT NULL
          AND t.assignee_agent NOT IN ('system')
        ORDER BY
          CASE WHEN g.status = 'active' THEN 0 ELSE 1 END,
          COALESCE(t.priority_rank, 0) DESC,
          t.created_at ASC
        LIMIT 1
        """
    ).fetchone()


def _heartbeat_id_for_agent(conn, agent: str) -> str | None:
    """Find a heartbeat to run for this agent (prefer the autopilot one)."""
    row = conn.execute(
        "SELECT id FROM heartbeats WHERE agent = ? "
        "ORDER BY CASE WHEN id LIKE 'autopilot-%' THEN 0 ELSE 1 END LIMIT 1",
        (agent,),
    ).fetchone()
    return row["id"] if row else None


def _dispatch_agent(heartbeat_id: str) -> None:
    """Run the agent heartbeat in a background thread, releasing inflight after."""
    def _run():
        try:
            from heartbeat_runner import run_heartbeat
            run_heartbeat(heartbeat_id, triggered_by="orchestrator")
        except Exception as exc:  # noqa: BLE001
            print(f"[orchestrator] run failed for {heartbeat_id}: {exc}", flush=True)
        finally:
            with _inflight_lock:
                _inflight.discard(heartbeat_id)

    threading.Thread(target=_run, name=f"orch-{heartbeat_id}", daemon=True).start()


def tick() -> dict:
    """One orchestration cycle. Cheap; safe to call frequently."""
    max_inflight = int(os.environ.get("ORCHESTRATOR_MAX_INFLIGHT", "1"))
    dry_run = os.environ.get("ORCHESTRATOR_DRY_RUN", "0").lower() in ("1", "true", "yes")

    with _inflight_lock:
        inflight_now = len(_inflight)
    if inflight_now >= max_inflight:
        return {"dispatched": None, "reason": f"max_inflight reached ({inflight_now})"}

    conn = _get_db()
    try:
        ticket = _next_ticket(conn)
        if not ticket:
            return {"dispatched": None, "reason": "queue empty"}

        agent = ticket["assignee_agent"]
        hb_id = _heartbeat_id_for_agent(conn, agent)
        if not hb_id:
            return {"dispatched": None, "reason": f"no heartbeat for agent {agent}",
                    "ticket": ticket["id"]}

        with _inflight_lock:
            if hb_id in _inflight:
                return {"dispatched": None, "reason": f"{hb_id} already running"}
            if not dry_run:
                _inflight.add(hb_id)

        if dry_run:
            return {"dispatched": None, "dry_run": True, "would_dispatch": hb_id,
                    "agent": agent, "ticket": ticket["id"], "title": ticket["title"]}

        _dispatch_agent(hb_id)
        return {"dispatched": hb_id, "agent": agent, "ticket": ticket["id"],
                "title": ticket["title"]}
    finally:
        conn.close()
