"""Heartbeats API — CRUD + manual trigger + run history."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from flask_login import current_user
from models import db, Heartbeat, HeartbeatRun, HeartbeatTriggerEvent, has_permission, audit

bp = Blueprint("heartbeats", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent


def _require(action: str):
    if not has_permission(current_user.role, "heartbeats", action):
        return jsonify({"error": "Forbidden"}), 403
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _load_yaml_config():
    """Load heartbeats from YAML source of truth."""
    import sys
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from heartbeat_schema import load_heartbeats_yaml
    return load_heartbeats_yaml()


def _save_yaml_config(data):
    """Atomically save heartbeats to YAML."""
    import sys
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from heartbeat_schema import save_heartbeats_yaml
    save_heartbeats_yaml(data)


def _mirror_to_db(hb_config):
    """Mirror a single HeartbeatConfig to the DB."""
    now = _now_iso()
    existing = Heartbeat.query.get(hb_config.id)
    if existing:
        existing.agent = hb_config.agent
        existing.interval_seconds = hb_config.interval_seconds
        existing.max_turns = hb_config.max_turns
        existing.timeout_seconds = hb_config.timeout_seconds
        existing.lock_timeout_seconds = hb_config.lock_timeout_seconds
        existing.wake_triggers_list = hb_config.wake_triggers
        existing.goal_id = hb_config.goal_id
        existing.required_secrets_list = hb_config.required_secrets
        existing.decision_prompt = hb_config.decision_prompt
        # Note: enabled is NOT updated from YAML on edits — UI toggle is source of truth for enabled
    else:
        new_hb = Heartbeat(
            id=hb_config.id,
            agent=hb_config.agent,
            interval_seconds=hb_config.interval_seconds,
            max_turns=hb_config.max_turns,
            timeout_seconds=hb_config.timeout_seconds,
            lock_timeout_seconds=hb_config.lock_timeout_seconds,
            enabled=hb_config.enabled,
            goal_id=hb_config.goal_id,
            decision_prompt=hb_config.decision_prompt,
        )
        new_hb.wake_triggers_list = hb_config.wake_triggers
        new_hb.required_secrets_list = hb_config.required_secrets
        db.session.add(new_hb)
    db.session.commit()


def _cost_7d(heartbeat_id: str) -> float:
    """Sum cost_usd for runs in last 7 days."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    runs = HeartbeatRun.query.filter(
        HeartbeatRun.heartbeat_id == heartbeat_id,
        HeartbeatRun.started_at >= cutoff,
    ).all()
    return round(sum(r.cost_usd or 0 for r in runs), 6)


# ── List ─────────────────────────────────────────────────────────────────────

@bp.route("/api/heartbeats")
def list_heartbeats():
    denied = _require("view")
    if denied:
        return denied

    query = Heartbeat.query
    # Wave 1.1: optional ?source_plugin=<slug> filter (ADR Step 4)
    source_plugin = request.args.get("source_plugin")
    if source_plugin:
        query = query.filter_by(source_plugin=source_plugin)

    heartbeats = query.all()
    result = []
    for hb in heartbeats:
        d = hb.to_dict()
        d["cost_7d"] = _cost_7d(hb.id)
        result.append(d)

    return jsonify({
        "heartbeats": result,
        "total": len(result),
    })


# ── Get single ───────────────────────────────────────────────────────────────

@bp.route("/api/heartbeats/<string:heartbeat_id>")
def get_heartbeat(heartbeat_id):
    denied = _require("view")
    if denied:
        return denied

    hb = Heartbeat.query.get_or_404(heartbeat_id)
    d = hb.to_dict()
    d["cost_7d"] = _cost_7d(hb.id)

    # Last 10 runs
    runs = HeartbeatRun.query.filter_by(heartbeat_id=heartbeat_id).order_by(
        HeartbeatRun.started_at.desc()
    ).limit(10).all()
    d["recent_runs"] = [r.to_dict() for r in runs]

    return jsonify(d)


# ── Create ───────────────────────────────────────────────────────────────────

@bp.route("/api/heartbeats", methods=["POST"])
def create_heartbeat():
    denied = _require("manage")
    if denied:
        return denied

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Validate via pydantic
    import sys
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from heartbeat_schema import HeartbeatConfig, HeartbeatsFile
    from pydantic import ValidationError

    # Check ID uniqueness
    if Heartbeat.query.get(data.get("id", "")):
        return jsonify({"error": f"Heartbeat id '{data.get('id')}' already exists"}), 409

    try:
        hb_config = HeartbeatConfig.model_validate(data)
    except ValidationError as exc:
        return jsonify({"error": "Validation failed", "details": exc.errors()}), 422

    # Write to YAML atomically
    try:
        yaml_cfg = _load_yaml_config()
        yaml_cfg.heartbeats.append(hb_config)
        _save_yaml_config(yaml_cfg)
    except Exception as exc:
        return jsonify({"error": f"Failed to write YAML: {exc}"}), 500

    # Mirror to DB
    _mirror_to_db(hb_config)

    audit(current_user, "create", "heartbeats", f"created heartbeat {hb_config.id}")
    return jsonify(Heartbeat.query.get(hb_config.id).to_dict()), 201


# ── Update (partial) ─────────────────────────────────────────────────────────

@bp.route("/api/heartbeats/<string:heartbeat_id>", methods=["PATCH"])
def update_heartbeat(heartbeat_id):
    denied = _require("manage")
    if denied:
        return denied

    hb = Heartbeat.query.get_or_404(heartbeat_id)
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Apply allowed partial updates
    updatable = {
        "agent", "interval_seconds", "max_turns", "timeout_seconds",
        "lock_timeout_seconds", "wake_triggers", "enabled",
        "goal_id", "required_secrets", "decision_prompt",
    }

    for field, value in data.items():
        if field not in updatable:
            continue
        if field == "wake_triggers":
            hb.wake_triggers_list = value
        elif field == "required_secrets":
            hb.required_secrets_list = value
        elif field == "enabled":
            hb.enabled = bool(value)
        elif hasattr(hb, field):
            setattr(hb, field, value)

    hb.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    # Sync back to YAML
    try:
        yaml_cfg = _load_yaml_config()
        from heartbeat_schema import HeartbeatConfig
        from pydantic import ValidationError

        # Rebuild YAML list with updated entry
        updated = []
        found = False
        for entry in yaml_cfg.heartbeats:
            if entry.id == heartbeat_id:
                try:
                    merged = entry.model_dump()
                    for k, v in data.items():
                        if k in merged:
                            merged[k] = v
                    updated.append(HeartbeatConfig.model_validate(merged))
                    found = True
                except ValidationError:
                    updated.append(entry)  # keep original on validation failure
            else:
                updated.append(entry)
        if not found:
            pass  # DB-only entry, no YAML update needed

        yaml_cfg.heartbeats = updated
        _save_yaml_config(yaml_cfg)
    except Exception as exc:
        # Non-fatal: DB is the live state, YAML is best-effort
        print(f"[heartbeats] WARNING: YAML sync failed after PATCH: {exc}", flush=True)

    # If dispatcher is running, re-register interval jobs to pick up changes
    if "interval_seconds" in data or "enabled" in data or "wake_triggers" in data:
        try:
            from heartbeat_dispatcher import register_interval_jobs
            register_interval_jobs()
        except Exception:
            pass

    audit(current_user, "update", "heartbeats", f"updated heartbeat {heartbeat_id}: {list(data.keys())}")
    return jsonify(hb.to_dict())


# ── Delete ───────────────────────────────────────────────────────────────────

@bp.route("/api/heartbeats/<string:heartbeat_id>", methods=["DELETE"])
def delete_heartbeat(heartbeat_id):
    denied = _require("manage")
    if denied:
        return denied

    hb = Heartbeat.query.get_or_404(heartbeat_id)

    force = request.args.get("force", "false").lower() == "true"

    # Safety check: running run?
    running = HeartbeatRun.query.filter_by(
        heartbeat_id=heartbeat_id, status="running"
    ).first()
    if running and not force:
        return jsonify({
            "error": "Heartbeat has a run in progress",
            "run_id": running.run_id,
            "hint": "Use ?force=true to delete anyway (run will be orphaned)",
        }), 409

    # Remove from YAML
    try:
        yaml_cfg = _load_yaml_config()
        yaml_cfg.heartbeats = [h for h in yaml_cfg.heartbeats if h.id != heartbeat_id]
        _save_yaml_config(yaml_cfg)
    except Exception as exc:
        print(f"[heartbeats] WARNING: YAML removal failed: {exc}", flush=True)

    # Remove from DB (cascade deletes runs + triggers)
    db.session.delete(hb)
    db.session.commit()

    # Unregister interval job
    try:
        import schedule as _schedule
        _schedule.clear(f"hb-interval-{heartbeat_id}")
    except Exception:
        pass

    audit(current_user, "delete", "heartbeats", f"deleted heartbeat {heartbeat_id}")
    return jsonify({"deleted": heartbeat_id}), 200


# ── Manual trigger ───────────────────────────────────────────────────────────

@bp.route("/api/heartbeats/<string:heartbeat_id>/run", methods=["POST"])
def manual_run(heartbeat_id):
    denied = _require("execute")
    if denied:
        return denied

    hb = Heartbeat.query.get_or_404(heartbeat_id)

    from heartbeat_dispatcher import dispatch
    dispatched, run_id = dispatch(heartbeat_id, "manual")

    if not dispatched:
        return jsonify({
            "warning": "Heartbeat debounced or disabled — no run dispatched",
            "heartbeat_id": heartbeat_id,
        }), 200

    audit(current_user, "execute", "heartbeats", f"manual run {heartbeat_id} run_id={run_id}")
    return jsonify({"run_id": run_id, "heartbeat_id": heartbeat_id, "status": "dispatched"}), 202


# ── Runs history ─────────────────────────────────────────────────────────────

@bp.route("/api/heartbeats/<string:heartbeat_id>/runs")
def list_runs(heartbeat_id):
    denied = _require("view")
    if denied:
        return denied

    _ = Heartbeat.query.get_or_404(heartbeat_id)

    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    runs = (
        HeartbeatRun.query
        .filter_by(heartbeat_id=heartbeat_id)
        .order_by(HeartbeatRun.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = HeartbeatRun.query.filter_by(heartbeat_id=heartbeat_id).count()

    return jsonify({
        "runs": [r.to_dict() for r in runs],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


# ── Single run ───────────────────────────────────────────────────────────────

@bp.route("/api/heartbeats/<string:heartbeat_id>/runs/<string:run_id>")
def get_run(heartbeat_id, run_id):
    denied = _require("view")
    if denied:
        return denied

    run = HeartbeatRun.query.filter_by(
        heartbeat_id=heartbeat_id, run_id=run_id
    ).first_or_404()
    return jsonify(run.to_dict())


# ── Reindex (rebuild DB from YAML) ───────────────────────────────────────────

@bp.route("/api/heartbeats/reindex", methods=["POST"])
def reindex_heartbeats():
    denied = _require("manage")
    if denied:
        return denied

    try:
        yaml_cfg = _load_yaml_config()
        for hb_config in yaml_cfg.heartbeats:
            _mirror_to_db(hb_config)
        return jsonify({"reindexed": len(yaml_cfg.heartbeats)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
