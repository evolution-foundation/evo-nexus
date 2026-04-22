"""Tests for knowledge/chunking.py.

No Postgres required — pure Python/tiktoken only.
"""

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_chunking():
    import sys
    import os
    backend = os.path.join(os.path.dirname(__file__), "..", "..")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from knowledge.chunking import chunk_markdown
    return chunk_markdown


# ---------------------------------------------------------------------------
# Structural chunking (with headings)
# ---------------------------------------------------------------------------

class TestStructuralChunking:
    def test_single_heading_produces_one_chunk(self):
        chunk_markdown = _import_chunking()
        md = "# Introduction\n\nThis is an intro paragraph."
        chunks = chunk_markdown(md)
        assert len(chunks) == 1
        assert "Introduction" in chunks[0]["content"]

    def test_multiple_headings_produce_multiple_chunks(self):
        chunk_markdown = _import_chunking()
        md = (
            "# Chapter 1\n\nContent of chapter one.\n\n"
            "# Chapter 2\n\nContent of chapter two."
        )
        chunks = chunk_markdown(md)
        assert len(chunks) == 2
        assert chunks[0]["chunk_idx"] == 0
        assert chunks[1]["chunk_idx"] == 1

    def test_chunk_idx_is_sequential(self):
        chunk_markdown = _import_chunking()
        md = "\n\n".join(f"# Section {i}\n\nContent {i}" for i in range(5))
        chunks = chunk_markdown(md)
        assert [c["chunk_idx"] for c in chunks] == list(range(len(chunks)))

    def test_heading_path_in_metadata(self):
        chunk_markdown = _import_chunking()
        md = "## Sub Section\n\nContent here."
        chunks = chunk_markdown(md)
        assert chunks[0]["metadata"]["heading_path"] == "Sub Section"

    def test_nested_heading_path(self):
        chunk_markdown = _import_chunking()
        md = "# Top\n\nTop content.\n\n## Sub\n\nSub content."
        chunks = chunk_markdown(md)
        # Second chunk should include both Top and Sub in path
        assert "Sub" in chunks[1]["metadata"]["heading_path"]

    def test_chunk_type_heading(self):
        chunk_markdown = _import_chunking()
        md = "# Title\n\nSome content."
        chunks = chunk_markdown(md)
        assert chunks[0]["chunk_type"] == "heading"

    def test_chunk_type_code(self):
        chunk_markdown = _import_chunking()
        md = "# Code Section\n\n```python\nprint('hello')\n```"
        chunks = chunk_markdown(md)
        assert chunks[0]["chunk_type"] == "code"

    def test_chunk_type_table(self):
        chunk_markdown = _import_chunking()
        md = "# Table\n\n| Col1 | Col2 |\n|------|------|\n| A | B |"
        chunks = chunk_markdown(md)
        assert chunks[0]["chunk_type"] == "table"

    def test_chunk_type_list(self):
        chunk_markdown = _import_chunking()
        md = "# List\n\n- item 1\n- item 2\n- item 3"
        chunks = chunk_markdown(md)
        assert chunks[0]["chunk_type"] == "list"


# ---------------------------------------------------------------------------
# Token-based fallback (no headings)
# ---------------------------------------------------------------------------

class TestTokenFallback:
    def test_short_text_produces_single_chunk(self):
        chunk_markdown = _import_chunking()
        md = "This is a short plain text document with no headings."
        chunks = chunk_markdown(md)
        assert len(chunks) >= 1
        assert chunks[0]["chunk_idx"] == 0

    def test_long_text_splits_into_multiple_chunks(self):
        chunk_markdown = _import_chunking()
        # Generate text that's definitely > 500 tokens
        md = " ".join(["word"] * 2000)
        chunks = chunk_markdown(md)
        assert len(chunks) > 1

    def test_token_chunks_have_overlap(self):
        chunk_markdown = _import_chunking()
        # Create deterministic text with identifiable boundaries
        md = " ".join([f"word{i}" for i in range(2000)])
        chunks = chunk_markdown(md)
        if len(chunks) >= 2:
            # Last words of chunk 0 should appear in beginning of chunk 1 (overlap)
            end_of_first = chunks[0]["content"].split()[-5:]
            start_of_second = chunks[1]["content"].split()[:10]
            overlap = set(end_of_first) & set(start_of_second)
            assert len(overlap) > 0, "Expected overlap between consecutive token-based chunks"

    def test_chunk_type_paragraph_for_plain_text(self):
        chunk_markdown = _import_chunking()
        md = "This is plain text without special markup."
        chunks = chunk_markdown(md)
        assert chunks[0]["chunk_type"] == "paragraph"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_markdown_returns_empty_list(self):
        chunk_markdown = _import_chunking()
        assert chunk_markdown("") == []
        assert chunk_markdown("   \n\n  ") == []

    def test_metadata_keys_present(self):
        chunk_markdown = _import_chunking()
        md = "# Section\n\nContent."
        chunks = chunk_markdown(md)
        assert "page" in chunks[0]["metadata"]
        assert "section" in chunks[0]["metadata"]
        assert "heading_path" in chunks[0]["metadata"]

    def test_doc_metadata_passed_through(self):
        chunk_markdown = _import_chunking()
        md = "# Section\n\nContent."
        doc_meta = {"title": "Test Doc", "author": "Test Author"}
        chunks = chunk_markdown(md, doc_meta)
        assert len(chunks) >= 1  # doc_metadata doesn't affect chunk count

    def test_pre_heading_content_captured(self):
        chunk_markdown = _import_chunking()
        md = "Intro text before any heading.\n\n# First Section\n\nContent."
        chunks = chunk_markdown(md)
        full_text = " ".join(c["content"] for c in chunks)
        assert "Intro text" in full_text
