"""Hermes profiles endpoint — inspect installed profiles and preview routing.

Fase 2 da integração Hermes↔EvoNexus. Read-only surface over the profile
registry in ``ADWs/hermes_profiles.py``: lists installed Hermes profiles and
previews which profile a task_type/override would resolve to, so the dashboard
UI can show the effective privilege before a task runs.

Auth: dashboard session cookie via ``@login_required`` (same pattern as
providers.py). No Bearer/service-auth.
"""

import re
import sys
from pathlib import Path

import yaml
from flask import Blueprint, jsonify, request
from flask_login import login_required

from routes._helpers import WORKSPACE

# Reuse the pure resolution module from ADWs (single source of truth).
_ADW_DIR = WORKSPACE / "ADWs"
if str(_ADW_DIR) not in sys.path:
    sys.path.insert(0, str(_ADW_DIR))

import hermes_profiles as hp  # noqa: E402

bp = Blueprint("hermes_profiles", __name__)

# Keys in a profile's config.yaml whose values must never reach the client.
_SECRET_KEY_RE = re.compile(r"(key|token|secret|password|api_key)", re.IGNORECASE)


def _mask_secret(value: str) -> str:
    """Mask a secret for safe display (mirrors providers._mask_secret)."""
    if not isinstance(value, str) or len(value) < 8:
        return "****" if value else ""
    return value[:6] + "****" + value[-4:]


def _profile_meta(slug: str, config: dict) -> dict:
    """Read a profile's config.yaml, returning safe (masked) metadata.

    Only scalar strings are surfaced — nested dicts (which may contain secrets
    like api_key buried several levels deep) are never returned whole.
    """
    base = hp._profiles_dir(config) / slug / "config.yaml"
    meta: dict = {"slug": slug, "provider": None, "model": None}
    try:
        data = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return meta
    if not isinstance(data, dict):
        return meta

    # model: either a string ("gpt-4") or a dict ({"default": "glm-4.6", "provider": "zai", ...})
    model_raw = data.get("model")
    if isinstance(model_raw, str):
        meta["model"] = model_raw
    elif isinstance(model_raw, dict):
        meta["model"] = model_raw.get("default")
        meta["provider"] = model_raw.get("provider")

    # top-level provider (may override model.provider if it's a plain string)
    provider_raw = data.get("provider")
    if isinstance(provider_raw, str) and provider_raw:
        meta["provider"] = provider_raw

    return meta


@bp.route("/api/hermes/profiles", methods=["GET"])
@login_required
def list_profiles():
    """List installed Hermes profiles + current routing table + fallback."""
    config = hp.load_routing()
    installed = hp.list_installed_profiles(config)
    return jsonify({
        "profiles": [_profile_meta(slug, config) for slug in installed],
        "installed": installed,
        "routing": config.get("routing", {}),
        "fallback_profile": config.get("fallback_profile"),
    })


@bp.route("/api/hermes/profiles/resolve", methods=["GET"])
@login_required
def resolve():
    """Preview the profile a task_type/override would resolve to (no execution)."""
    task_type = request.args.get("task_type") or None
    override = request.args.get("override") or None
    try:
        slug, reason = hp.resolve_profile(task_type, override)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({
        "profile": slug,
        "reason": reason,
        "task_type": task_type,
        "override": override,
    })
