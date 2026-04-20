"""LlamaParse document parser (opt-in via LLAMAPARSE_API_KEY).

Stub implementation — provides a working registry entry. Full implementation
deferred to v1.1 per plan.md.

Lazy import: llama_parse is NOT imported at module load time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from knowledge.parsers.base import BaseParser, PageInfo, ParseResult


class LlamaParseNotConfiguredError(RuntimeError):
    """Raised when LLAMAPARSE_API_KEY is not set."""


class LlamaParseParser(BaseParser):
    """Document parser backed by LlamaParse (cloud API).

    Requires LLAMAPARSE_API_KEY environment variable.
    Install: pip install llama-cloud-services
    """

    def parse(self, file_path: Path) -> ParseResult:
        """Parse *file_path* via LlamaParse API.

        Raises:
            LlamaParseNotConfiguredError: if LLAMAPARSE_API_KEY not set.
            RuntimeError: if llama-cloud-services is not installed.
            FileNotFoundError: if file_path does not exist.
        """
        api_key = os.environ.get("LLAMAPARSE_API_KEY")
        if not api_key:
            raise LlamaParseNotConfiguredError(
                "LLAMAPARSE_API_KEY is not set. "
                "Set it in .env to use LlamaParse: LLAMAPARSE_API_KEY=llx-..."
            )

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            from llama_parse import LlamaParse  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "llama-cloud-services is not installed. "
                "Run: pip install llama-cloud-services"
            ) from exc

        parser = LlamaParse(api_key=api_key, result_type="markdown")
        documents = parser.load_data(str(file_path))

        # Combine all document chunks into a single markdown string
        markdown_parts: List[str] = [doc.text for doc in documents if doc.text]
        md = "\n\n".join(markdown_parts)

        pages: List[PageInfo] = [
            {"page_number": i + 1, "markdown": doc.text or ""}
            for i, doc in enumerate(documents)
        ]

        metadata: Dict[str, Any] = {
            "title": file_path.stem,
            "author": None,
            "page_count": len(pages),
            "source_mime": None,
        }

        return ParseResult(markdown=md, pages=pages, metadata=metadata)
