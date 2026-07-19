"""routes/integrations_core_postiz.py — admin-only gate, secret masking,
"keep current on masked/blank submit", SSRF guard, test-connection.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))

XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def _isolate_env_file(app, tmp_path, monkeypatch):
    """PUT /api/integrations/core/postiz writes to WORKSPACE/.env for real —
    redirect it to a throwaway path so tests never touch this repo's actual
    .env file. Depends on `app` so it patches AFTER conftest's
    importlib.reload(routes.integrations_core_postiz) — reload re-executes
    the module's top-level `from routes._helpers import WORKSPACE`, which
    would silently undo a patch applied before it.

    Also snapshots/restores every POSTIZ_*-ish env var: the route's PUT
    handler intentionally does `os.environ[key] = value` directly (not via
    monkeypatch) so the change is picked up immediately in-process — correct
    in production, but it means a PUT test would otherwise leak a real key
    into every later test in this same pytest process.
    """
    import os
    import routes.integrations_core_postiz as postiz_routes
    monkeypatch.setattr(postiz_routes, "WORKSPACE", tmp_path)

    snapshot = {k: os.environ.get(k) for k in postiz_routes._ALLOWED_KEYS}
    yield
    for k, v in snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_viewer_forbidden(viewer_client):
    resp = viewer_client.get("/api/integrations/core/postiz", headers=XHR)
    assert resp.status_code == 403


def test_admin_get_without_xhr_header_forbidden(admin_client):
    """_require_xhr() CSRF mitigation — session-cookie auth must carry the header."""
    resp = admin_client.get("/api/integrations/core/postiz")
    assert resp.status_code == 403


def test_admin_get_returns_masked_config(admin_client, monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "sk-super-secret-value-123")
    resp = admin_client.get("/api/integrations/core/postiz", headers=XHR)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["configured"] is True
    assert data["config"]["POSTIZ_API_KEY"] != "sk-super-secret-value-123"
    assert "****" in data["config"]["POSTIZ_API_KEY"]
    assert data["config"]["POSTIZ_URL"] == "https://postiz.example.com"  # non-secret, not masked


def test_put_rejects_non_https_url(admin_client):
    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"POSTIZ_URL": "http://postiz.example.com"},
    )
    assert resp.status_code == 400


def test_put_ssrf_guard_rejects_private_host(admin_client):
    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"POSTIZ_URL": "https://10.0.0.5"},
    )
    assert resp.status_code == 400
    assert "interno" in resp.get_json()["error"] or "privado" in resp.get_json()["error"]


def test_put_ssrf_guard_allows_explicit_allowlisted_internal_host(admin_client, monkeypatch):
    monkeypatch.setenv("POSTIZ_URL_ALLOWED_INTERNAL_HOSTS", "postiz-internal")
    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"POSTIZ_URL": "https://postiz-internal"},
    )
    assert resp.status_code == 200


def test_put_sets_url_and_key(admin_client):
    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"POSTIZ_URL": "https://postiz.example.com", "POSTIZ_API_KEY": "brand-new-key"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["configured"] is True
    assert "****" in data["config"]["POSTIZ_API_KEY"]


def test_put_masked_submit_keeps_current_secret(admin_client, monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "original-secret-key")

    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"POSTIZ_API_KEY": "sk-****-abcd"},  # looks masked — must be ignored
    )
    # A submit where the only field is masked-and-ignored has nothing left
    # to update — 400 "nothing to update" is as acceptable as a 200 no-op,
    # as long as the stored secret is provably untouched either way.
    assert resp.status_code in (200, 400)
    import os
    assert os.environ["POSTIZ_API_KEY"] == "original-secret-key"


def test_put_blank_submit_never_wipes_stored_secret(admin_client, monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "original-secret-key")

    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"POSTIZ_API_KEY": ""},
    )
    assert resp.status_code in (200, 400)  # 400 if it ends up as "no valid fields"
    import os
    assert os.environ["POSTIZ_API_KEY"] == "original-secret-key"


def test_put_rejects_invalid_default_post_mode(admin_client):
    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"SOCIAL_DEFAULT_POST_MODE": "publish-immediately"},
    )
    assert resp.status_code == 400


def test_put_rejects_non_numeric_timeout(admin_client):
    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"POSTIZ_REQUEST_TIMEOUT_SECONDS": "not-a-number"},
    )
    assert resp.status_code == 400


def test_put_rejects_unknown_keys_silently_ignored(admin_client):
    """Only the explicit allowlist may ever be written — the briefing is
    explicit: 'Não permita que o endpoint altere variáveis arbitrárias.'
    """
    resp = admin_client.put(
        "/api/integrations/core/postiz", headers=XHR,
        json={"DASHBOARD_API_TOKEN": "should-never-be-settable-here"},
    )
    assert resp.status_code == 400  # nothing valid to update
    import os
    assert "DASHBOARD_API_TOKEN" not in os.environ or os.environ.get("DASHBOARD_API_TOKEN") != "should-never-be-settable-here"


def test_test_connection_requires_config(admin_client, monkeypatch):
    monkeypatch.delenv("POSTIZ_URL", raising=False)
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    resp = admin_client.post("/api/integrations/core/postiz/test", headers=XHR)
    assert resp.status_code == 400


def test_test_connection_reports_per_platform_status(admin_client, monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "key")
    with patch("routes.integrations_core_postiz.PostizClient") as MockClient:
        instance = MockClient.from_env.return_value
        instance.test_connection.return_value = {
            "ok": True, "detail": "2 integrações encontradas.",
            "platforms": {"instagram": {"connected": True, "id": "int-1"}},
        }
        resp = admin_client.post("/api/integrations/core/postiz/test", headers=XHR)
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
