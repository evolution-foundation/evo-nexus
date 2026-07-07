#!/usr/bin/env python3
"""Local EvoNexus goal writer.

This is intentionally DB-local because agent sessions do not reliably share the
dashboard browser auth cookie. The dashboard API remains the UI path; this script
is the trusted local automation path for the /create-goal skill.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_METRIC_TYPES = {"count", "currency", "percentage", "percent", "boolean", "tasks"}


def workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "dashboard" / "data" / "evonexus.db").exists():
            return parent
    raise SystemExit("Could not find dashboard/data/evonexus.db from script path")


def db_path() -> Path:
    return workspace_root() / "dashboard" / "data" / "evonexus.db"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "goal"


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_mission(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    slug = args.slug or slugify(args.title)
    existing = conn.execute("SELECT * FROM missions WHERE slug=?", (slug,)).fetchone()
    if existing:
        return row_to_dict(existing) | {"created": False}
    ts = now()
    conn.execute(
        """INSERT INTO missions
           (slug, title, description, target_metric, target_value, current_value, due_date, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            slug,
            args.title,
            args.description,
            args.target_metric,
            args.target_value,
            args.current_value,
            args.due_date,
            args.status,
            ts,
            ts,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM missions WHERE slug=?", (slug,)).fetchone()
    return row_to_dict(row) | {"created": True}


def ensure_project(
    conn: sqlite3.Connection,
    *,
    slug: str,
    title: str | None = None,
    description: str | None = None,
    mission_slug: str | None = None,
    workspace_folder_path: str | None = None,
) -> dict[str, Any]:
    existing = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
    if existing:
        return row_to_dict(existing) | {"created": False}

    mission_id = None
    if mission_slug:
        mission = conn.execute("SELECT id FROM missions WHERE slug=?", (mission_slug,)).fetchone()
        if mission is None:
            raise SystemExit(f"Mission not found: {mission_slug}")
        mission_id = mission["id"]

    ts = now()
    conn.execute(
        """INSERT INTO projects
           (slug, mission_id, title, description, workspace_folder_path, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
        (
            slug,
            mission_id,
            title or slug.replace("-", " ").title(),
            description,
            workspace_folder_path,
            ts,
            ts,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
    return row_to_dict(row) | {"created": True}


def create_project(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    slug = args.slug or slugify(args.title)
    return ensure_project(
        conn,
        slug=slug,
        title=args.title,
        description=args.description,
        mission_slug=args.mission_slug,
        workspace_folder_path=args.workspace_folder_path,
    )


def create_goal(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    metric_type = args.metric_type
    if metric_type == "percent":
        metric_type = "percentage"
    if metric_type not in VALID_METRIC_TYPES:
        raise SystemExit(f"Invalid metric_type: {args.metric_type}")

    slug = args.slug or slugify(args.title)
    existing = conn.execute("SELECT * FROM goals WHERE slug=?", (slug,)).fetchone()
    if existing:
        return row_to_dict(existing) | {"created": False}

    project_slug = args.project_slug or "global"
    project = ensure_project(
        conn,
        slug=project_slug,
        title=args.project_title or ("Global" if project_slug == "global" else None),
        mission_slug=args.mission_slug,
    )

    target_value = args.target_value
    if metric_type == "boolean":
        target_value = 1.0 if target_value is None else target_value
    elif target_value is None:
        raise SystemExit("--target-value is required unless metric_type=boolean")

    ts = now()
    conn.execute(
        """INSERT INTO goals
           (slug, project_id, title, description, target_metric, metric_type, target_value, current_value, due_date, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            slug,
            project["id"],
            args.title,
            args.description,
            args.target_metric,
            metric_type,
            target_value,
            args.current_value,
            args.due_date,
            args.status,
            ts,
            ts,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM goals WHERE slug=?", (slug,)).fetchone()
    return row_to_dict(row) | {"created": True, "project": project}


def create_task(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    goal_id = None
    if args.goal_slug:
        goal = conn.execute("SELECT id FROM goals WHERE slug=?", (args.goal_slug,)).fetchone()
        if goal is None:
            raise SystemExit(f"Goal not found: {args.goal_slug}")
        goal_id = goal["id"]

    ts = now()
    conn.execute(
        """INSERT INTO goal_tasks
           (goal_id, title, description, priority, assignee_agent, status, due_date, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            goal_id,
            args.title,
            args.description,
            args.priority,
            args.assignee_agent,
            args.status,
            args.due_date,
            ts,
            ts,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM goal_tasks WHERE id=last_insert_rowid()").fetchone()
    return row_to_dict(row) | {"created": True}


def list_tree(conn: sqlite3.Connection, _args: argparse.Namespace) -> dict[str, Any]:
    missions = [dict(r) for r in conn.execute("SELECT * FROM missions ORDER BY id")]
    projects = [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id")]
    goals = [dict(r) for r in conn.execute("SELECT * FROM goals ORDER BY due_date IS NULL, due_date, id")]
    tasks = [dict(r) for r in conn.execute("SELECT * FROM goal_tasks ORDER BY priority, id")]
    return {"missions": missions, "projects": projects, "goals": goals, "goal_tasks": tasks}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Create/list EvoNexus missions, projects, goals, and goal tasks")
    sub = p.add_subparsers(dest="command", required=True)

    mission = sub.add_parser("create-mission")
    mission.add_argument("--title", required=True)
    mission.add_argument("--slug")
    mission.add_argument("--description")
    mission.add_argument("--target-metric")
    mission.add_argument("--target-value", type=float)
    mission.add_argument("--current-value", type=float, default=0.0)
    mission.add_argument("--due-date")
    mission.add_argument("--status", default="active")

    project = sub.add_parser("create-project")
    project.add_argument("--title", required=True)
    project.add_argument("--slug")
    project.add_argument("--description")
    project.add_argument("--mission-slug")
    project.add_argument("--workspace-folder-path")

    goal = sub.add_parser("create-goal")
    goal.add_argument("--title", required=True)
    goal.add_argument("--slug")
    goal.add_argument("--description")
    goal.add_argument("--project-slug", default="global")
    goal.add_argument("--project-title")
    goal.add_argument("--mission-slug")
    goal.add_argument("--metric-type", default="count")
    goal.add_argument("--target-metric")
    goal.add_argument("--target-value", type=float)
    goal.add_argument("--current-value", type=float, default=0.0)
    goal.add_argument("--due-date")
    goal.add_argument("--status", default="active")

    task = sub.add_parser("create-task")
    task.add_argument("--title", required=True)
    task.add_argument("--description")
    task.add_argument("--goal-slug")
    task.add_argument("--priority", type=int, default=3)
    task.add_argument("--assignee-agent")
    task.add_argument("--status", default="open")
    task.add_argument("--due-date")

    sub.add_parser("list")
    return p


def main() -> None:
    args = parser().parse_args()
    with connect() as conn:
        if args.command == "create-mission":
            result = create_mission(conn, args)
        elif args.command == "create-project":
            result = create_project(conn, args)
        elif args.command == "create-goal":
            result = create_goal(conn, args)
        elif args.command == "create-task":
            result = create_task(conn, args)
        elif args.command == "list":
            result = list_tree(conn, args)
        else:
            raise SystemExit(f"Unknown command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
