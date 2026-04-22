"""Tests for routes/knowledge.py settings endpoints.

Covers:
  GET  /api/knowledge/settings
  PUT  /api/knowledge/settings
  GET  /api/knowledge/embedders/models

Uses Flask test client with mocked auth (require_permission bypassed),
mocked _upsert_env_vars, mocked assert_master_key, and SQLite in-memory DB.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_login import LoginManager, login_user


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


# ---------------------------------------------------------------------------
# Fake user for flask-login
# ---------------------------------------------------------------------------

class _FakeUser:
    id = 1
    username = "test_admin"
    is_authenticated = True
    is_active = True
    is_anonymous = False
    role = "admin"

    def get_id(self):
        return str(self.id)


_FAKE_USER = _FakeUser()


# ---------------------------------------------------------------------------
# App fixture — minimal Flask app with the knowledge blueprint
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app(tmp_path_factory, in_memory_db):
    _add_backend()

    # Build a minimal SQLite DB for the settings endpoint (needs knowledge_connections)
    db_file = tmp_path_factory.mktemp("settings") / "settings_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS knowledge_connections
           (id TEXT PRIMARY KEY, slug TEXT, name TEXT, status TEXT)"""
    )
    conn.commit()
    conn.close()

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret-settings"
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_file}"
    flask_app.config["WTF_CSRF_ENABLED"] = False

    # Set up flask-login with a loader that always returns our fake user
    lm = LoginManager(flask_app)

    @lm.user_loader
    def load_user(uid):
        return _FAKE_USER

    # Patch require_permission to be a no-op decorator
    import routes.auth_routes as auth_mod
    _orig_require_permission = auth_mod.require_permission

    def _passthrough_require_permission(resource, action):
        def decorator(f):
            return f
        return decorator

    auth_mod.require_permission = _passthrough_require_permission

    # Patch assert_master_key to be a no-op
    with patch("knowledge.assert_master_key", return_value=None):
        from routes.knowledge import bp
        flask_app.register_blueprint(bp)

    auth_mod.require_permission = _orig_require_permission

    # Restore after registering
    def _noop_require_permission(resource, action):
        def decorator(f):
            return f
        return decorator

    # Keep patched for the duration of tests
    auth_mod.require_permission = _noop_require_permission

    return flask_app


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        with app.test_request_context():
            login_user(_FAKE_USER)
        yield c


# ---------------------------------------------------------------------------
# Helper headers — include X-Requested-With for CSRF check + logged-in session
# ---------------------------------------------------------------------------

_WRITE_HEADERS = {
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}

_READ_HEADERS = {}


# ---------------------------------------------------------------------------
# GET /api/knowledge/settings
# ---------------------------------------------------------------------------

class TestGetSettings:
    def test_returns_200_with_expected_fields(self, client):
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.get("/api/knowledge/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "embedder_provider" in data
        assert "embedder_model" in data
        assert "vector_dim" in data
        assert "parser_default" in data
        assert "locked" in data
        assert "openai_api_key_set" in data
        assert "gemini_api_key_set" in data

    def test_locked_false_when_no_connections(self, client):
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.get("/api/knowledge/settings")
        data = resp.get_json()
        assert data["locked"] is False

    def test_default_provider_is_local(self, client, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_EMBEDDER_PROVIDER", raising=False)
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.get("/api/knowledge/settings")
        data = resp.get_json()
        assert data["embedder_provider"] == "local"


# ---------------------------------------------------------------------------
# GET /api/knowledge/embedders/models
# ---------------------------------------------------------------------------

class TestListEmbedderModels:
    def test_returns_all_providers_when_no_filter(self, client):
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.get("/api/knowledge/embedders/models")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "providers" in data
        assert "local" in data["providers"]
        assert "openai" in data["providers"]
        assert "gemini" in data["providers"]

    def test_gemini_provider_lists_both_models(self, client):
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.get("/api/knowledge/embedders/models?provider=gemini")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["provider"] == "gemini"
        model_ids = [m["id"] for m in data["models"]]
        assert "gemini-embedding-001" in model_ids
        assert "gemini-embedding-2-preview" in model_ids

    def test_filter_by_openai_provider(self, client):
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.get("/api/knowledge/embedders/models?provider=openai")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["provider"] == "openai"
        model_ids = [m["id"] for m in data["models"]]
        assert "text-embedding-3-small" in model_ids
        assert "text-embedding-3-large" in model_ids

    def test_invalid_provider_returns_400(self, client):
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.get("/api/knowledge/embedders/models?provider=voyage")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PUT /api/knowledge/settings
# ---------------------------------------------------------------------------

class TestPutSettings:
    def test_missing_xhr_header_returns_403(self, client):
        """CSRF guard: requests without X-Requested-With must be rejected."""
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.put(
                "/api/knowledge/settings",
                json={"embedder_provider": "local"},
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 403

    def test_valid_parser_change_returns_200(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("KNOWLEDGE_DEFAULT_PARSER", "marker")
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with (
            patch("routes.knowledge._assert_key", return_value=None),
            patch("routes.knowledge._WORKSPACE_ROOT", tmp_path),
            patch("routes.integrations._upsert_env_vars", return_value=None),
            patch("routes.knowledge.audit", return_value=None),
            patch("routes.knowledge.current_user", _FAKE_USER),
        ):
            resp = client.put(
                "/api/knowledge/settings",
                json={"parser_default": "marker"},
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 200

    def test_invalid_provider_returns_400(self, client):
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.put(
                "/api/knowledge/settings",
                json={"embedder_provider": "voyage"},
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 400

    def test_openai_key_rejected_when_provider_is_local(self, client, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "local")
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.put(
                "/api/knowledge/settings",
                json={"openai_api_key": "sk-test123"},
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 400

    def test_openai_key_without_sk_prefix_returns_400(self, client, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "openai")
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.put(
                "/api/knowledge/settings",
                json={"embedder_provider": "openai", "openai_api_key": "badkey"},
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 400

    def test_empty_body_returns_current_settings(self, client):
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.put(
                "/api/knowledge/settings",
                json={},
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "embedder_provider" in data

    # -----------------------------------------------------------------
    # Gemini-specific validation
    # -----------------------------------------------------------------

    def test_gemini_key_rejected_when_provider_is_local(self, client, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "local")
        valid_key = "AIzaSy" + "a" * 33
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.put(
                "/api/knowledge/settings",
                json={"gemini_api_key": valid_key},
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 400
        assert "gemini_api_key" in resp.get_json().get("error", "")

    def test_gemini_key_rejected_when_pattern_invalid(self, client, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "gemini")
        with (
            patch("routes.knowledge._assert_key", return_value=None),
            patch("routes.integrations._upsert_env_vars", return_value=None),
            patch("routes.knowledge.audit", return_value=None),
            patch("routes.knowledge.current_user", _FAKE_USER),
        ):
            resp = client.put(
                "/api/knowledge/settings",
                json={
                    "embedder_provider": "gemini",
                    "gemini_api_key": "not-a-valid-key",
                },
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 400
        assert "AIzaSy" in resp.get_json().get("error", "")

    def test_gemini_valid_key_accepted(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "gemini")
        env_file = tmp_path / ".env"
        env_file.write_text("")
        valid_key = "AIzaSy" + "a" * 33
        with (
            patch("routes.knowledge._assert_key", return_value=None),
            patch("routes.knowledge._WORKSPACE_ROOT", tmp_path),
            patch("routes.integrations._upsert_env_vars", return_value=None),
            patch("routes.knowledge.audit", return_value=None),
            patch("routes.knowledge.current_user", _FAKE_USER),
        ):
            resp = client.put(
                "/api/knowledge/settings",
                json={
                    "embedder_provider": "gemini",
                    "gemini_api_key": valid_key,
                },
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 200

    def test_gemini_dim_rejected_when_provider_is_local(self, client, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "local")
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.put(
                "/api/knowledge/settings",
                json={"gemini_dim": 1536},
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 400

    def test_gemini_dim_invalid_value_rejected(self, client, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "gemini")
        with patch("routes.knowledge._assert_key", return_value=None):
            resp = client.put(
                "/api/knowledge/settings",
                json={
                    "embedder_provider": "gemini",
                    "gemini_dim": 512,
                },
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 400

    def test_gemini_model_2_preview_accepted(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "gemini")
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with (
            patch("routes.knowledge._assert_key", return_value=None),
            patch("routes.knowledge._WORKSPACE_ROOT", tmp_path),
            patch("routes.integrations._upsert_env_vars", return_value=None),
            patch("routes.knowledge.audit", return_value=None),
            patch("routes.knowledge.current_user", _FAKE_USER),
        ):
            resp = client.put(
                "/api/knowledge/settings",
                json={
                    "embedder_provider": "gemini",
                    "embedder_model": "gemini-embedding-2-preview",
                },
                headers=_WRITE_HEADERS,
            )
        assert resp.status_code == 200
