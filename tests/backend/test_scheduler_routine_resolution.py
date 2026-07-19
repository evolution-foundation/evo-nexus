"""
tests/backend/test_scheduler_routine_resolution.py

Operação-24/7 audit fix (2026-07-17) — config/routines.yaml's loader always
requests scripts with a "custom/" prefix (_load_routines_from_yaml), but two
real, currently-enabled routines don't live there:
  - daily_status_report.py sits directly in ADWs/routines/ (not custom/)
  - publish_scheduled.py (the ONLY script that actually dispatches real posts
    to X via Postiz) sits at the repo's top-level scripts/
Both used to silently no-op every tick ("script not found: custom/<name>")
without raising anywhere a human would see it. run_adw() now tries three
candidate locations before giving up. This proves the fix without needing a
running scheduler process.

Run:
    cd /path/to/workspace && pytest tests/backend/test_scheduler_routine_resolution.py -v
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scheduler_module():
    spec = importlib.util.spec_from_file_location("scheduler_under_test", REPO_ROOT / "scheduler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_subprocess_run(**overrides):
    result = MagicMock()
    result.returncode = 0
    for k, v in overrides.items():
        setattr(result, k, v)
    return result


def test_finds_script_directly_in_routines_dir(scheduler_module):
    """AI News scripts genuinely live in ADWs/routines/custom/ — the
    common, already-working case must keep working unmodified."""
    with patch("subprocess.run", return_value=_mock_subprocess_run()) as mock_run:
        scheduler_module.run_adw("AI News Daily Draft", "custom/ai_news_daily_draft.py")
    cmd = mock_run.call_args[0][0]
    assert "ADWs/routines/custom/ai_news_daily_draft.py" in cmd


def test_falls_back_to_routines_dir_root_for_daily_status_report(scheduler_module):
    """daily_status_report.py is requested as 'custom/daily_status_report.py'
    (the loader always prepends custom/) but the real file is one level up —
    moving it would break its own ROOT-relative DB path resolution."""
    with patch("subprocess.run", return_value=_mock_subprocess_run()) as mock_run:
        scheduler_module.run_adw("Status Diário (WhatsApp)", "custom/daily_status_report.py")
    cmd = mock_run.call_args[0][0]
    assert "ADWs/routines/daily_status_report.py" in cmd
    assert "custom/daily_status_report.py" not in cmd


def test_falls_back_to_top_level_scripts_for_publish_scheduled(scheduler_module):
    """publish_scheduled.py — the routine that actually dispatches posts to X
    via Postiz — lives at the repo's top-level scripts/, not ADWs/routines/
    at all. This was the highest-impact silent no-op found in this audit."""
    with patch("subprocess.run", return_value=_mock_subprocess_run()) as mock_run:
        scheduler_module.run_adw("Publicar Posts Sociais (X)", "custom/publish_scheduled.py")
    cmd = mock_run.call_args[0][0]
    assert "scripts/publish_scheduled.py" in cmd


def test_prints_not_found_when_no_candidate_exists(scheduler_module, capsys):
    with patch("subprocess.run") as mock_run:
        scheduler_module.run_adw("Rotina Fantasma", "custom/does_not_exist_anywhere.py")
    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "script not found" in captured.out
