"""Marker-pdf document parser (default).

Supports: PDF, DOCX, PPTX, XLSX, HTML, EPUB, images (PNG, JPG, etc.).
Marker handles format detection internally.

Lazy import: ``marker`` is NOT imported at module load time. This keeps Flask
startup fast and allows the module to be imported even when marker-pdf is not
installed (the error only surfaces when parse() is called).

Model download: call ``download_marker_models(progress_callback)`` once
(triggered by the UI when the user enables Knowledge). The worker skips
download on parse() — it assumes models are cached after the install step.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from knowledge.parsers.base import BaseParser, PageInfo, ParseResult


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class MarkerNotInstalledError(RuntimeError):
    """Raised when marker-pdf is not installed."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MARKER_TIMEOUT_SECONDS = int(os.environ.get("MARKER_TIMEOUT_SECONDS", "600"))

# Thread-safe converter cache (lazy init)
_converter_lock = threading.Lock()
_converter: Optional[Any] = None


def _get_converter() -> Any:
    """Return a cached PdfConverter instance (lazy init, thread-safe)."""
    global _converter
    with _converter_lock:
        if _converter is not None:
            return _converter
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
        except ImportError as exc:
            raise MarkerNotInstalledError(
                "marker-pdf is not installed. "
                "Run: pip install marker-pdf  (or: uv add marker-pdf)"
            ) from exc
        _converter = PdfConverter(artifact_dict=create_model_dict())
        return _converter


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class MarkerParser(BaseParser):
    """Document parser backed by marker-pdf."""

    def parse(self, file_path: Path) -> ParseResult:
        """Parse *file_path* and return a ParseResult.

        Raises:
            FileNotFoundError: if file_path does not exist.
            MarkerNotInstalledError: if marker-pdf is not installed.
            TimeoutError: if parsing exceeds MARKER_TIMEOUT_SECONDS.
        """
        file_path = Path(file_path)

        try:
            from marker.converters.pdf import PdfConverter  # noqa: F401
        except ImportError as exc:
            raise MarkerNotInstalledError(
                "marker-pdf is not installed. "
                "Run: pip install marker-pdf  (or: uv add marker-pdf)"
            ) from exc

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Run conversion in a thread so we can enforce a timeout
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, Exception] = {}

        def _run() -> None:
            try:
                converter = _get_converter()
                rendered = converter(str(file_path))
                result_holder["rendered"] = rendered
            except Exception as exc:
                error_holder["exc"] = exc

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=_MARKER_TIMEOUT_SECONDS)

        if t.is_alive():
            raise TimeoutError(
                f"Marker timed out after {_MARKER_TIMEOUT_SECONDS}s parsing {file_path.name}. "
                f"Increase MARKER_TIMEOUT_SECONDS to allow more time."
            )
        if "exc" in error_holder:
            raise error_holder["exc"]

        rendered = result_holder["rendered"]
        return _to_parse_result(rendered, file_path)


# ---------------------------------------------------------------------------
# Result conversion
# ---------------------------------------------------------------------------

def _to_parse_result(rendered: Any, file_path: Path) -> ParseResult:
    """Convert a Marker rendered output to our ParseResult schema."""
    # Marker returns a RenderedDocument with .markdown and .metadata
    md: str = getattr(rendered, "markdown", "") or ""

    # Extract per-page text if available
    pages: List[PageInfo] = []
    children = getattr(rendered, "children", None) or []
    for i, child in enumerate(children):
        page_md = getattr(child, "markdown", "") or ""
        pages.append({"page_number": i + 1, "markdown": page_md})

    # If no page children, treat full markdown as single page
    if not pages and md:
        pages = [{"page_number": 1, "markdown": md}]

    # Metadata
    raw_meta = getattr(rendered, "metadata", {}) or {}
    metadata: Dict[str, Any] = {
        "title": raw_meta.get("title") or file_path.stem,
        "author": raw_meta.get("author"),
        "page_count": len(pages),
        "source_mime": _guess_mime(file_path),
    }

    return ParseResult(markdown=md, pages=pages, metadata=metadata)


def _guess_mime(path: Path) -> Optional[str]:
    _map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".html": "text/html",
        ".epub": "application/epub+zip",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    return _map.get(path.suffix.lower())


# ---------------------------------------------------------------------------
# Model download (ADR-002 — called by UI, not by parse())
# ---------------------------------------------------------------------------

def download_marker_models(
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> Dict[str, Any]:
    """Download and cache Marker/Surya models.

    This is called by POST /api/knowledge/parsers/install when the user enables
    Knowledge. It is NOT called automatically during parse().

    Args:
        progress_callback: optional callable(stage: str, progress: float 0.0-1.0)

    Returns:
        {"status": "ok", "cached_at": iso_timestamp} on success.
    """
    sentinel = Path.home() / ".cache" / "evonexus" / "marker_installed.ok"

    if sentinel.exists():
        return {"status": "already_installed", "cached_at": sentinel.read_text().strip()}

    if progress_callback:
        progress_callback("importing_marker", 0.0)

    try:
        from marker.models import create_model_dict
    except ImportError as exc:
        raise MarkerNotInstalledError(
            "marker-pdf is not installed. "
            "Run: pip install marker-pdf  (or: uv add marker-pdf)"
        ) from exc

    if progress_callback:
        progress_callback("downloading_models", 0.1)

    # This call downloads all Surya models and caches them in ~/.cache/huggingface/
    create_model_dict()

    if progress_callback:
        progress_callback("finalizing", 0.95)

    sentinel.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    sentinel.write_text(ts)

    if progress_callback:
        progress_callback("done", 1.0)

    return {"status": "ok", "cached_at": ts}
