"""Tests for knowledge/middleware.py and the _ping route.

Uses Flask test client.  Rate-limiter Postgres calls are patched out for unit
tests; integration tests that hit real Postgres are gated behind
``requires_postgres``.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest

from flask import Flask

_PING = "/api/knowledge/v1/_ping"
_MOCK_DSN = "postgresql://mock/mock"


# ---------------------------------------------------------------------------
# App fixture — minimal Flask app with the knowledge_public blueprint
# Scope is "session" so routes can be added once before first request.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app(in_memory_db):
    from knowledge.api_keys import ensure_table
    ensure_table()

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"

    from routes.knowledge_public import bp
    flask_app.register_blueprint(bp)

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_key(**kwargs):
    from knowledge.api_keys import create_api_key
    defaults = {"name": "test", "connection_id": "conn-test"}
    defaults.update(kwargs)
    return create_api_key(**defaults)


# ---------------------------------------------------------------------------
# AC-11: DASHBOARD_API_TOKEN bypasses rate limit
# ---------------------------------------------------------------------------

class TestInternalAuth:
    def test_internal_token_accepted(self, client):
        with patch.dict(os.environ, {"DASHBOARD_API_TOKEN": "secret-internal"}):
            resp = client.post(_PING, headers=_auth("secret-internal"))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["auth_mode"] == "internal"

    def test_internal_no_rate_limit_called(self, client):
        """Internal token must NOT call check_rate_limit."""
        with patch.dict(os.environ, {"DASHBOARD_API_TOKEN": "secret-internal"}):
            with patch("knowledge.middleware.check_rate_limit") as mock_rl:
                resp = client.post(_PING, headers=_auth("secret-internal"))
                assert resp.status_code == 200
                mock_rl.assert_not_called()

    def test_wrong_internal_token_rejected(self, client):
        with patch.dict(os.environ, {"DASHBOARD_API_TOKEN": "secret-internal"}):
            # "wrong-token" doesn't start with "evo_k_" and doesn't match internal
            resp = client.post(_PING, headers=_auth("wrong-token"))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Missing / malformed auth
# ---------------------------------------------------------------------------

class TestMissingAuth:
    def test_no_header_returns_401(self, client):
        resp = client.post(_PING)
        assert resp.status_code == 401

    def test_non_bearer_returns_401(self, client):
        resp = client.post(
            _PING,
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401

    def test_garbage_bearer_returns_401(self, client):
        resp = client.post(_PING, headers=_auth("not-a-real-token"))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# External key auth — rate limit mocked out
# ---------------------------------------------------------------------------

class TestExternalAuth:
    def test_valid_key_accepted(self, client):
        row, token = _make_key(connection_id="c1")
        with patch("knowledge.middleware.get_dsn_for_connection", return_value=_MOCK_DSN):
            with patch("knowledge.middleware.check_rate_limit"):
                resp = client.post(_PING, headers=_auth(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auth_mode"] == "external"

    def test_revoked_key_rejected(self, client):
        from knowledge.api_keys import revoke_api_key
        row, token = _make_key(connection_id="c2")
        revoke_api_key(row["id"])
        resp = client.post(_PING, headers=_auth(token))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# AC-09: Scope violation
# ---------------------------------------------------------------------------

class TestScopeViolation:
    def test_wrong_space_id_returns_403(self, client):
        """Route with space_id view_arg not in key's space_ids → 403 (AC-09)."""
        row, token = _make_key(connection_id="scoped-conn", space_ids=["space-a"])

        with patch("knowledge.middleware.get_dsn_for_connection", return_value=_MOCK_DSN):
            with patch("knowledge.middleware.check_rate_limit"):
                resp = client.get(
                    "/api/knowledge/v1/spaces/space-b/_probe",
                    headers=_auth(token),
                )
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "scope_violation"

    def test_allowed_space_passes(self, client):
        row, token = _make_key(connection_id="scoped-conn2", space_ids=["space-x"])

        with patch("knowledge.middleware.get_dsn_for_connection", return_value=_MOCK_DSN):
            with patch("knowledge.middleware.check_rate_limit"):
                resp = client.get(
                    "/api/knowledge/v1/spaces/space-x/_probe",
                    headers=_auth(token),
                )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Rate limit responses
# ---------------------------------------------------------------------------

class TestRateLimitResponse:
    def test_429_with_retry_after(self, client):
        from knowledge.rate_limiter import RateLimitExceeded
        row, token = _make_key(connection_id="rl-conn")

        with patch("knowledge.middleware.get_dsn_for_connection", return_value=_MOCK_DSN):
            with patch(
                "knowledge.middleware.check_rate_limit",
                side_effect=RateLimitExceeded(retry_after=42, window="minute"),
            ):
                resp = client.post(_PING, headers=_auth(token))

        assert resp.status_code == 429
        data = resp.get_json()
        assert data["error"] == "rate_limit_exceeded"
        assert data["retry_after"] == 42
        assert resp.headers["Retry-After"] == "42"

    def test_503_when_rate_limiter_down(self, client):
        from knowledge.rate_limiter import RateLimiterUnavailable
        row, token = _make_key(connection_id="rl-conn2")

        with patch("knowledge.middleware.get_dsn_for_connection", return_value=_MOCK_DSN):
            with patch(
                "knowledge.middleware.check_rate_limit",
                side_effect=RateLimiterUnavailable("DB down"),
            ):
                resp = client.post(_PING, headers=_auth(token))

        assert resp.status_code == 503
        data = resp.get_json()
        assert data["error"] == "service_unavailable"
        assert resp.headers["Retry-After"] == "5"
