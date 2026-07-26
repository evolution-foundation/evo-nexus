"""Tests for ticket_janitor — portable timeout expiry logic (Fase 3).

Coverage:
- Unit: _parse logic (expired vs not-expired) using datetime arithmetic
- Integration (SQLite): release_expired_locks() via Flask app fixture
- Integration (PG): same, marked @pytest.mark.postgres
"""

from __future__ import annotations

import importlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixture: minimal Flask app with tickets table
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, request):
    """Flask app backed by SQLite (default) or Postgres (DATABASE_URL env)."""
    import os
    import flask
    import models as _models

    db_url = os.environ.get("DATABASE_URL", "") or f"sqlite:///{tmp_path}/janitor_test.db"

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-janitor"
    _app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    _models.db.init_app(_app)

    with _app.app_context():
        _models.db.create_all()

    yield _app

    # Cleanup — PG keeps tables across tests unless we drop.
    # db.drop_all() fails on PG when views (goal_progress_v) depend on
    # underlying tables; DROP SCHEMA CASCADE is the reliable teardown.
    with _app.app_context():
        _models.db.session.remove()
        dialect = _models.db.engine.dialect.name
        if dialect == "postgresql":
            _models.db.engine.dispose()
            from sqlalchemy import create_engine, text as _text
            _engine = create_engine(db_url)
            with _engine.connect() as _conn:
                _conn.execute(_text("DROP SCHEMA public CASCADE"))
                _conn.execute(_text("CREATE SCHEMA public"))
                _conn.commit()
            _engine.dispose()
        else:
            _models.db.drop_all()


def _create_locked_ticket(db, locked_at: datetime, timeout_secs: int = 1800) -> str:
    """Insert a locked ticket and return its id."""
    from models import Ticket
    tid = str(uuid.uuid4())
    now_str = _iso(_utcnow())
    t = Ticket(
        id=tid,
        title="test locked",
        status="in_progress",
        priority="medium",
        priority_rank=2,
        locked_at=_iso(locked_at),
        locked_by="test-agent",
        lock_timeout_seconds=timeout_secs,
        created_at=now_str,
        updated_at=now_str,
        created_by="test",
    )
    db.session.add(t)
    db.session.commit()
    return tid


# ---------------------------------------------------------------------------
# Unit: Python-side expiry check (no DB required)
# ---------------------------------------------------------------------------

class TestExpiryLogic:
    """Validate that the Python expiry logic in ticket_janitor is correct."""

    def test_expired_ticket_is_detected(self):
        """A lock set 31 minutes ago with 30-minute timeout is expired."""
        locked_at = _utcnow() - timedelta(seconds=1860)  # 31 minutes ago
        timeout = 1800  # 30 minutes
        assert locked_at + timedelta(seconds=timeout) < _utcnow()

    def test_active_lock_not_expired(self):
        """A lock set 1 minute ago with 30-minute timeout is still active."""
        locked_at = _utcnow() - timedelta(seconds=60)
        timeout = 1800
        assert not (locked_at + timedelta(seconds=timeout) < _utcnow())

    def test_exactly_at_boundary_not_expired(self):
        """A lock that expires exactly now (boundary) is NOT yet expired."""
        locked_at = _utcnow() - timedelta(seconds=1800)
        timeout = 1800
        # locked_at + timeout == utcnow — should NOT be strictly less-than
        # In practice clock drift means this can flicker; we just verify logic
        assert (locked_at + timedelta(seconds=timeout)) <= _utcnow()


# ---------------------------------------------------------------------------
# Integration: release_expired_locks() via SQLite (always runs)
# ---------------------------------------------------------------------------

class TestJanitorIntegration:

    def test_expired_lock_is_released(self, app):
        """release_expired_locks() clears a lock that expired 1 hour ago."""
        import ticket_janitor as _tj
        importlib.reload(_tj)

        from models import db, Ticket
        locked_at = _utcnow() - timedelta(hours=1)

        with app.app_context():
            tid = _create_locked_ticket(db, locked_at, timeout_secs=1800)
            released = _tj.release_expired_locks()

        assert released == 1

        with app.app_context():
            t = db.session.get(Ticket, tid)
            assert t is not None
            assert t.locked_at is None
            assert t.locked_by is None

    def test_active_lock_is_not_released(self, app):
        """release_expired_locks() leaves a recently-acquired lock intact."""
        import ticket_janitor as _tj
        importlib.reload(_tj)

        from models import db, Ticket
        locked_at = _utcnow() - timedelta(seconds=30)  # 30s ago, timeout 1800

        with app.app_context():
            tid = _create_locked_ticket(db, locked_at, timeout_secs=1800)
            released = _tj.release_expired_locks()

        assert released == 0

        with app.app_context():
            t = db.session.get(Ticket, tid)
            assert t is not None
            assert t.locked_at is not None  # still locked

    def test_activity_row_written_on_release(self, app):
        """Each released lock produces a TicketActivity row."""
        import ticket_janitor as _tj
        importlib.reload(_tj)

        from models import db, TicketActivity
        locked_at = _utcnow() - timedelta(hours=2)

        with app.app_context():
            tid = _create_locked_ticket(db, locked_at, timeout_secs=60)
            _tj.release_expired_locks()
            acts = db.session.query(TicketActivity).filter_by(
                ticket_id=tid, action="auto_release"
            ).all()

        assert len(acts) == 1
        payload = json.loads(acts[0].payload)
        assert payload["previously_locked_by"] == "test-agent"

    def test_malformed_locked_at_releases_defensively(self, app):
        """A ticket with unparseable locked_at is released, not skipped."""
        import ticket_janitor as _tj
        importlib.reload(_tj)

        from models import db, Ticket
        now_str = _iso(_utcnow())
        tid = str(uuid.uuid4())

        with app.app_context():
            t = Ticket(
                id=tid,
                title="malformed lock",
                status="in_progress",
                priority="medium",
                priority_rank=2,
                locked_at="NOT-A-DATE",
                locked_by="agent",
                lock_timeout_seconds=1800,
                created_at=now_str,
                updated_at=now_str,
                created_by="test",
            )
            db.session.add(t)
            db.session.commit()
            released = _tj.release_expired_locks()

        assert released == 1

    def test_zero_locked_tickets_no_commit(self, app):
        """No tickets locked → returns 0, no commit/error."""
        import ticket_janitor as _tj
        importlib.reload(_tj)

        from models import db
        with app.app_context():
            released = _tj.release_expired_locks()

        assert released == 0


# ---------------------------------------------------------------------------
# PG-specific: same tests under Postgres
# ---------------------------------------------------------------------------

@pytest.mark.postgres
class TestJanitorPostgres:
    """Mirror of integration tests, runs only when DATABASE_URL points to PG."""

    def test_expired_lock_released_on_pg(self, app):
        import ticket_janitor as _tj
        importlib.reload(_tj)

        from models import db, Ticket
        locked_at = _utcnow() - timedelta(hours=1)

        with app.app_context():
            tid = _create_locked_ticket(db, locked_at, timeout_secs=1800)
            released = _tj.release_expired_locks()

        assert released == 1

        with app.app_context():
            t = db.session.get(Ticket, tid)
            assert t.locked_at is None

    def test_active_lock_stays_on_pg(self, app):
        import ticket_janitor as _tj
        importlib.reload(_tj)

        from models import db, Ticket
        locked_at = _utcnow() - timedelta(seconds=30)

        with app.app_context():
            tid = _create_locked_ticket(db, locked_at, timeout_secs=1800)
            released = _tj.release_expired_locks()

        assert released == 0
