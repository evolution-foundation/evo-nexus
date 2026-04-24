"""Trigger Registry API — reactive event triggers (webhook + event-based)."""

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from flask_login import current_user
from models import db, Trigger, TriggerExecution, has_permission, audit

bp = Blueprint("triggers", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
VALID_TYPES = ("webhook", "event")
VALID_SOURCES = ("github", "linear", "telegram", "discord", "stripe", "custom")
VALID_ACTION_TYPES = ("skill", "prompt", "script")

# Cache python command at module load time (F3)
_PYTHON_CMD = shutil.which("uv")
PYTHON_CMD = "uv run python" if _PYTHON_CMD else "python3"


def _require(resource: str, action: str):
    if not has_permission(current_user.role, resource, action):
        return jsonify({"error": "Forbidden"}), 403
    return None


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def _safe_int(value, default: int) -> int:
    """Parse int from query param, returning default on failure (F5)."""
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _validate_trigger_fields(data: dict) -> str | None:
    """Validate trigger fields, return error message or None (F4, F16)."""
    if "type" in data and data["type"] not in VALID_TYPES:
        return "type must be webhook or event"
    if "source" in data and data["source"] not in VALID_SOURCES:
        return "Invalid source"
    if "action_type" in data and data["action_type"] not in VALID_ACTION_TYPES:
        return "action_type must be skill, prompt, or script"
    return None


# ── CRUD Endpoints ──────────────────────────────────────────────────────────


@bp.route("/api/triggers")
def list_triggers():
    denied = _require("triggers", "view")
    if denied:
        return denied

    type_filter = request.args.get("type")
    source_filter = request.args.get("source")
    enabled_filter = request.args.get("enabled")
    page = _safe_int(request.args.get("page"), 1)
    per_page = min(_safe_int(request.args.get("per_page"), 50), 100)

    query = Trigger.query
    if type_filter:
        query = query.filter_by(type=type_filter)
    if source_filter:
        query = query.filter_by(source=source_filter)
    if enabled_filter in ("true", "false"):  # F7: only apply for explicit values
        query = query.filter_by(enabled=enabled_filter == "true")
    query = query.order_by(Trigger.created_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "triggers": [t.to_dict() for t in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
    })


@bp.route("/api/triggers/<int:trigger_id>")
def get_trigger(trigger_id):
    denied = _require("triggers", "view")
    if denied:
        return denied

    trigger = Trigger.query.get_or_404(trigger_id)
    data = trigger.to_dict()
    # Include last 20 executions
    execs = TriggerExecution.query.filter_by(trigger_id=trigger_id) \
        .order_by(TriggerExecution.started_at.desc()).limit(20).all()
    data["executions"] = [e.to_dict() for e in execs]
    return jsonify(data)


@bp.route("/api/triggers", methods=["POST"])
def create_trigger():
    denied = _require("triggers", "execute")
    if denied:
        return denied

    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    required = ["name", "type", "source", "action_type", "action_payload"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    err = _validate_trigger_fields(data)
    if err:
        return jsonify({"error": err}), 400

    slug = _slugify(data["name"])
    existing = Trigger.query.filter_by(slug=slug).first()
    if existing:
        slug = f"{slug}-{int(datetime.now(timezone.utc).timestamp())}"

    event_filter = data.get("event_filter", {})
    if isinstance(event_filter, str):
        try:
            event_filter = json.loads(event_filter)
        except json.JSONDecodeError:
            return jsonify({"error": "event_filter must be valid JSON"}), 400

    trigger = Trigger(
        name=data["name"],
        slug=slug,
        type=data["type"],
        source=data["source"],
        event_filter=json.dumps(event_filter),
        action_type=data["action_type"],
        action_payload=data["action_payload"],
        agent=data.get("agent"),
        secret=Trigger.generate_secret(),
        enabled=data.get("enabled", True),
        created_by=current_user.id if current_user.is_authenticated else None,
    )
    db.session.add(trigger)
    db.session.commit()

    audit(current_user, "create", "triggers", f"Created trigger #{trigger.id}: {trigger.name}")

    result = trigger.to_dict(include_secret=True)
    result["webhook_url"] = trigger.webhook_url(request.host_url.rstrip("/"))
    return jsonify(result), 201


@bp.route("/api/triggers/<int:trigger_id>", methods=["PUT"])
def update_trigger(trigger_id):
    denied = _require("triggers", "execute")
    if denied:
        return denied

    trigger = Trigger.query.get_or_404(trigger_id)
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    # F4: Validate fields before applying
    err = _validate_trigger_fields(data)
    if err:
        return jsonify({"error": err}), 400

    for field in ("name", "type", "source", "action_type", "action_payload", "agent", "enabled"):
        if field in data:
            setattr(trigger, field, data[field])

    if "event_filter" in data:
        ef = data["event_filter"]
        if isinstance(ef, str):
            try:
                ef = json.loads(ef)
            except json.JSONDecodeError:
                return jsonify({"error": "event_filter must be valid JSON"}), 400
        trigger.event_filter = json.dumps(ef)

    db.session.commit()
    audit(current_user, "update", "triggers", f"Updated trigger #{trigger.id}: {trigger.name}")
    return jsonify(trigger.to_dict())


@bp.route("/api/triggers/<int:trigger_id>", methods=["DELETE"])
def delete_trigger(trigger_id):
    denied = _require("triggers", "manage")
    if denied:
        return denied

    trigger = Trigger.query.get_or_404(trigger_id)
    name = trigger.name
    db.session.delete(trigger)
    db.session.commit()
    audit(current_user, "delete", "triggers", f"Deleted trigger #{trigger_id}: {name}")
    return jsonify({"status": "ok"})


@bp.route("/api/triggers/<int:trigger_id>/test", methods=["POST"])
def test_trigger(trigger_id):
    denied = _require("triggers", "execute")
    if denied:
        return denied

    trigger = Trigger.query.get_or_404(trigger_id)

    test_event = {
        "_test": True,
        "source": trigger.source,
        "event_type": trigger.event_filter_dict.get("event", "test"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    execution = TriggerExecution(
        trigger_id=trigger.id,
        event_data=json.dumps(test_event),
        status="pending",
    )
    db.session.add(execution)
    db.session.commit()

    from flask import current_app
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            _execute_trigger(trigger.id, execution.id, test_event)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    audit(current_user, "test", "triggers", f"Tested trigger #{trigger.id}: {trigger.name}")
    return jsonify({"status": "started", "execution_id": execution.id})


@bp.route("/api/triggers/<int:trigger_id>/regenerate-secret", methods=["POST"])
def regenerate_secret(trigger_id):
    denied = _require("triggers", "manage")
    if denied:
        return denied

    trigger = Trigger.query.get_or_404(trigger_id)
    trigger.secret = Trigger.generate_secret()
    db.session.commit()
    audit(current_user, "regenerate_secret", "triggers", f"Regenerated secret for trigger #{trigger.id}")
    return jsonify({"secret": trigger.secret})


@bp.route("/api/triggers/<int:trigger_id>/executions")
def list_executions(trigger_id):
    denied = _require("triggers", "view")
    if denied:
        return denied

    Trigger.query.get_or_404(trigger_id)

    page = _safe_int(request.args.get("page"), 1)
    per_page = min(_safe_int(request.args.get("per_page"), 20), 100)

    query = TriggerExecution.query.filter_by(trigger_id=trigger_id) \
        .order_by(TriggerExecution.started_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "executions": [e.to_dict() for e in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
    })


# ── Webhook Receiver (PUBLIC — no session auth) ────────────────────────────


@bp.route("/api/triggers/webhook/<int:trigger_id>", methods=["POST"])
def webhook_receiver(trigger_id):
    """Public endpoint that receives webhook POSTs from external services."""
    trigger = Trigger.query.get(trigger_id)

    # F6: Uniform responses to prevent trigger ID enumeration
    if not trigger or not trigger.enabled:
        return jsonify({"status": "ok"}), 200

    # Validate signature based on source
    if not _validate_webhook_signature(request, trigger):
        return jsonify({"status": "ok"}), 200  # F6: don't reveal invalid signature vs not found

    # Parse event data
    event_data = _parse_webhook_event(request, trigger.source)

    # Check event filter
    if not _matches_filter(event_data, trigger.event_filter_dict):
        return jsonify({"status": "ok"}), 200

    # Create execution and run async
    execution = TriggerExecution(
        trigger_id=trigger.id,
        event_data=json.dumps(event_data),
        status="pending",
    )
    db.session.add(execution)
    db.session.commit()

    from flask import current_app
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            _execute_trigger(trigger.id, execution.id, event_data)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"status": "ok"}), 200


# ── Webhook Validation & Parsing ───────────────────────────────────────────


def _validate_webhook_signature(req, trigger) -> bool:
    """Validate webhook signature based on source type."""
    source = trigger.source
    secret = trigger.secret.encode()
    body = req.get_data()

    if source == "github":
        sig_header = req.headers.get("X-Hub-Signature-256", "")
        if not sig_header.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig_header, expected)

    elif source == "stripe":
        sig_header = req.headers.get("Stripe-Signature", "")
        if not sig_header:
            return False
        parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
        timestamp = parts.get("t", "")
        sig = parts.get("v1", "")
        if not timestamp or not sig:
            return False
        # F9: Reject stale timestamps (replay protection, 5-minute window)
        try:
            if abs(time.time() - int(timestamp)) > 300:
                return False
        except (ValueError, OverflowError):
            return False
        signed_payload = f"{timestamp}.{body.decode()}"
        expected = hmac.new(secret, signed_payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)

    elif source == "linear":
        sig_header = req.headers.get("X-Linear-Signature", "")
        if not sig_header:
            return False
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig_header, expected)

    else:
        # F2: All sources require signature validation — no bypass for custom
        sig_header = req.headers.get("X-Webhook-Signature", "")
        if not sig_header:
            return False
        if sig_header.startswith("sha256="):
            sig_header = sig_header[7:]
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig_header, expected)


def _parse_webhook_event(req, source: str) -> dict:
    """Parse incoming webhook into normalized event data."""
    payload = req.get_json(silent=True) or {}

    if source == "github":
        event_type = req.headers.get("X-GitHub-Event", "unknown")
        return {
            "event_type": event_type,
            "source": "github",
            "action": payload.get("action", ""),
            "repo": payload.get("repository", {}).get("full_name", ""),
            "branch": (payload.get("ref", "").replace("refs/heads/", "")
                       if event_type == "push" else
                       payload.get("pull_request", {}).get("head", {}).get("ref", "")),
            "sender": payload.get("sender", {}).get("login", ""),
            "data": payload,
        }

    elif source == "stripe":
        return {
            "event_type": payload.get("type", "unknown"),
            "source": "stripe",
            "data": payload.get("data", {}).get("object", {}),
            "api_version": payload.get("api_version", ""),
        }

    elif source == "linear":
        return {
            "event_type": payload.get("type", "unknown"),
            "source": "linear",
            "action": payload.get("action", ""),
            "data": payload.get("data", {}),
        }

    elif source == "telegram":
        msg = payload.get("message", {})
        return {
            "event_type": "message" if msg else payload.get("callback_query", {}).get("data", "callback"),
            "source": "telegram",
            "chat_id": str(msg.get("chat", {}).get("id", "")),
            "from_user": msg.get("from", {}).get("username", ""),
            "text": msg.get("text", ""),
            "data": payload,
        }

    elif source == "discord":
        return {
            "event_type": payload.get("t", "unknown"),
            "source": "discord",
            "channel_id": payload.get("d", {}).get("channel_id", ""),
            "data": payload,
        }

    else:
        return {
            "event_type": payload.get("event", payload.get("type", "unknown")),
            "source": source,
            "data": payload,
        }


def _matches_filter(event_data: dict, filter_config: dict) -> bool:
    """Check if event data matches the trigger's filter configuration."""
    if not filter_config:
        return True

    for key, expected in filter_config.items():
        actual = event_data.get(key)
        if actual is None:
            actual = event_data.get("data", {}).get(key)
        if actual is None:
            return False

        if isinstance(expected, str) and "*" in expected:
            import fnmatch
            if not fnmatch.fnmatch(str(actual), expected):
                return False
        elif str(actual) != str(expected):
            return False

    return True


# ── Trigger Execution ──────────────────────────────────────────────────────


def _execute_trigger(trigger_id: int, execution_id: int, event_data: dict):
    """Execute a trigger action. Must be called within app context."""
    trigger = Trigger.query.get(trigger_id)
    execution = TriggerExecution.query.get(execution_id)

    # F8: If trigger or execution is gone, mark execution as failed if possible
    if not execution:
        return
    if not trigger:
        execution.status = "failed"
        execution.error = "Trigger was deleted before execution started"
        execution.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        return

    execution.status = "running"
    execution.started_at = datetime.now(timezone.utc)
    db.session.commit()

    start_time = datetime.now(timezone.utc)

    try:
        # F1: Use subprocess with argument list instead of shell=True
        # Import runner directly via sys.path
        runner_path = str(WORKSPACE / "ADWs")
        if runner_path not in sys.path:
            sys.path.insert(0, runner_path)

        from runner import run_skill, run_claude

        event_context = json.dumps(event_data, ensure_ascii=False)[:1000]

        if trigger.action_type == "skill":
            result = run_skill(
                trigger.action_payload,
                log_name=f"trigger-{trigger.slug}",
                timeout=600,
                agent=trigger.agent or None,
            )
        elif trigger.action_type == "prompt":
            payload_with_context = f"{trigger.action_payload}\n\nEvent data: {event_context}"
            result = run_claude(
                payload_with_context,
                log_name=f"trigger-{trigger.slug}",
                timeout=600,
                agent=trigger.agent or None,
            )
        elif trigger.action_type == "script":
            # F1: Validate script path is within ADWs/routines/
            script_path = (WORKSPACE / "ADWs" / "routines" / trigger.action_payload).resolve()
            allowed_dir = (WORKSPACE / "ADWs" / "routines").resolve()
            if not str(script_path).startswith(str(allowed_dir)):
                raise ValueError(f"Script path escapes ADWs/routines/: {trigger.action_payload}")
            if not script_path.exists():
                raise FileNotFoundError(f"Script not found: {trigger.action_payload}")

            proc = subprocess.run(
                [PYTHON_CMD.split()[0]] + PYTHON_CMD.split()[1:] + [str(script_path)],
                capture_output=True, text=True, timeout=660, cwd=str(WORKSPACE)
            )
            result = {
                "success": proc.returncode == 0,
                "stdout": (proc.stdout or "")[:5000],
                "stderr": (proc.stderr or "")[:2000],
            }
        else:
            raise ValueError(f"Unknown action_type: {trigger.action_type}")

        execution.status = "completed" if result.get("success") else "failed"
        execution.result_summary = (result.get("stdout", "") or "")[:5000]
        if not result.get("success"):
            execution.error = (result.get("stderr", "") or "")[:2000]

    except subprocess.TimeoutExpired:
        execution.status = "failed"
        execution.error = "Timeout (11 min)"
    except Exception as e:
        execution.status = "failed"
        execution.error = str(e)[:2000]

    end_time = datetime.now(timezone.utc)
    execution.duration_seconds = (end_time - start_time).total_seconds()
    execution.completed_at = end_time
    db.session.commit()


# ── YAML Sync ──────────────────────────────────────────────────────────────


def sync_triggers_from_yaml():
    """Load trigger definitions from config/triggers.yaml into DB.
    Only creates new triggers; does not overwrite UI-edited ones."""
    import yaml

    config_path = WORKSPACE / "config" / "triggers.yaml"
    if not config_path.exists():
        return

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if not config:
            return
    except Exception:
        return

    all_triggers = []
    for trigger_def in (config.get("webhooks") or []):
        trigger_def["_type"] = "webhook"
        all_triggers.append(trigger_def)
    for trigger_def in (config.get("events") or []):
        trigger_def["_type"] = "event"
        all_triggers.append(trigger_def)

    for tdef in all_triggers:
        name = tdef.get("name")
        if not name:
            continue

        existing = Trigger.query.filter_by(name=name, from_yaml=True).first()
        if existing:
            continue

        # F16: Validate fields from YAML
        source = tdef.get("source", "custom")
        action_type = tdef.get("action_type", "prompt")
        if source not in VALID_SOURCES or action_type not in VALID_ACTION_TYPES:
            continue

        slug = _slugify(name)
        if Trigger.query.filter_by(slug=slug).first():
            slug = f"{slug}-yaml"

        trigger = Trigger(
            name=name,
            slug=slug,
            type=tdef["_type"],
            source=source,
            event_filter=json.dumps(tdef.get("event_filter", {})),
            action_type=action_type,
            action_payload=tdef.get("action_payload", ""),
            agent=tdef.get("agent"),
            secret=Trigger.generate_secret(),
            enabled=tdef.get("enabled", True),
            from_yaml=True,
        )
        db.session.add(trigger)

    db.session.commit()
