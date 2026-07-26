from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADWS_DIR = ROOT / "ADWs"
if str(ADWS_DIR) not in sys.path:
    sys.path.insert(0, str(ADWS_DIR))


@pytest.fixture
def runner(monkeypatch, tmp_path):
    import runner as runner_module

    runner_module = importlib.reload(runner_module)
    monkeypatch.setattr(runner_module, "LOGS_DIR", tmp_path)
    return runner_module


@pytest.fixture
def legacy_metrics(tmp_path):
    payload = {
        "morning": {
            "runs": 2,
            "successes": 1,
            "failures": 1,
            "total_seconds": 30,
            "avg_seconds": 15,
            "success_rate": 50,
            "agent": "clawdia",
            "total_cost_usd": 1.5,
        }
    }
    (tmp_path / "metrics.json").write_text(json.dumps(payload))
    return payload


def test_save_metrics_migrates_legacy_schema_and_derives_aggregates(runner, legacy_metrics):
    runner._save_metrics("morning", 10, 0, "clawdia", "done")

    saved = json.loads((runner.LOGS_DIR / "metrics.json").read_text())["morning"]
    assert saved["runs"] == 3
    assert saved["successes"] == 2
    assert saved["failures"] == 1
    assert saved["total_seconds"] == 40
    assert saved["total_duration"] == 40
    assert saved["avg_seconds"] == pytest.approx(40 / 3)
    assert saved["success_rate"] == pytest.approx(200 / 3)
    assert saved["agent"] == "clawdia"
    assert saved["last_agent"] == "clawdia"


def test_normalizing_legacy_metrics_is_idempotent(runner, legacy_metrics):
    entry = legacy_metrics["morning"]

    first = runner._normalize_metric_entry(entry)
    second = runner._normalize_metric_entry(first)

    assert second == first
    assert second["total_seconds"] == 30
    assert second["total_duration"] == 30
    assert second["avg_seconds"] == 15
    assert second["success_rate"] == 50


def test_run_script_keeps_success_when_metrics_telemetry_fails(runner, monkeypatch):
    telemetry_calls = []
    monkeypatch.setattr(runner, "_log_to_file", lambda *args, **kwargs: telemetry_calls.append(args[4]))
    monkeypatch.setattr(runner, "_save_metrics", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    result = runner.run_script(lambda: {"ok": True, "summary": "completed"}, "manual-run")

    assert result["success"] is True
    assert result["returncode"] == 0
    assert telemetry_calls == [0]


def test_run_script_keeps_primary_failure_when_metrics_telemetry_fails(runner, monkeypatch):
    log_calls = []
    monkeypatch.setattr(runner, "_log_to_file", lambda *args, **kwargs: log_calls.append(args[4]))
    monkeypatch.setattr(runner, "_save_metrics", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    result = runner.run_script(lambda: (_ for _ in ()).throw(RuntimeError("primary failure")), "manual-run")

    assert result == {
        "success": False,
        "stdout": "",
        "stderr": "primary failure",
        "returncode": -3,
        "duration": pytest.approx(result["duration"]),
    }
    assert log_calls == [-3]
