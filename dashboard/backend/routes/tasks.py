"""Scheduled task API and durable task execution lifecycle."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from models import RuntimeRunApproval, RuntimeRunEvidence, ScheduledTask, audit, db, has_permission
from secret_redaction import redact_secrets

TASK_TIMEOUT_SECONDS = 15 * 60
TASK_RECOVERY_TIMEOUT_SECONDS = TASK_TIMEOUT_SECONDS + 60
_RUNNING_PROCESSES: dict[int, subprocess.Popen] = {}


class _SpawnHandoffEntry:
    def __init__(self):
        self.lock = threading.Lock()
        self.users = 0


_SPAWN_HANDOFF_LOCKS: dict[int, _SpawnHandoffEntry] = {}
_SPAWN_HANDOFF_LOCKS_GUARD = threading.Lock()
_ANY_RUNTIME_RUN = object()
bp = Blueprint("tasks", __name__)


def _acquire_spawn_handoff(task_id: int) -> threading.Lock:
    with _SPAWN_HANDOFF_LOCKS_GUARD:
        entry = _SPAWN_HANDOFF_LOCKS.setdefault(task_id, _SpawnHandoffEntry())
        entry.users += 1
    entry.lock.acquire()
    return entry.lock


def _release_spawn_handoff(task_id: int, lock: threading.Lock) -> None:
    lock.release()
    with _SPAWN_HANDOFF_LOCKS_GUARD:
        entry = _SPAWN_HANDOFF_LOCKS.get(task_id)
        if entry is None or entry.lock is not lock:
            raise RuntimeError("Spawn handoff registry lost its active lock")
        entry.users -= 1
        if entry.users == 0:
            _SPAWN_HANDOFF_LOCKS.pop(task_id, None)


@contextmanager
def _spawn_handoff_lock(task_id: int):
    lock = _acquire_spawn_handoff(task_id)
    try:
        yield lock
    finally:
        _release_spawn_handoff(task_id, lock)


def _require(resource: str, action: str):
    if not has_permission(current_user.role, resource, action):
        return jsonify({"error": "Forbidden"}), 403
    return None


def _validate_hermes_profile_override(data: dict):
    if "hermes_profile" not in data:
        return None, None
    profile = data["hermes_profile"] or None
    if profile and not has_permission(current_user.role, "tasks", "manage"):
        return None, (jsonify({"error": "Forbidden"}), 403)
    if profile:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ADWs"))
            from hermes_profiles import is_valid_slug
            if not is_valid_slug(profile):
                return None, (jsonify({"error": "Invalid Hermes profile"}), 400)
        finally:
            sys.path.pop(0)
    return profile, None


def _validate_task_data(data: dict, *, partial: bool = False):
    required = ("name", "type", "payload", "scheduled_at")
    if not partial:
        missing = [field for field in required if not data.get(field)]
        if missing:
            return None, (jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400)
    if "name" in data and (not isinstance(data["name"], str) or not data["name"].strip()):
        return None, (jsonify({"error": "name must be a non-empty string"}), 400)
    if "type" in data and data["type"] not in ("skill", "prompt", "script"):
        return None, (jsonify({"error": "type must be skill, prompt, or script"}), 400)
    if "payload" in data and (not isinstance(data["payload"], str) or not data["payload"].strip()):
        return None, (jsonify({"error": "payload must be a non-empty string"}), 400)
    scheduled_at = None
    if "scheduled_at" in data:
        try:
            scheduled_at = datetime.fromisoformat(data["scheduled_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None, (jsonify({"error": "Invalid scheduled_at format (use ISO 8601)"}), 400)
        if scheduled_at.tzinfo is None:
            return None, (jsonify({"error": "scheduled_at must include timezone"}), 400)
    return scheduled_at, None


def claim_task(
    task_id: int,
    *,
    expected_status: str = "pending",
    expected_attempt: int | None = None,
    expected_runtime_run_id: str | None | object = _ANY_RUNTIME_RUN,
    clear_error: bool = False,
) -> bool:
    ownership = ScheduledTask.query.filter(
        ScheduledTask.id == task_id,
        ScheduledTask.status == expected_status,
    )
    if expected_attempt is not None:
        ownership = ownership.filter(ScheduledTask.attempt == expected_attempt)
    if expected_runtime_run_id is not _ANY_RUNTIME_RUN:
        if expected_runtime_run_id is None:
            ownership = ownership.filter(ScheduledTask.runtime_run_id.is_(None))
        else:
            ownership = ownership.filter(
                ScheduledTask.runtime_run_id == expected_runtime_run_id,
            )
    updates = {
        ScheduledTask.status: "running",
        ScheduledTask.started_at: datetime.now(timezone.utc),
        ScheduledTask.completed_at: None,
    }
    if clear_error:
        updates[ScheduledTask.error] = None
    claimed = ownership.update(updates, synchronize_session=False)
    db.session.commit()
    return claimed == 1


def claim_due_tasks(now: datetime | None = None) -> list[int]:
    now = now or datetime.now(timezone.utc)
    ids = [row.id for row in ScheduledTask.query.filter(
        ScheduledTask.status == "pending", ScheduledTask.scheduled_at <= now,
    ).all()]
    return [task_id for task_id in ids if claim_task(task_id)]


def _recover_stale_tasks_with_runs(
    max_age_seconds: int,
) -> tuple[int, int]:
    """Recover each stale scheduled task and its attached run as one unit."""
    from event_bus import publish
    from models import RuntimeRun

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    stale_tasks = ScheduledTask.query.filter(
        ScheduledTask.status == "running",
        ScheduledTask.started_at < cutoff,
    ).order_by(ScheduledTask.id).all()
    recovered_tasks = recovered_runs = 0

    for task in stale_tasks:
        original_attempt = task.attempt
        original_run_id = task.runtime_run_id
        task_status = "pending"
        task_error = "Recovered before external execution started"
        task_attempt = original_attempt + 1
        completed_at = None
        recovered_run = None
        expected_run_status = None
        run_error = None

        if original_run_id is not None:
            run = db.session.get(RuntimeRun, original_run_id)
            if run is None or run.attempt != original_attempt:
                db.session.rollback()
                continue
            if run.status == "awaiting_approval":
                db.session.rollback()
                continue
            if run.status in ("queued", "running", "cancel_requested"):
                expected_run_status = run.status
                external_outcome_unknown = run.status in ("running", "cancel_requested")
                run_error = (
                    "Worker lease expired; external outcome indeterminate"
                    if external_outcome_unknown
                    else "Worker lease expired before external execution"
                )
                completed_at = datetime.now(timezone.utc)
                recovered_run = run
                if external_outcome_unknown:
                    task_status = "failed"
                    task_error = run_error
                    task_attempt = original_attempt
            elif run.status in ("failed", "cancelled", "succeeded"):
                task_status = "failed"
                task_error = f"Attached runtime run is already {run.status}"
                task_attempt = original_attempt
                completed_at = datetime.now(timezone.utc)
            else:
                db.session.rollback()
                continue

        task_updates = {
            ScheduledTask.status: task_status,
            ScheduledTask.started_at: None,
            ScheduledTask.attempt: task_attempt,
            ScheduledTask.error: task_error,
        }
        if completed_at is not None:
            task_updates[ScheduledTask.completed_at] = completed_at
        task_query = ScheduledTask.query.filter(
            ScheduledTask.id == task.id,
            ScheduledTask.status == "running",
            ScheduledTask.attempt == original_attempt,
            ScheduledTask.started_at < cutoff,
        )
        if original_run_id is None:
            task_query = task_query.filter(ScheduledTask.runtime_run_id.is_(None))
        else:
            task_query = task_query.filter(ScheduledTask.runtime_run_id == original_run_id)
        task_updated = task_query.update(task_updates, synchronize_session="fetch")
        if task_updated != 1:
            db.session.rollback()
            continue

        if recovered_run is not None:
            run_updated = RuntimeRun.query.filter(
                RuntimeRun.id == recovered_run.id,
                RuntimeRun.attempt == original_attempt,
                RuntimeRun.status == expected_run_status,
            ).update(
                {
                    RuntimeRun.status: "failed",
                    RuntimeRun.completed_at: completed_at,
                    RuntimeRun.error: run_error,
                    RuntimeRun.exit_code: -1,
                },
                synchronize_session="fetch",
            )
            if run_updated != 1:
                db.session.rollback()
                continue
            publish(
                "run.failed",
                f"run:{recovered_run.id}",
                recovered_run.correlation_id,
                {"task_id": recovered_run.task_id, "attempt": recovered_run.attempt},
            )
        db.session.commit()
        recovered_tasks += 1
        recovered_runs += int(recovered_run is not None)

    return recovered_tasks, recovered_runs


def recover_stale_tasks(max_age_seconds: int = TASK_RECOVERY_TIMEOUT_SECONDS) -> int:
    recovered_tasks, _ = _recover_stale_tasks_with_runs(max_age_seconds)
    return recovered_tasks


def recover_interrupted_work(
    *,
    task_timeout_seconds: int = TASK_RECOVERY_TIMEOUT_SECONDS,
    run_lease_seconds: int = TASK_RECOVERY_TIMEOUT_SECONDS,
) -> dict[str, int]:
    """Recover durable task and runtime state left behind by a stopped worker."""
    from runtime_runs import recover_orphaned_runs

    recovered_tasks, attached_runs = _recover_stale_tasks_with_runs(task_timeout_seconds)
    return {
        "tasks": recovered_tasks,
        "runtime_runs": attached_runs + recover_orphaned_runs(run_lease_seconds),
    }


def _resolve_task_runtime(task: ScheduledTask) -> tuple[str, str | None, str | None]:
    adw_dir = str(Path(__file__).resolve().parents[3] / "ADWs")
    sys.path.insert(0, adw_dir)
    try:
        from runner import _get_provider_config
        provider, _ = _get_provider_config()
        profile = None
        if provider == "hermes":
            from hermes_profiles import resolve_profile
            profile, _ = resolve_profile(task.agent or task.type, task.hermes_profile)
        return provider, profile, None
    finally:
        sys.path.pop(0)


def _requirements_met(run_id: str) -> bool:
    return not RuntimeRunApproval.query.filter(
        RuntimeRunApproval.run_id == run_id,
        RuntimeRunApproval.status.in_(("pending", "rejected")),
    ).count()


def _kill_process_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass


def cancel_task_process(task_id: int) -> bool:
    process = _RUNNING_PROCESSES.get(task_id)
    if not process:
        return False
    _kill_process_group(process)
    return True


@bp.route("/api/tasks")
def list_tasks():
    denied = _require("tasks", "view")
    if denied:
        return denied
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 100)
    pagination = ScheduledTask.query.order_by(ScheduledTask.scheduled_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({"tasks": [task.to_dict() for task in pagination.items], "total": pagination.total, "pages": pagination.pages, "page": page})


@bp.route("/api/tasks/<int:task_id>")
def get_task(task_id):
    denied = _require("tasks", "view")
    if denied:
        return denied
    return jsonify(ScheduledTask.query.get_or_404(task_id).to_dict())


@bp.route("/api/tasks", methods=["POST"])
def create_task():
    denied = _require("tasks", "execute")
    if denied:
        return denied
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    scheduled_at, error = _validate_task_data(data)
    if error:
        return error
    profile, error = _validate_hermes_profile_override(data)
    if error:
        return error
    ticket_id = data.get("ticket_id")
    if ticket_id is not None:
        from models import Ticket
        if not isinstance(ticket_id, str):
            return jsonify({"error": "ticket_id must be a string"}), 400
        if not Ticket.query.get(ticket_id):
            return jsonify({"error": "ticket_id does not exist"}), 404
    task = ScheduledTask(name=data["name"], description=data.get("description"), type=data["type"], payload=data["payload"], agent=data.get("agent"), hermes_profile=profile, ticket_id=ticket_id, scheduled_at=scheduled_at, status="pending", created_by=current_user.id if current_user.is_authenticated else None)
    db.session.add(task)
    db.session.commit()
    audit(current_user, "create", "tasks", f"Created task #{task.id}: {task.name}")
    return jsonify(task.to_dict()), 201


@bp.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    denied = _require("tasks", "execute")
    if denied:
        return denied
    task = ScheduledTask.query.get_or_404(task_id)
    if task.status != "pending":
        return jsonify({"error": "Only pending tasks can be edited"}), 400
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    scheduled_at, error = _validate_task_data(data, partial=True)
    if error:
        return error
    profile, error = _validate_hermes_profile_override(data)
    if error:
        return error
    for field in ("name", "description", "type", "payload", "agent", "ticket_id"):
        if field in data:
            setattr(task, field, data[field])
    if "hermes_profile" in data:
        task.hermes_profile = profile
    if scheduled_at is not None:
        task.scheduled_at = scheduled_at
    db.session.commit()
    audit(current_user, "update", "tasks", f"Updated task #{task.id}: {task.name}")
    return jsonify(task.to_dict())


def _cancel_task_atomically(task: ScheduledTask) -> bool:
    with _spawn_handoff_lock(task.id):
        return _cancel_task_atomically_locked(task)


def _cancel_task_atomically_locked(task: ScheduledTask) -> bool:
    from event_bus import publish
    from models import RuntimeRun

    original_status = task.status
    if original_status not in ("pending", "failed", "running"):
        return False
    now = datetime.now(timezone.utc)
    query = ScheduledTask.query.filter(
        ScheduledTask.id == task.id,
        ScheduledTask.status == original_status,
        ScheduledTask.attempt == task.attempt,
    )
    if task.runtime_run_id is None:
        query = query.filter(ScheduledTask.runtime_run_id.is_(None))
    else:
        query = query.filter(ScheduledTask.runtime_run_id == task.runtime_run_id)
    updated = query.update(
        {
            ScheduledTask.status: "cancelled",
            ScheduledTask.completed_at: now,
        },
        synchronize_session="fetch",
    )
    if updated != 1:
        db.session.rollback()
        return False

    if task.runtime_run_id and original_status == "running":
        run = db.session.get(RuntimeRun, task.runtime_run_id)
        if run is not None and run.attempt == task.attempt:
            requested = RuntimeRun.query.filter(
                RuntimeRun.id == run.id,
                RuntimeRun.attempt == task.attempt,
                RuntimeRun.status == "running",
            ).update({RuntimeRun.status: "cancel_requested"}, synchronize_session="fetch")
            event_type = "run.cancel_requested"
            if requested != 1:
                cancelled = RuntimeRun.query.filter(
                    RuntimeRun.id == run.id,
                    RuntimeRun.attempt == task.attempt,
                    RuntimeRun.status.in_(("queued", "awaiting_approval", "cancel_requested")),
                ).update(
                    {
                        RuntimeRun.status: "cancelled",
                        RuntimeRun.completed_at: now,
                    },
                    synchronize_session="fetch",
                )
                if cancelled != 1:
                    db.session.rollback()
                    return False
                event_type = "run.cancelled"
            publish(
                event_type,
                f"run:{run.id}",
                run.correlation_id,
                {"task_id": run.task_id, "attempt": run.attempt},
            )
    db.session.commit()
    return True


@bp.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def cancel_task(task_id):
    denied = _require("tasks", "execute")
    if denied:
        return denied
    task = ScheduledTask.query.get_or_404(task_id)
    if task.status not in ("pending", "failed", "running"):
        return jsonify({"error": f"Cannot cancel task with status '{task.status}'"}), 400
    was_running = task.status == "running"
    if not _cancel_task_atomically(task):
        return jsonify({"error": "Task state changed before cancellation"}), 409
    if was_running:
        cancel_task_process(task.id)
    task = db.session.get(ScheduledTask, task_id)
    audit(current_user, "cancel", "tasks", f"Cancelled task #{task.id}: {task.name}")
    return jsonify(task.to_dict())


def _start_task(
    task_id: int,
    *,
    expected_status: str = "pending",
    expected_attempt: int | None = None,
    expected_runtime_run_id: str | None | object = _ANY_RUNTIME_RUN,
    clear_error: bool = False,
) -> bool:
    if not claim_task(
        task_id,
        expected_status=expected_status,
        expected_attempt=expected_attempt,
        expected_runtime_run_id=expected_runtime_run_id,
        clear_error=clear_error,
    ):
        return False
    app = current_app._get_current_object()
    threading.Thread(target=lambda: _execute_with_context(app, task_id), daemon=True).start()
    return True


def _execute_with_context(app, task_id: int):
    with app.app_context():
        _execute_task(task_id, already_claimed=True)


def _start_owned_execution(task_id: int, run, attempt: int) -> bool:
    """Atomically renew the task lease and start its attached run."""
    from event_bus import publish
    from models import RuntimeRun

    started_at = datetime.now(timezone.utc)
    task_updated = ScheduledTask.query.filter(
        ScheduledTask.id == task_id,
        ScheduledTask.status == "running",
        ScheduledTask.attempt == attempt,
        ScheduledTask.runtime_run_id == run.id,
    ).update(
        {ScheduledTask.started_at: started_at},
        synchronize_session="fetch",
    )
    if task_updated != 1:
        db.session.rollback()
        return False

    run_updated = RuntimeRun.query.filter(
        RuntimeRun.id == run.id,
        RuntimeRun.attempt == attempt,
        RuntimeRun.status == "queued",
    ).update(
        {
            RuntimeRun.status: "running",
            RuntimeRun.started_at: started_at,
            RuntimeRun.completed_at: None,
        },
        synchronize_session="fetch",
    )
    if run_updated != 1:
        db.session.rollback()
        return False

    publish(
        "run.running",
        f"run:{run.id}",
        run.correlation_id,
        {"task_id": task_id, "attempt": attempt},
    )
    db.session.commit()
    return True


def _renew_owned_execution(task_id: int, run, attempt: int) -> bool:
    """Revalidate and renew ownership immediately before an external effect."""
    from models import RuntimeRun

    renewed_at = datetime.now(timezone.utc)
    task_updated = ScheduledTask.query.filter(
        ScheduledTask.id == task_id,
        ScheduledTask.status == "running",
        ScheduledTask.attempt == attempt,
        ScheduledTask.runtime_run_id == run.id,
    ).update({ScheduledTask.started_at: renewed_at}, synchronize_session="fetch")
    if task_updated != 1:
        db.session.rollback()
        return False
    run_updated = RuntimeRun.query.filter(
        RuntimeRun.id == run.id,
        RuntimeRun.attempt == attempt,
        RuntimeRun.status == "running",
    ).update({RuntimeRun.started_at: renewed_at}, synchronize_session="fetch")
    if run_updated != 1:
        db.session.rollback()
        return False
    db.session.commit()
    return True


def _finalize_owned_execution(
    task_id: int,
    run,
    attempt: int,
    *,
    expected_task_status: str,
    task_status: str,
    run_status: str,
    summary: str | None = None,
    error: str | None = None,
    exit_code: int | None = None,
    previous_runtime_run_id: str | None = None,
    expected_run_statuses: tuple[str, ...] | None = None,
    require_approvals_met: bool = False,
) -> bool:
    """Atomically write terminal state only for the run that still owns the task."""
    from event_bus import publish
    from models import RuntimeRun

    now = datetime.now(timezone.utc)
    task_updates = {
        ScheduledTask.status: task_status,
        ScheduledTask.completed_at: now,
    }
    if summary is not None:
        task_updates[ScheduledTask.result_summary] = summary
    if error is not None:
        task_updates[ScheduledTask.error] = error

    task_query = ScheduledTask.query.filter(
        ScheduledTask.id == task_id,
        ScheduledTask.attempt == attempt,
        ScheduledTask.status == expected_task_status,
    )
    if run is None:
        if previous_runtime_run_id is None:
            task_query = task_query.filter(ScheduledTask.runtime_run_id.is_(None))
        else:
            task_query = task_query.filter(
                ScheduledTask.runtime_run_id == previous_runtime_run_id
            )
    else:
        task_query = task_query.filter(ScheduledTask.runtime_run_id == run.id)
    task_updated = task_query.update(task_updates, synchronize_session="fetch")
    if task_updated != 1:
        db.session.rollback()
        return False

    if run is not None:
        if expected_run_statuses is None:
            if run_status == "succeeded":
                expected_run_statuses = ("running",)
            elif run_status == "failed":
                expected_run_statuses = ("queued", "running")
            else:
                expected_run_statuses = (
                    "queued",
                    "running",
                    "awaiting_approval",
                    "cancel_requested",
                )
        run_updates = {
            RuntimeRun.status: run_status,
            RuntimeRun.completed_at: now,
        }
        if summary is not None:
            run_updates[RuntimeRun.result_summary] = summary[:5000]
        if error is not None:
            run_updates[RuntimeRun.error] = error[:2000]
        if exit_code is not None:
            run_updates[RuntimeRun.exit_code] = exit_code
        run_query = RuntimeRun.query.filter(
            RuntimeRun.id == run.id,
            RuntimeRun.attempt == attempt,
            RuntimeRun.status.in_(expected_run_statuses),
        )
        if require_approvals_met:
            blocking_approval = RuntimeRunApproval.query.filter(
                RuntimeRunApproval.run_id == run.id,
                RuntimeRunApproval.status.in_(("pending", "rejected")),
            ).exists()
            run_query = run_query.filter(~blocking_approval)
        run_updated = run_query.update(run_updates, synchronize_session="fetch")
        if run_updated != 1:
            db.session.rollback()
            return False

    if run is not None:
        publish(
            f"run.{run_status}",
            f"run:{run.id}",
            run.correlation_id,
            {"task_id": run.task_id, "attempt": run.attempt},
        )
    db.session.commit()
    return True


def _finish_owned_execution(
    task_id: int,
    run,
    attempt: int,
    *,
    task_status: str,
    run_status: str,
    summary: str | None = None,
    error: str | None = None,
    exit_code: int | None = None,
    previous_runtime_run_id: str | None = None,
) -> bool:
    if _finalize_owned_execution(
        task_id,
        run,
        attempt,
        expected_task_status="running",
        task_status=task_status,
        run_status=run_status,
        summary=summary,
        error=error,
        exit_code=exit_code,
        previous_runtime_run_id=previous_runtime_run_id,
        require_approvals_met=run_status == "succeeded",
    ):
        return True
    if run is not None and _finalize_owned_execution(
        task_id,
        run,
        attempt,
        expected_task_status="running",
        task_status="cancelled",
        run_status="cancelled",
        summary=summary,
        exit_code=exit_code,
        expected_run_statuses=("cancel_requested",),
    ):
        return True
    return _finalize_owned_execution(
        task_id,
        run,
        attempt,
        expected_task_status="cancelled",
        task_status="cancelled",
        run_status="cancelled",
        summary=summary,
        exit_code=exit_code,
        previous_runtime_run_id=previous_runtime_run_id,
    )


@bp.route("/api/tasks/<int:task_id>/run", methods=["POST"])
def run_task_now(task_id):
    denied = _require("tasks", "execute")
    if denied:
        return denied
    task = ScheduledTask.query.get_or_404(task_id)
    expected_status = task.status
    if expected_status not in {"pending", "failed"}:
        return jsonify({"error": f"Cannot run task with status '{task.status}'"}), 400
    if not _start_task(
        task.id,
        expected_status=expected_status,
        expected_attempt=task.attempt,
        expected_runtime_run_id=task.runtime_run_id,
        clear_error=expected_status == "failed",
    ):
        return jsonify({"error": "Task state changed before it could be claimed"}), 409
    audit(current_user, "run", "tasks", f"Manually triggered task #{task.id}: {task.name}")
    return jsonify(ScheduledTask.query.get(task.id).to_dict()), 202


def _execute_task(task_id: int, *, already_claimed: bool = False):
    if not already_claimed and not claim_task(task_id):
        return False
    task = ScheduledTask.query.get(task_id)
    if not task:
        return False
    from runtime_runs import create_run, transition
    run = None
    execution_attempt = task.attempt
    previous_runtime_run_id = task.runtime_run_id
    try:
        provider = profile = fallback = None
        if task.type != "script":
            provider, profile, fallback = _resolve_task_runtime(task)
        run = create_run(task.id, requested_profile=task.hermes_profile, resolved_profile=profile, provider=provider)
        ownership = ScheduledTask.query.filter(
            ScheduledTask.id == task_id,
            ScheduledTask.status == "running",
            ScheduledTask.attempt == execution_attempt,
        )
        if previous_runtime_run_id is None:
            ownership = ownership.filter(ScheduledTask.runtime_run_id.is_(None))
        else:
            ownership = ownership.filter(ScheduledTask.runtime_run_id == previous_runtime_run_id)
        attached = ownership.update(
            {
                ScheduledTask.provider: provider,
                ScheduledTask.resolved_profile: profile,
                ScheduledTask.workflow_policy: run.workflow_slug,
                ScheduledTask.fallback_reason: fallback,
                ScheduledTask.runtime_run_id: run.id,
                ScheduledTask.attempt: run.attempt,
            },
            synchronize_session="fetch",
        )
        db.session.commit()
        if attached != 1:
            transition(run, "cancelled", error="Task lease lost before execution", exit_code=-1)
            return False
        execution_attempt = run.attempt
        if not _start_owned_execution(task_id, run, execution_attempt):
            return False
        task = ScheduledTask.query.get(task_id)
        if task.type in ("skill", "prompt"):
            adw_dir = str(Path(__file__).resolve().parents[3] / "ADWs")
            sys.path.insert(0, adw_dir)
            try:
                from runner import run_claude, run_skill
                handoff = _acquire_spawn_handoff(task.id)
                handoff_held = True

                def track_process(process):
                    nonlocal handoff_held
                    _RUNNING_PROCESSES[task.id] = process
                    if handoff_held:
                        _release_spawn_handoff(task.id, handoff)
                        handoff_held = False

                try:
                    if not _renew_owned_execution(task_id, run, execution_attempt):
                        return False
                    result = run_skill(task.payload, log_name=f"task-{task.id}", timeout=TASK_TIMEOUT_SECONDS, agent=task.agent, profile=profile, on_process=track_process) if task.type == "skill" else run_claude(task.payload, log_name=f"task-{task.id}", timeout=TASK_TIMEOUT_SECONDS, agent=task.agent, profile=profile, on_process=track_process)
                finally:
                    if handoff_held:
                        _release_spawn_handoff(task.id, handoff)
                        handoff_held = False
            finally:
                sys.path.pop(0)
            summary = redact_secrets(result.get("stdout"), limit=5000)
            if result.get("success"):
                if not _requirements_met(run.id):
                    raise RuntimeError("Pending or denied approval blocks successful completion")
                return _finish_owned_execution(
                    task_id,
                    run,
                    execution_attempt,
                    task_status="completed",
                    run_status="succeeded",
                    summary=summary,
                    exit_code=result.get("returncode", 0),
                )
            error = redact_secrets(result.get("stderr") or "Task failed", limit=2000)
            return _finish_owned_execution(
                task_id,
                run,
                execution_attempt,
                task_status="failed",
                run_status="failed",
                summary=summary,
                error=error,
                exit_code=result.get("returncode", -1),
            )
        elif task.type == "script":
            script = (Path(__file__).resolve().parents[3] / "ADWs" / "routines" / task.payload).resolve()
            root = (Path(__file__).resolve().parents[3] / "ADWs" / "routines").resolve()
            if root not in script.parents or not script.is_file():
                raise FileNotFoundError(f"Script not found: {task.payload}")
            handoff = _spawn_handoff_lock(task.id)
            with handoff:
                if not _renew_owned_execution(task_id, run, execution_attempt):
                    return False
                process = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
                _RUNNING_PROCESSES[task.id] = process
            stdout, stderr = process.communicate(timeout=TASK_TIMEOUT_SECONDS)
            summary = redact_secrets(stdout, limit=5000)
            if process.returncode == 0:
                return _finish_owned_execution(
                    task_id,
                    run,
                    execution_attempt,
                    task_status="completed",
                    run_status="succeeded",
                    summary=summary,
                    exit_code=0,
                )
            error = redact_secrets(stderr or "Script failed", limit=2000)
            return _finish_owned_execution(
                task_id,
                run,
                execution_attempt,
                task_status="failed",
                run_status="failed",
                summary=summary,
                error=error,
                exit_code=process.returncode,
            )
    except subprocess.TimeoutExpired:
        process = _RUNNING_PROCESSES.get(task_id)
        if process:
            _kill_process_group(process)
        error = f"Timeout ({TASK_TIMEOUT_SECONDS}s)"
        return _finish_owned_execution(
            task_id,
            run,
            execution_attempt,
            task_status="failed",
            run_status="failed",
            error=error,
            exit_code=-1,
            previous_runtime_run_id=previous_runtime_run_id,
        )
    except Exception as exc:
        error = redact_secrets(exc, limit=2000)
        return _finish_owned_execution(
            task_id,
            run,
            execution_attempt,
            task_status="failed",
            run_status="failed",
            error=error,
            exit_code=-1,
            previous_runtime_run_id=previous_runtime_run_id,
        )
    finally:
        _RUNNING_PROCESSES.pop(task_id, None)
    return False
