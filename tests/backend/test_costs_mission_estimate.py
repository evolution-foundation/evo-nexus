"""
tests/backend/test_costs_mission_estimate.py

Panorama 2026-07-17, item 2 — /api/costs gains an ESTIMATED breakdown of
spend by Mission/Project. There is no direct link from a cost record
(routine run, heartbeat run) to a specific Goal/Ticket, so this allocates
each agent's total cost across the Missions/Projects that agent's tickets
belong to, weighted by ticket count — explicitly labeled as an estimate, not
an exact ledger.

Run:
    cd /path/to/workspace && pytest tests/backend/test_costs_mission_estimate.py -v
"""

from __future__ import annotations

import importlib
import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

NOW = "2026-07-17T00:00:00.000000Z"


@pytest.fixture
def app(tmp_path):
    import flask
    from flask_login import LoginManager
    import models as _models
    importlib.reload(_models)

    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    _app.config["SECRET_KEY"] = "test-costs-mission"
    _app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    _app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    _models.db.init_app(_app)

    _login_manager = LoginManager()
    _login_manager.init_app(_app)

    @_login_manager.user_loader
    def _load_user(user_id):
        return _models.User.query.get(int(user_id))

    with _app.app_context():
        _models.db.create_all()
        admin = _models.User(username="admin", role="admin")
        admin.set_password("password")
        _models.db.session.add(admin)
        _models.db.session.commit()

        mission = _models.Mission(slug="m1", title="Evolution MRR", created_at=NOW, updated_at=NOW)
        _models.db.session.add(mission)
        _models.db.session.commit()
        project = _models.GoalProject(slug="p1", title="Evo AI", mission_id=mission.id, created_at=NOW, updated_at=NOW)
        _models.db.session.add(project)
        _models.db.session.commit()
        goal = _models.Goal(slug="g1", title="100 clientes", project_id=project.id, created_at=NOW, updated_at=NOW)
        _models.db.session.add(goal)
        _models.db.session.commit()

        # 3 tickets assigned to pixel-social-media under this Goal/Project/Mission
        for i in range(3):
            _models.db.session.add(_models.Ticket(
                id=str(uuid.uuid4()), title=f"Post {i}", status="open", priority="medium",
                priority_rank=2, goal_id=goal.id, assignee_agent="pixel-social-media",
                created_at=NOW, updated_at=NOW,
            ))
        # 1 ticket assigned to an agent with no cost record anywhere
        _models.db.session.add(_models.Ticket(
            id=str(uuid.uuid4()), title="Orphan work", status="open", priority="medium",
            priority_rank=2, assignee_agent="clawdia-assistant", created_at=NOW, updated_at=NOW,
        ))
        _models.db.session.commit()

    import routes.costs as _costs_routes
    importlib.reload(_costs_routes)
    _app.register_blueprint(_costs_routes.bp)
    return _app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        yield c


def _write_metrics(tmp_path, monkeypatch, data: dict):
    import routes.costs as costs_module
    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text(json.dumps(data))
    monkeypatch.setattr(costs_module, "METRICS_PATH", metrics_file)
    monkeypatch.setattr(costs_module, "LOGS_DIR", tmp_path)


def test_pixel_routine_cost_allocates_to_its_mission_and_project(client, tmp_path, monkeypatch):
    # metrics.json agent field uses the SHORT docstring alias ("pixel"),
    # tickets use the full slug ("pixel-social-media") — the allocator must
    # bridge that gap via prefix match.
    _write_metrics(tmp_path, monkeypatch, {
        "pixel-growth": {"total_cost_usd": 9.0, "runs": 3, "agent": "pixel", "total_input_tokens": 0, "total_output_tokens": 0},
    })

    resp = client.get("/api/costs")
    assert resp.status_code == 200
    body = resp.get_json()

    assert len(body["by_mission"]) == 1
    assert body["by_mission"][0]["title"] == "Evolution MRR"
    assert body["by_mission"][0]["estimated_cost"] == pytest.approx(9.0)
    assert body["by_mission"][0]["ticket_count"] == 3

    assert len(body["by_project"]) == 1
    assert body["by_project"][0]["title"] == "Evo AI"
    assert body["by_project"][0]["estimated_cost"] == pytest.approx(9.0)

    assert body["unallocated_cost"] == 0
    assert "estimativa" in body["methodology"]


def test_cost_for_agent_with_no_tickets_is_unallocated(client, tmp_path, monkeypatch):
    _write_metrics(tmp_path, monkeypatch, {
        "ai-news": {"total_cost_usd": 5.0, "runs": 1, "agent": "sage", "total_input_tokens": 0, "total_output_tokens": 0},
    })
    resp = client.get("/api/costs")
    body = resp.get_json()
    assert body["by_mission"] == []
    assert body["unallocated_cost"] == pytest.approx(5.0)


def test_ambiguous_short_name_prefix_is_unallocated(client, tmp_path, monkeypatch):
    """If two ticket assignee_agent slugs share the same short-name prefix,
    resolving would be a guess — must fall back to unallocated rather than
    picking one arbitrarily."""
    import models as _models
    with client.application.app_context():
        _models.db.session.add(_models.Ticket(
            id=str(uuid.uuid4()), title="Other pixel-ish agent", status="open", priority="medium",
            priority_rank=2, assignee_agent="pixel-other-thing", created_at=NOW, updated_at=NOW,
        ))
        _models.db.session.commit()

    _write_metrics(tmp_path, monkeypatch, {
        "pixel-growth": {"total_cost_usd": 9.0, "runs": 3, "agent": "pixel", "total_input_tokens": 0, "total_output_tokens": 0},
    })
    resp = client.get("/api/costs")
    body = resp.get_json()
    assert body["unallocated_cost"] == pytest.approx(9.0)
    assert body["by_mission"] == []


def test_ticket_with_no_goal_or_project_contributes_to_unallocated(client, tmp_path, monkeypatch):
    import models as _models
    with client.application.app_context():
        _models.db.session.add(_models.Ticket(
            id=str(uuid.uuid4()), title="Freestanding ticket", status="open", priority="medium",
            priority_rank=2, assignee_agent="pixel-social-media", created_at=NOW, updated_at=NOW,
        ))
        _models.db.session.commit()

    _write_metrics(tmp_path, monkeypatch, {
        "pixel-growth": {"total_cost_usd": 12.0, "runs": 4, "agent": "pixel", "total_input_tokens": 0, "total_output_tokens": 0},
    })
    resp = client.get("/api/costs")
    body = resp.get_json()
    # 3 tickets under the Mission/Project + 1 freestanding = 4 total tickets,
    # each worth 12/4 = 3.0; the freestanding one goes to unallocated.
    assert body["by_mission"][0]["estimated_cost"] == pytest.approx(9.0)
    assert body["unallocated_cost"] == pytest.approx(3.0)


def test_empty_metrics_file_returns_empty_estimate_shape(client, tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules["routes.costs"], "METRICS_PATH", tmp_path / "does-not-exist.json")
    resp = client.get("/api/costs")
    body = resp.get_json()
    assert body["by_mission"] == []
    assert body["by_project"] == []
    assert body["unallocated_cost"] == 0
