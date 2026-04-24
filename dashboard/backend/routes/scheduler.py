"""Scheduler endpoint — parse scheduler.py to extract schedule entries."""

import re
from flask import Blueprint, jsonify
from routes._helpers import WORKSPACE, safe_read, get_script_agents, discover_routines

bp = Blueprint("scheduler", __name__)


def _script_to_command_map() -> dict:
    """script path → `make run R=<id>` command."""
    registry = discover_routines()
    return {r["script"]: f"make run R={make_id}" for make_id, r in registry.items()}


def _command_for(script: str) -> str:
    if not script:
        return ""
    mapping = _script_to_command_map()
    # Try exact match first
    if script in mapping:
        return mapping[script]
    # Try stripping 'custom/' prefix and re-matching
    bare = script.replace("custom/", "")
    if bare in mapping:
        return mapping[bare]
    # Try with 'custom/' prefix
    with_prefix = f"custom/{bare}"
    if with_prefix in mapping:
        return mapping[with_prefix]
    return ""


@bp.route("/api/scheduler")
def get_schedule():
    content = safe_read(WORKSPACE / "scheduler.py")
    if not content:
        return jsonify([])

    entries = []

    pattern = re.compile(
        r'schedule\.every\(([^)]*)\)\.'
        r'([\w.]+)'
        r'(?:\.at\(["\']([^"\']+)["\']\))?'
        r'\.do\(\s*(\w+)'
        r'(?:\s*,\s*["\']([^"\']*)["\'])?'
        r'(?:\s*,\s*["\']([^"\']*)["\'])?'
    )

    for m in pattern.finditer(content):
        interval, freq_chain, time_str, func_name, name_arg, script_arg = m.groups()

        # Determine frequency
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        day_match = [d for d in days if d in freq_chain.lower()]

        if day_match:
            frequency = "weekly"
            day_label = day_match[0].capitalize()
        elif "minute" in freq_chain:
            frequency = f"every {interval or '1'} min"
            day_label = ""
        elif "hour" in freq_chain:
            frequency = f"every {interval or '1'} hour"
            day_label = ""
        else:
            frequency = "daily"
            day_label = ""

        schedule_str = f"{frequency}"
        if day_label:
            schedule_str = f"{day_label}"
        if time_str:
            schedule_str += f" @ {time_str}"

        # Get real name from first arg, or derive from script
        task_name = name_arg or ""
        script = script_arg or ""

        if not task_name and script:
            task_name = script.replace(".py", "").replace("_", " ").title()

        # Get agent from script name (strip custom/ prefix and .py)
        script_key = script.replace("custom/", "").replace(".py", "") if script else ""
        agent = get_script_agents().get(script_key, "")

        entries.append({
            "name": task_name,
            "script": script,
            "schedule": schedule_str,
            "frequency": frequency,
            "time": time_str or "",
            "agent": agent,
            "custom": "custom/" in script,
            "command": _command_for(script),
        })

    # Also load custom routines from config/routines.yaml
    _load_yaml_routines(entries)

    return jsonify(entries)


def _load_yaml_routines(entries: list):
    """Load custom routines from config/routines.yaml."""
    import yaml
    config_path = WORKSPACE / "config" / "routines.yaml"
    if not config_path.exists():
        return

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if not config:
            return

        for r in config.get("daily", []) or []:
            if not r.get("enabled", True):
                continue
            script = r.get("script", "")
            script_key = script.replace(".py", "")
            agent = get_script_agents().get(script_key, "")
            if r.get("interval"):
                sched = f"every {r['interval']} min"
            else:
                sched = f"daily @ {r.get('time', '?')}"
            entries.append({
                "name": r.get("name", script),
                "script": f"custom/{script}",
                "schedule": sched,
                "frequency": "daily",
                "time": r.get("time", ""),
                "agent": agent,
                "custom": True,
                "command": _command_for(f"custom/{script}"),
            })

        for r in config.get("weekly", []) or []:
            if not r.get("enabled", True):
                continue
            script = r.get("script", "")
            script_key = script.replace(".py", "")
            agent = get_script_agents().get(script_key, "")
            days = r.get("days", [r.get("day", "friday")])
            time_str = r.get("time", "09:00")
            for d in days:
                entries.append({
                    "name": r.get("name", script),
                    "script": f"custom/{script}",
                    "schedule": f"{d.capitalize()} @ {time_str}",
                    "frequency": "weekly",
                    "time": time_str,
                    "agent": agent,
                    "custom": True,
                    "command": _command_for(f"custom/{script}"),
                })

        for r in config.get("monthly", []) or []:
            if not r.get("enabled", True):
                continue
            script = r.get("script", "")
            script_key = script.replace(".py", "")
            agent = get_script_agents().get(script_key, "")
            entries.append({
                "name": r.get("name", script),
                "script": f"custom/{script}",
                "schedule": f"Day {r.get('day', 1)} @ {r.get('time', '08:00')}",
                "frequency": "monthly",
                "time": r.get("time", ""),
                "agent": agent,
                "custom": True,
                "command": _command_for(f"custom/{script}"),
            })

    except Exception as e:
        print(f"Warning: Failed to load routines.yaml: {e}")
