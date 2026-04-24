"""Tests for backend health endpoints."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace-root"
    (root / "dashboard" / "data").mkdir(parents=True, exist_ok=True)
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "providers.json").write_text(json.dumps({"active": "anthropic"}), encoding="utf-8")

    monkeypatch.setenv("EVONEXUS_SECRET_KEY", "test-secret-health")
    monkeypatch.setenv("EVONEXUS_ENV", "development")

    import routes.health as _health

    importlib.reload(_health)
    monkeypatch.setattr(_health, "WORKSPACE", root)
    return root


@pytest.fixture
def app(workspace, monkeypatch):
    import flask
    from flask_login import LoginManager
    import models as _models
    import routes.health as _health

    importlib.reload(_models)
    importlib.reload(_health)
    monkeypatch.setattr(_health, "WORKSPACE", workspace)

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-secret-health"
    _app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    _models.db.init_app(_app)

    login_manager = LoginManager()
    login_manager.init_app(_app)

    @login_manager.user_loader
    def load_user(user_id):
        return _models.User.query.get(int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        return flask.jsonify({"error": "Authentication required"}), 401

    _app.register_blueprint(_health.bp)

    with _app.app_context():
        _models.db.create_all()
        _models.seed_roles()

        admin = _models.User(
            username="admin",
            email="admin@example.com",
            display_name="Admin",
            role="admin",
        )
        admin.set_password("Strong!234")
        _models.db.session.add(admin)

        viewer = _models.User(
            username="viewer",
            email="viewer@example.com",
            display_name="Viewer",
            role="viewer",
        )
        viewer.set_password("Strong!234")
        _models.db.session.add(viewer)

        _models.db.session.commit()

    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def _login_as(client, username):
    # Seed a valid flask-login session via the test client without routing
    # through the full auth blueprint (not registered in this test app).
    with client.session_transaction() as session:
        from models import User
        user = User.query.filter_by(username=username).one()
        session["_user_id"] = str(user.id)
        session["_fresh"] = True


def test_public_health_returns_only_status(client):
    response = client.get("/api/health")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload == {"status": "ok"}
    # Public endpoint must not leak internal structure.
    assert "checks" not in payload
    assert "timestamp" not in payload


def test_deep_health_requires_auth(client):
    response = client.get("/api/health/deep")
    assert response.status_code == 401


def test_deep_health_rejects_non_admin(client, app):
    with app.test_request_context():
        _login_as(client, "viewer")

    response = client.get("/api/health/deep")
    assert response.status_code == 403


def test_deep_health_includes_providers_for_admin(client, app):
    with app.test_request_context():
        _login_as(client, "admin")

    response = client.get("/api/health/deep")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["filesystem"]["status"] == "ok"
    assert payload["checks"]["workspace"]["status"] == "ok"
    assert payload["checks"]["secret_key"]["status"] == "ok"
    assert payload["checks"]["providers"]["status"] == "ok"
    assert payload["checks"]["providers"]["active"] == "anthropic"
    # Per-check detail must not leak absolute filesystem paths.
    for check in payload["checks"].values():
        assert "path" not in check
