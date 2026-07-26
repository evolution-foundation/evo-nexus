from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from flask import Flask

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("HERMES_CONTROL_API_TOKEN", "control-test-token")
    monkeypatch.setenv("HERMES_CONTROL_API_SCOPES", "health:read,projects:read,tickets:read,tickets:write,comments:write,evidence:write,goals:read,goals:write,heartbeats:read,heartbeats:write,heartbeats:run,scheduled_tasks:read,scheduled_tasks:write,scheduled_tasks:run,routines:read,routines:run")
    import models
    import routes.control_api as control_api
    importlib.reload(control_api)
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    models.db.init_app(app)
    app.register_blueprint(control_api.bp)
    with app.app_context():
        models.db.create_all()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def headers(**extra):
    return {"Authorization": "Bearer control-test-token", **extra}


def test_list_control_routines_normalizes_legacy_metrics(client, monkeypatch, tmp_path):
    import routes._helpers as helpers

    metrics_path = tmp_path / "ADWs" / "logs"
    metrics_path.mkdir(parents=True)
    (metrics_path / "metrics.json").write_text('{"morning":{"runs":2,"successes":1,"total_seconds":30,"agent":"clawdia","total_cost_usd":1.5}}')
    monkeypatch.setattr(helpers, "WORKSPACE", tmp_path)
    monkeypatch.setattr(helpers, "discover_routines", lambda: {"morning": {"name": "Morning", "agent": "clawdia"}})

    response = client.get("/api/control/v1/routines", headers=headers())

    assert response.status_code == 200
    routine = response.json["data"][0]
    assert routine["runs"] == 2
    assert routine["success_rate"] == 50
    assert routine["total_cost_usd"] == 1.5


def test_requires_service_token(client):
    assert client.get("/api/control/v1/health").status_code == 401
    assert client.get("/api/control/v1/health", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_requires_scope(client, monkeypatch):
    monkeypatch.setenv("HERMES_CONTROL_API_SCOPES", "health:read")
    assert client.post("/api/control/v1/tickets", headers=headers(**{"Idempotency-Key": "a"}), json={"title": "x"}).status_code == 403


def test_create_ticket_is_idempotent(client):
    request_headers = headers(**{"Idempotency-Key": "create-1", "X-Correlation-ID": "corr-1"})
    payload = {"title": "Follow up", "description": "Safe"}
    created = client.post("/api/control/v1/tickets", headers=request_headers, json=payload)
    replayed = client.post("/api/control/v1/tickets", headers=request_headers, json=payload)
    assert created.status_code == 201
    assert replayed.status_code == 201
    assert created.json["data"]["id"] == replayed.json["data"]["id"]
    assert replayed.json["correlation_id"] == "corr-1"
    conflict = client.post("/api/control/v1/tickets", headers=request_headers, json={"title": "Other"})
    assert conflict.status_code == 409


def test_mutations_require_idempotency_key(client):
    created = client.post("/api/control/v1/tickets", headers=headers(**{"Idempotency-Key": "key-required"}), json={"title": "Task"})
    ticket_id = created.json["data"]["id"]
    assert client.patch(f"/api/control/v1/tickets/{ticket_id}", headers=headers(), json={"title": "Changed"}).status_code == 400
    assert client.post(f"/api/control/v1/tickets/{ticket_id}/comments", headers=headers(), json={"body": "Done"}).status_code == 400
    assert client.post(f"/api/control/v1/tickets/{ticket_id}/evidence", headers=headers(), json={"evidence": "run-123"}).status_code == 400


def test_ticket_timeline_checkout_release_and_conflict(client):
    created = client.post("/api/control/v1/tickets", headers=headers(**{"Idempotency-Key": "ticket-lock"}), json={"title": "Task"})
    ticket_id = created.json["data"]["id"]
    assert client.get(f"/api/control/v1/tickets/{ticket_id}/timeline", headers=headers()).status_code == 200

    lock_headers = headers(**{"Idempotency-Key": "lock-1", "X-Correlation-ID": "lock-correlation"})
    locked = client.post(f"/api/control/v1/tickets/{ticket_id}/checkout", headers=lock_headers, json={"agent": "zara-cs"})
    replayed = client.post(f"/api/control/v1/tickets/{ticket_id}/checkout", headers=lock_headers, json={"agent": "zara-cs"})
    assert locked.status_code == replayed.status_code == 200
    assert locked.json["data"]["locked_by"] == "zara-cs"
    assert client.post(f"/api/control/v1/tickets/{ticket_id}/checkout", headers=headers(**{"Idempotency-Key": "lock-2"}), json={"agent": "atlas-project"}).status_code == 409
    assert client.post(f"/api/control/v1/tickets/{ticket_id}/release", headers=headers(**{"Idempotency-Key": "release-wrong"}), json={"agent": "atlas-project"}).status_code == 403
    released = client.post(f"/api/control/v1/tickets/{ticket_id}/release", headers=headers(**{"Idempotency-Key": "release-1"}), json={"agent": "zara-cs"})
    assert released.status_code == 200
    assert released.json["data"]["locked_by"] is None


@pytest.mark.parametrize("lock_timeout", [True, "1800", 0, -1, 86401])
def test_checkout_rejects_invalid_lock_timeout(client, lock_timeout):
    created = client.post(
        "/api/control/v1/tickets",
        headers=headers(**{"Idempotency-Key": f"timeout-ticket-{lock_timeout}"}),
        json={"title": "Task"},
    )
    ticket_id = created.json["data"]["id"]

    response = client.post(
        f"/api/control/v1/tickets/{ticket_id}/checkout",
        headers=headers(**{"Idempotency-Key": f"timeout-lock-{lock_timeout}"}),
        json={"agent": "zara-cs", "lock_timeout_seconds": lock_timeout},
    )

    assert response.status_code == 400
    assert response.json["error"] == "invalid_lock_timeout_seconds"


def test_routine_reserves_idempotency_before_starting_subprocess(
    client, monkeypatch, tmp_path
):
    import subprocess
    import routes._helpers as helpers
    from models import ControlApiIdempotency

    routines_dir = tmp_path / "ADWs" / "routines"
    routines_dir.mkdir(parents=True)
    (routines_dir / "daily.py").write_text("print('ok')\n")
    monkeypatch.setattr(helpers, "WORKSPACE", tmp_path)
    monkeypatch.setattr(
        helpers,
        "discover_routines",
        lambda: {"daily": {"script": "daily.py"}},
    )
    secret = "routine-secret-value"
    monkeypatch.setenv("ROUTINE_API_KEY", secret)

    def fake_run(*args, **kwargs):
        reservation = ControlApiIdempotency.query.filter_by(
            operation="run_routine:daily", key="routine-once"
        ).one()
        assert reservation.response_status == 409
        assert reservation.response_json == '{"error": "request_in_progress"}'
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=f"ok {secret}", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    response = client.post(
        "/api/control/v1/routines/daily/run",
        headers=headers(**{"Idempotency-Key": "routine-once"}),
        json={},
    )

    assert response.status_code == 200
    assert secret not in response.get_data(as_text=True)
    assert "[REDACTED]" in response.get_data(as_text=True)
    replay = client.post(
        "/api/control/v1/routines/daily/run",
        headers=headers(**{"Idempotency-Key": "routine-once"}),
        json={},
    )
    assert replay.status_code == 200
    assert replay.json["data"] == response.json["data"]


def test_comments_evidence_and_validation(client):
    created = client.post("/api/control/v1/tickets", headers=headers(**{"Idempotency-Key": "ticket-1"}), json={"title": "Task"})
    ticket_id = created.json["data"]["id"]
    comment_headers = headers(**{"Idempotency-Key": "comment-1"})
    comment = client.post(f"/api/control/v1/tickets/{ticket_id}/comments", headers=comment_headers, json={"body": "Done"})
    replay = client.post(f"/api/control/v1/tickets/{ticket_id}/comments", headers=comment_headers, json={"body": "Done"})
    assert comment.status_code == replay.status_code == 201
    assert comment.json["data"]["id"] == replay.json["data"]["id"]
    invalid = client.post(f"/api/control/v1/tickets/{ticket_id}/evidence", headers=headers(**{"Idempotency-Key": "evidence-invalid"}), json={"evidence": "../../secret"})
    assert invalid.status_code == 400
    evidence = client.post(f"/api/control/v1/tickets/{ticket_id}/evidence", headers=headers(**{"Idempotency-Key": "evidence-1"}), json={"evidence": "run-123"})
    assert evidence.status_code == 201


def test_token_rotation_overlap_window(client, monkeypatch):
    monkeypatch.setenv("HERMES_CONTROL_API_TOKEN", "new-token")
    monkeypatch.setenv("HERMES_CONTROL_API_TOKEN_PREVIOUS", "control-test-token")
    old = client.get("/api/control/v1/health", headers=headers())
    new = client.get("/api/control/v1/health", headers={"Authorization": "Bearer new-token"})
    assert old.status_code == 200
    assert new.status_code == 200
    monkeypatch.delenv("HERMES_CONTROL_API_TOKEN_PREVIOUS", raising=False)
    expired = client.get("/api/control/v1/health", headers=headers())
    assert expired.status_code == 401


def test_control_audit_never_stores_token(client, app):
    client.post("/api/control/v1/tickets", headers=headers(**{"Idempotency-Key": "audit-1"}), json={"title": "Audited"})
    from models import ControlApiAuditLog
    with app.app_context():
        entry = ControlApiAuditLog.query.one()
        assert "control-test-token" not in " ".join((entry.operation, entry.resource, entry.correlation_id))


def test_goals_list_read(client, app):
    from models import Mission, GoalProject, Goal, GoalTask
    with app.app_context():
        now = "2026-07-25T00:00:00.000000Z"
        m = Mission(slug="m1", title="M1", created_at=now, updated_at=now)
        from models import db
        db.session.add(m)
        db.session.commit()
        p = GoalProject(slug="p1", title="P1", mission_id=m.id, created_at=now, updated_at=now)
        db.session.add(p)
        db.session.commit()
        g = Goal(slug="g1", title="G1", project_id=p.id, created_at=now, updated_at=now)
        db.session.add(g)
        db.session.commit()
        t = GoalTask(goal_id=g.id, title="T1", created_at=now, updated_at=now)
        db.session.add(t)
        db.session.commit()

    assert client.get("/api/control/v1/missions", headers=headers()).status_code == 200
    assert client.get("/api/control/v1/projects", headers=headers()).status_code == 200
    resp = client.get("/api/control/v1/goals", headers=headers())
    assert resp.status_code == 200
    assert len(resp.json["data"]) == 1
    assert resp.json["data"][0]["slug"] == "g1"
    resp = client.get("/api/control/v1/goal-tasks", headers=headers())
    assert resp.status_code == 200
    assert len(resp.json["data"]) == 1


def test_goal_update_idempotent(client, app):
    from models import Goal, GoalProject, Mission, db
    with app.app_context():
        now = "2026-07-25T00:00:00.000000Z"
        m = Mission(slug="m2", title="M2", created_at=now, updated_at=now)
        db.session.add(m)
        db.session.commit()
        p = GoalProject(slug="p2", title="P2", mission_id=m.id, created_at=now, updated_at=now)
        db.session.add(p)
        db.session.commit()
        g = Goal(slug="g2", title="G2", project_id=p.id, created_at=now, updated_at=now)
        db.session.add(g)
        db.session.commit()
        goal_id = g.id

    h = headers(**{"Idempotency-Key": "goal-update-1"})
    resp = client.patch(f"/api/control/v1/goals/{goal_id}", headers=h, json={"current_value": 42})
    assert resp.status_code == 200
    assert resp.json["data"]["current_value"] == 42
    replay = client.patch(f"/api/control/v1/goals/{goal_id}", headers=h, json={"current_value": 42})
    assert replay.status_code == 200
    assert replay.json["data"]["current_value"] == 42


def test_heartbeat_list_and_enable_disable(client, app):
    from models import Heartbeat, db
    with app.app_context():
        hb = Heartbeat(id="test-hb", agent="zara-cs", interval_seconds=3600, decision_prompt="Test", enabled=False)
        db.session.add(hb)
        db.session.commit()

    resp = client.get("/api/control/v1/heartbeats", headers=headers())
    assert resp.status_code == 200
    assert any(h["id"] == "test-hb" for h in resp.json["data"])

    resp = client.get("/api/control/v1/heartbeats/test-hb", headers=headers())
    assert resp.status_code == 200
    assert resp.json["data"]["enabled"] is False

    h = headers(**{"Idempotency-Key": "hb-enable-1"})
    resp = client.patch("/api/control/v1/heartbeats/test-hb/enabled", headers=h, json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json["data"]["enabled"] is True


def test_heartbeat_run_requires_scope(client, monkeypatch):
    monkeypatch.setenv("HERMES_CONTROL_API_SCOPES", "heartbeats:read")
    h = headers(**{"Idempotency-Key": "hb-run-no-scope"})
    resp = client.post("/api/control/v1/heartbeats/test-hb/run", headers=h, json={})
    assert resp.status_code == 403


def test_heartbeat_run_reserves_idempotency_before_dispatch(client, app, monkeypatch):
    import heartbeat_dispatcher
    from models import Heartbeat, db

    with app.app_context():
        db.session.add(
            Heartbeat(
                id="run-hb",
                agent="zara-cs",
                interval_seconds=3600,
                decision_prompt="Test",
                enabled=True,
            )
        )
        db.session.commit()

    dispatch_calls = []
    request_headers = headers(**{"Idempotency-Key": "hb-run-1"})

    def fake_dispatch(heartbeat_id, trigger):
        concurrent = client.post(
            "/api/control/v1/heartbeats/run-hb/run", headers=request_headers, json={}
        )
        assert concurrent.status_code == 409
        assert concurrent.json["data"]["error"] == "request_in_progress"
        dispatch_calls.append((heartbeat_id, trigger))
        return True, "run-1"

    monkeypatch.setattr(heartbeat_dispatcher, "dispatch", fake_dispatch)

    created = client.post(
        "/api/control/v1/heartbeats/run-hb/run", headers=request_headers, json={}
    )
    replayed = client.post(
        "/api/control/v1/heartbeats/run-hb/run", headers=request_headers, json={}
    )

    assert created.status_code == 202, created.json
    assert replayed.status_code == 202
    assert created.json["data"] == replayed.json["data"]
    assert dispatch_calls == [("run-hb", "manual")]


def test_heartbeat_run_failure_is_redacted_and_replayed(client, app, monkeypatch):
    import heartbeat_dispatcher
    from models import Heartbeat, db

    with app.app_context():
        db.session.add(
            Heartbeat(
                id="failing-hb",
                agent="zara-cs",
                interval_seconds=3600,
                decision_prompt="Test",
                enabled=True,
            )
        )
        db.session.commit()

    dispatch_calls = []
    secret = "token=" + "heartbeat-secret"

    def failing_dispatch(heartbeat_id, trigger):
        dispatch_calls.append((heartbeat_id, trigger))
        raise RuntimeError(secret)

    monkeypatch.setattr(heartbeat_dispatcher, "dispatch", failing_dispatch)
    request_headers = headers(**{"Idempotency-Key": "hb-run-failure-1"})

    failed = client.post(
        "/api/control/v1/heartbeats/failing-hb/run", headers=request_headers, json={}
    )
    replayed = client.post(
        "/api/control/v1/heartbeats/failing-hb/run", headers=request_headers, json={}
    )

    assert failed.status_code == 500
    assert replayed.status_code == 500
    assert secret not in failed.get_data(as_text=True)
    assert failed.json["data"] == replayed.json["data"]
    assert dispatch_calls == [("failing-hb", "manual")]


def test_scheduled_task_cancel_and_transitions(client, app):
    from models import ScheduledTask, db
    from datetime import datetime, timezone
    with app.app_context():
        task = ScheduledTask(name="cancel-me", type="prompt", payload="ok", scheduled_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
        db.session.add(task)
        db.session.commit()
        task_id = task.id

    resp = client.get(f"/api/control/v1/scheduled-tasks/{task_id}", headers=headers())
    assert resp.status_code == 200
    assert resp.json["data"]["status"] == "pending"

    h = headers(**{"Idempotency-Key": "cancel-task-1"})
    resp = client.post(f"/api/control/v1/scheduled-tasks/{task_id}/cancel", headers=h, json={})
    assert resp.status_code == 200
    assert resp.json["data"]["status"] == "cancelled"

    h2 = headers(**{"Idempotency-Key": "cancel-task-2"})
    resp = client.post(f"/api/control/v1/scheduled-tasks/{task_id}/cancel", headers=h2, json={})
    assert resp.status_code == 409


def test_scheduled_task_run_now(client, app):
    from models import ScheduledTask, db
    from datetime import datetime, timezone
    with app.app_context():
        task = ScheduledTask(name="run-me", type="prompt", payload="ok", scheduled_at=datetime(2026, 12, 1, tzinfo=timezone.utc), status="failed")
        db.session.add(task)
        db.session.commit()
        task_id = task.id

    h = headers(**{"Idempotency-Key": "run-task-1"})
    resp = client.post(f"/api/control/v1/scheduled-tasks/{task_id}/run", headers=h, json={})
    assert resp.status_code == 202
    assert resp.json["data"]["status"] == "pending"
    with app.app_context():
        queued = ScheduledTask.query.get(task_id)
        assert queued.scheduled_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)


def test_scheduled_task_cancel_reclaims_stale_idempotency_reservation(client, app):
    import hashlib
    import json
    from datetime import datetime, timedelta, timezone
    from models import ControlApiIdempotency, ScheduledTask, db

    with app.app_context():
        task = ScheduledTask(
            name="recover-idempotency",
            type="prompt",
            payload="ok",
            scheduled_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id
        reservation = ControlApiIdempotency(
            operation=f"cancel_scheduled_task:{task_id}",
            key="stale-cancel-reservation",
            request_hash=hashlib.sha256(b"{}").hexdigest(),
            response_status=409,
            response_json=json.dumps({"error": "request_in_progress"}),
            created_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
        )
        db.session.add(reservation)
        db.session.commit()

    response = client.post(
        f"/api/control/v1/scheduled-tasks/{task_id}/cancel",
        headers=headers(**{"Idempotency-Key": "stale-cancel-reservation"}),
        json={},
    )

    assert response.status_code == 200
    assert response.json["data"]["status"] == "cancelled"
    with app.app_context():
        reservation = ControlApiIdempotency.query.filter_by(
            operation=f"cancel_scheduled_task:{task_id}",
            key="stale-cancel-reservation",
        ).one()
        assert reservation.response_status == 200


def test_stale_external_reservation_does_not_repeat_unknown_effect(client, app, monkeypatch):
    import hashlib
    import heartbeat_dispatcher
    import json
    from datetime import datetime, timedelta, timezone
    from models import ControlApiIdempotency, Heartbeat, db

    with app.app_context():
        db.session.add(
            Heartbeat(
                id="stale-heartbeat",
                agent="zara-cs",
                interval_seconds=3600,
                decision_prompt="Test",
                enabled=True,
            )
        )
        db.session.add(
            ControlApiIdempotency(
                operation="run_heartbeat:stale-heartbeat",
                key="stale-external-reservation",
                request_hash=hashlib.sha256(b"{}").hexdigest(),
                response_status=409,
                response_json=json.dumps({"error": "request_in_progress"}),
                created_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                ),
            )
        )
        db.session.commit()

    monkeypatch.setattr(
        heartbeat_dispatcher,
        "dispatch",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("indeterminate external effect must not be repeated")
        ),
    )
    response = client.post(
        "/api/control/v1/heartbeats/stale-heartbeat/run",
        headers=headers(**{"Idempotency-Key": "stale-external-reservation"}),
        json={},
    )

    assert response.status_code == 409
    assert response.json["error"] == "idempotency_outcome_indeterminate"


def test_reclaimed_run_now_fences_the_previous_reservation_owner(app, monkeypatch):
    import threading
    from datetime import datetime, timedelta, timezone

    import routes.control_api as control_api
    from models import ControlApiAuditLog, ScheduledTask, db

    with app.app_context():
        task = ScheduledTask(
            name="fence-stale-run-now-owner",
            type="prompt",
            payload="ok",
            scheduled_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
            status="failed",
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id

    original_reserve = control_api._reserve_idempotency
    old_reserved = threading.Event()
    allow_old_owner = threading.Event()

    def pause_old_owner(operation, key, request_hash, correlation_id):
        reservation, replay = original_reserve(operation, key, request_hash, correlation_id)
        if threading.current_thread().name == "old-idempotency-owner" and reservation:
            reservation.created_at = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            db.session.commit()
            old_reserved.set()
            assert allow_old_owner.wait(timeout=10)
        return reservation, replay

    monkeypatch.setattr(control_api, "_reserve_idempotency", pause_old_owner)
    request_headers = headers(**{"Idempotency-Key": "reclaimed-run-now-owner"})
    old_result = {}

    def old_request():
        with app.test_client() as old_client:
            old_result["response"] = old_client.post(
                f"/api/control/v1/scheduled-tasks/{task_id}/run",
                headers=request_headers,
                json={},
            )

    old_thread = threading.Thread(target=old_request, name="old-idempotency-owner")
    old_thread.start()
    assert old_reserved.wait(timeout=10)

    with app.test_client() as new_client:
        new_response = new_client.post(
            f"/api/control/v1/scheduled-tasks/{task_id}/run",
            headers=request_headers,
            json={},
        )
    assert new_response.status_code == 202

    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        task.status = "failed"
        db.session.commit()

    allow_old_owner.set()
    old_thread.join(timeout=10)
    assert not old_thread.is_alive()
    assert old_result["response"].status_code == 409

    with app.app_context():
        assert db.session.get(ScheduledTask, task_id).status == "failed"
        assert ControlApiAuditLog.query.filter_by(
            operation="run_scheduled_task",
            resource=f"scheduled_task:{task_id}",
        ).count() == 1


def test_routines_list(client, monkeypatch):
    monkeypatch.setattr("routes.control_api.discover_routines", lambda: {"morning": {"name": "Good Morning", "agent": "clawdia", "custom": False, "script": "good_morning.py", "script_key": "good_morning"}}, raising=False)
    import routes.control_api as _mod
    from routes._helpers import discover_routines as _real
    monkeypatch.setattr(_mod, "discover_routines", lambda: {"morning": {"name": "Good Morning", "agent": "clawdia", "custom": False, "script": "good_morning.py", "script_key": "good_morning"}}, raising=False)
    resp = client.get("/api/control/v1/routines", headers=headers())
    assert resp.status_code == 200


def test_routines_run_requires_scope(client, monkeypatch):
    monkeypatch.setenv("HERMES_CONTROL_API_SCOPES", "routines:read")
    h = headers(**{"Idempotency-Key": "run-routine-no-scope"})
    resp = client.post("/api/control/v1/routines/morning/run", headers=h, json={})
    assert resp.status_code == 403
