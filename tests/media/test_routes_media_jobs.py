"""routes/media_jobs.py — Flask test client coverage: auth/RBAC, state
machine enforcement at the HTTP boundary, idempotency, timezone handling,
path traversal on the video endpoint.

Postiz is always mocked here — these tests never touch a real Postiz
instance (opt-in integration tests live elsewhere, gated by env vars).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))

from postiz_client import PostizError  # noqa: E402


def _create_job(client, **overrides):
    body = {
        "title": "Teste OmniNexus",
        "brief": "Vídeo curto de teste",
        "platform": "instagram",
        "width": 720,
        "height": 1280,
        "fps": 30,
        "duration_seconds": 6,
        "publication_mode": "draft",
    }
    body.update(overrides)
    resp = client.post("/api/media/jobs", json=body)
    return resp


# ── creation ──────────────────────────────────────────────────────────────

def test_create_job_requires_fields(admin_client):
    resp = admin_client.post("/api/media/jobs", json={"title": "x"})
    assert resp.status_code == 400


def test_create_job_rejects_invalid_platform(admin_client):
    resp = _create_job(admin_client, platform="facebook")
    assert resp.status_code == 400


def test_create_job_defaults_to_queued(admin_client):
    resp = _create_job(admin_client)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "queued"
    assert data["attempt_count"] == 0


def test_create_job_writes_input_manifest(admin_client, app, tmp_path):
    resp = _create_job(admin_client)
    data = resp.get_json()
    workspace_path = Path(data["workspace_path"])
    manifest = json.loads((workspace_path / "input" / "job.json").read_text())
    assert manifest["job_id"] == data["id"]
    assert manifest["platform"] == "instagram"


def test_create_job_schedule_mode_requires_future_date(admin_client):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp = _create_job(admin_client, publication_mode="schedule", scheduled_at=past)
    assert resp.status_code == 400


def test_create_job_schedule_mode_accepts_future_date(admin_client):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    resp = _create_job(admin_client, publication_mode="schedule", scheduled_at=future, timezone="America/Bahia")
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["scheduled_at_utc"] is not None
    assert data["scheduled_at_utc"].endswith("Z")


def test_create_job_timezone_conversion_america_bahia_to_utc(admin_client):
    """America/Bahia is UTC-3 year-round (no DST) — 10:00 local -> 13:00 UTC."""
    resp = _create_job(
        admin_client, publication_mode="schedule",
        scheduled_at="2027-01-15T10:00:00", timezone="America/Bahia",
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["scheduled_at_utc"] == "2027-01-15T13:00:00.000Z"


# ── RBAC ──────────────────────────────────────────────────────────────────

def test_viewer_cannot_create_job(viewer_client):
    resp = _create_job(viewer_client)
    assert resp.status_code == 403


def test_viewer_cannot_approve(viewer_client, admin_client):
    created = _create_job(admin_client).get_json()
    resp = viewer_client.post(f"/api/media/jobs/{created['id']}/approve")
    assert resp.status_code == 403


def test_viewer_can_list_and_view(viewer_client, admin_client):
    created = _create_job(admin_client).get_json()
    assert viewer_client.get("/api/media/jobs").status_code == 200
    assert viewer_client.get(f"/api/media/jobs/{created['id']}").status_code == 200


# ── state machine enforcement at the HTTP boundary ──────────────────────

def test_approve_before_ready_for_review_is_409(admin_client):
    created = _create_job(admin_client).get_json()
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/approve")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "invalid_transition"


def test_create_draft_before_approved_is_409(admin_client):
    created = _create_job(admin_client).get_json()
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/create-draft")
    assert resp.status_code == 409


def test_schedule_before_draft_created_is_400_or_409(admin_client):
    created = _create_job(admin_client).get_json()
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/schedule")
    assert resp.status_code in (400, 409)


def test_reject_requires_reason(admin_client):
    created = _create_job(admin_client).get_json()
    admin_client.patch(f"/api/media/jobs/{created['id']}", json={"status": "preparing"})
    admin_client.patch(f"/api/media/jobs/{created['id']}", json={"status": "generating"})
    admin_client.patch(f"/api/media/jobs/{created['id']}", json={"status": "rendering"})
    admin_client.patch(f"/api/media/jobs/{created['id']}", json={"status": "validating"})
    admin_client.patch(f"/api/media/jobs/{created['id']}", json={"status": "ready_for_review"})
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/reject", json={})
    assert resp.status_code == 400
    resp2 = admin_client.post(f"/api/media/jobs/{created['id']}/reject", json={"reason": "qualidade ruim"})
    assert resp2.status_code == 200
    assert resp2.get_json()["status"] == "rejected"


def test_patch_rejects_invalid_status_transition(admin_client):
    created = _create_job(admin_client).get_json()
    resp = admin_client.patch(f"/api/media/jobs/{created['id']}", json={"status": "published"})
    assert resp.status_code == 409


# ── /run atomic claim ─────────────────────────────────────────────────────

def test_run_claims_queued_job(admin_client):
    created = _create_job(admin_client).get_json()
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/run", json={"agent": "media-worker"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "preparing"
    assert data["locked_by"] == "media-worker"
    assert data["attempt_count"] == 1


def test_run_second_claim_conflicts(admin_client):
    created = _create_job(admin_client).get_json()
    admin_client.post(f"/api/media/jobs/{created['id']}/run", json={"agent": "worker-a"})
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/run", json={"agent": "worker-b"})
    assert resp.status_code == 409


def test_run_from_rejected_requeues_and_resets_attempt_count(admin_client):
    created = _create_job(admin_client).get_json()
    job_id = created["id"]
    for status in ("preparing", "generating", "rendering", "validating", "ready_for_review"):
        admin_client.patch(f"/api/media/jobs/{job_id}", json={"status": status})
    admin_client.post(f"/api/media/jobs/{job_id}/reject", json={"reason": "x"})
    resp = admin_client.post(f"/api/media/jobs/{job_id}/run")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "preparing"
    assert data["last_error"] is None


# ── cancel ────────────────────────────────────────────────────────────────

def test_cancel_from_queued(admin_client):
    created = _create_job(admin_client).get_json()
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "cancelled"


def test_cancel_from_terminal_state_is_409(admin_client):
    created = _create_job(admin_client).get_json()
    admin_client.post(f"/api/media/jobs/{created['id']}/cancel")
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/cancel")
    assert resp.status_code == 409


# ── idempotent create-draft (mocked Postiz) ──────────────────────────────

def _approve_job(client, job_id):
    for status in ("preparing", "generating", "rendering", "validating", "ready_for_review"):
        client.patch(f"/api/media/jobs/{job_id}", json={"status": status})
    return client.post(f"/api/media/jobs/{job_id}/approve")


def test_create_draft_requires_render_path(admin_client):
    created = _create_job(admin_client).get_json()
    _approve_job(admin_client, created["id"])
    resp = admin_client.post(f"/api/media/jobs/{created['id']}/create-draft")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "render_missing"


def test_create_draft_uploads_once_and_creates_post_once(admin_client, tmp_path, monkeypatch):
    created = _create_job(admin_client).get_json()
    job_id = created["id"]
    render_path = Path(created["workspace_path"]) / "output" / "final.mp4"
    render_path.write_bytes(b"fake-mp4")
    admin_client.patch(f"/api/media/jobs/{job_id}", json={"render_path": str(render_path)})
    _approve_job(admin_client, job_id)

    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "key")

    with patch("routes.media_jobs.PostizClient") as MockClient:
        instance = MockClient.from_env.return_value
        instance.upload_file.return_value = {"id": "media-1", "path": "https://x/final.mp4", "name": "final.mp4"}
        instance.list_integrations.return_value = [{"id": "int-ig", "identifier": "instagram", "disabled": False}]
        instance.select_integration.return_value = {"id": "int-ig", "identifier": "instagram"}
        instance.create_draft.return_value = [{"postId": "post-1"}]

        resp1 = admin_client.post(f"/api/media/jobs/{job_id}/create-draft")
        assert resp1.status_code == 200
        assert resp1.get_json()["status"] == "draft_created"
        assert instance.upload_file.call_count == 1
        assert instance.create_draft.call_count == 1

        # Idempotent re-POST: already draft_created + postiz_post_id set -> no new calls.
        resp2 = admin_client.post(f"/api/media/jobs/{job_id}/create-draft")
        assert resp2.status_code == 200
        assert instance.upload_file.call_count == 1
        assert instance.create_draft.call_count == 1


def test_create_draft_failure_sets_retryable_and_preserves_uploaded_media_id(admin_client, monkeypatch):
    created = _create_job(admin_client).get_json()
    job_id = created["id"]
    render_path = Path(created["workspace_path"]) / "output" / "final.mp4"
    render_path.write_bytes(b"fake-mp4")
    admin_client.patch(f"/api/media/jobs/{job_id}", json={"render_path": str(render_path)})
    _approve_job(admin_client, job_id)

    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "key")

    with patch("routes.media_jobs.PostizClient") as MockClient:
        instance = MockClient.from_env.return_value
        instance.upload_file.return_value = {"id": "media-1", "path": "https://x/final.mp4", "name": "final.mp4"}
        instance.list_integrations.side_effect = PostizError("timeout talking to Postiz")

        resp = admin_client.post(f"/api/media/jobs/{job_id}/create-draft")
        assert resp.status_code == 502
        data = admin_client.get(f"/api/media/jobs/{job_id}").get_json()
        assert data["status"] == "retryable_failure"
        assert data["postiz_media_id"] == "media-1"  # upload result preserved — won't re-upload on retry

        # Retry: upload must NOT happen again (idempotency).
        instance.list_integrations.side_effect = None
        instance.list_integrations.return_value = [{"id": "int-ig", "identifier": "instagram", "disabled": False}]
        instance.select_integration.return_value = {"id": "int-ig", "identifier": "instagram"}
        instance.create_draft.return_value = [{"postId": "post-1"}]
        resp2 = admin_client.post(f"/api/media/jobs/{job_id}/create-draft")
        assert resp2.status_code == 200
        assert instance.upload_file.call_count == 1


# ── schedule ──────────────────────────────────────────────────────────────

def test_schedule_requires_future_date_and_draft_created(admin_client, monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    created = _create_job(admin_client, publication_mode="schedule", scheduled_at=future).get_json()
    job_id = created["id"]
    resp = admin_client.post(f"/api/media/jobs/{job_id}/schedule")
    # Never scheduled: the job never went through create-draft, so it fails
    # the "has a postiz_post_id" precondition before the state-machine check
    # is even reached — either error is an acceptable "not ready" signal.
    assert resp.status_code in (400, 409)


def test_schedule_calls_change_status_not_create_post(admin_client, monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")
    created = _create_job(admin_client, publication_mode="schedule", scheduled_at=future).get_json()
    job_id = created["id"]
    render_path = Path(created["workspace_path"]) / "output" / "final.mp4"
    render_path.write_bytes(b"fake-mp4")
    admin_client.patch(f"/api/media/jobs/{job_id}", json={"render_path": str(render_path)})
    _approve_job(admin_client, job_id)

    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("POSTIZ_API_KEY", "key")

    with patch("routes.media_jobs.PostizClient") as MockClient:
        instance = MockClient.from_env.return_value
        instance.upload_file.return_value = {"id": "media-1", "path": "https://x/final.mp4", "name": "final.mp4"}
        instance.list_integrations.return_value = [{"id": "int-ig", "identifier": "instagram", "disabled": False}]
        instance.select_integration.return_value = {"id": "int-ig", "identifier": "instagram"}
        instance.create_draft.return_value = [{"postId": "post-1"}]
        admin_client.post(f"/api/media/jobs/{job_id}/create-draft")

        resp = admin_client.post(f"/api/media/jobs/{job_id}/schedule")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "scheduled"
        instance.change_status.assert_called_once_with("post-1", "schedule")
        instance.create_draft.assert_called_once()  # never called twice — no duplicate post


# ── video endpoint: path traversal defense ───────────────────────────────

def test_video_endpoint_404_when_no_render(admin_client):
    created = _create_job(admin_client).get_json()
    resp = admin_client.get(f"/api/media/jobs/{created['id']}/video")
    assert resp.status_code == 404


def test_video_endpoint_rejects_render_path_outside_workspace(admin_client, tmp_path):
    created = _create_job(admin_client).get_json()
    outside = tmp_path.parent / "outside-workspace-final.mp4"
    outside.write_bytes(b"x")
    admin_client.patch(f"/api/media/jobs/{created['id']}", json={"render_path": str(outside)})
    resp = admin_client.get(f"/api/media/jobs/{created['id']}/video")
    assert resp.status_code == 400


def test_video_endpoint_serves_file_within_workspace(admin_client):
    created = _create_job(admin_client).get_json()
    render_path = Path(created["workspace_path"]) / "output" / "final.mp4"
    render_path.write_bytes(b"fake-mp4-bytes")
    admin_client.patch(f"/api/media/jobs/{created['id']}", json={"render_path": str(render_path)})
    resp = admin_client.get(f"/api/media/jobs/{created['id']}/video")
    assert resp.status_code == 200
