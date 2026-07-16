"""
tests/goals/test_step5_step6.py

goal-ticket-unification Steps 5+6 — goal-planner emitter/assignee-selection,
and the self-healing review loop's bounce/exhaust/reset logic.

Run:
    cd /path/to/workspace && pytest tests/goals/test_step5_step6.py -v
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

NOW = "2026-07-16T00:00:00.000000Z"


# ---------------------------------------------------------------------------
# Flask app fixture (mirrors tests/tickets/test_tickets.py's pattern)
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    import flask
    from flask_login import LoginManager
    import models as _models
    importlib.reload(_models)

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-step5-step6"
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
        project = _models.GoalProject(
            slug="p1", mission_id=mission.id, title="Project", created_at=NOW, updated_at=NOW
        )
        _models.db.session.add(project)
        _models.db.session.commit()

    import routes.goals as _goals_routes
    import routes.tickets as _tickets_routes
    importlib.reload(_goals_routes)
    importlib.reload(_tickets_routes)
    _app.register_blueprint(_goals_routes.bp)
    _app.register_blueprint(_tickets_routes.bp)

    return _app


@pytest.fixture
def client(app):
    # Log in via the session directly (flask_login's default session key) —
    # avoids routing through /api/auth/login's auth_security/LoginThrottle
    # machinery, which isn't reloaded alongside `models` per-test and holds
    # a stale SQLAlchemy binding from whichever test imported it first.
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        yield c


# ---------------------------------------------------------------------------
# Step 5 — goal_created emitter (only top-level human goals fire it)
# ---------------------------------------------------------------------------

def test_top_level_goal_dispatches_goal_created(client):
    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        mock_dispatch.return_value = (True, "run-id")
        resp = client.post("/api/goals", json={
            "slug": "g-top", "title": "Top-level Goal", "project_id": 1,
        })
        assert resp.status_code == 201, resp.get_json()
        goal_id = resp.get_json()["id"]

        mock_dispatch.assert_called_once_with("goal-planner", "goal_created", {"goal_id": goal_id})


def test_subgoal_does_not_dispatch_goal_created(client):
    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        mock_dispatch.return_value = (True, "run-id")

        parent = client.post("/api/goals", json={
            "slug": "g-parent", "title": "Parent Goal", "project_id": 1,
        })
        assert parent.status_code == 201
        parent_id = parent.get_json()["id"]
        mock_dispatch.reset_mock()  # only care about the sub-goal call below

        sub = client.post("/api/goals", json={
            "slug": "g-sub", "title": "Sub Goal", "project_id": 1,
            "parent_goal_id": parent_id, "due_date": "2026-08-01",
        })
        assert sub.status_code == 201, sub.get_json()
        assert sub.get_json()["parent_goal_id"] == parent_id

        mock_dispatch.assert_not_called()


def test_subgoal_without_due_date_rejected(client):
    with patch("heartbeat_dispatcher.dispatch"):
        parent = client.post("/api/goals", json={
            "slug": "g-parent2", "title": "Parent Goal 2", "project_id": 1,
        })
        parent_id = parent.get_json()["id"]

        resp = client.post("/api/goals", json={
            "slug": "g-sub-nodate", "title": "Sub Goal no date", "project_id": 1,
            "parent_goal_id": parent_id,
        })
        assert resp.status_code == 400
        assert "due_date" in resp.get_json()["error"]


def test_dispatch_failure_does_not_block_goal_creation(client):
    """Best-effort: if heartbeat_dispatcher blows up, goal creation still succeeds (201)."""
    with patch("heartbeat_dispatcher.dispatch", side_effect=RuntimeError("boom")):
        resp = client.post("/api/goals", json={
            "slug": "g-dispatch-fail", "title": "Goal", "project_id": 1,
        })
        assert resp.status_code == 201, resp.get_json()


# ---------------------------------------------------------------------------
# Step 5 — closed-set assignee_agent validation at POST /api/tickets
# ---------------------------------------------------------------------------

def test_invalid_assignee_agent_routes_to_clawdia(client):
    resp = client.post("/api/tickets", json={
        "title": "Ticket with bogus assignee",
        "assignee_agent": "not-a-real-agent-slug",
    })
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["assignee_agent"] == "clawdia-assistant"
    assert "not-a-real-agent-slug" in (body["description"] or "")


def test_valid_assignee_agent_passes_through(client):
    resp = client.post("/api/tickets", json={
        "title": "Ticket with real assignee",
        "assignee_agent": "bolt-executor",
    })
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["assignee_agent"] == "bolt-executor"
    assert not (body["description"] or "")


# ---------------------------------------------------------------------------
# Step 6 — self-healing review loop: bounce / exhaust / reset
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    """Temp sqlite3 DB carrying the tickets/ticket_activity/ticket_comments schema."""
    db_file = tmp_path / "review_loop_test.db"
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_value REAL NOT NULL DEFAULT 1.0,
            current_value REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'active',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            goal_id INTEGER,
            blocked_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE ticket_comments (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            mentions TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE ticket_activity (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
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


def _make_ticket(conn, status="review") -> str:
    tid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO tickets (id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (tid, f"Ticket {tid[:8]}", status, NOW, NOW),
    )
    conn.commit()
    return tid


def test_parse_verdict_finds_json_block():
    text = 'blah blah\n```json\n{"verdict": "pass", "critique": "looks good"}\n```\nmore text'
    v = heartbeat_outcome.parse_verdict(text)
    assert v == {"verdict": "pass", "critique": "looks good"}


def test_parse_verdict_none_when_absent():
    assert heartbeat_outcome.parse_verdict('{"action": "work", "result": "did stuff"}') is None
    assert heartbeat_outcome.parse_verdict(None) is None


def test_three_fails_in_a_row_exhausts_to_blocked(conn):
    """3 consecutive fail verdicts (no reopen in between) -> 3rd lands on blocked/review_exhausted."""
    tid = _make_ticket(conn)

    r1 = heartbeat_outcome._apply_review_verdict(tid, "bolt-executor", {"verdict": "fail", "critique": "bug A"}, conn)
    assert r1 == {"verdict": "fail", "critique": "bug A", "exhausted": False, "bounce": 1}
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (tid,)).fetchone()[0]
    assert status == "in_progress"

    r2 = heartbeat_outcome._apply_review_verdict(tid, "bolt-executor", {"verdict": "fail", "critique": "bug B"}, conn)
    assert r2 == {"verdict": "fail", "critique": "bug B", "exhausted": False, "bounce": 2}

    r3 = heartbeat_outcome._apply_review_verdict(tid, "bolt-executor", {"verdict": "fail", "critique": "bug C"}, conn)
    assert r3["exhausted"] is True
    row = conn.execute("SELECT status, blocked_reason FROM tickets WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "blocked"
    assert row["blocked_reason"] == "review_exhausted"

    # Never loops back to review/in_progress on its own after exhaustion.
    bounces = conn.execute(
        "SELECT COUNT(*) FROM ticket_activity WHERE ticket_id = ? AND action = 'review_bounce'", (tid,)
    ).fetchone()[0]
    assert bounces == 2  # only the first two fails bounced; the 3rd exhausted instead


def test_pass_verdict_resolves(conn):
    tid = _make_ticket(conn)
    r = heartbeat_outcome._apply_review_verdict(tid, "bolt-executor", {"verdict": "pass", "critique": "ship it"}, conn)
    assert r["verdict"] == "pass"
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (tid,)).fetchone()[0]
    assert status == "resolved"


def test_manual_reopen_resets_bounce_budget(conn):
    """A ticket that exhausted, then got manually reopened (review_reset logged),
    starts a fresh bounce budget — the next 2 fails bounce again instead of
    immediately re-exhausting."""
    tid = _make_ticket(conn)

    heartbeat_outcome._apply_review_verdict(tid, "bolt-executor", {"verdict": "fail", "critique": "x"}, conn)
    heartbeat_outcome._apply_review_verdict(tid, "bolt-executor", {"verdict": "fail", "critique": "y"}, conn)
    exhausted = heartbeat_outcome._apply_review_verdict(tid, "bolt-executor", {"verdict": "fail", "critique": "z"}, conn)
    assert exhausted["exhausted"] is True

    # Simulate the manual-reopen path routes/tickets.py logs on a human
    # walking a blocked ticket back to open/in_progress. Use a real "now" (not
    # the fixed NOW fixture constant) so it sorts after the bounce rows above,
    # which were stamped with heartbeat_outcome's own _now_iso().
    conn.execute(
        "INSERT INTO ticket_activity (id, ticket_id, actor, action, payload, created_at) "
        "VALUES (?, ?, ?, 'review_reset', '{}', ?)",
        (str(uuid.uuid4()), tid, "human:felipe", heartbeat_outcome._now_iso()),
    )
    conn.commit()

    assert heartbeat_outcome._count_review_bounces(tid, conn) == 0

    r1 = heartbeat_outcome._apply_review_verdict(tid, "bolt-executor", {"verdict": "fail", "critique": "still broken"}, conn)
    assert r1 == {"verdict": "fail", "critique": "still broken", "exhausted": False, "bounce": 1}
