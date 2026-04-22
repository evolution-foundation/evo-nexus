"""Goals API — CRUD for Mission → Project → Goal → Task hierarchy (Feature 1.2)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flask import Blueprint, jsonify, request
from flask_login import current_user
from models import db, Mission, GoalProject, Goal, GoalTask, has_permission, audit

bp = Blueprint("goals", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent


def _require(action: str, resource: str = "goals"):
    if not has_permission(current_user.role, resource, action):
        return jsonify({"error": "Forbidden"}), 403
    return None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _db_path() -> str:
    return str(WORKSPACE / "dashboard" / "data" / "evonexus.db")


# --------------- Missions ---------------

@bp.route("/api/missions")
def list_missions():
    denied = _require("view")
    if denied:
        return denied
    missions = Mission.query.order_by(Mission.id.asc()).all()
    return jsonify([m.to_dict(include_projects=True) for m in missions])


@bp.route("/api/missions/<int:mission_id>")
def get_mission(mission_id: int):
    denied = _require("view")
    if denied:
        return denied
    mission = Mission.query.get_or_404(mission_id)
    return jsonify(mission.to_dict(include_projects=True))


@bp.route("/api/missions", methods=["POST"])
def create_mission():
    denied = _require("manage")
    if denied:
        return denied
    data = request.get_json() or {}
    if not data.get("slug") or not data.get("title"):
        return jsonify({"error": "slug and title are required"}), 400
    if Mission.query.filter_by(slug=data["slug"]).first():
        return jsonify({"error": f"Mission slug '{data['slug']}' already exists"}), 409
    now = _now()
    m = Mission(
        slug=data["slug"],
        title=data["title"],
        description=data.get("description"),
        target_metric=data.get("target_metric"),
        target_value=data.get("target_value"),
        current_value=data.get("current_value", 0),
        due_date=data.get("due_date"),
        status=data.get("status", "active"),
        created_at=now,
        updated_at=now,
    )
    db.session.add(m)
    db.session.commit()
    audit(current_user, "create", "goals", f"Created mission #{m.id}: {m.title}")
    return jsonify(m.to_dict()), 201


@bp.route("/api/missions/<int:mission_id>", methods=["PATCH"])
def patch_mission(mission_id: int):
    denied = _require("manage")
    if denied:
        return denied
    m = Mission.query.get_or_404(mission_id)
    data = request.get_json() or {}
    for field in ("title", "description", "target_metric", "target_value", "current_value", "due_date", "status"):
        if field in data:
            setattr(m, field, data[field])
    m.updated_at = _now()
    db.session.commit()
    audit(current_user, "update", "goals", f"Updated mission #{m.id}: {m.title}")
    return jsonify(m.to_dict())


@bp.route("/api/missions/<int:mission_id>", methods=["DELETE"])
def delete_mission(mission_id: int):
    denied = _require("manage")
    if denied:
        return denied
    m = Mission.query.get_or_404(mission_id)
    db.session.delete(m)
    db.session.commit()
    audit(current_user, "delete", "goals", f"Deleted mission #{mission_id}")
    return jsonify({"status": "ok"})


# --------------- Projects ---------------

@bp.route("/api/projects")
def list_projects():
    denied = _require("view")
    if denied:
        return denied
    mission_id = request.args.get("mission_id", type=int)
    q = GoalProject.query
    if mission_id:
        q = q.filter_by(mission_id=mission_id)
    projects = q.order_by(GoalProject.id.asc()).all()
    include_goals = request.args.get("include_goals", "false").lower() == "true"
    return jsonify([p.to_dict(include_goals=include_goals) for p in projects])


@bp.route("/api/projects/<int:project_id>")
def get_project(project_id: int):
    denied = _require("view")
    if denied:
        return denied
    p = GoalProject.query.get_or_404(project_id)
    return jsonify(p.to_dict(include_goals=True))


@bp.route("/api/projects", methods=["POST"])
def create_project():
    denied = _require("manage")
    if denied:
        return denied
    data = request.get_json() or {}
    if not data.get("slug") or not data.get("title"):
        return jsonify({"error": "slug and title are required"}), 400
    if GoalProject.query.filter_by(slug=data["slug"]).first():
        return jsonify({"error": f"Project slug '{data['slug']}' already exists"}), 409
    now = _now()
    p = GoalProject(
        slug=data["slug"],
        mission_id=data.get("mission_id"),
        title=data["title"],
        description=data.get("description"),
        workspace_folder_path=data.get("workspace_folder_path"),
        status=data.get("status", "active"),
        created_at=now,
        updated_at=now,
    )
    db.session.add(p)
    db.session.commit()
    audit(current_user, "create", "goals", f"Created project #{p.id}: {p.title}")
    return jsonify(p.to_dict()), 201


@bp.route("/api/projects/<int:project_id>", methods=["PATCH"])
def patch_project(project_id: int):
    denied = _require("manage")
    if denied:
        return denied
    p = GoalProject.query.get_or_404(project_id)
    data = request.get_json() or {}
    for field in ("title", "description", "mission_id", "workspace_folder_path", "status"):
        if field in data:
            setattr(p, field, data[field])
    p.updated_at = _now()
    db.session.commit()
    audit(current_user, "update", "goals", f"Updated project #{p.id}: {p.title}")
    return jsonify(p.to_dict())


@bp.route("/api/projects/<int:project_id>", methods=["DELETE"])
def delete_project(project_id: int):
    denied = _require("manage")
    if denied:
        return denied
    p = GoalProject.query.get_or_404(project_id)
    db.session.delete(p)
    db.session.commit()
    audit(current_user, "delete", "goals", f"Deleted project #{project_id}")
    return jsonify({"status": "ok"})


# --------------- Goals ---------------

@bp.route("/api/goals")
def list_goals():
    denied = _require("view")
    if denied:
        return denied
    project_id = request.args.get("project_id", type=int)
    status_filter = request.args.get("status")
    due_filter = request.args.get("due_date")

    q = Goal.query
    if project_id:
        q = q.filter_by(project_id=project_id)
    if status_filter:
        q = q.filter_by(status=status_filter)

    from datetime import date, timedelta
    today = date.today().isoformat()
    if due_filter == "overdue":
        q = q.filter(Goal.due_date < today, Goal.status == "active")
    elif due_filter == "this-week":
        week_end = (date.today() + timedelta(days=7)).isoformat()
        q = q.filter(Goal.due_date >= today, Goal.due_date <= week_end)
    elif due_filter == "this-month":
        month_end = (date.today() + timedelta(days=30)).isoformat()
        q = q.filter(Goal.due_date >= today, Goal.due_date <= month_end)

    goals = q.order_by(Goal.due_date.asc()).all()
    include_tasks = request.args.get("include_tasks", "false").lower() == "true"
    return jsonify([g.to_dict(include_tasks=include_tasks) for g in goals])


@bp.route("/api/goals/<int:goal_id>")
def get_goal(goal_id: int):
    denied = _require("view")
    if denied:
        return denied
    g = Goal.query.get_or_404(goal_id)
    return jsonify(g.to_dict(include_tasks=True))


@bp.route("/api/goals", methods=["POST"])
def create_goal():
    denied = _require("manage")
    if denied:
        return denied
    data = request.get_json() or {}
    if not data.get("slug") or not data.get("title") or not data.get("project_id"):
        return jsonify({"error": "slug, title, and project_id are required"}), 400
    if Goal.query.filter_by(slug=data["slug"]).first():
        return jsonify({"error": f"Goal slug '{data['slug']}' already exists"}), 409
    now = _now()
    g = Goal(
        slug=data["slug"],
        project_id=data["project_id"],
        title=data["title"],
        description=data.get("description"),
        target_metric=data.get("target_metric"),
        metric_type=data.get("metric_type", "count"),
        target_value=data.get("target_value", 1.0),
        current_value=data.get("current_value", 0.0),
        due_date=data.get("due_date"),
        status=data.get("status", "active"),
        created_at=now,
        updated_at=now,
    )
    db.session.add(g)
    db.session.commit()
    audit(current_user, "create", "goals", f"Created goal #{g.id}: {g.title}")
    return jsonify(g.to_dict()), 201


@bp.route("/api/goals/<int:goal_id>", methods=["PATCH"])
def patch_goal(goal_id: int):
    denied = _require("manage")
    if denied:
        return denied
    g = Goal.query.get_or_404(goal_id)
    data = request.get_json() or {}
    for field in ("title", "description", "target_metric", "metric_type", "target_value",
                  "current_value", "due_date", "status", "project_id"):
        if field in data:
            setattr(g, field, data[field])
    g.updated_at = _now()
    db.session.commit()
    audit(current_user, "update", "goals", f"Updated goal #{g.id}: {g.title}")
    return jsonify(g.to_dict())


@bp.route("/api/goals/<int:goal_id>", methods=["DELETE"])
def delete_goal(goal_id: int):
    denied = _require("manage")
    if denied:
        return denied
    g = Goal.query.get_or_404(goal_id)
    db.session.delete(g)
    db.session.commit()
    audit(current_user, "delete", "goals", f"Deleted goal #{goal_id}")
    return jsonify({"status": "ok"})


@bp.route("/api/goals/<int:goal_id>/recalculate", methods=["POST"])
def recalculate_goal(goal_id: int):
    """Drift correction: recompute current_value from goal_progress_v."""
    denied = _require("execute")
    if denied:
        return denied
    g = Goal.query.get_or_404(goal_id)

    conn = sqlite3.connect(_db_path())
    row = conn.execute(
        "SELECT done_tasks, pct_complete FROM goal_progress_v WHERE goal_id = ?", (goal_id,)
    ).fetchone()
    conn.close()

    if row:
        g.current_value = float(row[0])
        if g.current_value >= g.target_value and g.status == "active":
            g.status = "achieved"
    g.updated_at = _now()
    db.session.commit()
    audit(current_user, "recalculate", "goals", f"Recalculated goal #{goal_id}")
    return jsonify(g.to_dict())


# --------------- Goal Tasks ---------------

@bp.route("/api/goal-tasks")
def list_goal_tasks():
    denied = _require("view")
    if denied:
        return denied
    goal_id = request.args.get("goal_id", type=int)
    status_filter = request.args.get("status")
    assignee = request.args.get("assignee")

    q = GoalTask.query
    if goal_id:
        q = q.filter_by(goal_id=goal_id)
    if status_filter:
        q = q.filter_by(status=status_filter)
    if assignee:
        q = q.filter_by(assignee_agent=assignee)

    tasks = q.order_by(GoalTask.priority.asc(), GoalTask.id.asc()).all()
    return jsonify([t.to_dict() for t in tasks])


@bp.route("/api/goal-tasks/<int:task_id>")
def get_goal_task(task_id: int):
    denied = _require("view")
    if denied:
        return denied
    t = GoalTask.query.get_or_404(task_id)
    return jsonify(t.to_dict())


@bp.route("/api/goal-tasks", methods=["POST"])
def create_goal_task():
    denied = _require("execute")
    if denied:
        return denied
    data = request.get_json() or {}
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400
    now = _now()
    t = GoalTask(
        goal_id=data.get("goal_id"),
        title=data["title"],
        description=data.get("description"),
        priority=data.get("priority", 3),
        assignee_agent=data.get("assignee_agent"),
        status=data.get("status", "open"),
        due_date=data.get("due_date"),
        created_at=now,
        updated_at=now,
    )
    db.session.add(t)
    db.session.commit()
    audit(current_user, "create", "goals", f"Created goal task #{t.id}: {t.title}")
    return jsonify(t.to_dict()), 201


@bp.route("/api/goal-tasks/<int:task_id>", methods=["PATCH"])
def patch_goal_task(task_id: int):
    denied = _require("execute")
    if denied:
        return denied
    t = GoalTask.query.get_or_404(task_id)
    data = request.get_json() or {}
    old_status = t.status
    for field in ("title", "description", "priority", "assignee_agent", "status",
                  "goal_id", "due_date", "locked_at", "locked_by"):
        if field in data:
            setattr(t, field, data[field])
    t.updated_at = _now()
    db.session.commit()

    # If status changed to done and there's a goal, trigger recalc via the view
    if data.get("status") == "done" and old_status != "done" and t.goal_id:
        _recalculate_goal_value(t.goal_id)

    audit(current_user, "update", "goals", f"Updated goal task #{t.id}: {t.title}")
    return jsonify(t.to_dict())


@bp.route("/api/goal-tasks/<int:task_id>", methods=["DELETE"])
def delete_goal_task(task_id: int):
    denied = _require("execute")
    if denied:
        return denied
    t = GoalTask.query.get_or_404(task_id)
    db.session.delete(t)
    db.session.commit()
    audit(current_user, "delete", "goals", f"Deleted goal task #{task_id}")
    return jsonify({"status": "ok"})


def _recalculate_goal_value(goal_id: int):
    """Internal helper: sync goal.current_value with done tasks count."""
    conn = sqlite3.connect(_db_path())
    row = conn.execute(
        "SELECT done_tasks FROM goal_progress_v WHERE goal_id = ?", (goal_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return
    g = Goal.query.get(goal_id)
    if g is None:
        return
    g.current_value = float(row[0])
    if g.current_value >= g.target_value and g.status == "active":
        g.status = "achieved"
    g.updated_at = _now()
    db.session.commit()


# --------------- Link routine to goal ---------------

@bp.route("/api/goals/link-routine", methods=["POST"])
def link_routine_to_goal():
    """Write goal_id to a routine entry in config/routines.yaml."""
    denied = _require("manage")
    if denied:
        return denied
    data = request.get_json() or {}
    routine_name = data.get("routine_name")
    goal_slug = data.get("goal_id")  # slug string

    if not routine_name or not goal_slug:
        return jsonify({"error": "routine_name and goal_id (slug) are required"}), 400

    # Validate goal exists
    if not Goal.query.filter_by(slug=goal_slug).first():
        return jsonify({"error": f"Goal '{goal_slug}' not found"}), 404

    routines_path = WORKSPACE / "config" / "routines.yaml"
    if not routines_path.exists():
        return jsonify({"error": "config/routines.yaml not found"}), 404

    import yaml  # type: ignore
    with open(routines_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    updated = False
    for section_key, section_val in config.items():
        if not isinstance(section_val, list):
            continue
        for routine in section_val:
            if isinstance(routine, dict) and routine.get("name") == routine_name:
                routine["goal_id"] = goal_slug
                updated = True
                break
        if updated:
            break

    if not updated:
        return jsonify({"error": f"Routine '{routine_name}' not found in routines.yaml"}), 404

    with open(routines_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    audit(current_user, "link", "goals", f"Linked routine '{routine_name}' to goal '{goal_slug}'")
    return jsonify({"status": "ok", "routine_name": routine_name, "goal_id": goal_slug})
