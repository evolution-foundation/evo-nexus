"""Smoke tests — CSRF guard on knowledge_proxy and integrations write endpoints.

Verifies that POST/PATCH/DELETE endpoints on knowledge_proxy and integrations
return 403 when X-Requested-With header is absent (no session cookie needed:
the CSRF check fires before any auth logic).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


def _make_app():
    """Minimal Flask app with knowledge_proxy and integrations blueprints."""
    _add_backend()

    from flask import Flask
    from flask_login import LoginManager, login_user

    class _FakeUser:
        id = 1
        username = "test_admin"
        is_authenticated = True
        is_active = True
        is_anonymous = False
        role = "admin"

        def get_id(self):
            return str(self.id)

    fake_user = _FakeUser()

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "csrf-smoke-secret"
    flask_app.config["WTF_CSRF_ENABLED"] = False

    lm = LoginManager(flask_app)

    @lm.user_loader
    def load_user(uid):
        return fake_user

    # Bypass require_permission so auth does not interfere with CSRF smoke
    import routes.auth_routes as auth_mod
    _orig = auth_mod.require_permission

    def _passthrough(resource, action):
        def decorator(f):
            return f
        return decorator

    auth_mod.require_permission = _passthrough

    with patch("knowledge.assert_master_key", return_value=None):
        from routes.knowledge_proxy import bp as proxy_bp
        flask_app.register_blueprint(proxy_bp)

    from routes.integrations import bp as integrations_bp
    flask_app.register_blueprint(integrations_bp)

    # Keep bypassed for test duration
    auth_mod.require_permission = _passthrough

    return flask_app, fake_user


import pytest


@pytest.fixture(scope="module")
def csrf_app():
    app, user = _make_app()
    return app, user


@pytest.fixture()
def csrf_client(csrf_app):
    app, user = csrf_app
    with app.test_client() as c:
        with app.test_request_context():
            from flask_login import login_user
            login_user(user)
        yield c


# ---------------------------------------------------------------------------
# H-1: knowledge_proxy write endpoints — no X-Requested-With → 403
# ---------------------------------------------------------------------------

class TestKnowledgeProxyCsrf:
    """POST /api/knowledge/connections/<cid>/api-keys without X-Requested-With → 403."""

    def test_create_api_key_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.post(
            "/api/knowledge/connections/test-cid/api-keys",
            json={"name": "test-key"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_create_api_key_with_xhr_passes_csrf(self, csrf_client):
        """With header present, CSRF check passes (may fail on business logic — that is ok)."""
        with patch("knowledge.api_keys.create_api_key", return_value=({"id": "k1"}, "tok")):
            resp = csrf_client.post(
                "/api/knowledge/connections/test-cid/api-keys",
                json={"name": "test-key"},
                headers={
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        # CSRF passed — response is not 403 (may be 201, 400, 500 depending on mocks)
        assert resp.status_code != 403

    def test_delete_api_key_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.delete(
            "/api/knowledge/connections/test-cid/api-keys/some-kid",
            headers={},
        )
        assert resp.status_code == 403

    def test_upload_document_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.post(
            "/api/knowledge/connections/test-cid/documents",
            data={"space_id": "s1"},
            headers={},
        )
        assert resp.status_code == 403

    def test_create_space_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.post(
            "/api/knowledge/connections/test-cid/spaces",
            json={},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_delete_space_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.delete(
            "/api/knowledge/connections/test-cid/spaces/some-sid",
        )
        assert resp.status_code == 403

    def test_create_unit_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.post(
            "/api/knowledge/connections/test-cid/spaces/s1/units",
            json={},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_delete_unit_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.delete(
            "/api/knowledge/connections/test-cid/spaces/s1/units/u1",
        )
        assert resp.status_code == 403

    def test_reorder_units_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.post(
            "/api/knowledge/connections/test-cid/spaces/s1/units/reorder",
            json={"ordered_ids": []},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_bearer_token_bypasses_csrf(self, csrf_client):
        """Bearer auth is exempt from CSRF check (not session-cookie auth)."""
        with patch("knowledge.api_keys.create_api_key", return_value=({"id": "k1"}, "tok")):
            resp = csrf_client.post(
                "/api/knowledge/connections/test-cid/api-keys",
                json={"name": "test-key"},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer some-dashboard-token",
                },
            )
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# H-2: /api/integrations/custom — no X-Requested-With → 403
# ---------------------------------------------------------------------------

class TestIntegrationsCsrf:
    def test_create_custom_integration_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.post(
            "/api/integrations/custom",
            json={"slug": "my-int", "displayName": "My Integration"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_create_custom_integration_with_xhr_passes_csrf(self, csrf_client, tmp_path):
        """With header present, CSRF check passes — may fail on business logic."""
        with (
            patch("routes.integrations.SKILLS_DIR", tmp_path),
            patch("routes.integrations.audit", return_value=None),
            patch("routes.integrations.current_user", MagicMock(id=1, username="admin")),
        ):
            resp = csrf_client.post(
                "/api/integrations/custom",
                json={"slug": "my-int", "displayName": "My Integration"},
                headers={
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        assert resp.status_code != 403

    def test_update_custom_integration_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.patch(
            "/api/integrations/custom/my-int",
            json={"displayName": "Updated"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_delete_custom_integration_no_xhr_returns_403(self, csrf_client):
        resp = csrf_client.delete(
            "/api/integrations/custom/my-int",
        )
        assert resp.status_code == 403
