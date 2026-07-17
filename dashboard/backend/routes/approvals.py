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

from models import db, has_permission, Ticket, TICKET_PRIORITIES, PRIORITY_RANK, GoalProject, Goal
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
        db.text(
            "SELECT gate_type, ticket_id, goal_id, mission_id, project_id, agent, payload "
            "FROM pending_approvals WHERE id=:id"
        ),
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
    elif row.gate_type == "decomposition":
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

            # pending_approvals.status and goals.decomposition_state are
            # already committed to 'approved' above (CAS point of no return —
            # a 2nd decision on this approval_id 409s, and goal-planner
            # refuses to re-propose a goal whose decomposition_state is
            # non-null). If this loop dies partway, silently leaving zero
            # tickets behind an "approved" state would be a real data-loss
            # bug: the human sees the Telegram approval succeed and never
            # learns nothing was actually created. isinstance guards the
            # common malformed-payload case (goal-planner double-encoding
            # "tickets" as a string, etc.) without losing the rest of the
            # batch; the try/except is defense in depth for anything else,
            # and always alerts + re-raises rather than swallowing.
            created = 0
            try:
                for t in payload.get("tickets") or []:
                    if not isinstance(t, dict):
                        continue
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
                    created += 1
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                from notifications import send_telegram_alert
                send_telegram_alert(
                    f"⚠️ Aprovação de decomposição #{approval_id} (Goal #{row.goal_id}) foi "
                    f"registrada como aprovada, mas a criação dos tickets falhou: {exc}. "
                    f"Nenhum ticket foi criado e a aprovação já foi consumida (não pode ser "
                    f"repetida) — intervenção manual necessária."
                )
                raise

            if created == 0 and (payload.get("tickets") or []):
                from notifications import send_telegram_alert
                send_telegram_alert(
                    f"⚠️ Aprovação de decomposição #{approval_id} (Goal #{row.goal_id}) foi "
                    f"aprovada, mas nenhum ticket válido foi encontrado no payload — confira "
                    f"manualmente."
                )
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

    elif row.gate_type == "project_suggestion":
        # ai-hierarchy-suggestions (quick-spec): Mission -> Project rung.
        # Unlike decomposition, there's no parent-side "state" column to
        # flip (Mission has no decomposition_state) — pending_approvals.status
        # (already CAS'd to approved/rejected above) is the sole durable
        # record of this decision.
        if new_status == "approved":
            try:
                payload = json.loads(row.payload or "{}")
            except (ValueError, TypeError):
                payload = {}

            created_project_ids: list[int] = []
            try:
                for item in payload.get("projects") or []:
                    if not isinstance(item, dict):
                        continue
                    p_title = (item.get("title") or "").strip()
                    p_slug = (item.get("slug") or "").strip()
                    if not p_title or not p_slug:
                        continue
                    if GoalProject.query.filter_by(slug=p_slug).first():
                        continue  # duplicate slug — skip, don't crash the batch
                    project = GoalProject(
                        slug=p_slug, title=p_title, description=item.get("description"),
                        mission_id=row.mission_id, status="active",
                        created_at=now, updated_at=now,
                    )
                    db.session.add(project)
                    db.session.flush()  # need project.id before the cascade dispatch below
                    created_project_ids.append(project.id)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                from notifications import send_telegram_alert
                send_telegram_alert(
                    f"⚠️ Aprovação de sugestão de Projects #{approval_id} (Mission #{row.mission_id}) "
                    f"foi registrada como aprovada, mas a criação falhou: {exc}. Nenhum Project foi "
                    f"criado e a aprovação já foi consumida — intervenção manual necessária."
                )
                raise

            if not created_project_ids and (payload.get("projects") or []):
                from notifications import send_telegram_alert
                send_telegram_alert(
                    f"⚠️ Aprovação de sugestão de Projects #{approval_id} (Mission #{row.mission_id}) "
                    f"foi aprovada, mas nenhum Project válido foi encontrado no payload — confira "
                    f"manualmente."
                )

            # Cascade by design (quick-spec ai-hierarchy-suggestions): each
            # created Project wakes goal-suggester for itself. Still gated by
            # its own human approval before any Goal exists — never runs
            # away unsupervised, same reasoning as create_project's own
            # unconditional project_created dispatch.
            from heartbeat_dispatcher import dispatch
            for pid in created_project_ids:
                try:
                    dispatch("goal-suggester", "project_created", {"project_id": pid})
                except Exception:  # noqa: BLE001 — best-effort, never fail the decision response
                    pass
        # reject: zero projects created — nothing else to do.

    elif row.gate_type == "goal_suggestion":
        # ai-hierarchy-suggestions: Project -> Goal rung. Goals created here
        # have no parent_goal_id (their parent is a Project, not a Goal), so
        # they're indistinguishable from a human-created top-level goal —
        # correctly falling under create_goal's own `parent_goal_id IS NULL`
        # rule if ever created via that route. Here we create them directly
        # (same reasoning as the decomposition/project_suggestion branches)
        # and dispatch goal_created ourselves per created goal.
        if new_status == "approved":
            try:
                payload = json.loads(row.payload or "{}")
            except (ValueError, TypeError):
                payload = {}

            created_goal_ids: list[int] = []
            try:
                for item in payload.get("goals") or []:
                    if not isinstance(item, dict):
                        continue
                    g_title = (item.get("title") or "").strip()
                    g_slug = (item.get("slug") or "").strip()
                    if not g_title or not g_slug:
                        continue
                    if Goal.query.filter_by(slug=g_slug).first():
                        continue  # duplicate slug — skip, don't crash the batch

                    metric_type = item.get("metric_type") or "count"
                    if metric_type not in ("count", "currency", "percentage", "boolean"):
                        metric_type = "count"
                    target_value = item.get("target_value")
                    if metric_type == "boolean":
                        target_value = 1.0 if target_value is None else target_value
                    elif target_value is None:
                        continue  # no target and not boolean — not a measurable goal, skip

                    goal = Goal(
                        slug=g_slug, project_id=row.project_id, title=g_title,
                        description=item.get("description"), target_metric=item.get("target_metric"),
                        metric_type=metric_type, target_value=target_value, current_value=0,
                        due_date=item.get("due_date"), status="active",
                        created_at=now, updated_at=now,
                    )
                    db.session.add(goal)
                    db.session.flush()
                    created_goal_ids.append(goal.id)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                from notifications import send_telegram_alert
                send_telegram_alert(
                    f"⚠️ Aprovação de sugestão de Goals #{approval_id} (Project #{row.project_id}) "
                    f"foi registrada como aprovada, mas a criação falhou: {exc}. Nenhuma Goal foi "
                    f"criada e a aprovação já foi consumida — intervenção manual necessária."
                )
                raise

            if not created_goal_ids and (payload.get("goals") or []):
                from notifications import send_telegram_alert
                send_telegram_alert(
                    f"⚠️ Aprovação de sugestão de Goals #{approval_id} (Project #{row.project_id}) "
                    f"foi aprovada, mas nenhuma Goal válida foi encontrada no payload — confira "
                    f"manualmente."
                )

            # Cascade by design: each created Goal wakes the existing
            # goal-planner heartbeat, same as a human-created top-level goal.
            from heartbeat_dispatcher import dispatch
            for gid in created_goal_ids:
                try:
                    dispatch("goal-planner", "goal_created", {"goal_id": gid})
                except Exception:  # noqa: BLE001
                    pass
        # reject: zero goals created — nothing else to do.

    return jsonify({"status": "ok", "approval_id": approval_id, "decision": new_status}), 200


def _gate_context_line(gate_type: str, mission_id, project_id, goal_id) -> str:
    """Mission/Project context line so an approval doesn't get lost among
    several Sistema Britto missions/projects being decomposed in parallel —
    the Telegram audit's médio-priority gap #3, generalized across gates."""
    try:
        if gate_type == "project_suggestion" and mission_id:
            row = db.session.execute(
                db.text("SELECT title FROM missions WHERE id=:i"), {"i": mission_id}
            ).fetchone()
            if row:
                return f"Missão: {row[0]}"
        elif gate_type == "goal_suggestion" and project_id:
            row = db.session.execute(
                db.text("SELECT title FROM projects WHERE id=:i"), {"i": project_id}
            ).fetchone()
            if row:
                return f"Projeto: {row[0]}"
        elif gate_type == "decomposition" and goal_id:
            row = db.session.execute(
                db.text(
                    "SELECT g.title AS goal_title, p.title AS project_title "
                    "FROM goals g LEFT JOIN projects p ON p.id = g.project_id WHERE g.id=:i"
                ),
                {"i": goal_id},
            ).fetchone()
            if row:
                return f"Projeto: {row[1] or '—'} · Meta: {row[0]}"
    except Exception:
        return ""
    return ""


def _render_structured_items(gate_type: str, payload: dict) -> str:
    """Render the actual proposed items (titles) from payload, independent of
    whatever free text the agent wrote in payload["body"]. Closes the gap
    where the structured list only reliably lives in the DB, not in what the
    human actually sees on Telegram (Telegram audit finding #2)."""
    key = {"decomposition": "tickets", "project_suggestion": "projects", "goal_suggestion": "goals"}.get(gate_type)
    if not key:
        return ""
    items = payload.get(key)
    if not isinstance(items, list) or not items:
        return ""
    titled = [item["title"] for item in items if isinstance(item, dict) and item.get("title")]
    if not titled:
        return ""
    lines = [f"\nItens propostos ({len(titled)}):"]
    lines.extend(f"• {t}" for t in titled[:10])
    if len(titled) > 10:
        lines.append(f"… e mais {len(titled) - 10}")
    return "\n".join(lines)


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
    valid_gate_types = ("publish", "decomposition", "project_suggestion", "goal_suggestion")
    if gate_type not in valid_gate_types:
        return jsonify({"error": f"gate_type must be one of {valid_gate_types}"}), 400

    goal_id = data.get("goal_id")
    ticket_id = data.get("ticket_id")
    mission_id = data.get("mission_id")
    project_id = data.get("project_id")
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
    elif gate_type == "publish":
        if not ticket_id:
            return jsonify({"error": "ticket_id is required for gate_type=publish"}), 400
        attempt = db.session.execute(
            db.text("SELECT COUNT(*) FROM pending_approvals WHERE ticket_id=:t AND gate_type='publish'"),
            {"t": ticket_id},
        ).scalar() or 0
        idempotency_key = f"publish:{ticket_id}:{attempt}"
    elif gate_type == "project_suggestion":
        if not mission_id:
            return jsonify({"error": "mission_id is required for gate_type=project_suggestion"}), 400
        attempt = db.session.execute(
            db.text(
                "SELECT COUNT(*) FROM pending_approvals WHERE mission_id=:m AND gate_type='project_suggestion'"
            ),
            {"m": mission_id},
        ).scalar() or 0
        idempotency_key = f"projsug:{mission_id}:{attempt}"
    else:  # goal_suggestion
        if not project_id:
            return jsonify({"error": "project_id is required for gate_type=goal_suggestion"}), 400
        attempt = db.session.execute(
            db.text(
                "SELECT COUNT(*) FROM pending_approvals WHERE project_id=:p AND gate_type='goal_suggestion'"
            ),
            {"p": project_id},
        ).scalar() or 0
        idempotency_key = f"goalsug:{project_id}:{attempt}"

    now = _now()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    db.session.execute(
        db.text(
            "INSERT OR IGNORE INTO pending_approvals "
            "(gate_type, ticket_id, goal_id, mission_id, project_id, agent, attempt, idempotency_key, "
            "status, payload, created_at, expires_at) "
            "VALUES (:gt, :tid, :gid, :mid, :pid, :a, :att, :k, 'pending', :p, :c, :e)"
        ),
        {
            "gt": gate_type, "tid": ticket_id, "gid": goal_id, "mid": mission_id, "pid": project_id,
            "a": agent, "att": attempt, "k": idempotency_key,
            "p": json.dumps(payload, ensure_ascii=False), "c": now, "e": expires_at,
        },
    )
    db.session.commit()

    row = db.session.execute(
        db.text("SELECT id FROM pending_approvals WHERE idempotency_key=:k"), {"k": idempotency_key}
    ).fetchone()

    body_parts = [
        _gate_context_line(gate_type, mission_id, project_id, goal_id),
        payload.get("body") or "",
        _render_structured_items(gate_type, payload),
    ]
    telegram_body = "\n".join(p for p in body_parts if p).strip()[:1500]

    from notifications import send_approval_request
    message_id = send_approval_request(row.id, payload.get("title") or "Aprovação pendente", telegram_body)
    if message_id is not None:
        db.session.execute(
            db.text("UPDATE pending_approvals SET telegram_message_id=:m WHERE id=:id"),
            {"m": message_id, "id": row.id},
        )
        db.session.commit()

    return jsonify({"id": row.id, "idempotency_key": idempotency_key, "status": "pending"}), 201
