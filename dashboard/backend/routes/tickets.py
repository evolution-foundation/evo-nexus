"""Tickets API — persistent conversation topics with atomic checkout (Feature 1.3)."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user

# Trigger a summary job after this many turns (fixed in v1, Q9)
SUMMARY_EVERY_N = 20

# Max memory.md size before warning (bytes)
MEMORY_WARN_BYTES = 64 * 1024   # 64 KB
MEMORY_TRUNCATE_BYTES = 32 * 1024  # 32 KB per section
from models import (
    Heartbeat, Ticket, TicketActivity, TicketComment,
    PRIORITY_RANK, TICKET_PRIORITIES, TICKET_STATUSES,
    db, has_permission, audit,
)

bp = Blueprint("tickets", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent

# Max mentions per comment to prevent heartbeat storms
MAX_MENTIONS_PER_COMMENT = 3


def _require(action: str) -> Optional[tuple]:
    if not has_permission(current_user.role, "tickets", action):
        return jsonify({"error": "Forbidden"}), 403
    return None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _log_activity(ticket_id: str, actor: str, action: str, payload: dict | None = None):
    act = TicketActivity(
        id=str(uuid.uuid4()),
        ticket_id=ticket_id,
        actor=actor,
        action=action,
        payload=json.dumps(payload or {}),
        created_at=_now(),
    )
    db.session.add(act)


def _parse_mentions(body: str) -> list[str]:
    """Extract @agent-slug mentions from comment body. Max MAX_MENTIONS_PER_COMMENT."""
    raw = re.findall(r"@([a-z0-9][a-z0-9-]{1,50})", body)
    seen: list[str] = []
    for slug in raw:
        if slug not in seen:
            seen.append(slug)
        if len(seen) >= MAX_MENTIONS_PER_COMMENT:
            break
    return seen


def _get_agent_slugs() -> set[str]:
    """Return set of known agent slugs from .claude/agents/*.md files."""
    agents_dir = WORKSPACE / ".claude" / "agents"
    slugs: set[str] = set()
    if agents_dir.is_dir():
        for f in agents_dir.glob("*.md"):
            slugs.add(f.stem)
    return slugs


def _fire_mention_triggers(ticket_id: str, comment_id: str, mentions: list[str], author: str):
    """Insert heartbeat_triggers rows for each mentioned agent (F1.1 integration stub).

    Requires F1.1 (heartbeat_triggers table) to be merged.
    If the table doesn't exist yet, logs and skips gracefully.
    """
    known_agents = _get_agent_slugs()
    for slug in mentions:
        if slug not in known_agents:
            print(f"[tickets] mention @{slug} skipped — not a known agent slug", flush=True)
            continue
        # Find enabled heartbeat for this agent
        hb = Heartbeat.query.filter_by(agent=slug, enabled=True).first()
        if not hb:
            print(f"[tickets] mention @{slug} skipped — no active heartbeat", flush=True)
            continue
        try:
            trigger_id = str(uuid.uuid4())
            payload = json.dumps({
                "ticket_id": ticket_id,
                "comment_id": comment_id,
                "mentioner": author,
            })
            db.session.execute(
                db.text(
                    "INSERT INTO heartbeat_triggers (id, heartbeat_id, trigger_type, payload, created_at) "
                    "VALUES (:id, :hb_id, 'mention', :payload, :now)"
                ),
                {"id": trigger_id, "hb_id": hb.id, "payload": payload, "now": _now()},
            )
        except Exception as exc:
            # heartbeat_triggers table may not exist (F1.1 not merged yet)
            print(f"[tickets] WARNING: could not insert mention trigger for @{slug}: {exc}", flush=True)


# --------------- List ---------------

@bp.route("/api/tickets")
def list_tickets():
    denied = _require("view")
    if denied:
        return denied

    # Filters
    statuses = request.args.getlist("status")
    assignees = request.args.getlist("assignee_agent")
    priorities = request.args.getlist("priority")
    project_id = request.args.get("project_id", type=int)
    goal_id = request.args.get("goal_id", type=int)
    q = request.args.get("q", "").strip()
    display_mode = request.args.get("display_mode", "all")  # threads | issues | all
    limit = min(int(request.args.get("limit", 50)), 500)
    offset = int(request.args.get("offset", 0))

    query = Ticket.query

    if statuses:
        query = query.filter(Ticket.status.in_(statuses))
    if assignees:
        query = query.filter(Ticket.assignee_agent.in_(assignees))
    if priorities:
        query = query.filter(Ticket.priority.in_(priorities))
    if project_id is not None:
        query = query.filter(Ticket.project_id == project_id)
    if goal_id is not None:
        query = query.filter(Ticket.goal_id == goal_id)
    if display_mode == "threads":
        query = query.filter(Ticket.memory_md_path.isnot(None))
    elif display_mode == "issues":
        query = query.filter(Ticket.memory_md_path.is_(None))
    if q:
        like = f"%{q}%"
        from models import TicketComment as TC
        matching_ids = (
            db.session.query(TC.ticket_id)
            .filter(TC.body.ilike(like))
            .subquery()
        )
        query = query.filter(
            db.or_(
                Ticket.title.ilike(like),
                Ticket.description.ilike(like),
                Ticket.id.in_(matching_ids),
            )
        )

    total = query.count()
    tickets = (
        query
        .order_by(Ticket.priority_rank.desc(), Ticket.created_at.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return jsonify({
        "tickets": [t.to_dict() for t in tickets],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


# --------------- Counts ---------------

@bp.route("/api/tickets/counts")
def ticket_counts():
    """Return thread/issue split for the Issues page section headers."""
    denied = _require("view")
    if denied:
        return denied

    threads = Ticket.query.filter(Ticket.memory_md_path.isnot(None)).count()
    issues = Ticket.query.filter(Ticket.memory_md_path.is_(None)).count()
    return jsonify({"threads": threads, "issues": issues, "total": threads + issues})


# --------------- Single ---------------

@bp.route("/api/tickets/<string:ticket_id>")
def get_ticket(ticket_id: str):
    denied = _require("view")
    if denied:
        return denied

    ticket = Ticket.query.get_or_404(ticket_id)
    d = ticket.to_dict()
    d["comments"] = [c.to_dict() for c in ticket.comments.order_by(TicketComment.created_at.asc()).all()]
    d["activity"] = [a.to_dict() for a in ticket.activity.order_by(TicketActivity.created_at.asc()).all()]
    return jsonify(d)


# --------------- Timeline ---------------

@bp.route("/api/tickets/<string:ticket_id>/timeline")
def ticket_timeline(ticket_id: str):
    denied = _require("view")
    if denied:
        return denied

    _ = Ticket.query.get_or_404(ticket_id)

    comments = [
        {**c.to_dict(), "_type": "comment"}
        for c in TicketComment.query.filter_by(ticket_id=ticket_id).order_by(TicketComment.created_at.asc()).all()
    ]
    activities = [
        {**a.to_dict(), "_type": "activity"}
        for a in TicketActivity.query.filter_by(ticket_id=ticket_id).order_by(TicketActivity.created_at.asc()).all()
    ]

    timeline = sorted(comments + activities, key=lambda x: x["created_at"])
    return jsonify({"timeline": timeline, "total": len(timeline)})


# --------------- Create ---------------

@bp.route("/api/tickets", methods=["POST"])
def create_ticket():
    denied = _require("execute")
    if denied:
        return denied

    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    priority = data.get("priority", "medium")
    if priority not in TICKET_PRIORITIES:
        return jsonify({"error": f"priority must be one of {TICKET_PRIORITIES}"}), 400

    status = data.get("status", "open")
    if status not in TICKET_STATUSES:
        return jsonify({"error": f"status must be one of {TICKET_STATUSES}"}), 400

    source_agent = (data.get("source_agent") or "").strip() or None
    source_session_id = (data.get("source_session_id") or "").strip() or None

    now = _now()
    ticket = Ticket(
        id=str(uuid.uuid4()),
        title=title,
        description=data.get("description"),
        status=status,
        priority=priority,
        priority_rank=PRIORITY_RANK[priority],
        assignee_agent=data.get("assignee_agent"),
        project_id=data.get("project_id"),
        goal_id=data.get("goal_id"),
        created_by=current_user.username,
        source_agent=source_agent,
        source_session_id=source_session_id,
        created_at=now,
        updated_at=now,
    )
    db.session.add(ticket)
    activity_payload: dict = {"title": title}
    if source_agent:
        activity_payload["source_agent"] = source_agent
    if source_session_id:
        activity_payload["source_session_id"] = source_session_id
    _log_activity(ticket.id, current_user.username, "created", activity_payload)
    db.session.commit()

    audit(current_user, "create", "tickets", f"created ticket {ticket.id}: {title}")
    return jsonify(ticket.to_dict()), 201


# --------------- Update ---------------

@bp.route("/api/tickets/<string:ticket_id>", methods=["PATCH"])
def update_ticket(ticket_id: str):
    denied = _require("execute")
    if denied:
        return denied

    ticket = Ticket.query.get_or_404(ticket_id)
    data = request.get_json() or {}
    changes: dict = {}

    if "status" in data:
        new_status = data["status"]
        if new_status not in TICKET_STATUSES:
            return jsonify({"error": f"invalid status: {new_status}"}), 400
        old_status = ticket.status
        ticket.status = new_status
        changes["status"] = {"from": old_status, "to": new_status}
        if new_status in ("resolved", "closed") and not ticket.resolved_at:
            ticket.resolved_at = _now()

    if "priority" in data:
        new_priority = data["priority"]
        if new_priority not in TICKET_PRIORITIES:
            return jsonify({"error": f"invalid priority: {new_priority}"}), 400
        ticket.priority = new_priority
        ticket.priority_rank = PRIORITY_RANK[new_priority]
        changes["priority"] = new_priority

    if "assignee_agent" in data:
        # Thread-mode invariant: agent is immutable once thread is activated (RF1, Q1)
        if ticket.memory_md_path is not None and data["assignee_agent"] != ticket.assignee_agent:
            return jsonify({"error": "agent_immutable_in_thread_mode"}), 409
        ticket.assignee_agent = data["assignee_agent"] or None
        changes["assignee_agent"] = ticket.assignee_agent

    if "title" in data:
        ticket.title = data["title"].strip() or ticket.title

    if "description" in data:
        ticket.description = data["description"]

    if "project_id" in data:
        ticket.project_id = data["project_id"]

    if "goal_id" in data:
        ticket.goal_id = data["goal_id"]

    # Thread-areas: allow persisting the SDK session ID back from the frontend (R1 mitigation)
    if "thread_session_id" in data:
        ticket.thread_session_id = data["thread_session_id"] or None
        changes["thread_session_id"] = ticket.thread_session_id

    ticket.updated_at = _now()
    _log_activity(ticket_id, current_user.username, "status_changed" if "status" in changes else "updated", changes)
    db.session.commit()

    audit(current_user, "update", "tickets", f"updated ticket {ticket_id}: {list(data.keys())}")
    return jsonify(ticket.to_dict())


# --------------- Delete (soft close or admin hard delete) ---------------

@bp.route("/api/tickets/<string:ticket_id>", methods=["DELETE"])
def delete_ticket(ticket_id: str):
    denied = _require("manage")
    if denied:
        return denied

    ticket = Ticket.query.get_or_404(ticket_id)
    hard = request.args.get("hard", "false").lower() == "true"

    if hard:
        db.session.delete(ticket)
        db.session.commit()
        audit(current_user, "delete", "tickets", f"hard deleted ticket {ticket_id}")
        return jsonify({"deleted": ticket_id, "hard": True})
    else:
        now = _now()
        old_status = ticket.status
        ticket.status = "closed"
        ticket.resolved_at = ticket.resolved_at or now
        ticket.updated_at = now
        _log_activity(ticket_id, current_user.username, "status_changed", {"from": old_status, "to": "closed"})
        db.session.commit()
        audit(current_user, "update", "tickets", f"closed ticket {ticket_id}")
        return jsonify({"deleted": ticket_id, "hard": False, "ticket": ticket.to_dict()})


# --------------- Atomic checkout ---------------

@bp.route("/api/tickets/<string:ticket_id>/checkout", methods=["POST"])
def checkout_ticket(ticket_id: str):
    denied = _require("execute")
    if denied:
        return denied

    data = request.get_json() or {}
    agent = data.get("agent") or current_user.username
    lock_timeout = int(data.get("lock_timeout_seconds", 1800))
    now = _now()

    result = db.session.execute(
        db.text(
            "UPDATE tickets SET locked_at = :now, locked_by = :agent, "
            "lock_timeout_seconds = :timeout, updated_at = :now "
            "WHERE id = :id AND locked_at IS NULL"
        ),
        {"id": ticket_id, "agent": agent, "timeout": lock_timeout, "now": now},
    )
    db.session.commit()

    if result.rowcount == 0:
        ticket = Ticket.query.get(ticket_id)
        if ticket is None:
            return jsonify({"error": "not_found"}), 404
        return jsonify({
            "error": "already_locked",
            "locked_by": ticket.locked_by,
            "locked_at": ticket.locked_at,
        }), 409

    _log_activity(ticket_id, agent, "checkout", {"lock_timeout_seconds": lock_timeout})
    db.session.commit()

    audit(current_user, "execute", "tickets", f"checkout ticket {ticket_id} by {agent}")
    return jsonify({"success": True, "ticket_id": ticket_id, "locked_by": agent, "locked_at": now})


# --------------- Release ---------------

@bp.route("/api/tickets/<string:ticket_id>/release", methods=["POST"])
def release_ticket(ticket_id: str):
    denied = _require("execute")
    if denied:
        return denied

    data = request.get_json() or {}
    agent = data.get("agent") or current_user.username

    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.locked_by != agent:
        return jsonify({
            "error": "not_locked_by_you",
            "locked_by": ticket.locked_by,
        }), 403

    now = _now()
    ticket.locked_at = None
    ticket.locked_by = None
    ticket.updated_at = now
    _log_activity(ticket_id, agent, "release")
    db.session.commit()

    audit(current_user, "execute", "tickets", f"release ticket {ticket_id} by {agent}")
    return jsonify({"success": True, "ticket_id": ticket_id})


# --------------- Comments ---------------

@bp.route("/api/tickets/<string:ticket_id>/comments", methods=["POST"])
def add_comment(ticket_id: str):
    denied = _require("execute")
    if denied:
        return denied

    _ = Ticket.query.get_or_404(ticket_id)
    data = request.get_json() or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body is required"}), 400

    author = data.get("author") or current_user.username
    mentions = _parse_mentions(body)
    now = _now()

    comment = TicketComment(
        id=str(uuid.uuid4()),
        ticket_id=ticket_id,
        author=author,
        body=body,
        created_at=now,
    )
    comment.mentions_list = mentions
    db.session.add(comment)
    _log_activity(ticket_id, author, "commented", {"comment_id": comment.id, "mentions": mentions})
    db.session.commit()

    # Fire mention triggers (F1.1 integration — gracefully skipped if not merged)
    if mentions:
        _fire_mention_triggers(ticket_id, comment.id, mentions, author)
        db.session.commit()

    return jsonify(comment.to_dict()), 201


# --------------- Bulk actions ---------------

@bp.route("/api/tickets/bulk", methods=["POST"])
def bulk_action():
    denied = _require("manage")
    if denied:
        return denied

    data = request.get_json() or {}
    ids = data.get("ids", [])
    action = data.get("action")
    payload = data.get("payload", {})

    if not ids or not action:
        return jsonify({"error": "ids and action are required"}), 400

    ALLOWED_ACTIONS = ("close", "reopen", "delete", "reassign", "relink_goal")
    if action not in ALLOWED_ACTIONS:
        return jsonify({"error": f"action must be one of {ALLOWED_ACTIONS}"}), 400

    now = _now()
    updated = 0

    try:
        for tid in ids:
            ticket = Ticket.query.get(tid)
            if not ticket:
                continue
            if action == "close":
                old = ticket.status
                ticket.status = "closed"
                ticket.resolved_at = ticket.resolved_at or now
                ticket.updated_at = now
                _log_activity(tid, current_user.username, "status_changed", {"from": old, "to": "closed"})
            elif action == "reopen":
                old = ticket.status
                ticket.status = "open"
                ticket.resolved_at = None
                ticket.updated_at = now
                _log_activity(tid, current_user.username, "status_changed", {"from": old, "to": "open"})
            elif action == "delete":
                db.session.delete(ticket)
                _log_activity(tid, current_user.username, "deleted", {})
            elif action == "reassign":
                new_agent = payload.get("assignee_agent")
                ticket.assignee_agent = new_agent
                ticket.updated_at = now
                _log_activity(tid, current_user.username, "assigned", {"assignee_agent": new_agent})
            elif action == "relink_goal":
                ticket.goal_id = payload.get("goal_id")
                ticket.updated_at = now
                _log_activity(tid, current_user.username, "updated", {"goal_id": ticket.goal_id})
            updated += 1
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"bulk action failed: {exc}"}), 500

    audit(current_user, "manage", "tickets", f"bulk {action} on {updated} tickets")
    return jsonify({"updated": updated, "action": action})


# --------------- CSV export ---------------

@bp.route("/api/tickets/export.csv")
def export_csv():
    denied = _require("view")
    if denied:
        return denied

    # Same filters as list
    statuses = request.args.getlist("status")
    assignees = request.args.getlist("assignee_agent")
    priorities = request.args.getlist("priority")
    project_id = request.args.get("project_id", type=int)
    goal_id = request.args.get("goal_id", type=int)
    q = request.args.get("q", "").strip()

    query = Ticket.query
    if statuses:
        query = query.filter(Ticket.status.in_(statuses))
    if assignees:
        query = query.filter(Ticket.assignee_agent.in_(assignees))
    if priorities:
        query = query.filter(Ticket.priority.in_(priorities))
    if project_id is not None:
        query = query.filter(Ticket.project_id == project_id)
    if goal_id is not None:
        query = query.filter(Ticket.goal_id == goal_id)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Ticket.title.ilike(like), Ticket.description.ilike(like))
        )

    tickets = query.order_by(Ticket.priority_rank.desc(), Ticket.created_at.asc()).limit(10000).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "title", "status", "priority", "assignee_agent", "project_id", "goal_id", "created_at", "updated_at", "resolved_at"])
    for t in tickets:
        writer.writerow([
            t.id, t.title, t.status, t.priority, t.assignee_agent or "",
            t.project_id or "", t.goal_id or "", t.created_at, t.updated_at, t.resolved_at or "",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=tickets.csv"},
    )


# ============================================================
# Thread-Areas endpoints
# ============================================================

# --------------- Convert to thread ---------------

@bp.route("/api/tickets/<string:ticket_id>/convert-to-thread", methods=["PATCH"])
def convert_to_thread(ticket_id: str):
    denied = _require("execute")
    if denied:
        return denied
    ticket = Ticket.query.get_or_404(ticket_id)

    data = request.get_json() or {}
    workspace_path = (data.get("workspace_path") or "").strip()

    if ticket.memory_md_path is not None:
        # Idempotência: já é thread. Conflito só se workspace_path diferente foi passado.
        if workspace_path and workspace_path != ticket.workspace_path:
            return jsonify({
                "error": "workspace_path_conflict",
                "current_workspace_path": ticket.workspace_path,
                "requested_workspace_path": workspace_path,
            }), 409
        # No-op — retorna estado atual sem criar nada novo
        return jsonify(ticket.to_dict()), 200
    if not workspace_path:
        return jsonify({"error": "workspace_path is required"}), 400

    # Validate workspace_path stays inside WORKSPACE
    target = (WORKSPACE / workspace_path).resolve()
    if not str(target).startswith(str(WORKSPACE.resolve())):
        return jsonify({"error": "workspace_path must be inside workspace root"}), 400

    # Create the workspace path if it doesn't exist (Q3: UI offers create if not exists)
    target.mkdir(parents=True, exist_ok=True)

    # Create memory directory + empty memory.md
    memory_dir = WORKSPACE / "memory" / "threads" / ticket_id
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_md = memory_dir / "memory.md"
    if not memory_md.exists():
        memory_md.write_text(
            f"# Thread Memory — {ticket.title}\n\n"
            f"Ticket ID: {ticket_id}\n"
            f"Created: {_now()}\n\n"
            f"---\n",
            encoding="utf-8",
        )

    memory_md_path = f"memory/threads/{ticket_id}/memory.md"
    now = _now()
    ticket.workspace_path = workspace_path
    ticket.memory_md_path = memory_md_path
    ticket.thread_session_id = None  # Lazy — created on first chat turn (RF2)
    ticket.updated_at = now

    _log_activity(
        ticket_id,
        current_user.username,
        "converted_to_thread",
        {"workspace_path": workspace_path, "memory_md_path": memory_md_path},
    )
    db.session.commit()

    audit(current_user, "execute", "tickets", f"converted ticket {ticket_id} to thread")
    return jsonify(ticket.to_dict())


# --------------- Get thread memory ---------------

@bp.route("/api/tickets/<string:ticket_id>/memory")
def get_thread_memory(ticket_id: str):
    denied = _require("view")
    if denied:
        return denied
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.memory_md_path is None:
        return jsonify({"error": "not_a_thread"}), 400

    memory_file = WORKSPACE / ticket.memory_md_path
    if not memory_file.exists():
        return jsonify({"content": "", "size_bytes": 0, "truncated": False})

    raw = memory_file.read_bytes()
    size = len(raw)
    truncated = False
    content = raw.decode("utf-8", errors="replace")

    if size > MEMORY_WARN_BYTES:
        # Truncate by section: keep as many complete sections (## headings) as fit under 32KB
        sections = re.split(r"(?=\n## )", content)
        kept: list[str] = []
        total = 0
        for section in reversed(sections):
            section_bytes = len(section.encode("utf-8"))
            if total + section_bytes <= MEMORY_TRUNCATE_BYTES:
                kept.insert(0, section)
                total += section_bytes
            else:
                truncated = True
        content = "".join(kept)

    return jsonify({
        "content": content,
        "size_bytes": size,
        "truncated": truncated,
        "memory_md_path": ticket.memory_md_path,
    })


# --------------- Workspace subfolders ---------------

@bp.route("/api/workspace/subfolders")
def workspace_subfolders():
    """Return immediate subdirectories of workspace/ for the convert-to-thread modal."""
    denied = _require("view")
    if denied:
        return denied
    root = WORKSPACE / "workspace"
    folders: list[dict] = []
    try:
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                rel_path = f"workspace/{entry.name}"
                folders.append({
                    "name": entry.name,
                    "path": rel_path,
                    "full_path": str(entry),
                })
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"folders": folders})


@bp.route("/api/workspace/subfolders", methods=["POST"])
def create_workspace_subfolder():
    """Create a new immediate subdirectory under workspace/."""
    denied = _require("execute")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()

    if not name:
        return jsonify({"error": "name_required"}), 400
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return jsonify({"error": "invalid_name", "message": "Use só letras minúsculas, números e hífen."}), 400
    if len(name) < 2 or len(name) > 50:
        return jsonify({"error": "invalid_length"}), 400

    root = WORKSPACE / "workspace"
    target = root / name
    # path traversal defense
    resolved = target.resolve()
    if not str(resolved).startswith(str(root.resolve())):
        return jsonify({"error": "path_traversal"}), 400

    if target.exists():
        return jsonify({"error": "already_exists", "path": f"workspace/{name}"}), 409

    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return jsonify({"error": "mkdir_failed", "message": str(exc)}), 500

    return jsonify({
        "name": name,
        "path": f"workspace/{name}",
        "full_path": str(target),
    }), 201


# --------------- Turn completed (Option D — summary trigger) ---------------

@bp.route("/api/tickets/<string:ticket_id>/turn-completed", methods=["POST"])
def turn_completed(ticket_id: str):
    """Frontend fires this on every chat_complete event for thread tickets.

    Monotonically increments message_count. If the delta since last_summary_at_message
    reaches SUMMARY_EVERY_N, enqueues a summary job (fire-and-forget subprocess).
    Idempotent: UPDATE ... WHERE message_count < :n prevents decrement races.
    """
    denied = _require("execute")
    if denied:
        return denied
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.memory_md_path is None:
        return jsonify({"error": "not_a_thread"}), 400

    now = _now()

    # Monotonic increment — race-safe: SET message_count = :n WHERE message_count < :n
    # Two concurrent POSTs with the same current value will both compute n = current + 1;
    # only the first will satisfy the WHERE guard; the second is a no-op (rowcount == 0).
    expected = ticket.message_count + 1
    result = db.session.execute(
        db.text(
            "UPDATE tickets SET message_count = :n, updated_at = :now "
            "WHERE id = :id AND message_count < :n"
        ),
        {"id": ticket_id, "n": expected, "now": now},
    )
    db.session.commit()
    if result.rowcount == 0:
        # Already at or above expected — no-op, return current state silently
        import logging
        logging.getLogger(__name__).debug(
            "[turn-completed] no-op for ticket %s (already at message_count >= %d)", ticket_id, expected
        )

    # Re-read fresh count
    db.session.expire(ticket)
    ticket = Ticket.query.get(ticket_id)
    new_count = ticket.message_count
    delta = new_count - ticket.last_summary_at_message

    summary_queued = False
    if delta >= SUMMARY_EVERY_N:
        # Update last_summary_at_message first (monotonic guard)
        updated = db.session.execute(
            db.text(
                "UPDATE tickets SET last_summary_at_message = :new_count, updated_at = :now "
                "WHERE id = :id AND last_summary_at_message < :new_count"
            ),
            {"id": ticket_id, "new_count": new_count, "now": now},
        )
        db.session.commit()
        if updated.rowcount > 0:
            _enqueue_summary(ticket_id, ticket.memory_md_path, new_count)
            summary_queued = True

    return jsonify({
        "message_count": new_count,
        "last_summary_at_message": ticket.last_summary_at_message,
        "summary_queued": summary_queued,
    })


def _enqueue_summary(ticket_id: str, memory_md_path: str, up_to_turn: int) -> None:
    """Fire-and-forget: spawn summary_worker.py as a subprocess."""
    import subprocess
    import sys
    worker = Path(__file__).resolve().parent.parent / "summary_worker.py"
    if not worker.exists():
        print(f"[tickets] WARNING: summary_worker.py not found at {worker}", flush=True)
        return
    subprocess.Popen(
        [sys.executable, str(worker), "--ticket-id", ticket_id,
         "--memory-path", memory_md_path, "--up-to-turn", str(up_to_turn)],
        start_new_session=True,
    )
    print(f"[tickets] summary job queued for ticket {ticket_id} (up to turn {up_to_turn})", flush=True)


# --------------- Archive thread ---------------

@bp.route("/api/tickets/<string:ticket_id>/archive-thread", methods=["POST"])
def archive_thread(ticket_id: str):
    """Tombstone: status='archived', move memory folder to _archive/."""
    denied = _require("manage")
    if denied:
        return denied
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.memory_md_path is None:
        return jsonify({"error": "not_a_thread"}), 400
    if ticket.status == "archived":
        return jsonify({"error": "already_archived"}), 409

    src_dir = WORKSPACE / "memory" / "threads" / ticket_id
    archive_dir = WORKSPACE / "memory" / "threads" / "_archive" / ticket_id

    if src_dir.exists():
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_dir), str(archive_dir))
        # Write tombstone
        (archive_dir / "ARCHIVED.json").write_text(
            json.dumps({"ticket_id": ticket_id, "archived_at": _now(), "archived_by": current_user.username}),
            encoding="utf-8",
        )

    now = _now()
    old_status = ticket.status
    ticket.status = "archived"
    ticket.updated_at = now
    _log_activity(ticket_id, current_user.username, "status_changed", {"from": old_status, "to": "archived"})
    db.session.commit()

    audit(current_user, "manage", "tickets", f"archived thread {ticket_id}")
    return jsonify(ticket.to_dict())


# --------------- Unarchive thread ---------------

@bp.route("/api/tickets/<string:ticket_id>/unarchive-thread", methods=["POST"])
def unarchive_thread(ticket_id: str):
    """Restore archived thread: move memory folder back, set status='open'."""
    denied = _require("manage")
    if denied:
        return denied
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.memory_md_path is None:
        return jsonify({"error": "not_a_thread"}), 400
    if ticket.status != "archived":
        return jsonify({"error": "not_archived"}), 409

    archive_dir = WORKSPACE / "memory" / "threads" / "_archive" / ticket_id
    src_dir = WORKSPACE / "memory" / "threads" / ticket_id

    if archive_dir.exists():
        # Remove tombstone before moving back
        tombstone = archive_dir / "ARCHIVED.json"
        if tombstone.exists():
            tombstone.unlink()
        shutil.move(str(archive_dir), str(src_dir))

    now = _now()
    ticket.status = "open"
    ticket.updated_at = now
    _log_activity(ticket_id, current_user.username, "status_changed", {"from": "archived", "to": "open"})
    db.session.commit()

    audit(current_user, "manage", "tickets", f"unarchived thread {ticket_id}")
    return jsonify(ticket.to_dict())
