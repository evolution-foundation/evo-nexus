"""Onboarding wizard endpoints."""

import re

from flask import Blueprint, request, jsonify, abort
from flask_login import login_required, current_user
from models import db, User, BrainRepoConfig

from routes.providers import _read_config, _write_config, ALLOWED_ENV_VARS

bp = Blueprint("onboarding", __name__)

# Provider ids that set_provider knows how to handle in-band. Everything else
# either lives in providers.example.json (in which case we let it through with
# an env_vars merge) or is rejected as a 400.
_IN_BAND_PROVIDERS = frozenset({"anthropic", "openai", "openrouter"})

# Shell metacharacter filter — mirrors routes.providers._sanitize_env_vars so
# that onboarding cannot slip past the same guardrail providers.py enforces.
_SHELL_METACHAR_RE = re.compile(r'[;&|`$\n\r]')


def _filter_env_vars(env_vars: dict) -> dict:
    """Drop keys outside ALLOWED_ENV_VARS and values with shell metachars.

    Silent drop — same behaviour as providers.py so the frontend doesn't have
    to branch on partial accepts.
    """
    safe = {}
    if not isinstance(env_vars, dict):
        return safe
    for k, v in env_vars.items():
        if k not in ALLOWED_ENV_VARS:
            continue
        if not isinstance(v, str) or _SHELL_METACHAR_RE.search(v):
            continue
        safe[k] = v
    return safe


@bp.route("/api/onboarding/state")
@login_required
def get_state():
    """Return full onboarding state for frontend to restore correct step."""
    brain = BrainRepoConfig.query.filter_by(user_id=current_user.id).first()
    return jsonify({
        "onboarding_state": current_user.onboarding_state,
        "onboarding_completed_agents_visit": current_user.onboarding_completed_agents_visit,
        "brain_repo_configured": brain is not None and brain.github_token_encrypted is not None,
        "brain_repo": brain.to_dict() if brain else None,
    })

@bp.route("/api/onboarding/start", methods=["POST"])
@login_required
def start():
    current_user.onboarding_state = "pending"
    db.session.commit()
    return jsonify({"onboarding_state": "pending"})

@bp.route("/api/onboarding/complete", methods=["POST"])
@login_required
def complete():
    current_user.onboarding_state = "completed"
    db.session.commit()
    return jsonify({"onboarding_state": "completed"})

@bp.route("/api/onboarding/skip", methods=["POST"])
@login_required
def skip():
    current_user.onboarding_state = "skipped"
    db.session.commit()
    return jsonify({"onboarding_state": "skipped"})

@bp.route("/api/onboarding/provider", methods=["POST"])
@login_required
def set_provider():
    """Save the selected provider during onboarding.

    Writes into ``config/providers.json`` using the canonical schema
    ``{active_provider, providers: {<id>: {cli_command, env_vars, ...}}}``
    — NEVER the legacy ``{<id>: {api_key, enabled}}`` shape, which broke
    ``routes/providers.py`` and the Providers UI.

    Accepted request bodies:

    - Anthropic (configured via terminal login, no key needed)::

        {"provider": "anthropic"}

      Sets ``active_provider = "anthropic"``. ``env_vars`` is left untouched.

    - OpenAI API key::

        {"provider": "openai",
         "env_vars": {"OPENAI_API_KEY": "sk-...", "OPENAI_MODEL": "gpt-4o"}}

      Merges the supplied env vars (allowlisted only) into
      ``providers.openai.env_vars`` and sets ``active_provider = "openai"``.

    - OpenRouter::

        {"provider": "openrouter",
         "env_vars": {"OPENAI_API_KEY": "...",
                      "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
                      "OPENAI_MODEL": "..."}}

      Same pattern, target id ``openrouter``.

    - Codex OAuth (``codex`` / ``codex_auth``) is handled OUT-OF-BAND by the
      existing endpoints:
        * ``POST /api/providers/openai/auth-start``
        * ``POST /api/providers/openai/auth-complete``
        * ``POST /api/providers/openai/device-start``
        * ``POST /api/providers/openai/device-poll``
      Those endpoints write ``~/.codex/auth.json`` and already set
      ``active_provider = "codex_auth"``. The frontend must call them
      directly. This endpoint treats ``provider in {"codex", "codex_auth"}``
      as a no-op that only flips ``active_provider`` when the codex_auth
      provider is already defined — it does NOT accept an api_key payload.

    Unknown provider ids (not present in providers.example.json) return 400.
    Env vars outside ``ALLOWED_ENV_VARS`` or containing shell metacharacters
    are silently dropped (same policy as ``routes/providers.py``).
    """
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip()
    if not provider:
        abort(400, description="provider required")

    config = _read_config()
    providers = config.setdefault("providers", {})

    # Codex OAuth: do nothing here — auth lives in ~/.codex/auth.json and is
    # written by /api/providers/openai/auth-complete | device-poll. If the
    # codex_auth provider is already configured, just mark it active.
    if provider in ("codex", "codex_auth"):
        if "codex_auth" in providers:
            config["active_provider"] = "codex_auth"
            _write_config(config)
            return jsonify({
                "ok": True,
                "provider": "codex_auth",
                "active_provider": "codex_auth",
                "note": "Use /api/providers/openai/auth-start to complete OAuth",
            })
        return jsonify({
            "ok": True,
            "provider": "codex_auth",
            "active_provider": config.get("active_provider"),
            "note": "Codex requires OAuth — call /api/providers/openai/auth-start",
        })

    # Provider must exist in the canonical schema (providers.example.json).
    # _read_config() already seeded providers.json from the example when it
    # was missing, so `providers` should contain all seven known ids.
    if provider not in providers:
        abort(400, description=f"unknown provider: {provider}")

    # Anthropic: no env vars, no API key — user is expected to run `claude`
    # login from a terminal. Just flip active_provider.
    if provider == "anthropic":
        config["active_provider"] = "anthropic"
        _write_config(config)
        return jsonify({
            "ok": True,
            "provider": "anthropic",
            "active_provider": "anthropic",
        })

    # OpenAI / OpenRouter: merge filtered env_vars into the provider block.
    incoming_env = _filter_env_vars(data.get("env_vars") or {})
    existing_env = providers[provider].get("env_vars", {}) or {}
    merged = dict(existing_env)
    for k, v in incoming_env.items():
        merged[k] = v
    providers[provider]["env_vars"] = merged
    config["active_provider"] = provider
    _write_config(config)

    return jsonify({
        "ok": True,
        "provider": provider,
        "active_provider": provider,
    })
