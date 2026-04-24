"""Integration tests for GET /api/plugins/<slug>/update/preview (Wave 1.2).

Tests:
- Correct JSON shape for clean update
- Cache: second call within 300s does not re-invoke _compute_preview
- Read-only: mtime of plugins/<slug>/ unchanged after preview call
- not_found → 404
- invalid_source (ValueError) → 400
- fetch_failed (RuntimeError) → 500
- not_newer → 200 with up_to_date:true
"""

import json
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Minimal Flask app with plugins blueprint + temp SQLite DB
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plugins_installed (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'essential',
    source_type TEXT,
    source_url TEXT,
    source_ref TEXT,
    installed_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    manifest_json TEXT,
    install_sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_error TEXT,
    capabilities_disabled TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS plugin_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT,
    success INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""

_CLEAN_MANIFEST = {
    "files": {
        ".claude/agents/pm-nova.md": {"sha256": "aaaa"},
        ".claude/skills/project-health.md": {"sha256": "bbbb"},
        "migrations/install.sql": {"sha256": "sqlsha1"},
    },
    "id": "pm-essentials",
    "name": "PM Essentials",
    "version": "0.2.0",
    "description": "desc",
    "author": "test",
    "license": "MIT",
    "min_evonexus_version": "0.1.0",
    "tier": "essential",
    "capabilities": [],
}


@pytest.fixture(scope="module")
def tmp_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    # Insert a fixture plugin row (v0.1.0)
    installed_manifest = {
        "files": {
            ".claude/agents/pm-nova.md": {"sha256": "oldsha"},
            "migrations/install.sql": {"sha256": "sqlsha1"},  # same SHA as candidate
        },
        "id": "pm-essentials",
        "name": "PM Essentials",
        "version": "0.1.0",
        "capabilities": [],
    }
    conn.execute(
        "INSERT INTO plugins_installed (id, slug, name, version, source_url, installed_at, manifest_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()), "pm-essentials", "PM Essentials", "0.1.0",
            "github:test/pm-essentials", "2026-01-01T00:00:00Z",
            json.dumps(installed_manifest),
        )
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(scope="module")
def app(tmp_db):
    import flask
    from flask_login import LoginManager

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test"
    _app.config["WTF_CSRF_ENABLED"] = False

    login_manager = LoginManager()
    login_manager.init_app(_app)

    @login_manager.unauthorized_handler
    def unauthorized():
        return flask.jsonify({"error": "Authentication required"}), 401

    # Patch DB_PATH before importing plugins blueprint
    import routes.plugins as plugins_mod
    plugins_mod.DB_PATH = tmp_db

    from routes.plugins import bp as plugins_bp
    _app.register_blueprint(plugins_bp)

    yield _app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(app, client):
    """Client that bypasses login_required by patching current_user."""
    with patch("flask_login.utils._get_user") as mock_user:
        user = MagicMock()
        user.is_authenticated = True
        user.is_active = True
        user.is_anonymous = False
        user.get_id.return_value = "1"
        mock_user.return_value = user
        yield client


# ---------------------------------------------------------------------------
# Good preview result fixture
# ---------------------------------------------------------------------------

GOOD_PREVIEW = {
    "from_version": "0.1.0",
    "to_version": "0.2.0",
    "added": {"agents": ["pm-nova.md"]},
    "removed": {},
    "modified": {},
    "sql_migrations_blocked": False,
    "breaking_changes": [],
    "tarball_sha7": "abc1234",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPreviewEndpoint:
    def test_not_found_returns_404(self, auth_client):
        with patch("routes.plugins._compute_preview", side_effect=ValueError("invalid_source: x")):
            resp = auth_client.get("/api/plugins/nonexistent-slug/update/preview")
        assert resp.status_code == 404

    def test_invalid_source_returns_400(self, auth_client, tmp_db):
        with patch("routes.plugins._compute_preview", side_effect=ValueError("invalid_source: bad url")):
            resp = auth_client.get("/api/plugins/pm-essentials/update/preview?source=bad")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_source"

    def test_fetch_failed_returns_500(self, auth_client):
        with patch("routes.plugins._compute_preview", side_effect=RuntimeError("fetch_failed: network error")):
            resp = auth_client.get("/api/plugins/pm-essentials/update/preview?source=github:test/test")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error"] == "fetch_failed"

    def test_clean_preview_returns_correct_shape(self, auth_client):
        with patch("routes.plugins._compute_preview", return_value=GOOD_PREVIEW.copy()):
            resp = auth_client.get("/api/plugins/pm-essentials/update/preview")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "from_version" in data
        assert "to_version" in data
        assert "added" in data
        assert "removed" in data
        assert "modified" in data
        assert "sql_migrations_blocked" in data
        assert "breaking_changes" in data
        assert "tarball_sha7" in data
        assert "up_to_date" not in data  # clean update, not up_to_date

    def test_not_newer_returns_up_to_date(self, auth_client):
        import routes.plugins as plugins_mod
        # Clear cache to avoid stale hits from previous test
        with plugins_mod._PREVIEW_CACHE_LOCK:
            plugins_mod._PREVIEW_CACHE.clear()

        not_newer_result = {
            "_not_newer": True,
            "from_version": "0.2.0",
            "to_version": "0.1.0",
        }
        with patch("routes.plugins._compute_preview", return_value=not_newer_result):
            resp = auth_client.get("/api/plugins/pm-essentials/update/preview?source=github:test/not-newer")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("up_to_date") is True
        assert data.get("added") == {}
        assert data.get("removed") == {}
        assert data.get("modified") == {}

    def test_cache_hit_does_not_call_compute_again(self, auth_client):
        """Second call within TTL must NOT call _compute_preview again."""
        import routes.plugins as plugins_mod

        # Clear cache first
        with plugins_mod._PREVIEW_CACHE_LOCK:
            plugins_mod._PREVIEW_CACHE.clear()

        call_count = {"n": 0}
        original = GOOD_PREVIEW.copy()

        def counting_compute(slug, source_url):
            call_count["n"] += 1
            return original

        with patch("routes.plugins._compute_preview", side_effect=counting_compute):
            auth_client.get("/api/plugins/pm-essentials/update/preview?source=github:test/x")
            auth_client.get("/api/plugins/pm-essentials/update/preview?source=github:test/x")

        assert call_count["n"] == 1, f"Expected 1 call, got {call_count['n']}"

    def test_cache_expires_after_ttl(self, auth_client):
        """After TTL expires, _compute_preview is called again."""
        import routes.plugins as plugins_mod

        with plugins_mod._PREVIEW_CACHE_LOCK:
            plugins_mod._PREVIEW_CACHE.clear()
            # Inject an expired entry
            plugins_mod._PREVIEW_CACHE[("pm-essentials", "github:test/expired")] = (
                time.monotonic() - plugins_mod._PREVIEW_CACHE_TTL - 1,
                GOOD_PREVIEW.copy(),
            )

        call_count = {"n": 0}

        def counting_compute(slug, source_url):
            call_count["n"] += 1
            return GOOD_PREVIEW.copy()

        with patch("routes.plugins._compute_preview", side_effect=counting_compute):
            resp = auth_client.get("/api/plugins/pm-essentials/update/preview?source=github:test/expired")

        assert resp.status_code == 200
        assert call_count["n"] == 1

    def test_preview_is_read_only_no_db_write(self, auth_client, tmp_db):
        """After a preview call, manifest_json in DB is unchanged (AC1.2.4)."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        before = conn.execute(
            "SELECT manifest_json FROM plugins_installed WHERE slug = ?", ("pm-essentials",)
        ).fetchone()["manifest_json"]
        conn.close()

        with patch("routes.plugins._compute_preview", return_value=GOOD_PREVIEW.copy()):
            auth_client.get("/api/plugins/pm-essentials/update/preview")

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        after = conn.execute(
            "SELECT manifest_json FROM plugins_installed WHERE slug = ?", ("pm-essentials",)
        ).fetchone()["manifest_json"]
        conn.close()

        assert before == after, "manifest_json changed after preview — AC1.2.4 violated"

    def test_preview_does_not_write_to_plugin_dir(self, auth_client, tmp_path):
        """Preview endpoint must not write files to plugins/<slug>/."""
        import routes.plugins as plugins_mod

        fake_plugin_dir = tmp_path / "pm-essentials"
        fake_plugin_dir.mkdir()
        sentinel_file = fake_plugin_dir / "manifest.yaml"
        sentinel_file.write_text("version: 0.1.0\n")
        mtime_before = sentinel_file.stat().st_mtime

        original_plugins_dir = plugins_mod.PLUGINS_DIR
        try:
            plugins_mod.PLUGINS_DIR = tmp_path
            with patch("routes.plugins._compute_preview", return_value=GOOD_PREVIEW.copy()):
                auth_client.get("/api/plugins/pm-essentials/update/preview")
        finally:
            plugins_mod.PLUGINS_DIR = original_plugins_dir

        mtime_after = sentinel_file.stat().st_mtime
        assert mtime_before == mtime_after, "Plugin dir file was modified — AC1.2.4 violated"
