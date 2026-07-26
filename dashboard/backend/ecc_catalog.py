"""Self-contained allowlisted ECC workflow catalog."""

from __future__ import annotations

import hashlib
import json

TASK_WORKFLOWS = {
    "bug": "orch-fix-defect",
    "feature": "orch-add-feature",
    "refactor": "orch-refine-code",
    "review": "orch-review",
    "security": "orch-review",
    "research": "orch-review",
}

WORKFLOW_CATALOG = {
    "orch-fix-defect": {"version": 1, "risk": "standard"},
    "orch-add-feature": {"version": 1, "risk": "standard"},
    "orch-refine-code": {"version": 1, "risk": "standard"},
    "orch-review": {"version": 1, "risk": "standard"},
}


def resolve_workflow(task_type: str, override: str | None = None) -> dict:
    """Resolve an allowlisted workflow without relying on host-only files."""
    slug = override or TASK_WORKFLOWS.get(task_type)
    if not slug:
        raise ValueError("Workflow is not allowlisted")
    definition = WORKFLOW_CATALOG.get(slug)
    if definition is None:
        raise ValueError("Workflow is not allowlisted")

    risk = "high" if task_type == "security" else definition["risk"]
    canonical = json.dumps(
        {"slug": slug, "version": definition["version"], "risk": risk},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "slug": slug,
        "version": definition["version"],
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "risk": risk,
    }
