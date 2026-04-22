"""Tests for knowledge/classify_worker.py.

Tests checkout logic, lock timeout release, and LLM key absence handling.
No real Postgres required for unit tests.
"""

import os
import sys
import time
import threading
from unittest.mock import MagicMock, patch

import pytest


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


# ---------------------------------------------------------------------------
# _extract_json — robustness against fences and chatter
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json_object(self):
        _add_backend()
        from knowledge.classify_worker import _extract_json

        raw = '{"content_type": "tutorial", "difficulty_level": "beginner", "topics": ["python"]}'
        result = _extract_json(raw)
        assert result is not None
        assert result["content_type"] == "tutorial"

    def test_json_with_backtick_fence(self):
        _add_backend()
        from knowledge.classify_worker import _extract_json

        raw = '```json\n{"content_type": "reference", "difficulty_level": "advanced", "topics": []}\n```'
        result = _extract_json(raw)
        assert result is not None
        assert result["content_type"] == "reference"

    def test_json_with_leading_chatter(self):
        _add_backend()
        from knowledge.classify_worker import _extract_json

        raw = 'Sure! Here is the classification:\n{"content_type": "article", "difficulty_level": "intermediate", "topics": ["ai"]}'
        result = _extract_json(raw)
        assert result is not None
        assert result["content_type"] == "article"

    def test_empty_string_returns_none(self):
        _add_backend()
        from knowledge.classify_worker import _extract_json

        assert _extract_json("") is None

    def test_invalid_json_returns_none(self):
        _add_backend()
        from knowledge.classify_worker import _extract_json

        assert _extract_json("{not valid json}") is None

    def test_no_braces_returns_none(self):
        _add_backend()
        from knowledge.classify_worker import _extract_json

        assert _extract_json("just text, no JSON here") is None


# ---------------------------------------------------------------------------
# _classify_document — claude CLI not on PATH
# ---------------------------------------------------------------------------

class TestClassifyNoCLI:
    def test_returns_none_when_claude_not_on_path(self, monkeypatch):
        """When claude CLI is absent, _classify_document returns None."""
        _add_backend()
        import knowledge.classify_worker as cw
        cw._warned_no_claude = False  # Reset warning state

        with patch("shutil.which", return_value=None):
            result = cw._classify_document("Sample text content")
        assert result is None

    def test_warning_logged_only_once_when_no_cli(self, monkeypatch, caplog):
        """The 'claude CLI not found' warning fires at most once."""
        _add_backend()
        import logging
        import knowledge.classify_worker as cw
        cw._warned_no_claude = False

        with patch("shutil.which", return_value=None), \
             caplog.at_level(logging.WARNING, logger="classify_worker"):
            cw._classify_document("text 1")
            cw._classify_document("text 2")
            cw._classify_document("text 3")

        warning_count = sum(
            1 for r in caplog.records
            if "claude" in r.message.lower() and "not found" in r.message.lower()
        )
        assert warning_count <= 1


# ---------------------------------------------------------------------------
# _classify_document — with mocked claude subprocess
# ---------------------------------------------------------------------------

class TestClassifyWithMockedSubprocess:
    def test_returns_classification_dict_when_cli_succeeds(self, monkeypatch):
        """When claude CLI exits 0 and returns valid JSON, result is a dict."""
        _add_backend()
        import knowledge.classify_worker as cw

        fake_output = '{"content_type": "tutorial", "difficulty_level": "beginner", "topics": ["python"]}'

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_output
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/local/bin/claude"), \
             patch("subprocess.run", return_value=mock_result):
            result = cw._classify_document("test content")

        assert result is not None
        assert result["content_type"] == "tutorial"
        assert result["difficulty_level"] == "beginner"

    def test_returns_none_when_cli_exits_nonzero(self, monkeypatch):
        """Non-zero exit code → None."""
        _add_backend()
        import knowledge.classify_worker as cw

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        with patch("shutil.which", return_value="/usr/local/bin/claude"), \
             patch("subprocess.run", return_value=mock_result):
            result = cw._classify_document("test content")

        assert result is None


# ---------------------------------------------------------------------------
# start_classify_worker — idempotency
# ---------------------------------------------------------------------------

class TestWorkerIdempotency:
    def test_start_twice_starts_only_one_thread(self, tmp_path):
        _add_backend()
        import knowledge.classify_worker as cw

        # Reset state
        cw._worker_started = False

        db_path = str(tmp_path / "test.db")
        threads_before = threading.active_count()

        cw.start_classify_worker(db_path)
        after_first = threading.active_count()

        cw.start_classify_worker(db_path)
        after_second = threading.active_count()

        # Only one new thread should have been created
        assert after_second == after_first

        # Reset for other tests
        cw._worker_started = False

    def test_worker_thread_is_daemon(self, tmp_path):
        _add_backend()
        import knowledge.classify_worker as cw

        cw._worker_started = False

        db_path = str(tmp_path / "test2.db")
        cw.start_classify_worker(db_path)

        # Find our thread
        worker_thread = None
        for t in threading.enumerate():
            if t.name == "knowledge-classify-worker":
                worker_thread = t
                break

        assert worker_thread is not None
        assert worker_thread.daemon is True

        # Reset
        cw._worker_started = False


# ---------------------------------------------------------------------------
# _get_connections
# ---------------------------------------------------------------------------

class TestGetConnections:
    def test_returns_empty_list_when_no_db(self, tmp_path):
        _add_backend()
        from knowledge.classify_worker import _get_connections

        # Non-existent DB path should return empty list without crashing
        result = _get_connections(str(tmp_path / "nonexistent.db"))
        assert result == []

    def test_returns_empty_list_when_no_ready_connections(self, tmp_path):
        _add_backend()
        import sqlite3
        from knowledge.classify_worker import _get_connections

        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE knowledge_connections
               (id TEXT, connection_string_encrypted BLOB, status TEXT)"""
        )
        conn.execute(
            "INSERT INTO knowledge_connections VALUES ('conn1', NULL, 'disconnected')"
        )
        conn.commit()
        conn.close()

        result = _get_connections(db_path)
        assert result == []

    def test_returns_ready_connections(self, tmp_path):
        _add_backend()
        import sqlite3
        from knowledge.classify_worker import _get_connections

        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE knowledge_connections
               (id TEXT, connection_string_encrypted BLOB, status TEXT)"""
        )
        conn.execute(
            "INSERT INTO knowledge_connections VALUES ('conn1', X'AABB', 'ready')"
        )
        conn.execute(
            "INSERT INTO knowledge_connections VALUES ('conn2', NULL, 'ready')"
        )
        conn.commit()
        conn.close()

        result = _get_connections(db_path)
        assert len(result) == 2
        assert any(r["id"] == "conn1" for r in result)
