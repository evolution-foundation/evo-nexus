"""Tests for knowledge/documents.py (CRUD + async upload)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


def _make_mock_engine():
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute = MagicMock()
    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_conn
    mock_engine.connect.return_value = mock_conn
    return mock_engine, mock_conn


def _mock_row(data: dict):
    row = MagicMock()
    row._mapping = data
    return row


class TestDocumentsCRUDUnit:
    def _make_doc_row(self, space_id: str = None) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "space_id": space_id or str(uuid.uuid4()),
            "unit_id": None,
            "title": "Test Doc",
            "description": None,
            "tags": "[]",
            "owner_id": None,
            "source_uri": "/tmp/test.pdf",
            "mime_type": None,
            "size_bytes": 1024,
            "metadata": "{}",
            "status": "pending",
            "content_type": None,
            "error_message": None,
            "created_at": "2024-01-01",
            "indexed_at": None,
        }

    def test_get_document_not_found(self):
        _add_backend()
        from knowledge import documents

        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchone.return_value = None

        with patch.object(documents, "get_dsn", return_value="postgresql://test"), \
             patch.object(documents, "get_engine", return_value=mock_engine):
            result = documents.get_document("conn-1", str(uuid.uuid4()))

        assert result is None

    def test_delete_document_returns_true(self):
        _add_backend()
        from knowledge import documents

        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.rowcount = 1

        with patch.object(documents, "get_dsn", return_value="postgresql://test"), \
             patch.object(documents, "get_engine", return_value=mock_engine):
            result = documents.delete_document("conn-1", str(uuid.uuid4()))

        assert result is True

    def test_list_documents_with_filters(self):
        _add_backend()
        from knowledge import documents

        mock_engine, mock_conn = _make_mock_engine()
        space_id = str(uuid.uuid4())
        mock_conn.execute.return_value.fetchall.return_value = [
            _mock_row(self._make_doc_row(space_id))
        ]

        with patch.object(documents, "get_dsn", return_value="postgresql://test"), \
             patch.object(documents, "get_engine", return_value=mock_engine):
            result = documents.list_documents("conn-1", space_id, status="pending")

        assert len(result) == 1

    def test_upload_document_spawns_worker(self, tmp_path):
        _add_backend()
        from knowledge import documents

        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"%PDF-1.4 minimal")

        space_id = str(uuid.uuid4())
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchone.return_value = _mock_row(
            self._make_doc_row(space_id)
        )

        popen_calls = []

        class FakePopen:
            def __init__(self, *args, **kwargs):
                popen_calls.append((args, kwargs))
                from unittest.mock import MagicMock
                self.stdin = MagicMock()

        with patch.object(documents, "get_dsn", return_value="postgresql://test"), \
             patch.object(documents, "get_engine", return_value=mock_engine), \
             patch("knowledge.documents.subprocess.Popen", FakePopen):
            doc = documents.upload_document("conn-1", space_id, test_file)

        assert doc is not None
        assert len(popen_calls) == 1
        # Worker script path should contain _knowledge_worker
        cmd = popen_calls[0][0][0]
        assert "_knowledge_worker" in str(cmd[1])

    def test_get_ingestion_status_missing_returns_none(self):
        _add_backend()
        from knowledge import documents

        result = documents.get_ingestion_status(str(uuid.uuid4()))
        assert result is None

    def test_get_ingestion_status_reads_file(self, tmp_path):
        _add_backend()
        from knowledge import documents

        doc_id = str(uuid.uuid4())
        status_data = {"document_id": doc_id, "phase": "storing"}
        status_file = tmp_path / f"{doc_id}.json"
        status_file.write_text(json.dumps(status_data))

        with patch.object(documents, "_status_dir", return_value=tmp_path):
            result = documents.get_ingestion_status(doc_id)

        assert result["phase"] == "storing"


@pytest.mark.requires_postgres
class TestDocumentsIntegration:
    def test_create_list_delete(self, pg_full_schema, tmp_path):
        _add_backend()
        from knowledge import spaces, documents
        from sqlalchemy import create_engine

        dsn = os.environ["KNOWLEDGE_TEST_DATABASE_URL"]
        engine = create_engine(dsn)
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello knowledge base")

        with patch.object(spaces, "get_dsn", return_value=dsn), \
             patch.object(spaces, "get_engine", return_value=engine), \
             patch.object(documents, "get_dsn", return_value=dsn), \
             patch.object(documents, "get_engine", return_value=engine), \
             patch("knowledge.documents.subprocess.Popen", MagicMock()):

            space = spaces.create_space("test", {
                "slug": f"doc-test-{uuid.uuid4().hex[:8]}",
                "name": "Doc Test Space",
            })

            doc = documents.upload_document("test", str(space["id"]), test_file)
            assert doc["status"] == "pending"
            assert doc["space_id"] == space["id"]

            listed = documents.list_documents("test", space["id"])
            assert any(d["id"] == doc["id"] for d in listed)

            fetched = documents.get_document("test", doc["id"])
            assert fetched["id"] == doc["id"]

            documents.delete_document("test", doc["id"])
            spaces.delete_space("test", space["id"])
