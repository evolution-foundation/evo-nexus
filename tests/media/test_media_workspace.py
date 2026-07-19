"""media_workspace.py — path traversal prevention, sanitization, checksum."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))

import pytest
import media_workspace as mw


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_WORKSPACE", str(tmp_path))
    return tmp_path


def test_job_id_generation_and_validation():
    job_id = mw.new_job_id()
    assert mw.is_valid_job_id(job_id)
    assert not mw.is_valid_job_id("../../etc/passwd")
    assert not mw.is_valid_job_id("not-a-uuid")
    assert not mw.is_valid_job_id("")


def test_job_dir_rejects_non_uuid(workspace):
    with pytest.raises(mw.PathSecurityError):
        mw.job_dir("../../etc/passwd")


def test_ensure_job_scaffold_creates_expected_layout(workspace):
    job_id = mw.new_job_id()
    base = mw.ensure_job_scaffold(job_id)
    for sub in ("input/brand", "input/assets", "project", "output", "logs"):
        assert (base / sub).is_dir()


@pytest.mark.parametrize("bad_path", [
    "../evil",
    "/etc/passwd",
    "a/../../evil",
    "../../../../etc/shadow",
    "input/../../../etc/passwd",
])
def test_resolve_within_rejects_traversal(workspace, bad_path):
    job_id = mw.new_job_id()
    base = mw.ensure_job_scaffold(job_id)
    with pytest.raises(mw.PathSecurityError):
        mw.resolve_within(base, bad_path)


def test_resolve_within_accepts_legit_relative_path(workspace):
    job_id = mw.new_job_id()
    base = mw.ensure_job_scaffold(job_id)
    resolved = mw.resolve_within(base, "output/final.mp4")
    assert resolved == (base / "output" / "final.mp4").resolve()


def test_resolve_within_rejects_absolute_path(workspace):
    job_id = mw.new_job_id()
    base = mw.ensure_job_scaffold(job_id)
    with pytest.raises(mw.PathSecurityError):
        mw.resolve_within(base, "/etc/passwd")


def test_safe_asset_filename_strips_directories_and_sanitizes():
    assert mw.safe_asset_filename("../../evil.mp4") == "evil.mp4"
    assert mw.safe_asset_filename("/etc/passwd.png") == "passwd.png"
    result = mw.safe_asset_filename("logo weird name!!.PNG")
    assert result.endswith(".png")
    assert " " not in result and "!" not in result


def test_safe_asset_filename_rejects_disallowed_extension():
    with pytest.raises(mw.PathSecurityError):
        mw.safe_asset_filename("script.sh")
    with pytest.raises(mw.PathSecurityError):
        mw.safe_asset_filename("payload.exe")


def test_sha256_file_streams_correctly(tmp_path):
    f = tmp_path / "final.mp4"
    content = b"hello world" * 100_000  # large enough to exercise chunking
    f.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert mw.sha256_file(f, chunk_size=4096) == expected


def test_skills_symlink_best_effort_when_source_missing(workspace, monkeypatch):
    monkeypatch.setenv("MEDIA_SKILLS_DIR", "/nonexistent/path")
    job_id = mw.new_job_id()
    base = mw.ensure_job_scaffold(job_id)  # must not raise
    assert not (base / ".claude" / "skills").exists()


def test_skills_symlink_created_when_source_exists(workspace, monkeypatch, tmp_path):
    skills_src = tmp_path.parent / "skills-source"
    skills_src.mkdir(exist_ok=True)
    monkeypatch.setenv("MEDIA_SKILLS_DIR", str(skills_src))
    job_id = mw.new_job_id()
    base = mw.ensure_job_scaffold(job_id)
    link = base / ".claude" / "skills"
    assert link.is_symlink()
    assert link.resolve() == skills_src.resolve()
