"""postiz_client.py — HTTP mocked (never touches a real Postiz instance),
payload builders, URL safety, secret redaction, idempotent-friendly design.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))

import pytest
from postiz_client import (
    PostizClient, PostizAPIError, PostizConfigError,
    build_instagram_payload, build_youtube_payload, build_linkedin_payload, build_tiktok_payload,
    build_platform_settings,
)


class _Resp:
    def __init__(self, body, status_code=200, text=None):
        self._body = body
        self.status_code = status_code
        self.text = text if text is not None else str(body)

    def json(self):
        return self._body


@pytest.fixture
def client():
    return PostizClient(
        base_url="https://postiz.example.com", api_key="secret-key-123",
        allowed_media_hosts=frozenset({"cdn.example.com"}),
        integration_ids={"instagram": "int-ig-1"},
    )


# ── construction / config ────────────────────────────────────────────────

def test_requires_https():
    with pytest.raises(PostizConfigError):
        PostizClient(base_url="http://postiz.example.com", api_key="x")


def test_requires_url_and_key():
    with pytest.raises(PostizConfigError):
        PostizClient(base_url="", api_key="x")
    with pytest.raises(PostizConfigError):
        PostizClient(base_url="https://postiz.example.com", api_key="")


def test_from_env_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("POSTIZ_URL", raising=False)
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    assert PostizClient.from_env() is None


def test_from_env_builds_client(monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "key123")
    monkeypatch.setenv("POSTIZ_INTEGRATION_TIKTOK_ID", "int-tt-1")
    c = PostizClient.from_env()
    assert c is not None
    assert c.base_url == "https://postiz.example.com"
    assert c.integration_ids["tiktok"] == "int-tt-1"


def test_from_env_honors_legacy_timeout_var(monkeypatch):
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "key123")
    monkeypatch.delenv("POSTIZ_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("POSTIZ_HTTP_TIMEOUT_SECONDS", "45")
    c = PostizClient.from_env()
    assert c.request_timeout == 45.0


# ── media URL safety ─────────────────────────────────────────────────────

def test_is_safe_media_url_accepts_allowlisted_https(client):
    assert client.is_safe_media_url("https://cdn.example.com/x.jpg")
    assert client.is_safe_media_url("https://sub.cdn.example.com/x.jpg")


@pytest.mark.parametrize("url", [
    "http://cdn.example.com/x.jpg",         # not https
    "https://evil.example/x.jpg",           # not allowlisted
    "https://user:pass@cdn.example.com/x",  # userinfo present
    "not-a-url",
])
def test_is_safe_media_url_rejects(client, url):
    assert not client.is_safe_media_url(url)


# ── secret redaction ──────────────────────────────────────────────────────

def test_redact_strips_api_key_from_error_text(client):
    msg = client._redact(f"failed with key secret-key-123 in body")
    assert "secret-key-123" not in msg
    assert "REDACTED" in msg


def test_network_error_never_leaks_api_key(client):
    with patch("postiz_client.requests.request", side_effect=Exception("boom secret-key-123")):
        with pytest.raises(PostizAPIError) as exc_info:
            client.list_integrations()
    assert "secret-key-123" not in str(exc_info.value)


# ── upload ────────────────────────────────────────────────────────────────

def test_upload_file_streams_and_parses_response(client, tmp_path):
    f = tmp_path / "final.mp4"
    f.write_bytes(b"fake-mp4-bytes")
    with patch("postiz_client.requests.request", return_value=_Resp(
        {"id": "media-1", "path": "https://uploads.postiz.com/final.mp4", "name": "final.mp4"}
    )) as mock_request:
        result = client.upload_file(f)
    assert result == {"id": "media-1", "path": "https://uploads.postiz.com/final.mp4", "name": "final.mp4"}
    call = mock_request.call_args
    assert call.args[0] == "POST"
    assert call.args[1].endswith("/public/v1/upload")
    assert "files" in call.kwargs
    assert "Authorization" not in str(call.kwargs.get("files"))  # key never embedded in the multipart body


def test_upload_file_missing_file_raises(client, tmp_path):
    with pytest.raises(Exception):
        client.upload_file(tmp_path / "does-not-exist.mp4")


def test_upload_file_rejects_response_without_id_or_path(client, tmp_path):
    f = tmp_path / "final.mp4"
    f.write_bytes(b"x")
    with patch("postiz_client.requests.request", return_value=_Resp({"name": "final.mp4"})):
        with pytest.raises(PostizAPIError):
            client.upload_file(f)


# ── HTTP error codes ──────────────────────────────────────────────────────

@pytest.mark.parametrize("status_code", [401, 413, 429, 500])
def test_request_raises_on_http_error(client, status_code):
    with patch("postiz_client.requests.request", return_value=_Resp({"error": "x"}, status_code=status_code)):
        with pytest.raises(PostizAPIError) as exc_info:
            client.list_integrations()
    assert exc_info.value.status_code == status_code


def test_request_raises_on_invalid_json_response(client):
    resp = _Resp(None, status_code=200)
    resp.json = lambda: (_ for _ in ()).throw(ValueError("bad json"))
    with patch("postiz_client.requests.request", return_value=resp):
        with pytest.raises(PostizAPIError):
            client.list_integrations()


# ── post creation ─────────────────────────────────────────────────────────

def test_create_draft_uses_type_draft(client):
    with patch("postiz_client.requests.request", return_value=_Resp(
        [{"postId": "post-1"}]
    )) as mock_request:
        client.create_draft(
            integration_id="int-1", content="Legenda", media=[],
            settings={"__type": "instagram", "post_type": "post"}, now_iso_utc="2026-07-19T00:00:00.000Z",
        )
    sent = mock_request.call_args.kwargs["json"]
    assert sent["type"] == "draft"
    assert "creationMethod" not in sent  # only the legacy create_post_now path sets this


def test_create_post_now_preserves_legacy_creation_method_field(client):
    with patch("postiz_client.requests.request", return_value=_Resp([{"postId": "post-1"}])) as mock_request:
        client.create_post_now(
            integration_id="int-1", content="x", media=[], settings={"__type": "linkedin"},
            now_iso_utc="2026-07-19T00:00:00.000Z",
        )
    sent = mock_request.call_args.kwargs["json"]
    assert sent["type"] == "now"
    assert sent["creationMethod"] == "API"


def test_schedule_post_uses_type_schedule_and_future_date(client):
    with patch("postiz_client.requests.request", return_value=_Resp([{"postId": "post-1"}])) as mock_request:
        client.schedule_post(
            integration_id="int-1", content="x", media=[], settings={"__type": "youtube"},
            scheduled_at_utc="2026-08-01T12:00:00.000Z",
        )
    sent = mock_request.call_args.kwargs["json"]
    assert sent["type"] == "schedule"
    assert sent["date"] == "2026-08-01T12:00:00.000Z"


def test_create_post_raw_rejects_invalid_post_type(client):
    with pytest.raises(ValueError):
        client._create_post_raw(
            integration_id="i", content="x", media=[], settings={}, post_type="bogus", date_iso_utc="now",
        )


def test_change_status_valid_values(client):
    with patch("postiz_client.requests.request", return_value=_Resp({"id": "post-1", "state": "QUEUE"})) as mock_request:
        client.change_status("post-1", "schedule")
    assert mock_request.call_args.args[0] == "PUT"
    assert mock_request.call_args.args[1].endswith("/public/v1/posts/post-1/status")


def test_change_status_rejects_invalid_value(client):
    with pytest.raises(ValueError):
        client.change_status("post-1", "published")


# ── integration selection ────────────────────────────────────────────────

def test_select_integration_uses_configured_id(client):
    integrations = [
        {"id": "int-a", "identifier": "instagram", "disabled": False},
        {"id": "int-ig-1", "identifier": "instagram", "disabled": False},
    ]
    match = client.select_integration("instagram", integrations)
    assert match["id"] == "int-ig-1"


def test_select_integration_ambiguous_without_configured_id():
    c = PostizClient(base_url="https://postiz.example.com", api_key="k")
    integrations = [
        {"id": "int-a", "identifier": "linkedin", "disabled": False},
        {"id": "int-b", "identifier": "linkedin", "disabled": False},
    ]
    assert c.select_integration("linkedin", integrations) is None


def test_select_integration_ignores_disabled(client):
    integrations = [{"id": "int-ig-1", "identifier": "instagram", "disabled": True}]
    assert client.select_integration("instagram", integrations) is None


# ── polling / publication confirmation (fail-closed) ─────────────────────

def test_wait_for_publication_fails_closed_on_timeout(client):
    with patch("postiz_client.requests.request", return_value=_Resp({"posts": [{"id": "post-1", "state": "QUEUE"}]})):
        result = client.wait_for_publication(
            ["post-1"], wait_seconds=0.2, poll_seconds=0.05, window=("start", "end"),
        )
    assert result["published"] is False


def test_wait_for_publication_fails_closed_on_error_state(client):
    with patch("postiz_client.requests.request", return_value=_Resp({"posts": [{"id": "post-1", "state": "ERROR"}]})):
        result = client.wait_for_publication(
            ["post-1"], wait_seconds=1, poll_seconds=0.05, window=("start", "end"),
        )
    assert result["published"] is False
    assert "ERROR" in result["detail"]


def test_wait_for_publication_succeeds_only_on_confirmed_published(client):
    with patch("postiz_client.requests.request", return_value=_Resp({"posts": [{"id": "post-1", "state": "PUBLISHED"}]})):
        result = client.wait_for_publication(
            ["post-1"], wait_seconds=1, poll_seconds=0.05, window=("start", "end"),
        )
    assert result["published"] is True


# ── platform payload builders (schema confirmed against docs.postiz.com) ──

def test_build_instagram_payload_defaults():
    assert build_instagram_payload() == {"__type": "instagram", "post_type": "post"}


def test_build_instagram_payload_rejects_invalid_post_type():
    with pytest.raises(ValueError):
        build_instagram_payload(post_type="reel")  # not a real Postiz post_type value


def test_build_youtube_payload_requires_valid_title_length():
    with pytest.raises(ValueError):
        build_youtube_payload(title="x")  # too short (min 2 chars)


def test_build_youtube_payload_rejects_invalid_visibility():
    with pytest.raises(ValueError):
        build_youtube_payload(title="Meu vídeo", visibility="hidden")


def test_build_linkedin_payload_page_vs_personal():
    assert build_linkedin_payload(page=False)["__type"] == "linkedin"
    assert build_linkedin_payload(page=True)["__type"] == "linkedin-page"


def test_build_tiktok_payload_rejects_invalid_privacy_level():
    with pytest.raises(ValueError):
        build_tiktok_payload(privacy_level="EVERYONE")


def test_build_platform_settings_dispatch_and_unknown_platform():
    assert build_platform_settings("linkedin")["__type"] == "linkedin"
    with pytest.raises(ValueError):
        build_platform_settings("facebook")
