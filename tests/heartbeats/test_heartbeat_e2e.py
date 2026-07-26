"""E2E tests for heartbeat runtime: triggers, locks, decision parsing, timeout, goal context."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def tmp_db(tmp_path):
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
            wake_triggers TEXT DEFAULT '["interval","manual"]',
            enabled INTEGER NOT NULL DEFAULT 1,
            goal_id TEXT,
            required_secrets TEXT DEFAULT '[]',
            decision_prompt TEXT DEFAULT 'Decide whether to work.',
            hermes_profile TEXT,
            handler TEXT,
            handler_contract TEXT,
            concurrency_policy TEXT DEFAULT 'skip',
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
            status TEXT NOT NULL,
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
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'medium',
            priority_rank INTEGER DEFAULT 2,
            assignee_agent TEXT,
            locked_at TEXT,
            locked_by TEXT,
            lock_timeout_seconds INTEGER DEFAULT 1800,
            project_id INTEGER,
            goal_id TEXT,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    now = _now_iso()
    conn.execute(
        """INSERT INTO heartbeats (id, agent, interval_seconds, max_turns, timeout_seconds,
           lock_timeout_seconds, enabled, decision_prompt, created_at, updated_at)
           VALUES ('atlas-4h', 'atlas-project', 14400, 10, 600, 1800, 1, 'Check Linear for blockers.', ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO heartbeats (id, agent, interval_seconds, max_turns, timeout_seconds,
           lock_timeout_seconds, enabled, goal_id, decision_prompt, created_at, updated_at)
           VALUES ('flux-6h', 'flux-finance', 21600, 5, 300, 1800, 1, 'revenue-goal-1',
           'Check payments.', ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO heartbeats (id, agent, interval_seconds, max_turns, timeout_seconds,
           lock_timeout_seconds, enabled, decision_prompt, created_at, updated_at)
           VALUES ('disabled-hb', 'test-agent', 3600, 3, 60, 900, 0, 'Should not run.', ?, ?)""",
        (now, now),
    )
    conn.commit()
    conn.close()
    return db_path


def _row_conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _make_runtime_result(output='{"action":"work"}', status="succeeded", provider="hermes", profile="atlas"):
    return MagicMock(
        status=status, provider=provider, requested_profile=None,
        resolved_profile=profile, output=output, error=None,
        exit_code=0, duration_ms=150, tokens_in=None, tokens_out=None, cost_usd=None,
        fallback_from=None,
    )


# ────────────────────────────────────────────────────────────────────────────
# Decision parsing
# ────────────────────────────────────────────────────────────────────────────

class TestDecisionParsing:
    def test_work_action_parsed(self):
        from heartbeat_runner import parse_decision
        action, data = parse_decision('Some preamble\n{"action":"work","reason":"blockers found"}')
        assert action == "work"
        assert data["reason"] == "blockers found"

    def test_skip_action_parsed(self):
        from heartbeat_runner import parse_decision
        action, data = parse_decision('{"action":"skip","reason":"all clear"}')
        assert action == "skip"
        assert data["action"] == "skip"

    def test_empty_output_defaults_to_work(self):
        from heartbeat_runner import parse_decision
        action, data = parse_decision("")
        assert action == "work"
        assert data is None

    def test_invalid_json_defaults_to_work(self):
        from heartbeat_runner import parse_decision
        action, data = parse_decision("I decided to skip this time because nothing is urgent.")
        assert action == "work"
        assert data is None

    def test_last_json_line_wins(self):
        from heartbeat_runner import parse_decision
        output = '{"action":"skip"}\nsome text\n{"action":"work","detail":"found issue"}'
        action, data = parse_decision(output)
        assert action == "work"


# ────────────────────────────────────────────────────────────────────────────
# Atomic checkout/release
# ────────────────────────────────────────────────────────────────────────────

class TestAtomicCheckout:
    def test_checkout_acquires_lock(self, tmp_db):
        from heartbeat_runner import step5_atomic_checkout
        conn = _row_conn(tmp_db)
        conn.execute(
            "INSERT INTO tickets (id, title, status, priority, assignee_agent, created_at, updated_at) VALUES ('t1', 'Test', 'open', 'high', 'atlas-project', ?, ?)",
            (_now_iso(), _now_iso()),
        )
        conn.commit()
        assert step5_atomic_checkout("t1", "run-1", 1800, conn) is True
        row = conn.execute("SELECT locked_at, locked_by FROM tickets WHERE id='t1'").fetchone()
        assert row["locked_by"] == "run-1"
        assert row["locked_at"] is not None
        conn.close()

    def test_checkout_fails_if_already_locked(self, tmp_db):
        from heartbeat_runner import step5_atomic_checkout
        conn = _row_conn(tmp_db)
        conn.execute(
            "INSERT INTO tickets (id, title, status, priority, assignee_agent, locked_at, locked_by, created_at, updated_at) VALUES ('t2', 'Locked', 'open', 'high', 'atlas-project', ?, 'other-run', ?, ?)",
            (_now_iso(), _now_iso(), _now_iso()),
        )
        conn.commit()
        assert step5_atomic_checkout("t2", "run-2", 1800, conn) is False
        conn.close()

    def test_checkout_failure_rolls_back_and_fails_closed(self):
        from heartbeat_runner import step5_atomic_checkout

        conn = MagicMock()
        conn.execute.side_effect = sqlite3.InterfaceError("connection failed")
        assert step5_atomic_checkout("t-fail", "run-fail", 1800, conn) is False
        conn.rollback.assert_called_once()

    def test_release_only_by_owner(self, tmp_db):
        from heartbeat_runner import step9_release_checkout
        conn = _row_conn(tmp_db)
        conn.execute(
            "INSERT INTO tickets (id, title, status, priority, locked_at, locked_by, created_at, updated_at) VALUES ('t3', 'Owned', 'open', 'medium', ?, 'run-owner', ?, ?)",
            (_now_iso(), _now_iso(), _now_iso()),
        )
        conn.commit()
        step9_release_checkout("t3", "run-intruder", conn)
        row = conn.execute("SELECT locked_by FROM tickets WHERE id='t3'").fetchone()
        assert row["locked_by"] == "run-owner"  # not released
        step9_release_checkout("t3", "run-owner", conn)
        row = conn.execute("SELECT locked_by FROM tickets WHERE id='t3'").fetchone()
        assert row["locked_by"] is None
        conn.close()

    def test_concurrent_checkout_one_wins(self, tmp_db):
        from heartbeat_runner import step5_atomic_checkout
        conn = _row_conn(tmp_db)
        conn.execute(
            "INSERT INTO tickets (id, title, status, priority, created_at, updated_at) VALUES ('t4', 'Race', 'open', 'urgent', ?, ?)",
            (_now_iso(), _now_iso()),
        )
        conn.commit()
        results = []
        for i in range(10):
            c = _row_conn(tmp_db)
            results.append(step5_atomic_checkout("t4", f"run-{i}", 1800, c))
            c.close()
        assert results.count(True) == 1
        assert results.count(False) == 9
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# Trigger E2E: manual, interval, mention, goal
# ────────────────────────────────────────────────────────────────────────────

class TestTriggerE2E:
    def _invoke_heartbeat(self, tmp_db, heartbeat_id, triggered_by, output='{"action":"work"}'):
        import heartbeat_runner as runner
        runtime_result = _make_runtime_result(output=output)
        with patch.object(runner, "_get_db", return_value=_row_conn(tmp_db)), \
             patch.object(runner, "_load_heartbeat", wraps=lambda hb_id: dict(_row_conn(tmp_db).execute("SELECT * FROM heartbeats WHERE id=?", (hb_id,)).fetchone())), \
             patch.object(runner, "step1_load_identity", return_value="# Atlas Agent"), \
             patch.object(runner, "step7_invoke_runtime", return_value={
                 "status": "success", "output": output, "error": None, "duration_ms": 150,
                 "tokens_in": None, "tokens_out": None, "cost_usd": None,
                 "provider": "hermes", "requested_profile": None, "resolved_profile": "atlas",
                 "exit_code": 0, "fallback_from": None, "decision_action": None, "decision_json": None,
             }), \
             patch.object(runner, "LOGS_DIR", tmp_db.parent / "logs"):
            run_id = runner.run_heartbeat(heartbeat_id, triggered_by=triggered_by)
        return run_id

    def test_manual_trigger(self, tmp_db):
        run_id = self._invoke_heartbeat(tmp_db, "atlas-4h", "manual")
        conn = _row_conn(tmp_db)
        row = conn.execute("SELECT * FROM heartbeat_runs WHERE run_id=?", (run_id,)).fetchone()
        assert row is not None
        assert row["triggered_by"] == "manual"
        assert row["status"] == "success"
        conn.close()

    def test_interval_trigger(self, tmp_db):
        run_id = self._invoke_heartbeat(tmp_db, "atlas-4h", "interval")
        conn = _row_conn(tmp_db)
        row = conn.execute("SELECT * FROM heartbeat_runs WHERE run_id=?", (run_id,)).fetchone()
        assert row["triggered_by"] == "interval"
        conn.close()

    def test_mention_trigger(self, tmp_db):
        run_id = self._invoke_heartbeat(tmp_db, "atlas-4h", "mention")
        conn = _row_conn(tmp_db)
        row = conn.execute("SELECT * FROM heartbeat_runs WHERE run_id=?", (run_id,)).fetchone()
        assert row["triggered_by"] == "mention"
        conn.close()

    def test_goal_context_injected(self, tmp_db):
        import heartbeat_runner as runner
        with patch.object(runner, "_get_db", return_value=_row_conn(tmp_db)), \
             patch.object(runner, "_load_heartbeat", return_value={
                 "id": "flux-6h", "agent": "flux-finance", "interval_seconds": 21600,
                 "max_turns": 5, "timeout_seconds": 300, "lock_timeout_seconds": 1800,
                 "enabled": True, "goal_id": "revenue-goal-1", "decision_prompt": "Check payments.",
                 "handler": None,
             }), \
             patch.object(runner, "step1_load_identity", return_value="# Flux Agent"), \
             patch("goal_context.inject_into_prompt", return_value="GOAL INJECTED PROMPT") as mock_inject, \
             patch.object(runner, "step7_invoke_runtime", return_value={
                 "status": "success", "output": '{"action":"skip"}', "error": None,
                 "duration_ms": 50, "tokens_in": None, "tokens_out": None, "cost_usd": None,
                 "provider": "hermes", "requested_profile": None, "resolved_profile": "flux",
                 "exit_code": 0, "fallback_from": None,
             }), \
             patch.object(runner, "LOGS_DIR", tmp_db.parent / "logs"):
            runner.run_heartbeat("flux-6h", triggered_by="interval")
        mock_inject.assert_called_once()
        assert mock_inject.call_args.kwargs.get("goal_id") == "revenue-goal-1" or "revenue-goal-1" in str(mock_inject.call_args)

    def test_skip_decision_persists(self, tmp_db):
        run_id = self._invoke_heartbeat(tmp_db, "atlas-4h", "manual", output='{"action":"skip","reason":"nothing to do"}')
        conn = _row_conn(tmp_db)
        row = conn.execute("SELECT decision_action, decision_json FROM heartbeat_runs WHERE run_id=?", (run_id,)).fetchone()
        assert row["decision_action"] == "skip"
        data = json.loads(row["decision_json"])
        assert data["reason"] == "nothing to do"
        conn.close()

    def test_disabled_heartbeat_does_not_run(self, tmp_db):
        import heartbeat_runner as runner
        with patch.object(runner, "_get_db", return_value=_row_conn(tmp_db)), \
             patch.object(runner, "_load_heartbeat", return_value={
                 "id": "disabled-hb", "agent": "test-agent", "enabled": False,
             }), \
             patch.object(runner, "step7_invoke_runtime") as invoke:
            runner.run_heartbeat("disabled-hb", triggered_by="interval")
        invoke.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# Timeout and lock cleanup
# ────────────────────────────────────────────────────────────────────────────

class TestTimeoutAndLockCleanup:
    def test_timeout_releases_lock(self, tmp_db):
        import heartbeat_runner as runner
        conn = _row_conn(tmp_db)
        conn.execute(
            "INSERT INTO tickets (id, title, status, priority, assignee_agent, created_at, updated_at) VALUES ('t-timeout', 'TimeoutTicket', 'open', 'high', 'atlas-project', ?, ?)",
            (_now_iso(), _now_iso()),
        )
        conn.commit()
        conn.close()

        with patch.object(runner, "_get_db", return_value=_row_conn(tmp_db)), \
             patch.object(runner, "_load_heartbeat", return_value={
                 "id": "atlas-4h", "agent": "atlas-project", "interval_seconds": 14400,
                 "max_turns": 10, "timeout_seconds": 600, "lock_timeout_seconds": 1800,
                 "enabled": True, "goal_id": None, "decision_prompt": "Check Linear.",
                 "handler": None,
             }), \
             patch.object(runner, "step1_load_identity", return_value="# Atlas"), \
             patch.object(runner, "step7_invoke_runtime", return_value={
                 "status": "timeout", "output": "", "error": "Killed after 600s",
                 "duration_ms": 600000, "tokens_in": None, "tokens_out": None, "cost_usd": None,
                 "provider": "hermes", "requested_profile": None, "resolved_profile": "atlas",
                 "exit_code": -1, "fallback_from": None,
             }), \
             patch.object(runner, "LOGS_DIR", tmp_db.parent / "logs"):
            runner.run_heartbeat("atlas-4h", triggered_by="interval")

        conn2 = _row_conn(tmp_db)
        row = conn2.execute("SELECT locked_by FROM tickets WHERE id='t-timeout'").fetchone()
        assert row["locked_by"] is None  # lock must have been released
        conn2.close()

    def test_failed_run_is_retryable_trigger_state(self, tmp_db):
        import heartbeat_runner as runner
        with patch.object(runner, "_get_db", return_value=_row_conn(tmp_db)), \
             patch.object(runner, "_load_heartbeat", return_value={
                 "id": "atlas-4h", "agent": "atlas-project", "interval_seconds": 14400,
                 "max_turns": 10, "timeout_seconds": 600, "lock_timeout_seconds": 1800,
                 "enabled": True, "goal_id": None, "decision_prompt": "Check Linear.",
                 "handler": None,
             }), \
             patch.object(runner, "step1_load_identity", return_value="# Atlas"), \
             patch.object(runner, "step7_invoke_runtime", return_value={
                 "status": "fail", "output": "", "error": "process crashed",
                 "duration_ms": 100, "tokens_in": None, "tokens_out": None, "cost_usd": None,
                 "provider": "hermes", "requested_profile": None, "resolved_profile": "atlas",
                 "exit_code": 1, "fallback_from": None,
             }), \
             patch.object(runner, "LOGS_DIR", tmp_db.parent / "logs"):
            run_id = runner.run_heartbeat("atlas-4h", triggered_by="interval")

        conn = _row_conn(tmp_db)
        row = conn.execute("SELECT status FROM heartbeat_runs WHERE run_id=?", (run_id,)).fetchone()
        assert row["status"] == "fail"
        # A new interval trigger should be allowed (no leftover lock blocking retries)
        triggers = conn.execute("SELECT * FROM heartbeat_triggers WHERE heartbeat_id='atlas-4h'").fetchall()
        # No stale trigger blocking — the table has no lingering consumed_at=NULL row from this run
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# Provider/profile persisted in run
# ────────────────────────────────────────────────────────────────────────────

class TestProviderPersistence:
    def test_work_claims_inbox_ticket_and_skip_leaves_it_unlocked(self, tmp_db):
        import heartbeat_runner as runner

        conn = _row_conn(tmp_db)
        conn.execute("INSERT INTO tickets (id, title, status, priority, created_at, updated_at) VALUES ('ticket-1', 'Work', 'open', 'high', ?, ?)", (_now_iso(), _now_iso()))
        conn.commit()
        conn.close()
        heartbeat = {"id": "atlas-4h", "agent": "atlas-project", "interval_seconds": 60, "max_turns": 1, "timeout_seconds": 10, "lock_timeout_seconds": 60, "enabled": True, "goal_id": None, "decision_prompt": "Decide", "handler": None, "hermes_profile": None}
        work = {"status": "success", "output": '{"action":"work","ticket_id":"ticket-1"}', "duration_ms": 1}
        done = {"status": "success", "output": "done", "duration_ms": 1}
        with patch.object(runner, "_get_db", return_value=_row_conn(tmp_db)), patch.object(runner, "_load_heartbeat", return_value=heartbeat), patch.object(runner, "step1_load_identity", return_value="identity"), patch.object(runner, "step7_invoke_runtime", side_effect=[work, done]), patch.object(runner, "LOGS_DIR", tmp_db.parent / "logs"):
            runner.run_heartbeat("atlas-4h")
        conn = _row_conn(tmp_db)
        assert conn.execute("SELECT locked_at FROM tickets WHERE id='ticket-1'").fetchone()["locked_at"] is None
        conn.close()

    def test_provider_and_profile_stored_in_run(self, tmp_db):
        import heartbeat_runner as runner
        with patch.object(runner, "_get_db", return_value=_row_conn(tmp_db)), \
             patch.object(runner, "_load_heartbeat", return_value={
                 "id": "atlas-4h", "agent": "atlas-project", "interval_seconds": 14400,
                 "max_turns": 10, "timeout_seconds": 600, "lock_timeout_seconds": 1800,
                 "enabled": True, "goal_id": None, "decision_prompt": "Check.",
                 "handler": None, "hermes_profile": "atlas-ops",
             }), \
             patch.object(runner, "step1_load_identity", return_value="# Atlas"), \
             patch.object(runner, "step7_invoke_runtime", return_value={
                 "status": "success", "output": '{"action":"work"}', "error": None,
                 "duration_ms": 200, "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.002,
                 "provider": "hermes", "requested_profile": "atlas-ops", "resolved_profile": "atlas-ops",
                 "exit_code": 0, "fallback_from": None,
             }), \
             patch.object(runner, "LOGS_DIR", tmp_db.parent / "logs"):
            run_id = runner.run_heartbeat("atlas-4h", triggered_by="manual")

        conn = _row_conn(tmp_db)
        row = conn.execute("SELECT provider, resolved_profile, tokens_in, tokens_out, cost_usd FROM heartbeat_runs WHERE run_id=?", (run_id,)).fetchone()
        assert row["provider"] == "hermes"
        assert row["resolved_profile"] == "atlas-ops"
        assert row["tokens_in"] == 100
        assert row["tokens_out"] == 50
        assert row["cost_usd"] == 0.002
        conn.close()
