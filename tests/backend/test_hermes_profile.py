"""Tests for Hermes profile registry, deterministic routing, and the profiles API.

Covers ADWs/hermes_profiles.py (pure resolution) and
dashboard/backend/routes/hermes_profiles_routes.py (authenticated endpoint).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADW_DIR = REPO_ROOT / "ADWs"
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
for _p in (str(ADW_DIR), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Pure resolution — hermes_profiles.resolve_profile
# ---------------------------------------------------------------------------

# A fixed in-memory config so tests never depend on ~/.hermes contents.
def _fake_config(tmp_path: Path) -> dict:
    profiles_dir = tmp_path / "profiles"
    for slug in ("default-mac", "developer", "orchestrator", "researcher", "reviewer"):
        (profiles_dir / slug).mkdir(parents=True, exist_ok=True)
    return {
        "routing": {
            "infra": "developer",
            "build": "developer",
            "skill": "developer",
            "script": "developer",
            "planning": "orchestrator",
            "research": "researcher",
            "review": "reviewer",
            "qa": "reviewer",
        },
        "fallback_profile": "default-mac",
        "profiles_dir": str(profiles_dir),
    }


@pytest.fixture
def hp():
    import hermes_profiles as _hp
    importlib.reload(_hp)
    return _hp


@pytest.mark.parametrize("task_type,expected", [
    ("infra", "developer"),
    ("build", "developer"),
    ("skill", "developer"),
    ("script", "developer"),
    ("planning", "orchestrator"),
    ("research", "researcher"),
    ("review", "reviewer"),
    ("qa", "reviewer"),
])
def test_routing_maps_task_type_to_profile(hp, tmp_path, task_type, expected):
    slug, reason = hp.resolve_profile(task_type, None, config=_fake_config(tmp_path))
    assert slug == expected
    assert reason == f"routed:{task_type}"


def test_unknown_task_type_falls_back(hp, tmp_path):
    slug, reason = hp.resolve_profile("totally-unknown", None, config=_fake_config(tmp_path))
    assert slug == "default-mac"
    assert reason == "fallback"


def test_none_task_type_falls_back(hp, tmp_path):
    slug, reason = hp.resolve_profile(None, None, config=_fake_config(tmp_path))
    assert slug == "default-mac"
    assert reason == "fallback"


def test_valid_override_wins(hp, tmp_path):
    slug, reason = hp.resolve_profile("research", "reviewer", config=_fake_config(tmp_path))
    assert slug == "reviewer"
    assert reason == "manual_override"


def test_invalid_override_never_escalates(hp, tmp_path):
    # An uninstalled/garbage override must degrade to routing/fallback, not error.
    slug, reason = hp.resolve_profile("research", "not-a-real-profile", config=_fake_config(tmp_path))
    assert slug == "researcher"          # falls through to routing
    assert reason == "routed:research"


def test_malformed_override_ignored(hp, tmp_path):
    slug, _ = hp.resolve_profile("build", "BAD SLUG!!", config=_fake_config(tmp_path))
    assert slug == "developer"           # malformed slug ignored, routing applies


def test_fallback_missing_raises(hp, tmp_path):
    cfg = _fake_config(tmp_path)
    # Remove the fallback profile from disk → no installable profile.
    import shutil
    shutil.rmtree(Path(cfg["profiles_dir"]) / "default-mac")
    cfg["routing"] = {}
    with pytest.raises(RuntimeError):
        hp.resolve_profile("xpto", None, config=cfg)


def test_list_installed_profiles(hp, tmp_path):
    cfg = _fake_config(tmp_path)
    assert hp.list_installed_profiles(cfg) == [
        "default-mac", "developer", "orchestrator", "researcher", "reviewer",
    ]


def test_is_valid_slug(hp):
    assert hp.is_valid_slug("developer")
    assert hp.is_valid_slug("default-mac")
    assert not hp.is_valid_slug("Bad Slug")
    assert not hp.is_valid_slug("")
    assert not hp.is_valid_slug(None)


# ---------------------------------------------------------------------------
# API — /api/hermes/profiles + /resolve
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    import flask
    from flask_login import LoginManager
    import models as _models
    import hermes_profiles as _hp
    import routes.hermes_profiles_routes as _routes

    # Point the registry at a temp profiles dir with a known config.
    cfg = _fake_config(tmp_path)
    cfg_path = tmp_path / "hermes_profiles.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(_hp, "PROFILES_CONFIG", cfg_path)
    # write a config.yaml with a secret to verify masking
    dev_dir = Path(cfg["profiles_dir"]) / "developer"
    (dev_dir / "config.yaml").write_text(
        "provider: zai\nmodel: glm-4\napi_key: sk-supersecret-1234567890\n", encoding="utf-8"
    )

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-secret-hermes"
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

    _app.register_blueprint(_routes.bp)

    with _app.app_context():
        _models.db.create_all()
        _models.seed_roles()
        admin = _models.User(username="admin", email="admin@example.com",
                             display_name="Admin", role="admin")
        admin.set_password("Strong!234")
        _models.db.session.add(admin)
        _models.db.session.commit()

    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def _login(client):
    from models import User
    with client.application.app_context():
        user = User.query.filter_by(username="admin").one()
        uid = str(user.id)
    with client.session_transaction() as session:
        session["_user_id"] = uid
        session["_fresh"] = True


def test_profiles_requires_auth(client):
    assert client.get("/api/hermes/profiles").status_code == 401


def test_list_profiles(client):
    _login(client)
    resp = client.get("/api/hermes/profiles")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data["installed"]) == {
        "default-mac", "developer", "orchestrator", "researcher", "reviewer",
    }
    assert data["fallback_profile"] == "default-mac"
    assert data["routing"]["research"] == "researcher"


def test_list_profiles_masks_secrets(client):
    _login(client)
    data = client.get("/api/hermes/profiles").get_json()
    dev = next(p for p in data["profiles"] if p["slug"] == "developer")
    assert dev["provider"] == "zai"
    # Secret fields are never included in the response — not even masked.
    raw = json.dumps(dev)
    assert "sk-supersecret-1234567890" not in raw
    assert "api_key" not in raw
    # Only safe scalar metadata surfaces.
    assert set(dev.keys()) == {"slug", "provider", "model"}


def test_resolve_routed(client):
    _login(client)
    data = client.get("/api/hermes/profiles/resolve?task_type=research").get_json()
    assert data["profile"] == "researcher"
    assert data["reason"] == "routed:research"


def test_resolve_fallback(client):
    _login(client)
    data = client.get("/api/hermes/profiles/resolve?task_type=xpto").get_json()
    assert data["profile"] == "default-mac"
    assert data["reason"] == "fallback"


def test_resolve_override(client):
    _login(client)
    data = client.get("/api/hermes/profiles/resolve?task_type=research&override=reviewer").get_json()
    assert data["profile"] == "reviewer"
    assert data["reason"] == "manual_override"


def test_resolve_requires_auth(client):
    assert client.get("/api/hermes/profiles/resolve?task_type=research").status_code == 401
