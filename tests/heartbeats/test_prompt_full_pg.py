"""Tests for heartbeat step8_persist — prompt_full in PG mode.

PG tests: prompt_full stored in heartbeat_run_prompts; prompt_preview truncated to 1000.
SQLite tests: heartbeat_run_prompts table NOT written; JSONL appended instead.

Marked @pytest.mark.postgres where live PG is required.
SQLite tests run without any marker (always pass in CI sqlite job).

Usage:
    # PG tests (requires live PG):
    DATABASE_URL='postgresql://postgres:test@localhost:55491/postgres' \\
        pytest tests/heartbeats/test_prompt_full_pg.py -v

    # SQLite invariant (no DATABASE_URL needed):
    pytest tests/heartbeats/test_prompt_full_pg.py -v -k "sqlite"
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
_BACKEND = _REPO_ROOT / "dashboard" / "backend"
_ALEMBIC_DIR = _REPO_ROOT / "dashboard" / "alembic"
sys.path.insert(0, str(_BACKEND))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_IS_PG = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres://")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _pg_dsn() -> str:
    url = DATABASE_URL
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _run_alembic_upgrade(db_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": db_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ALEMBIC_DIR),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"


def _make_result(status: str = "success") -> dict:
    return {
        "status": status,
        "agent": "atlas-project",
        "started_at": _now_iso(),
        "duration_ms": 1234,
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_usd": 0.001,
        "error": None,
    }


# ---------------------------------------------------------------------------
# PG fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pg_engine():
    if not _IS_PG:
        pytest.skip("DATABASE_URL not set or not PostgreSQL")

    from sqlalchemy import create_engine
    engine = create_engine(_pg_dsn(), pool_pre_ping=True)
    _run_alembic_upgrade(DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture()
def clean_pg(pg_engine):
    """Clean heartbeat tables before and after each PG test."""
    from sqlalchemy import text
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM heartbeat_run_prompts"))
        conn.execute(text("DELETE FROM heartbeat_runs"))
        conn.execute(text("DELETE FROM heartbeats"))
    yield pg_engine
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM heartbeat_run_prompts"))
        conn.execute(text("DELETE FROM heartbeat_runs"))
        conn.execute(text("DELETE FROM heartbeats"))


def _seed_heartbeat(conn, hb_id: str = "atlas-4h") -> None:
    from sqlalchemy import text
    now = _now_iso()
    conn.execute(text("""
        INSERT INTO heartbeats
          (id, agent, interval_seconds, max_turns, timeout_seconds,
           lock_timeout_seconds, wake_triggers, enabled,
           decision_prompt, created_at, updated_at)
        VALUES (:id, 'atlas-project', 14400, 10, 600, 1800,
                '["interval"]', true, 'check Linear', :now, :now)
        ON CONFLICT (id) DO NOTHING
    """), {"id": hb_id, "now": now})


def _seed_run(conn, run_id: str, hb_id: str = "atlas-4h") -> None:
    """Insert a 'running' row so step8_persist can find it for the upsert path."""
    from sqlalchemy import text
    now = _now_iso()
    conn.execute(text("""
        INSERT INTO heartbeat_runs
          (run_id, heartbeat_id, started_at, ended_at, status,
           tokens_in, tokens_out, cost_usd, error, triggered_by)
        VALUES (:rid, :hbid, :now, :now, 'running', 0, 0, 0, NULL, 'interval')
    """), {"rid": run_id, "hbid": hb_id, "now": now})


# ---------------------------------------------------------------------------
# PG: full prompt stored intact
# ---------------------------------------------------------------------------

@pytest.mark.postgres
def test_pg_prompt_full_stored(clean_pg, monkeypatch):
    """PG mode: prompt_full in heartbeat_run_prompts == full text (not truncated)."""
    from sqlalchemy import text

    engine = clean_pg
    run_id = str(uuid.uuid4())
    long_prompt = "A" * 3000  # well over 1000 chars

    # Wire the module-level engine to our test PG engine.
    import db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "_engine", engine)

    import importlib
    import heartbeat_runner as runner_mod
    importlib.reload(runner_mod)
    monkeypatch.setattr(engine_mod, "_engine", engine)

    with engine.begin() as conn:
        _seed_heartbeat(conn)
        # No _seed_run: let step8_persist do the fresh INSERT so prompt_preview is set.

    with engine.connect() as conn:
        runner_mod.step8_persist(
            run_id=run_id,
            heartbeat_id="atlas-4h",
            result=_make_result(),
            trigger_id=None,
            triggered_by="interval",
            prompt_preview=long_prompt,
            conn=conn,
        )

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT prompt_preview FROM heartbeat_runs WHERE run_id = :rid"),
            {"rid": run_id},
        ).fetchone()
        assert row is not None
        assert len(row.prompt_preview) == 1000, (
            f"prompt_preview should be truncated to 1000 chars, got {len(row.prompt_preview)}"
        )

        pfull = conn.execute(
            text("SELECT prompt_full FROM heartbeat_run_prompts WHERE run_id = :rid"),
            {"rid": run_id},
        ).fetchone()
        assert pfull is not None, "heartbeat_run_prompts row missing in PG mode"
        assert pfull.prompt_full == long_prompt, (
            f"prompt_full was truncated: expected {len(long_prompt)} chars, "
            f"got {len(pfull.prompt_full)}"
        )


# ---------------------------------------------------------------------------
# PG: short prompt (< 1000 chars) — preview == full
# ---------------------------------------------------------------------------

@pytest.mark.postgres
def test_pg_short_prompt_preview_equals_full(clean_pg, monkeypatch):
    """PG: when prompt < 1000 chars, prompt_preview == prompt_full."""
    from sqlalchemy import text

    engine = clean_pg
    run_id = str(uuid.uuid4())
    short_prompt = "short context"

    import db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "_engine", engine)

    import importlib
    import heartbeat_runner as runner_mod
    importlib.reload(runner_mod)
    monkeypatch.setattr(engine_mod, "_engine", engine)

    with engine.begin() as conn:
        _seed_heartbeat(conn)

    with engine.connect() as conn:
        runner_mod.step8_persist(
            run_id=run_id,
            heartbeat_id="atlas-4h",
            result=_make_result(),
            trigger_id=None,
            triggered_by="interval",
            prompt_preview=short_prompt,
            conn=conn,
        )

    with engine.connect() as conn:
        preview = conn.execute(
            text("SELECT prompt_preview FROM heartbeat_runs WHERE run_id = :rid"),
            {"rid": run_id},
        ).scalar_one()
        pfull = conn.execute(
            text("SELECT prompt_full FROM heartbeat_run_prompts WHERE run_id = :rid"),
            {"rid": run_id},
        ).scalar_one()

    assert preview == short_prompt
    assert pfull == short_prompt
    assert preview == pfull


# ---------------------------------------------------------------------------
# PG: idempotency — duplicate call updates, does not error
# ---------------------------------------------------------------------------

@pytest.mark.postgres
def test_pg_idempotency_on_duplicate_run_id(clean_pg, monkeypatch):
    """PG: calling step8_persist twice with same run_id must not raise."""
    from sqlalchemy import text

    engine = clean_pg
    run_id = str(uuid.uuid4())
    prompt_v1 = "B" * 2000
    prompt_v2 = "C" * 2500

    import db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "_engine", engine)

    import importlib
    import heartbeat_runner as runner_mod
    importlib.reload(runner_mod)
    monkeypatch.setattr(engine_mod, "_engine", engine)

    with engine.begin() as conn:
        _seed_heartbeat(conn)

    # First call
    with engine.connect() as conn:
        runner_mod.step8_persist(
            run_id=run_id,
            heartbeat_id="atlas-4h",
            result=_make_result(),
            trigger_id=None,
            triggered_by="interval",
            prompt_preview=prompt_v1,
            conn=conn,
        )

    # Second call with a different (longer) prompt — must UPDATE, not error.
    # Because status is now 'success' (not 'running'), the early-return guard fires
    # and the second persist is a no-op — that is the correct idempotent behavior.
    with engine.connect() as conn:
        runner_mod.step8_persist(
            run_id=run_id,
            heartbeat_id="atlas-4h",
            result=_make_result(),
            trigger_id=None,
            triggered_by="interval",
            prompt_preview=prompt_v2,
            conn=conn,
        )

    with engine.connect() as conn:
        pfull = conn.execute(
            text("SELECT prompt_full FROM heartbeat_run_prompts WHERE run_id = :rid"),
            {"rid": run_id},
        ).scalar_one()

    # First write wins (idempotent guard skips second persist)
    assert pfull == prompt_v1


# ---------------------------------------------------------------------------
# SQLite: heartbeat_run_prompts NOT written; JSONL IS written
# ---------------------------------------------------------------------------

@pytest.fixture()
def sqlite_env(tmp_path):
    """Minimal SQLite DB with heartbeat tables."""
    from sqlalchemy import create_engine, text

    db_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.execute(text("""
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
            )
        """))
        conn.execute(text("""
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
            )
        """))
        conn.execute(text("""
            CREATE TABLE heartbeat_run_prompts (
                run_id TEXT PRIMARY KEY,
                prompt_full TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """))

    return engine, tmp_path


@pytest.mark.sqlite
def test_sqlite_no_write_to_heartbeat_run_prompts(sqlite_env, monkeypatch):
    """SQLite mode: step8_persist must NOT write to heartbeat_run_prompts."""
    from sqlalchemy import text

    engine, tmp_path = sqlite_env
    run_id = str(uuid.uuid4())
    long_prompt = "X" * 3000

    import db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "_engine", engine)

    import importlib
    import heartbeat_runner as runner_mod
    importlib.reload(runner_mod)
    monkeypatch.setattr(engine_mod, "_engine", engine)

    # Override LOGS_DIR to tmp_path so JSONL write doesn't hit repo dir.
    monkeypatch.setattr(runner_mod, "LOGS_DIR", tmp_path / "logs")

    now = _now_iso()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO heartbeats VALUES
              ('atlas-4h','atlas-project',14400,10,600,1800,'[]',1,NULL,'[]','check',
               :now, :now)
        """), {"now": now})
        conn.execute(text("""
            INSERT INTO heartbeat_runs
              (run_id, heartbeat_id, started_at, ended_at, status, triggered_by)
            VALUES (:rid, 'atlas-4h', :now, :now, 'running', 'interval')
        """), {"rid": run_id, "now": now})

    with engine.connect() as conn:
        runner_mod.step8_persist(
            run_id=run_id,
            heartbeat_id="atlas-4h",
            result=_make_result(),
            trigger_id=None,
            triggered_by="interval",
            prompt_preview=long_prompt,
            conn=conn,
        )

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM heartbeat_run_prompts WHERE run_id = :rid"),
            {"rid": run_id},
        ).scalar_one()

    assert count == 0, (
        "SQLite mode must NOT write to heartbeat_run_prompts, but found a row"
    )


@pytest.mark.sqlite
def test_sqlite_jsonl_written(sqlite_env, monkeypatch):
    """SQLite mode: step8_persist writes a JSONL entry to the log file."""
    from sqlalchemy import text

    engine, tmp_path = sqlite_env
    run_id = str(uuid.uuid4())
    logs_dir = tmp_path / "logs"

    import db.engine as engine_mod
    monkeypatch.setattr(engine_mod, "_engine", engine)

    import importlib
    import heartbeat_runner as runner_mod
    importlib.reload(runner_mod)
    monkeypatch.setattr(engine_mod, "_engine", engine)
    monkeypatch.setattr(runner_mod, "LOGS_DIR", logs_dir)

    now = _now_iso()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO heartbeats VALUES
              ('atlas-4h','atlas-project',14400,10,600,1800,'[]',1,NULL,'[]','check',
               :now, :now)
        """), {"now": now})
        conn.execute(text("""
            INSERT INTO heartbeat_runs
              (run_id, heartbeat_id, started_at, ended_at, status, triggered_by)
            VALUES (:rid, 'atlas-4h', :now, :now, 'running', 'interval')
        """), {"rid": run_id, "now": now})

    with engine.connect() as conn:
        runner_mod.step8_persist(
            run_id=run_id,
            heartbeat_id="atlas-4h",
            result=_make_result(),
            trigger_id=None,
            triggered_by="interval",
            prompt_preview="some prompt",
            conn=conn,
        )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = logs_dir / f"atlas-4h-{today}.jsonl"
    assert log_file.exists(), f"JSONL file not created: {log_file}"

    lines = [json.loads(l) for l in log_file.read_text().strip().splitlines()]
    assert any(l["run_id"] == run_id for l in lines), (
        f"run_id {run_id} not found in JSONL log"
    )
