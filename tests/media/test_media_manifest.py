"""media_manifest.py — publication_manifest.json schema validation + path safety."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))

import pytest
import media_workspace as mw
import media_manifest as mm


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_WORKSPACE", str(tmp_path))
    job_id = mw.new_job_id()
    base = mw.ensure_job_scaffold(job_id)
    return job_id, base


VALID_MANIFEST = {
    "render_file": "output/final.mp4",
    "title": "Teste OmniNexus",
    "caption": "Legenda",
    "platform": "instagram",
    "format": "vertical",
    "width": 720,
    "height": 1280,
    "fps": 30,
    "duration_seconds": 6,
    "platform_settings": {"__type": "instagram", "post_type": "reel"},
}


def _write_manifest(base: Path, data: dict) -> None:
    (base / "output" / "publication_manifest.json").write_text(json.dumps(data), encoding="utf-8")


def test_valid_manifest_loads(job):
    job_id, base = job
    (base / "output" / "final.mp4").write_bytes(b"fake")
    _write_manifest(base, {**VALID_MANIFEST, "job_id": job_id})
    result = mm.load_and_validate_manifest(base)
    assert result["_resolved_render_path"] == str((base / "output" / "final.mp4").resolve())


@pytest.mark.parametrize("missing_field", [
    "job_id", "render_file", "title", "platform", "format", "width", "height", "fps", "duration_seconds",
])
def test_missing_required_field_rejected(job, missing_field):
    job_id, base = job
    data = {**VALID_MANIFEST, "job_id": job_id}
    del data[missing_field]
    _write_manifest(base, data)
    with pytest.raises(mm.ManifestValidationError):
        mm.load_and_validate_manifest(base)


def test_invalid_platform_rejected(job):
    job_id, base = job
    _write_manifest(base, {**VALID_MANIFEST, "job_id": job_id, "platform": "facebook"})
    with pytest.raises(mm.ManifestValidationError):
        mm.load_and_validate_manifest(base)


def test_invalid_fps_rejected(job):
    job_id, base = job
    _write_manifest(base, {**VALID_MANIFEST, "job_id": job_id, "fps": 25})
    with pytest.raises(mm.ManifestValidationError):
        mm.load_and_validate_manifest(base)


@pytest.mark.parametrize("bad_render_file", [
    "../../../etc/passwd",
    "/etc/passwd",
    "../../output/final.mp4",
])
def test_path_traversal_in_render_file_rejected(job, bad_render_file):
    job_id, base = job
    _write_manifest(base, {**VALID_MANIFEST, "job_id": job_id, "render_file": bad_render_file})
    with pytest.raises(mm.ManifestValidationError):
        mm.load_and_validate_manifest(base)


def test_missing_manifest_file_rejected(job):
    _job_id, base = job
    with pytest.raises(mm.ManifestValidationError):
        mm.load_and_validate_manifest(base)


def test_malformed_json_rejected(job):
    job_id, base = job
    (base / "output" / "publication_manifest.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(mm.ManifestValidationError):
        mm.load_and_validate_manifest(base)


def test_platform_settings_requires_type_when_present(job):
    job_id, base = job
    data = {**VALID_MANIFEST, "job_id": job_id, "platform_settings": {"post_type": "reel"}}
    _write_manifest(base, data)
    with pytest.raises(mm.ManifestValidationError):
        mm.load_and_validate_manifest(base)


def test_extra_fields_tolerated(job):
    job_id, base = job
    data = {**VALID_MANIFEST, "job_id": job_id, "notes": "agent's own scratch notes"}
    (base / "output" / "final.mp4").write_bytes(b"fake")
    _write_manifest(base, data)
    result = mm.load_and_validate_manifest(base)
    assert result["notes"] == "agent's own scratch notes"
