from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
ADWS_DIR = REPO_ROOT / "ADWs"
sys.path.extend([str(BACKEND_DIR), str(ADWS_DIR)])


@pytest.fixture
def client(tmp_path, monkeypatch):
    import routes.routines as routines

    importlib.reload(routines)
    monkeypatch.setattr(routines, "LOGS_DIR", tmp_path)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(routines.bp)
    return app.test_client()


def test_routine_logs_reads_each_file_once_and_assigns_stable_unique_ids(client, tmp_path):
    entries = [{"timestamp": f"2026-07-25T10:00:0{i}", "run": f"routine-{i}"} for i in range(7)]
    (tmp_path / "2026-07-25.jsonl").write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")

    first = client.get("/api/routines/logs?date=2026-07-25")
    second = client.get("/api/routines/logs?date=2026-07-25")

    assert first.status_code == second.status_code == 200
    assert len(first.json) == 7
    assert [entry["id"] for entry in first.json] == [entry["id"] for entry in second.json]
    assert len({entry["id"] for entry in first.json}) == 7


def test_routine_logs_skips_invalid_json_lines(client, tmp_path):
    (tmp_path / "2026-07-25.jsonl").write_text('{"run":"valid"}\nnot-json\n')

    response = client.get("/api/routines/logs?date=2026-07-25")

    assert response.status_code == 200
    assert response.json[0]["run"] == "valid"


@pytest.mark.parametrize("triggered_by", ["manual", "schedule"])
def test_runner_persists_triggered_by_in_jsonl(tmp_path, monkeypatch, triggered_by):
    import runner

    monkeypatch.setattr(runner, "LOGS_DIR", tmp_path)
    monkeypatch.setenv("EVONEXUS_TRIGGERED_BY", "schedule")
    runner._log_to_file("routine", "prompt", "output", "", 0, 1.0, triggered_by=triggered_by)

    [log_file] = list(tmp_path.glob("*.jsonl"))
    assert json.loads(log_file.read_text())["triggered_by"] == triggered_by


def test_scheduler_passes_schedule_origin_to_subprocess(monkeypatch):
    import scheduler

    captured = {}
    completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(scheduler.Path, "exists", lambda _: True)

    def run(*args, **kwargs):
        captured.update(kwargs)
        return completed

    monkeypatch.setattr(scheduler.subprocess, "run", run)
    scheduler.run_adw("Routine", "routine.py")

    assert captured["env"]["EVONEXUS_TRIGGERED_BY"] == "schedule"


def test_scheduler_accepts_manual_origin_without_changing_callers(monkeypatch):
    import scheduler

    captured = {}
    completed = type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(scheduler.Path, "exists", lambda _: True)

    def run(*args, **kwargs):
        captured.update(kwargs)
        return completed

    monkeypatch.setattr(scheduler.subprocess, "run", run)
    scheduler.run_adw("Routine", "routine.py", triggered_by="manual")

    assert captured["env"]["EVONEXUS_TRIGGERED_BY"] == "manual"



def test_scheduler_heartbeat_continues_during_long_job(monkeypatch):
    import scheduler

    writes = []
    first_write = threading.Event()
    release_job = threading.Event()
    monkeypatch.setattr(scheduler, "write_status", lambda: (writes.append(time.monotonic()), first_write.set()))

    stop_event, heartbeat_thread = scheduler._start_heartbeat(interval=0.01)
    assert first_write.wait(timeout=1)

    def long_job():
        release_job.wait(timeout=1)

    job_thread = threading.Thread(target=long_job)
    job_thread.start()
    time.sleep(0.05)
    release_job.set()
    job_thread.join(timeout=1)
    stop_event.set()
    heartbeat_thread.join(timeout=1)

    assert len(writes) >= 2
    assert not heartbeat_thread.is_alive()


def test_scheduler_heartbeat_write_failure_does_not_stop_loop(monkeypatch):
    import scheduler

    calls = []
    stop_event = threading.Event()

    def write_status():
        calls.append(None)
        if len(calls) == 1:
            raise OSError("read-only filesystem")
        stop_event.set()

    monkeypatch.setattr(scheduler, "write_status", write_status)
    scheduler._heartbeat_loop(stop_event, interval=0)

    assert len(calls) == 2
