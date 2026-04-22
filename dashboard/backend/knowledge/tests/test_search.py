"""Tests for knowledge/search.py — hybrid search with RRF + boosts."""

from __future__ import annotations

import os
import sys
import uuid
from typing import List
from unittest.mock import MagicMock, patch

import pytest


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


def _make_mock_embedder(dim: int = 768):
    mock = MagicMock()
    mock.dim = dim
    # Accept optional task_type for API parity with real embedders
    # (Gemini uses it; local/OpenAI ignore it silently).
    mock.embed.side_effect = lambda texts, task_type=None: [[0.1] * dim for _ in texts]
    return mock


def _make_mock_engine(rows=None):
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchall.return_value = rows or []
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn
    return mock_engine, mock_conn


def _mock_row(data: dict):
    row = MagicMock()
    row._mapping = data
    return row


class TestHybridSearchUnit:
    def test_returns_empty_on_no_results(self):
        _add_backend()
        from knowledge import search

        mock_engine, _ = _make_mock_engine(rows=[])
        mock_embedder = _make_mock_embedder()

        with patch.object(search, "get_dsn", return_value="postgresql://test"), \
             patch.object(search, "get_engine", return_value=mock_engine), \
             patch.object(search, "get_embedder", return_value=mock_embedder):
            results = search.hybrid_search(
                connection_id="conn-1",
                space_id=str(uuid.uuid4()),
                query="test query",
                top_k=10,
            )

        assert results == []

    def test_applies_default_boosts(self):
        _add_backend()
        from knowledge import search

        space_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        chunk_id = str(uuid.uuid4())

        # Two rows: faq (boost 1.20) and transcript (boost 0.80)
        faq_row = _mock_row({
            "chunk_id": chunk_id,
            "rrf_score": 0.1,
            "document_id": doc_id,
            "space_id": space_id,
            "unit_id": None,
            "chunk_idx": 0,
            "chunk_type": "paragraph",
            "content": "FAQ content",
            "chunk_metadata": "{}",
            "doc_title": "FAQ Doc",
            "content_type": "faq",
            "doc_status": "ready",
            "space_boost": 1.0,  # no JSONB override
        })
        transcript_row = _mock_row({
            "chunk_id": str(uuid.uuid4()),
            "rrf_score": 0.1,
            "document_id": str(uuid.uuid4()),
            "space_id": space_id,
            "unit_id": None,
            "chunk_idx": 0,
            "chunk_type": "paragraph",
            "content": "Transcript content",
            "chunk_metadata": "{}",
            "doc_title": "Transcript Doc",
            "content_type": "transcript",
            "doc_status": "ready",
            "space_boost": 1.0,
        })

        mock_engine, _ = _make_mock_engine(rows=[faq_row, transcript_row])
        mock_embedder = _make_mock_embedder()

        with patch.object(search, "get_dsn", return_value="postgresql://test"), \
             patch.object(search, "get_engine", return_value=mock_engine), \
             patch.object(search, "get_embedder", return_value=mock_embedder):
            results = search.hybrid_search("conn-1", space_id, "query", top_k=10)

        # FAQ score: 0.1 * 1.0 * 1.20 = 0.12
        # Transcript score: 0.1 * 1.0 * 0.80 = 0.08
        # FAQ should come first
        assert len(results) == 2
        assert results[0]["content_type"] == "faq"
        assert results[1]["content_type"] == "transcript"
        assert results[0]["final_score"] > results[1]["final_score"]

    def test_top_k_limits_results(self):
        _add_backend()
        from knowledge import search

        space_id = str(uuid.uuid4())
        rows = []
        for i in range(20):
            rows.append(_mock_row({
                "chunk_id": str(uuid.uuid4()),
                "rrf_score": float(20 - i) / 100,
                "document_id": str(uuid.uuid4()),
                "space_id": space_id,
                "unit_id": None,
                "chunk_idx": i,
                "chunk_type": "paragraph",
                "content": f"Content {i}",
                "chunk_metadata": "{}",
                "doc_title": f"Doc {i}",
                "content_type": "article",
                "doc_status": "ready",
                "space_boost": 1.0,
            }))

        mock_engine, _ = _make_mock_engine(rows=rows)
        mock_embedder = _make_mock_embedder()

        with patch.object(search, "get_dsn", return_value="postgresql://test"), \
             patch.object(search, "get_engine", return_value=mock_engine), \
             patch.object(search, "get_embedder", return_value=mock_embedder):
            results = search.hybrid_search("conn-1", space_id, "query", top_k=5)

        assert len(results) == 5

    def test_filter_by_unit_id(self):
        _add_backend()
        from knowledge import search

        space_id = str(uuid.uuid4())
        target_unit = str(uuid.uuid4())
        other_unit = str(uuid.uuid4())

        rows = [
            _mock_row({
                "chunk_id": str(uuid.uuid4()),
                "rrf_score": 0.1,
                "document_id": str(uuid.uuid4()),
                "space_id": space_id,
                "unit_id": target_unit,
                "chunk_idx": 0,
                "chunk_type": "paragraph",
                "content": "In unit",
                "chunk_metadata": "{}",
                "doc_title": "Doc",
                "content_type": "article",
                "doc_status": "ready",
                "space_boost": 1.0,
            }),
            _mock_row({
                "chunk_id": str(uuid.uuid4()),
                "rrf_score": 0.2,
                "document_id": str(uuid.uuid4()),
                "space_id": space_id,
                "unit_id": other_unit,
                "chunk_idx": 0,
                "chunk_type": "paragraph",
                "content": "Other unit",
                "chunk_metadata": "{}",
                "doc_title": "Other Doc",
                "content_type": "article",
                "doc_status": "ready",
                "space_boost": 1.0,
            }),
        ]

        mock_engine, _ = _make_mock_engine(rows=rows)
        mock_embedder = _make_mock_embedder()

        with patch.object(search, "get_dsn", return_value="postgresql://test"), \
             patch.object(search, "get_engine", return_value=mock_engine), \
             patch.object(search, "get_embedder", return_value=mock_embedder):
            results = search.hybrid_search(
                "conn-1", space_id, "query", top_k=10,
                filters={"unit_id": target_unit}
            )

        assert len(results) == 1
        assert results[0]["unit_id"] == target_unit

    def test_embedding_cache_reuse(self):
        """Same query+connection should reuse cached vector."""
        _add_backend()
        from knowledge import search

        # Clear cache
        with search._cache_lock:
            search._embed_cache.clear()

        mock_engine, _ = _make_mock_engine()
        mock_embedder = _make_mock_embedder()
        space_id = str(uuid.uuid4())

        with patch.object(search, "get_dsn", return_value="postgresql://test"), \
             patch.object(search, "get_engine", return_value=mock_engine), \
             patch.object(search, "get_embedder", return_value=mock_embedder):
            search.hybrid_search("conn-1", space_id, "same query", top_k=5)
            search.hybrid_search("conn-1", space_id, "same query", top_k=5)

        # embed() should only be called once (second call uses cache)
        assert mock_embedder.embed.call_count == 1

    def test_space_boost_overrides_default(self):
        """When space_boost != 1.0, it overrides JSONB; DEFAULT_BOOSTS still applies."""
        _add_backend()
        from knowledge import search

        space_id = str(uuid.uuid4())
        row = _mock_row({
            "chunk_id": str(uuid.uuid4()),
            "rrf_score": 0.1,
            "document_id": str(uuid.uuid4()),
            "space_id": space_id,
            "unit_id": None,
            "chunk_idx": 0,
            "chunk_type": "paragraph",
            "content": "FAQ with space override",
            "chunk_metadata": "{}",
            "doc_title": "FAQ Doc",
            "content_type": "faq",
            "doc_status": "ready",
            "space_boost": 2.0,  # explicit JSONB override
        })

        mock_engine, _ = _make_mock_engine(rows=[row])
        mock_embedder = _make_mock_embedder()

        with patch.object(search, "get_dsn", return_value="postgresql://test"), \
             patch.object(search, "get_engine", return_value=mock_engine), \
             patch.object(search, "get_embedder", return_value=mock_embedder):
            results = search.hybrid_search("conn-1", space_id, "query", top_k=5)

        # final_score = 0.1 * 2.0 * 1.20 (default faq boost) = 0.24
        assert len(results) == 1
        assert abs(results[0]["final_score"] - 0.24) < 1e-5


@pytest.mark.requires_postgres
class TestHybridSearchIntegration:
    def test_search_against_real_postgres(self, pg_full_schema, tmp_path):
        """End-to-end: ingest a doc, then search for it."""
        _add_backend()
        from knowledge import spaces, search
        from knowledge.ingestion import ingest_document
        from sqlalchemy import create_engine

        dsn = os.environ["KNOWLEDGE_TEST_DATABASE_URL"]
        engine = create_engine(dsn)

        # Write a test file
        test_file = tmp_path / "test_doc.md"
        test_file.write_text("# Machine Learning\n\nNeural networks are powerful.")

        space_id = None
        try:
            with patch.object(spaces, "get_dsn", return_value=dsn), \
                 patch.object(spaces, "get_engine", return_value=engine):
                space = spaces.create_space("test", {
                    "slug": f"search-test-{uuid.uuid4().hex[:8]}",
                    "name": "Search Test Space",
                })
                space_id = space["id"]

            # Ingest with mocked embedder (real DB, mocked embedder)
            mock_embedder = _make_mock_embedder()
            with patch("knowledge.ingestion.get_parser") as mp, \
                 patch("knowledge.ingestion.get_embedder", return_value=mock_embedder), \
                 patch("knowledge.ingestion.get_dsn", return_value=dsn), \
                 patch("knowledge.ingestion.get_engine", return_value=engine):
                mp.return_value.parse.return_value = {
                    "markdown": "# Machine Learning\n\nNeural networks are powerful.",
                    "pages": [{"page_number": 1, "markdown": "# Machine Learning\n\nNeural networks are powerful."}],
                    "metadata": {"title": "ML Doc", "author": None, "page_count": 1, "source_mime": "text/plain"},
                }
                ingest_document("test", space_id, test_file)

            # Search (mocked embedder, real DB)
            with patch.object(search, "get_dsn", return_value=dsn), \
                 patch.object(search, "get_engine", return_value=engine), \
                 patch.object(search, "get_embedder", return_value=mock_embedder):
                results = search.hybrid_search("test", space_id, "machine learning", top_k=5)

            # With mocked zero-vectors, FTS should still work
            assert isinstance(results, list)

        finally:
            if space_id:
                with patch.object(spaces, "get_dsn", return_value=dsn), \
                     patch.object(spaces, "get_engine", return_value=engine):
                    spaces.delete_space("test", space_id)
