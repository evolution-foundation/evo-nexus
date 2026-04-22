"""Config endpoint — CLAUDE.md, ROUTINES.md, ROADMAP.md, integration env vars, commands, Makefile."""

import os
import re
from flask import Blueprint, jsonify, request, Response, abort
from flask_login import login_required
from routes._helpers import WORKSPACE, safe_read

bp = Blueprint("config", __name__)


@bp.route("/api/config/workspace-status")
def workspace_status():
    """Check if workspace.yaml exists AND has owner configured."""
    config_path = WORKSPACE / "config" / "workspace.yaml"
    if not config_path.is_file():
        return jsonify({"configured": False})
    # File exists but check if owner is actually filled in
    try:
        content = config_path.read_text(encoding="utf-8")
        import yaml
        data = yaml.safe_load(content) or {}
        ws = data.get("workspace", data)
        owner = (ws.get("owner") or ws.get("owner_name") or "").strip()
        return jsonify({"configured": bool(owner)})
    except Exception:
        return jsonify({"configured": False})


@bp.route("/api/config/claude-md")
def get_claude_md():
    content = safe_read(WORKSPACE / "CLAUDE.md")
    if content is None:
        abort(404, description="CLAUDE.md not found")
    return Response(content, mimetype="text/markdown")


@bp.route("/api/config/routines")
def get_routines_md():
    # Try new name first, fallback to old
    content = safe_read(WORKSPACE / "ROUTINES.md")
    if content is None:
        abort(404, description="ROUTINES.md not found")
    return Response(content, mimetype="text/markdown")


# Legacy route alias
@bp.route("/api/config/routines")
def get_routines_legacy():
    return get_routines_md()


@bp.route("/api/config/roadmap")
def get_roadmap():
    content = safe_read(WORKSPACE / "ROADMAP.md")
    if content is None:
        abort(404, description="ROADMAP.md not found")
    return Response(content, mimetype="text/markdown")


@bp.route("/api/config/commands")
def list_commands():
    cmd_dir = WORKSPACE / ".claude" / "commands"
    if not cmd_dir.is_dir():
        return jsonify([])
    commands = []
    for f in sorted(cmd_dir.iterdir()):
        if f.suffix.lower() == ".md" and f.is_file():
            content = safe_read(f) or ""
            commands.append({
                "name": f.stem,
                "file": f.name,
                "content": content,
            })
    return jsonify(commands)


@bp.route("/api/config/makefile")
def parse_makefile():
    content = safe_read(WORKSPACE / "Makefile")
    if content is None:
        abort(404, description="Makefile not found")

    targets = []
    lines = content.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*):", line)
        if m:
            name = m.group(1)
            desc = ""
            if "##" in line:
                desc = line.split("##", 1)[1].strip()
            elif i > 0 and lines[i - 1].startswith("#"):
                desc = lines[i - 1].lstrip("# ").strip()
            targets.append({"name": name, "description": desc})

    return jsonify(targets)


# ── Integration env vars (scoped read/write — NOT a full .env editor) ──────

@bp.route("/api/config/env")
@login_required
def get_integration_env():
    """Read .env as structured key-value pairs (for integration drawer)."""
    env_path = WORKSPACE / ".env"
    content = safe_read(env_path)
    if content is None:
        return jsonify({"entries": [], "raw": ""})

    entries = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            entries.append({"type": "comment", "value": line})
        elif "=" in stripped:
            key, _, val = stripped.partition("=")
            entries.append({"type": "var", "key": key.strip(), "value": val.strip()})
        else:
            entries.append({"type": "comment", "value": line})

    return jsonify({"entries": entries, "raw": content})


@bp.route("/api/config/env", methods=["PUT"])
@login_required
def update_integration_env():
    """Update specific env vars (used by integration drawer).

    Accepts {entries: [...]} — merges into the existing .env file,
    only updating keys present in the payload.
    """
    from models import has_permission, audit
    from flask_login import current_user

    if not has_permission(current_user.role, "config", "manage"):
        abort(403)

    data = request.get_json()
    env_path = WORKSPACE / ".env"

    if "entries" not in data:
        abort(400, description="Missing entries")

    # Read current .env
    current_content = safe_read(env_path) or ""
    current_lines = current_content.splitlines()

    # Build a map of incoming changes
    changes = {}
    for entry in data["entries"]:
        if entry.get("type") == "var" and entry.get("key"):
            changes[entry["key"]] = entry.get("value", "")

    # Update existing lines or track which keys were updated
    updated_keys = set()
    new_lines = []
    for line in current_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in changes:
                new_lines.append(f"{key}={changes[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # Append any new keys not found in existing file
    for key, val in changes.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Reload dotenv in current process
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)
    except Exception:
        pass

    # Also set in os.environ for immediate effect
    for key, val in changes.items():
        os.environ[key] = val

    audit(current_user, "integration_env_updated", "config",
          f"Updated env vars: {', '.join(changes.keys())}")
    return jsonify({"status": "saved"})
