"""Hermes Control API — service-authenticated, scoped internal integration."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from models import (
    ControlApiAuditLog, ControlApiIdempotency, GoalProject, Goal, GoalTask, Mission,
    Heartbeat, HeartbeatRun, ScheduledTask, Ticket, TicketActivity, TicketComment,
    TICKET_PRIORITIES, TICKET_STATUSES, db,
)
from event_bus import publish as publish_event
from secret_redaction import redact_secrets

bp = Blueprint("control_api", __name__)
ACTOR = "service:hermes"
MAX_TITLE = 500
MAX_DESCRIPTION = 20_000
MAX_COMMENT = 20_000
MAX_EVIDENCE = 8_000


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _idempotency_reservation_cutoff() -> str:
    try:
        lease_seconds = int(os.environ.get("EVONEXUS_IDEMPOTENCY_LEASE_SECONDS", "300"))
    except ValueError:
        lease_seconds = 300
    lease_seconds = max(1, lease_seconds)
    return (datetime.now(timezone.utc) - timedelta(seconds=lease_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _can_reclaim_idempotency(operation: str) -> bool:
    return operation.startswith(("cancel_scheduled_task:", "run_scheduled_task:"))


def _correlation_id() -> str:
    return request.headers.get("X-Correlation-ID", "").strip()[:128] or str(uuid.uuid4())


def _response(data=None, error=None, status=200, correlation_id=None):
    body = {"correlation_id": correlation_id or _correlation_id()}
    if error is not None:
        body["error"] = error
    else:
        body["data"] = data
    return jsonify(body), status


def _scopes() -> set[str]:
    return {scope.strip() for scope in os.environ.get("HERMES_CONTROL_API_SCOPES", "").split(",") if scope.strip()}


def _service_authenticated() -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    presented = header[7:].strip()
    for env_var in ("HERMES_CONTROL_API_TOKEN", "HERMES_CONTROL_API_TOKEN_PREVIOUS"):
        expected = os.environ.get(env_var, "").strip()
        if expected and secrets.compare_digest(presented, expected):
            return True
    return False


def _require(scope: str):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            correlation_id = _correlation_id()
            if not _service_authenticated():
                return _response(error="unauthorized", status=401, correlation_id=correlation_id)
            if scope not in _scopes():
                return _response(error="forbidden", status=403, correlation_id=correlation_id)
            return fn(*args, correlation_id=correlation_id, **kwargs)

        return wrapped

    return decorator


def _payload() -> tuple[dict | None, tuple | None]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, _response(error="invalid_json", status=400)
    return data, None


def _string(data: dict, key: str, maximum: int, required: bool = False) -> tuple[str | None, str | None]:
    value = data.get(key)
    if value is None:
        return (None, f"{key}_required") if required else (None, None)
    if not isinstance(value, str):
        return None, f"{key}_must_be_string"
    value = value.strip()
    if required and not value:
        return None, f"{key}_required"
    if len(value) > maximum:
        return None, f"{key}_too_long"
    return value or None, None


def _ticket_data(ticket: Ticket) -> dict:
    return ticket.to_dict()


def _audit(operation: str, resource: str, correlation_id: str, result: str) -> None:
    db.session.add(
        ControlApiAuditLog(
            operation=operation,
            resource=resource,
            outcome=result,
            correlation_id=correlation_id,
            created_at=_now(),
        )
    )
    publish_event(
        "control_api.mutation", resource, correlation_id,
        {"operation": operation, "outcome": result, "provider": "hermes", "agent": ACTOR},
    )


def _request_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _replay(operation: str, key: str, request_hash: str, correlation_id: str):
    existing = ControlApiIdempotency.query.filter_by(operation=operation, key=key).first()
    if existing is None:
        return None
    if not secrets.compare_digest(existing.request_hash, request_hash):
        return _response(error="idempotency_conflict", status=409, correlation_id=correlation_id)
    if (
        existing.response_status == 409
        and existing.response_json == json.dumps({"error": "request_in_progress"})
        and existing.created_at < _idempotency_reservation_cutoff()
    ):
        if _can_reclaim_idempotency(operation):
            return None
        return _response(
            error="idempotency_outcome_indeterminate",
            status=409,
            correlation_id=correlation_id,
        )
    return _response(json.loads(existing.response_json), status=existing.response_status, correlation_id=correlation_id)


def _record_idempotent(operation: str, key: str, request_hash: str, status: int, response: dict) -> None:
    db.session.add(
        ControlApiIdempotency(
            operation=operation,
            key=key,
            request_hash=request_hash,
            response_status=status,
            response_json=json.dumps(response),
            created_at=_now(),
        )
    )


def _reserve_idempotency(
    operation: str,
    key: str,
    request_hash: str,
    correlation_id: str,
):
    in_progress = json.dumps({"error": "request_in_progress"})
    reclaimed = 0
    owner_token = _now()
    if _can_reclaim_idempotency(operation):
        reclaimed = ControlApiIdempotency.query.filter(
            ControlApiIdempotency.operation == operation,
            ControlApiIdempotency.key == key,
            ControlApiIdempotency.request_hash == request_hash,
            ControlApiIdempotency.response_status == 409,
            ControlApiIdempotency.response_json == in_progress,
            ControlApiIdempotency.created_at < _idempotency_reservation_cutoff(),
        ).update(
            {ControlApiIdempotency.created_at: owner_token},
            synchronize_session="fetch",
        )
    if reclaimed == 1:
        db.session.commit()
        reservation = ControlApiIdempotency.query.filter_by(
            operation=operation,
            key=key,
        ).one()
        reservation._owner_token = owner_token
        return reservation, None

    reservation = ControlApiIdempotency(
        operation=operation,
        key=key,
        request_hash=request_hash,
        response_status=409,
        response_json=in_progress,
        created_at=owner_token,
    )
    db.session.add(reservation)
    try:
        # Commit the claim before any external effect. The unique constraint is
        # the cross-process arbiter for concurrent requests with the same key.
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        replay = _replay(operation, key, request_hash, correlation_id)
        if replay is None:
            replay = _response(
                error="request_in_progress", status=409, correlation_id=correlation_id
            )
        return None, replay
    reservation._owner_token = owner_token
    return reservation, None


def _claim_idempotency_owner(reservation: ControlApiIdempotency) -> bool:
    """Fence reclaimed reservation holders before a transactional mutation."""
    in_progress = json.dumps({"error": "request_in_progress"})
    owner_token = getattr(reservation, "_owner_token", reservation.created_at)
    claimed = ControlApiIdempotency.query.filter(
        ControlApiIdempotency.id == reservation.id,
        ControlApiIdempotency.created_at == owner_token,
        ControlApiIdempotency.response_status == 409,
        ControlApiIdempotency.response_json == in_progress,
    ).update(
        {ControlApiIdempotency.response_json: json.dumps({"error": "request_committing"})},
        synchronize_session="fetch",
    )
    if claimed != 1:
        db.session.rollback()
        return False
    return True


def _require_idempotency(operation: str, data: dict, correlation_id: str):
    key, error = _idempotency_key()
    if error:
        return None, None, error
    request_hash = _request_hash(data)
    replay = _replay(operation, key, request_hash, correlation_id)
    return key, request_hash, replay


def _idempotency_key() -> tuple[str | None, tuple | None]:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        return None, _response(error="idempotency_key_required", status=400)
    if len(key) > 255:
        return None, _response(error="idempotency_key_too_long", status=400)
    return key, None


@bp.route("/api/control/v1/health")
@_require("health:read")
def health(correlation_id: str):
    return _response({"dashboard": "ok", "hermes_configured": bool(os.environ.get("HERMES_CONTROL_API_TOKEN"))}, correlation_id=correlation_id)


@bp.route("/api/control/v1/projects/<int:project_id>")
@_require("projects:read")
def get_project(project_id: int, correlation_id: str):
    project = GoalProject.query.get(project_id)
    if project is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    return _response(project.to_dict(include_goals=True), correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets/<string:ticket_id>")
@_require("tickets:read")
def get_ticket(ticket_id: str, correlation_id: str):
    ticket = Ticket.query.get(ticket_id)
    if ticket is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    return _response(_ticket_data(ticket), correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets")
@_require("tickets:read")
def list_tickets(correlation_id: str):
    return _response([_ticket_data(ticket) for ticket in Ticket.query.order_by(Ticket.created_at.asc()).all()], correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets/<string:ticket_id>/timeline")
@_require("tickets:read")
def ticket_timeline(ticket_id: str, correlation_id: str):
    ticket = Ticket.query.get(ticket_id)
    if ticket is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    comments = [{**comment.to_dict(), "_type": "comment"} for comment in ticket.comments.order_by(TicketComment.created_at.asc()).all()]
    activities = [{**activity.to_dict(), "_type": "activity"} for activity in ticket.activity.order_by(TicketActivity.created_at.asc()).all()]
    timeline = sorted(comments + activities, key=lambda item: item["created_at"])
    return _response({"timeline": timeline, "total": len(timeline)}, correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets/<string:ticket_id>/checkout", methods=["POST"])
@_require("tickets:write")
def checkout_ticket(ticket_id: str, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    key, request_hash, replay = _require_idempotency(f"checkout_ticket:{ticket_id}", data, correlation_id)
    if replay:
        return replay
    if key is None:
        return request_hash
    agent, validation = _string(data, "agent", 100, required=True)
    if validation:
        return _response(error=validation, status=400, correlation_id=correlation_id)
    lock_timeout = data.get("lock_timeout_seconds", 1800)
    if (
        isinstance(lock_timeout, bool)
        or not isinstance(lock_timeout, int)
        or not 1 <= lock_timeout <= 86400
    ):
        return _response(
            error="invalid_lock_timeout_seconds",
            status=400,
            correlation_id=correlation_id,
        )

    locked_at = _now()
    updated = Ticket.query.filter(
        Ticket.id == ticket_id,
        Ticket.locked_at.is_(None),
    ).update(
        {
            Ticket.locked_at: locked_at,
            Ticket.locked_by: agent,
            Ticket.lock_timeout_seconds: lock_timeout,
            Ticket.updated_at: locked_at,
        },
        synchronize_session=False,
    )
    if updated != 1:
        if Ticket.query.filter_by(id=ticket_id).one_or_none() is None:
            return _response(error="not_found", status=404, correlation_id=correlation_id)
        return _response(error="already_locked", status=409, correlation_id=correlation_id)

    ticket = Ticket.query.filter_by(id=ticket_id).one()
    db.session.add(TicketActivity(id=str(uuid.uuid4()), ticket_id=ticket.id, actor=ACTOR, action="checkout", payload=json.dumps({"agent": agent} ), created_at=_now()))
    response = _ticket_data(ticket)
    _record_idempotent(f"checkout_ticket:{ticket_id}", key, request_hash, 200, response)
    _audit("checkout_ticket", f"ticket:{ticket.id}", correlation_id, "locked")
    db.session.commit()
    return _response(response, correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets/<string:ticket_id>/release", methods=["POST"])
@_require("tickets:write")
def release_ticket(ticket_id: str, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    key, request_hash, replay = _require_idempotency(f"release_ticket:{ticket_id}", data, correlation_id)
    if replay:
        return replay
    if key is None:
        return request_hash
    ticket = Ticket.query.get(ticket_id)
    if ticket is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    agent, validation = _string(data, "agent", 100, required=True)
    if validation:
        return _response(error=validation, status=400, correlation_id=correlation_id)
    if ticket.locked_by != agent:
        return _response(error="not_locked_by_you", status=403, correlation_id=correlation_id)
    ticket.locked_at = None
    ticket.locked_by = None
    ticket.updated_at = _now()
    db.session.add(TicketActivity(id=str(uuid.uuid4()), ticket_id=ticket.id, actor=ACTOR, action="release", payload=json.dumps({"agent": agent}), created_at=_now()))
    response = _ticket_data(ticket)
    _record_idempotent(f"release_ticket:{ticket_id}", key, request_hash, 200, response)
    _audit("release_ticket", f"ticket:{ticket.id}", correlation_id, "released")
    db.session.commit()
    return _response(response, correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets", methods=["POST"])
@_require("tickets:write")
def create_ticket(correlation_id: str):
    data, error = _payload()
    if error:
        return error
    key, request_hash, replay = _require_idempotency("create_ticket", data, correlation_id)
    if replay:
        return replay
    if key is None:
        return request_hash
    title, validation = _string(data, "title", MAX_TITLE, required=True)
    if validation:
        return _response(error=validation, status=400, correlation_id=correlation_id)
    description, validation = _string(data, "description", MAX_DESCRIPTION)
    if validation:
        return _response(error=validation, status=400, correlation_id=correlation_id)
    priority = data.get("priority", "medium")
    if priority not in TICKET_PRIORITIES:
        return _response(error="invalid_priority", status=400, correlation_id=correlation_id)
    status = data.get("status", "open")
    if status not in TICKET_STATUSES:
        return _response(error="invalid_status", status=400, correlation_id=correlation_id)

    now = _now()
    ticket = Ticket(
        id=str(uuid.uuid4()), title=title, description=description, priority=priority, status=status,
        created_at=now, updated_at=now,
    )
    response = _ticket_data(ticket)
    db.session.add(ticket)
    _record_idempotent("create_ticket", key, request_hash, 201, response)
    _audit("create_ticket", f"ticket:{ticket.id}", correlation_id, "created")
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _replay("create_ticket", key, request_hash, correlation_id)
    return _response(response, status=201, correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets/<string:ticket_id>", methods=["PATCH"])
@_require("tickets:write")
def update_ticket(ticket_id: str, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    key, request_hash, replay = _require_idempotency(f"update_ticket:{ticket_id}", data, correlation_id)
    if replay:
        return replay
    if key is None:
        return request_hash
    ticket = Ticket.query.get(ticket_id)
    if ticket is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    allowed = {"title", "description", "priority", "status"}
    if not data or set(data) - allowed:
        return _response(error="invalid_fields", status=400, correlation_id=correlation_id)
    if "title" in data:
        title, validation = _string(data, "title", MAX_TITLE, required=True)
        if validation:
            return _response(error=validation, status=400, correlation_id=correlation_id)
        ticket.title = title
    if "description" in data:
        description, validation = _string(data, "description", MAX_DESCRIPTION)
        if validation:
            return _response(error=validation, status=400, correlation_id=correlation_id)
        ticket.description = description
    if "priority" in data:
        if data["priority"] not in TICKET_PRIORITIES:
            return _response(error="invalid_priority", status=400, correlation_id=correlation_id)
        ticket.priority = data["priority"]
    if "status" in data:
        if data["status"] not in TICKET_STATUSES:
            return _response(error="invalid_status", status=400, correlation_id=correlation_id)
        ticket.status = data["status"]
    ticket.updated_at = _now()
    response = _ticket_data(ticket)
    _record_idempotent(f"update_ticket:{ticket_id}", key, request_hash, 200, response)
    _audit("update_ticket", f"ticket:{ticket.id}", correlation_id, "updated")
    db.session.commit()
    return _response(response, correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets/<string:ticket_id>/comments", methods=["POST"])
@_require("comments:write")
def create_comment(ticket_id: str, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    key, request_hash, replay = _require_idempotency(f"create_comment:{ticket_id}", data, correlation_id)
    if replay:
        return replay
    if key is None:
        return request_hash
    ticket = Ticket.query.get(ticket_id)
    if ticket is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    body, validation = _string(data, "body", MAX_COMMENT, required=True)
    if validation:
        return _response(error=validation, status=400, correlation_id=correlation_id)
    comment = TicketComment(id=str(uuid.uuid4()), ticket_id=ticket.id, author=ACTOR, body=body, created_at=_now())
    response = comment.to_dict()
    db.session.add(comment)
    _record_idempotent(f"create_comment:{ticket_id}", key, request_hash, 201, response)
    _audit("create_comment", f"ticket:{ticket.id}", correlation_id, "created")
    db.session.commit()
    return _response(response, status=201, correlation_id=correlation_id)


@bp.route("/api/control/v1/tickets/<string:ticket_id>/evidence", methods=["POST"])
@_require("evidence:write")
def create_evidence(ticket_id: str, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    key, request_hash, replay = _require_idempotency(f"create_evidence:{ticket_id}", data, correlation_id)
    if replay:
        return replay
    if key is None:
        return request_hash
    ticket = Ticket.query.get(ticket_id)
    if ticket is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    evidence, validation = _string(data, "evidence", MAX_EVIDENCE, required=True)
    if validation:
        return _response(error=validation, status=400, correlation_id=correlation_id)
    if ".." in evidence or evidence.startswith("/"):
        return _response(error="invalid_evidence", status=400, correlation_id=correlation_id)
    comment = TicketComment(id=str(uuid.uuid4()), ticket_id=ticket.id, author=ACTOR, body=f"Evidence: {evidence}", created_at=_now())
    response = comment.to_dict()
    db.session.add(comment)
    _record_idempotent(f"create_evidence:{ticket_id}", key, request_hash, 201, response)
    _audit("create_evidence", f"ticket:{ticket.id}", correlation_id, "created")
    db.session.commit()
    return _response(response, status=201, correlation_id=correlation_id)


@bp.route("/api/control/v1/missions")
@_require("goals:read")
def list_missions(correlation_id: str):
    return _response([mission.to_dict(include_projects=True) for mission in Mission.query.order_by(Mission.id).all()], correlation_id=correlation_id)


@bp.route("/api/control/v1/projects")
@_require("projects:read")
def list_projects(correlation_id: str):
    return _response([project.to_dict(include_goals=True) for project in GoalProject.query.order_by(GoalProject.id).all()], correlation_id=correlation_id)


@bp.route("/api/control/v1/goals")
@_require("goals:read")
def list_goals(correlation_id: str):
    return _response([goal.to_dict(include_tasks=True) for goal in Goal.query.order_by(Goal.id).all()], correlation_id=correlation_id)


@bp.route("/api/control/v1/goal-tasks")
@_require("goals:read")
def list_goal_tasks(correlation_id: str):
    return _response([task.to_dict() for task in GoalTask.query.order_by(GoalTask.id).all()], correlation_id=correlation_id)


def _controlled_mutation(operation: str, data: dict, correlation_id: str):
    key, request_hash, replay = _require_idempotency(operation, data, correlation_id)
    if replay:
        return None, replay
    if key is None:
        return None, request_hash
    return (key, request_hash), None


@bp.route("/api/control/v1/goals/<int:goal_id>", methods=["PATCH"])
@_require("goals:write")
def update_goal(goal_id: int, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    idem, replay = _controlled_mutation(f"update_goal:{goal_id}", data, correlation_id)
    if replay:
        return replay
    goal = Goal.query.get(goal_id)
    if goal is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    allowed = {"title", "description", "target_metric", "target_value", "current_value", "due_date", "status"}
    if not data or set(data) - allowed:
        return _response(error="invalid_fields", status=400, correlation_id=correlation_id)
    for key in allowed & data.keys():
        setattr(goal, key, data[key])
    goal.updated_at = _now()
    response = goal.to_dict(include_tasks=True)
    _record_idempotent(f"update_goal:{goal_id}", idem[0], idem[1], 200, response)
    _audit("update_goal", f"goal:{goal_id}", correlation_id, "updated")
    db.session.commit()
    return _response(response, correlation_id=correlation_id)


@bp.route("/api/control/v1/goal-tasks/<int:task_id>", methods=["PATCH"])
@_require("goals:write")
def update_goal_task(task_id: int, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    idem, replay = _controlled_mutation(f"update_goal_task:{task_id}", data, correlation_id)
    if replay:
        return replay
    task = GoalTask.query.get(task_id)
    if task is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    allowed = {"title", "description", "priority", "assignee_agent", "status", "due_date"}
    if not data or set(data) - allowed:
        return _response(error="invalid_fields", status=400, correlation_id=correlation_id)
    for key in allowed & data.keys():
        setattr(task, key, data[key])
    task.updated_at = _now()
    response = task.to_dict()
    _record_idempotent(f"update_goal_task:{task_id}", idem[0], idem[1], 200, response)
    _audit("update_goal_task", f"goal_task:{task_id}", correlation_id, "updated")
    db.session.commit()
    return _response(response, correlation_id=correlation_id)


@bp.route("/api/control/v1/heartbeats")
@_require("heartbeats:read")
def list_control_heartbeats(correlation_id: str):
    return _response([heartbeat.to_dict() for heartbeat in Heartbeat.query.order_by(Heartbeat.id).all()], correlation_id=correlation_id)


@bp.route("/api/control/v1/heartbeats/<string:heartbeat_id>")
@_require("heartbeats:read")
def get_control_heartbeat(heartbeat_id: str, correlation_id: str):
    heartbeat = Heartbeat.query.get(heartbeat_id)
    if heartbeat is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    return _response(heartbeat.to_dict(), correlation_id=correlation_id)


@bp.route("/api/control/v1/heartbeats/<string:heartbeat_id>/runs/<string:run_id>")
@_require("heartbeats:read")
def get_control_heartbeat_run(heartbeat_id: str, run_id: str, correlation_id: str):
    run = HeartbeatRun.query.filter_by(heartbeat_id=heartbeat_id, run_id=run_id).first()
    if run is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    return _response(run.to_dict(), correlation_id=correlation_id)


@bp.route("/api/control/v1/heartbeats/<string:heartbeat_id>/enabled", methods=["PATCH"])
@_require("heartbeats:write")
def set_control_heartbeat_enabled(heartbeat_id: str, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    idem, replay = _controlled_mutation(f"set_heartbeat_enabled:{heartbeat_id}", data, correlation_id)
    if replay:
        return replay
    heartbeat = Heartbeat.query.get(heartbeat_id)
    if heartbeat is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    if set(data) != {"enabled"} or not isinstance(data["enabled"], bool):
        return _response(error="invalid_enabled", status=400, correlation_id=correlation_id)
    heartbeat.enabled = data["enabled"]
    response = heartbeat.to_dict()
    _record_idempotent(f"set_heartbeat_enabled:{heartbeat_id}", idem[0], idem[1], 200, response)
    _audit("set_heartbeat_enabled", f"heartbeat:{heartbeat_id}", correlation_id, "enabled" if heartbeat.enabled else "disabled")
    db.session.commit()
    return _response(response, correlation_id=correlation_id)


@bp.route("/api/control/v1/heartbeats/<string:heartbeat_id>/run", methods=["POST"])
@_require("heartbeats:run")
def run_control_heartbeat(heartbeat_id: str, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    idem, replay = _controlled_mutation(f"run_heartbeat:{heartbeat_id}", data, correlation_id)
    if replay:
        return replay
    if Heartbeat.query.get(heartbeat_id) is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    assert idem is not None
    assert idem[1] is not None
    reservation, concurrent_replay = _reserve_idempotency(
        f"run_heartbeat:{heartbeat_id}", idem[0], idem[1], correlation_id
    )
    if concurrent_replay:
        return concurrent_replay
    assert reservation is not None
    from heartbeat_dispatcher import dispatch
    try:
        dispatched, run_id = dispatch(heartbeat_id, "manual")
        response = {"heartbeat_id": heartbeat_id, "run_id": run_id, "status": "dispatched" if dispatched else "not_dispatched"}
        status_code = 202
    except Exception as exc:
        response = {"heartbeat_id": heartbeat_id, "run_id": None, "status": "dispatch_failed", "error": redact_secrets(exc, limit=500)}
        status_code = 500
    reservation.response_status = status_code
    reservation.response_json = json.dumps(response)
    _audit("run_heartbeat", f"heartbeat:{heartbeat_id}", correlation_id, response["status"])
    db.session.commit()
    return _response(response, status=status_code, correlation_id=correlation_id)


@bp.route("/api/control/v1/scheduled-tasks")
@_require("scheduled_tasks:read")
def list_control_scheduled_tasks(correlation_id: str):
    return _response([task.to_dict() for task in ScheduledTask.query.order_by(ScheduledTask.scheduled_at.desc()).all()], correlation_id=correlation_id)


@bp.route("/api/control/v1/scheduled-tasks/<int:task_id>")
@_require("scheduled_tasks:read")
def get_control_scheduled_task(task_id: int, correlation_id: str):
    task = ScheduledTask.query.get(task_id)
    if task is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    return _response(task.to_dict(), correlation_id=correlation_id)


@bp.route("/api/control/v1/scheduled-tasks/<int:task_id>/cancel", methods=["POST"])
@_require("scheduled_tasks:write")
def cancel_control_scheduled_task(task_id: int, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    idem, replay = _controlled_mutation(f"cancel_scheduled_task:{task_id}", data, correlation_id)
    if replay:
        return replay
    task = ScheduledTask.query.get(task_id)
    if task is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    if task.status != "pending":
        error = "cannot_cancel_running" if task.status == "running" else "invalid_transition"
        return _response(error=error, status=409, correlation_id=correlation_id)
    assert idem is not None
    assert idem[1] is not None
    reservation, concurrent_replay = _reserve_idempotency(
        f"cancel_scheduled_task:{task_id}", idem[0], idem[1], correlation_id
    )
    if concurrent_replay:
        return concurrent_replay
    assert reservation is not None
    if not _claim_idempotency_owner(reservation):
        return _response(
            error="idempotency_lease_lost", status=409, correlation_id=correlation_id
        )
    updated = ScheduledTask.query.filter(
        ScheduledTask.id == task_id,
        ScheduledTask.status == "pending",
    ).update({ScheduledTask.status: "cancelled"}, synchronize_session="fetch")
    if updated != 1:
        reservation.response_status = 409
        reservation.response_json = json.dumps({"error": "invalid_transition"})
        db.session.commit()
        return _response(error="invalid_transition", status=409, correlation_id=correlation_id)
    task = db.session.get(ScheduledTask, task_id)
    response = task.to_dict()
    reservation.response_status = 200
    reservation.response_json = json.dumps(response)
    _audit("cancel_scheduled_task", f"scheduled_task:{task_id}", correlation_id, "cancelled")
    db.session.commit()
    return _response(response, correlation_id=correlation_id)


@bp.route("/api/control/v1/scheduled-tasks/<int:task_id>/run", methods=["POST"])
@_require("scheduled_tasks:run")
def run_control_scheduled_task(task_id: int, correlation_id: str):
    data, error = _payload()
    if error:
        return error
    idem, replay = _controlled_mutation(f"run_scheduled_task:{task_id}", data, correlation_id)
    if replay:
        return replay
    task = ScheduledTask.query.get(task_id)
    if task is None:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    if task.status not in {"pending", "failed"}:
        return _response(error="invalid_transition", status=409, correlation_id=correlation_id)
    assert idem is not None
    assert idem[1] is not None
    reservation, concurrent_replay = _reserve_idempotency(
        f"run_scheduled_task:{task_id}", idem[0], idem[1], correlation_id
    )
    if concurrent_replay:
        return concurrent_replay
    assert reservation is not None
    if not _claim_idempotency_owner(reservation):
        return _response(
            error="idempotency_lease_lost", status=409, correlation_id=correlation_id
        )
    updated = ScheduledTask.query.filter(
        ScheduledTask.id == task_id,
        ScheduledTask.status.in_(("pending", "failed")),
    ).update(
        {
            ScheduledTask.status: "pending",
            ScheduledTask.scheduled_at: datetime.now(timezone.utc),
        },
        synchronize_session="fetch",
    )
    if updated != 1:
        reservation.response_status = 409
        reservation.response_json = json.dumps({"error": "invalid_transition"})
        db.session.commit()
        return _response(error="invalid_transition", status=409, correlation_id=correlation_id)
    task = db.session.get(ScheduledTask, task_id)
    response = task.to_dict()
    reservation.response_status = 202
    reservation.response_json = json.dumps(response)
    _audit("run_scheduled_task", f"scheduled_task:{task_id}", correlation_id, "queued")
    db.session.commit()
    return _response(response, status=202, correlation_id=correlation_id)


# ────────────────── Routines ──────────────────────────────────────────────────

@bp.route("/api/control/v1/routines")
@_require("routines:read")
def list_control_routines(correlation_id: str):
    from routes._helpers import discover_routines, WORKSPACE, safe_read
    from routes.routines import normalize_metrics_entry
    import json as _json
    metrics_path = WORKSPACE / "ADWs" / "logs" / "metrics.json"
    metrics: dict = {}
    content = safe_read(metrics_path)
    if content:
        try:
            metrics = _json.loads(content) or {}
        except Exception:
            pass
    registry = discover_routines()
    result = []
    for make_id, spec in registry.items():
        entry = {
            "id": make_id,
            "name": spec.get("name") or make_id,
            "agent": spec.get("agent", ""),
            "custom": spec.get("custom", False),
        }
        m = metrics.get(make_id)
        if isinstance(m, dict):
            m = normalize_metrics_entry(m)
            entry["runs"] = m["runs"]
            entry["last_run"] = m.get("last_run")
            entry["success_rate"] = m["success_rate"]
            entry["total_cost_usd"] = m["total_cost_usd"]
        else:
            entry["runs"] = 0
            entry["last_run"] = None
            entry["success_rate"] = 0
            entry["total_cost_usd"] = 0.0
        result.append(entry)
    return _response(result, correlation_id=correlation_id)


@bp.route("/api/control/v1/routines/<string:routine_id>/logs")
@_require("routines:read")
def get_control_routine_logs(routine_id: str, correlation_id: str):
    from routes._helpers import discover_routines, WORKSPACE, safe_read
    import json as _json
    from datetime import date as _date
    registry = discover_routines()
    if routine_id not in registry:
        return _response(error="not_found", status=404, correlation_id=correlation_id)
    logs_dir = WORKSPACE / "ADWs" / "logs"
    target = request.args.get("date", _date.today().isoformat())
    entries = []
    if logs_dir.is_dir():
        for f in logs_dir.iterdir():
            if f.suffix == ".jsonl" and target in f.name:
                text = safe_read(f)
                if text:
                    for line in text.strip().splitlines():
                        try:
                            parsed = _json.loads(line)
                            if isinstance(parsed, dict) and parsed.get("routine") == routine_id:
                                entries.append(parsed)
                        except Exception:
                            continue
    return _response({"routine_id": routine_id, "date": target, "entries": entries[:200]}, correlation_id=correlation_id)


@bp.route("/api/control/v1/routines/<string:routine_id>/run", methods=["POST"])
@_require("routines:run")
def run_control_routine(routine_id: str, correlation_id: str):
    import subprocess
    from pathlib import Path
    from routes._helpers import discover_routines, WORKSPACE

    data, error = _payload()
    if error:
        return error
    idem, replay = _controlled_mutation(f"run_routine:{routine_id}", data, correlation_id)
    if replay:
        return replay
    assert idem is not None
    assert idem[1] is not None

    registry = discover_routines()
    if routine_id not in registry:
        return _response(error="not_found", status=404, correlation_id=correlation_id)

    script = registry[routine_id].get("script", "")
    script_path = (WORKSPACE / "ADWs" / "routines" / script).resolve()
    allowed_dir = (WORKSPACE / "ADWs" / "routines").resolve()
    if not str(script_path).startswith(str(allowed_dir)):
        return _response(error="invalid_script_path", status=400, correlation_id=correlation_id)
    if not script_path.is_file():
        return _response(error="script_not_found", status=404, correlation_id=correlation_id)

    reservation, concurrent_replay = _reserve_idempotency(
        f"run_routine:{routine_id}", idem[0], idem[1], correlation_id
    )
    if concurrent_replay:
        return concurrent_replay
    assert reservation is not None

    try:
        import shutil
        python = shutil.which("python3") or "python3"
        env = os.environ.copy()
        env["EVONEXUS_TRIGGERED_BY"] = "manual"
        proc = subprocess.run(
            [python, str(script_path)],
            capture_output=True, text=True, timeout=900, cwd=str(WORKSPACE), env=env,
        )
        response = {
            "routine_id": routine_id,
            "exit_code": proc.returncode,
            "stdout": redact_secrets(proc.stdout, limit=5000),
            "stderr": redact_secrets(proc.stderr, limit=2000),
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        response = {"routine_id": routine_id, "exit_code": -1, "stdout": "", "stderr": "timeout", "success": False}
    except Exception as exc:
        response = {"routine_id": routine_id, "exit_code": -1, "stdout": "", "stderr": redact_secrets(exc, limit=2000), "success": False}

    status_code = 200 if response["success"] else 500
    reservation.response_status = status_code
    reservation.response_json = json.dumps(response)
    _audit("run_routine", f"routine:{routine_id}", correlation_id, "success" if response["success"] else "failed")
    db.session.commit()
    return _response(response, status=status_code, correlation_id=correlation_id)
