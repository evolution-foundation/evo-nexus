"""
tests/goals/test_publish_approval_content.py

Telegram-approval audit fix (2026-07-17) — the publish-gate approval message
used to show outcome["result"] (an agent's free-text summary) while
_run_publish_action actually publishes outcome["publish_content"]/
publish_media, a DIFFERENT pair of fields. A human approving the summary
never saw the exact text/media going live. This file proves the fix:
heartbeat_outcome._build_publish_approval_body now renders the real
publish_content/publish_media, plus a Missão/Projeto context line via
_publish_context_line so an approval doesn't get lost among parallel
Sistema Britto projects.

Run:
    cd /path/to/workspace && pytest tests/goals/test_publish_approval_content.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import heartbeat_outcome  # noqa: E402

NOW = "2026-07-17T00:00:00.000000Z"


def test_body_shows_real_publish_content_not_result():
    outcome = {
        "result": "Vou postar sobre a promoção de julho amanhã de manhã.",
        "publish_content": "🔥 Promoção de julho: 20% OFF em todos os planos até domingo!",
        "publish_media": ["https://cdn.example.com/promo-julho.png"],
    }
    body = heartbeat_outcome._build_publish_approval_body("instagram", outcome)

    assert "20% OFF em todos os planos" in body
    assert "https://cdn.example.com/promo-julho.png" in body
    assert "Vou postar sobre a promoção de julho amanhã" not in body


def test_body_flags_empty_publish_content():
    outcome = {"result": "resumo qualquer", "publish_content": "", "publish_media": []}
    body = heartbeat_outcome._build_publish_approval_body("linkedin", outcome)
    assert "vazio" in body.lower()


def test_body_includes_context_line_when_provided():
    outcome = {"publish_content": "texto", "publish_media": []}
    body = heartbeat_outcome._build_publish_approval_body(
        "x", outcome, context_line="Missão: Evolution MRR $1M · Projeto: Evo AI"
    )
    assert body.startswith("Missão: Evolution MRR $1M · Projeto: Evo AI")


def test_body_ignores_non_list_publish_media():
    outcome = {"publish_content": "texto", "publish_media": "not-a-list"}
    body = heartbeat_outcome._build_publish_approval_body("x", outcome)
    assert "Mídia:" not in body


# ---------------------------------------------------------------------------
# _publish_context_line — raw sqlite: ticket -> goal -> project -> mission
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE missions (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL);
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            mission_id INTEGER REFERENCES missions(id)
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            project_id INTEGER REFERENCES projects(id)
        );
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, goal_id INTEGER REFERENCES goals(id)
        );
        """
    )
    c.commit()
    yield c
    c.close()


def test_context_line_walks_ticket_to_mission(conn):
    conn.execute("INSERT INTO missions (id, title) VALUES (1, 'Evolution MRR $1M')")
    conn.execute("INSERT INTO projects (id, title, mission_id) VALUES (1, 'Evo AI', 1)")
    conn.execute("INSERT INTO goals (id, title, project_id) VALUES (1, '100 clientes', 1)")
    conn.execute("INSERT INTO tickets (id, title, goal_id) VALUES ('t1', 'Post X', 1)")
    conn.commit()

    line = heartbeat_outcome._publish_context_line("t1", conn)
    assert "Evolution MRR $1M" in line
    assert "Evo AI" in line


def test_context_line_empty_when_ticket_has_no_goal(conn):
    conn.execute("INSERT INTO tickets (id, title, goal_id) VALUES ('t2', 'Post Y', NULL)")
    conn.commit()
    assert heartbeat_outcome._publish_context_line("t2", conn) == ""


def test_context_line_never_raises_for_missing_ticket(conn):
    assert heartbeat_outcome._publish_context_line("does-not-exist", conn) == ""
