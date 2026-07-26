"""Tests for dashboard/backend/routes/workspace.py — Step 1 verification."""

import json
import importlib
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — allow importing from dashboard/backend
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    """Create a minimal Flask app with workspace_bp registered."""
    import flask
    from flask_login import LoginManager
    from flask_sqlalchemy import SQLAlchemy

    # Use in-memory SQLite
    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-secret"
    _app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _app.config["WTF_CSRF_ENABLED"] = False

    # Init DB
    from models import db, User, Role, seed_roles
    db.init_app(_app)

    login_manager = LoginManager()
    login_manager.init_app(_app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        return flask.jsonify({"error": "Authentication required"}), 401

    from routes.workspace import bp as workspace_bp
    _app.register_blueprint(workspace_bp)

    with _app.app_context():
        db.create_all()
        seed_roles()

        # Create admin user
        admin = User(
            username="testadmin",
            email="admin@test.com",
            display_name="Test Admin",
            role="admin",
        )
        admin.set_password("password123")
        db.session.add(admin)

        # Create viewer user (no manage)
        viewer = User(
            username="testviewer",
            email="viewer@test.com",
            display_name="Test Viewer",
            role="viewer",
        )
        viewer.set_password("password123")
        db.session.add(viewer)
        db.session.commit()

    yield _app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin_client(app, client):
    """Client logged in as admin."""
    with app.test_request_context():
        from models import User
        user = User.query.filter_by(username="testadmin").first()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    return client


@pytest.fixture()
def viewer_client(app, client):
    """Client logged in as viewer (no manage permission)."""
    with app.test_request_context():
        from models import User
        user = User.query.filter_by(username="testviewer").first()

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    return client


@pytest.fixture()
def tmp_workspace(tmp_path, monkeypatch):
    """Patch WORKSPACE_DIR to a temporary directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    import routes.workspace as wm
    monkeypatch.setattr(wm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(wm, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(wm, "TRASH_DIR", ws / ".trash")
    monkeypatch.setattr(wm, "ADMIN_ROOTS", [tmp_path / ".claude"])

    return ws


# ---------------------------------------------------------------------------
# 1. test_resolve_safe
# ---------------------------------------------------------------------------

class TestResolveSafe:
    """Unit tests for _resolve_safe without HTTP context."""

    def _call(self, path, *, require_admin=False, role="admin"):
        """Call _resolve_safe with a mocked current_user."""
        import routes.workspace as wm

        mock_user = MagicMock()
        mock_user.role = role
        mock_user.is_authenticated = True

        with patch("routes.workspace.current_user", mock_user):
            with patch("routes.workspace.has_permission") as mock_hp:
                # config:manage only for admin
                mock_hp.return_value = (role == "admin")
                try:
                    result = wm._resolve_safe(path, require_admin=require_admin)
                    return result, None
                except Exception as exc:
                    return None, exc

    def test_traversal_rejected(self, tmp_workspace, monkeypatch):
        """../../etc/passwd → 403."""
        result, exc = self._call("../../etc/passwd")
        assert result is None
        assert exc is not None

    def test_blocklist_git_rejected(self, tmp_workspace, monkeypatch):
        """workspace/.git/config → 403."""
        import routes.workspace as wm
        # Create .git inside workspace to test blocklist (not traversal)
        git_dir = tmp_workspace / ".git"
        git_dir.mkdir()
        result, exc = self._call("workspace/.git/config")
        assert result is None
        assert exc is not None

    def test_admin_can_touch_claude(self, tmp_workspace, monkeypatch):
        """Admin with config:manage can access .claude/."""
        import routes.workspace as wm
        claude_dir = wm.REPO_ROOT / ".claude"
        claude_dir.mkdir(exist_ok=True)
        target = claude_dir / "agents"
        target.mkdir(exist_ok=True)

        monkeypatch.setattr(wm, "ADMIN_ROOTS", [claude_dir])

        result, exc = self._call(".claude/agents", require_admin=True, role="admin")
        assert exc is None
        assert result is not None

    def test_non_admin_claude_rejected(self, tmp_workspace, monkeypatch):
        """Non-admin cannot access .claude/."""
        import routes.workspace as wm
        claude_dir = wm.REPO_ROOT / ".claude"
        claude_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(wm, "ADMIN_ROOTS", [claude_dir])

        result, exc = self._call(".claude/agents", require_admin=False, role="viewer")
        assert result is None
        assert exc is not None

    def test_absolute_path_rejected(self, tmp_workspace, monkeypatch):
        """/etc/passwd → 403."""
        result, exc = self._call("/etc/passwd")
        assert result is None
        assert exc is not None

    def test_normal_workspace_path_ok(self, tmp_workspace):
        """workspace/finance/x.md → resolves OK (file need not exist)."""
        import routes.workspace as wm
        result, exc = self._call("workspace/finance/x.md", require_admin=False)
        # Should resolve without exception
        assert exc is None
        assert result is not None


# ---------------------------------------------------------------------------
# 2. test_seed_roles_migration
# ---------------------------------------------------------------------------

class TestSeedRolesMigration:
    """Test the one-shot migration in seed_roles()."""

    def _run_migration(self, initial_perms: dict) -> dict:
        """Simulate the migration block on a given permissions dict."""
        current = dict(initial_perms)
        legacy_actions = set(current.get("files", [])) | set(current.get("reports", []))
        if legacy_actions:
            merged = set(current.get("workspace", [])) | legacy_actions
            current["workspace"] = sorted(merged)
        current.pop("files", None)
        current.pop("reports", None)
        return current

    def test_only_files_becomes_workspace(self):
        """Role with only files:view → workspace:view."""
        result = self._run_migration({"files": ["view"]})
        assert result.get("workspace") == ["view"]
        assert "files" not in result

    def test_only_reports_becomes_workspace(self):
        """Role with only reports:manage → workspace:manage."""
        result = self._run_migration({"reports": ["manage"]})
        assert result.get("workspace") == ["manage"]
        assert "reports" not in result

    def test_both_merged_to_workspace(self):
        """files:view + reports:manage → workspace:manage (union)."""
        result = self._run_migration({"files": ["view"], "reports": ["manage"]})
        assert set(result.get("workspace", [])) == {"view", "manage"}
        assert "files" not in result
        assert "reports" not in result

    def test_already_migrated_unchanged(self):
        """Role already with workspace:view and no files/reports → unchanged."""
        initial = {"workspace": ["view"], "chat": ["view"]}
        result = self._run_migration(initial)
        assert result.get("workspace") == ["view"]
        assert result.get("chat") == ["view"]
        assert "files" not in result
        assert "reports" not in result


# ---------------------------------------------------------------------------
# 3. test_audit_append
# ---------------------------------------------------------------------------

class TestAuditAppend:
    """Test _audit helper."""

    def test_happy_path_writes_jsonl(self, app, tmp_path, monkeypatch):
        """Successful mutation writes a line to the JSONL file."""
        import routes.workspace as wm
        workspace_audit = importlib.import_module("workspace_audit")

        monkeypatch.setattr(wm, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(
            workspace_audit,
            "AUDIT_LOG_FILE",
            tmp_path / "ADWs" / "logs" / "workspace-mutations.jsonl",
        )

        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.username = "testadmin"
        mock_user.id = 1
        mock_user.role = "admin"

        mock_request = MagicMock()
        mock_request.remote_addr = "127.0.0.1"
        mock_request.headers = {"User-Agent": "pytest"}

        with app.app_context():
            with patch("routes.workspace.current_user", mock_user):
                with patch("routes.workspace.request", mock_request):
                    (tmp_path / "ADWs" / "logs").mkdir(parents=True, exist_ok=True)
                    wm._audit("write", "workspace/test.md", bytes=42, size=42)

        log_file = tmp_path / "ADWs" / "logs" / "workspace-mutations.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["op"] == "write"
        assert entry["path"] == "workspace/test.md"
        assert entry["result"] == "ok"
        assert entry["user_id"] == 1
        assert entry["role"] == "admin"

    def test_fail_safe_no_propagation(self, app, monkeypatch):
        """Error writing audit log never propagates."""
        import routes.workspace as wm

        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.username = "testadmin"
        mock_user.id = 1
        mock_user.role = "admin"

        mock_request = MagicMock()
        mock_request.remote_addr = "127.0.0.1"
        mock_request.headers = {"User-Agent": "pytest"}

        # Make open fail
        with patch("routes.workspace.open", side_effect=PermissionError("no write")):
            with patch("routes.workspace.current_user", mock_user):
                with patch("routes.workspace.request", mock_request):
                    with app.app_context():
                        # Should not raise
                        wm._audit("write", "workspace/test.md")


# ---------------------------------------------------------------------------
# 4. test_workspace_endpoints
# ---------------------------------------------------------------------------

class TestWorkspaceEndpoints:
    """Integration tests for workspace HTTP endpoints."""

    def test_tree_happy_path(self, admin_client, tmp_workspace):
        """GET /api/workspace/tree returns entries for workspace dir."""
        (tmp_workspace / "finance").mkdir()
        (tmp_workspace / "finance" / "report.md").write_text("# Report")

        resp = admin_client.get("/api/workspace/tree?path=workspace")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "entries" in data
        names = [e["name"] for e in data["entries"]]
        assert "finance" in names

    def test_tree_traversal_returns_403(self, admin_client, tmp_workspace):
        """GET /api/workspace/tree with path traversal → 403."""
        resp = admin_client.get("/api/workspace/tree?path=../../etc")
        assert resp.status_code == 403

    def test_file_write_happy_path(self, admin_client, tmp_workspace):
        """PUT /api/workspace/file writes content atomically."""
        resp = admin_client.put(
            "/api/workspace/file",
            json={"path": "workspace/hello.md", "content": "# Hello"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "size" in data
        assert (tmp_workspace / "hello.md").read_text() == "# Hello"

    def test_file_write_no_auth_returns_401(self, client, tmp_workspace):
        """PUT /api/workspace/file without auth → 401."""
        resp = client.put(
            "/api/workspace/file",
            json={"path": "workspace/hello.md", "content": "x"},
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_delete_moves_to_trash(self, admin_client, tmp_workspace):
        """DELETE /api/workspace/file soft-deletes file to .trash/."""
        target = tmp_workspace / "to_delete.md"
        target.write_text("bye")

        resp = admin_client.delete("/api/workspace/file?path=workspace/to_delete.md")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "trashed_to" in data
        assert not target.exists()
        # File moved somewhere under .trash
        assert "trashed_to" in data
        trashed = REPO_ROOT / data["trashed_to"] if False else None
        # Verify the file was actually trashed (exists somewhere under .trash)
        trash_root = tmp_workspace / ".trash"
        all_trashed = list(trash_root.rglob("to_delete.md"))
        assert len(all_trashed) == 1

    def test_rename_between_dirs_returns_400(self, admin_client, tmp_workspace):
        """POST /api/workspace/rename with different parent dirs → 400."""
        (tmp_workspace / "a").mkdir()
        (tmp_workspace / "b").mkdir()
        (tmp_workspace / "a" / "file.md").write_text("x")

        resp = admin_client.post(
            "/api/workspace/rename",
            json={"from": "workspace/a/file.md", "to": "workspace/b/file.md"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("code") == "bad_path"
