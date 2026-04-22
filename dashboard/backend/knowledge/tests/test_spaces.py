"""Tests for knowledge/spaces.py.

Unit tests use mocked SQLAlchemy engine.
Postgres integration tests require KNOWLEDGE_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestSpacesCRUDUnit:
    def _patch(self, mock_engine):
        _add_backend()
        from knowledge import spaces
        return (
            patch.object(spaces, "get_dsn", return_value="postgresql://test"),
            patch.object(spaces, "get_engine", return_value=mock_engine),
        )

    def test_create_space_returns_dict(self):
        _add_backend()
        from knowledge import spaces

        mock_engine, mock_conn = _make_mock_engine()
        space_data = {
            "id": str(uuid.uuid4()),
            "slug": "test-space",
            "name": "Test Space",
            "description": None,
            "owner_id": None,
            "visibility": "private",
            "access_rules": "{}",
            "content_type_boosts": "{}",
            "created_at": "2024-01-01T00:00:00Z",
        }
        mock_conn.execute.return_value.fetchone.return_value = _mock_row(space_data)

        with patch.object(spaces, "get_dsn", return_value="postgresql://test"), \
             patch.object(spaces, "get_engine", return_value=mock_engine):
            result = spaces.create_space("conn-1", {"slug": "test-space", "name": "Test Space"})

        assert result["slug"] == "test-space"
        assert result["name"] == "Test Space"

    def test_list_spaces_no_filters(self):
        _add_backend()
        from knowledge import spaces

        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchall.return_value = [
            _mock_row({"id": str(uuid.uuid4()), "slug": "s1", "name": "S1",
                        "access_rules": "{}", "content_type_boosts": "{}",
                        "owner_id": None, "visibility": "private", "description": None, "created_at": "2024-01-01"})
        ]

        with patch.object(spaces, "get_dsn", return_value="postgresql://test"), \
             patch.object(spaces, "get_engine", return_value=mock_engine):
            result = spaces.list_spaces("conn-1")

        assert len(result) == 1
        assert result[0]["slug"] == "s1"

    def test_get_space_not_found_returns_none(self):
        _add_backend()
        from knowledge import spaces

        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchone.return_value = None

        with patch.object(spaces, "get_dsn", return_value="postgresql://test"), \
             patch.object(spaces, "get_engine", return_value=mock_engine):
            result = spaces.get_space("conn-1", str(uuid.uuid4()))

        assert result is None

    def test_delete_space_returns_false_if_not_found(self):
        _add_backend()
        from knowledge import spaces

        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.rowcount = 0

        with patch.object(spaces, "get_dsn", return_value="postgresql://test"), \
             patch.object(spaces, "get_engine", return_value=mock_engine):
            result = spaces.delete_space("conn-1", str(uuid.uuid4()))

        assert result is False

    def test_delete_space_returns_true_if_deleted(self):
        _add_backend()
        from knowledge import spaces

        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.rowcount = 1

        with patch.object(spaces, "get_dsn", return_value="postgresql://test"), \
             patch.object(spaces, "get_engine", return_value=mock_engine):
            result = spaces.delete_space("conn-1", str(uuid.uuid4()))

        assert result is True


# ---------------------------------------------------------------------------
# Integration tests (requires Postgres)
# ---------------------------------------------------------------------------

@pytest.mark.requires_postgres
class TestSpacesIntegration:
    def _conn_id(self, pg_dsn):
        """Use pg_dsn as a fake connection_id mapped to the test engine."""
        return "__test__"

    def _patch_engine(self, pg_dsn):
        _add_backend()
        from knowledge import spaces
        from sqlalchemy import create_engine
        engine = create_engine(pg_dsn)
        return (
            patch.object(spaces, "get_dsn", return_value=pg_dsn),
            patch.object(spaces, "get_engine", return_value=engine),
        )

    def test_create_get_delete(self, pg_full_schema):
        _add_backend()
        from knowledge import spaces
        from sqlalchemy import create_engine

        dsn = os.environ["KNOWLEDGE_TEST_DATABASE_URL"]
        engine = create_engine(dsn)

        with patch.object(spaces, "get_dsn", return_value=dsn), \
             patch.object(spaces, "get_engine", return_value=engine):

            created = spaces.create_space("test", {
                "slug": f"integration-test-{uuid.uuid4().hex[:8]}",
                "name": "Integration Test Space",
                "visibility": "public",
            })
            assert created["name"] == "Integration Test Space"

            fetched = spaces.get_space("test", created["id"])
            assert fetched is not None
            assert fetched["id"] == created["id"]

            deleted = spaces.delete_space("test", created["id"])
            assert deleted is True

            after_delete = spaces.get_space("test", created["id"])
            assert after_delete is None

    def test_update_space(self, pg_full_schema):
        _add_backend()
        from knowledge import spaces
        from sqlalchemy import create_engine

        dsn = os.environ["KNOWLEDGE_TEST_DATABASE_URL"]
        engine = create_engine(dsn)

        with patch.object(spaces, "get_dsn", return_value=dsn), \
             patch.object(spaces, "get_engine", return_value=engine):

            created = spaces.create_space("test", {
                "slug": f"upd-test-{uuid.uuid4().hex[:8]}",
                "name": "Before Update",
            })

            updated = spaces.update_space("test", created["id"], {"name": "After Update"})
            assert updated["name"] == "After Update"

            # Cleanup
            spaces.delete_space("test", created["id"])
