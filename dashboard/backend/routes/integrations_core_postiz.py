"""PUT/POST/GET /api/integrations/core/postiz — admin config for the Postiz
publish bridge (briefing Etapa 13).

Persists into the same WORKSPACE/.env file already used by
routes/integrations.py's custom/plugin integration config (via
_upsert_env_vars) — POSTIZ_URL/POSTIZ_API_KEY/etc. are already read from
os.environ everywhere else in the codebase (postiz_client.PostizClient.from_env,
heartbeat_outcome, routes/integrations.py's INTEGRATIONS registry). This
endpoint does not introduce a second source of truth for those values — it
adds masking, "keep current on masked submit", SSRF-guarded URL validation,
and a test-connection action on top of the existing .env persistence.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request
from flask_login import current_user

from models import audit
from routes._helpers import WORKSPACE
from routes.integrations import _upsert_env_vars
from routes.knowledge import _require_xhr
from postiz_client import PostizClient

bp = Blueprint("integrations_core_postiz", __name__)

_ALLOWED_KEYS = (
    "POSTIZ_URL",
    "POSTIZ_API_KEY",
    "POSTIZ_INTEGRATION_INSTAGRAM_ID",
    "POSTIZ_INTEGRATION_YOUTUBE_ID",
    "POSTIZ_INTEGRATION_LINKEDIN_ID",
    "POSTIZ_INTEGRATION_TIKTOK_ID",
    "POSTIZ_REQUEST_TIMEOUT_SECONDS",
    "POSTIZ_UPLOAD_TIMEOUT_SECONDS",
    "SOCIAL_DEFAULT_POST_MODE",
    "MEDIA_TIMEZONE",
)
_SECRET_KEYS = frozenset({"POSTIZ_API_KEY"})
_MASK = "****"


def _require_admin():
    """Stricter than the generic RBAC resource check — this endpoint can
    write POSTIZ_API_KEY, so briefing Etapa 13 explicitly requires
    'acesso apenas administrativo', not just integrations:manage.
    """
    _require_xhr()
    if not current_user.is_authenticated or current_user.role != "admin":
        return jsonify({"error": "Forbidden — apenas administradores."}), 403
    return None


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) < 8:
        return _MASK
    return value[:4] + _MASK + value[-4:]


def _is_private_host(hostname: str) -> bool:
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    try:
        resolved = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(resolved)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except (socket.gaierror, ValueError):
        return False


def _validate_postiz_url(url: str) -> str | None:
    """Returns an error string, or None if the URL is acceptable.

    SSRF guard (briefing Etapa 13: "bloqueio de URLs internas arbitrárias
    ... exceto hosts explicitamente permitidos"): HTTPS required, and the
    resolved host must not be private/loopback/link-local unless it is
    explicitly allowlisted via POSTIZ_URL_ALLOWED_INTERNAL_HOSTS (comma-
    separated) — needed for self-hosted Postiz reachable only over an
    internal Swarm network alias.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "POSTIZ_URL deve ser HTTPS."
    if not parsed.hostname:
        return "POSTIZ_URL inválida."
    allowed_internal = {
        h.strip().lower()
        for h in os.environ.get("POSTIZ_URL_ALLOWED_INTERNAL_HOSTS", "").split(",")
        if h.strip()
    }
    if parsed.hostname.lower() in allowed_internal:
        return None
    if _is_private_host(parsed.hostname):
        return (
            f"POSTIZ_URL resolve para um host interno/privado ({parsed.hostname}) — "
            "isso não é permitido a menos que o host esteja em "
            "POSTIZ_URL_ALLOWED_INTERNAL_HOSTS."
        )
    return None


def _current_masked_config() -> dict:
    values = {k: os.environ.get(k, "") for k in _ALLOWED_KEYS}
    return {k: (_mask_secret(v) if k in _SECRET_KEYS else v) for k, v in values.items()}


@bp.route("/api/integrations/core/postiz", methods=["GET"])
def get_postiz_config():
    denied = _require_admin()
    if denied:
        return denied
    configured = bool(os.environ.get("POSTIZ_URL")) and bool(os.environ.get("POSTIZ_API_KEY"))
    return jsonify({"config": _current_masked_config(), "configured": configured})


@bp.route("/api/integrations/core/postiz", methods=["PUT"])
def put_postiz_config():
    denied = _require_admin()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}

    updates: dict[str, str] = {}
    for key in _ALLOWED_KEYS:
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, str):
            return jsonify({"error": f"{key} deve ser uma string."}), 400
        if key in _SECRET_KEYS and (_MASK in value or value == ""):
            continue  # masked or blank submit — keep the currently stored secret
        updates[key] = value

    if "POSTIZ_URL" in updates:
        error = _validate_postiz_url(updates["POSTIZ_URL"])
        if error:
            return jsonify({"error": error}), 400
        updates["POSTIZ_URL"] = updates["POSTIZ_URL"].rstrip("/")

    for key in ("POSTIZ_REQUEST_TIMEOUT_SECONDS", "POSTIZ_UPLOAD_TIMEOUT_SECONDS"):
        if key in updates:
            try:
                float(updates[key])
            except ValueError:
                return jsonify({"error": f"{key} deve ser numérico."}), 400

    if "SOCIAL_DEFAULT_POST_MODE" in updates and updates["SOCIAL_DEFAULT_POST_MODE"] not in ("draft", "schedule"):
        return jsonify({"error": "SOCIAL_DEFAULT_POST_MODE deve ser 'draft' ou 'schedule'."}), 400

    if not updates:
        return jsonify({"error": "Nenhum campo válido para atualizar."}), 400

    env_path = WORKSPACE / ".env"
    _upsert_env_vars(env_path, updates, section_comment="core-postiz (social-media-production)")
    try:
        env_path.chmod(0o600)  # restrict permission — briefing: "arquivo com permissão restrita"
    except OSError:
        pass

    # Reload dotenv in-process (same idiom as configure_plugin_integration)
    # so this dashboard process picks up the change without a restart.
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path, override=True)
    except Exception:
        pass
    for key, value in updates.items():
        os.environ[key] = value

    audit(current_user, "manage", "integrations", f"updated core postiz config: keys={sorted(updates.keys())}")

    configured = bool(os.environ.get("POSTIZ_URL")) and bool(os.environ.get("POSTIZ_API_KEY"))
    return jsonify({
        "config": _current_masked_config(),
        "configured": configured,
        "note": (
            "Valores aplicados neste processo do dashboard imediatamente. "
            "Outros serviços do Swarm (media-worker, scheduler, telegram) leem "
            "estas mesmas variáveis do arquivo .env no boot — reinicie-os "
            "(docker service update) se precisarem do novo valor."
        ),
    })


@bp.route("/api/integrations/core/postiz/test", methods=["POST"])
def test_postiz_connection():
    denied = _require_admin()
    if denied:
        return denied
    client = PostizClient.from_env()
    if client is None:
        return jsonify({"ok": False, "detail": "POSTIZ_URL/POSTIZ_API_KEY não configurados."}), 400
    result = client.test_connection()
    audit(current_user, "execute", "integrations", f"tested postiz connection: ok={result.get('ok')}")
    return jsonify(result), (200 if result.get("ok") else 502)
