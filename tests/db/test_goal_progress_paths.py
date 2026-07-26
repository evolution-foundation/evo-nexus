"""AC4 — Goal progress trigger fires via three distinct update paths.

The DB-side trigger (trg_task_done_updates_goal) must increment current_value
regardless of whether the UPDATE comes from:
  - "orm"      — ORM object mutation + session.commit() per task
  - "raw_text" — text("UPDATE ... SET status = 'done' WHERE id = :id") per task
  - "bulk_orm" — Query.update({...}, synchronize_session=False) in one statement

Parametrised over both backends. Requires alembic upgrade head so the trigger
and view exist (db.create_all() does not create triggers).

Run:
    # SQLite only (no Docker)
    pytest tests/db/test_goal_progress_paths.py -m 'not postgres' -v

    # Postgres
    DATABASE_URL=postgresql://postgres:test@localhost:55443/postgres \
        pytest tests/db/test_goal_progress_paths.py -m postgres -v
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
_ALEMBIC_DIR = REPO_ROOT / "dashboard" / "alembic"

sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _alembic_upgrade(db_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": db_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ALEMBIC_DIR),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"


def _norm_pg_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


_NOW = "2026-01-01T00:00:00.000Z"


def _insert_goal_chain(conn: sa.Connection, suffix: str) -> tuple[int, list[int]]:
    """Insert mission → project → goal (target=5) → 5 tasks. Return (goal_id, task_ids)."""
    conn.execute(text(
        "INSERT INTO missions (slug, title, created_at, updated_at) "
        "VALUES (:slug, 'M', :now, :now)"
    ), {"slug": f"m-{suffix}", "now": _NOW})
    mission_id = conn.execute(
        text("SELECT id FROM missions WHERE slug = :s"), {"s": f"m-{suffix}"}
    ).fetchone()[0]

    conn.execute(text(
        "INSERT INTO projects (slug, mission_id, title, status, created_at, updated_at) "
        "VALUES (:slug, :mid, 'P', 'active', :now, :now)"
    ), {"slug": f"p-{suffix}", "mid": mission_id, "now": _NOW})
    project_id = conn.execute(
        text("SELECT id FROM projects WHERE slug = :s"), {"s": f"p-{suffix}"}
    ).fetchone()[0]

    conn.execute(text(
        "INSERT INTO goals (slug, project_id, title, metric_type, "
        "target_value, current_value, status, created_at, updated_at) "
        "VALUES (:slug, :pid, 'G', 'count', 5, 0, 'active', :now, :now)"
    ), {"slug": f"g-{suffix}", "pid": project_id, "now": _NOW})
    goal_id = conn.execute(
        text("SELECT id FROM goals WHERE slug = :s"), {"s": f"g-{suffix}"}
    ).fetchone()[0]

    for i in range(5):
        conn.execute(text(
            "INSERT INTO goal_tasks (goal_id, title, priority, status, created_at, updated_at) "
            "VALUES (:gid, :title, 3, 'open', :now, :now)"
        ), {"gid": goal_id, "title": f"task-{i}", "now": _NOW})

    task_ids = [
        r[0] for r in conn.execute(
            text("SELECT id FROM goal_tasks WHERE goal_id = :gid ORDER BY id"),
            {"gid": goal_id},
        ).fetchall()
    ]
    conn.commit()
    return goal_id, task_ids


def _cleanup(conn: sa.Connection, suffix: str) -> None:
    """Remove test data inserted by _insert_goal_chain (PG keeps data across tests)."""
    mission_id = conn.execute(
        text("SELECT id FROM missions WHERE slug = :s"), {"s": f"m-{suffix}"}
    ).fetchone()
    if mission_id is None:
        return
    mission_id = mission_id[0]
    project_id = conn.execute(
        text("SELECT id FROM projects WHERE slug = :s"), {"s": f"p-{suffix}"}
    ).fetchone()[0]
    goal_id = conn.execute(
        text("SELECT id FROM goals WHERE slug = :s"), {"s": f"g-{suffix}"}
    ).fetchone()[0]

    conn.execute(text("DELETE FROM goal_tasks WHERE goal_id = :gid"), {"gid": goal_id})
    conn.execute(text("DELETE FROM goals WHERE id = :gid"), {"gid": goal_id})
    conn.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": project_id})
    conn.execute(text("DELETE FROM missions WHERE id = :mid"), {"mid": mission_id})
    conn.commit()


# ---------------------------------------------------------------------------
# SQLite fixture — fresh DB via alembic upgrade per test
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_engine(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test_goal_progress.db"
    _alembic_upgrade(db_url)
    engine = sa.create_engine(db_url, connect_args={"check_same_thread": False})
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Postgres fixture — reuses DATABASE_URL (alembic already run in CI setup)
# ---------------------------------------------------------------------------

@pytest.fixture
def pg_engine():
    db_url = os.environ.get("DATABASE_URL", "")
    if not (db_url.startswith("postgresql") or db_url.startswith("postgres://")):
        pytest.skip("Postgres not configured (DATABASE_URL not set)")
    _alembic_upgrade(db_url)
    engine = sa.create_engine(_norm_pg_url(db_url), pool_pre_ping=True)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# ORM-path helper (needs Flask + models)
# ---------------------------------------------------------------------------

def _mark_done_via_orm(engine: sa.Engine, task_ids: list[int]) -> None:
    """Mark tasks done via SQLAlchemy ORM (Flask-SQLAlchemy session)."""
    import flask
    import models as _models

    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-ac4"
    app.config["SQLALCHEMY_DATABASE_URI"] = engine.url.render_as_string(hide_password=False)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _models.db.init_app(app)

    with app.app_context():
        for tid in task_ids:
            task = _models.db.session.get(_models.GoalTask, tid)
            assert task is not None, f"GoalTask {tid} not found"
            task.status = "done"
            _models.db.session.commit()


def _mark_done_via_bulk_orm(engine: sa.Engine, task_ids: list[int], goal_id: int) -> None:
    """Mark tasks done via ORM Query.update (bulk, single SQL statement)."""
    import flask
    import models as _models

    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-ac4-bulk"
    app.config["SQLALCHEMY_DATABASE_URI"] = engine.url.render_as_string(hide_password=False)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _models.db.init_app(app)

    with app.app_context():
        _models.db.session.query(_models.GoalTask).filter(
            _models.GoalTask.goal_id == goal_id
        ).update({"status": "done"}, synchronize_session=False)
        _models.db.session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGoalProgressViaSQLite:
    """AC4 — three update paths on SQLite."""

    def test_orm_path(self, sqlite_engine):
        suffix = uuid.uuid4().hex[:8]
        with sqlite_engine.connect() as conn:
            goal_id, task_ids = _insert_goal_chain(conn, suffix)

        _mark_done_via_orm(sqlite_engine, task_ids)

        with sqlite_engine.connect() as conn:
            row = conn.execute(
                text("SELECT current_value, status FROM goals WHERE id = :gid"), {"gid": goal_id}
            ).fetchone()
        assert row[0] == 5.0, f"orm path: expected current_value=5, got {row[0]}"
        assert row[1] == "achieved", f"orm path: expected status=achieved, got {row[1]}"

    def test_raw_text_path(self, sqlite_engine):
        suffix = uuid.uuid4().hex[:8]
        with sqlite_engine.connect() as conn:
            goal_id, task_ids = _insert_goal_chain(conn, suffix)

        with sqlite_engine.connect() as conn:
            for tid in task_ids:
                conn.execute(
                    text("UPDATE goal_tasks SET status = 'done' WHERE id = :id"), {"id": tid}
                )
                conn.commit()

            row = conn.execute(
                text("SELECT current_value, status FROM goals WHERE id = :gid"), {"gid": goal_id}
            ).fetchone()
        assert row[0] == 5.0, f"raw_text path: expected current_value=5, got {row[0]}"
        assert row[1] == "achieved", f"raw_text path: expected status=achieved, got {row[1]}"

    def test_bulk_orm_path(self, sqlite_engine):
        suffix = uuid.uuid4().hex[:8]
        with sqlite_engine.connect() as conn:
            goal_id, task_ids = _insert_goal_chain(conn, suffix)

        _mark_done_via_bulk_orm(sqlite_engine, task_ids, goal_id)

        with sqlite_engine.connect() as conn:
            row = conn.execute(
                text("SELECT current_value, status FROM goals WHERE id = :gid"), {"gid": goal_id}
            ).fetchone()
        assert row[0] == 5.0, f"bulk_orm path: expected current_value=5, got {row[0]}"
        assert row[1] == "achieved", f"bulk_orm path: expected status=achieved, got {row[1]}"


@pytest.mark.postgres
class TestGoalProgressViaPostgres:
    """AC4 — three update paths on Postgres."""

    def test_orm_path(self, pg_engine):
        suffix = uuid.uuid4().hex[:8]
        with pg_engine.connect() as conn:
            goal_id, task_ids = _insert_goal_chain(conn, suffix)

        _mark_done_via_orm(pg_engine, task_ids)

        with pg_engine.connect() as conn:
            row = conn.execute(
                text("SELECT current_value, status FROM goals WHERE id = :gid"), {"gid": goal_id}
            ).fetchone()
            _cleanup(conn, suffix)
        assert row[0] == 5.0, f"pg orm path: expected current_value=5, got {row[0]}"
        assert row[1] == "achieved", f"pg orm path: expected status=achieved, got {row[1]}"

    def test_raw_text_path(self, pg_engine):
        suffix = uuid.uuid4().hex[:8]
        with pg_engine.connect() as conn:
            goal_id, task_ids = _insert_goal_chain(conn, suffix)

        with pg_engine.connect() as conn:
            for tid in task_ids:
                conn.execute(
                    text("UPDATE goal_tasks SET status = 'done' WHERE id = :id"), {"id": tid}
                )
                conn.commit()

            row = conn.execute(
                text("SELECT current_value, status FROM goals WHERE id = :gid"), {"gid": goal_id}
            ).fetchone()
            _cleanup(conn, suffix)
        assert row[0] == 5.0, f"pg raw_text path: expected current_value=5, got {row[0]}"
        assert row[1] == "achieved", f"pg raw_text path: expected status=achieved, got {row[1]}"

    def test_bulk_orm_path(self, pg_engine):
        suffix = uuid.uuid4().hex[:8]
        with pg_engine.connect() as conn:
            goal_id, task_ids = _insert_goal_chain(conn, suffix)

        _mark_done_via_bulk_orm(pg_engine, task_ids, goal_id)

        with pg_engine.connect() as conn:
            row = conn.execute(
                text("SELECT current_value, status FROM goals WHERE id = :gid"), {"gid": goal_id}
            ).fetchone()
            _cleanup(conn, suffix)
        assert row[0] == 5.0, f"pg bulk_orm path: expected current_value=5, got {row[0]}"
        assert row[1] == "achieved", f"pg bulk_orm path: expected status=achieved, got {row[1]}"
