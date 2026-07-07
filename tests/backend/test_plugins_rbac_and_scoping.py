"""Wave 2.1.x — endpoint-level RBAC + readonly auto-scoping tests.

Covers:
- PluginWritableResource accepts requires_role list (Pydantic validator)
- requires_role rejects bad role names
- writable_data handler returns 403 when role mismatch
- writable_data handler accepts when role matches OR user is 'admin'
- readonly_data auto-injects current_user_id and current_user_role
- readonly_data rejects client-supplied current_user_id (reserved param)
- backwards-compat: resources without requires_role still work for any user
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Pydantic schema tests — requires_role field
# ---------------------------------------------------------------------------


class TestRequiresRoleSchema:
    def test_writable_resource_accepts_requires_role(self):
        from plugin_schema import PluginWritableResource
        r = PluginWritableResource(
            id="patients_clinical",
            description="Clinical fields — nutri only",
            table="nutri_patients",
            allowed_columns=["goal", "consent_given_at"],
            requires_role=["nutricionista", "nutri-admin"],
        )
        assert r.requires_role == ["nutricionista", "nutri-admin"]

    def test_writable_resource_requires_role_optional(self):
        from plugin_schema import PluginWritableResource
        r = PluginWritableResource(
            id="open_resource",
            description="No RBAC — backwards compatible",
            table="nutri_brand_settings",
            allowed_columns=["office_name"],
        )
        assert r.requires_role is None

    def test_writable_resource_rejects_bad_role_name(self):
        from plugin_schema import PluginWritableResource
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PluginWritableResource(
                id="test",
                description="x",
                table="nutri_test",
                allowed_columns=["x"],
                requires_role=["Nutri Admin"],  # uppercase + space — should fail
            )

    def test_writable_resource_accepts_kebab_case_role(self):
        from plugin_schema import PluginWritableResource
        r = PluginWritableResource(
            id="t",
            description="x",
            table="nutri_test",
            allowed_columns=["x"],
            requires_role=["nutri-admin", "super-user", "viewer"],
        )
        assert r.requires_role == ["nutri-admin", "super-user", "viewer"]


# ---------------------------------------------------------------------------
# Flask app + handlers tests — RBAC enforcement + readonly auto-scoping
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Temp SQLite DB with users + plugins_installed + a fake nutri_test table."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, username TEXT, role TEXT NOT NULL DEFAULT 'viewer'
        );
        INSERT INTO users (id, username, role) VALUES
            (1, 'alice', 'nutricionista'),
            (2, 'bob',   'recepcao'),
            (3, 'admin', 'admin');
        CREATE TABLE plugins_installed (
            slug TEXT PRIMARY KEY, enabled INTEGER, status TEXT,
            capabilities_disabled TEXT
        );
        INSERT INTO plugins_installed (slug, enabled, status, capabilities_disabled)
            VALUES ('nutri', 1, 'active', '{}');
        CREATE TABLE nutri_test (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            owner_id INTEGER
        );
        INSERT INTO nutri_test (name, owner_id) VALUES
            ('Alice patient', 1),
            ('Alice patient 2', 1),
            ('Bob patient', 2);
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def app(tmp_path, tmp_db):
    """Flask app with plugins blueprint + a fake installed plugin manifest on disk."""
    import flask
    from flask_login import LoginManager

    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "nutri"
    plugin_dir.mkdir(parents=True)

    manifest = {
        "manifest": {
            "id": "nutri",
            "writable_data": [
                {
                    "id": "test_open",
                    "description": "open",
                    "table": "nutri_test",
                    "allowed_columns": ["name", "owner_id"],
                },
                {
                    "id": "test_clinical",
                    "description": "clinical",
                    "table": "nutri_test",
                    "allowed_columns": ["name", "owner_id"],
                    "requires_role": ["nutricionista", "nutri-admin"],
                },
            ],
            "readonly_data": [
                {
                    "id": "my_rows",
                    "description": "rows owned by current user",
                    "sql": "SELECT id, name FROM nutri_test WHERE owner_id = :current_user_id ORDER BY id",
                },
                {
                    "id": "all_rows",
                    "description": "all rows",
                    "sql": "SELECT id, name, owner_id FROM nutri_test ORDER BY id",
                },
            ],
        }
    }
    (plugin_dir / ".install-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Patch PLUGINS_DIR + _get_db before importing the blueprint
    import routes.plugins as plugins_mod
    plugins_mod.PLUGINS_DIR = plugins_root

    def _get_db_override():
        c = sqlite3.connect(str(tmp_db))
        c.row_factory = sqlite3.Row
        return c
    plugins_mod._get_db = _get_db_override

    from routes.plugins import bp as plugins_bp

    flask_app = flask.Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test"

    lm = LoginManager()
    lm.init_app(flask_app)

    class FakeUser:
        is_authenticated = True
        is_active = True
        is_anonymous = False
        def __init__(self, uid, role):
            self.id = uid
            self.username = f"user{uid}"
            self.role = role
        def get_id(self):
            return str(self.id)

    flask_app._fake_user = None  # set per test

    @lm.user_loader
    def loader(uid):
        return flask_app._fake_user if flask_app._fake_user else None

    @lm.unauthorized_handler
    def unauthorized():
        return flask.jsonify({"error": "auth required"}), 401

    flask_app.register_blueprint(plugins_bp)
    flask_app._FakeUser = FakeUser
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def login_as(app, client, uid, role):
    app._fake_user = app._FakeUser(uid, role)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


# ── writable_data RBAC ─────────────────────────────────────────────────────

class TestWritableDataRbac:
    def test_open_resource_accepts_any_role(self, app, client):
        """Resource without requires_role works for recepcao (backwards compat)."""
        login_as(app, client, 2, "recepcao")
        resp = client.post(
            "/api/plugins/nutri/data/test_open",
            json={"name": "new", "owner_id": 2},
        )
        assert resp.status_code in (200, 201), resp.get_json()

    def test_clinical_resource_rejects_recepcao(self, app, client):
        """requires_role=[nutricionista,nutri-admin] → recepcao gets 403."""
        login_as(app, client, 2, "recepcao")
        resp = client.post(
            "/api/plugins/nutri/data/test_clinical",
            json={"name": "leaked", "owner_id": 2},
        )
        assert resp.status_code == 403
        body = resp.get_json()
        assert "requires role" in body["error"].lower()

    def test_clinical_resource_accepts_nutricionista(self, app, client):
        """requires_role=[nutricionista,nutri-admin] → nutricionista passes."""
        login_as(app, client, 1, "nutricionista")
        resp = client.post(
            "/api/plugins/nutri/data/test_clinical",
            json={"name": "ok", "owner_id": 1},
        )
        assert resp.status_code in (200, 201), resp.get_json()

    def test_clinical_resource_admin_override(self, app, client):
        """role='admin' always passes (super-user override)."""
        login_as(app, client, 3, "admin")
        resp = client.post(
            "/api/plugins/nutri/data/test_clinical",
            json={"name": "by-admin", "owner_id": 3},
        )
        assert resp.status_code in (200, 201), resp.get_json()


# ── readonly_data auto-scoping ─────────────────────────────────────────────

class TestReadonlyAutoScoping:
    def test_current_user_id_injected(self, app, client):
        """SQL with :current_user_id returns only user 1's rows."""
        login_as(app, client, 1, "nutricionista")
        resp = client.get("/api/plugins/nutri/readonly-data/my_rows")
        assert resp.status_code in (200, 201), resp.get_json()
        rows = resp.get_json()["rows"]
        assert len(rows) == 2  # Alice has 2 rows
        assert all(r["name"].startswith("Alice") for r in rows)

    def test_current_user_id_changes_per_user(self, app, client):
        """Different user → different rows scoped automatically."""
        login_as(app, client, 2, "recepcao")
        resp = client.get("/api/plugins/nutri/readonly-data/my_rows")
        rows = resp.get_json()["rows"]
        assert len(rows) == 1  # Bob has 1 row
        assert rows[0]["name"] == "Bob patient"

    def test_client_cannot_spoof_current_user_id(self, app, client):
        """?current_user_id=2 from client → 400, identity is server-only."""
        login_as(app, client, 1, "nutricionista")
        resp = client.get("/api/plugins/nutri/readonly-data/my_rows?current_user_id=2")
        assert resp.status_code == 400
        assert "reserved" in resp.get_json()["error"].lower()

    def test_client_cannot_spoof_current_user_role(self, app, client):
        login_as(app, client, 2, "recepcao")
        resp = client.get(
            "/api/plugins/nutri/readonly-data/all_rows?current_user_role=admin"
        )
        assert resp.status_code == 400
        assert "reserved" in resp.get_json()["error"].lower()

    def test_query_without_current_user_ref_still_works(self, app, client):
        """Backwards compat — queries that don't reference :current_user_id work fine."""
        login_as(app, client, 1, "nutricionista")
        resp = client.get("/api/plugins/nutri/readonly-data/all_rows")
        assert resp.status_code == 200
        rows = resp.get_json()["rows"]
        assert len(rows) == 3  # all rows visible
