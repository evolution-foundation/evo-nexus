"""Routines endpoint — metrics, logs, and ADW scripts."""

import ast
import json
from datetime import date
from flask import Blueprint, jsonify, request
from routes._helpers import WORKSPACE, safe_read

bp = Blueprint("routines", __name__)

METRICS_PATH = WORKSPACE / "ADWs" / "logs" / "metrics.json"
LOGS_DIR = WORKSPACE / "ADWs" / "logs"
ROTINAS_DIR = WORKSPACE / "ADWs" / "routines"


def _metric_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_metrics_entry(entry: dict) -> dict:
    """Expose derived fields for legacy and current metrics schemas."""
    entry = dict(entry) if isinstance(entry, dict) else {}
    runs = max(0, int(_metric_number(entry.get("runs"))))
    successes = max(0, int(_metric_number(entry.get("successes"))))
    total_seconds = _metric_number(entry.get("total_duration", entry.get("total_seconds")))
    if not total_seconds and runs:
        total_seconds = _metric_number(entry.get("avg_seconds")) * runs
    entry.update({
        "runs": runs,
        "successes": successes,
        "total_seconds": total_seconds,
        "total_duration": total_seconds,
        "avg_seconds": total_seconds / runs if runs else 0,
        "success_rate": successes / runs * 100 if runs else 0,
        "total_cost_usd": _metric_number(entry.get("total_cost_usd")),
        "total_input_tokens": int(_metric_number(entry.get("total_input_tokens"))),
        "total_output_tokens": int(_metric_number(entry.get("total_output_tokens"))),
    })
    if not entry.get("agent") and entry.get("last_agent"):
        entry["agent"] = entry["last_agent"]
    return entry


@bp.route("/api/routines")
def get_routines():
    content = safe_read(METRICS_PATH)
    data: dict = {}
    if content:
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                data = {
                    name: normalize_metrics_entry(entry)
                    for name, entry in parsed.items()
                    if isinstance(entry, dict)
                }
        except json.JSONDecodeError:
            data = {}

    # Merge in routines that are declared (ADWs/ or plugins/) but haven't
    # run yet — so plugin rotinas, custom rotinas recém-adicionadas and
    # core scripts without history all appear in the UI with zeroed metrics.
    try:
        from routes._helpers import discover_routines
        discovered = discover_routines()
    except Exception:
        discovered = {}

    for make_id, spec in discovered.items():
        if make_id in data:
            # already has execution metrics; enrich with agent/name if missing
            entry = data[make_id]
            if isinstance(entry, dict):
                entry.setdefault("agent", spec.get("agent", ""))
                entry.setdefault("source_plugin", spec.get("source_plugin"))
            continue
        data[make_id] = {
            "agent": spec.get("agent", ""),
            "runs": 0,
            "successes": 0,
            "success_rate": 0,
            "avg_seconds": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "avg_cost_usd": 0.0,
            "last_run": None,
            "source_plugin": spec.get("source_plugin"),
        }

    # Calculate totals
    totals = {"total_runs": 0, "total_cost": 0.0, "total_tokens": 0}
    for key, val in data.items():
        if isinstance(val, dict):
            totals["total_runs"] += val.get("runs", 0)
            totals["total_cost"] += val.get("total_cost_usd", 0.0)
            totals["total_tokens"] += val.get("total_input_tokens", 0) + val.get("total_output_tokens", 0)

    return jsonify({"metrics": data, "totals": totals})


@bp.route("/api/routines/logs")
def get_routine_logs():
    target = request.args.get("date", date.today().isoformat())
    entries = []
    if LOGS_DIR.is_dir():
        for log_file in sorted(LOGS_DIR.glob(f"*{target}*.jsonl")):
            text = safe_read(log_file)
            if not text:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entry["id"] = f"routine-log-{log_file.name}-{line_number}"
                    entries.append(entry)
    return jsonify(entries)


@bp.route("/api/routines/adws")
def list_adws():
    if not ROTINAS_DIR.is_dir():
        return jsonify([])
    scripts = []
    for f in sorted(ROTINAS_DIR.iterdir()):
        if f.suffix == ".py" and f.is_file():
            doc = ""
            text = safe_read(f)
            if text:
                try:
                    tree = ast.parse(text)
                    raw = ast.get_docstring(tree)
                    if raw:
                        doc = raw.splitlines()[0]
                except SyntaxError:
                    pass
            scripts.append({"name": f.stem, "file": f.name, "description": doc})
    return jsonify(scripts)


# Pricing per model for image generation (USD)
# Format: { model_substring: { per_image: float, input_per_1m: float, output_per_1m: float } }
IMAGE_MODEL_PRICING = {
    "gemini-3.1-flash-image-preview": {"per_image": 0.039, "input_per_1m": 0.075, "output_per_1m": 0.30},
    "gemini-2.0-flash": {"per_image": 0.039, "input_per_1m": 0.10, "output_per_1m": 0.40},
    "flux-2": {"per_image": 0.03, "input_per_1m": 0, "output_per_1m": 0},
    "flux.2": {"per_image": 0.03, "input_per_1m": 0, "output_per_1m": 0},
    "gpt-5-image": {"per_image": 0.04, "input_per_1m": 0.005, "output_per_1m": 0.015},
    "seedream": {"per_image": 0.02, "input_per_1m": 0, "output_per_1m": 0},
    "riverflow": {"per_image": 0.02, "input_per_1m": 0, "output_per_1m": 0},
}


def _estimate_image_cost(entry: dict) -> float:
    """Estimate USD cost for a single image generation entry."""
    model = entry.get("model", "").lower()
    tokens = entry.get("token_usage", {})
    total_tokens = tokens.get("total_tokens", 0)

    # Find matching pricing by model substring
    pricing = None
    for key, p in IMAGE_MODEL_PRICING.items():
        if key in model:
            pricing = p
            break

    if not pricing:
        # Fallback: assume $0.03/image for unknown models
        return 0.03

    cost = pricing["per_image"]
    if total_tokens > 0 and pricing["input_per_1m"] > 0:
        cost += (total_tokens / 1_000_000) * pricing["input_per_1m"]
    return round(cost, 6)


@bp.route("/api/routines/image-costs")
def get_image_costs():
    """Return AI image generation cost entries with estimated costs."""
    costs_path = LOGS_DIR / "ai-image-creator-costs.json"
    content = safe_read(costs_path)
    if not content:
        return jsonify({"entries": [], "totals": {}})
    try:
        entries = json.loads(content)
    except json.JSONDecodeError:
        return jsonify({"entries": [], "totals": {}})

    total_tokens = 0
    total_seconds = 0.0
    total_bytes = 0
    total_cost = 0.0
    for e in entries:
        tokens = e.get("token_usage", {})
        total_tokens += tokens.get("total_tokens", 0)
        total_seconds += e.get("elapsed_seconds", 0)
        total_bytes += e.get("size_bytes", 0)
        est = _estimate_image_cost(e)
        e["estimated_cost_usd"] = est
        total_cost += est

    return jsonify({
        "entries": entries,
        "totals": {
            "count": len(entries),
            "total_tokens": total_tokens,
            "total_seconds": round(total_seconds, 1),
            "total_bytes": total_bytes,
            "total_cost_usd": round(total_cost, 4),
        },
    })
