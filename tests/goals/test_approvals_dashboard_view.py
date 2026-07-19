"""
tests/goals/test_approvals_dashboard_view.py

Panorama 2026-07-17, item 1 — before this, pending_approvals only ever
surfaced as a Telegram message with no dashboard fallback. Covers:
  - GET /api/approvals (list, status filter) and GET /api/approvals/<id>
    (single, with rendered Missão/Projeto context + structured items).
  - POST /api/approvals/<id>/dashboard-decision — a SEPARATE decision path
    from /decision, gated so an agent holding DASHBOARD_API_TOKEN (which
    maps to an admin current_user via _try_api_token_auth) cannot use it —
    only a real cookie-authenticated admin session can. This is the same
    threat V1 already closed for the bridge-token endpoint, generalized to
    the new one.

Run:
    cd /path/to/workspace && pytest tests/goals/test_approvals_dashboard_view.py -v
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
    _app.config["SECRET_KEY"] = "test-approvals-dashboard-view"
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

    # Test-only shim for the one thing app.py's real before_request normally
    # provides: setting g.auth_via_api_token when a request "authenticated"
    # via the API-token path. Simulated here via a header the real bridge
    # never sends, so we can prove decide_approval_via_dashboard rejects it.
    @_app.before_request
    def _simulate_api_token_auth():
        from flask import request, g
        if request.headers.get("X-Simulate-Api-Token-Auth") == "1":
            g.auth_via_api_token = True

    with _app.app_context():
        _models.db.create_all()
        admin = _models.User(username="admin", role="admin")
        admin.set_password("password")
        _models.db.session.add(admin)
        viewer = _models.User(username="viewer", role="viewer")
        viewer.set_password("password")
        _models.db.session.add(viewer)
        _models.db.session.commit()

        mission = _models.Mission(slug="m1", title="Evolution MRR", created_at=NOW, updated_at=NOW)
        _models.db.session.add(mission)
        _models.db.session.commit()

    import routes.goals as _goals_routes
    import routes.approvals as _approvals_routes
    importlib.reload(_goals_routes)
    importlib.reload(_approvals_routes)
    _app.register_blueprint(_goals_routes.bp)
    _app.register_blueprint(_approvals_routes.bp)

    return _app


@pytest.fixture
def admin_client(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        yield c


@pytest.fixture(autouse=True)
def _approval_env(monkeypatch):
    monkeypatch.setenv("APPROVAL_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.setenv("APPROVAL_APPROVER_IDS", "12345")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def _create_project_suggestion_approval(client, mission_id=1) -> int:
    payload = {
        "title": "Aprovar Projects", "body": "resumo",
        "projects": [{"slug": "evo-ai", "title": "Evo AI", "description": "CRM"}],
    }
    with patch("notifications.send_approval_request", return_value=None):
        resp = client.post("/api/approvals", json={
            "gate_type": "project_suggestion", "mission_id": mission_id,
            "agent": "project-planner", "payload": payload,
        })
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["id"]


# ---------------------------------------------------------------------------
# GET /api/approvals — list + single
# ---------------------------------------------------------------------------

def test_list_defaults_to_pending_only(admin_client):
    approval_id = _create_project_suggestion_approval(admin_client)

    resp = admin_client.get("/api/approvals")
    assert resp.status_code == 200
    approvals = resp.get_json()["approvals"]
    assert any(a["id"] == approval_id for a in approvals)
    assert all(a["status"] == "pending" for a in approvals)


def test_list_status_all_includes_decided(admin_client):
    approval_id = _create_project_suggestion_approval(admin_client)
    with patch("heartbeat_dispatcher.dispatch"):
        admin_client.post(f"/api/approvals/{approval_id}/dashboard-decision", json={"decision": "reject"})

    pending = admin_client.get("/api/approvals?status=pending").get_json()["approvals"]
    assert not any(a["id"] == approval_id for a in pending)

    all_approvals = admin_client.get("/api/approvals?status=all").get_json()["approvals"]
    assert any(a["id"] == approval_id and a["status"] == "rejected" for a in all_approvals)


def test_get_single_includes_context_and_items_preview(admin_client):
    approval_id = _create_project_suggestion_approval(admin_client)
    resp = admin_client.get(f"/api/approvals/{approval_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["context"] == "Missão: Evolution MRR"
    assert "Evo AI" in body["items_preview"]


def test_get_single_404_for_missing(admin_client):
    resp = admin_client.get("/api/approvals/999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/approvals/<id>/dashboard-decision
# ---------------------------------------------------------------------------

def test_dashboard_decision_approve_by_admin_session_works(admin_client):
    approval_id = _create_project_suggestion_approval(admin_client)
    with patch("heartbeat_dispatcher.dispatch"):
        resp = admin_client.post(f"/api/approvals/{approval_id}/dashboard-decision", json={"decision": "approve"})
    assert resp.status_code == 200, resp.get_json()

    detail = admin_client.get(f"/api/approvals/{approval_id}").get_json()
    assert detail["status"] == "approved"
    assert detail["decided_by"] == "dashboard:admin"


def test_dashboard_decision_rejects_api_token_auth(admin_client):
    """The core V1-style protection: a caller that reached this handler via
    DASHBOARD_API_TOKEN (simulated here — see the app fixture's before_request
    shim) must be rejected even though it maps to an admin current_user."""
    approval_id = _create_project_suggestion_approval(admin_client)
    resp = admin_client.post(
        f"/api/approvals/{approval_id}/dashboard-decision",
        json={"decision": "approve"},
        headers={"X-Simulate-Api-Token-Auth": "1"},
    )
    assert resp.status_code == 403

    detail = admin_client.get(f"/api/approvals/{approval_id}").get_json()
    assert detail["status"] == "pending"  # untouched


def test_dashboard_decision_rejects_non_admin_role(admin_client):
    approval_id = _create_project_suggestion_approval(admin_client)
    with admin_client.session_transaction() as sess:
        sess["_user_id"] = "2"  # switch to the viewer user created in the app fixture
        sess["_fresh"] = True
    resp = admin_client.post(f"/api/approvals/{approval_id}/dashboard-decision", json={"decision": "approve"})
    assert resp.status_code == 403


def test_dashboard_decision_double_press_is_noop_409(admin_client):
    approval_id = _create_project_suggestion_approval(admin_client)
    with patch("heartbeat_dispatcher.dispatch"):
        first = admin_client.post(f"/api/approvals/{approval_id}/dashboard-decision", json={"decision": "approve"})
        assert first.status_code == 200
        second = admin_client.post(f"/api/approvals/{approval_id}/dashboard-decision", json={"decision": "approve"})
    assert second.status_code == 409


def test_dashboard_decision_invalid_decision_value_400(admin_client):
    approval_id = _create_project_suggestion_approval(admin_client)
    resp = admin_client.post(f"/api/approvals/{approval_id}/dashboard-decision", json={"decision": "maybe"})
    assert resp.status_code == 400
