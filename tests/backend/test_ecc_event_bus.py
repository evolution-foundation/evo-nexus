from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from flask import Flask

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def test_ecc_catalog_allowlist():
    from ecc_catalog import resolve_workflow
    workflow = resolve_workflow("bug")
    assert workflow["slug"] == "orch-fix-defect"
    assert workflow["version"] == 1
    assert len(workflow["sha256"]) == 64
    with pytest.raises(ValueError):
        resolve_workflow("bug", "../../etc/passwd")


def test_heartbeat_models_match_runtime_migration():
    from models import Heartbeat, HeartbeatRun

    assert "handler" in Heartbeat.__table__.columns
    expected_run_columns = {
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
    }
    assert expected_run_columns <= set(HeartbeatRun.__table__.columns.keys())


@pytest.fixture
def app():
    import models

    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    models.db.init_app(app)
    with app.app_context():
        models.db.create_all()
    return app


def test_outbox_and_webhook_deduplication(app):
    import event_bus
    importlib.reload(event_bus)
    with app.app_context():
        event = event_bus.publish("run.started", "run:1", "corr-1", {"safe": True})
        app.extensions["sqlalchemy"].session.commit()
        assert event.status == "pending"
        assert event_bus.accept_webhook("evolution", "event-1") is True
        assert event_bus.accept_webhook("evolution", "event-1") is False
        event_bus.mark_failed(event, max_attempts=1)
        assert event.status == "dead_letter"
