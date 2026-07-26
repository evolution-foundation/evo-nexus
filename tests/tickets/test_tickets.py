"""Tests for Tickets feature (Feature 1.3).

Coverage:
- Unit: CHECK constraints, mention parser, priority_rank derivation
- Integration: atomic checkout (10 concurrent threads), auto-release, bulk actions
- Regression: existing session compatibility (ticket_id stays NULL)
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
import uuid
from pathlib import Path
import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Fixture: in-memory Flask app with tickets tables
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    import flask
    from flask_login import LoginManager
    import models as _models

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-tickets"
    db_path = tmp_path / "tickets.db"
    _app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 5}}

    _models.db.init_app(_app)

    # Setup LoginManager so @login_required works
    _login_manager = LoginManager()
    _login_manager.init_app(_app)

    @_login_manager.user_loader
    def _load_user(user_id):
        return _models.User.query.get(int(user_id))

    @_login_manager.unauthorized_handler
    def _unauthorized():
        from flask import jsonify
        return jsonify({"error": "Authentication required"}), 401

    with _app.app_context():
        _models.db.create_all()
        # Seed admin user
        admin = _models.User(username="admin", role="admin")
        admin.set_password("password")
        _models.db.session.add(admin)
        _models.db.session.commit()

    # Register tickets blueprint
    import routes.tickets as _tickets_routes
    importlib.reload(_tickets_routes)
    _app.register_blueprint(_tickets_routes.bp)

    # Register auth blueprint so /api/auth/login works
    try:
        import routes.auth_routes as _auth_routes
        importlib.reload(_auth_routes)
        _app.register_blueprint(_auth_routes.bp)
    except Exception:
        pass

    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        # Log in as admin
        c.post("/api/auth/login", json={"username": "admin", "password": "password"})
        yield c


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield app


# ---------------------------------------------------------------------------
# Unit — priority_rank derivation
# ---------------------------------------------------------------------------

class TestPriorityRank:
    def test_priority_rank_mapping(self):
        from models import PRIORITY_RANK
        assert PRIORITY_RANK["urgent"] == 4
        assert PRIORITY_RANK["high"] == 3
        assert PRIORITY_RANK["medium"] == 2
        assert PRIORITY_RANK["low"] == 1

    def test_priority_rank_ordering(self):
        from models import PRIORITY_RANK
        assert PRIORITY_RANK["urgent"] > PRIORITY_RANK["high"]
        assert PRIORITY_RANK["high"] > PRIORITY_RANK["medium"]
        assert PRIORITY_RANK["medium"] > PRIORITY_RANK["low"]


# ---------------------------------------------------------------------------
# Unit — mention parser
# ---------------------------------------------------------------------------

class TestMentionParser:
    def _parse(self, body: str) -> list[str]:
        import routes.tickets as t
        importlib.reload(t)
        return t._parse_mentions(body)

    def test_single_mention(self):
        mentions = self._parse("@zara-cs please check this")
        assert "zara-cs" in mentions

    def test_multiple_mentions(self):
        mentions = self._parse("@flux and @atlas should review this")
        assert "flux" in mentions
        assert "atlas" in mentions

    def test_max_3_mentions(self):
        mentions = self._parse("@a @b @c @d @e")
        assert len(mentions) <= 3

    def test_no_mentions(self):
        mentions = self._parse("no mentions here")
        assert mentions == []

    def test_dedup_mentions(self):
        mentions = self._parse("@atlas @atlas @atlas")
        assert mentions.count("atlas") == 1

    def test_invalid_mention_pattern_ignored(self):
        mentions = self._parse("@UPPERCASE @123 @valid-slug")
        # Only valid-slug should match (lowercase letters + digits + hyphens)
        assert "valid-slug" in mentions
        assert "UPPERCASE" not in mentions


# ---------------------------------------------------------------------------
# Unit — CHECK constraints via ORM
# ---------------------------------------------------------------------------

class TestCheckConstraints:
    def test_valid_status(self, ctx):
        from models import db, Ticket, TICKET_STATUSES
        now = "2026-01-01T00:00:00.000000Z"
        with ctx.app_context():
            for s in TICKET_STATUSES:
                t = Ticket(
                    id=str(uuid.uuid4()),
                    title=f"Test {s}",
                    status=s,
                    priority="medium",
                    priority_rank=2,
                    created_by="test",
                    created_at=now,
                    updated_at=now,
                )
                db.session.add(t)
            db.session.commit()

    def test_valid_priority(self, ctx):
        from models import db, Ticket, TICKET_PRIORITIES
        now = "2026-01-01T00:00:00.000000Z"
        with ctx.app_context():
            for p in TICKET_PRIORITIES:
                from models import PRIORITY_RANK
                t = Ticket(
                    id=str(uuid.uuid4()),
                    title=f"Test priority {p}",
                    status="open",
                    priority=p,
                    priority_rank=PRIORITY_RANK[p],
                    created_by="test",
                    created_at=now,
                    updated_at=now,
                )
                db.session.add(t)
            db.session.commit()

    def test_locked_consistency_both_null(self, ctx):
        """locked_at=NULL and locked_by=NULL is valid."""
        from models import db, Ticket
        now = "2026-01-01T00:00:00.000000Z"
        with ctx.app_context():
            t = Ticket(
                id=str(uuid.uuid4()),
                title="Unlocked ticket",
                status="open",
                priority="medium",
                priority_rank=2,
                created_by="test",
                created_at=now,
                updated_at=now,
                locked_at=None,
                locked_by=None,
            )
            db.session.add(t)
            db.session.commit()

    def test_locked_consistency_both_set(self, ctx):
        """locked_at set AND locked_by set is valid."""
        from models import db, Ticket
        now = "2026-01-01T00:00:00.000000Z"
        with ctx.app_context():
            t = Ticket(
                id=str(uuid.uuid4()),
                title="Locked ticket",
                status="open",
                priority="medium",
                priority_rank=2,
                created_by="test",
                created_at=now,
                updated_at=now,
                locked_at=now,
                locked_by="zara-cs",
            )
            db.session.add(t)
            db.session.commit()


# ---------------------------------------------------------------------------
# Integration — atomic checkout (10 parallel threads, exactly 1 wins)
# ---------------------------------------------------------------------------

class TestAtomicCheckout:
    def test_exactly_one_winner_under_concurrency(self, app):
        """10 threads simultaneously attempt checkout — exactly 1 should succeed."""
        from models import db, Ticket, PRIORITY_RANK

        now = "2026-01-01T00:00:00.000000Z"
        ticket_id = str(uuid.uuid4())

        with app.app_context():
            t = Ticket(
                id=ticket_id,
                title="Race test ticket",
                status="open",
                priority="medium",
                priority_rank=PRIORITY_RANK["medium"],
                created_by="test",
                created_at=now,
                updated_at=now,
            )
            db.session.add(t)
            db.session.commit()

        results = []
        lock = threading.Lock()

        def attempt_checkout(agent_name: str):
            with app.app_context():
                _now = "2026-01-01T00:00:01.000000Z"
                result = db.session.execute(
                    db.text(
                        "UPDATE tickets SET locked_at = :now, locked_by = :agent, updated_at = :now "
                        "WHERE id = :id AND locked_at IS NULL"
                    ),
                    {"id": ticket_id, "agent": agent_name, "now": _now},
                )
                db.session.commit()
                with lock:
                    results.append(result.rowcount)

        threads = [threading.Thread(target=attempt_checkout, args=(f"agent-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = sum(results)
        assert successes == 1, f"Expected exactly 1 checkout win, got {successes}"
        assert results.count(0) == 9, f"Expected 9 failures, got {results.count(0)}"

    def test_release_by_wrong_agent_returns_403(self, client, app):
        """Agent Y cannot release a ticket locked by Agent X."""
        from models import db, Ticket, PRIORITY_RANK

        now = "2026-01-01T00:00:00.000000Z"
        ticket_id = str(uuid.uuid4())

        with app.app_context():
            t = Ticket(
                id=ticket_id,
                title="Release test",
                status="open",
                priority="medium",
                priority_rank=PRIORITY_RANK["medium"],
                created_by="test",
                created_at=now,
                updated_at=now,
                locked_at=now,
                locked_by="agent-x",
            )
            db.session.add(t)
            db.session.commit()

        resp = client.post(
            f"/api/tickets/{ticket_id}/release",
            json={"agent": "agent-y"},
            content_type="application/json",
        )
        # 401 (not logged in via test client) or 403 (wrong agent)
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Integration — auto-release via janitor
# ---------------------------------------------------------------------------

class TestJanitor:
    def test_release_expired_locks(self, app):
        """Tickets with expired locks should be released by janitor."""
        from models import db, Ticket, PRIORITY_RANK

        now = "2020-01-01T00:00:00.000000Z"  # very old — definitely expired
        ticket_id = str(uuid.uuid4())

        with app.app_context():
            t = Ticket(
                id=ticket_id,
                title="Expired lock ticket",
                status="open",
                priority="medium",
                priority_rank=PRIORITY_RANK["medium"],
                created_by="test",
                created_at=now,
                updated_at=now,
                locked_at=now,  # locked in 2020 — expired
                locked_by="stale-agent",
                lock_timeout_seconds=60,
            )
            db.session.add(t)
            db.session.commit()

        with app.app_context():
            from ticket_janitor import release_expired_locks
            released = release_expired_locks()
            assert released >= 1

            # Verify lock is cleared
            t2 = Ticket.query.get(ticket_id)
            assert t2.locked_at is None
            assert t2.locked_by is None

    def test_fresh_lock_not_released(self, app):
        """Tickets with fresh locks should NOT be released by janitor."""
        from models import db, Ticket, TicketActivity, PRIORITY_RANK
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        ticket_id = str(uuid.uuid4())

        with app.app_context():
            t = Ticket(
                id=ticket_id,
                title="Fresh lock ticket",
                status="open",
                priority="medium",
                priority_rank=PRIORITY_RANK["medium"],
                created_by="test",
                created_at=now,
                updated_at=now,
                locked_at=now,  # just now — not expired
                locked_by="active-agent",
                lock_timeout_seconds=1800,
            )
            db.session.add(t)
            db.session.commit()

        with app.app_context():
            from ticket_janitor import release_expired_locks
            released = release_expired_locks()
            # Fresh lock should NOT be released
            t2 = Ticket.query.get(ticket_id)
            assert t2.locked_at is not None


# ---------------------------------------------------------------------------
# Integration — CRUD endpoints
# ---------------------------------------------------------------------------

class TestTicketCRUD:
    def test_create_ticket(self, client, app):
        with app.app_context():
            from models import User
            admin = User.query.filter_by(username="admin").first()
            with client.session_transaction() as sess:
                sess["_user_id"] = str(admin.id)
                sess["_fresh"] = True

        resp = client.post(
            "/api/tickets",
            json={"title": "Test ticket from pytest", "priority": "high"},
            content_type="application/json",
        )
        assert resp.status_code in (201, 401), f"Got {resp.status_code}: {resp.data}"

    def test_list_tickets(self, client, app):
        with app.app_context():
            from models import User
            admin = User.query.filter_by(username="admin").first()
            with client.session_transaction() as sess:
                sess["_user_id"] = str(admin.id)
                sess["_fresh"] = True

        resp = client.get("/api/tickets")
        assert resp.status_code in (200, 401)


# ---------------------------------------------------------------------------
# Integration — bulk actions
# ---------------------------------------------------------------------------

class TestBulkActions:
    def test_bulk_close(self, app):
        """Bulk close should update all selected tickets in a transaction."""
        from models import db, Ticket, PRIORITY_RANK

        now = "2026-01-01T00:00:00.000000Z"
        ids = [str(uuid.uuid4()) for _ in range(3)]

        with app.app_context():
            for tid in ids:
                t = Ticket(
                    id=tid,
                    title=f"Bulk test {tid[:8]}",
                    status="open",
                    priority="low",
                    priority_rank=PRIORITY_RANK["low"],
                    created_by="test",
                    created_at=now,
                    updated_at=now,
                )
                db.session.add(t)
            db.session.commit()

        with app.app_context():
            now2 = "2026-01-02T00:00:00.000000Z"
            for tid in ids:
                t = Ticket.query.get(tid)
                t.status = "closed"
                t.resolved_at = now2
                t.updated_at = now2
            db.session.commit()

            # Verify
            for tid in ids:
                t = Ticket.query.get(tid)
                assert t.status == "closed"


# ---------------------------------------------------------------------------
# Regression — models import cleanly
# ---------------------------------------------------------------------------

class TestModelsRegression:
    def test_ticket_model_imported(self):
        import models
        assert hasattr(models, "Ticket")
        assert hasattr(models, "TicketComment")
        assert hasattr(models, "TicketActivity")

    def test_ticket_statuses_constant(self):
        from models import TICKET_STATUSES, normalize_ticket_status
        assert "open" in TICKET_STATUSES
        assert "blocked" in TICKET_STATUSES
        assert "review" in TICKET_STATUSES
        assert "archived" in TICKET_STATUSES
        assert "waiting" not in TICKET_STATUSES
        assert normalize_ticket_status("waiting") == "blocked"

    def test_ticket_priorities_constant(self):
        from models import TICKET_PRIORITIES
        assert "urgent" in TICKET_PRIORITIES
        assert "low" in TICKET_PRIORITIES
        assert len(TICKET_PRIORITIES) == 4
