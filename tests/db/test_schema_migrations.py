"""Schema migration tests — Phase 2 (postgres-compat).

Tests:
  - alembic upgrade head on fresh SQLite and Postgres creates all expected tables
  - alembic downgrade base reverts non-ORM tables cleanly
  - goal-progress trigger works after upgrade (both backends)
  - goal_progress_v view exists and returns correct data

Markers:
  @pytest.mark.sqlite  — SQLite-only tests
  @pytest.mark.postgres — Postgres-only tests
  (unmarked tests use the db_backend fixture and run on both)

Usage:
    # SQLite only (no Docker needed)
    pytest tests/db/test_schema_migrations.py -m 'not postgres'

    # Postgres only
    DATABASE_URL=postgresql://postgres:test@localhost:55434/postgres \
        pytest tests/db/test_schema_migrations.py -m postgres
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]          # …-postgres-compat/
_ALEMBIC_DIR = _REPO_ROOT / "dashboard" / "alembic"

_REQUIRED_TABLES = {
    "alembic_version",
    "users",
    "roles",
    "heartbeats",
    "heartbeat_runs",
    "heartbeat_triggers",
    "missions",
    "projects",
    "goals",
    "goal_tasks",
    "tickets",
    "ticket_comments",
    "ticket_activity",
    "brain_repo_configs",
    "plugin_scan_cache",
    "plugin_audit_log",
    "plugins_installed",
    "plugin_hook_circuit_state",
    "integration_health_cache",
    "plugin_orphans",
    "knowledge_connections",
    "knowledge_connection_events",
    "knowledge_api_keys",
    # 0007 — PG-native configs (SQLite migration is a no-op; tables only on PG)
    # These are intentionally omitted from the SQLite set and checked separately
    # in the PG-specific test below.
}

_REQUIRED_VIEWS = {"goal_progress_v"}
_REQUIRED_TRIGGERS = {"trg_task_done_updates_goal"}


def _get_tables(conn: sa.Connection) -> set[str]:
    insp = sa.inspect(conn)
    return set(insp.get_table_names())


def _assert_heartbeat_runtime_columns(conn: sa.Connection) -> None:
    inspector = sa.inspect(conn)
    heartbeat_columns = {
        column["name"] for column in inspector.get_columns("heartbeats")
    }
    assert "handler" in heartbeat_columns
    heartbeat_run_columns = {
        column["name"] for column in inspector.get_columns("heartbeat_runs")
    }
    assert {
        "decision_action",
        "decision_json",
        "provider",
        "requested_profile",
        "resolved_profile",
        "exit_code",
        "fallback_from",
        "stdout_tail",
        "stderr_tail",
        "runtime_run_id",
    } <= heartbeat_run_columns

    runtime_foreign_keys = {
        (foreign_key["constrained_columns"][0], foreign_key["referred_table"])
        for foreign_key in inspector.get_foreign_keys("runtime_runs")
    }
    assert {
        ("task_id", "scheduled_tasks"),
        ("ticket_id", "tickets"),
        ("goal_id", "goals"),
    } <= runtime_foreign_keys


def _assert_archived_ticket_status(conn: sa.Connection) -> None:
    ticket_id = "00000000-0000-0000-0000-000000000012"
    now = "2026-01-01T00:00:00.000000Z"
    conn.execute(
        text(
            "INSERT INTO tickets "
            "(id, title, status, priority, priority_rank, created_by, "
            "message_count, last_summary_at_message, created_at, updated_at) "
            "VALUES (:id, 'Archived migration check', 'archived', 'medium', 2, "
            "'migration-test', 0, 0, :now, :now)"
        ),
        {"id": ticket_id, "now": now},
    )
    conn.execute(text("DELETE FROM tickets WHERE id = :id"), {"id": ticket_id})


def _get_views(conn: sa.Connection) -> set[str]:
    dialect = conn.dialect.name
    if dialect == "sqlite":
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='view'")
        ).fetchall()
        return {r[0] for r in rows}
    else:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_schema='public'"
            )
        ).fetchall()
        return {r[0] for r in rows}


def _get_triggers(conn: sa.Connection) -> set[str]:
    dialect = conn.dialect.name
    if dialect == "sqlite":
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='trigger'")
        ).fetchall()
        return {r[0] for r in rows}
    else:
        rows = conn.execute(
            text(
                "SELECT trigger_name FROM information_schema.triggers "
                "WHERE trigger_schema='public'"
            )
        ).fetchall()
        return {r[0] for r in rows}


def _alembic_upgrade(db_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ALEMBIC_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


def _alembic_downgrade(db_url: str, target: str = "base") -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", target],
        cwd=str(_ALEMBIC_DIR),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# SQLite-specific tests
# ---------------------------------------------------------------------------

@pytest.mark.sqlite
def test_sqlite_fresh_upgrade_head(tmp_path):
    """Fresh SQLite: alembic upgrade head creates all expected tables."""
    db_file = tmp_path / "test_fresh.db"
    db_url = f"sqlite:///{db_file}"

    result = _alembic_upgrade(db_url)
    assert result.returncode == 0, f"upgrade failed:\n{result.stderr}"

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        tables = _get_tables(conn)
        missing = _REQUIRED_TABLES - tables
        assert not missing, f"Missing tables: {missing}"
        _assert_heartbeat_runtime_columns(conn)
        _assert_archived_ticket_status(conn)

        views = _get_views(conn)
        assert _REQUIRED_VIEWS <= views, f"Missing views: {_REQUIRED_VIEWS - views}"

        triggers = _get_triggers(conn)
        assert _REQUIRED_TRIGGERS <= triggers, f"Missing triggers: {_REQUIRED_TRIGGERS - triggers}"

        ver = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        assert ver[0] == "0012", f"Expected version 0012, got {ver[0]}"


@pytest.mark.sqlite
def test_sqlite_downgrade_base(tmp_path):
    """SQLite: downgrade base removes non-ORM tables, alembic_version disappears."""
    db_file = tmp_path / "test_downgrade.db"
    db_url = f"sqlite:///{db_file}"

    up = _alembic_upgrade(db_url)
    assert up.returncode == 0, f"upgrade failed:\n{up.stderr}"

    down = _alembic_downgrade(db_url, "base")
    assert down.returncode == 0, f"downgrade failed:\n{down.stderr}"

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        tables = _get_tables(conn)
        # Non-ORM tables should be gone
        assert "plugins_installed" not in tables
        assert "plugin_hook_circuit_state" not in tables
        assert "knowledge_connections" not in tables
        assert "plugin_orphans" not in tables
        assert "integration_health_cache" not in tables
        # alembic_version persists (managed by Alembic itself, not our migrations)
        # version_num should be empty (no current revision)
        if "alembic_version" in tables:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            assert ver is None, f"Expected no alembic version after downgrade base, got {ver}"


@pytest.mark.sqlite
def test_sqlite_downgrade_0012_restores_ticket_status_constraint(tmp_path):
    db_file = tmp_path / "test_downgrade_0012.db"
    db_url = f"sqlite:///{db_file}"
    up = _alembic_upgrade(db_url)
    assert up.returncode == 0, f"upgrade failed:\n{up.stderr}"

    engine = sa.create_engine(db_url)
    ticket_id = "00000000-0000-0000-0000-000000000012"
    now = "2026-01-01T00:00:00.000000Z"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tickets "
                "(id, title, status, priority, priority_rank, created_by, "
                "message_count, last_summary_at_message, created_at, updated_at) "
                "VALUES (:id, 'Archived downgrade check', 'archived', 'medium', 2, "
                "'migration-test', 0, 0, :now, :now)"
            ),
            {"id": ticket_id, "now": now},
        )

    down = _alembic_downgrade(db_url, "0011")
    assert down.returncode == 0, f"downgrade failed:\n{down.stderr}"

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM tickets WHERE id = :id"), {"id": ticket_id}
        ).scalar_one()
        assert status == "closed"


@pytest.mark.sqlite
def test_sqlite_trigger_goal_progress(tmp_path):
    """SQLite: trigger increments current_value and sets achieved status."""
    db_file = tmp_path / "test_trigger.db"
    db_url = f"sqlite:///{db_file}"

    _alembic_upgrade(db_url)

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        now = "2026-01-01T00:00:00.000Z"
        conn.execute(text(
            "INSERT INTO missions (slug, title, created_at, updated_at) "
            "VALUES ('m1', 'Mission', :now, :now)"
        ), {"now": now})
        conn.execute(text(
            "INSERT INTO projects (slug, mission_id, title, status, created_at, updated_at) "
            "VALUES ('p1', 1, 'Project', 'active', :now, :now)"
        ), {"now": now})
        conn.execute(text(
            "INSERT INTO goals (slug, project_id, title, metric_type, "
            "target_value, current_value, status, created_at, updated_at) "
            "VALUES ('g1', 1, 'Goal', 'count', 2, 0, 'active', :now, :now)"
        ), {"now": now})
        conn.execute(text(
            "INSERT INTO goal_tasks (goal_id, title, priority, status, created_at, updated_at) "
            "VALUES (1, 'Task A', 3, 'open', :now, :now)"
        ), {"now": now})
        conn.execute(text(
            "INSERT INTO goal_tasks (goal_id, title, priority, status, created_at, updated_at) "
            "VALUES (1, 'Task B', 3, 'open', :now, :now)"
        ), {"now": now})
        conn.commit()

        # Task A done — current_value should be 1, status still active
        conn.execute(text(
            "UPDATE goal_tasks SET status = 'done' WHERE id = 1"
        ))
        conn.commit()
        row = conn.execute(text("SELECT current_value, status FROM goals WHERE id = 1")).fetchone()
        assert row[0] == 1.0, f"Expected current_value=1, got {row[0]}"
        assert row[1] == "active", f"Expected status=active, got {row[1]}"

        # Task B done — current_value should be 2, status = achieved
        conn.execute(text(
            "UPDATE goal_tasks SET status = 'done' WHERE id = 2"
        ))
        conn.commit()
        row = conn.execute(text("SELECT current_value, status FROM goals WHERE id = 1")).fetchone()
        assert row[0] == 2.0, f"Expected current_value=2, got {row[0]}"
        assert row[1] == "achieved", f"Expected status=achieved, got {row[1]}"


@pytest.mark.sqlite
def test_sqlite_view_goal_progress(tmp_path):
    """SQLite: goal_progress_v returns correct pct_complete."""
    db_file = tmp_path / "test_view.db"
    db_url = f"sqlite:///{db_file}"
    _alembic_upgrade(db_url)

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        now = "2026-01-01T00:00:00.000Z"
        conn.execute(text(
            "INSERT INTO missions (slug, title, created_at, updated_at) VALUES ('m1', 'M', :now, :now)"
        ), {"now": now})
        conn.execute(text(
            "INSERT INTO projects (slug, mission_id, title, status, created_at, updated_at) "
            "VALUES ('p1', 1, 'P', 'active', :now, :now)"
        ), {"now": now})
        conn.execute(text(
            "INSERT INTO goals (slug, project_id, title, metric_type, target_value, current_value, "
            "status, created_at, updated_at) VALUES ('g1', 1, 'G', 'count', 4, 0, 'active', :now, :now)"
        ), {"now": now})
        for i in range(4):
            status = "done" if i < 2 else "open"
            conn.execute(text(
                "INSERT INTO goal_tasks (goal_id, title, priority, status, created_at, updated_at) "
                "VALUES (1, :title, 3, :status, :now, :now)"
            ), {"title": f"T{i}", "status": status, "now": now})
        conn.commit()

        row = conn.execute(text(
            "SELECT total_tasks, done_tasks, pct_complete FROM goal_progress_v WHERE goal_id = 1"
        )).fetchone()
        assert row[0] == 4, f"total_tasks expected 4, got {row[0]}"
        assert row[1] == 2, f"done_tasks expected 2, got {row[1]}"
        assert abs(row[2] - 50.0) < 0.01, f"pct_complete expected 50.0, got {row[2]}"


# ---------------------------------------------------------------------------
# Postgres-specific tests
# ---------------------------------------------------------------------------

@pytest.mark.postgres
def test_postgres_fresh_upgrade_head():
    """Postgres: alembic upgrade head creates all expected tables."""
    db_url = os.environ.get("DATABASE_URL", "")
    assert db_url, "DATABASE_URL must be set for postgres tests"

    result = _alembic_upgrade(db_url)
    assert result.returncode == 0, f"upgrade failed:\n{result.stderr}"

    url_norm = db_url
    if url_norm.startswith("postgresql://") and "+psycopg2" not in url_norm:
        url_norm = url_norm.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = sa.create_engine(url_norm)
    with engine.connect() as conn:
        tables = _get_tables(conn)
        missing = _REQUIRED_TABLES - tables
        assert not missing, f"Missing tables: {missing}"
        _assert_heartbeat_runtime_columns(conn)
        _assert_archived_ticket_status(conn)

        views = _get_views(conn)
        assert _REQUIRED_VIEWS <= views, f"Missing views: {_REQUIRED_VIEWS - views}"

        triggers = _get_triggers(conn)
        assert _REQUIRED_TRIGGERS <= triggers, f"Missing triggers: {_REQUIRED_TRIGGERS - triggers}"

        ver = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        assert ver[0] == "0012", f"Expected version 0012, got {ver[0]}"

        # 0007 tables must exist on Postgres (migration is PG-only).
        pg_tables_0007 = {"runtime_configs", "llm_providers", "routine_definitions"}
        missing_0007 = pg_tables_0007 - tables
        assert not missing_0007, f"Missing PG-native config tables: {missing_0007}"


@pytest.mark.postgres
def test_postgres_trigger_goal_progress():
    """Postgres: plpgsql trigger increments current_value and sets achieved."""
    db_url = os.environ.get("DATABASE_URL", "")
    assert db_url, "DATABASE_URL must be set for postgres tests"

    url_norm = db_url
    if url_norm.startswith("postgresql://") and "+psycopg2" not in url_norm:
        url_norm = url_norm.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = sa.create_engine(url_norm)
    with engine.connect() as conn:
        now = "2026-01-01T00:00:00.000Z"
        # Use a unique slug to avoid conflicts with other test runs
        import uuid
        suffix = uuid.uuid4().hex[:8]
        conn.execute(text(
            "INSERT INTO missions (slug, title, created_at, updated_at) "
            "VALUES (:slug, 'Test Mission', :now, :now)"
        ), {"slug": f"m-{suffix}", "now": now})
        mission_id = conn.execute(text("SELECT id FROM missions WHERE slug = :slug"), {"slug": f"m-{suffix}"}).fetchone()[0]

        conn.execute(text(
            "INSERT INTO projects (slug, mission_id, title, status, created_at, updated_at) "
            "VALUES (:slug, :mid, 'Test Proj', 'active', :now, :now)"
        ), {"slug": f"p-{suffix}", "mid": mission_id, "now": now})
        project_id = conn.execute(text("SELECT id FROM projects WHERE slug = :slug"), {"slug": f"p-{suffix}"}).fetchone()[0]

        conn.execute(text(
            "INSERT INTO goals (slug, project_id, title, metric_type, "
            "target_value, current_value, status, created_at, updated_at) "
            "VALUES (:slug, :pid, 'Test Goal', 'count', 2, 0, 'active', :now, :now)"
        ), {"slug": f"g-{suffix}", "pid": project_id, "now": now})
        goal_id = conn.execute(text("SELECT id FROM goals WHERE slug = :slug"), {"slug": f"g-{suffix}"}).fetchone()[0]

        conn.execute(text(
            "INSERT INTO goal_tasks (goal_id, title, priority, status, created_at, updated_at) "
            "VALUES (:gid, 'Task A', 3, 'open', :now, :now)"
        ), {"gid": goal_id, "now": now})
        conn.execute(text(
            "INSERT INTO goal_tasks (goal_id, title, priority, status, created_at, updated_at) "
            "VALUES (:gid, 'Task B', 3, 'open', :now, :now)"
        ), {"gid": goal_id, "now": now})
        conn.commit()

        # Get task IDs
        task_ids = [r[0] for r in conn.execute(
            text("SELECT id FROM goal_tasks WHERE goal_id = :gid ORDER BY id"),
            {"gid": goal_id}
        ).fetchall()]

        # Mark first task done
        conn.execute(text("UPDATE goal_tasks SET status = 'done' WHERE id = :tid"), {"tid": task_ids[0]})
        conn.commit()
        row = conn.execute(text("SELECT current_value, status FROM goals WHERE id = :gid"), {"gid": goal_id}).fetchone()
        assert row[0] == 1.0, f"Expected current_value=1, got {row[0]}"
        assert row[1] == "active", f"Expected status=active, got {row[1]}"

        # Mark second task done — should achieve goal
        conn.execute(text("UPDATE goal_tasks SET status = 'done' WHERE id = :tid"), {"tid": task_ids[1]})
        conn.commit()
        row = conn.execute(text("SELECT current_value, status FROM goals WHERE id = :gid"), {"gid": goal_id}).fetchone()
        assert row[0] == 2.0, f"Expected current_value=2, got {row[0]}"
        assert row[1] == "achieved", f"Expected status=achieved, got {row[1]}"

        # Cleanup
        conn.execute(text("DELETE FROM goals WHERE id = :gid"), {"gid": goal_id})
        conn.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": project_id})
        conn.execute(text("DELETE FROM missions WHERE id = :mid"), {"mid": mission_id})
        conn.commit()


@pytest.mark.postgres
def test_postgres_view_goal_progress():
    """Postgres: goal_progress_v view returns correct pct_complete."""
    db_url = os.environ.get("DATABASE_URL", "")
    assert db_url, "DATABASE_URL must be set for postgres tests"

    url_norm = db_url
    if url_norm.startswith("postgresql://") and "+psycopg2" not in url_norm:
        url_norm = url_norm.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = sa.create_engine(url_norm)
    with engine.connect() as conn:
        now = "2026-01-01T00:00:00.000Z"
        import uuid
        suffix = uuid.uuid4().hex[:8]

        conn.execute(text(
            "INSERT INTO missions (slug, title, created_at, updated_at) VALUES (:slug, 'M', :now, :now)"
        ), {"slug": f"vm-{suffix}", "now": now})
        mission_id = conn.execute(text("SELECT id FROM missions WHERE slug = :s"), {"s": f"vm-{suffix}"}).fetchone()[0]

        conn.execute(text(
            "INSERT INTO projects (slug, mission_id, title, status, created_at, updated_at) "
            "VALUES (:slug, :mid, 'P', 'active', :now, :now)"
        ), {"slug": f"vp-{suffix}", "mid": mission_id, "now": now})
        project_id = conn.execute(text("SELECT id FROM projects WHERE slug = :s"), {"s": f"vp-{suffix}"}).fetchone()[0]

        conn.execute(text(
            "INSERT INTO goals (slug, project_id, title, metric_type, target_value, current_value, "
            "status, created_at, updated_at) VALUES (:slug, :pid, 'G', 'count', 4, 0, 'active', :now, :now)"
        ), {"slug": f"vg-{suffix}", "pid": project_id, "now": now})
        goal_id = conn.execute(text("SELECT id FROM goals WHERE slug = :s"), {"s": f"vg-{suffix}"}).fetchone()[0]

        for i in range(4):
            status = "done" if i < 2 else "open"
            conn.execute(text(
                "INSERT INTO goal_tasks (goal_id, title, priority, status, created_at, updated_at) "
                "VALUES (:gid, :t, 3, :s, :now, :now)"
            ), {"gid": goal_id, "t": f"T{i}", "s": status, "now": now})
        conn.commit()

        row = conn.execute(text(
            "SELECT total_tasks, done_tasks, pct_complete FROM goal_progress_v WHERE goal_id = :gid"
        ), {"gid": goal_id}).fetchone()
        assert row[0] == 4, f"total_tasks expected 4, got {row[0]}"
        assert row[1] == 2, f"done_tasks expected 2, got {row[1]}"
        assert abs(float(row[2]) - 50.0) < 0.01, f"pct_complete expected 50.0, got {row[2]}"

        # Cleanup
        conn.execute(text("DELETE FROM goals WHERE id = :gid"), {"gid": goal_id})
        conn.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": project_id})
        conn.execute(text("DELETE FROM missions WHERE id = :mid"), {"mid": mission_id})
        conn.commit()


@pytest.mark.postgres
def test_postgres_downgrade_0012_restores_ticket_status_constraint():
    db_url = os.environ.get("DATABASE_URL", "")
    assert db_url, "DATABASE_URL must be set for postgres tests"

    url_norm = db_url
    if url_norm.startswith("postgresql://") and "+psycopg2" not in url_norm:
        url_norm = url_norm.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = sa.create_engine(url_norm)
    ticket_id = "00000000-0000-0000-0000-000000000012"
    now = "2026-01-01T00:00:00.000000Z"
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tickets WHERE id = :id"), {"id": ticket_id})
        conn.execute(
            text(
                "INSERT INTO tickets "
                "(id, title, status, priority, priority_rank, created_by, "
                "message_count, last_summary_at_message, created_at, updated_at) "
                "VALUES (:id, 'Archived downgrade check', 'archived', 'medium', 2, "
                "'migration-test', 0, 0, :now, :now)"
            ),
            {"id": ticket_id, "now": now},
        )

    down = _alembic_downgrade(db_url, "0011")
    assert down.returncode == 0, f"downgrade failed:\n{down.stderr}"

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM tickets WHERE id = :id"), {"id": ticket_id}
        ).scalar_one()
        assert status == "closed"
