from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def _services_module():
    import routes.services as services

    return services


def test_dashboard_reports_current_api_process_as_running():
    services = _services_module()

    assert services._check_dashboard() == {
        "running": True,
        "detail": "Running (serving this API)",
    }


def test_scheduler_reports_fresh_shared_heartbeat_as_running(tmp_path, monkeypatch):
    services = _services_module()
    heartbeat = tmp_path / "scheduler-status.json"
    heartbeat.write_text(
        '{"updated_at": "2026-07-25T12:00:00+00:00"}', encoding="utf-8"
    )
    monkeypatch.setattr(services, "SCHEDULER_STATUS_FILE", heartbeat)
    monkeypatch.setattr(services, "_now_utc", lambda: datetime(2026, 7, 25, 12, 0, 20, tzinfo=timezone.utc))

    assert services._check_scheduler() == {
        "running": True,
        "detail": "Running (shared heartbeat)",
    }


def test_scheduler_does_not_report_stale_shared_heartbeat_as_running(tmp_path, monkeypatch):
    services = _services_module()
    heartbeat = tmp_path / "scheduler-status.json"
    heartbeat.write_text(
        '{"updated_at": "2026-07-25T11:58:00+00:00"}', encoding="utf-8"
    )
    monkeypatch.setattr(services, "SCHEDULER_STATUS_FILE", heartbeat)
    monkeypatch.setattr(services, "_now_utc", lambda: datetime(2026, 7, 25, 12, 0, 20, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "_check_local_scheduler", lambda: {"running": False, "detail": ""})

    assert services._check_scheduler() == {
        "running": False,
        "detail": "Heartbeat stale",
    }


def test_scheduler_keeps_local_process_fallback_when_no_shared_heartbeat(tmp_path, monkeypatch):
    services = _services_module()
    monkeypatch.setattr(services, "SCHEDULER_STATUS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(services, "_check_local_scheduler", lambda: {"running": True, "detail": "local scheduler"})

    assert services._check_scheduler() == {"running": True, "detail": "local scheduler"}


def test_swarm_service_actions_do_not_claim_local_control(monkeypatch):
    services = _services_module()
    app = Flask(__name__)
    app.register_blueprint(services.bp)
    monkeypatch.setenv("EVONEXUS_CONTAINERIZED", "1")

    response = app.test_client().post("/api/services/scheduler/stop")

    assert response.status_code == 409
    assert "Swarm" in response.get_json()["error"]


def test_list_services_has_one_container_aware_dashboard_entry(monkeypatch):
    services = _services_module()
    app = Flask(__name__)
    app.register_blueprint(services.bp)
    monkeypatch.setattr(services, "_check_process", lambda *args, **kwargs: {"running": False, "detail": ""})

    response = app.test_client().get("/api/services")

    dashboards = [service for service in response.get_json() if service["id"] == "dashboard"]
    assert dashboards == [{
        "id": "dashboard",
        "name": "Dashboard",
        "description": "Dashboard API and web interface",
        "command": "make dashboard-app",
        "running": True,
        "detail": "Running (serving this API)",
    }]
