"""Tests for knowledge/units.py."""

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


class TestUnitsUnit:
    def test_create_unit_returns_dict(self):
        _add_backend()
        from knowledge import units

        mock_engine, mock_conn = _make_mock_engine()
        unit_data = {
            "id": str(uuid.uuid4()),
            "space_id": str(uuid.uuid4()),
            "parent_id": None,
            "name": "Chapter 1",
            "description": None,
            "sort_order": 0,
            "metadata": "{}",
            "created_at": "2024-01-01",
        }
        mock_conn.execute.return_value.fetchone.return_value = _mock_row(unit_data)

        with patch.object(units, "get_dsn", return_value="postgresql://test"), \
             patch.object(units, "get_engine", return_value=mock_engine):
            result = units.create_unit("conn-1", {
                "space_id": unit_data["space_id"],
                "name": "Chapter 1",
                "slug": "chapter-1",
            })

        assert result["name"] == "Chapter 1"

    def test_list_units_returns_list(self):
        _add_backend()
        from knowledge import units

        mock_engine, mock_conn = _make_mock_engine()
        space_id = str(uuid.uuid4())
        rows = [
            _mock_row({"id": str(uuid.uuid4()), "space_id": space_id, "parent_id": None,
                        "name": "U1", "description": None, "sort_order": 0,
                        "metadata": "{}", "created_at": "2024-01-01"}),
        ]
        mock_conn.execute.return_value.fetchall.return_value = rows

        with patch.object(units, "get_dsn", return_value="postgresql://test"), \
             patch.object(units, "get_engine", return_value=mock_engine):
            result = units.list_units("conn-1", space_id)

        assert len(result) == 1
        assert result[0]["name"] == "U1"

    def test_get_unit_not_found(self):
        _add_backend()
        from knowledge import units

        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.fetchone.return_value = None

        with patch.object(units, "get_dsn", return_value="postgresql://test"), \
             patch.object(units, "get_engine", return_value=mock_engine):
            result = units.get_unit("conn-1", str(uuid.uuid4()))

        assert result is None

    def test_delete_unit_returns_bool(self):
        _add_backend()
        from knowledge import units

        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.return_value.rowcount = 1

        with patch.object(units, "get_dsn", return_value="postgresql://test"), \
             patch.object(units, "get_engine", return_value=mock_engine):
            result = units.delete_unit("conn-1", str(uuid.uuid4()))

        assert result is True

    def test_reorder_units_calls_updates(self):
        _add_backend()
        from knowledge import units

        mock_engine, mock_conn = _make_mock_engine()
        space_id = str(uuid.uuid4())
        ordered = [str(uuid.uuid4()), str(uuid.uuid4())]
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch.object(units, "get_dsn", return_value="postgresql://test"), \
             patch.object(units, "get_engine", return_value=mock_engine):
            units.reorder_units("conn-1", space_id, ordered)

        # Should have called execute at least len(ordered) times for UPDATEs
        assert mock_conn.execute.call_count >= len(ordered)


@pytest.mark.requires_postgres
class TestUnitsIntegration:
    def test_create_list_delete(self, pg_full_schema):
        _add_backend()
        from knowledge import spaces, units
        from sqlalchemy import create_engine

        dsn = os.environ["KNOWLEDGE_TEST_DATABASE_URL"]
        engine = create_engine(dsn)

        with patch.object(spaces, "get_dsn", return_value=dsn), \
             patch.object(spaces, "get_engine", return_value=engine), \
             patch.object(units, "get_dsn", return_value=dsn), \
             patch.object(units, "get_engine", return_value=engine):

            # Create space first
            space = spaces.create_space("test", {
                "slug": f"unit-test-{uuid.uuid4().hex[:8]}",
                "name": "Unit Test Space",
            })

            unit = units.create_unit("test", {
                "space_id": space["id"],
                "title": "Module 1",
                "slug": f"module-{uuid.uuid4().hex[:8]}",
                "sort_order": 0,
            })
            assert unit["title"] == "Module 1"

            listed = units.list_units("test", space["id"])
            assert any(u["id"] == unit["id"] for u in listed)

            units.delete_unit("test", unit["id"])
            spaces.delete_space("test", space["id"])
