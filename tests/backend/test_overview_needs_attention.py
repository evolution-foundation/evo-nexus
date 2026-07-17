"""
tests/backend/test_overview_needs_attention.py

Panorama 2026-07-17, item 19 — /api/overview gains a "needs_attention" block
aggregating 3 signals that previously required visiting 3 separate pages:
heartbeats whose LATEST run failed, tickets with a lock past its own
lock_timeout_seconds still visible (the janitor should have cleared it — if
it's still here, that's a bug worth surfacing), and approvals pending > 24h.

Run:
    cd /path/to/workspace && pytest tests/backend/test_overview_needs_attention.py -v
"""

from __future__ import annotations

import importlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def app():
    import flask
    from flask_login import LoginManager
    import models as _models
    importlib.reload(_models)

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-overview-attention"
    _app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _models.db.init_app(_app)

    _login_manager = LoginManager()
    _login_manager.init_app(_app)

    @_login_manager.user_loader
    def _load_user(user_id):
        return _models.User.query.get(int(user_id))

    with _app.app_context():
        _models.db.create_all()
        admin = _models.User(username="admin", role="admin")
        admin.set_password("password")
        _models.db.session.add(admin)
        _models.db.session.commit()

    import routes.overview as _overview_routes
    importlib.reload(_overview_routes)
    _app.register_blueprint(_overview_routes.bp)
    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        yield c


def test_empty_state_has_zero_total(client):
    resp = client.get("/api/overview")
    assert resp.status_code == 200
    attention = resp.get_json()["needs_attention"]
    assert attention == {
        "heartbeat_failures": [], "stale_locked_tickets": [], "aged_approvals": [], "total": 0,
    }


def test_heartbeat_with_latest_run_failed_is_flagged(client):
    import models as _models
    with client.application.app_context():
        hb = _models.Heartbeat(
            id="atlas-4h", agent="atlas-project", interval_seconds=14400,
            decision_prompt="check things", created_at=_iso(NOW), updated_at=_iso(NOW),
        )
        _models.db.session.add(hb)
        _models.db.session.commit()
        # older successful run, then a more recent failed run — only the
        # LATEST run's status should decide whether this heartbeat is flagged.
        _models.db.session.add(_models.HeartbeatRun(
            run_id=str(uuid.uuid4()), heartbeat_id="atlas-4h", status="success",
            started_at=_iso(NOW - timedelta(hours=2)),
        ))
        _models.db.session.add(_models.HeartbeatRun(
            run_id=str(uuid.uuid4()), heartbeat_id="atlas-4h", status="fail",
            error="subprocess timeout", started_at=_iso(NOW - timedelta(minutes=5)),
        ))
        _models.db.session.commit()

    resp = client.get("/api/overview")
    failures = resp.get_json()["needs_attention"]["heartbeat_failures"]
    assert len(failures) == 1
    assert failures[0]["heartbeat_id"] == "atlas-4h"
    assert "timeout" in failures[0]["error"]


def test_heartbeat_whose_latest_run_succeeded_is_not_flagged(client):
    import models as _models
    with client.application.app_context():
        hb = _models.Heartbeat(
            id="zara-2h", agent="zara-cs", interval_seconds=7200,
            decision_prompt="check things", created_at=_iso(NOW), updated_at=_iso(NOW),
        )
        _models.db.session.add(hb)
        _models.db.session.commit()
        _models.db.session.add(_models.HeartbeatRun(
            run_id=str(uuid.uuid4()), heartbeat_id="zara-2h", status="fail",
            error="old failure", started_at=_iso(NOW - timedelta(hours=3)),
        ))
        _models.db.session.add(_models.HeartbeatRun(
            run_id=str(uuid.uuid4()), heartbeat_id="zara-2h", status="success",
            started_at=_iso(NOW - timedelta(minutes=10)),
        ))
        _models.db.session.commit()

    resp = client.get("/api/overview")
    assert resp.get_json()["needs_attention"]["heartbeat_failures"] == []


def test_stale_locked_ticket_is_flagged(client):
    import models as _models
    with client.application.app_context():
        _models.db.session.add(_models.Ticket(
            id=str(uuid.uuid4()), title="Ticket travado", status="in_progress", priority="medium",
            priority_rank=2, locked_by="bolt-executor",
            locked_at=_iso(NOW - timedelta(hours=2)), lock_timeout_seconds=1800,  # 30min timeout, locked 2h ago
            created_at=_iso(NOW), updated_at=_iso(NOW),
        ))
        _models.db.session.commit()

    resp = client.get("/api/overview")
    stale = resp.get_json()["needs_attention"]["stale_locked_tickets"]
    assert len(stale) == 1
    assert stale[0]["title"] == "Ticket travado"


def test_fresh_locked_ticket_is_not_flagged(client):
    import models as _models
    with client.application.app_context():
        _models.db.session.add(_models.Ticket(
            id=str(uuid.uuid4()), title="Ticket recente", status="in_progress", priority="medium",
            priority_rank=2, locked_by="bolt-executor",
            locked_at=_iso(NOW - timedelta(minutes=2)), lock_timeout_seconds=1800,
            created_at=_iso(NOW), updated_at=_iso(NOW),
        ))
        _models.db.session.commit()

    resp = client.get("/api/overview")
    assert resp.get_json()["needs_attention"]["stale_locked_tickets"] == []


def test_approval_pending_over_24h_is_flagged(client):
    import models as _models
    with client.application.app_context():
        db_conn = _models.db.session
        db_conn.execute(_models.db.text(
            "INSERT INTO pending_approvals (gate_type, agent, attempt, idempotency_key, status, created_at, expires_at) "
            "VALUES ('publish', 'pixel-social-media', 0, :k, 'pending', :c, :e)"
        ), {"k": "test-key-1", "c": _iso(NOW - timedelta(hours=30)), "e": _iso(NOW + timedelta(hours=8))})
        db_conn.commit()

    resp = client.get("/api/overview")
    aged = resp.get_json()["needs_attention"]["aged_approvals"]
    assert len(aged) == 1
    assert aged[0]["gate_type"] == "publish"


def test_recent_approval_is_not_flagged(client):
    import models as _models
    with client.application.app_context():
        db_conn = _models.db.session
        db_conn.execute(_models.db.text(
            "INSERT INTO pending_approvals (gate_type, agent, attempt, idempotency_key, status, created_at, expires_at) "
            "VALUES ('publish', 'pixel-social-media', 0, :k, 'pending', :c, :e)"
        ), {"k": "test-key-2", "c": _iso(NOW - timedelta(hours=1)), "e": _iso(NOW + timedelta(hours=8))})
        db_conn.commit()

    resp = client.get("/api/overview")
    assert resp.get_json()["needs_attention"]["aged_approvals"] == []


def test_decided_approval_over_24h_old_is_not_flagged(client):
    """Only status='pending' counts — an old but already-decided approval
    must never show up as needing attention."""
    import models as _models
    with client.application.app_context():
        db_conn = _models.db.session
        db_conn.execute(_models.db.text(
            "INSERT INTO pending_approvals (gate_type, agent, attempt, idempotency_key, status, created_at, expires_at, decided_at) "
            "VALUES ('publish', 'pixel-social-media', 0, :k, 'approved', :c, :e, :d)"
        ), {"k": "test-key-3", "c": _iso(NOW - timedelta(hours=30)), "e": _iso(NOW + timedelta(hours=8)), "d": _iso(NOW - timedelta(hours=29))})
        db_conn.commit()

    resp = client.get("/api/overview")
    assert resp.get_json()["needs_attention"]["aged_approvals"] == []
