"""Tests for heartbeat runner — timeout, idempotence, step protocol."""

import json
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """In-memory SQLite DB with heartbeat tables + a seed heartbeat."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE heartbeats (
            id TEXT PRIMARY KEY,
            agent TEXT NOT NULL,
            interval_seconds INTEGER NOT NULL DEFAULT 3600,
            max_turns INTEGER NOT NULL DEFAULT 10,
            timeout_seconds INTEGER NOT NULL DEFAULT 600,
            lock_timeout_seconds INTEGER NOT NULL DEFAULT 1800,
            wake_triggers TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            goal_id TEXT,
            required_secrets TEXT DEFAULT '[]',
            decision_prompt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE heartbeat_runs (
            run_id TEXT PRIMARY KEY,
            heartbeat_id TEXT NOT NULL,
            trigger_id TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_ms INTEGER,
            tokens_in INTEGER,
            tokens_out INTEGER,
            cost_usd REAL,
            status TEXT NOT NULL DEFAULT 'running',
            prompt_preview TEXT,
            error TEXT,
            triggered_by TEXT,
            decision_action TEXT,
            decision_json TEXT,
            provider TEXT,
            resolved_profile TEXT,
            stdout_tail TEXT,
            stderr_tail TEXT,
            runtime_run_id TEXT
        );
        CREATE TABLE heartbeat_triggers (
            id TEXT PRIMARY KEY,
            heartbeat_id TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            payload TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            consumed_at TEXT,
            coalesced_into TEXT
        );
    """)
    now = _now_iso()
    conn.execute("""
        INSERT INTO heartbeats VALUES (
            'atlas-4h', 'atlas-project', 14400, 10, 5, 1800,
            '["interval","manual"]', 1, null, '[]',
            'You are Atlas. Decide: act or skip?', ?, ?
        )
    """, (now, now))
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Step 1: Load identity
# ---------------------------------------------------------------------------

def test_step1_load_identity_exists():
    """step1_load_identity should return content for atlas-project."""
    from heartbeat_runner import step1_load_identity

    content = step1_load_identity("atlas-project")
    assert len(content) > 0
    assert isinstance(content, str)


def test_step1_load_identity_missing_raises():
    """step1_load_identity should raise FileNotFoundError for nonexistent agent."""
    from heartbeat_runner import step1_load_identity

    with pytest.raises(FileNotFoundError):
        step1_load_identity("nonexistent-agent-xyz-abc")


# ---------------------------------------------------------------------------
# Step 7: Timeout enforcement
# ---------------------------------------------------------------------------

def test_step7_timeout_hard_kill():
    """Subprocess exceeding timeout_seconds must be killed with status=timeout."""
    from heartbeat_runner import step7_invoke_claude

    # Patch subprocess to simulate a slow process
    import subprocess as _subprocess

    class _SlowProc:
        pid = 99999
        returncode = -9

        def communicate(self, timeout=None):
            if timeout is not None and timeout < 30:
                raise _subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
            return ("", "")

        def kill(self):
            pass

    with patch("subprocess.Popen", return_value=_SlowProc()):
        with patch("os.killpg", return_value=None):
            result = step7_invoke_claude(
                agent="atlas-project",
                prompt="Test prompt",
                max_turns=5,
                timeout_seconds=2,  # very short
            )

    assert result["status"] == "timeout"
    assert result["error"] is not None
    assert "timeout" in result["error"].lower() or "Killed" in result["error"]


def test_step7_success_path():
    """Successful subprocess should return status=success."""
    from heartbeat_runner import step7_invoke_claude

    class _FastProc:
        pid = 1234
        returncode = 0

        def communicate(self, timeout=None):
            return ("Agent decided to skip: no urgent tasks.", "")

    with patch("subprocess.Popen", return_value=_FastProc()):
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = step7_invoke_claude(
                agent="atlas-project",
                prompt="Test prompt",
                max_turns=5,
                timeout_seconds=60,
            )

    assert result["status"] == "success"
    assert "Agent decided" in result["output"]


def test_step7_nonzero_exit_is_fail():
    """Non-zero exit code should return status=fail."""
    from heartbeat_runner import step7_invoke_claude

    class _FailProc:
        pid = 1234
        returncode = 1

        def communicate(self, timeout=None):
            return ("", "Something went wrong.")

    with patch("subprocess.Popen", return_value=_FailProc()):
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = step7_invoke_claude(
                agent="atlas-project",
                prompt="Test prompt",
                max_turns=5,
                timeout_seconds=60,
            )

    assert result["status"] == "fail"
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# Step 8: Idempotence (crash recovery)
# ---------------------------------------------------------------------------

def test_step8_idempotent_no_duplicate(tmp_db):
    """Calling step8_persist twice with same run_id should not create duplicates."""
    from heartbeat_runner import step8_persist

    run_id = str(uuid.uuid4())
    result = {
        "status": "success",
        "duration_ms": 1500,
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_usd": 0.001,
        "error": None,
        "agent": "atlas-project",
        "started_at": _now_iso(),
    }

    from sqlalchemy import create_engine
    sa_engine = create_engine(f"sqlite:///{tmp_db}", connect_args={"check_same_thread": False})
    sa_conn = sa_engine.connect()

    # First persist
    step8_persist(run_id, "atlas-4h", result, None, "manual", "test prompt", sa_conn)

    # Second persist (simulating restart after crash)
    step8_persist(run_id, "atlas-4h", result, None, "manual", "test prompt", sa_conn)
    sa_conn.close()

    verify = sqlite3.connect(str(tmp_db))
    count = verify.execute(
        "SELECT COUNT(*) FROM heartbeat_runs WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    verify.close()

    assert count == 1, f"Expected 1 run, got {count} (duplicate inserted)"


def test_step8_already_finalized_skips_update(tmp_db):
    """If run already has a final status (not 'running'), step8 should not overwrite."""
    from heartbeat_runner import step8_persist

    run_id = str(uuid.uuid4())
    now = _now_iso()

    setup_conn = sqlite3.connect(str(tmp_db))
    setup_conn.row_factory = sqlite3.Row

    # Pre-insert a finalized run
    setup_conn.execute(
        """INSERT INTO heartbeat_runs
           (run_id, heartbeat_id, started_at, status, triggered_by)
           VALUES (?, 'atlas-4h', ?, 'success', 'manual')""",
        (run_id, now),
    )
    setup_conn.commit()
    setup_conn.close()

    from sqlalchemy import create_engine
    sa_engine = create_engine(f"sqlite:///{tmp_db}", connect_args={"check_same_thread": False})
    sa_conn = sa_engine.connect()

    # Try to "persist" a failure result — should be ignored
    result = {"status": "fail", "error": "late write", "duration_ms": 0, "agent": "atlas-project"}
    step8_persist(run_id, "atlas-4h", result, None, "manual", "late", sa_conn)
    sa_conn.close()

    verify = sqlite3.connect(str(tmp_db))
    verify.row_factory = sqlite3.Row
    row = verify.execute(
        "SELECT status FROM heartbeat_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    verify.close()

    # Status should still be 'success' (not overwritten by 'fail')
    assert row["status"] == "success"


# ---------------------------------------------------------------------------
# Atomic checkout
# ---------------------------------------------------------------------------

def test_step5_checkout_with_no_task_always_succeeds():
    """step5 with task_id=None should always return True (no lock to contend)."""
    from heartbeat_runner import step5_atomic_checkout

    # Minimal DB with tasks table (empty)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tasks (id TEXT, locked_at TEXT, locked_by TEXT, status TEXT)")
    conn.commit()

    result = step5_atomic_checkout(None, "run-123", 1800, conn)
    assert result is True


def test_step5_checkout_persists_configured_lock_timeout():
    from heartbeat_runner import step5_atomic_checkout

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE tickets ("
        "id TEXT PRIMARY KEY, locked_at TEXT, locked_by TEXT, "
        "lock_timeout_seconds INTEGER)"
    )
    conn.execute("INSERT INTO tickets (id) VALUES ('ticket-1')")
    conn.commit()

    acquired = step5_atomic_checkout("ticket-1", "run-123", 7200, conn)
    locked_by, lock_timeout = conn.execute(
        "SELECT locked_by, lock_timeout_seconds FROM tickets WHERE id = 'ticket-1'"
    ).fetchone()

    assert acquired is True
    assert locked_by == "run-123"
    assert lock_timeout == 7200


# ---------------------------------------------------------------------------
# JSONL log
# ---------------------------------------------------------------------------

def test_step8_writes_jsonl_log(tmp_db, tmp_path):
    """step8 should append a valid JSON line to the JSONL log file."""
    from heartbeat_runner import step8_persist
    import heartbeat_runner as runner_mod

    run_id = str(uuid.uuid4())
    result = {
        "status": "success",
        "duration_ms": 2000,
        "error": None,
        "agent": "atlas-project",
        "started_at": _now_iso(),
        "tokens_in": None,
        "tokens_out": None,
        "cost_usd": None,
    }

    # Patch LOGS_DIR to use temp path
    original_logs_dir = runner_mod.LOGS_DIR
    runner_mod.LOGS_DIR = tmp_path / "heartbeats"
    try:
        from sqlalchemy import create_engine
        sa_engine = create_engine(f"sqlite:///{tmp_db}", connect_args={"check_same_thread": False})
        sa_conn = sa_engine.connect()
        step8_persist(run_id, "atlas-4h", result, None, "interval", "test", sa_conn)
        sa_conn.close()

        log_files = list((tmp_path / "heartbeats").glob("atlas-4h-*.jsonl"))
        assert len(log_files) >= 1

        lines = log_files[0].read_text().strip().splitlines()
        assert len(lines) >= 1

        entry = json.loads(lines[-1])
        assert entry["run_id"] == run_id
        assert entry["status"] == "success"
        assert entry["heartbeat_id"] == "atlas-4h"
        assert "ts" in entry
    finally:
        runner_mod.LOGS_DIR = original_logs_dir
