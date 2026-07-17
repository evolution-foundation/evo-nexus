"""
tests/goals/test_ai_hierarchy_suggestions.py

ai-hierarchy-suggestions quick-spec — generalizes the goal-ticket-unification
approval gate one rung up in each direction:

  Mission --(project-planner, gate_type=project_suggestion)--> Project
  Project --(goal-suggester,  gate_type=goal_suggestion)-----> Goal

Covers: mission_created/project_created dispatch on creation, POST
/api/approvals accepting mission_id/project_id, approve/reject creating the
right rows from payload, the intentional cascade (approving a
project_suggestion wakes goal-suggester per created Project; approving a
goal_suggestion wakes the existing goal-planner per created Goal), and the
same malformed-payload resilience already proven for decomposition.

Run:
    cd /path/to/workspace && pytest tests/goals/test_ai_hierarchy_suggestions.py -v
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

NOW = "2026-07-17T00:00:00.000000Z"


@pytest.fixture
def app():
    import flask
    from flask_login import LoginManager
    import models as _models
    importlib.reload(_models)

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-ai-hierarchy"
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
        _models.db.session.commit()

    import routes.goals as _goals_routes
    import routes.approvals as _approvals_routes
    importlib.reload(_goals_routes)
    importlib.reload(_approvals_routes)
    _app.register_blueprint(_goals_routes.bp)
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
    # Same safety net as test_step7_publish_decomposition.py — never let a
    # real send_approval_request escape to a real Telegram bot during tests.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def _bridge_headers():
    return {"Authorization": "Bearer test-bridge-token"}


def _create_mission(client) -> int:
    with patch("heartbeat_dispatcher.dispatch"):
        resp = client.post("/api/missions", json={"slug": "m-evo", "title": "Evolution MRR"})
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()["id"]


def _create_project(client, mission_id: int | None = None, suffix: str = "a") -> int:
    with patch("heartbeat_dispatcher.dispatch"):
        payload = {"slug": f"proj-{suffix}", "title": f"Project {suffix}"}
        if mission_id is not None:
            payload["mission_id"] = mission_id
        resp = client.post("/api/projects", json=payload)
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()["id"]


# ---------------------------------------------------------------------------
# Dispatch on creation
# ---------------------------------------------------------------------------

def test_create_mission_dispatches_mission_created(client):
    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        resp = client.post("/api/missions", json={"slug": "m1", "title": "M1"})
        mission_id = resp.get_json()["id"]
    mock_dispatch.assert_called_once_with(
        "project-planner", "mission_created", {"mission_id": mission_id}
    )


def test_create_project_dispatches_project_created(client):
    mission_id = _create_mission(client)
    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        resp = client.post("/api/projects", json={
            "slug": "p1", "title": "P1", "mission_id": mission_id,
        })
        project_id = resp.get_json()["id"]
    mock_dispatch.assert_called_once_with(
        "goal-suggester", "project_created", {"project_id": project_id}
    )


# ---------------------------------------------------------------------------
# project_suggestion — Mission -> Project
# ---------------------------------------------------------------------------

def test_project_suggestion_approve_creates_projects_and_cascades(client):
    mission_id = _create_mission(client)

    payload = {
        "title": "Aprovar Projects", "body": "resumo",
        "projects": [
            {"slug": "evo-ai", "title": "Evo AI", "description": "CRM"},
            {"slug": "evo-summit", "title": "Evolution Summit"},
        ],
    }
    create_resp = client.post("/api/approvals", json={
        "gate_type": "project_suggestion", "mission_id": mission_id,
        "agent": "project-planner", "payload": payload,
    })
    assert create_resp.status_code == 201, create_resp.get_json()
    approval_id = create_resp.get_json()["id"]
    assert create_resp.get_json()["idempotency_key"] == f"projsug:{mission_id}:0"

    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        decision_resp = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approve", "from_id": "12345"},
            headers=_bridge_headers(),
        )
        assert decision_resp.status_code == 200, decision_resp.get_json()

    projects = client.get(f"/api/projects?mission_id={mission_id}").get_json()
    assert len(projects) == 2
    titles = {p["title"] for p in projects}
    assert titles == {"Evo AI", "Evolution Summit"}
    for p in projects:
        assert p["mission_id"] == mission_id

    # Cascade: goal-suggester woken once per created Project.
    assert mock_dispatch.call_count == 2
    dispatched_agents = {call.args[0] for call in mock_dispatch.call_args_list}
    dispatched_triggers = {call.args[1] for call in mock_dispatch.call_args_list}
    assert dispatched_agents == {"goal-suggester"}
    assert dispatched_triggers == {"project_created"}


def test_project_suggestion_reject_creates_zero_projects(client):
    mission_id = _create_mission(client)
    payload = {"title": "t", "body": "b", "projects": [{"slug": "x", "title": "X"}]}
    create_resp = client.post("/api/approvals", json={
        "gate_type": "project_suggestion", "mission_id": mission_id,
        "agent": "project-planner", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        decision_resp = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "reject", "from_id": "12345", "reason": "não agora"},
            headers=_bridge_headers(),
        )
    assert decision_resp.status_code == 200, decision_resp.get_json()
    assert client.get(f"/api/projects?mission_id={mission_id}").get_json() == []
    mock_dispatch.assert_not_called()


def test_project_suggestion_skips_malformed_and_duplicate_slugs(client):
    mission_id = _create_mission(client)
    existing_id = _create_project(client, mission_id=mission_id, suffix="existing")

    payload = {
        "title": "t", "body": "b",
        "projects": [
            "not-a-dict", None, 42,
            {"slug": "proj-existing", "title": "Duplicate of existing"},  # slug collision
            {"title": "No slug at all"},  # missing slug
            {"slug": "brand-new", "title": "Brand New"},
        ],
    }
    create_resp = client.post("/api/approvals", json={
        "gate_type": "project_suggestion", "mission_id": mission_id,
        "agent": "project-planner", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    with patch("heartbeat_dispatcher.dispatch"):
        decision_resp = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approve", "from_id": "12345"},
            headers=_bridge_headers(),
        )
    assert decision_resp.status_code == 200, decision_resp.get_json()

    projects = client.get(f"/api/projects?mission_id={mission_id}").get_json()
    titles = {p["title"] for p in projects}
    assert titles == {"Project existing", "Brand New"}
    assert len(projects) == 2  # existing (untouched) + the one valid new proposal


# ---------------------------------------------------------------------------
# goal_suggestion — Project -> Goal
# ---------------------------------------------------------------------------

def test_goal_suggestion_approve_creates_goals_and_cascades(client):
    project_id = _create_project(client, suffix="b")

    payload = {
        "title": "Aprovar Goals", "body": "resumo",
        "goals": [
            {"slug": "g-100-customers", "title": "100 customers", "metric_type": "count",
             "target_value": 100, "due_date": "2026-12-31"},
            {"slug": "g-ship-billing", "title": "Ship billing v2", "metric_type": "boolean",
             "due_date": "2026-11-30"},
        ],
    }
    create_resp = client.post("/api/approvals", json={
        "gate_type": "goal_suggestion", "project_id": project_id,
        "agent": "goal-suggester", "payload": payload,
    })
    assert create_resp.status_code == 201, create_resp.get_json()
    approval_id = create_resp.get_json()["id"]
    assert create_resp.get_json()["idempotency_key"] == f"goalsug:{project_id}:0"

    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        decision_resp = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approve", "from_id": "12345"},
            headers=_bridge_headers(),
        )
        assert decision_resp.status_code == 200, decision_resp.get_json()

    goals = client.get(f"/api/goals?project_id={project_id}").get_json()
    assert len(goals) == 2
    by_title = {g["title"]: g for g in goals}
    assert by_title["100 customers"]["target_value"] == 100
    assert by_title["100 customers"]["parent_goal_id"] is None
    assert by_title["Ship billing v2"]["target_value"] == 1.0  # boolean default
    for g in goals:
        assert g["project_id"] == project_id

    # Cascade: goal-planner woken once per created Goal.
    assert mock_dispatch.call_count == 2
    dispatched_agents = {call.args[0] for call in mock_dispatch.call_args_list}
    dispatched_triggers = {call.args[1] for call in mock_dispatch.call_args_list}
    assert dispatched_agents == {"goal-planner"}
    assert dispatched_triggers == {"goal_created"}


def test_goal_suggestion_reject_creates_zero_goals(client):
    project_id = _create_project(client, suffix="c")
    payload = {"title": "t", "body": "b", "goals": [
        {"slug": "x", "title": "X", "metric_type": "boolean"},
    ]}
    create_resp = client.post("/api/approvals", json={
        "gate_type": "goal_suggestion", "project_id": project_id,
        "agent": "goal-suggester", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    with patch("heartbeat_dispatcher.dispatch") as mock_dispatch:
        decision_resp = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "reject", "from_id": "12345"},
            headers=_bridge_headers(),
        )
    assert decision_resp.status_code == 200, decision_resp.get_json()
    assert client.get(f"/api/goals?project_id={project_id}").get_json() == []
    mock_dispatch.assert_not_called()


def test_goal_suggestion_skips_item_without_target_value_or_boolean(client):
    project_id = _create_project(client, suffix="d")
    payload = {
        "title": "t", "body": "b",
        "goals": [
            {"slug": "no-target", "title": "No target, not boolean", "metric_type": "count"},
            {"slug": "has-target", "title": "Has target", "metric_type": "count", "target_value": 5},
        ],
    }
    create_resp = client.post("/api/approvals", json={
        "gate_type": "goal_suggestion", "project_id": project_id,
        "agent": "goal-suggester", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    with patch("heartbeat_dispatcher.dispatch"):
        decision_resp = client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approve", "from_id": "12345"},
            headers=_bridge_headers(),
        )
    assert decision_resp.status_code == 200, decision_resp.get_json()

    goals = client.get(f"/api/goals?project_id={project_id}").get_json()
    assert len(goals) == 1
    assert goals[0]["title"] == "Has target"


def test_decision_double_press_project_suggestion_is_noop_409(client):
    mission_id = _create_mission(client)
    payload = {"title": "t", "body": "b", "projects": []}
    create_resp = client.post("/api/approvals", json={
        "gate_type": "project_suggestion", "mission_id": mission_id,
        "agent": "project-planner", "payload": payload,
    })
    approval_id = create_resp.get_json()["id"]

    with patch("heartbeat_dispatcher.dispatch"):
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
# Telegram approval body — mission/project context + rendered items list
# (Telegram audit fix, 2026-07-17): must not depend on the agent's free-text
# body alone to convey what's actually being proposed.
# ---------------------------------------------------------------------------

def test_project_suggestion_telegram_body_has_mission_context_and_items(client):
    mission_id = _create_mission(client)  # title="Evolution MRR"
    payload = {
        "title": "Aprovar Projects", "body": "resumo curto",
        "projects": [
            {"slug": "evo-ai", "title": "Evo AI", "description": "CRM"},
            {"slug": "evo-summit", "title": "Evolution Summit"},
        ],
    }
    with patch("notifications.send_approval_request") as mock_send:
        mock_send.return_value = None
        resp = client.post("/api/approvals", json={
            "gate_type": "project_suggestion", "mission_id": mission_id,
            "agent": "project-planner", "payload": payload,
        })
    assert resp.status_code == 201, resp.get_json()
    mock_send.assert_called_once()
    _, sent_title, sent_body = mock_send.call_args[0]
    assert "Evolution MRR" in sent_body
    assert "Evo AI" in sent_body
    assert "Evolution Summit" in sent_body
    assert "resumo curto" in sent_body


def test_goal_suggestion_telegram_body_has_project_context_and_items(client):
    mission_id = _create_mission(client)
    project_id = _create_project(client, mission_id=mission_id, suffix="ctx")
    payload = {
        "title": "Aprovar Goals", "body": "resumo",
        "goals": [{"slug": "g-ctx-1", "title": "100 clientes pagantes", "metric_type": "count", "target_value": 100}],
    }
    with patch("notifications.send_approval_request") as mock_send:
        mock_send.return_value = None
        resp = client.post("/api/approvals", json={
            "gate_type": "goal_suggestion", "project_id": project_id,
            "agent": "goal-suggester", "payload": payload,
        })
    assert resp.status_code == 201, resp.get_json()
    _, _, sent_body = mock_send.call_args[0]
    assert "Project ctx" in sent_body
    assert "100 clientes pagantes" in sent_body


def test_decomposition_telegram_body_has_goal_and_project_context(client):
    mission_id = _create_mission(client)
    project_id = _create_project(client, mission_id=mission_id, suffix="decomp")
    with patch("heartbeat_dispatcher.dispatch"):
        goal_resp = client.post("/api/goals", json={
            "slug": "g-decomp", "title": "Meta de Decomposição", "project_id": project_id,
        })
    goal_id = goal_resp.get_json()["id"]

    payload = {
        "title": "Aprovar decomposição", "body": "resumo",
        "tickets": [{"title": "Escrever 5 posts"}, {"title": "Gravar 1 reel"}],
    }
    with patch("notifications.send_approval_request") as mock_send:
        mock_send.return_value = None
        resp = client.post("/api/approvals", json={
            "gate_type": "decomposition", "goal_id": goal_id,
            "agent": "goal-planner", "payload": payload,
        })
    assert resp.status_code == 201, resp.get_json()
    _, _, sent_body = mock_send.call_args[0]
    assert "Meta de Decomposição" in sent_body
    assert "Escrever 5 posts" in sent_body
    assert "Gravar 1 reel" in sent_body


def test_telegram_body_skips_items_block_when_payload_has_no_list(client):
    mission_id = _create_mission(client)
    payload = {"title": "t", "body": "b"}  # no "projects" key at all
    with patch("notifications.send_approval_request") as mock_send:
        mock_send.return_value = None
        client.post("/api/approvals", json={
            "gate_type": "project_suggestion", "mission_id": mission_id,
            "agent": "project-planner", "payload": payload,
        })
    _, _, sent_body = mock_send.call_args[0]
    assert "Itens propostos" not in sent_body
