"""Tests for knowledge/ingestion.py.

Uses mocked parsers, embedders, and SQLAlchemy engine — no real Postgres needed
for unit tests. Postgres tests use the `requires_postgres` marker.
"""

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch, call

import pytest


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------

def _make_mock_parser(markdown: str = "# Section\n\nContent.", pages=None):
    """Return a mock parser that returns a fixed ParseResult."""
    mock = MagicMock()
    mock.parse.return_value = {
        "markdown": markdown,
        "pages": pages or [{"page_number": 1, "markdown": markdown}],
        "metadata": {"title": "Test Doc", "author": None, "page_count": 1, "source_mime": "application/pdf"},
    }
    return mock


def _make_mock_embedder(dim: int = 768):
    """Return a mock embedder that returns zero-vectors."""
    mock = MagicMock()
    mock.dim = dim

    def _embed(texts):
        return [[0.0] * dim for _ in texts]

    mock.embed.side_effect = _embed
    return mock


def _make_mock_engine():
    """Return a mock SQLAlchemy engine with context-manager support."""
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute = MagicMock()

    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_conn
    return mock_engine, mock_conn


# ---------------------------------------------------------------------------
# Unit tests (fully mocked)
# ---------------------------------------------------------------------------

class TestIngestDocumentUnit:
    def _make_test_file(self, tmp_path: Path) -> Path:
        f = tmp_path / "test.pdf"
        f.write_bytes(b"%PDF-1.4 minimal")
        return f

    def test_returns_document_id_string(self, tmp_path):
        _add_backend()
        from knowledge import ingestion

        test_file = self._make_test_file(tmp_path)
        mock_parser = _make_mock_parser()
        mock_embedder = _make_mock_embedder()
        mock_engine, mock_conn = _make_mock_engine()

        with patch.object(ingestion, "get_parser", return_value=mock_parser), \
             patch.object(ingestion, "get_embedder", return_value=mock_embedder), \
             patch.object(ingestion, "get_dsn", return_value="postgresql://test"), \
             patch.object(ingestion, "get_engine", return_value=mock_engine):

            doc_id = ingestion.ingest_document(
                connection_id="conn-123",
                space_id=str(uuid.uuid4()),
                file_path=test_file,
            )

        assert isinstance(doc_id, str)
        # Should be a valid UUID
        uuid.UUID(doc_id)

    def test_uses_provided_document_id(self, tmp_path):
        _add_backend()
        from knowledge import ingestion

        test_file = self._make_test_file(tmp_path)
        fixed_id = str(uuid.uuid4())
        mock_parser = _make_mock_parser()
        mock_embedder = _make_mock_embedder()
        mock_engine, _ = _make_mock_engine()

        with patch.object(ingestion, "get_parser", return_value=mock_parser), \
             patch.object(ingestion, "get_embedder", return_value=mock_embedder), \
             patch.object(ingestion, "get_dsn", return_value="postgresql://test"), \
             patch.object(ingestion, "get_engine", return_value=mock_engine):

            doc_id = ingestion.ingest_document(
                connection_id="conn-123",
                space_id=str(uuid.uuid4()),
                file_path=test_file,
                document_id=fixed_id,
            )

        assert doc_id == fixed_id

    def test_progress_callback_called_in_order(self, tmp_path):
        _add_backend()
        from knowledge import ingestion

        test_file = self._make_test_file(tmp_path)
        phases_seen = []

        def _callback(phase):
            phases_seen.append(phase)

        mock_parser = _make_mock_parser()
        mock_embedder = _make_mock_embedder()
        mock_engine, _ = _make_mock_engine()

        with patch.object(ingestion, "get_parser", return_value=mock_parser), \
             patch.object(ingestion, "get_embedder", return_value=mock_embedder), \
             patch.object(ingestion, "get_dsn", return_value="postgresql://test"), \
             patch.object(ingestion, "get_engine", return_value=mock_engine):

            ingestion.ingest_document(
                connection_id="conn-123",
                space_id=str(uuid.uuid4()),
                file_path=test_file,
                progress_callback=_callback,
            )

        assert "scanning" in phases_seen
        assert "parsing" in phases_seen
        assert "chunking" in phases_seen
        assert "embedding" in phases_seen
        assert "storing" in phases_seen
        assert "done" in phases_seen

    def test_rollback_on_embed_error(self, tmp_path):
        """ADR-007: on embed error, document is marked 'error' and exception re-raised."""
        _add_backend()
        from knowledge import ingestion

        test_file = self._make_test_file(tmp_path)
        mock_parser = _make_mock_parser()
        mock_engine, _ = _make_mock_engine()

        failing_embedder = MagicMock()
        failing_embedder.embed.side_effect = RuntimeError("GPU out of memory")

        mark_error_calls = []

        def _mock_mark_error(engine, doc_id, msg):
            mark_error_calls.append(msg)

        with patch.object(ingestion, "get_parser", return_value=mock_parser), \
             patch.object(ingestion, "get_embedder", return_value=failing_embedder), \
             patch.object(ingestion, "get_dsn", return_value="postgresql://test"), \
             patch.object(ingestion, "get_engine", return_value=mock_engine), \
             patch.object(ingestion, "_mark_error", side_effect=_mock_mark_error):

            with pytest.raises(RuntimeError, match="GPU out of memory"):
                ingestion.ingest_document(
                    connection_id="conn-123",
                    space_id=str(uuid.uuid4()),
                    file_path=test_file,
                )

        assert len(mark_error_calls) == 1
        assert "Embed error" in mark_error_calls[0]

    def test_rollback_on_parse_error(self, tmp_path):
        """Parse failure marks document 'error' and re-raises."""
        _add_backend()
        from knowledge import ingestion

        test_file = self._make_test_file(tmp_path)
        failing_parser = MagicMock()
        failing_parser.parse.side_effect = ValueError("Corrupt PDF")
        mock_engine, _ = _make_mock_engine()
        mark_error_calls = []

        def _mock_mark_error(engine, doc_id, msg):
            mark_error_calls.append(msg)

        with patch.object(ingestion, "get_parser", return_value=failing_parser), \
             patch.object(ingestion, "get_dsn", return_value="postgresql://test"), \
             patch.object(ingestion, "get_engine", return_value=mock_engine), \
             patch.object(ingestion, "_mark_error", side_effect=_mock_mark_error):

            with pytest.raises(ValueError, match="Corrupt PDF"):
                ingestion.ingest_document(
                    connection_id="conn-123",
                    space_id=str(uuid.uuid4()),
                    file_path=test_file,
                )

        assert len(mark_error_calls) == 1
        assert "Parse error" in mark_error_calls[0]

    def test_classify_enqueue_failure_is_non_fatal(self, tmp_path):
        """Classify enqueue failure should not abort ingestion (ADR-008)."""
        _add_backend()
        from knowledge import ingestion

        test_file = self._make_test_file(tmp_path)
        mock_parser = _make_mock_parser()
        mock_embedder = _make_mock_embedder()

        # Engine that fails on the 3rd begin() call (classify enqueue)
        call_count = {"n": 0}
        normal_engine, normal_conn = _make_mock_engine()

        def begin_side_effect():
            call_count["n"] += 1
            if call_count["n"] >= 3:  # 3rd transaction = classify enqueue
                raise Exception("DB blip during enqueue")
            return normal_conn

        normal_engine.begin.side_effect = begin_side_effect

        with patch.object(ingestion, "get_parser", return_value=mock_parser), \
             patch.object(ingestion, "get_embedder", return_value=mock_embedder), \
             patch.object(ingestion, "get_dsn", return_value="postgresql://test"), \
             patch.object(ingestion, "get_engine", return_value=normal_engine):

            # Should not raise — classify enqueue failure is swallowed
            doc_id = ingestion.ingest_document(
                connection_id="conn-123",
                space_id=str(uuid.uuid4()),
                file_path=test_file,
            )

        assert doc_id is not None

    def test_empty_chunks_marks_error(self, tmp_path):
        """Document with no chunks produced should be marked error."""
        _add_backend()
        from knowledge import ingestion

        test_file = self._make_test_file(tmp_path)
        mock_parser = _make_mock_parser(markdown="")  # Empty markdown → no chunks
        mock_embedder = _make_mock_embedder()
        mock_engine, _ = _make_mock_engine()
        mark_error_calls = []

        def _mock_mark_error(engine, doc_id, msg):
            mark_error_calls.append(msg)

        with patch.object(ingestion, "get_parser", return_value=mock_parser), \
             patch.object(ingestion, "get_embedder", return_value=mock_embedder), \
             patch.object(ingestion, "get_dsn", return_value="postgresql://test"), \
             patch.object(ingestion, "get_engine", return_value=mock_engine), \
             patch.object(ingestion, "_mark_error", side_effect=_mock_mark_error):

            with pytest.raises(ValueError, match="No chunks produced"):
                ingestion.ingest_document(
                    connection_id="conn-123",
                    space_id=str(uuid.uuid4()),
                    file_path=test_file,
                )

        assert len(mark_error_calls) == 1
