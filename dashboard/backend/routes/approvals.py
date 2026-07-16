"""Approvals API — shared Telegram approve/reject gate (goal-ticket-unification).

One `pending_approvals` table serves both gates (publish + decomposition, ADR
SPEC 3). This module owns only the decision endpoint's infrastructure: auth,
idempotent state transition, and correlation back to the ticket/goal. The
business effect of an approval (actually publishing, actually creating
sub-goal tickets) is wired in Step 7 — see the TODO markers below.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from models import db
from routes._helpers import valid_approval_bridge_token

bp = Blueprint("approvals", __name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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
        db.text("SELECT gate_type, ticket_id, goal_id FROM pending_approvals WHERE id=:id"),
        {"id": approval_id},
    ).fetchone()

    if row.gate_type == "publish":
        # TODO(Step7): invocar _run_publish_action + _move_ticket aqui —
        # infra do endpoint (auth/idempotência/correlação) só, sem efeito de
        # negócio (ver ADR SPEC 3e, condição do escopo deste Step).
        pass
    else:  # decomposition
        db.session.execute(
            db.text("UPDATE goals SET decomposition_state=:s, updated_at=:t WHERE id=:id"),
            {"s": new_status, "t": now, "id": row.goal_id},
        )
        db.session.commit()
        # TODO(Step7): consumir aprovação sem re-disparar goal_created — ver
        # ADR reserva R1 — é aqui que a criação de tickets a partir do
        # payload aprovado aconteceria.

    return jsonify({"status": "ok", "approval_id": approval_id, "decision": new_status}), 200
