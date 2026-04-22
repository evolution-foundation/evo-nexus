"""Marker model download management (ADR-002).

Called by POST /api/knowledge/parsers/install when the user activates
the Knowledge Base and wants to use the default Marker parser.

Model download is NOT triggered automatically — the UI explicitly asks
the user to install parser models (one-time, ~500MB Surya models).

Sentinel file: ~/.cache/evonexus/marker_installed.ok
    Present → models cached; install endpoint returns "already_installed".
    Absent → download needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


_SENTINEL = Path.home() / ".cache" / "evonexus" / "marker_installed.ok"


def get_parser_status() -> Dict[str, Any]:
    """Return current parser installation status.

    Returns:
        {
            "marker_installed": bool,
            "models_cached": list[str],
            "cached_at": iso_timestamp | None,
        }
    """
    installed = _SENTINEL.exists()
    cached_at = _SENTINEL.read_text().strip() if installed else None

    models_cached: List[str] = []
    if installed:
        # Best-effort: list HuggingFace cache entries for Surya/Marker models
        hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
        if hf_cache.exists():
            models_cached = [
                d.name for d in hf_cache.iterdir()
                if d.is_dir() and ("surya" in d.name.lower() or "marker" in d.name.lower())
            ]

    return {
        "marker_installed": installed,
        "models_cached": models_cached,
        "cached_at": cached_at,
    }


def download_marker_models(
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    """Download and cache Marker/Surya models.

    Delegates to ``marker_parser.download_marker_models``.

    Args:
        progress_callback: optional callable(stage: str, progress: float 0.0-1.0)

    Returns:
        {"status": "ok" | "already_installed", "cached_at": iso_timestamp}

    Raises:
        MarkerNotInstalledError: if marker-pdf is not installed.
    """
    from knowledge.parsers.marker_parser import download_marker_models as _download
    return _download(progress_callback)
