"""State machine for persistent task runtime runs."""

from __future__ import annotations

import hashlib
import sys
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, or_

from event_bus import publish
from models import RuntimeRun, RuntimeRunApproval, RuntimeRunEvidence, db

TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"awaiting_approval", "cancel_requested", "succeeded", "failed"},
    "awaiting_approval": {"running", "cancelled"},
    "cancel_requested": {"cancelled", "failed"},
    "failed": set(),
    "succeeded": set(),
    "cancelled": set(),
}
TERMINAL = {"succeeded", "failed", "cancelled"}


def create_run(
    task_id: int | None = None,
    *,
    origin_type: str | None = None,
    origin_id: str | None = None,
    ticket_id: str | None = None,
    goal_id: int | None = None,
    agent_slug: str | None = None,
    requested_profile: str | None = None,
    resolved_profile: str | None = None,
    provider: str | None = None,
    workflow_type: str | None = None,
) -> RuntimeRun:
    from ecc_catalog import resolve_workflow

    if task_id is None and not (origin_type and origin_id):
        raise ValueError("task_id or origin_type with origin_id is required")
    origin_type = origin_type or "scheduled_task"
    origin_id = origin_id or str(task_id)
    prior_query = RuntimeRun.query.filter_by(origin_type=origin_type, origin_id=origin_id)
    prior = prior_query.order_by(RuntimeRun.attempt.desc()).first()
    workflow = resolve_workflow(workflow_type) if workflow_type and task_id is not None else None
    if provider is None and workflow is not None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ADWs"))
        from runtime_policy import resolve_runtime
        decision = resolve_runtime(workflow["slug"], str(task_id))
        provider = decision["primary"]
    run = RuntimeRun(
        id=str(uuid.uuid4()), task_id=task_id, origin_type=origin_type, origin_id=origin_id,
        ticket_id=ticket_id, goal_id=goal_id, agent_slug=agent_slug,
        attempt=(prior.attempt + 1 if prior else 1), requested_profile=requested_profile,
        resolved_profile=resolved_profile, runtime_provider=provider,
        workflow_slug=workflow["slug"] if workflow else None,
        workflow_hash=workflow["sha256"] if workflow else None, correlation_id=str(uuid.uuid4()),
    )
    db.session.add(run)
    publish("run.queued", f"run:{run.id}", run.correlation_id, {"origin_type": origin_type, "origin_id": origin_id, "task_id": task_id, "workflow": run.workflow_slug})
    db.session.commit()
    return run


def request_approval(run: RuntimeRun, action: str) -> RuntimeRunApproval:
    if run.status != "running":
        raise ValueError("Only running runs can await approval")
    approval = RuntimeRunApproval(
        id=str(uuid.uuid4()), run_id=run.id,
        action_hash=hashlib.sha256(action.encode()).hexdigest(),
    )
    transition(run, "awaiting_approval", commit=False)
    db.session.add(approval)
    db.session.commit()
    return approval


def decide_approval(run: RuntimeRun, approval: RuntimeRunApproval, *, approved: bool, actor: str) -> RuntimeRun:
    if approval.status != "pending":
        raise ValueError("Approval is already decided")
    handoff = None
    if not approved and run.task_id is not None:
        from routes.tasks import _spawn_handoff_lock

        handoff = _spawn_handoff_lock(run.task_id)

    cancelled_task_id = None
    with handoff or nullcontext():
        approval.status = "approved" if approved else "rejected"
        approval.decided_by = actor
        approval.decided_at = datetime.now(timezone.utc)
        if run.task_id is not None:
            from models import ScheduledTask

            task_updates = (
                {
                    ScheduledTask.started_at: datetime.now(timezone.utc),
                }
                if approved
                else {
                    ScheduledTask.status: "cancelled",
                    ScheduledTask.completed_at: datetime.now(timezone.utc),
                }
            )
            updated = ScheduledTask.query.filter(
                ScheduledTask.id == run.task_id,
                ScheduledTask.status == "running",
                ScheduledTask.runtime_run_id == run.id,
                ScheduledTask.attempt == run.attempt,
            ).update(task_updates, synchronize_session="fetch")
            still_linked = ScheduledTask.query.filter(
                ScheduledTask.id == run.task_id,
                ScheduledTask.runtime_run_id == run.id,
            ).count()
            if updated != 1 and still_linked:
                db.session.rollback()
                raise ValueError("Linked scheduled task changed before approval decision")
            if not approved and updated == 1:
                cancelled_task_id = run.task_id
        transition(run, "running" if approved else "cancelled", commit=False)
        db.session.commit()
        if cancelled_task_id is not None:
            from routes.tasks import cancel_task_process

            cancel_task_process(cancelled_task_id)
    return run


def add_evidence(run: RuntimeRun, reference: str, mime_type: str | None = None, size_bytes: int | None = None) -> RuntimeRunEvidence:
    if not reference or reference.startswith("/") or ".." in reference:
        raise ValueError("Invalid evidence reference")
    evidence = RuntimeRunEvidence(
        id=str(uuid.uuid4()), run_id=run.id, reference=reference,
        mime_type=mime_type, size_bytes=size_bytes,
        checksum=hashlib.sha256(reference.encode()).hexdigest(),
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def compute_metrics() -> dict:
    from collections import Counter, defaultdict

    runs = RuntimeRun.query.all()
    status_counts = Counter(run.status for run in runs)

    queue_durations = []
    run_durations = []
    per_workflow = defaultdict(lambda: {"succeeded": 0, "failed": 0, "cancelled": 0, "total": 0})
    for run in runs:
        if run.started_at and run.queued_at:
            queue_durations.append((run.started_at - run.queued_at).total_seconds())
        if run.completed_at and run.started_at:
            run_durations.append((run.completed_at - run.started_at).total_seconds())
        if run.workflow_slug:
            bucket = per_workflow[run.workflow_slug]
            bucket["total"] += 1
            if run.status in ("succeeded", "failed", "cancelled"):
                bucket[run.status] += 1

    def _avg(values):
        return round(sum(values) / len(values), 3) if values else None

    workflows = {}
    for slug, bucket in per_workflow.items():
        finished = bucket["succeeded"] + bucket["failed"]
        workflows[slug] = {
            **bucket,
            "success_rate": round(bucket["succeeded"] / finished, 4) if finished else None,
        }

    return {
        "status_counts": dict(status_counts),
        "avg_queue_seconds": _avg(queue_durations),
        "avg_run_seconds": _avg(run_durations),
        "workflows": workflows,
    }


def recover_orphaned_runs(lease_seconds: int = 900) -> int:
    from models import ScheduledTask

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=lease_seconds)
    stale_condition = or_(
        and_(RuntimeRun.status == "queued", RuntimeRun.queued_at < cutoff),
        and_(
            RuntimeRun.status.in_(("running", "cancel_requested")),
            RuntimeRun.started_at < cutoff,
        ),
    )
    attached_to_active_task = ScheduledTask.query.filter(
        ScheduledTask.status == "running",
        ScheduledTask.runtime_run_id == RuntimeRun.id,
        ScheduledTask.attempt == RuntimeRun.attempt,
    ).exists()
    stale = RuntimeRun.query.filter(
        stale_condition,
        ~attached_to_active_task,
    ).order_by(RuntimeRun.id).all()
    recovered = 0
    completed_at = datetime.now(timezone.utc)
    for run in stale:
        ownership = RuntimeRun.query.filter(
            RuntimeRun.id == run.id,
            RuntimeRun.status == run.status,
        )
        if run.status == "queued":
            ownership = ownership.filter(RuntimeRun.queued_at < cutoff)
        else:
            ownership = ownership.filter(RuntimeRun.started_at < cutoff)
        claimed = ownership.update(
            {
                RuntimeRun.status: "failed",
                RuntimeRun.completed_at: completed_at,
                RuntimeRun.error: "Worker lease expired",
                RuntimeRun.exit_code: -1,
            },
            synchronize_session="fetch",
        )
        if claimed:
            publish(
                "run.failed",
                f"run:{run.id}",
                run.correlation_id,
                {"task_id": run.task_id, "attempt": run.attempt},
            )
            recovered += 1
    db.session.commit()
    return recovered


def transition(
    run: RuntimeRun,
    target: str,
    *,
    summary: str | None = None,
    error: str | None = None,
    exit_code: int | None = None,
    commit: bool = True,
) -> RuntimeRun:
    if target == run.status:
        return run
    if target not in TRANSITIONS.get(run.status, set()):
        raise ValueError(f"Invalid runtime run transition: {run.status} -> {target}")
    current_status = run.status
    now = datetime.now(timezone.utc)
    updates: dict = {RuntimeRun.status: target}
    if target == "running":
        updates[RuntimeRun.started_at] = now
    if target in TERMINAL:
        updates[RuntimeRun.completed_at] = now
    if summary is not None:
        updates[RuntimeRun.result_summary] = summary[:5000]
    if error is not None:
        updates[RuntimeRun.error] = error[:2000]
    if exit_code is not None:
        updates[RuntimeRun.exit_code] = exit_code
    updated = RuntimeRun.query.filter(
        RuntimeRun.id == run.id,
        RuntimeRun.status == current_status,
    ).update(updates, synchronize_session="fetch")
    if updated != 1:
        db.session.rollback()
        raise ValueError(
            f"Concurrent runtime run transition: {current_status} -> {target}"
        )
    publish(f"run.{target}", f"run:{run.id}", run.correlation_id, {"task_id": run.task_id, "attempt": run.attempt})
    if commit:
        db.session.commit()
    return run
