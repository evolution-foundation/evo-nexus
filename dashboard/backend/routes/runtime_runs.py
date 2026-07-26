"""Runtime run API."""

from flask import Blueprint, jsonify, request
from flask_login import current_user

from models import EventOutbox, RuntimeRun, RuntimeRunApproval, RuntimeRunEvidence, ScheduledTask, db, has_permission
from runtime_runs import add_evidence, compute_metrics, create_run, decide_approval, request_approval, transition

bp = Blueprint("runtime_runs", __name__)


def _require(action: str):
    if not has_permission(current_user.role, "tasks", action):
        return jsonify({"error": "Forbidden"}), 403
    return None


@bp.route("/api/runtime-runs")
def list_runs():
    denied = _require("view")
    if denied:
        return denied
    query = RuntimeRun.query
    filters = {
        "origin_type": RuntimeRun.origin_type,
        "provider": RuntimeRun.runtime_provider,
        "profile": RuntimeRun.resolved_profile,
        "agent": RuntimeRun.agent_slug,
        "status": RuntimeRun.status,
        "correlation_id": RuntimeRun.correlation_id,
    }
    for name, column in filters.items():
        value = request.args.get(name)
        if value:
            query = query.filter(column == value)
    return jsonify({"runs": [run.to_dict() for run in query.order_by(RuntimeRun.queued_at.desc()).limit(100)]})


@bp.route("/api/runtime-runs/<string:run_id>")
def get_run(run_id: str):
    denied = _require("view")
    if denied:
        return denied
    run = RuntimeRun.query.get_or_404(run_id)
    return jsonify(run.to_dict())


@bp.route("/api/runtime-runs/<string:run_id>/cancel", methods=["POST"])
def cancel_run(run_id: str):
    denied = _require("execute")
    if denied:
        return denied
    run = RuntimeRun.query.get_or_404(run_id)
    if run.status not in {"queued", "awaiting_approval", "running"}:
        return jsonify({"error": f"Cannot cancel run with status '{run.status}'"}), 400

    linked_task = None
    if run.task_id is not None:
        linked_task = ScheduledTask.query.filter(
            ScheduledTask.id == run.task_id,
            ScheduledTask.runtime_run_id == run.id,
            ScheduledTask.attempt == run.attempt,
        ).one_or_none()
    if linked_task is not None:
        from routes.tasks import _cancel_task_atomically, cancel_task_process

        if not _cancel_task_atomically(linked_task):
            return jsonify({"error": "Task state changed before cancellation"}), 409
        cancel_task_process(linked_task.id)
        db.session.refresh(run)
    elif run.status in {"queued", "awaiting_approval"}:
        transition(run, "cancelled")
    else:
        transition(run, "cancel_requested")
    return jsonify(run.to_dict())


@bp.route("/api/runtime-runs/<string:run_id>/retry", methods=["POST"])
def retry_run(run_id: str):
    denied = _require("execute")
    if denied:
        return denied
    run = RuntimeRun.query.get_or_404(run_id)
    if run.status != "failed":
        return jsonify({"error": "Only failed runs can be retried"}), 400
    task = ScheduledTask.query.get_or_404(run.task_id)
    if task.status != "failed":
        return jsonify({"error": f"Cannot retry task with status '{task.status}'"}), 400
    from routes.tasks import _start_task
    if not _start_task(
        task.id,
        expected_status="failed",
        expected_attempt=run.attempt,
        expected_runtime_run_id=run.id,
        clear_error=True,
    ):
        return jsonify({"error": "Task could not be claimed for retry"}), 409
    return jsonify({"task": task.to_dict(), "previous_run": run.to_dict()}), 202


@bp.route("/api/runtime-runs/<string:run_id>/approval", methods=["POST"])
def create_approval(run_id: str):
    denied = _require("execute")
    if denied:
        return denied
    run = RuntimeRun.query.get_or_404(run_id)
    action = (request.get_json(silent=True) or {}).get("action", "")
    if not isinstance(action, str) or not action.strip() or len(action) > 20_000:
        return jsonify({"error": "Valid action is required"}), 400
    try:
        approval = request_approval(run, action)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": approval.id, "run_id": run.id, "status": approval.status}), 201


@bp.route("/api/runtime-runs/<string:run_id>/approval/<string:approval_id>", methods=["POST"])
def decide_run_approval(run_id: str, approval_id: str):
    denied = _require("execute")
    if denied:
        return denied
    run = RuntimeRun.query.get_or_404(run_id)
    approval = RuntimeRunApproval.query.filter_by(id=approval_id, run_id=run.id).first_or_404()
    approved = (request.get_json(silent=True) or {}).get("approved")
    if not isinstance(approved, bool):
        return jsonify({"error": "approved must be boolean"}), 400
    try:
        decide_approval(run, approval, approved=approved, actor=current_user.username)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(run.to_dict())


@bp.route("/api/runtime-runs/<string:run_id>/evidence", methods=["POST"])
def create_run_evidence(run_id: str):
    denied = _require("execute")
    if denied:
        return denied
    run = RuntimeRun.query.get_or_404(run_id)
    data = request.get_json(silent=True) or {}
    try:
        evidence = add_evidence(run, data.get("reference", ""), data.get("mime_type"), data.get("size_bytes"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": evidence.id, "run_id": run.id, "reference": evidence.reference, "checksum": evidence.checksum}), 201


@bp.route("/api/runtime-runs/<string:run_id>/timeline")
def run_timeline(run_id: str):
    denied = _require("view")
    if denied:
        return denied
    run = RuntimeRun.query.get_or_404(run_id)
    approvals = RuntimeRunApproval.query.filter_by(run_id=run.id).all()
    evidence = RuntimeRunEvidence.query.filter_by(run_id=run.id).all()
    return jsonify({"run": run.to_dict(), "approvals": [{"id": item.id, "status": item.status, "decided_by": item.decided_by} for item in approvals], "evidence": [{"id": item.id, "reference": item.reference, "checksum": item.checksum} for item in evidence]})


@bp.route("/api/runtime-runs/metrics")
def run_metrics():
    denied = _require("view")
    if denied:
        return denied
    metrics = compute_metrics()
    metrics["event_dead_letter_count"] = EventOutbox.query.filter_by(status="dead_letter").count()
    return jsonify(metrics)


@bp.route("/api/events/outbox")
def outbox_status():
    denied = _require("view")
    if denied:
        return denied
    events = EventOutbox.query.order_by(EventOutbox.created_at.desc()).limit(200).all()
    return jsonify({
        "events": [
            {"id": e.id, "event_type": e.event_type, "subject": e.subject, "status": e.status, "attempts": e.attempts}
            for e in events
        ],
        "dead_letter_count": EventOutbox.query.filter_by(status="dead_letter").count(),
    })
