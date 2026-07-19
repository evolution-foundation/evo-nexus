"""MP4 validation via ffprobe (briefing Etapa 8).

A MediaJob is never allowed to reach `ready_for_review` on the strength of a
render process's exit code alone — every field declared in the job's
manifest (resolution, duration, fps, audio presence) is independently
re-derived from the actual file with ffprobe and compared.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

ALLOWED_VIDEO_CODECS = frozenset({"h264", "hevc"})
DURATION_TOLERANCE_SECONDS = 0.75
FPS_TOLERANCE = 1.0
DEFAULT_MAX_SIZE_BYTES = 1024 * 1024 * 1024  # 1 GiB, overridden by MEDIA_MAX_FILE_SIZE_BYTES


class ValidationError(ValueError):
    """A specific, human-readable reason the render failed validation."""


@dataclass
class VideoMetadata:
    duration_seconds: float
    width: int
    height: int
    fps: float
    video_codec: str
    has_audio: bool
    size_bytes: int
    format_name: str

    def to_dict(self) -> dict:
        return {
            "render_duration_seconds": self.duration_seconds,
            "render_width": self.width,
            "render_height": self.height,
            "render_fps": round(self.fps),
            "render_has_audio": self.has_audio,
            "render_size_bytes": self.size_bytes,
        }


def _run_ffprobe(path: Path, timeout_seconds: int = 60) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        raise ValidationError(f"ffprobe excedeu {timeout_seconds}s") from None
    except FileNotFoundError:
        raise ValidationError("ffprobe não encontrado no PATH do worker.") from None
    if proc.returncode != 0:
        raise ValidationError(f"ffprobe falhou (exit {proc.returncode}): {(proc.stderr or '')[:500]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ValidationError("ffprobe retornou JSON inválido.") from None


def _parse_fps(rate: str) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        den_f = float(den)
        return float(num) / den_f if den_f else 0.0
    return float(rate)


def extract_metadata(path: Path) -> VideoMetadata:
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"Arquivo não encontrado ou não é um arquivo regular: {path}")

    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise ValidationError("Arquivo vazio (0 bytes).")

    probe = _run_ffprobe(path)
    fmt = probe.get("format") or {}
    streams = probe.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not video_streams:
        raise ValidationError("Nenhum stream de vídeo encontrado no arquivo.")
    v = video_streams[0]

    duration_raw = fmt.get("duration") or v.get("duration") or "0"
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        duration = 0.0

    return VideoMetadata(
        duration_seconds=duration,
        width=int(v.get("width") or 0),
        height=int(v.get("height") or 0),
        fps=_parse_fps(v.get("r_frame_rate", "0/0")),
        video_codec=str(v.get("codec_name") or ""),
        has_audio=bool(audio_streams),
        size_bytes=size_bytes,
        format_name=str(fmt.get("format_name") or ""),
    )


def validate_mp4(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_duration_seconds: float,
    expected_fps: int,
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
    require_audio: bool | None = None,
    workspace_root: Path | None = None,
) -> VideoMetadata:
    """Raises ValidationError with the specific failing rule. Never treats a
    render process's exit code as sufficient on its own — every rule below is
    independently checked against the actual bytes on disk.
    """
    path = Path(path).resolve()
    if workspace_root is not None:
        workspace_root = Path(workspace_root).resolve()
        if workspace_root != path and workspace_root not in path.parents:
            raise ValidationError(f"Path do render fora do workspace do job: {path}")

    meta = extract_metadata(path)

    if meta.size_bytes > max_size_bytes:
        raise ValidationError(f"Arquivo excede o tamanho máximo permitido: {meta.size_bytes} > {max_size_bytes} bytes")
    if "mp4" not in meta.format_name and "mov" not in meta.format_name:
        raise ValidationError(f"Container inesperado: {meta.format_name!r} (esperado mp4)")
    if meta.video_codec not in ALLOWED_VIDEO_CODECS:
        raise ValidationError(
            f"Codec de vídeo não permitido: {meta.video_codec!r} (aceita {sorted(ALLOWED_VIDEO_CODECS)})"
        )
    if meta.width != expected_width or meta.height != expected_height:
        raise ValidationError(
            f"Resolução {meta.width}x{meta.height} não bate com o esperado {expected_width}x{expected_height}"
        )
    if abs(meta.duration_seconds - expected_duration_seconds) > DURATION_TOLERANCE_SECONDS:
        raise ValidationError(
            f"Duração {meta.duration_seconds:.2f}s fora da tolerância de "
            f"{expected_duration_seconds:.2f}s (±{DURATION_TOLERANCE_SECONDS}s)"
        )
    if abs(meta.fps - expected_fps) > FPS_TOLERANCE:
        raise ValidationError(f"FPS {meta.fps:.2f} fora da tolerância de {expected_fps} (±{FPS_TOLERANCE})")
    if require_audio is True and not meta.has_audio:
        raise ValidationError("Áudio esperado (require_audio=True) mas ausente no render.")
    if require_audio is False and meta.has_audio:
        raise ValidationError("Áudio presente no render mas não esperado (require_audio=False).")

    return meta
