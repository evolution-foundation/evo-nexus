"""Fase 7 — Policy resolver: qual runtime primário usar por workflow.

Módulo puro, sem I/O de rede. Runtime é escolhido por workflow (não por troca
global instantânea), com cohort determinístico, modo shadow/canary/default e
kill switch de emergência.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
POLICY_CONFIG = WORKSPACE / "config" / "runtime_policy.json"
POLICY_EXAMPLE = WORKSPACE / "config" / "runtime_policy.example.json"
KILL_SWITCH_ENV = "HERMES_KILL_SWITCH"


def load_policy() -> dict:
    try:
        if not POLICY_CONFIG.is_file() and POLICY_EXAMPLE.is_file():
            shutil.copy2(POLICY_EXAMPLE, POLICY_CONFIG)
        if POLICY_CONFIG.is_file():
            data = json.loads(POLICY_CONFIG.read_text(encoding="utf-8"))
            data.setdefault("workflows", {})
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"workflows": {}}


def _cohort_bucket(cohort_key: str) -> int:
    digest = hashlib.sha256(cohort_key.encode()).hexdigest()
    return int(digest[:8], 16) % 100


def resolve_runtime(workflow_slug: str, cohort_key: str, policy: dict | None = None) -> dict:
    """Return {"primary", "mode", "reason"} for a workflow execution.

    Precedence: kill switch env var > per-workflow config > safe default (claude/off).
    """
    if os.environ.get(KILL_SWITCH_ENV, "").strip() == "1":
        return {"primary": "claude", "mode": "off", "reason": "kill_switch"}

    policy = policy or load_policy()
    entry = policy.get("workflows", {}).get(workflow_slug)
    if not entry:
        return {"primary": "claude", "mode": "off", "reason": "unmigrated_workflow"}

    mode = entry.get("mode", "off")
    if mode == "off":
        return {"primary": "claude", "mode": "off", "reason": "workflow_disabled"}
    if mode == "shadow":
        return {"primary": "claude", "mode": "shadow", "reason": "shadow_no_side_effects"}
    if mode in ("canary", "default"):
        cohort_pct = int(entry.get("cohort_percent", 0))
        bucket = _cohort_bucket(f"{workflow_slug}:{cohort_key}")
        if bucket < cohort_pct:
            return {"primary": "hermes", "mode": mode, "reason": f"cohort:{bucket}<{cohort_pct}"}
        return {"primary": entry.get("fallback", "claude"), "mode": mode, "reason": f"cohort:{bucket}>={cohort_pct}"}
    return {"primary": "claude", "mode": "off", "reason": "unknown_mode"}
