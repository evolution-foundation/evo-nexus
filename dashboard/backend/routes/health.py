"""Backend health endpoints.

These probes give deployment tooling a cheap way to verify the database,
filesystem, and runtime configuration without loading the full dashboard.

Two tiers:
- ``/api/health`` — public liveness. Minimal payload (status only) so it does
  not leak internal structure (filesystem paths, provider identity, error
  details) to unauthenticated callers. Suitable for load balancer health
  checks.
- ``/api/health/deep`` — authenticated readiness. Includes per-check details
  (database, filesystem, secret key source, provider). Requires an
  authenticated admin session so internal information stays internal.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify
from flask_login import login_required, current_user

from models import db
from routes._helpers import WORKSPACE

bp = Blueprint("health", __name__)


def _check_database() -> dict:
    try:
        db.session.execute(db.text("SELECT 1"))
        db.session.rollback()
        return {"status": "ok"}
    except Exception as exc:
        db.session.rollback()
        return {"status": "error", "detail": str(exc)[:200]}


def _check_writable_dir(path: Path, label: str) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".healthcheck.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"status": "ok", "label": label}
    except Exception as exc:
        return {"status": "error", "label": label, "detail": str(exc)[:200]}


def _check_secret_key() -> dict:
    key = os.environ.get("EVONEXUS_SECRET_KEY", "").strip()
    if key:
        return {"status": "ok", "source": "env"}

    key_file = WORKSPACE / "dashboard" / "data" / ".secret_key"
    if key_file.exists():
        return {
            "status": "warning",
            "source": "file",
            "detail": "Using plaintext fallback. Set EVONEXUS_SECRET_KEY in production.",
        }

    return {
        "status": "error",
        "source": "missing",
        "detail": "No secret key configured. Set EVONEXUS_SECRET_KEY or create a local fallback.",
    }


def _check_provider_config() -> dict:
    config_path = WORKSPACE / "config" / "providers.json"
    if not config_path.exists():
        return {"status": "warning", "detail": "providers.json is missing"}

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "detail": f"Invalid providers.json: {str(exc)[:150]}"}

    active = raw.get("active") if isinstance(raw, dict) else None
    if not active or active == "none":
        return {"status": "warning", "detail": "No active provider configured"}

    return {"status": "ok", "active": active}


def _overall_status(checks: dict) -> str:
    statuses = [check.get("status") for check in checks.values()]
    if any(status == "error" for status in statuses):
        return "error"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "ok"


def _build_report(deep: bool = False) -> dict:
    checks = {
        "database": _check_database(),
        "filesystem": _check_writable_dir(WORKSPACE / "dashboard" / "data", "dashboard-data"),
        "workspace": _check_writable_dir(WORKSPACE / "workspace", "workspace"),
        "secret_key": _check_secret_key(),
    }
    if deep:
        checks["providers"] = _check_provider_config()

    status = _overall_status(checks)
    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


@bp.route("/api/health")
def health():
    """Public liveness probe. Returns only ``status`` to avoid leaking internal
    structure. Use ``/api/health/deep`` (auth required) for per-check detail."""
    report = _build_report(deep=False)
    status = report["status"]
    http_code = 503 if status == "error" else 200
    return jsonify({"status": status}), http_code


@bp.route("/api/health/deep")
@login_required
def deep_health():
    """Authenticated readiness probe. Requires an active admin session; other
    roles see a 403 so non-operators cannot enumerate internal state."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"error": "Admin role required"}), 403
    report = _build_report(deep=True)
    http_code = 503 if report["status"] == "error" else 200
    return jsonify(report), http_code
