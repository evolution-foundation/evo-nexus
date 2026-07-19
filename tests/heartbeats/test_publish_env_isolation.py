"""Postiz credentials must never reach agent subprocesses."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def test_postiz_api_key_is_removed_from_agent_env(monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", "secret-postiz-key")
    monkeypatch.setenv("POSTIZ_URL", "https://postiz.example.com")
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "allowed-dashboard-token")

    import provider_fallback
    importlib.reload(provider_fallback)
    env = provider_fallback._build_agent_run_env()

    assert "POSTIZ_API_KEY" not in env
    assert env["POSTIZ_URL"] == "https://postiz.example.com"
    assert env["DASHBOARD_API_TOKEN"] == "allowed-dashboard-token"
