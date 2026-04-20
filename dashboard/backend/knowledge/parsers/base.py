"""Base parser ABC + registry for the Knowledge Base.

ParseResult schema:
    {
        "markdown": str,               # full document as Markdown text
        "pages": [PageInfo, ...],      # per-page metadata
        "metadata": {                  # document-level metadata
            "title": str | None,
            "author": str | None,
            "page_count": int,
            "source_mime": str | None,
        }
    }

PageInfo schema:
    {"page_number": int, "markdown": str}

Registry logic:
    get_parser("auto")   → MarkerParser if marker-pdf installed
    get_parser("marker") → MarkerParser (raises if not installed)
    get_parser("llamaparse") → LlamaParseParser (raises if no API key)
    get_parser("auto")   → LlamaParseParser if LLAMAPARSE_API_KEY set AND marker not installed
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

class PageInfo(TypedDict):
    page_number: int
    markdown: str


class ParseResult(TypedDict):
    markdown: str
    pages: List[PageInfo]
    metadata: Dict[str, Any]


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

class BaseParser(ABC):
    """Abstract base for all document parsers."""

    @abstractmethod
    def parse(self, file_path: Path) -> ParseResult:
        """Parse *file_path* and return a ParseResult dict.

        Raises:
            FileNotFoundError: if file_path does not exist.
            ValueError: if the file type is not supported.
            RuntimeError: if the parser dependency is not installed.
        """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _marker_available() -> bool:
    """Return True if marker-pdf is importable."""
    try:
        import importlib
        importlib.util.find_spec("marker")
        return importlib.util.find_spec("marker") is not None
    except Exception:
        return False


def _llamaparse_api_key() -> Optional[str]:
    """Return the LlamaParse API key from env, or None."""
    return os.environ.get("LLAMAPARSE_API_KEY") or None


def get_parser(
    name: str = "auto",
    file_path: Optional[Path] = None,
) -> BaseParser:
    """Return an instantiated parser for *name*, optionally scoped to *file_path*.

    name:
        "auto"       — plaintext for .md/.txt/etc, else Marker, else LlamaParse
        "plaintext"  — force PlainTextParser (text-native formats only)
        "marker"     — force Marker (raises MarkerNotInstalledError if missing)
        "llamaparse" — force LlamaParse (raises if LLAMAPARSE_API_KEY not set)

    When name == "auto" and file_path is given, .md/.txt/.csv/.json/.html/etc.
    always use PlainTextParser — skipping Marker's heavy ML path entirely.

    Raises:
        ValueError: unknown name
        RuntimeError: no suitable parser is available
    """
    from knowledge.parsers.marker_parser import MarkerParser
    from knowledge.parsers.llamaparse_parser import LlamaParseParser
    from knowledge.parsers.plaintext_parser import PlainTextParser, is_plain_text_file

    if name == "auto":
        # Route text-native formats to the cheap parser.
        if file_path is not None and is_plain_text_file(file_path):
            return PlainTextParser()
        if _marker_available():
            return MarkerParser()
        if _llamaparse_api_key():
            return LlamaParseParser()
        raise RuntimeError(
            "No document parser available. "
            "Install marker-pdf: `pip install marker-pdf` (or `uv add marker-pdf`), "
            "or set LLAMAPARSE_API_KEY to use LlamaParse."
        )
    if name == "plaintext":
        return PlainTextParser()
    if name == "marker":
        return MarkerParser()
    if name == "llamaparse":
        return LlamaParseParser()
    raise ValueError(
        f"Unknown parser name '{name}'. "
        "Valid options: 'auto', 'plaintext', 'marker', 'llamaparse'."
    )
