"""
tests/goals/test_goal_ticket_rollup.py

goal-ticket-unification (Steps 2+3) — single source of truth for
goals.current_value, verified against a raw sqlite3 DB carrying the migrated
schema (tickets.goal_id/task_id/status, goals.target_value/current_value).

Proves:
  - A goal with target_value=2, resolved via two tickets (one goal_id-only,
    one goal_id+task_id — the mixed population that used to be triple/
    quadruple-counted), ends at current_value == 2 exactly.
  - Reopening + re-resolving a ticket is idempotent (stays at 2, never 3).
  - trg_task_done_updates_goal does not exist after the migration runs, and
    is not recreated by a second "boot" of the migration.

Run:
    cd /path/to/workspace && pytest tests/goals/test_goal_ticket_rollup.py -v
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import heartbeat_outcome  # noqa: E402


NOW = "2026-07-16T00:00:00.000000Z"


@pytest.fixture
def conn(tmp_path):
    """Temp sqlite3 DB carrying the post-migration schema (goals + tickets)."""
    db_file = tmp_path / "rollup_test.db"
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            metric_type TEXT NOT NULL DEFAULT 'count',
            target_value REAL NOT NULL DEFAULT 1.0,
            current_value REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'active',
            completed_at TEXT,
            parent_goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
            decomposition_state TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE goal_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
            task_id INTEGER REFERENCES goal_tasks(id) ON DELETE SET NULL,
            due_date TEXT,
            requires_human_approval INTEGER NOT NULL DEFAULT 0,
            blocked_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE ticket_comments (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            mentions TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE ticket_activity (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            payload TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    c.commit()
    yield c
    c.close()


def _make_goal(conn, target_value=2.0) -> int:
    conn.execute(
        "INSERT INTO projects (slug, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("proj-rollup", "Rollup Test Project", NOW, NOW),
    )
    project_id = conn.execute("SELECT id FROM projects WHERE slug = 'proj-rollup'").fetchone()[0]
    conn.execute(
        "INSERT INTO goals (slug, project_id, title, target_value, current_value, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 0.0, 'active', ?, ?)",
        ("goal-rollup", project_id, "Rollup Test Goal", target_value, NOW, NOW),
    )
    conn.commit()
    return conn.execute("SELECT id FROM goals WHERE slug = 'goal-rollup'").fetchone()[0]


def _make_ticket(conn, goal_id: int, task_id: int | None = None) -> str:
    tid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO tickets (id, title, status, goal_id, task_id, created_at, updated_at) "
        "VALUES (?, ?, 'open', ?, ?, ?, ?)",
        (tid, f"Ticket {tid[:8]}", goal_id, task_id, NOW, NOW),
    )
    conn.commit()
    return tid


def _current_value(conn, goal_id: int) -> float:
    return conn.execute("SELECT current_value FROM goals WHERE id = ?", (goal_id,)).fetchone()[0]


def _goal_status(conn, goal_id: int) -> str:
    return conn.execute("SELECT status FROM goals WHERE id = ?", (goal_id,)).fetchone()[0]


# ---------------------------------------------------------------------------
# AC1 — single source of truth, mixed population, idempotent
# ---------------------------------------------------------------------------

def test_two_tickets_resolve_to_exactly_target(conn):
    """goal_id-only ticket + goal_id+task_id ticket, both resolved -> current_value == 2 exactly."""
    goal_id = _make_goal(conn, target_value=2.0)

    conn.execute(
        "INSERT INTO goal_tasks (goal_id, title, status, created_at, updated_at) "
        "VALUES (?, 'legacy task', 'open', ?, ?)",
        (goal_id, NOW, NOW),
    )
    conn.commit()
    task_id = conn.execute("SELECT id FROM goal_tasks WHERE goal_id = ?", (goal_id,)).fetchone()[0]

    ticket_a = _make_ticket(conn, goal_id)  # goal_id only
    ticket_b = _make_ticket(conn, goal_id, task_id=task_id)  # goal_id + task_id (mixed population)

    assert _current_value(conn, goal_id) == 0.0

    heartbeat_outcome._move_ticket(ticket_a, "resolved", "bolt-executor", "done", conn)
    assert _current_value(conn, goal_id) == 1.0

    heartbeat_outcome._move_ticket(ticket_b, "resolved", "bolt-executor", "done", conn)
    assert _current_value(conn, goal_id) == 2.0  # exactly 2, not 3, not 4
    assert _goal_status(conn, goal_id) == "achieved"


def test_reopen_and_reresolve_is_idempotent(conn):
    """Reopening a resolved ticket and resolving it again must not double-count."""
    goal_id = _make_goal(conn, target_value=2.0)
    ticket_a = _make_ticket(conn, goal_id)
    ticket_b = _make_ticket(conn, goal_id)

    heartbeat_outcome._move_ticket(ticket_a, "resolved", "bolt-executor", "done", conn)
    heartbeat_outcome._move_ticket(ticket_b, "resolved", "bolt-executor", "done", conn)
    assert _current_value(conn, goal_id) == 2.0

    # Reopen one of the two resolved tickets — count must drop back to 1.
    heartbeat_outcome._move_ticket(ticket_a, "in_progress", "bolt-executor", "reopened", conn)
    assert _current_value(conn, goal_id) == 1.0
    assert _goal_status(conn, goal_id) == "active"  # dropped back below target

    # Resolve it again — must land back on 2, not 3.
    heartbeat_outcome._move_ticket(ticket_a, "resolved", "bolt-executor", "done again", conn)
    assert _current_value(conn, goal_id) == 2.0

    # Run it a third time for good measure (pure idempotency, no transition).
    heartbeat_outcome._recompute_goal_from_tickets(goal_id, conn)
    assert _current_value(conn, goal_id) == 2.0


def test_recompute_ignores_tickets_without_goal(conn):
    heartbeat_outcome._recompute_goal_from_tickets(None, conn)  # must not raise
    heartbeat_outcome._recompute_goal_from_tickets(999999, conn)  # non-existent goal, must not raise


# ---------------------------------------------------------------------------
# 1e — trigger must not survive a migration re-run ("reboot")
# ---------------------------------------------------------------------------

def test_trigger_absent_and_not_recreated_on_reboot(conn):
    # Simulate a DB that still had the legacy trigger before this migration ran.
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_task_done_updates_goal
        AFTER UPDATE OF status ON goal_tasks
        WHEN NEW.goal_id IS NOT NULL AND NEW.status = 'done' AND OLD.status != 'done'
        BEGIN
          UPDATE goals SET current_value = current_value + 1, updated_at = datetime('now') WHERE id = NEW.goal_id;
        END;
        """
    )
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_task_done_updates_goal'"
    ).fetchone() is not None

    # Apply the migration's DROP (mirrors app.py's `_cur.execute("DROP TRIGGER IF EXISTS ...")`).
    conn.execute("DROP TRIGGER IF EXISTS trg_task_done_updates_goal")
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_task_done_updates_goal'"
    ).fetchone() is None

    # Simulate a second app boot: the "Always ensure view exists" executescript
    # in app.py no longer contains a CREATE TRIGGER statement (only CREATE VIEW
    # goal_progress_v) — reproduce that exact statement here and confirm it
    # does NOT bring the trigger back.
    conn.executescript(
        """
        CREATE VIEW IF NOT EXISTS goal_progress_v AS
        SELECT g.id as goal_id, g.target_value,
               COUNT(t.id) as total_tasks,
               COUNT(CASE WHEN t.status='done' THEN 1 END) as done_tasks
        FROM goals g LEFT JOIN goal_tasks t ON t.goal_id = g.id
        GROUP BY g.id;
        """
    )
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_task_done_updates_goal'"
    ).fetchone() is None


def test_app_py_boot_executescript_has_no_create_trigger():
    """Static check: app.py's idempotent boot block must not contain the trigger DDL."""
    app_py = (BACKEND_DIR / "app.py").read_text()
    marker = "Always ensure view exists"
    assert marker in app_py, "expected the updated boot-block comment to be present"
    start = app_py.index(marker)
    end = app_py.index("_conn.commit()", start)
    boot_block = app_py[start:end]
    assert "CREATE TRIGGER" not in boot_block
