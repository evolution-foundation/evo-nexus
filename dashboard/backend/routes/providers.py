"""Providers endpoint — manage AI provider configurations (Anthropic, OpenRouter, OpenAI, Gemini, etc.).

EvoNexus supports multiple AI providers via OpenClaude. The active provider
determines which CLI binary (claude vs openclaude) and which env vars are
injected when spawning sessions.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request, session
from flask_login import login_required

from routes._helpers import WORKSPACE

bp = Blueprint("providers", __name__)

PROVIDERS_CONFIG = WORKSPACE / "config" / "providers.json"

OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"

# Allowlisted CLI commands — only these binaries can be spawned
ALLOWED_CLI_COMMANDS = frozenset({"claude", "openclaude"})

# Allowlisted env var names — only these can be injected into subprocess
ALLOWED_ENV_VARS = frozenset({
    "CLAUDE_CODE_USE_OPENAI",
    "CLAUDE_CODE_USE_GEMINI",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    # Codex OAuth support (OpenClaude 0.3+ reads ~/.codex/auth.json automatically,
    # but these allow overriding the auth file path or providing a raw token)
    "CODEX_AUTH_JSON_PATH",
    "CODEX_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "AWS_REGION",
    "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
})


def _read_config() -> dict:
    """Read providers.json. If missing, copy from providers.example.json."""
    try:
        if not PROVIDERS_CONFIG.is_file():
            example = PROVIDERS_CONFIG.parent / "providers.example.json"
            if example.is_file():
                import shutil as _shutil
                _shutil.copy2(example, PROVIDERS_CONFIG)
        if PROVIDERS_CONFIG.is_file():
            return json.loads(PROVIDERS_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {"active_provider": "anthropic", "providers": {}}


def _write_config(config: dict):
    """Write providers.json."""
    PROVIDERS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    PROVIDERS_CONFIG.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _mask_secret(value: str) -> str:
    """Mask an API key for safe display: sk-or-v1-abc...xyz → sk-or-****xyz."""
    if not value or len(value) < 8:
        return "****" if value else ""
    return value[:6] + "****" + value[-4:]


def _run_cli_version(command: str, env: dict | None = None) -> dict:
    """Run '<command> --version' safely using hardcoded dispatch.

    Each branch uses a literal string for the executable so that
    semgrep/opengrep does not flag it as subprocess injection.
    """
    run_kwargs = dict(capture_output=True, text=True, timeout=10)
    if env is not None:
        run_kwargs["env"] = env

    try:
        if command == "openclaude":
            result = subprocess.run(["openclaude", "--version"], **run_kwargs)  # noqa: S603, S607
        elif command == "claude":
            result = subprocess.run(["claude", "--version"], **run_kwargs)  # noqa: S603, S607
        else:
            return {"installed": False, "version": None, "path": None}

        version = result.stdout.strip() or result.stderr.strip()
        return {"installed": True, "version": version, "path": shutil.which(command)}
    except (subprocess.TimeoutExpired, OSError):
        return {"installed": False, "version": None, "path": shutil.which(command)}


def _check_cli(command: str) -> dict:
    """Check if a CLI tool is installed. Only allowlisted commands are accepted."""
    if command not in ALLOWED_CLI_COMMANDS:
        return {"installed": False, "version": None, "path": None}
    return _run_cli_version(command)


def _sanitize_env_vars(env_vars: dict) -> dict:
    """Filter env vars to only allowlisted names and safe values."""
    safe = {}
    for k, v in env_vars.items():
        if k not in ALLOWED_ENV_VARS:
            continue
        # Reject values with shell metacharacters
        if not isinstance(v, str) or re.search(r'[;&|`$\n\r]', v):
            continue
        safe[k] = v
    return safe


def _save_codex_auth(tokens: dict):
    """Save tokens to ~/.codex/auth.json in the format OpenClaude/Codex expects.

    The correct format uses auth_mode + tokens object, NOT the old
    openai-codex wrapper that OpenClaude doesn't recognize.
    """
    import base64 as _b64

    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token", "")
    id_token = tokens.get("id_token", access_token)

    # Extract chatgpt_account_id from the access token JWT
    account_id = ""
    try:
        payload_b64 = access_token.split(".")[1]
        # Add padding
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(_b64.urlsafe_b64decode(payload_b64))
        account_id = payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id", "")
    except Exception:
        pass

    auth_data = {
        "auth_mode": "Chatgpt",
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "account_id": account_id,
        },
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    CODEX_AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    CODEX_AUTH_FILE.write_text(json.dumps(auth_data, indent=2), encoding="utf-8")


# ── Endpoints ──────────────────────────────────────────────


@bp.route("/api/providers")
@login_required
def list_providers():
    """List all providers with status info."""
    config = _read_config()
    active = config.get("active_provider", "anthropic")
    providers = config.get("providers", {})

    # Check CLI installation status for both binaries
    claude_status = _check_cli("claude")
    openclaude_status = _check_cli("openclaude")

    result = []
    for key, prov in providers.items():
        cli = prov.get("cli_command", "claude")
        if cli not in ALLOWED_CLI_COMMANDS:
            continue
        cli_status = claude_status if cli == "claude" else openclaude_status

        # Mask env var values for API response
        env_vars = prov.get("env_vars", {})
        masked_vars = {}
        for var_name, var_value in env_vars.items():
            if "KEY" in var_name or "SECRET" in var_name or "TOKEN" in var_name:
                masked_vars[var_name] = _mask_secret(var_value)
            else:
                masked_vars[var_name] = var_value

        # Check if provider has required env vars filled
        has_config = all(
            v != "" for k, v in env_vars.items()
            if k not in ("CLAUDE_CODE_USE_OPENAI", "CLAUDE_CODE_USE_GEMINI",
                         "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
        ) if env_vars else True

        result.append({
            "id": key,
            "name": prov.get("name", key),
            "description": prov.get("description", ""),
            "cli_command": cli,
            "is_active": key == active,
            "installed": cli_status["installed"],
            "version": cli_status["version"],
            "path": cli_status["path"],
            "has_config": has_config,
            "env_vars": masked_vars,
            "requires_logout": prov.get("requires_logout", False),
            "setup_hint": prov.get("setup_hint"),
            "default_model": prov.get("default_model"),
            "default_base_url": prov.get("default_base_url"),
            "default_region": prov.get("default_region"),
        })

    return jsonify({
        "providers": result,
        "active_provider": active,
        "claude_installed": claude_status["installed"],
        "openclaude_installed": openclaude_status["installed"],
    })


@bp.route("/api/providers/active", methods=["GET"])
@login_required
def get_active_provider():
    """Get the active provider."""
    config = _read_config()
    active = config.get("active_provider", "anthropic")
    provider = config.get("providers", {}).get(active, {})
    return jsonify({
        "active_provider": active,
        "name": provider.get("name", active),
        "cli_command": provider.get("cli_command", "claude"),
    })


@bp.route("/api/providers/active", methods=["POST"])
@login_required
def set_active_provider():
    """Set the active provider. Use provider_id='none' to disable all."""
    data = request.get_json(silent=True) or {}
    provider_id = data.get("provider_id")
    if provider_id is None:
        return jsonify({"error": "provider_id is required"}), 400

    config = _read_config()
    # Allow "none" to disable all providers
    if provider_id != "none" and provider_id not in config.get("providers", {}):
        return jsonify({"error": f"Unknown provider: {provider_id}"}), 400

    config["active_provider"] = provider_id
    _write_config(config)

    return jsonify({"status": "ok", "active_provider": provider_id})


@bp.route("/api/providers/<provider_id>/config", methods=["GET"])
@login_required
def get_provider_config(provider_id):
    """Get a provider's config (env vars masked)."""
    config = _read_config()
    provider = config.get("providers", {}).get(provider_id)
    if not provider:
        return jsonify({"error": f"Unknown provider: {provider_id}"}), 400

    env_vars = provider.get("env_vars", {})
    masked = {}
    for k, v in env_vars.items():
        if "KEY" in k or "SECRET" in k or "TOKEN" in k:
            masked[k] = _mask_secret(v)
        else:
            masked[k] = v

    return jsonify({
        "id": provider_id,
        "name": provider.get("name"),
        "env_vars": masked,
        "env_var_names": list(env_vars.keys()),
    })


@bp.route("/api/providers/<provider_id>/config", methods=["POST"])
@login_required
def update_provider_config(provider_id):
    """Update a provider's env vars."""
    data = request.get_json(silent=True) or {}
    new_env_vars = data.get("env_vars", {})

    config = _read_config()
    provider = config.get("providers", {}).get(provider_id)
    if not provider:
        return jsonify({"error": f"Unknown provider: {provider_id}"}), 400

    # Merge: only update allowlisted vars that are provided and not masked
    existing = provider.get("env_vars", {})
    for key, value in new_env_vars.items():
        if key not in ALLOWED_ENV_VARS:
            continue
        if key not in existing:
            continue
        # Skip if value looks masked (contains ****)
        if "****" in str(value):
            continue
        # Reject values with shell metacharacters
        if not isinstance(value, str) or re.search(r'[;&|`$\n\r]', value):
            continue
        existing[key] = value

    provider["env_vars"] = existing
    _write_config(config)

    return jsonify({"status": "ok", "provider_id": provider_id})


@bp.route("/api/providers/<provider_id>/test", methods=["POST"])
@login_required
def test_provider(provider_id):
    """Test a provider by running its CLI with --version."""
    config = _read_config()
    provider = config.get("providers", {}).get(provider_id)
    if not provider:
        return jsonify({"error": f"Unknown provider: {provider_id}"}), 400

    cli = provider.get("cli_command", "claude")
    if cli not in ALLOWED_CLI_COMMANDS:
        return jsonify({"success": False, "error": f"Unsupported CLI: {cli}"}), 400

    if not shutil.which(cli):
        return jsonify({
            "success": False,
            "error": f"'{cli}' not found in PATH",
            "hint": f"npm install -g {'@gitlawb/openclaude' if cli == 'openclaude' else '@anthropic-ai/claude-code'}",
        })

    # Build env with sanitized provider vars
    env_vars = _sanitize_env_vars(
        {k: v for k, v in provider.get("env_vars", {}).items() if v}
    )
    test_env = {**os.environ, **env_vars}

    result = _run_cli_version(cli, env=test_env)
    return jsonify({
        "success": result["installed"],
        "version": result["version"],
        "cli": cli,
        "path": result["path"],
    })


# ── OpenAI Auth Flow ──────────────────────────────


@bp.route("/api/providers/openai/auth-start", methods=["POST"])
@login_required
def openai_auth_start():
    """Generate PKCE + authorize URL for Browser OAuth flow."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)

    session["openai_code_verifier"] = code_verifier
    session["openai_oauth_state"] = state

    params = {
        "response_type": "code",
        "client_id": OPENAI_CLIENT_ID,
        "redirect_uri": "http://localhost:1455/auth/callback",
        "scope": "openid profile email offline_access api.connectors.read api.connectors.invoke",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    url = f"{OPENAI_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return jsonify({"authorize_url": url})


@bp.route("/api/providers/openai/auth-complete", methods=["POST"])
@login_required
def openai_auth_complete():
    """Receive callback URL pasted by user, extract code, exchange for tokens."""
    import requests as http_req

    data = request.get_json(silent=True) or {}
    callback_url = data.get("callback_url", "")

    parsed = urllib.parse.urlparse(callback_url)
    params = urllib.parse.parse_qs(parsed.query)
    code = params.get("code", [None])[0]

    if not code:
        return jsonify({"error": "URL invalida - nao contem codigo de autorizacao"}), 400

    code_verifier = session.pop("openai_code_verifier", None)
    if not code_verifier:
        return jsonify({"error": "Sessao expirada - inicie o login novamente"}), 400

    session.pop("openai_oauth_state", None)

    resp = http_req.post(OPENAI_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://localhost:1455/auth/callback",
        "client_id": OPENAI_CLIENT_ID,
        "code_verifier": code_verifier,
    }, timeout=30)

    if resp.status_code != 200:
        return jsonify({"error": f"Falha na troca de token (HTTP {resp.status_code})"}), 400

    _save_codex_auth(resp.json())

    config = _read_config()
    # Use dedicated codex_auth provider key when OAuth is used (falls back to
    # openai for backward compatibility if codex_auth is not configured).
    providers = config.get("providers", {})
    config["active_provider"] = "codex_auth" if "codex_auth" in providers else "openai"
    _write_config(config)

    return jsonify({"status": "ok", "message": "Autenticado com sucesso!"})


@bp.route("/api/providers/openai/device-start", methods=["POST"])
@login_required
def openai_device_start():
    """Start device auth flow."""
    import requests as http_req

    resp = http_req.post("https://auth.openai.com/deviceauth/usercode", json={
        "client_id": OPENAI_CLIENT_ID,
    }, timeout=15)

    if resp.status_code != 200:
        return jsonify({"error": "Device auth nao disponivel para sua organizacao"}), 400

    data = resp.json()
    session["openai_device_auth_id"] = data["device_auth_id"]
    session["openai_device_user_code"] = data["user_code"]

    return jsonify({
        "user_code": data["user_code"],
        "verification_url": "https://auth.openai.com/codex/device",
        "interval": data.get("interval", 5),
        "expires_in": data.get("expires_in", 900),
    })


@bp.route("/api/providers/openai/device-poll", methods=["POST"])
@login_required
def openai_device_poll():
    """Poll for device auth authorization."""
    import requests as http_req

    device_auth_id = session.get("openai_device_auth_id")
    user_code = session.get("openai_device_user_code")
    if not device_auth_id:
        return jsonify({"status": "error", "message": "Nenhum login pendente"}), 400

    resp = http_req.post("https://auth.openai.com/deviceauth/token", json={
        "device_auth_id": device_auth_id,
        "user_code": user_code,
    }, timeout=15)

    if resp.status_code in (403, 404):
        return jsonify({"status": "pending"})

    if resp.status_code != 200:
        return jsonify({"status": "error", "message": "Polling falhou"}), 500

    auth_data = resp.json()

    token_resp = http_req.post(OPENAI_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": auth_data["authorization_code"],
        "code_verifier": auth_data["code_verifier"],
        "client_id": OPENAI_CLIENT_ID,
    }, timeout=15)

    if token_resp.status_code != 200:
        return jsonify({"status": "error", "message": "Token exchange falhou"}), 500

    _save_codex_auth(token_resp.json())

    config = _read_config()
    providers = config.get("providers", {})
    config["active_provider"] = "codex_auth" if "codex_auth" in providers else "openai"
    _write_config(config)

    session.pop("openai_device_auth_id", None)
    session.pop("openai_device_user_code", None)

    return jsonify({"status": "authorized"})


@bp.route("/api/providers/openai/status")
@login_required
def openai_status():
    """Check if Codex OAuth token exists and is valid."""
    if not CODEX_AUTH_FILE.is_file():
        return jsonify({"authenticated": False, "method": "none"})

    try:
        auth = json.loads(CODEX_AUTH_FILE.read_text(encoding="utf-8"))
        # Support both new format (auth_mode+tokens) and old format (openai-codex)
        tokens = auth.get("tokens", {})
        has_access = bool(tokens.get("access_token") or auth.get("openai-codex", {}).get("access"))
        return jsonify({
            "authenticated": has_access,
            "method": "codex_oauth",
            "auth_mode": auth.get("auth_mode", "unknown"),
            "auth_file": str(CODEX_AUTH_FILE),
        })
    except (json.JSONDecodeError, OSError):
        return jsonify({"authenticated": False, "method": "none"})


@bp.route("/api/providers/codex_auth/status")
@login_required
def codex_auth_status():
    """Alias for openai/status — reflects the dedicated codex_auth provider key."""
    return openai_status()


# ── Dynamic model discovery ──────────────────────────────
#
# These endpoints let the frontend populate the MODEL field in the Configure
# modal with the actual list of models available to the user, instead of
# forcing them to memorize the name.
#
# For the 'openai' provider we hit api.openai.com/v1/models with the user's
# API key (POST body, never logged). For 'codex_auth' we return the static
# list of Codex backend aliases that OpenClaude supports — these are fixed
# by the upstream project and cannot be discovered via API.

# Prefixes we consider "coding/agent relevant" — excludes embedding, tts,
# whisper, dall-e, moderation, etc. which are not useful for Claude-Code-
# style workflows.
_OPENAI_MODEL_PREFIXES = (
    "gpt-", "o1", "o3", "o4", "chatgpt-",
)
_OPENAI_MODEL_EXCLUDE = (
    "embedding", "whisper", "tts", "dall-e", "audio", "moderation",
    "realtime", "search", "image", "transcribe",
)

# Static ordering hint — lower index = higher in the dropdown
_OPENAI_MODEL_PRIORITY = [
    "gpt-4.1", "gpt-4o", "o4", "o3", "o1", "gpt-5", "gpt-4", "gpt-3.5", "chatgpt",
]


def _openai_model_rank(model_id: str) -> tuple[int, str]:
    """Sort key — (priority bucket, lexical) so gpt-4.1 family floats to top."""
    for i, prefix in enumerate(_OPENAI_MODEL_PRIORITY):
        if model_id.startswith(prefix):
            return (i, model_id)
    return (len(_OPENAI_MODEL_PRIORITY), model_id)


@bp.route("/api/providers/openai/models", methods=["POST"])
@login_required
def openai_models():
    """Validate an OpenAI API key and return the list of usable models.

    Body: {"api_key": "sk-..."}
    Response:
      {"valid": true, "models": [{"id": "...", "owned_by": "..."}]}
      {"valid": false, "error": "..."}
    """
    import requests as http_req

    data = request.get_json(silent=True) or {}
    api_key = (data.get("api_key") or "").strip()

    # Basic sanity check only: reject obviously empty/short keys before hitting
    # the network. Delegate detailed format validation to OpenAI itself — any
    # future format change (e.g. new key prefixes or separator chars) will be
    # correctly rejected as 401 by the upstream /v1/models call, rather than
    # by a stale regex in this file.
    if not api_key or len(api_key) < 20:
        return jsonify({"valid": False, "error": "API key inválida ou vazia"}), 400

    try:
        resp = http_req.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except http_req.RequestException as e:
        # Don't leak the key in error messages
        return jsonify({"valid": False, "error": f"Falha de rede: {type(e).__name__}"}), 502

    if resp.status_code == 401:
        return jsonify({"valid": False, "error": "API key rejeitada pela OpenAI (401)"}), 200
    if resp.status_code != 200:
        return jsonify({"valid": False, "error": f"OpenAI respondeu HTTP {resp.status_code}"}), 200

    try:
        payload = resp.json()
    except ValueError:
        return jsonify({"valid": False, "error": "Resposta da OpenAI não é JSON"}), 502

    raw_models = payload.get("data", []) or []

    # Filter: only chat-completion-capable models relevant to coding agents
    filtered = []
    for m in raw_models:
        mid = m.get("id", "")
        if not mid:
            continue
        if not mid.startswith(_OPENAI_MODEL_PREFIXES):
            continue
        if any(excl in mid for excl in _OPENAI_MODEL_EXCLUDE):
            continue
        filtered.append({
            "id": mid,
            "owned_by": m.get("owned_by", ""),
        })

    filtered.sort(key=lambda m: _openai_model_rank(m["id"]))

    return jsonify({"valid": True, "models": filtered, "count": len(filtered)})


# Codex OAuth aliases — OpenClaude's Codex mode uses these literal strings
# to route to the Codex backend. These are hardcoded upstream in OpenClaude
# itself; there is no OpenAI endpoint to discover them dynamically.
_CODEX_ALIASES = [
    {
        "id": "codexplan",
        "description": "GPT-5.4 no backend Codex (reasoning alto, default)",
        "description_en": "GPT-5.4 on Codex backend (high reasoning, default)",
    },
    {
        "id": "codexspark",
        "description": "GPT-5.3 Codex Spark (mais rápido, mais barato)",
        "description_en": "GPT-5.3 Codex Spark (faster, cheaper)",
    },
]


@bp.route("/api/providers/codex_auth/models", methods=["GET"])
@login_required
def codex_auth_models():
    """Return the static list of Codex aliases OpenClaude supports.

    Response: {"valid": bool, "models": [...], "auth_ok": bool}
    """
    auth_ok = False
    if CODEX_AUTH_FILE.is_file():
        try:
            auth = json.loads(CODEX_AUTH_FILE.read_text(encoding="utf-8"))
            tokens = auth.get("tokens", {})
            auth_ok = bool(tokens.get("access_token") or auth.get("openai-codex", {}).get("access"))
        except (json.JSONDecodeError, OSError):
            auth_ok = False

    return jsonify({
        "valid": True,
        "models": _CODEX_ALIASES,
        "auth_ok": auth_ok,
        "count": len(_CODEX_ALIASES),
    })


@bp.route("/api/providers/openai/logout", methods=["POST"])
@login_required
def openai_logout():
    """Remove Codex auth.json and reset provider."""
    if CODEX_AUTH_FILE.is_file():
        CODEX_AUTH_FILE.unlink()
    config = _read_config()
    config["active_provider"] = "anthropic"
    _write_config(config)
    return jsonify({"status": "ok"})
