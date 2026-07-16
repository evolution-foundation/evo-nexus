"""Approvals API — shared Telegram approve/reject gate (goal-ticket-unification).

One `pending_approvals` table serves both gates (publish + decomposition, ADR
SPEC 3). This module owns only the decision endpoint's infrastructure: auth,
idempotent state transition, and correlation back to the ticket/goal. The
business effect of an approval (actually publishing, actually creating
sub-goal tickets) is wired in Step 7 — see the TODO markers below.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from flask_login import current_user

from models import db, has_permission, Ticket, TICKET_PRIORITIES, PRIORITY_RANK
from routes._helpers import valid_approval_bridge_token

bp = Blueprint("approvals", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _db_path() -> str:
    return str(WORKSPACE / "dashboard" / "data" / "evonexus.db")


def _require(action: str, resource: str = "goals"):
    if not has_permission(current_user.role, resource, action):
        return jsonify({"error": "Forbidden"}), 403
    return None


def _approver_allowlist() -> set[str]:
    """Individuals allowed to decide an approval (Vault V3 — not a chat/group).

    Sourced from the same access.json the Telegram bot reads (TELEGRAM_STATE/
    channels/telegram/access.json isn't reachable from this container — the
    dashboard doesn't mount it — so this allowlist comes from the
    APPROVAL_APPROVER_IDS env var instead, semicolon/comma-separated Telegram
    user ids). The bot performs its own allowlist check before ever calling
    this endpoint (§3d); this is defense in depth — decided_by is derived
    from from_id, so this is the last check before that value is trusted.
    """
    raw = os.environ.get("APPROVAL_APPROVER_IDS", "")
    ids = {i.strip() for i in re.split(r"[,;]", raw) if i.strip()}
    return ids


@bp.route("/api/approvals/<int:approval_id>/decision", methods=["POST"])
def decide_approval(approval_id: int):
    # V1: dedicated bridge token only — the general admin DASHBOARD_API_TOKEN
    # is explicitly rejected here even if it got the request past
    # before_request via the normal login flow.
    if not valid_approval_bridge_token(request.headers.get("Authorization")):
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    decision = data.get("decision")
    if decision not in ("approve", "reject"):
        return jsonify({"error": "decision must be 'approve' or 'reject'"}), 400

    from_id = str(data.get("from_id") or "")
    # V4: decided_by is DERIVED server-side from from_id after revalidating
    # against the allowlist — never trust a decided_by in the body, it's
    # forgeable by anyone who has the bridge token (the bot process).
    if not from_id or from_id not in _approver_allowlist():
        return jsonify({"error": "not an approver"}), 403
    decided_by = f"telegram:{from_id}"

    reason = str(data.get("reason") or "")[:500]
    new_status = "approved" if decision == "approve" else "rejected"
    now = _now()

    # Atomic checkout: WHERE status='pending' + rowcount is the idempotency
    # mechanism (Vault V6) — a second press on the same approval is a no-op
    # 409, never a double-effect.
    cur = db.session.execute(
        db.text(
            "UPDATE pending_approvals SET status=:s, decided_at=:t, decided_by=:b, "
            "reject_reason=:r, approver_from_id=:f WHERE id=:id AND status='pending'"
        ),
        {"s": new_status, "t": now, "b": decided_by, "r": reason, "f": from_id, "id": approval_id},
    )
    if cur.rowcount == 0:
        db.session.rollback()
        return jsonify({"error": "already decided or not found"}), 409
    db.session.commit()

    row = db.session.execute(
        db.text("SELECT gate_type, ticket_id, goal_id, agent, payload FROM pending_approvals WHERE id=:id"),
        {"id": approval_id},
    ).fetchone()

    if row.gate_type == "publish":
        from heartbeat_outcome import _move_ticket, _run_publish_action

        conn = sqlite3.connect(_db_path())
        try:
            if decision == "approve":
                result = _run_publish_action(row, conn)
                if result["published"]:
                    conn.execute(
                        "UPDATE pending_approvals SET status='published' WHERE id=?", (approval_id,)
                    )
                    _move_ticket(row.ticket_id, "resolved", row.agent or "system",
                                 f"Publicado: {result['detail']}", conn)
                else:
                    # Approved but nothing was actually published (no automated
                    # integration for the target) — back to in_progress, not
                    # resolved, so the ticket doesn't silently claim completion.
                    _move_ticket(row.ticket_id, "in_progress", row.agent or "system",
                                 f"Aprovado mas publicação falhou/pendente: {result['detail']}", conn)
                    conn.execute(
                        "UPDATE tickets SET blocked_reason=NULL, requires_human_approval=0 WHERE id=?",
                        (row.ticket_id,),
                    )
            else:
                _move_ticket(row.ticket_id, "in_progress", row.agent or "system",
                             f"Publicação rejeitada: {reason or 'sem motivo informado'}", conn)
                conn.execute(
                    "UPDATE tickets SET blocked_reason=NULL, requires_human_approval=0 WHERE id=?",
                    (row.ticket_id,),
                )
            conn.commit()
        finally:
            conn.close()
    else:  # decomposition
        db.session.execute(
            db.text("UPDATE goals SET decomposition_state=:s, updated_at=:t WHERE id=:id"),
            {"s": new_status, "t": now, "id": row.goal_id},
        )
        db.session.commit()

        if new_status == "approved":
            try:
                payload = json.loads(row.payload or "{}")
            except (ValueError, TypeError):
                payload = {}
            from routes.tickets import _get_agent_slugs

            for t in payload.get("tickets") or []:
                t_title = (t.get("title") or "").strip()
                if not t_title:
                    continue
                t_priority = t.get("priority") if t.get("priority") in TICKET_PRIORITIES else "medium"
                t_assignee = t.get("assignee_agent")
                if t_assignee and t_assignee not in _get_agent_slugs():
                    t_assignee = "clawdia-assistant"
                db.session.add(Ticket(
                    id=str(uuid.uuid4()), title=t_title, description=t.get("description"),
                    status="open", priority=t_priority, priority_rank=PRIORITY_RANK[t_priority],
                    assignee_agent=t_assignee, goal_id=row.goal_id,
                    created_by="system:approval", source_agent="goal-planner",
                    created_at=now, updated_at=now,
                ))
            db.session.commit()
        # reject: decomposition_state='rejected' already set above, zero
        # tickets created — nothing else to do.
        #
        # R1 (ADR Sign-off — Raven's reservation, closed here): deliberately
        # NEVER call dispatch("goal-planner", "goal_created", ...) from this
        # branch. A re-dispatch would bypass create_goal's `parent_goal_id IS
        # NULL` guard (that guard only covers goal CREATION, not this
        # approval-triggered re-wake) and let sub-goals nest to arbitrary
        # depth. The tickets from an approved decomposition are created
        # directly above, straight from the payload the human already saw
        # and approved — no heartbeat is woken to re-decompose anything.

    return jsonify({"status": "ok", "approval_id": approval_id, "decision": new_status}), 200


@bp.route("/api/approvals", methods=["POST"])
def create_approval():
    """Propose a pending_approvals row (goal-planner's decomposition-gate write
    path, ADR SPEC 3h/Step 7). goal-planner has no chat surface — it calls this
    via EvoClient with DASHBOARD_API_TOKEN — so `attempt` is computed
    server-side (Vault V7) rather than trusted from the caller, keeping the
    idempotency-key scoping correct even if goal-planner retries a call.
    """
    denied = _require("execute")
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    gate_type = data.get("gate_type")
    if gate_type not in ("publish", "decomposition"):
        return jsonify({"error": "gate_type must be 'publish' or 'decomposition'"}), 400

    goal_id = data.get("goal_id")
    ticket_id = data.get("ticket_id")
    agent = data.get("agent")
    payload = data.get("payload") or {}

    if gate_type == "decomposition":
        if not goal_id:
            return jsonify({"error": "goal_id is required for gate_type=decomposition"}), 400
        attempt = db.session.execute(
            db.text("SELECT COUNT(*) FROM pending_approvals WHERE goal_id=:g AND gate_type='decomposition'"),
            {"g": goal_id},
        ).scalar() or 0
        idempotency_key = f"decomp:{goal_id}:{attempt}"
    else:
        if not ticket_id:
            return jsonify({"error": "ticket_id is required for gate_type=publish"}), 400
        attempt = db.session.execute(
            db.text("SELECT COUNT(*) FROM pending_approvals WHERE ticket_id=:t AND gate_type='publish'"),
            {"t": ticket_id},
        ).scalar() or 0
        idempotency_key = f"publish:{ticket_id}:{attempt}"

    now = _now()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    db.session.execute(
        db.text(
            "INSERT OR IGNORE INTO pending_approvals "
            "(gate_type, ticket_id, goal_id, agent, attempt, idempotency_key, status, payload, created_at, expires_at) "
            "VALUES (:gt, :tid, :gid, :a, :att, :k, 'pending', :p, :c, :e)"
        ),
        {
            "gt": gate_type, "tid": ticket_id, "gid": goal_id, "a": agent, "att": attempt,
            "k": idempotency_key, "p": json.dumps(payload, ensure_ascii=False), "c": now, "e": expires_at,
        },
    )
    db.session.commit()

    row = db.session.execute(
        db.text("SELECT id FROM pending_approvals WHERE idempotency_key=:k"), {"k": idempotency_key}
    ).fetchone()

    from notifications import send_approval_request
    message_id = send_approval_request(row.id, payload.get("title") or "Aprovação pendente", payload.get("body") or "")
    if message_id is not None:
        db.session.execute(
            db.text("UPDATE pending_approvals SET telegram_message_id=:m WHERE id=:id"),
            {"m": message_id, "id": row.id},
        )
        db.session.commit()

    return jsonify({"id": row.id, "idempotency_key": idempotency_key, "status": "pending"}), 201
