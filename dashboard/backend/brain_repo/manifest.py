"""Brain Repo — manifest.yaml management."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = "1.0"

_GITIGNORE_CONTENT = """\
*.env
*.key
providers.json
*.db
__pycache__/
*.pyc
.DS_Store
"""

_README_TEMPLATE = """\
# EvoNexus Brain Repo

This repository is managed automatically by [EvoNexus](https://evonexus.ai).
It stores versioned snapshots of your workspace memory, customizations, and
configuration so you can restore or audit any point in time.

## Structure

| Path | Description |
|---|---|
| `memory/` | Context, projects, sessions, and raw transcripts |
| `workspace/` | General workspace files |
| `kb-mirror/` | Knowledge base mirror (read-only markdown) |
| `customizations/` | Agents, skills, commands, and routines |
| `config-safe/` | Non-sensitive configuration |

## Usage

Managed automatically via EvoNexus dashboard.
Do **not** store secrets in this repository.
"""


def read_manifest(repo_dir: Path) -> dict:
    """Read manifest.yaml from repo_dir. Returns {} if not found."""
    manifest_path = repo_dir / "manifest.yaml"
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("Could not read manifest.yaml: %s", exc)
        return {}


def write_manifest(repo_dir: Path, data: dict) -> None:
    """Write data to manifest.yaml in repo_dir."""
    manifest_path = repo_dir / "manifest.yaml"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)


def validate_schema(manifest: dict) -> tuple[bool, bool]:
    """Validate manifest schema.

    Returns:
        (schema_ok, migration_needed)
        schema_ok=True  → manifest has the expected fields
        migration_needed=True → schema_version < MANIFEST_SCHEMA_VERSION
    """
    if not manifest:
        return False, False

    required_fields = {"schema_version", "evonexus_version", "workspace_name"}
    schema_ok = required_fields.issubset(manifest.keys())

    version_str = manifest.get("schema_version", "0")
    try:
        current = tuple(int(x) for x in str(version_str).split("."))
        target = tuple(int(x) for x in MANIFEST_SCHEMA_VERSION.split("."))
        migration_needed = current < target
    except (ValueError, TypeError):
        migration_needed = True

    return schema_ok, migration_needed


def initialize_brain_repo(repo_dir: Path, config: dict) -> None:
    """Create the standard directory structure and initial files in repo_dir.

    config keys (all optional with defaults):
        evonexus_version, workspace_name, owner_username, github_username
    """
    repo_dir.mkdir(parents=True, exist_ok=True)

    # --- directories ---
    dirs = [
        "memory/context",
        "memory/projects",
        "memory/sessions",
        "memory/raw-transcripts",
        "workspace",
        "kb-mirror/connections",
        "kb-mirror/chunks",
        "customizations/agents",
        "customizations/skills",
        "customizations/commands",
        "customizations/routines",
        "config-safe",
    ]
    for d in dirs:
        (repo_dir / d).mkdir(parents=True, exist_ok=True)
        # Keep empty dirs in git
        gitkeep = repo_dir / d / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()

    now_iso = datetime.now(timezone.utc).isoformat()

    # --- manifest.yaml ---
    manifest_data: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "evonexus_version": config.get("evonexus_version", "unknown"),
        "workspace_name": config.get("workspace_name", ""),
        "owner_username": config.get("owner_username", ""),
        "github_username": config.get("github_username", ""),
        "created_at": now_iso,
        "last_sync": None,
        "folders": [],
        "stats": {
            "sessions": 0,
            "decisions": 0,
            "projects": 0,
            "custom_agents": 0,
        },
    }
    write_manifest(repo_dir, manifest_data)

    # --- .evo-brain ---
    evo_brain_path = repo_dir / ".evo-brain"
    if not evo_brain_path.exists():
        with open(evo_brain_path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "1.0", "created": now_iso}, fh, indent=2)

    # --- README.md ---
    readme_path = repo_dir / "README.md"
    if not readme_path.exists():
        with open(readme_path, "w", encoding="utf-8") as fh:
            fh.write(_README_TEMPLATE)

    # --- .gitignore ---
    gitignore_path = repo_dir / ".gitignore"
    if not gitignore_path.exists():
        with open(gitignore_path, "w", encoding="utf-8") as fh:
            fh.write(_GITIGNORE_CONTENT)

    log.info("Brain repo initialized at %s", repo_dir)
