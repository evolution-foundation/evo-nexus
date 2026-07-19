"""media_validation.py — ffprobe-based MP4 validation.

Uses the real ffmpeg/ffprobe binaries (not mocked) to generate a tiny test
clip — this is the same validator the media-worker runs against real
HyperFrames renders, so exercising it against a real MP4 is more honest than
mocking subprocess output. Skips if ffmpeg/ffprobe aren't on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))

import pytest
import media_validation as mv

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available in this environment",
)


@pytest.fixture(scope="module")
def sample_clip(tmp_path_factory):
    """720x1280, 6s, 30fps, h264, no audio — matches the briefing's smoke-test spec."""
    out = tmp_path_factory.mktemp("clips") / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=720x1280:d=6:r=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_extract_metadata_matches_known_clip(sample_clip):
    meta = mv.extract_metadata(sample_clip)
    assert meta.width == 720
    assert meta.height == 1280
    assert meta.video_codec == "h264"
    assert abs(meta.duration_seconds - 6.0) < 0.5
    assert abs(meta.fps - 30.0) < 1.0
    assert meta.has_audio is False
    assert meta.size_bytes > 0


def test_validate_mp4_passes_matching_expectations(sample_clip):
    meta = mv.validate_mp4(
        sample_clip, expected_width=720, expected_height=1280,
        expected_duration_seconds=6, expected_fps=30, require_audio=False,
    )
    assert meta.width == 720


def test_validate_mp4_rejects_resolution_mismatch(sample_clip):
    with pytest.raises(mv.ValidationError, match="Resolução"):
        mv.validate_mp4(sample_clip, expected_width=1080, expected_height=1920,
                         expected_duration_seconds=6, expected_fps=30)


def test_validate_mp4_rejects_duration_mismatch(sample_clip):
    with pytest.raises(mv.ValidationError, match="Duração"):
        mv.validate_mp4(sample_clip, expected_width=720, expected_height=1280,
                         expected_duration_seconds=20, expected_fps=30)


def test_validate_mp4_rejects_fps_mismatch(sample_clip):
    with pytest.raises(mv.ValidationError, match="FPS"):
        mv.validate_mp4(sample_clip, expected_width=720, expected_height=1280,
                         expected_duration_seconds=6, expected_fps=60)


def test_validate_mp4_rejects_unexpected_audio_presence(sample_clip):
    with pytest.raises(mv.ValidationError, match="Áudio"):
        mv.validate_mp4(sample_clip, expected_width=720, expected_height=1280,
                         expected_duration_seconds=6, expected_fps=30, require_audio=True)


def test_validate_mp4_rejects_oversized_file(sample_clip):
    with pytest.raises(mv.ValidationError, match="tamanho"):
        mv.validate_mp4(sample_clip, expected_width=720, expected_height=1280,
                         expected_duration_seconds=6, expected_fps=30, max_size_bytes=10)


def test_validate_mp4_rejects_path_outside_workspace(sample_clip, tmp_path):
    other_root = tmp_path / "other-workspace"
    other_root.mkdir()
    with pytest.raises(mv.ValidationError, match="workspace"):
        mv.validate_mp4(sample_clip, expected_width=720, expected_height=1280,
                         expected_duration_seconds=6, expected_fps=30, workspace_root=other_root)


def test_extract_metadata_rejects_missing_file(tmp_path):
    with pytest.raises(mv.ValidationError, match="não encontrado"):
        mv.extract_metadata(tmp_path / "does-not-exist.mp4")


def test_extract_metadata_rejects_empty_file(tmp_path):
    f = tmp_path / "empty.mp4"
    f.write_bytes(b"")
    with pytest.raises(mv.ValidationError, match="vazio"):
        mv.extract_metadata(f)


def test_extract_metadata_rejects_non_video_file(tmp_path):
    f = tmp_path / "not-a-video.mp4"
    f.write_text("this is plain text, not a real mp4")
    with pytest.raises(mv.ValidationError):
        mv.extract_metadata(f)
