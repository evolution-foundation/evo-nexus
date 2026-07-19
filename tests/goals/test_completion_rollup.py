"""
tests/goals/test_completion_rollup.py

ultraplan spec (2026-07-17) — completed_at/started_at columns on
missions/projects/goals/goal_tasks, plus the automatic Project->Goal
completion rollup, verified on both data-access paths:

  - ORM path: routes/goals.py::patch_goal / patch_project / patch_goal_task
    and the _maybe_complete_project() helper.
  - Raw-SQL path: heartbeat_outcome.py::_recompute_goal_from_tickets and
    _maybe_complete_project_raw() (the real-world path, via ticket
    resolution).

Run:
    cd /path/to/workspace && pytest tests/goals/test_completion_rollup.py -v
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import heartbeat_outcome  # noqa: E402

NOW = "2026-07-17T00:00:00.000000Z"


# ---------------------------------------------------------------------------
# Raw-sqlite fixture (mirrors tests/goals/test_goal_ticket_rollup.py's conn
# fixture, extended with the completed_at/started_at columns and the
# projects.status CHECK this migration adds).
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    db_file = tmp_path / "rollup_test.db"
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','completed','on-hold','cancelled')),
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
            started_at TEXT,
            completed_at TEXT,
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


def _make_project(conn, slug="proj-rollup") -> int:
    conn.execute(
        "INSERT INTO projects (slug, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (slug, "Rollup Test Project", NOW, NOW),
    )
    conn.commit()
    return conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()[0]


def _make_goal(conn, project_id: int, slug: str, target_value=1.0) -> int:
    conn.execute(
        "INSERT INTO goals (slug, project_id, title, target_value, current_value, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 0.0, 'active', ?, ?)",
        (slug, project_id, f"Goal {slug}", target_value, NOW, NOW),
    )
    conn.commit()
    return conn.execute("SELECT id FROM goals WHERE slug = ?", (slug,)).fetchone()[0]


def _make_ticket(conn, goal_id: int) -> str:
    tid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO tickets (id, title, status, goal_id, created_at, updated_at) "
        "VALUES (?, ?, 'open', ?, ?, ?)",
        (tid, f"Ticket {tid[:8]}", goal_id, NOW, NOW),
    )
    conn.commit()
    return tid


def _project_row(conn, project_id: int):
    return conn.execute(
        "SELECT status, completed_at FROM projects WHERE id = ?", (project_id,)
    ).fetchone()


def _goal_row(conn, goal_id: int):
    return conn.execute(
        "SELECT status, completed_at FROM goals WHERE id = ?", (goal_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Raw-SQL path — _recompute_goal_from_tickets / _maybe_complete_project_raw
# ---------------------------------------------------------------------------

def test_goal_achieved_via_tickets_sets_completed_at(conn):
    project_id = _make_project(conn, "proj-a")
    goal_id = _make_goal(conn, project_id, "goal-a", target_value=1.0)
    ticket = _make_ticket(conn, goal_id)

    heartbeat_outcome._move_ticket(ticket, "resolved", "bolt-executor", "done", conn)

    row = _goal_row(conn, goal_id)
    assert row["status"] == "achieved"
    assert row["completed_at"] is not None


def test_goal_reopened_clears_completed_at(conn):
    project_id = _make_project(conn, "proj-b")
    goal_id = _make_goal(conn, project_id, "goal-b", target_value=1.0)
    ticket = _make_ticket(conn, goal_id)

    heartbeat_outcome._move_ticket(ticket, "resolved", "bolt-executor", "done", conn)
    assert _goal_row(conn, goal_id)["completed_at"] is not None

    heartbeat_outcome._move_ticket(ticket, "in_progress", "bolt-executor", "reopened", conn)
    row = _goal_row(conn, goal_id)
    assert row["status"] == "active"
    assert row["completed_at"] is None


def test_single_goal_project_completes_when_goal_achieved(conn):
    project_id = _make_project(conn, "proj-single")
    goal_id = _make_goal(conn, project_id, "goal-single", target_value=1.0)
    ticket = _make_ticket(conn, goal_id)

    heartbeat_outcome._move_ticket(ticket, "resolved", "bolt-executor", "done", conn)

    proj = _project_row(conn, project_id)
    assert proj["status"] == "completed"
    assert proj["completed_at"] is not None


def test_project_waits_for_all_goals_to_go_terminal(conn):
    project_id = _make_project(conn, "proj-multi")
    goal_1 = _make_goal(conn, project_id, "goal-multi-1", target_value=1.0)
    goal_2 = _make_goal(conn, project_id, "goal-multi-2", target_value=1.0)
    ticket_1 = _make_ticket(conn, goal_1)

    heartbeat_outcome._move_ticket(ticket_1, "resolved", "bolt-executor", "done", conn)
    # goal_2 still active -> project must NOT complete yet
    assert _project_row(conn, project_id)["status"] == "active"

    ticket_2 = _make_ticket(conn, goal_2)
    heartbeat_outcome._move_ticket(ticket_2, "resolved", "bolt-executor", "done", conn)

    proj = _project_row(conn, project_id)
    assert proj["status"] == "completed"
    assert proj["completed_at"] is not None


def test_cancelled_goal_counts_as_terminal_for_rollup(conn):
    project_id = _make_project(conn, "proj-cancel")
    goal_1 = _make_goal(conn, project_id, "goal-cancel-1", target_value=1.0)
    goal_2 = _make_goal(conn, project_id, "goal-cancel-2", target_value=1.0)
    conn.execute("UPDATE goals SET status = 'cancelled' WHERE id = ?", (goal_2,))
    conn.commit()

    ticket_1 = _make_ticket(conn, goal_1)
    heartbeat_outcome._move_ticket(ticket_1, "resolved", "bolt-executor", "done", conn)

    proj = _project_row(conn, project_id)
    assert proj["status"] == "completed"


def test_already_completed_project_is_not_touched_again(conn):
    """A second call must not clobber completed_at with a fresh timestamp."""
    project_id = _make_project(conn, "proj-idempotent")
    goal_id = _make_goal(conn, project_id, "goal-idempotent", target_value=1.0)
    ticket = _make_ticket(conn, goal_id)
    heartbeat_outcome._move_ticket(ticket, "resolved", "bolt-executor", "done", conn)

    first_completed_at = _project_row(conn, project_id)["completed_at"]
    heartbeat_outcome._maybe_complete_project_raw(project_id, conn)
    conn.commit()

    assert _project_row(conn, project_id)["completed_at"] == first_completed_at


def test_project_rollup_never_raises_for_missing_project_or_goals(conn):
    heartbeat_outcome._maybe_complete_project_raw(999999, conn)  # no such project
    project_id = _make_project(conn, "proj-empty")
    heartbeat_outcome._maybe_complete_project_raw(project_id, conn)  # no goals under it
    assert _project_row(conn, project_id)["status"] == "active"


# ---------------------------------------------------------------------------
# ORM path — routes/goals.py (patch_project / patch_goal / patch_goal_task)
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    import flask
    from flask_login import LoginManager
    import models as _models
    importlib.reload(_models)

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-completion-rollup"
    _app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    _models.db.init_app(_app)

    _login_manager = LoginManager()
    _login_manager.init_app(_app)

    @_login_manager.user_loader
    def _load_user(user_id):
        return _models.User.query.get(int(user_id))

    @_login_manager.unauthorized_handler
    def _unauthorized():
        from flask import jsonify
        return jsonify({"error": "Authentication required"}), 401

    with _app.app_context():
        _models.db.create_all()
        admin = _models.User(username="admin", role="admin")
        admin.set_password("password")
        _models.db.session.add(admin)

        mission = _models.Mission(slug="m1", title="Mission", created_at=NOW, updated_at=NOW)
        _models.db.session.add(mission)
        _models.db.session.commit()

    import routes.goals as _goals_routes
    importlib.reload(_goals_routes)
    _app.register_blueprint(_goals_routes.bp)

    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        yield c


@pytest.fixture(autouse=True)
def _no_dispatch_no_mempalace(monkeypatch):
    # Every create_project()/create_goal() dispatches a heartbeat trigger and
    # syncs MemPalace as side effects unrelated to this file's scope — mock
    # both so tests only exercise the completed_at/started_at/rollup logic.
    monkeypatch.setattr("heartbeat_dispatcher.dispatch", lambda *a, **k: None, raising=False)


def _create_project(client, slug="p1", mission_id=1):
    with patch("heartbeat_dispatcher.dispatch"), patch("routes.goals._sync_mempalace_for_project"):
        resp = client.post("/api/projects", json={
            "slug": slug, "title": f"Project {slug}", "mission_id": mission_id,
        })
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["id"]


def _create_goal(client, project_id, slug, target_value=1.0):
    with patch("heartbeat_dispatcher.dispatch"):
        resp = client.post("/api/goals", json={
            "slug": slug, "title": f"Goal {slug}", "project_id": project_id,
            "target_value": target_value,
        })
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["id"]


def test_patch_project_sets_completed_at_on_terminal_status(client):
    project_id = _create_project(client, "orm-p1")
    with patch("routes.goals._sync_mempalace_for_project"):
        resp = client.patch(f"/api/projects/{project_id}", json={"status": "completed"})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None


def test_patch_project_clears_completed_at_on_reopen(client):
    project_id = _create_project(client, "orm-p2")
    with patch("routes.goals._sync_mempalace_for_project"):
        client.patch(f"/api/projects/{project_id}", json={"status": "completed"})
        resp = client.patch(f"/api/projects/{project_id}", json={"status": "active"})
    assert resp.get_json()["completed_at"] is None


def test_patch_goal_terminal_transition_triggers_project_rollup(client):
    project_id = _create_project(client, "orm-p3")
    goal_id = _create_goal(client, project_id, "orm-g3")

    resp = client.patch(f"/api/goals/{goal_id}", json={"status": "achieved"})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["completed_at"] is not None

    with patch("routes.goals._sync_mempalace_for_project"):
        project = client.get(f"/api/projects/{project_id}").get_json()
    assert project["status"] == "completed"
    assert project["completed_at"] is not None


def test_patch_goal_rollup_waits_for_sibling_goals(client):
    project_id = _create_project(client, "orm-p4")
    goal_1 = _create_goal(client, project_id, "orm-g4-1")
    goal_2 = _create_goal(client, project_id, "orm-g4-2")

    client.patch(f"/api/goals/{goal_1}", json={"status": "achieved"})
    with patch("routes.goals._sync_mempalace_for_project"):
        project = client.get(f"/api/projects/{project_id}").get_json()
    assert project["status"] == "active"

    client.patch(f"/api/goals/{goal_2}", json={"status": "cancelled"})
    with patch("routes.goals._sync_mempalace_for_project"):
        project = client.get(f"/api/projects/{project_id}").get_json()
    assert project["status"] == "completed"


def test_patch_goal_reopen_clears_completed_at(client):
    project_id = _create_project(client, "orm-p5")
    goal_id = _create_goal(client, project_id, "orm-g5")

    client.patch(f"/api/goals/{goal_id}", json={"status": "achieved"})
    resp = client.patch(f"/api/goals/{goal_id}", json={"status": "active"})
    assert resp.get_json()["completed_at"] is None


def test_patch_goal_task_sets_started_at_once_and_completed_at(client):
    import models as _models
    with client.application.app_context():
        t = _models.GoalTask(title="Task X", status="open", created_at=NOW, updated_at=NOW)
        _models.db.session.add(t)
        _models.db.session.commit()
        task_id = t.id

    resp = client.patch(f"/api/goal-tasks/{task_id}", json={"status": "in_progress"})
    body = resp.get_json()
    assert body["started_at"] is not None
    first_started_at = body["started_at"]

    resp = client.patch(f"/api/goal-tasks/{task_id}", json={"status": "done"})
    body = resp.get_json()
    assert body["completed_at"] is not None

    # Reopen then re-complete: started_at must not move, completed_at clears then resets.
    client.patch(f"/api/goal-tasks/{task_id}", json={"status": "open"})
    resp = client.patch(f"/api/goal-tasks/{task_id}", json={"status": "in_progress"})
    body = resp.get_json()
    assert body["started_at"] == first_started_at

    resp = client.patch(f"/api/goal-tasks/{task_id}", json={"status": "done"})
    assert resp.get_json()["completed_at"] is not None


def test_patch_goal_task_completed_at_clears_on_reopen(client):
    import models as _models
    with client.application.app_context():
        t = _models.GoalTask(title="Task Y", status="done", created_at=NOW, updated_at=NOW,
                              completed_at=NOW)
        _models.db.session.add(t)
        _models.db.session.commit()
        task_id = t.id

    resp = client.patch(f"/api/goal-tasks/{task_id}", json={"status": "open"})
    assert resp.get_json()["completed_at"] is None
