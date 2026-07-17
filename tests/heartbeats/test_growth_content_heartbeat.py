"""
tests/heartbeats/test_growth_content_heartbeat.py

growth-content-heartbeat quick-spec (2026-07-17) — pixel-social-media gains an
interval heartbeat that tops up a Goal's content-ticket queue when it runs
low, instead of only reacting once per goal_created/project_created event.

Proves:
  - config/heartbeats.example.yaml still validates as a whole against the
    pydantic schema (the new pixel-growth-6h entry included).
  - pixel-growth-6h is shaped correctly (agent, interval, wake_triggers).
  - pixel-social-media is a STATE_MONITOR_AGENTS member — without this the
    cost-guard would skip the heartbeat exactly when it's needed (empty
    ticket inbox = the queue running low that this heartbeat exists to fix).

Run:
    cd /path/to/workspace && pytest tests/heartbeats/test_growth_content_heartbeat.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from heartbeat_schema import HeartbeatConfig  # noqa: E402
from heartbeat_runner import STATE_MONITOR_AGENTS  # noqa: E402

EXAMPLE_YAML = REPO_ROOT / "config" / "heartbeats.example.yaml"


def _load_example_heartbeats():
    data = yaml.safe_load(EXAMPLE_YAML.read_text())
    return [HeartbeatConfig(**hb) for hb in data["heartbeats"]]


def test_example_yaml_validates_with_new_seed():
    heartbeats = _load_example_heartbeats()
    ids = [h.id for h in heartbeats]
    assert "pixel-growth-6h" in ids
    assert len(heartbeats) >= 8


def test_pixel_growth_heartbeat_shape():
    heartbeats = _load_example_heartbeats()
    hb = next(h for h in heartbeats if h.id == "pixel-growth-6h")
    assert hb.agent == "pixel-social-media"
    assert hb.interval_seconds == 21600
    assert "interval" in hb.wake_triggers
    assert hb.enabled is False  # seeds ship dormant


def test_pixel_social_media_is_state_monitor_agent():
    assert "pixel-social-media" in STATE_MONITOR_AGENTS


def test_cost_guard_predicate_does_not_skip_pixel_with_empty_inbox():
    """Mirrors the exact guard condition in heartbeat_runner.py's main loop
    (`not inbox and not approvals and hb["agent"] not in STATE_MONITOR_AGENTS`)
    without spinning up the full runner — proves pixel-social-media would NOT
    be skipped even with a fully empty ticket inbox and no approvals."""
    inbox = []
    approvals = []
    would_skip = not inbox and not approvals and "pixel-social-media" not in STATE_MONITOR_AGENTS
    assert would_skip is False


def test_cost_guard_predicate_still_skips_a_control_agent():
    inbox = []
    approvals = []
    would_skip = not inbox and not approvals and "zara-cs" not in STATE_MONITOR_AGENTS
    assert would_skip is True
