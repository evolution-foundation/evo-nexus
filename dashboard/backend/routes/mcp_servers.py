"""MCP Servers listing — Wave 2.3 UI.

Read-only view of every MCP server registered in ~/.claude.json under the
current workspace, cross-referenced with `plugins_installed` so the UI knows
which entries were injected by an EvoNexus plugin and which are native /
user-maintained.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify
from flask_login import login_required

from plugin_claude_config import CLAUDE_JSON, WORKSPACE

bp = Blueprint("mcp_servers", __name__)

DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"


def _plugin_mcp_ownership() -> dict[str, str]:
    """Return {effective_mcp_name: plugin_slug} for every installed plugin
    that tracks MCPs in its manifest_json.
    """
    ownership: dict[str, str] = {}
    if not DB_PATH.exists():
        return ownership
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT slug, manifest_json FROM plugins_installed WHERE enabled = 1"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return ownership
    for row in rows:
        try:
            manifest = json.loads(row["manifest_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        installed = manifest.get("mcp_servers_installed") or []
        for entry in installed:
            name = entry.get("effective_name") if isinstance(entry, dict) else None
            if name:
                ownership[name] = row["slug"]
    return ownership


@bp.route("/api/mcp-servers", methods=["GET"])
@login_required
def list_mcp_servers():
    """List all MCP servers in ~/.claude.json for the current workspace.

    Response shape:
        {
            "workspace": "/abs/path",
            "claude_json_exists": bool,
            "servers": [
                {
                    "name": "plugin-pm-essentials-filesystem-pm",
                    "command": "npx",
                    "args": [...],
                    "env": {...},
                    "source": "plugin" | "native",
                    "source_plugin": "pm-essentials" | null
                }
            ]
        }
    """
    ws = WORKSPACE.resolve()
    if not CLAUDE_JSON.exists():
        return jsonify({
            "workspace": str(ws),
            "claude_json_exists": False,
            "servers": [],
        })

    try:
        data = json.loads(CLAUDE_JSON.read_bytes())
    except (json.JSONDecodeError, OSError) as exc:
        return jsonify({
            "workspace": str(ws),
            "claude_json_exists": True,
            "error": f"Could not parse ~/.claude.json: {exc}",
            "servers": [],
        }), 500

    projects = data.get("projects") or {}
    ws_abs = str(ws)
    project_entry = projects.get(ws_abs) or {}
    mcp_servers = project_entry.get("mcpServers") or {}

    ownership = _plugin_mcp_ownership()

    servers: list[dict[str, Any]] = []
    for name, cfg in mcp_servers.items():
        if not isinstance(cfg, dict):
            continue
        source_plugin = ownership.get(name)
        servers.append({
            "name": name,
            "command": cfg.get("command"),
            "args": cfg.get("args") or [],
            "env": cfg.get("env") or {},
            "source": "plugin" if source_plugin else "native",
            "source_plugin": source_plugin,
        })

    # Stable sort: plugin MCPs first (grouped by plugin slug), then native
    servers.sort(key=lambda s: (
        0 if s["source"] == "plugin" else 1,
        s["source_plugin"] or "",
        s["name"],
    ))

    return jsonify({
        "workspace": ws_abs,
        "claude_json_exists": True,
        "claude_json_path": str(CLAUDE_JSON),
        "servers": servers,
    })
