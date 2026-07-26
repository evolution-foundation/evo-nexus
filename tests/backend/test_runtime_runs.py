from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def app():
    import models
    import runtime_runs
    importlib.reload(runtime_runs)
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    models.db.init_app(app)
    with app.app_context():
        models.db.create_all()
    return app


def test_state_machine_and_retry(app):
    from models import ScheduledTask
    from runtime_runs import add_evidence, create_run, decide_approval, recover_orphaned_runs, request_approval, transition
    with app.app_context():
        task = ScheduledTask(name="test", description="", type="prompt", payload="ok", scheduled_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        app.extensions["sqlalchemy"].session.add(task)
        app.extensions["sqlalchemy"].session.commit()
        run = create_run(task.id)
        assert run.status == "queued"
        transition(run, "running")
        transition(run, "failed", error="failure")
        assert run.status == "failed"
        retried = create_run(task.id)
        assert retried.attempt == 2 and retried.status == "queued"
        with pytest.raises(ValueError):
            transition(run, "running")


def test_approval_evidence_and_recovery(app):
    from models import ScheduledTask
    from runtime_runs import add_evidence, create_run, decide_approval, recover_orphaned_runs, request_approval, transition
    with app.app_context():
        task = ScheduledTask(name="test", description="", type="prompt", payload="ok", scheduled_at=datetime.now(timezone.utc))
        app.extensions["sqlalchemy"].session.add(task)
        app.extensions["sqlalchemy"].session.commit()
        run = create_run(task.id)
        transition(run, "running")
        approval = request_approval(run, "send message")
        assert run.status == "awaiting_approval"
        decide_approval(run, approval, approved=True, actor="admin")
        assert run.status == "running"
        evidence = add_evidence(run, "artifacts/output.json", "application/json", 42)
        assert evidence.checksum
        with pytest.raises(ValueError):
            add_evidence(run, "../secret")
        run.started_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        app.extensions["sqlalchemy"].session.commit()
        assert recover_orphaned_runs() == 1
        assert run.status == "failed"


def test_scheduled_task_links_to_ticket(app):
    from models import ScheduledTask, Ticket
    with app.app_context():
        ticket = Ticket(id="ticket-1", title="Investigate", status="open", priority="medium", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
        app.extensions["sqlalchemy"].session.add(ticket)
        app.extensions["sqlalchemy"].session.commit()

        task = ScheduledTask(name="fix", description="", type="prompt", payload="ok", scheduled_at=datetime.now(timezone.utc), ticket_id="ticket-1")
        app.extensions["sqlalchemy"].session.add(task)
        app.extensions["sqlalchemy"].session.commit()

        fetched = ScheduledTask.query.get(task.id)
        assert fetched.ticket_id == "ticket-1"
        assert fetched.to_dict()["ticket_id"] == "ticket-1"


def test_cancel_runtime_run_cancels_linked_running_task_atomically(app, monkeypatch):
    import routes.runtime_runs as runtime_routes
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run

    with app.app_context():
        task = ScheduledTask(
            name="cancel-linked-queued-run",
            type="prompt",
            payload="ok",
            status="running",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        run = create_run(task.id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        run_id = run.id
        task_id = task.id

        monkeypatch.setattr(runtime_routes, "_require", lambda _action: None)
        with app.test_request_context(method="POST"):
            response = runtime_routes.cancel_run(run_id)

        assert response.status_code == 200
        assert db.session.get(ScheduledTask, task_id).status == "cancelled"
        assert db.session.get(RuntimeRun, run_id).status == "cancelled"


def test_cancel_active_runtime_run_cancels_task_and_signals_process(app, monkeypatch):
    import routes.runtime_runs as runtime_routes
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, transition

    with app.app_context():
        task = ScheduledTask(
            name="cancel-linked-active-run",
            type="prompt",
            payload="ok",
            status="running",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        run = create_run(task.id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        run_id = run.id
        task_id = task.id
        signalled = []

        monkeypatch.setattr(runtime_routes, "_require", lambda _action: None)
        monkeypatch.setattr(
            "routes.tasks.cancel_task_process",
            lambda cancelled_task_id: signalled.append(cancelled_task_id) or True,
        )
        with app.test_request_context(method="POST"):
            response = runtime_routes.cancel_run(run_id)

        assert response.status_code == 200
        assert db.session.get(ScheduledTask, task_id).status == "cancelled"
        assert db.session.get(RuntimeRun, run_id).status == "cancel_requested"
        assert signalled == [task_id]


def test_cancel_awaiting_approval_runtime_run_signals_registered_process(app, monkeypatch):
    import routes.runtime_runs as runtime_routes
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, request_approval, transition

    with app.app_context():
        task = ScheduledTask(
            name="cancel-linked-awaiting-run",
            type="prompt",
            payload="ok",
            status="running",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        run = create_run(task.id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        request_approval(run, "publish")
        run_id = run.id
        task_id = task.id
        signalled = []

        monkeypatch.setattr(runtime_routes, "_require", lambda _action: None)
        monkeypatch.setattr(
            "routes.tasks.cancel_task_process",
            lambda cancelled_task_id: signalled.append(cancelled_task_id) or True,
        )
        with app.test_request_context(method="POST"):
            response = runtime_routes.cancel_run(run_id)

        assert response.status_code == 200
        assert db.session.get(ScheduledTask, task_id).status == "cancelled"
        assert db.session.get(RuntimeRun, run_id).status == "cancelled"
        assert signalled == [task_id]


def test_retry_failed_runtime_run_claims_task_without_pending_window(app, monkeypatch):
    import routes.runtime_runs as runtime_routes
    import routes.tasks as task_routes
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, transition

    class DeferredThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            return None

    with app.app_context():
        task = ScheduledTask(
            name="retry-linked-failed-run",
            type="prompt",
            payload="ok",
            status="running",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        run = create_run(task.id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        transition(run, "failed", error="failed")
        task.status = "failed"
        task.error = "failed"
        db.session.commit()
        run_id = run.id
        task_id = task.id

        monkeypatch.setattr(runtime_routes, "_require", lambda _action: None)
        monkeypatch.setattr(task_routes.threading, "Thread", DeferredThread)
        with app.test_request_context(method="POST"):
            response, status = runtime_routes.retry_run(run_id)

        assert status == 202
        assert response.get_json()["task"]["status"] == "running"
        assert db.session.get(ScheduledTask, task_id).status == "running"
        assert db.session.get(RuntimeRun, run_id).status == "failed"


def test_goal_linked_run_preserves_auditable_origin_and_evidence(app):
    from models import Goal, GoalProject, Mission, Ticket
    from runtime_runs import add_evidence, create_run

    with app.app_context():
        mission = Mission(slug="mission", title="Mission", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
        app.extensions["sqlalchemy"].session.add(mission)
        app.extensions["sqlalchemy"].session.flush()
        project = GoalProject(slug="project", mission_id=mission.id, title="Project", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
        app.extensions["sqlalchemy"].session.add(project)
        app.extensions["sqlalchemy"].session.flush()
        goal = Goal(slug="goal", project_id=project.id, title="Goal", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
        ticket = Ticket(id="ticket-1", title="Investigate", status="open", priority="medium", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z", goal_id=goal.id)
        app.extensions["sqlalchemy"].session.add_all([goal, ticket])
        app.extensions["sqlalchemy"].session.commit()

        run = create_run(
            origin_type="heartbeat",
            origin_id="atlas-4h",
            provider="hermes",
            resolved_profile="atlas",
            agent_slug="atlas-project",
            ticket_id=ticket.id,
            goal_id=goal.id,
        )
        evidence = add_evidence(run, "artifacts/heartbeat.json", "application/json", 42)

        payload = run.to_dict()
        assert payload["origin_type"] == "heartbeat"
        assert payload["origin_id"] == "atlas-4h"
        assert payload["runtime_provider"] == "hermes"
        assert payload["agent_slug"] == "atlas-project"
        assert payload["ticket_id"] == ticket.id
        assert payload["goal_id"] == goal.id
        assert evidence.run_id == run.id


def test_compute_metrics_aggregates_by_workflow(app):
    from models import ScheduledTask
    from runtime_runs import compute_metrics, create_run, transition
    with app.app_context():
        task = ScheduledTask(name="test", description="", type="prompt", payload="ok", scheduled_at=datetime.now(timezone.utc))
        app.extensions["sqlalchemy"].session.add(task)
        app.extensions["sqlalchemy"].session.commit()

        run_ok = create_run(task.id)
        transition(run_ok, "running")
        transition(run_ok, "succeeded")

        run_bad = create_run(task.id)
        transition(run_bad, "running")
        transition(run_bad, "failed", error="boom")

        metrics = compute_metrics()
        assert metrics["status_counts"]["succeeded"] == 1
        assert metrics["status_counts"]["failed"] == 1
        assert sum(metrics["status_counts"].values()) == 2
