from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("EVONEXUS_HERMES_USERNAME", "hermes-test-user")
    monkeypatch.setenv("EVONEXUS_HERMES_PASSWORD", "hermes-test-password")
    import models as models_module
    import routes.tailscale as tailscale_module

    importlib.reload(tailscale_module)

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="tailscale-test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    models_module.db.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return models_module.db.session.get(models_module.User, int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        return {"error": "Authentication required"}, 401

    app.register_blueprint(tailscale_module.bp)

    with app.app_context():
        models_module.db.create_all()
        models_module.seed_roles()
        for username, role in (("admin", "admin"), ("operator", "operator")):
            user = models_module.User(
                username=username,
                email=f"{username}@example.com",
                display_name=username.title(),
                role=role,
            )
            user.set_password("Strong!234")
            models_module.db.session.add(user)
        models_module.db.session.commit()

    return app, tailscale_module


@pytest.fixture
def client(app):
    flask_app, _ = app
    return flask_app.test_client()


def _login(client, username):
    from models import User

    with client.application.app_context():
        user = User.query.filter_by(username=username).one()
        user_id = user.id
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def _completed(*, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_status_requires_authentication(client):
    assert client.get("/api/tailscale/status").status_code == 401


def test_status_reports_needs_login_as_disconnected(client, app, monkeypatch):
    _, tailscale = app
    _login(client, "operator")
    payload = {"BackendState": "NeedsLogin", "Self": {"Online": False}}
    monkeypatch.setattr(tailscale, "_tailscale", lambda *args, **kwargs: _completed(stdout=json.dumps(payload)))

    response = client.get("/api/tailscale/status")

    assert response.status_code == 200
    assert response.get_json()["connected"] is False


def test_connect_requires_config_manage_permission(client, app):
    _login(client, "operator")

    response = client.post("/api/tailscale/connect", json={"auth_key": "tskey-auth-example"})

    assert response.status_code == 403


def test_connect_fails_closed_without_hermes_remote_auth(client, app, monkeypatch):
    _, tailscale = app
    _login(client, "admin")
    calls = []
    monkeypatch.delenv("EVONEXUS_HERMES_USERNAME")
    monkeypatch.delenv("EVONEXUS_HERMES_PASSWORD")
    monkeypatch.setattr(
        tailscale,
        "_tailscale",
        lambda *args, **kwargs: calls.append(args) or _completed(),
    )

    response = client.post(
        "/api/tailscale/connect", json={"auth_key": "tskey-auth-example"}
    )

    assert response.status_code == 503
    assert calls == []


def test_connect_uses_configured_hostname(client, app, monkeypatch):
    _, tailscale = app
    _login(client, "admin")
    calls = []
    statuses = iter(
        [
            {"BackendState": "NeedsLogin"},
            {
                "BackendState": "Running",
                "Self": {"TailscaleIPs": ["100.64.0.1"], "HostName": "nexus-node", "Online": True},
                "MagicDNSSuffix": "example.ts.net",
            },
        ]
    )

    def fake_tailscale(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("status", "--json"):
            return _completed(stdout=json.dumps(next(statuses)))
        return _completed()

    monkeypatch.setenv("EVONEXUS_TAILSCALE_HOSTNAME", "nexus-node")
    monkeypatch.setattr(tailscale, "_tailscale", fake_tailscale)

    response = client.post("/api/tailscale/connect", json={"auth_key": "tskey-auth-example"})

    assert response.status_code == 200
    assert ("up", "--authkey=tskey-auth-example", "--accept-dns", "--hostname=nexus-node", "--reset") in calls
    assert ("serve", "--bg", "--tcp=9119", "tcp://127.0.0.1:9119") in calls


def test_connect_repairs_hermes_serve_on_an_already_connected_node(client, app, monkeypatch):
    _, tailscale = app
    _login(client, "admin")
    calls = []
    monkeypatch.setattr(
        tailscale,
        "_get_status",
        lambda: {"connected": True, "ip": "100.64.0.1"},
    )
    monkeypatch.setattr(
        tailscale,
        "_tailscale",
        lambda *args, **kwargs: calls.append(args) or _completed(),
    )

    response = client.post(
        "/api/tailscale/connect", json={"auth_key": "tskey-auth-example"}
    )

    assert response.status_code == 200
    assert ("up", "--authkey=tskey-auth-example") not in calls
    assert ("serve", "--bg", "--tcp=9119", "tcp://127.0.0.1:9119") in calls


def test_disconnect_invokes_plain_down(client, app, monkeypatch):
    _, tailscale = app
    _login(client, "admin")
    calls = []
    monkeypatch.setattr(tailscale, "_get_status", lambda: {"connected": True})

    def fake_tailscale(*args, **kwargs):
        calls.append(args)
        return _completed()

    monkeypatch.setattr(tailscale, "_tailscale", fake_tailscale)

    response = client.post("/api/tailscale/disconnect")

    assert response.status_code == 200
    assert calls == [("down",)]


def test_connect_failure_never_returns_or_logs_auth_key(client, app, monkeypatch, caplog):
    _, tailscale = app
    _login(client, "admin")
    key = "tskey-auth-" + "test-value"
    monkeypatch.setattr(tailscale, "_get_status", lambda: {"connected": False})
    monkeypatch.setattr(
        tailscale,
        "_tailscale",
        lambda *args, **kwargs: _completed(returncode=1, stderr=f"rejected {key}"),
    )

    response = client.post("/api/tailscale/connect", json={"auth_key": key})

    assert response.status_code == 502
    assert key not in response.get_data(as_text=True)
    assert key not in caplog.text


def test_connect_records_an_audit_event(client, app, monkeypatch):
    _, tailscale = app
    _login(client, "admin")
    statuses = iter(
        [
            {"BackendState": "NeedsLogin"},
            {"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.0.2"], "Online": True}},
        ]
    )

    def fake_tailscale(*args, **kwargs):
        if args[:2] == ("status", "--json"):
            return _completed(stdout=json.dumps(next(statuses)))
        return _completed()

    monkeypatch.setattr(tailscale, "_tailscale", fake_tailscale)

    response = client.post("/api/tailscale/connect", json={"auth_key": "tskey-auth-example"})

    assert response.status_code == 200
    with client.application.app_context():
        from models import AuditLog

        event = AuditLog.query.filter_by(action="tailscale.connect").one()
        assert event.resource == "integrations"
