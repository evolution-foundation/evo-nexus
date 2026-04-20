"""Plain-text parser for files that don't need Marker.

Handles text-native formats (.md, .txt, .markdown, .csv, .json, .html)
by reading the file directly as UTF-8 and returning it as markdown.
Much faster and cheaper than routing through Marker + Surya OCR.

Extensions handled:
    .md .markdown .txt .text .csv .json .html .htm .xml .yaml .yml .log
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from knowledge.parsers.base import BaseParser, ParseResult


PLAIN_TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text",
    ".csv", ".json", ".html", ".htm",
    ".xml", ".yaml", ".yml", ".log",
}


def is_plain_text_file(file_path: Path) -> bool:
    """True if the file extension is one this parser handles natively."""
    return file_path.suffix.lower() in PLAIN_TEXT_EXTENSIONS


class PlainTextParser(BaseParser):
    """Read a text file directly — no Marker, no OCR, no PDF conversion."""

    def parse(self, file_path: Path) -> ParseResult:
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Read as UTF-8 with a permissive fallback for legacy encodings.
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = file_path.read_text(encoding="latin-1", errors="replace")

        ext = file_path.suffix.lower()

        # Wrap non-markdown formats in fenced code blocks so chunking
        # still sees structure (and the LLM knows what it's looking at).
        if ext in {".md", ".markdown"}:
            markdown = content
        elif ext in {".html", ".htm", ".xml"}:
            markdown = f"```{ext.lstrip('.')}\n{content}\n```"
        elif ext == ".json":
            markdown = f"```json\n{content}\n```"
        elif ext == ".csv":
            markdown = f"```csv\n{content}\n```"
        elif ext in {".yaml", ".yml"}:
            markdown = f"```yaml\n{content}\n```"
        else:
            # Plain text — keep as-is, no fencing.
            markdown = content

        metadata: Dict[str, Any] = {
            "title": file_path.stem,
            "author": None,
            "page_count": 1,
            "source_mime": _guess_mime(ext),
        }

        return ParseResult(
            markdown=markdown,
            pages=[{"page_number": 1, "markdown": markdown}],
            metadata=metadata,
        )


def _guess_mime(ext: str) -> str:
    mapping = {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".text": "text/plain",
        ".csv": "text/csv",
        ".json": "application/json",
        ".html": "text/html",
        ".htm": "text/html",
        ".xml": "application/xml",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".log": "text/plain",
    }
    return mapping.get(ext, "text/plain")
