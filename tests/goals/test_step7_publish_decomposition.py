"""
tests/goals/test_step7_publish_decomposition.py

goal-ticket-unification Step 7 — publish gate (heartbeat_outcome) and the
decomposition gate (routes/approvals.py), including the R1 reservation
(decomposition approval must never re-dispatch goal_created).

Run:
    cd /path/to/workspace && pytest tests/goals/test_step7_publish_decomposition.py -v
"""

from __future__ import annotations

import importlib
import json
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
# Flask app fixture (mirrors tests/goals/test_step5_step6.py's pattern, plus
# the approvals blueprint under test here)
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    import flask
    from flask_login import LoginManager
    import models as _models
    importlib.reload(_models)

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-step7"
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
    import routes.approvals as _approvals_routes
    importlib.reload(_goals_routes)
    importlib.reload(_tickets_routes)
    importlib.reload(_approvals_routes)
    _app.register_blueprint(_goals_routes.bp)
    _app.register_blueprint(_tickets_routes.bp)
    _app.register_blueprint(_approvals_routes.bp)

    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        yield c


@pytest.fixture(autouse=True)
def _approval_env(monkeypatch):
    monkeypatch.setenv("APPROVAL_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.setenv("APPROVAL_APPROVER_IDS", "12345")
    # Safety net: without this, any test path that reaches the real
    # notifications.send_approval_request (e.g. POST /api/approvals, which
    # none of the decomposition tests mock individually) would read the
    # developer's real TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID from .env and send
    # an actual Telegram message with test placeholder text. This bit for
    # real during Step 7 development — see workspace memory
    # goal-ticket-unification-build-state.md. send_approval_request already
    # no-ops (returns None) when these are unset, so clearing them here makes
    # every test in this module safe by construction, not by remembering to
    # mock each call site.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def _bridge_headers():
    return {"Authorization": "Bearer test-bridge-token"}


def _create_subgoal(client, slug_suffix: str) -> tuple[int, int]:
    """Create a parent + a proposed sub-goal, returning (parent_id, sub_id)."""
    with patch("heartbeat_dispatcher.dispatch"):
        parent = client.post("/api/goals", json={
            "slug": f"g-parent-{slug_suffix}", "title": f"Parent {slug_suffix}", "project_id": 1,
        })
        assert parent.status_code == 201, parent.get_json()
        parent_id = parent.get_json()["id"]

        sub = client.post("/api/goals", json={
            "slug": f"g-sub-{slug_suffix}", "title": f"Sub Goal {slug_suffix}", "project_id": 1,
            "parent_goal_id": parent_id, "due_date": "2026-08-01",
            "decomposition_state": "proposed",
        })
        assert sub.status_code == 201, sub.get_json()
        sub_id = sub.get_json()["id"]
    return parent_id, sub_id


# ---------------------------------------------------------------------------
# Decomposition gate — POST /api/approvals + decision
# ---------------------------------------------------------------------------

def test_decomposition_approve_creates_tickets_from_payload(client):
    _, sub_id = _create_subgoal(client, "d1")

    payload = {
        "title": "Aprovar decomposição: Sub Goal d1",
        "body": "resumo em pt-BR",
        "tickets": [
            {"title": "Ticket A", "priority": "high", "assignee_agent": "bolt-executor"},
            {"title": "Ticket B", "priority": "medium", "assignee_agent": "not-a-real-agent-slug"},
        ],
    }
    create_resp = client.post("/api/approvals", json={
        "gate_type": "decomposition", "goal_id": sub_id, "agent": "goal-planner", "payload": payload,
    })
    assert create_resp.status_code == 201, create_resp.get_json()
    approval_id = create_resp.get_json()["id"]
    assert create_resp.get_json()["idempotency_key"] == f"decomp:{sub_id}:0"

    decision_resp = client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "approve", "from_id": "12345"},
        headers=_bridge_headers(),
    )
    assert decision_resp.status_code == 200, decision_resp.get_json()

    tickets = client.get(f"/api/tickets?goal_id={sub_id}").get_json()["tickets"]
    assert len(tickets) == 2
    titles = {t["title"] for t in tickets}
    assert titles == {"Ticket A", "Ticket B"}
    for t in tickets:
        assert t["goal_id"] == sub_id
    b = next(t for t in tickets if t["title"] == "Ticket B")
    # invalid assignee_agent routes to the same clawdia triage bucket as
    # POST /api/tickets does (create_ticket's own closed-set validation).
    assert b["assignee_agent"] == "clawdia-assistant"

    goal = client.get(f"/api/goals/{sub_id}").get_json()
    assert goal["decomposition_state"] == "approved"


def test_decomposition_reject_creates_zero_tickets(client):
    _, sub_id = _create_subgoal(client, "d2")

    payload = {"title": "Aprovar decomposição: Sub Goal d2", "body": "resumo", "tickets": [{"title": "X"}]}
    create_resp = client.post("/api/approvals", json={
        "gate_type": "decomposition", "goal_id": sub_id, "agent": "goal-planner", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    decision_resp = client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "reject", "from_id": "12345", "reason": "não agora"},
        headers=_bridge_headers(),
    )
    assert decision_resp.status_code == 200, decision_resp.get_json()

    tickets = client.get(f"/api/tickets?goal_id={sub_id}").get_json()["tickets"]
    assert tickets == []

    goal = client.get(f"/api/goals/{sub_id}").get_json()
    assert goal["decomposition_state"] == "rejected"


def test_decomposition_approve_skips_malformed_ticket_entries(client):
    """A non-dict entry in payload["tickets"] (e.g. goal-planner double-encoding
    a string) must not lose the rest of the batch — skip it, create the valid
    ones."""
    _, sub_id = _create_subgoal(client, "d6")
    payload = {
        "title": "t", "body": "b",
        "tickets": ["not-a-dict", {"title": "Valid Ticket"}, 42, None],
    }
    create_resp = client.post("/api/approvals", json={
        "gate_type": "decomposition", "goal_id": sub_id, "agent": "goal-planner", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    decision_resp = client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "approve", "from_id": "12345"},
        headers=_bridge_headers(),
    )
    assert decision_resp.status_code == 200, decision_resp.get_json()

    tickets = client.get(f"/api/tickets?goal_id={sub_id}").get_json()["tickets"]
    assert len(tickets) == 1
    assert tickets[0]["title"] == "Valid Ticket"


def test_decomposition_approve_ticket_creation_failure_alerts_and_reraises(client):
    """The approval row and goals.decomposition_state are already committed to
    'approved' BEFORE the ticket-creation loop runs (CAS point of no return —
    a 2nd decision on this approval_id 409s). If the loop dies mid-way anyway
    (some exception the isinstance guard doesn't catch), silently losing the
    tickets behind an already-'approved' state would be real data loss: the
    human sees the Telegram approval succeed and never learns nothing was
    created. Must alert + re-raise, never swallow."""
    _, sub_id = _create_subgoal(client, "d7")
    payload = {
        "title": "t", "body": "b",
        "tickets": [{"title": "Ticket A", "assignee_agent": "bolt-executor"}],
    }
    create_resp = client.post("/api/approvals", json={
        "gate_type": "decomposition", "goal_id": sub_id, "agent": "goal-planner", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    with patch("routes.tickets._get_agent_slugs", side_effect=RuntimeError("boom")), \
         patch("notifications.send_telegram_alert") as mock_alert:
        with pytest.raises(RuntimeError):
            client.post(
                f"/api/approvals/{approval_id}/decision",
                json={"decision": "approve", "from_id": "12345"},
                headers=_bridge_headers(),
            )

    mock_alert.assert_called_once()
    assert "falhou" in mock_alert.call_args[0][0]
    assert str(approval_id) in mock_alert.call_args[0][0]

    # Zero tickets created — the alert exists precisely because this state
    # (approved but empty) can't self-heal via a retry.
    tickets = client.get(f"/api/tickets?goal_id={sub_id}").get_json()["tickets"]
    assert tickets == []


def test_decomposition_approve_never_dispatches_goal_created(client):
    """R1 (ADR Sign-off reservation): approving a decomposition creates tickets
    directly from the payload — it must NEVER re-dispatch goal_created, which
    would bypass create_goal's parent_goal_id-is-None recursion guard."""
    _, sub_id = _create_subgoal(client, "d3")

    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        payload = {"title": "t", "body": "b", "tickets": [{"title": "Ticket Z"}]}
        create_resp = client.post("/api/approvals", json={
            "gate_type": "decomposition", "goal_id": sub_id, "agent": "goal-planner", "payload": payload,
        })
        approval_id = create_resp.get_json()["id"]

        decision_resp = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approve", "from_id": "12345"},
            headers=_bridge_headers(),
        )
        assert decision_resp.status_code == 200, decision_resp.get_json()
        mock_dispatch.assert_not_called()


def test_decision_double_press_is_noop_409(client):
    _, sub_id = _create_subgoal(client, "d4")
    payload = {"title": "t", "body": "b", "tickets": []}
    create_resp = client.post("/api/approvals", json={
        "gate_type": "decomposition", "goal_id": sub_id, "agent": "goal-planner", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    first = client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "approve", "from_id": "12345"},
        headers=_bridge_headers(),
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "approve", "from_id": "12345"},
        headers=_bridge_headers(),
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Publish gate — heartbeat_outcome.apply_outcome (AC4)
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    """Temp sqlite3 DB carrying the tickets/pending_approvals schema this gate touches."""
    db_file = tmp_path / "publish_gate_test.db"
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_type TEXT NOT NULL DEFAULT 'count',
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
            requires_human_approval INTEGER NOT NULL DEFAULT 0,
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
        CREATE TABLE pending_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gate_type TEXT NOT NULL,
            ticket_id TEXT,
            goal_id INTEGER,
            agent TEXT,
            attempt INTEGER NOT NULL DEFAULT 0,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            payload TEXT,
            telegram_chat_id TEXT,
            telegram_message_id INTEGER,
            approver_from_id TEXT,
            reject_reason TEXT,
            decided_by TEXT,
            created_at TEXT NOT NULL,
            nudged_at TEXT,
            decided_at TEXT,
            expires_at TEXT NOT NULL
        );
        """
    )
    c.commit()
    yield c
    c.close()


def _make_ticket(conn, status="in_progress") -> str:
    tid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO tickets (id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (tid, f"Ticket {tid[:8]}", status, NOW, NOW),
    )
    conn.commit()
    return tid


def _work_result(ticket_id, publish_intent, publish_target, new_status="resolved"):
    return {
        "status": "success",
        "output": {
            "action": "work", "ticket_id": ticket_id, "result": "Post pronto para publicar.",
            "new_status": new_status, "publish_intent": publish_intent, "publish_target": publish_target,
        },
    }


def test_publish_gate_parks_ticket_pending_approval(conn):
    tid = _make_ticket(conn)
    with patch("notifications.send_approval_request", return_value=42) as mock_send:
        outcome = heartbeat_outcome.apply_outcome(
            "hb1", "pixel-social-media", _work_result(tid, True, "instagram"), conn
        )
    assert outcome["kind"] == "result"
    assert outcome["new_status"] == "blocked"
    mock_send.assert_called_once()

    row = conn.execute(
        "SELECT status, blocked_reason, requires_human_approval FROM tickets WHERE id = ?", (tid,)
    ).fetchone()
    assert row["status"] == "blocked"
    assert row["blocked_reason"] == "pending_human_approval"
    assert row["requires_human_approval"] == 1

    approval = conn.execute(
        "SELECT gate_type, status, telegram_message_id FROM pending_approvals WHERE ticket_id = ?", (tid,)
    ).fetchone()
    assert approval is not None
    assert approval["gate_type"] == "publish"
    assert approval["status"] == "pending"
    assert approval["telegram_message_id"] == 42


def test_publish_intent_false_bypasses_gate(conn):
    tid = _make_ticket(conn)
    outcome = heartbeat_outcome.apply_outcome(
        "hb1", "pixel-social-media", _work_result(tid, False, None), conn
    )
    assert outcome["new_status"] == "resolved"

    row = conn.execute("SELECT status FROM tickets WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "resolved"
    count = conn.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()[0]
    assert count == 0


def test_publish_target_invalid_blocks_without_approval(conn):
    tid = _make_ticket(conn)
    with patch("notifications.send_approval_request") as mock_send:
        outcome = heartbeat_outcome.apply_outcome(
            "hb1", "pixel-social-media", _work_result(tid, True, "myspace"), conn
        )
    mock_send.assert_not_called()
    assert outcome["kind"] == "blocked"

    row = conn.execute("SELECT status, blocked_reason FROM tickets WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "blocked"
    assert row["blocked_reason"] == "agent_blocked"
    count = conn.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()[0]
    assert count == 0


def test_non_publishing_agent_bypasses_gate(conn):
    tid = _make_ticket(conn)
    outcome = heartbeat_outcome.apply_outcome(
        "hb1", "bolt-executor", _work_result(tid, True, "instagram"), conn
    )
    assert outcome["new_status"] == "resolved"
    count = conn.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()[0]
    assert count == 0


def test_publish_intent_missing_still_gates_fail_closed(conn):
    """No publish_intent key at all (e.g. free-form parse without the field) —
    fail-closed still gates, same as an explicit True (Vault V5)."""
    tid = _make_ticket(conn)
    result = {
        "status": "success",
        "output": {
            "action": "work", "ticket_id": tid, "result": "Post pronto.",
            "new_status": "resolved", "publish_target": "linkedin",
        },
    }
    with patch("notifications.send_approval_request", return_value=None):
        outcome = heartbeat_outcome.apply_outcome("hb1", "mako-marketing", result, conn)
    assert outcome["new_status"] == "blocked"
    row = conn.execute("SELECT status FROM tickets WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "blocked"


def test_run_publish_action_requires_exact_content(conn):
    class _Row:
        payload = '{"outcome": {"publish_target": "instagram"}}'

    result = heartbeat_outcome._run_publish_action(_Row(), conn)
    assert result["published"] is False
    assert "publish_content" in result["detail"]


class _PostizResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def test_run_publish_action_confirms_postiz_published(conn):
    class _Row:
        payload = json.dumps({"outcome": {
            "publish_target": "linkedin",
            "publish_content": "Texto exato aprovado.",
            "publish_media": [],
        }})

    env = {
        "POSTIZ_URL": "https://postiz.example.com",
        "POSTIZ_API_KEY": "postiz-key",
        "POSTIZ_PUBLISH_TIMEOUT_SECONDS": "1",
        "POSTIZ_PUBLISH_POLL_SECONDS": "0.1",
    }
    get_responses = [
        _PostizResponse([{
            "id": "int-linkedin", "identifier": "linkedin", "disabled": False,
        }]),
        _PostizResponse({"posts": [{"id": "post-1", "state": "PUBLISHED"}]}),
    ]
    with patch.dict("os.environ", env, clear=False), \
         patch("heartbeat_outcome.requests.get", side_effect=get_responses) as mock_get, \
         patch("heartbeat_outcome.requests.post", return_value=_PostizResponse([
             {"postId": "post-1", "integration": "int-linkedin"}
         ])) as mock_post:
        result = heartbeat_outcome._run_publish_action(_Row(), conn)

    assert result["published"] is True
    assert "PUBLISHED" in result["detail"]
    assert mock_get.call_count == 2
    sent = mock_post.call_args.kwargs["json"]
    assert sent["type"] == "now"
    assert sent["posts"][0]["integration"]["id"] == "int-linkedin"
    assert sent["posts"][0]["value"][0]["content"] == "Texto exato aprovado."
    assert sent["posts"][0]["settings"] == {"__type": "linkedin"}


def test_run_publish_action_rejects_ambiguous_integration(conn):
    class _Row:
        payload = json.dumps({"outcome": {
            "publish_target": "linkedin", "publish_content": "Texto", "publish_media": [],
        }})

    integrations = [
        {"id": "int-1", "identifier": "linkedin", "disabled": False},
        {"id": "int-2", "identifier": "linkedin", "disabled": False},
    ]
    with patch.dict("os.environ", {
        "POSTIZ_URL": "https://postiz.example.com", "POSTIZ_API_KEY": "key",
    }, clear=False), patch(
        "heartbeat_outcome.requests.get", return_value=_PostizResponse(integrations)
    ), patch("heartbeat_outcome.requests.post") as mock_post:
        result = heartbeat_outcome._run_publish_action(_Row(), conn)

    assert result["published"] is False
    assert "inequívoca" in result["detail"]
    mock_post.assert_not_called()


def test_run_publish_action_instagram_requires_allowlisted_media(conn):
    class _Row:
        payload = json.dumps({"outcome": {
            "publish_target": "instagram",
            "publish_content": "Legenda aprovada.",
            "publish_media": ["https://evil.example/image.jpg"],
        }})

    with patch.dict("os.environ", {
        "POSTIZ_URL": "https://postiz.example.com",
        "POSTIZ_API_KEY": "key",
        "POSTIZ_ALLOWED_MEDIA_HOSTS": "cdn.example.com",
    }, clear=False), patch(
        "heartbeat_outcome.requests.get",
        return_value=_PostizResponse([{
            "id": "int-instagram", "identifier": "instagram", "disabled": False,
        }]),
    ), patch("heartbeat_outcome.requests.post") as mock_post:
        result = heartbeat_outcome._run_publish_action(_Row(), conn)

    assert result["published"] is False
    assert "URL de mídia inválida" in result["detail"]
    mock_post.assert_not_called()
