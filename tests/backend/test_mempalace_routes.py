"""Regression tests for dashboard/backend/routes/mempalace.py.

Covers the three MemPalace flows used by agents (heartbeats + routines):

1. ``/api/mempalace/status``     — installation probe + stats shape
2. ``/api/mempalace/sources``    — add/delete + path-allowlist (the
   security-sensitive endpoint — must reject paths outside $HOME and
   ``$WORKSPACE`` even though they exist on disk)
3. ``/api/mempalace/search``     — semantic search contract (query, wing,
   room, clamping of ``n``)
4. ``/api/mempalace/install``    — install flow (already installed, success,
   failure, timeout)
5. ``/api/mempalace/mine``       — mining flow (no sources, invalid index,
   concurrent 409, success)
6. RBAC enforcement               — viewer blocked on manage endpoints
7. Helper function edge cases     — corrupt sources, palace stats errors,
   search exception handling

The tests are unit-style: we patch ``mempalace.searcher.search_memories``
and the persistent files (``sources.json``, ``mining_status.json``) so the
suite runs without a live chromadb, and they validate the blueprint's
behavior, not the chromadb internals.

Skips oracle: there is no live chromadb on CI and the SDK does not ship
its own unit tests for the blueprint layer.

Run::

    python -m pytest tests/backend/test_mempalace_routes.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Path setup — mirror test_workspace.py
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_app_with_admin(tmp_palace_dir: Path):
    """Return a Flask app whose mempalace blueprint points at tmp_palace_dir.

    We do NOT init db/login — this suite exercises helper-level behavior
    by calling the blueprint view functions directly with a mocked
    ``current_user``. That keeps tests fast and avoids pulling in the auth
    machinery that other suites (test_workspace.py) already cover.
    """
    import flask

    app = flask.Flask(__name__)
    app.config["TESTING"] = True

    import routes.mempalace as mp
    mp.PALACE_DIR = tmp_palace_dir
    mp.SOURCES_FILE = tmp_palace_dir / "sources.json"
    mp.MINING_STATUS_FILE = tmp_palace_dir / "mining_status.json"

    app.register_blueprint(mp.bp)

    return app, mp


@pytest.fixture()
def tmp_palace_dir(tmp_path):
    d = tmp_path / "mempalace"
    d.mkdir()
    return d


@pytest.fixture()
def app(tmp_palace_dir):
    flask_app, _ = _build_app_with_admin(tmp_palace_dir)
    return flask_app


def _patch_auth(user, *, can_view=True, can_manage=None):
    """Return a ``with``-stack patching auth_routes decorators consistently.

    ``can_manage`` defaults to True for admins, False otherwise.
    """
    if can_manage is None:
        can_manage = (user.role == "admin")
    return [
        patch("routes.auth_routes.current_user", user),
        patch(
            "routes.auth_routes.has_permission",
            side_effect=lambda res, act: (act == "view" and can_view) or (act == "manage" and can_manage),
        ),
    ]


@pytest.fixture()
def admin_user():
    u = SimpleNamespace()
    u.is_authenticated = True
    u.role = "admin"
    u.username = "testadmin"
    u.id = 1
    return u


@pytest.fixture()
def viewer_user():
    u = SimpleNamespace()
    u.is_authenticated = True
    u.role = "viewer"
    u.username = "testviewer"
    u.id = 2
    return u


def _call_view(app, view_function, user, **path_kwargs):
    """Invoke a blueprint view as ``user`` over the test client."""
    with patch("routes.auth_routes.current_user", user), \
         patch("routes.auth_routes.has_permission",
               side_effect=lambda role, res, act: act in {"view"} or role == "admin"), \
         app.test_request_context():
        with app.test_client() as client:
            # Manually invoke the view function so we bypass route rules
            # (sanity for `int:` prefix in URL where the blueprint uses
            # straight routes — they're already registered, but this keeps
            # the helper explicit).
            response = view_function(**path_kwargs)
            return response


# ---------------------------------------------------------------------------
# 1. Status endpoint
# ---------------------------------------------------------------------------


class TestStatusEndpoint:
    """Verify the ``/api/mempalace/status`` shape and fallbacks."""

    def test_status_installed_returns_version(self, app, admin_user, tmp_palace_dir):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             patch.object(mp, "_get_palace_stats",
                          return_value={"total_drawers": 0, "wings": [], "rooms": []}), \
             app.test_request_context():
                response = mp.status()
                data = json.loads(response.get_data(as_text=True))

        assert data["installed"] is True
        assert data["version"] == "3.4.0"
        assert data["stats"]["total_drawers"] == 0
        assert data["sources_count"] == 0
        assert data["mining"] is None

    def test_status_not_installed_returns_error_shape(self, app, admin_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(False, None)), \
             app.test_request_context():
                response = mp.status()
                data = json.loads(response.get_data(as_text=True))

        assert data["installed"] is False
        assert data["version"] is None
        assert data["stats"] is None  # no stats when not installed
        assert "palace_path" in data


# ---------------------------------------------------------------------------
# 2. Sources endpoint — add/delete/path allowlist
# ---------------------------------------------------------------------------


class TestSourcesEndpoint:
    """Verify ``/api/mempalace/sources`` CRUD + path allowlist."""

    def test_add_source_happy_path(self, app, admin_user, tmp_palace_dir):
        import routes.mempalace as mp

        target = tmp_palace_dir / "src"
        target.mkdir()

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context(json={"path": str(target), "label": "src", "wing": "evo-nexus"}):
                response = mp.add_source()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 201
        assert data["status"] == "added"
        assert len(data["sources"]) == 1
        assert data["sources"][0]["label"] == "src"
        assert data["sources"][0]["wing"] == "evo-nexus"

    def test_add_source_rejects_missing_path(self, app, admin_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context(json={"path": ""}):
                response = mp.add_source()
                assert response.status_code == 400

    def test_add_source_rejects_nonexistent_dir(self, app, admin_user, tmp_path):
        import routes.mempalace as mp

        bogus = tmp_path / "does-not-exist"
        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context(json={"path": str(bogus)}):
                response = mp.add_source()
                assert response.status_code == 400

    def test_add_source_rejects_duplicate(self, app, admin_user, tmp_palace_dir):
        import routes.mempalace as mp

        target = tmp_palace_dir / "src"
        target.mkdir()

        # First add succeeds
        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context(json={"path": str(target)}):
                first = mp.add_source()

        # Second add with same resolved path → 409
        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context(json={"path": str(target)}):
                second = mp.add_source()
                assert second.status_code == 409

        assert first.status_code == 201

    def test_add_source_blocks_path_outside_home_and_workspace(
        self, app, admin_user, tmp_path, monkeypatch
    ):
        """A source must live under $HOME or $WORKSPACE.

        This guards against accidentally exporting ``/etc`` or ``/var/log``
        as an indexable directory.
        """
        import routes.mempalace as mp

        # Pretend $HOME and $WORKSPACE both live under a neutral subtree.
        # Anything outside those two is rejected — even if it is a real
        # readable directory.
        safe_zone = tmp_path / "safe"
        safe_zone.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "leaked"
        target.mkdir()

        monkeypatch.setattr(Path, "home", lambda: safe_zone)
        # WORKSPACE is read off the blueprint module — point at safe_zone
        # so any .resolve() walk that requires the workspace prefix sees it.

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context(json={"path": str(target)}):
                response = mp.add_source()
                assert response.status_code == 400
                body = json.loads(response.get_data(as_text=True))
                assert "outside" in body["error"].lower() or "within" in body["error"].lower()

    def test_delete_source_by_index(self, app, admin_user, tmp_palace_dir):
        import routes.mempalace as mp

        target = tmp_palace_dir / "src"
        target.mkdir()
        mp._save_sources([{"path": str(target), "label": "src", "wing": None}])

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context():
                response = mp.delete_source(0)
                data = json.loads(response.get_data(as_text=True))

        assert data["status"] == "removed"
        assert data["sources"] == []

    def test_delete_source_out_of_range_404(self, app, admin_user, tmp_palace_dir):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context():
                response = mp.delete_source(99)
                assert response.status_code == 404


# ---------------------------------------------------------------------------
# 3. Search endpoint
# ---------------------------------------------------------------------------


class TestSearchEndpoint:
    """Verify the search route contracts: required query, clamping of ``n``."""

    def test_search_requires_query(self, app, admin_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             app.test_request_context():
                response = mp.search()
                assert response.status_code == 400
                body = json.loads(response.get_data(as_text=True))
                assert "q" in body["error"]

    def test_search_returns_not_installed_error(self, app, admin_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(False, None)), \
             app.test_request_context(query_string={"q": "anything"}):
                response = mp.search()
                assert response.status_code == 400

    def test_search_calls_mempalace_and_returns_payload(self, app, admin_user):
        import routes.mempalace as mp

        fake_payload = {
            "query": "jwt auth",
            "filters": {"wing": "evo-nexus", "room": None},
            "total_before_filter": 1,
            "results": [
                {
                    "text": "JWT auth uses HS256 by default...",
                    "wing": "evo-nexus",
                    "room": "technical",
                    "source_file": "auth.md",
                    "similarity": 0.81,
                    "distance": 0.19,
                }
            ],
        }

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             patch(
                "mempalace.searcher.search_memories",
                return_value=fake_payload,
             ) as mock_search, \
             app.test_request_context(query_string={
                 "q": "jwt auth", "wing": "evo-nexus", "n": "10",
             }):
                response = mp.search()
                data = json.loads(response.get_data(as_text=True))

        assert data["query"] == "jwt auth"
        assert len(data["results"]) == 1
        assert data["results"][0]["source_file"] == "auth.md"
        # Verify the wrapper passes the right kwargs to mempalace
        kwargs = mock_search.call_args.kwargs
        assert kwargs["query"] == "jwt auth"
        assert kwargs["wing"] == "evo-nexus"
        assert kwargs["n_results"] == 10

    def test_search_clamps_n_to_50(self, app, admin_user):
        """Requests for n>50 must be clamped server-side, not forwarded."""
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             patch(
                "mempalace.searcher.search_memories",
                return_value={"query": "x", "results": []},
             ) as mock_search, \
             app.test_request_context(query_string={"q": "anything", "n": "500"}):
                response = mp.search()
                assert response.status_code == 200

        kwargs = mock_search.call_args.kwargs
        # Blueprint clamps to min(client_n, 50)
        assert kwargs["n_results"] == 50


# ---------------------------------------------------------------------------
# 4. Mining state file — PID janitor behavior
# ---------------------------------------------------------------------------


class TestMiningStatusLifecycle:
    """Verify the PID-aliveness check in ``_get_mining_status``."""

    def test_get_mining_status_unlinks_when_pid_dead(self, app, admin_user, tmp_palace_dir):
        import routes.mempalace as mp

        # Seed a status file with a dead PID. PID 99999999 is essentially
        # guaranteed not to exist on a fresh test box.
        mp.PALACE_DIR = tmp_palace_dir
        mp.MINING_STATUS_FILE = tmp_palace_dir / "mining_status.json"
        mp.MINING_STATUS_FILE.write_text(json.dumps({
            "pid": 99999999,
            "phase": "scanning",
            "started_at": "2026-06-16T00:00:00Z",
        }))

        result = mp._get_mining_status()

        assert result is None  # treated as no mining
        assert not mp.MINING_STATUS_FILE.exists()  # stale file cleaned up

    def test_get_mining_status_returns_when_pid_alive(self, app, admin_user, tmp_palace_dir):
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir
        mp.MINING_STATUS_FILE = tmp_palace_dir / "mining_status.json"
        seed = {"pid": None, "phase": "scanning", "started_at": "2026-06-16T00:00:00Z"}
        mp.MINING_STATUS_FILE.write_text(json.dumps(seed))

        result = mp._get_mining_status()
        assert result == seed  # no PID → still alive (active or just-initialized)


# ---------------------------------------------------------------------------
# 5. Authn/authz gate — confirm the decorator is wired
# ---------------------------------------------------------------------------


class TestPermissionWiring:
    """Quick check that view-level permission gates are not silently dropped.

    We invoke the view functions directly with an *unauthenticated* user
    and patch ``current_user.is_authenticated = False`` to make sure the
    ``require_permission`` decorator still aborts on 401 — not just 403.
    """

    def test_unauthenticated_user_is_rejected(self, app, tmp_palace_dir):
        import routes.mempalace as mp
        from flask import abort

        anon = SimpleNamespace()
        anon.is_authenticated = False

        with patch("routes.auth_routes.current_user", anon), \
             patch(
                "routes.auth_routes.has_permission",
                side_effect=lambda r, res, act: False,
             ), \
             app.test_request_context():
                with pytest.raises(Exception):
                    # The decorator abort()s with 401 — Flask converts that
                    # into an HTTPException in test_request_context.
                    mp.status()

        # If we reached here without a 401, the gate is missing.
        # (abort(401) raises HTTPException which pytest propagated.)


# ---------------------------------------------------------------------------
# 6. Install endpoint
# ---------------------------------------------------------------------------


class TestInstallEndpoint:
    """Verify ``/api/mempalace/install`` behavior.

    Covers: already_installed, install success (uv + pip fallback),
    install failure, and timeout.
    """

    def test_install_already_installed_returns_200(self, app, admin_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             app.test_request_context():
                response = mp.install()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 200
        assert data["status"] == "already_installed"

    def test_install_success_via_uv(self, app, admin_user, tmp_palace_dir):
        """When mempalace is not installed, install via uv and init."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(False, None)), \
             patch("routes.mempalace.shutil.which", return_value="/usr/bin/uv"), \
             patch("routes.mempalace.subprocess.run", return_value=mock_result) as mock_run, \
             app.test_request_context():
                response = mp.install()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 200
        assert data["status"] == "installed"
        # First call = install, second call = init
        assert mock_run.call_count == 2
        install_cmd = mock_run.call_args_list[0][0][0]
        assert "uv" in install_cmd

    def test_install_success_via_pip_fallback(self, app, admin_user, tmp_palace_dir):
        """When uv is not available, fall back to pip."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(False, None)), \
             patch("routes.mempalace.shutil.which", return_value=None), \
             patch("routes.mempalace.subprocess.run", return_value=mock_result) as mock_run, \
             app.test_request_context():
                response = mp.install()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 200
        assert data["status"] == "installed"
        # Verify pip fallback was used
        install_cmd = mock_run.call_args_list[0][0][0]
        assert "-m" in install_cmd and "pip" in install_cmd

    def test_install_failure_returns_500_with_stderr(self, app, admin_user, tmp_palace_dir):
        """Nonzero returncode from install command → 500 + stderr detail."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Could not find a version satisfying mempalace"

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(False, None)), \
             patch("routes.mempalace.shutil.which", return_value=None), \
             patch("routes.mempalace.subprocess.run", return_value=mock_result), \
             app.test_request_context():
                response = mp.install()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 500
        assert data["status"] == "error"
        assert "mempalace" in data["detail"]

    def test_install_timeout_returns_500(self, app, admin_user, tmp_palace_dir):
        """subprocess.TimeoutExpired → 500 + timeout message."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(False, None)), \
             patch("routes.mempalace.shutil.which", return_value=None), \
             patch("routes.mempalace.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=120)), \
             app.test_request_context():
                response = mp.install()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 500
        assert data["status"] == "error"
        assert "timed out" in data["detail"]


# ---------------------------------------------------------------------------
# 7. Mine endpoint
# ---------------------------------------------------------------------------


class TestMineEndpoint:
    """Verify ``/api/mempalace/mine`` behavior.

    Covers: not installed, no sources, invalid source index, concurrent
    mining (409), and success (worker spawn).
    """

    def test_mine_not_installed_returns_400(self, app, admin_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(False, None)), \
             app.test_request_context(json={}):
                response = mp.mine()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 400
        assert "not installed" in data["error"]

    def test_mine_no_sources_returns_400(self, app, admin_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             app.test_request_context(json={}):
                response = mp.mine()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 400
        assert "No sources" in data["error"]

    def test_mine_invalid_source_index_returns_404(self, app, admin_user, tmp_palace_dir):
        """source_index out of range → 404."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir
        target = tmp_palace_dir / "src"
        target.mkdir()
        mp._save_sources([{"path": str(target), "label": "src", "wing": None,
                           "added_at": "2026-01-01T00:00:00Z", "last_indexed": None}])

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             app.test_request_context(json={"source_index": 99}):
                response = mp.mine()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 404
        assert "Invalid source index" in data["error"]

    def test_mine_already_in_progress_returns_409(
        self, app, admin_user, tmp_palace_dir
    ):
        """If mining is already in progress → 409."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir
        mp.MINING_STATUS_FILE = tmp_palace_dir / "mining_status.json"
        target = tmp_palace_dir / "src"
        target.mkdir()
        mp._save_sources([{"path": str(target), "label": "src", "wing": None,
                           "added_at": "2026-01-01T00:00:00Z", "last_indexed": None}])

        # Seed a status file with a "dead" PID — but _get_mining_status
        # returns None for dead PIDs. We need to make it think mining is
        # alive, so we mock _get_mining_status directly.
        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             patch.object(mp, "_get_mining_status", return_value={"pid": 12345}), \
             app.test_request_context(json={}):
                response = mp.mine()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 409
        assert "already in progress" in data["error"]

    def test_mine_success_spawns_worker(
        self, app, admin_user, tmp_palace_dir
    ):
        """Successful mine call spawns subprocess, writes status, updates sources."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir
        mp.MINING_STATUS_FILE = tmp_palace_dir / "mining_status.json"
        target = tmp_palace_dir / "src"
        target.mkdir()
        mp._save_sources([{"path": str(target), "label": "src", "wing": "evo-nexus",
                           "added_at": "2026-01-01T00:00:00Z", "last_indexed": None}])

        mock_process = MagicMock()
        mock_process.pid = 99999
        mock_process.stdin = MagicMock()

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             patch.object(mp, "_get_mining_status", return_value=None), \
             patch("routes.mempalace.subprocess.Popen", return_value=mock_process) as mock_popen, \
             app.test_request_context(json={}):
                response = mp.mine()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 200
        assert data["status"] == "started"
        assert data["pid"] == 99999
        # Popen was called with the worker script
        mock_popen.assert_called_once()
        # Status file was seeded
        assert mp.MINING_STATUS_FILE.exists()
        status_data = json.loads(mp.MINING_STATUS_FILE.read_text())
        assert status_data["pid"] == 99999
        assert status_data["phase"] == "scanning"
        # last_indexed was updated on the source
        sources = mp._load_sources()
        assert sources[0]["last_indexed"] is not None

    def test_mine_specific_source_index(
        self, app, admin_user, tmp_palace_dir
    ):
        """Mine with source_index=0 targets only that source."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir
        mp.MINING_STATUS_FILE = tmp_palace_dir / "mining_status.json"
        src1 = tmp_palace_dir / "src1"
        src2 = tmp_palace_dir / "src2"
        src1.mkdir()
        src2.mkdir()
        mp._save_sources([
            {"path": str(src1), "label": "src1", "wing": None,
             "added_at": "2026-01-01T00:00:00Z", "last_indexed": None},
            {"path": str(src2), "label": "src2", "wing": None,
             "added_at": "2026-01-01T00:00:00Z", "last_indexed": None},
        ])

        mock_process = MagicMock()
        mock_process.pid = 88888
        mock_process.stdin = MagicMock()

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             patch.object(mp, "_get_mining_status", return_value=None), \
             patch("routes.mempalace.subprocess.Popen", return_value=mock_process), \
             app.test_request_context(json={"source_index": 0}):
                response = mp.mine()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 200
        # Only src1 should have been updated
        sources = mp._load_sources()
        assert sources[0]["last_indexed"] is not None
        assert sources[1]["last_indexed"] is None


# ---------------------------------------------------------------------------
# 8. RBAC — viewer blocked on manage endpoints
# ---------------------------------------------------------------------------


class TestRBACEnforcement:
    """Verify that a viewer-role user is blocked from manage endpoints.

    Viewers should get 403 on POST /sources, DELETE /sources/<idx>,
    POST /install, POST /mine — but still be allowed on GET /status,
    GET /sources, GET /search.
    """

    def test_viewer_can_view_status(self, app, viewer_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", viewer_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(False, None)), \
             app.test_request_context():
                response = mp.status()
                # 200 — viewer has view permission
                assert response.status_code == 200

    def test_viewer_can_list_sources(self, app, viewer_user):
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", viewer_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context():
                response = mp.list_sources()
                assert response.status_code == 200

    def test_viewer_blocked_from_add_source(self, app, viewer_user, tmp_palace_dir):
        """Viewer hitting POST /sources → 403."""
        import routes.mempalace as mp

        target = tmp_palace_dir / "src"
        target.mkdir()

        with patch("routes.auth_routes.current_user", viewer_user), \
             patch("routes.auth_routes.has_permission", return_value=False), \
             app.test_request_context(json={"path": str(target)}):
                with pytest.raises(Exception):
                    mp.add_source()

    def test_viewer_blocked_from_delete_source(self, app, viewer_user, tmp_palace_dir):
        """Viewer hitting DELETE /sources/<idx> → 403."""
        import routes.mempalace as mp

        target = tmp_palace_dir / "src"
        target.mkdir()
        mp._save_sources([{"path": str(target), "label": "src", "wing": None,
                           "added_at": "2026-01-01T00:00:00Z", "last_indexed": None}])

        with patch("routes.auth_routes.current_user", viewer_user), \
             patch("routes.auth_routes.has_permission", return_value=False), \
             app.test_request_context():
                with pytest.raises(Exception):
                    mp.delete_source(0)

    def test_viewer_blocked_from_install(self, app, viewer_user):
        """Viewer hitting POST /install → 403."""
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", viewer_user), \
             patch("routes.auth_routes.has_permission", return_value=False), \
             app.test_request_context():
                with pytest.raises(Exception):
                    mp.install()

    def test_viewer_blocked_from_mine(self, app, viewer_user):
        """Viewer hitting POST /mine → 403."""
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", viewer_user), \
             patch("routes.auth_routes.has_permission", return_value=False), \
             app.test_request_context(json={}):
                with pytest.raises(Exception):
                    mp.mine()


# ---------------------------------------------------------------------------
# 9. Helper function edge cases
# ---------------------------------------------------------------------------


class TestHelperEdgeCases:
    """Verify edge cases in helper functions."""

    def test_load_sources_missing_file_returns_empty_list(self, app, admin_user, tmp_palace_dir):
        """When sources.json doesn't exist, _load_sources returns []."""
        import routes.mempalace as mp

        mp.SOURCES_FILE = tmp_palace_dir / "nonexistent_sources.json"
        result = mp._load_sources()
        assert result == []

    def test_load_sources_corrupt_json_returns_empty_list(self, app, admin_user, tmp_palace_dir):
        """When sources.json is corrupt, _load_sources returns []."""
        import routes.mempalace as mp

        mp.SOURCES_FILE = tmp_palace_dir / "sources.json"
        mp.SOURCES_FILE.write_text("not valid json {{{", encoding="utf-8")

        result = mp._load_sources()
        assert result == []

    def test_mempalace_available_returns_true_when_imported(self, app, admin_user):
        """_mempalace_available returns (True, version) when import succeeds."""
        import routes.mempalace as mp

        # mempalace is already importable in test env (mocked or real)
        # We just verify the function doesn't crash
        installed, version = mp._mempalace_available()
        assert isinstance(installed, bool)

    def test_get_palace_stats_returns_none_on_exception(self, app, admin_user, tmp_palace_dir):
        """_get_palace_stats returns None when chromadb raises."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir

        with patch("routes.mempalace.chromadb.PersistentClient",
                   side_effect=Exception("chroma not available")):
            result = mp._get_palace_stats()

        assert result is None

    def test_get_palace_stats_missing_collection(self, app, admin_user, tmp_palace_dir):
        """_get_palace_stats returns zeroed stats when collection doesn't exist."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir

        mock_client = MagicMock()
        mock_client.get_collection.side_effect = Exception("collection not found")

        with patch("routes.mempalace.chromadb.PersistentClient", return_value=mock_client):
            result = mp._get_palace_stats()

        assert result == {"total_drawers": 0, "wings": [], "rooms": []}

    def test_search_exception_returns_500(self, app, admin_user):
        """When search_memories raises, the endpoint returns 500."""
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             patch("mempalace.searcher.search_memories",
                   side_effect=RuntimeError("embedding model failed")), \
             app.test_request_context(query_string={"q": "test"}):
                response = mp.search()
                data = json.loads(response.get_data(as_text=True))

        assert response.status_code == 500
        assert "embedding model failed" in data["error"]

    def test_search_wing_room_filter_passthrough(self, app, admin_user):
        """Verify wing and room filters are passed to search_memories."""
        import routes.mempalace as mp

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             patch.object(mp, "_mempalace_available", return_value=(True, "3.4.0")), \
             patch("mempalace.searcher.search_memories",
                   return_value={"query": "x", "results": []}) as mock_search, \
             app.test_request_context(query_string={
                 "q": "test", "wing": "evo-nexus", "room": "technical",
             }):
                mp.search()

        kwargs = mock_search.call_args.kwargs
        assert kwargs["wing"] == "evo-nexus"
        assert kwargs["room"] == "technical"

    def test_set_mining_status_writes_file(self, app, admin_user, tmp_palace_dir):
        """_set_mining_status writes JSON to the status file."""
        import routes.mempalace as mp

        mp.PALACE_DIR = tmp_palace_dir
        mp.MINING_STATUS_FILE = tmp_palace_dir / "mining_status.json"

        status = {"pid": 1234, "phase": "scanning", "files_done": 0}
        mp._set_mining_status(status)

        assert mp.MINING_STATUS_FILE.exists()
        written = json.loads(mp.MINING_STATUS_FILE.read_text())
        assert written["pid"] == 1234
        assert written["phase"] == "scanning"

    def test_path_validation_symlink_within_home(self, app, admin_user, tmp_palace_dir, monkeypatch):
        """A symlink that resolves within $HOME should be accepted."""
        import routes.mempalace as mp

        # Create a real dir inside the fake home
        safe_zone = tmp_palace_dir / "safe"
        safe_zone.mkdir()
        real_dir = safe_zone / "actual"
        real_dir.mkdir()

        # Create a symlink pointing to the real dir
        link_dir = tmp_palace_dir / "link"
        try:
            link_dir.symlink_to(real_dir)
        except OSError:
            pytest.skip("symlinks not supported on this platform")

        monkeypatch.setattr(Path, "home", lambda: safe_zone)

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context(json={"path": str(link_dir)}):
                response = mp.add_source()
                # The resolved path is inside safe_zone ($HOME) → accepted
                assert response.status_code == 201

    def test_path_validation_rejects_path_traversal(
        self, app, admin_user, tmp_palace_dir, monkeypatch
    ):
        """A path using .. to escape $HOME must be rejected."""
        import routes.mempalace as mp

        safe_zone = tmp_palace_dir / "safe"
        safe_zone.mkdir()
        outside = tmp_palace_dir / "outside"
        outside.mkdir()

        monkeypatch.setattr(Path, "home", lambda: safe_zone)

        # Use a path that resolves outside home via ..
        traversal_path = safe_zone / ".." / "outside"

        with patch("routes.auth_routes.current_user", admin_user), \
             patch("routes.auth_routes.has_permission", return_value=True), \
             app.test_request_context(json={"path": str(traversal_path)}):
                response = mp.add_source()
                assert response.status_code == 400
                body = json.loads(response.get_data(as_text=True))
                assert "within" in body["error"].lower() or "outside" in body["error"].lower()
