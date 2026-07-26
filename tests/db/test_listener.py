"""Tests for db/listeners.py — GoalTask after_update observability listener.

The listener is observability-only: it increments a thread-local counter when
a GoalTask transitions to status='done'.  It does NOT modify goal current_value
(that is the DB trigger's responsibility, tested separately in tests/goals/).

Coverage:
- register_all() is idempotent (safe to call twice)
- Counter increments on status→'done'
- Counter does NOT increment for other status transitions
- reset_orm_count() resets per-thread counter
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    import flask
    import models as _models

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-listener"
    _app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path}/listener_test.db"
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    _models.db.init_app(_app)

    with _app.app_context():
        _models.db.create_all()

    yield _app

    # Cleanup — PG views (goal_progress_v) block db.drop_all(); use CASCADE.
    with _app.app_context():
        _models.db.session.remove()
        dialect = _models.db.engine.dialect.name
        if dialect == "postgresql":
            _models.db.engine.dispose()
            import os as _os
            from sqlalchemy import create_engine, text as _text
            _db_url = _os.environ.get("DATABASE_URL", "")
            _engine = create_engine(_db_url)
            with _engine.connect() as _conn:
                _conn.execute(_text("DROP SCHEMA public CASCADE"))
                _conn.execute(_text("CREATE SCHEMA public"))
                _conn.commit()
            _engine.dispose()
        else:
            _models.db.drop_all()


def _make_goal_and_task(db):
    """Create a minimal Goal + GoalTask. Returns (goal, task)."""
    from models import GoalProject, Goal, GoalTask
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    project = GoalProject(
        slug="test-project",
        title="Test Project",
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.session.add(project)
    db.session.flush()

    goal = Goal(
        slug="test-goal",
        project_id=project.id,
        title="Test Goal",
        metric_type="count",
        target_value=5.0,
        current_value=0.0,
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.session.add(goal)
    db.session.flush()

    task = GoalTask(
        goal_id=goal.id,
        title="Test Task",
        priority=3,
        status="open",
        created_at=now,
        updated_at=now,
    )
    db.session.add(task)
    db.session.commit()
    return goal, task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestListenerRegistration:

    def test_register_all_is_idempotent(self, app):
        """Calling register_all() twice does not raise and does not double-count."""
        import db.listeners as _listeners

        # Register twice
        _listeners.register_all()
        _listeners.register_all()

        from models import db, GoalTask
        _listeners.reset_orm_count()

        with app.app_context():
            goal, task = _make_goal_and_task(db)
            task.status = "done"
            db.session.commit()

        # Should be 1, not 2, even after double-registration
        assert _listeners._get_orm_count() == 1


class TestListenerCounter:

    def test_done_transition_increments_counter(self, app):
        """Counter goes from 0 to 1 when a task is set to done."""
        import db.listeners as _listeners
        _listeners.register_all()
        _listeners.reset_orm_count()

        from models import db
        with app.app_context():
            _, task = _make_goal_and_task(db)
            task.status = "done"
            db.session.commit()

        assert _listeners._get_orm_count() == 1

    def test_non_done_transition_does_not_increment(self, app):
        """Changing status to 'review' does not increment the counter."""
        import db.listeners as _listeners
        _listeners.register_all()
        _listeners.reset_orm_count()

        from models import db
        with app.app_context():
            _, task = _make_goal_and_task(db)
            task.status = "review"
            db.session.commit()

        assert _listeners._get_orm_count() == 0

    def test_multiple_tasks_multiple_increments(self, app):
        """Two tasks set to done → counter is 2."""
        import db.listeners as _listeners
        _listeners.register_all()
        _listeners.reset_orm_count()

        from models import db, GoalTask
        from datetime import datetime, timezone

        with app.app_context():
            goal, task1 = _make_goal_and_task(db)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            task2 = GoalTask(
                goal_id=goal.id,
                title="Task 2",
                priority=2,
                status="open",
                created_at=now,
                updated_at=now,
            )
            db.session.add(task2)
            db.session.commit()

            task1.status = "done"
            task2.status = "done"
            db.session.commit()

        assert _listeners._get_orm_count() == 2

    def test_reset_orm_count_zeroes_counter(self, app):
        """reset_orm_count() brings counter back to 0."""
        import db.listeners as _listeners
        _listeners.register_all()
        _listeners.reset_orm_count()

        from models import db
        with app.app_context():
            _, task = _make_goal_and_task(db)
            task.status = "done"
            db.session.commit()

        assert _listeners._get_orm_count() == 1
        _listeners.reset_orm_count()
        assert _listeners._get_orm_count() == 0
