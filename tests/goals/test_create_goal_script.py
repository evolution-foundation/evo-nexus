"""Regression coverage for the /create-goal helper's API write path."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".claude/skills/create-goal/scripts/nexus_goal.py"
SPEC = importlib.util.spec_from_file_location("nexus_goal_script", SCRIPT)
nexus_goal = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(nexus_goal)


def test_create_goal_posts_to_dashboard_api():
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = None
    args = argparse.Namespace(
        slug="api-goal",
        title="Goal via API",
        description="Must wake goal-planner",
        project_slug="global",
        project_title="Global",
        mission_slug=None,
        metric_type="count",
        target_metric="tickets",
        target_value=3,
        current_value=0,
        due_date="2026-08-01",
        status="active",
    )
    project = {"id": 7, "slug": "global", "created": False}
    api_goal = {"id": 11, "slug": "api-goal", "title": "Goal via API"}

    with patch.object(nexus_goal, "ensure_project", return_value=project), \
         patch.object(nexus_goal, "api_post", return_value=api_goal) as mock_post:
        result = nexus_goal.create_goal(conn, args)

    mock_post.assert_called_once_with("/api/goals", {
        "slug": "api-goal",
        "project_id": 7,
        "title": "Goal via API",
        "description": "Must wake goal-planner",
        "target_metric": "tickets",
        "metric_type": "count",
        "target_value": 3,
        "current_value": 0,
        "due_date": "2026-08-01",
        "status": "active",
    })
    assert result["id"] == 11
    assert result["created"] is True
    assert result["project"] == project
