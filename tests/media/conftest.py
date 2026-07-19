"""Shared fixtures for social-media-production tests.

Mirrors the Flask app fixture pattern in tests/goals/test_step7_publish_decomposition.py
(in-memory SQLite, models.db.create_all(), seeded users per role) plus the
media_jobs blueprint and the admin-only postiz core config blueprint.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def app(tmp_path, monkeypatch):
    import flask
    from flask_login import LoginManager
    import importlib
    import models as _models
    importlib.reload(_models)

    monkeypatch.setenv("MEDIA_WORKSPACE", str(tmp_path / "media"))

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-media"
    _app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    _models.db.init_app(_app)

    login_manager = LoginManager()
    login_manager.init_app(_app)

    @login_manager.user_loader
    def _load_user(user_id):
        return _models.db.session.get(_models.User, int(user_id))

    @login_manager.unauthorized_handler
    def _unauthorized():
        from flask import jsonify
        return jsonify({"error": "Authentication required"}), 401

    # Route modules bind `from models import db, ...` at import time — after
    # reloading `models` above, any already-imported route module (from an
    # earlier test in this session) still references the *old* db instance.
    # Reload them too, mirroring tests/goals/test_step7_publish_decomposition.py.
    import routes.media_jobs as _media_jobs_routes
    import routes.integrations_core_postiz as _postiz_core_routes
    importlib.reload(_media_jobs_routes)
    importlib.reload(_postiz_core_routes)
    _app.register_blueprint(_media_jobs_routes.bp)
    _app.register_blueprint(_postiz_core_routes.bp)

    with _app.app_context():
        _models.db.create_all()
        admin = _models.User(username="admin", role="admin")
        admin.set_password("password")
        viewer = _models.User(username="viewer", role="viewer")
        viewer.set_password("password")
        _models.db.session.add_all([admin, viewer])
        _models.db.session.commit()

    yield _app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(app):
    """A test client with an authenticated admin session (via Flask-Login
    test_request_context login, since the real /api/auth/login route isn't
    registered in this minimal app fixture).
    """
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True
    return c


@pytest.fixture
def viewer_client(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = "2"
        sess["_fresh"] = True
    return c
