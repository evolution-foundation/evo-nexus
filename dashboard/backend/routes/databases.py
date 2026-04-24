"""Databases endpoint — CRUD DB_<FLAVOR>_N_* blocks in .env.

Same pattern as social accounts: UI sends form data, backend writes to .env.
No separate credential store. .env stays the source of truth.
"""

import json
import re
import subprocess
from pathlib import Path

from flask import Blueprint, jsonify, request

from routes._helpers import WORKSPACE

bp = Blueprint("databases", __name__)


ENV_PATH = WORKSPACE / ".env"

_SUPPORTED_FLAVORS = {
    "postgres": {
        "skill": "db-postgres",
        "fields": [
            "LABEL",
            "HOST",
            "PORT",
            "DATABASE",
            "USER",
            "PASSWORD",
            "SSL_MODE",
            "SSL_CA_PATH",
            "DSN",
            "ALLOW_WRITE",
            "QUERY_TIMEOUT",
            "MAX_ROWS",
        ],
    },
    "mysql": {
        "skill": "db-mysql",
        "fields": [
            "LABEL",
            "HOST",
            "PORT",
            "DATABASE",
            "USER",
            "PASSWORD",
            "SSL_CA_PATH",
            "DSN",
            "ALLOW_WRITE",
            "QUERY_TIMEOUT",
            "MAX_ROWS",
        ],
    },
    "mongo": {
        "skill": "db-mongo",
        "fields": [
            "LABEL",
            "URI",
            "HOST",
            "PORT",
            "DATABASE",
            "USER",
            "PASSWORD",
            "AUTH_SOURCE",
            "TLS",
            "ALLOW_WRITE",
            "QUERY_TIMEOUT",
            "MAX_ROWS",
        ],
    },
    "redis": {
        "skill": "db-redis",
        "fields": [
            "LABEL",
            "URL",
            "HOST",
            "PORT",
            "DB",
            "USERNAME",
            "PASSWORD",
            "TLS",
            "ALLOW_WRITE",
            "QUERY_TIMEOUT",
            "MAX_ROWS",
        ],
    },
}


# ── .env low-level helpers (mirrors social-auth/env_manager.py) ──

def _read_env_raw() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, _, v = stripped.partition("=")
            out[k.strip()] = v.strip()
    return out


def _set_env(key: str, value: str) -> None:
    """Set or update a key in .env — preserves surrounding lines/comments."""
    lines: list[str] = []
    found = False
    if ENV_PATH.exists():
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k == key:
                        lines.append(f"{key}={value}\n")
                        found = True
                        continue
                lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _delete_env(key: str) -> None:
    if not ENV_PATH.exists():
        return
    lines: list[str] = []
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k == key:
                    continue
            lines.append(line if line.endswith("\n") else line + "\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ── Multi-connection helpers ──

def _prefix(flavor: str, index: int) -> str:
    return f"DB_{flavor.upper()}_{index}"


def _next_index(flavor: str) -> int:
    env = _read_env_raw()
    pattern = re.compile(rf"^DB_{flavor.upper()}_(\d+)_")
    indices = {int(m.group(1)) for k in env if (m := pattern.match(k))}
    return (max(indices) + 1) if indices else 1


def _save_connection(flavor: str, index: int, data: dict) -> None:
    """Write all DB_<FLAVOR>_<N>_<FIELD> lines to .env. Empty values delete."""
    prefix = _prefix(flavor, index)
    allowed = set(_SUPPORTED_FLAVORS[flavor]["fields"])
    for field in allowed:
        value = data.get(field.lower())
        key = f"{prefix}_{field}"
        if value is None or value == "":
            _delete_env(key)
        else:
            _set_env(key, str(value))


def _delete_connection(flavor: str, index: int) -> None:
    prefix = _prefix(flavor, index)
    env = _read_env_raw()
    for key in list(env.keys()):
        if key.startswith(f"{prefix}_"):
            _delete_env(key)


# ── List (via skill script — excludes PASSWORD from payload) ──

def _run_accounts(skill_slug: str) -> dict:
    script = WORKSPACE / ".claude" / "skills" / skill_slug / "scripts" / "db_client.py"
    if not script.is_file():
        return {"ok": False, "error": f"Script not found: {script.name}", "connections": []}
    try:
        proc = subprocess.run(
            ["python3", str(script), "accounts"],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "connections": []}
    except FileNotFoundError:
        return {"ok": False, "error": "python3 not found", "connections": []}
    payload = (proc.stdout or proc.stderr or "").strip()
    if not payload:
        return {"ok": False, "error": f"empty output (rc={proc.returncode})", "connections": []}
    try:
        return json.loads(payload.splitlines()[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": f"invalid JSON: {payload[:200]}", "connections": []}


def _list_all_flavors() -> dict:
    flavors = []
    total = 0
    for slug, cfg in _SUPPORTED_FLAVORS.items():
        data = _run_accounts(cfg["skill"])
        entry = {
            "slug": slug,
            "skill": cfg["skill"],
            "ok": bool(data.get("ok", False)),
            "count": int(data.get("count", 0) or 0),
            "connections": data.get("connections") or [],
        }
        if not entry["ok"]:
            entry["error"] = data.get("error") or "unknown error"
        flavors.append(entry)
        total += entry["count"]
    return {"flavors": flavors, "total": total}


# ── Routes ──

@bp.route("/api/integrations/databases")
def list_databases():
    """Return all DB connections parsed from .env, grouped by flavor.

    Passwords are never included — the skill's `accounts` command strips them.
    """
    return jsonify(_list_all_flavors())


@bp.route("/api/integrations/databases/<flavor>", methods=["POST"])
def create_database(flavor: str):
    """Create a new DB connection block in .env — returns refreshed list."""
    if flavor not in _SUPPORTED_FLAVORS:
        return jsonify({"ok": False, "error": f"Unsupported flavor: {flavor}"}), 400
    data = request.get_json(silent=True) or {}
    if not (data.get("label") or "").strip():
        return jsonify({"ok": False, "error": "label is required"}), 400

    index = _next_index(flavor)
    _save_connection(flavor, index, data)
    refreshed = _list_all_flavors()
    return jsonify({"ok": True, "index": index, **refreshed})


@bp.route("/api/integrations/databases/<flavor>/<int:index>", methods=["PUT"])
def update_database(flavor: str, index: int):
    """Update an existing DB connection.

    Password semantics match the typical UX:
      - If `password` is absent or `null` in the payload → keep the current value.
      - If `password` is an empty string → explicitly clear it.
      - If `password` is the sentinel "__KEEP__" → keep the current value.
    """
    if flavor not in _SUPPORTED_FLAVORS:
        return jsonify({"ok": False, "error": f"Unsupported flavor: {flavor}"}), 400
    data = request.get_json(silent=True) or {}
    if not (data.get("label") or "").strip():
        return jsonify({"ok": False, "error": "label is required"}), 400

    # Preserve current password if the caller signals "keep"
    pwd_key = f"{_prefix(flavor, index)}_PASSWORD"
    incoming_pwd = data.get("password")
    if incoming_pwd is None or incoming_pwd == "__KEEP__":
        current_env = _read_env_raw()
        data["password"] = current_env.get(pwd_key, "")

    _save_connection(flavor, index, data)
    refreshed = _list_all_flavors()
    return jsonify({"ok": True, **refreshed})


@bp.route("/api/integrations/databases/<flavor>/<int:index>", methods=["DELETE"])
def delete_database(flavor: str, index: int):
    if flavor not in _SUPPORTED_FLAVORS:
        return jsonify({"ok": False, "error": f"Unsupported flavor: {flavor}"}), 400
    _delete_connection(flavor, index)
    refreshed = _list_all_flavors()
    return jsonify({"ok": True, **refreshed})
