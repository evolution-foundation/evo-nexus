"""Knowledge Base Flask blueprint — connection management + parser endpoints.

Endpoints (Step 1 scope — connections):
  GET    /api/knowledge/connections
  POST   /api/knowledge/connections
  GET    /api/knowledge/connections/<id>
  DELETE /api/knowledge/connections/<id>
  POST   /api/knowledge/connections/<id>/test
  POST   /api/knowledge/connections/<id>/configure
  POST   /api/knowledge/connections/<id>/migrate
  GET    /api/knowledge/connections/<id>/health

Endpoints (Step 2 scope — parsers):
  GET    /api/knowledge/parsers/status
  POST   /api/knowledge/parsers/install

All endpoints call assert_master_key() before any action so that missing
KNOWLEDGE_MASTER_KEY produces a clear 500 rather than a cryptic error.
"""

import logging
import os
import re
import sqlite3
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import current_user

from models import audit
from routes.auth_routes import require_permission

log = logging.getLogger(__name__)

bp = Blueprint("knowledge", __name__)

_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ---------------------------------------------------------------------------
# CSRF guard (defense layer 2 — pairs with SESSION_COOKIE_SAMESITE=Strict)
# ---------------------------------------------------------------------------

def _require_xhr() -> None:
    """Abort with 403 if the request lacks the X-Requested-With: XMLHttpRequest header.

    This is a lightweight CSRF mitigation: browsers cannot send custom headers
    cross-origin without triggering a CORS preflight (which our CORS config
    rejects for non-allowlisted origins). Combined with SESSION_COOKIE_SAMESITE=Strict
    this makes session-rider attacks impractical without requiring token plumbing.

    Exempted: requests carrying a Bearer token (DASHBOARD_API_TOKEN path) because
    those are already pre-shared-secret auth, not session-cookie auth.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return  # Bearer-authenticated calls are not session-rider candidates
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        abort(403, description="CSRF check failed: X-Requested-With header missing.")

_EMBEDDER_DEFAULTS = {
    "local": {
        "model": "paraphrase-multilingual-mpnet-base-v2",
        "vector_dim": 768,
    },
    "openai": {
        "model": "text-embedding-3-small",
        "vector_dim": 1536,
    },
    "gemini": {
        "model": "gemini-embedding-001",
        "vector_dim": 768,
    },
}

_ALLOWED_EMBEDDERS = set(_EMBEDDER_DEFAULTS.keys())
_ALLOWED_PARSERS = {"marker"}

# Gemini dim is orthogonal to the model (MRL): any of the two models can emit
# 768, 1536, or 3072-dim vectors. Dim is selected via KNOWLEDGE_GEMINI_DIM.
_GEMINI_ALLOWED_DIMS = {768, 1536, 3072}

_EMBEDDER_MODELS = {
    "local": [
        {"id": "paraphrase-multilingual-mpnet-base-v2", "dim": 768, "recommended": True},
    ],
    "openai": [
        {"id": "text-embedding-3-small", "dim": 1536, "recommended": True},
        {"id": "text-embedding-3-large", "dim": 3072},
        {"id": "text-embedding-ada-002", "dim": 1536, "legacy": True},
    ],
    "gemini": [
        {"id": "gemini-embedding-001", "dim": 768, "recommended": True,
         "supports_task_type": True,
         "note": "Text-only. Supports task_type for retrieval optimization."},
        {"id": "gemini-embedding-2-preview", "dim": 768,
         "supports_task_type": False,
         "preview": True,
         "note": "Multimodal preview. Task hint must be inline in the prompt."},
    ],
}

# Pattern for Google AI Studio API keys (AIzaSy + 33 chars).
_GEMINI_API_KEY_PATTERN = r"^AIzaSy[A-Za-z0-9_-]{33}$"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_path() -> str:
    return current_app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")


def _get_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _assert_key():
    """Fail-fast: assert KNOWLEDGE_MASTER_KEY before any handler runs."""
    from knowledge import assert_master_key
    assert_master_key()


# ---------------------------------------------------------------------------
# GET /api/knowledge/connections
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections", methods=["GET"])
@require_permission("knowledge", "view")
def list_connections():
    _assert_key()
    from knowledge.connections import list_connections as _list
    conn = _get_sqlite()
    try:
        return jsonify(_list(conn))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /api/knowledge/connections
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections", methods=["POST"])
@require_permission("knowledge", "manage")
def create_connection():
    _require_xhr()
    _assert_key()
    from knowledge.connections import create_connection as _create
    from knowledge.crypto import encrypt_secret, mask_connection_string

    data = request.get_json(force=True) or {}
    required = ("name", "slug")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    # Accept either a full connection_string or individual fields
    cs_plain = data.pop("connection_string", None)
    cs_enc = None
    masked = None
    if cs_plain:
        cs_enc = encrypt_secret(cs_plain)
        masked = mask_connection_string(cs_plain)

    row_data = {
        "name": data["name"],
        "slug": data["slug"],
        "host": data.get("host"),
        "port": data.get("port"),
        "database_name": data.get("database_name"),
        "username": data.get("username"),
        "ssl_mode": data.get("ssl_mode"),
        "connection_string_encrypted": cs_enc,
        "status": "disconnected",
    }

    conn = _get_sqlite()
    try:
        result = _create(conn, row_data)
        if masked:
            result["connection_string_masked"] = masked
        return jsonify(result), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /api/knowledge/connections/<id>
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<connection_id>", methods=["GET"])
@require_permission("knowledge", "view")
def get_connection(connection_id: str):
    _assert_key()
    from knowledge.connections import get_connection as _get, get_connection_events

    conn = _get_sqlite()
    try:
        row = _get(conn, connection_id)
        if row is None:
            return jsonify({"error": "Connection not found"}), 404
        row["events"] = get_connection_events(conn, connection_id, limit=20)
        return jsonify(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DELETE /api/knowledge/connections/<id>
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<connection_id>", methods=["DELETE"])
@require_permission("knowledge", "manage")
def delete_connection(connection_id: str):
    _require_xhr()
    _assert_key()
    from knowledge.connections import delete_connection as _delete
    from knowledge.connection_pool import dispose_engine

    conn = _get_sqlite()
    try:
        deleted = _delete(conn, connection_id)
        if not deleted:
            return jsonify({"error": "Connection not found"}), 404
        dispose_engine(connection_id)
        return jsonify({"deleted": True})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /api/knowledge/connections/<id>/test
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<connection_id>/test", methods=["POST"])
@require_permission("knowledge", "manage")
def test_connection(connection_id: str):
    """Quick connectivity test — SELECT 1 only, no migrations."""
    _assert_key()
    from knowledge.connections import get_connection as _get
    from knowledge.crypto import decrypt_secret
    from knowledge.connection_pool import get_engine
    from sqlalchemy import text
    import time

    conn = _get_sqlite()
    try:
        row = conn.execute(
            "SELECT connection_string_encrypted FROM knowledge_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Connection not found"}), 404

        cs_enc = row[0]
        if cs_enc is None:
            return jsonify({"error": "No connection string stored for this connection"}), 400

        cs = decrypt_secret(bytes(cs_enc))
        start = time.monotonic()
        engine = get_engine(connection_id, cs)
        with engine.connect() as pg_conn:
            pg_conn.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return jsonify({"ok": True, "latency_ms": latency_ms})

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 422
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /api/knowledge/connections/<id>/configure
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<connection_id>/configure", methods=["POST"])
@require_permission("knowledge", "manage")
def configure_connection(connection_id: str):
    """Full 'Connect & Configure' — validates Postgres + pgvector + runs Alembic."""
    _require_xhr()
    _assert_key()
    from knowledge.auto_migrator import configure_connection as _configure
    from knowledge.crypto import decrypt_secret

    sqlite_conn = _get_sqlite()
    try:
        row = sqlite_conn.execute(
            "SELECT connection_string_encrypted FROM knowledge_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Connection not found"}), 404

        cs_enc = row[0]
        if cs_enc is None:
            return jsonify({"error": "No connection string stored for this connection"}), 400

        cs = decrypt_secret(bytes(cs_enc))
        result = _configure(connection_id, cs, sqlite_conn)

        if result.get("status") == "ready":
            return jsonify(result)

        # Map specific error codes to HTTP status
        code = result.get("code", "configure_failed")
        if code in ("pgbouncer_blocked", "vector_dim_mismatch"):
            return jsonify(result), 422
        return jsonify(result), 500

    finally:
        sqlite_conn.close()


# ---------------------------------------------------------------------------
# POST /api/knowledge/connections/<id>/migrate
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<connection_id>/migrate", methods=["POST"])
@require_permission("knowledge", "manage")
def migrate_connection(connection_id: str):
    """Run pending Alembic migrations (idempotent — safe to call multiple times)."""
    _assert_key()
    from knowledge.auto_migrator import _run_alembic_upgrade, get_alembic_head
    from knowledge.connections import update_connection as _update
    from knowledge.crypto import decrypt_secret

    sqlite_conn = _get_sqlite()
    try:
        row = sqlite_conn.execute(
            "SELECT connection_string_encrypted FROM knowledge_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Connection not found"}), 404

        cs = decrypt_secret(bytes(row[0]))
        _run_alembic_upgrade(cs)
        head = get_alembic_head()
        _update(sqlite_conn, connection_id, {"status": "ready", "schema_version": head})
        return jsonify({"migrated": True, "schema_version": head})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        sqlite_conn.close()


# ---------------------------------------------------------------------------
# GET /api/knowledge/connections/<id>/health
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<connection_id>/health", methods=["GET"])
@require_permission("knowledge", "view")
def health_check(connection_id: str):
    """On-demand health check for a single connection."""
    _assert_key()
    from knowledge.health_check import check_connection_health
    from knowledge.crypto import decrypt_secret

    sqlite_conn = _get_sqlite()
    try:
        row = sqlite_conn.execute(
            "SELECT connection_string_encrypted FROM knowledge_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Connection not found"}), 404

        cs = decrypt_secret(bytes(row[0]))
        result = check_connection_health(connection_id, cs, sqlite_conn)
        return jsonify(result)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        sqlite_conn.close()


# ---------------------------------------------------------------------------
# GET /api/knowledge/parsers/status
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/parsers/status", methods=["GET"])
@require_permission("knowledge", "view")
def parser_status():
    """Return Marker model installation status."""
    _assert_key()
    from knowledge.parser_install import get_parser_status
    return jsonify(get_parser_status())


# ---------------------------------------------------------------------------
# POST /api/knowledge/parsers/install
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/parsers/install", methods=["POST"])
@require_permission("knowledge", "manage")
def parser_install():
    """Trigger Marker model download (ADR-002).

    Downloads Surya models (~500 MB) to ~/.cache/huggingface/.
    Creates sentinel ~/.cache/evonexus/marker_installed.ok on completion.
    Idempotent — returns "already_installed" if sentinel exists.
    """
    _assert_key()
    from knowledge.parser_install import download_marker_models
    from knowledge.parsers.marker_parser import MarkerNotInstalledError

    try:
        result = download_marker_models()
        return jsonify(result)
    except MarkerNotInstalledError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /api/knowledge/settings
# ---------------------------------------------------------------------------

def _read_env_clean(name: str, default: str = "") -> str:
    raw = os.environ.get(name, default)
    return raw.strip().strip('"').strip("'")


def _current_settings() -> dict:
    provider = (_read_env_clean("KNOWLEDGE_EMBEDDER_PROVIDER") or "local").lower()
    if provider not in _ALLOWED_EMBEDDERS:
        provider = "local"

    defaults = _EMBEDDER_DEFAULTS[provider]
    vector_dim = defaults["vector_dim"]

    if provider == "openai":
        model = _read_env_clean("KNOWLEDGE_OPENAI_MODEL") or defaults["model"]
    elif provider == "gemini":
        model = _read_env_clean("KNOWLEDGE_GEMINI_MODEL") or defaults["model"]
        raw_dim = _read_env_clean("KNOWLEDGE_GEMINI_DIM")
        if raw_dim:
            try:
                d = int(raw_dim)
                if d in _GEMINI_ALLOWED_DIMS:
                    vector_dim = d
            except ValueError:
                pass
    else:
        model = defaults["model"]

    parser = (_read_env_clean("KNOWLEDGE_DEFAULT_PARSER") or "marker").lower()
    if parser not in _ALLOWED_PARSERS:
        parser = "marker"

    # Detect if any connection exists — if so, embedder is locked.
    locked = False
    try:
        conn = _get_sqlite()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM knowledge_connections"
            ).fetchone()
            locked = bool(row and row[0] > 0)
        finally:
            conn.close()
    except sqlite3.Error:
        locked = False

    openai_key_set = bool(_read_env_clean("OPENAI_API_KEY"))
    # Gemini accepts either GEMINI_API_KEY (explicit) or GOOGLE_API_KEY
    # (the google-genai SDK default). Either one counts as "set".
    gemini_key_set = bool(
        _read_env_clean("GEMINI_API_KEY") or _read_env_clean("GOOGLE_API_KEY")
    )

    return {
        "embedder_provider": provider,
        "embedder_model": model,
        "vector_dim": vector_dim,
        "parser_default": parser,
        "locked": locked,
        "openai_api_key_set": openai_key_set,
        "gemini_api_key_set": gemini_key_set,
    }


@bp.route("/api/knowledge/settings", methods=["GET"])
@require_permission("knowledge", "view")
def get_settings():
    _assert_key()
    return jsonify(_current_settings())


# ---------------------------------------------------------------------------
# GET /api/knowledge/embedders/models
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/embedders/models", methods=["GET"])
@require_permission("knowledge", "view")
def list_embedder_models():
    """Return available models per provider for UI selection."""
    _assert_key()
    provider = (request.args.get("provider") or "").lower()
    if provider:
        if provider not in _ALLOWED_EMBEDDERS:
            return jsonify({"error": f"Invalid provider. Must be one of: {sorted(_ALLOWED_EMBEDDERS)}"}), 400
        return jsonify({"provider": provider, "models": _EMBEDDER_MODELS[provider]})
    return jsonify({"providers": _EMBEDDER_MODELS})


# ---------------------------------------------------------------------------
# PUT /api/knowledge/settings
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/settings", methods=["PUT"])
@require_permission("knowledge", "manage")
def update_settings():
    """Persist embedder provider / model / default parser to .env.

    Embedder provider can only change while there are no connections — otherwise
    returns 409. Parser default is always mutable.
    """
    _require_xhr()
    _assert_key()
    from routes.integrations import _upsert_env_vars

    data = request.get_json(silent=True) or {}
    current = _current_settings()

    kvs: dict[str, str] = {}

    # --- Embedder provider ---
    provider = data.get("embedder_provider")
    if provider is not None:
        provider = str(provider).lower()
        if provider not in _ALLOWED_EMBEDDERS:
            return jsonify({"error": f"Invalid embedder_provider. Must be one of: {sorted(_ALLOWED_EMBEDDERS)}"}), 400
        if provider != current["embedder_provider"] and current["locked"]:
            return jsonify({
                "error": "Embedder provider is locked because connections already exist. To change the provider, remove all connections and recreate them (reindex feature planned for v0.25.1).",
                "code": "embedder_locked",
            }), 409
        kvs["KNOWLEDGE_EMBEDDER_PROVIDER"] = provider
    else:
        provider = current["embedder_provider"]

    # --- Embedder model (only meaningful for openai/gemini) ---
    model = data.get("embedder_model")
    if model is not None:
        model = str(model).strip()
        if model:
            allowed_ids = {m["id"] for m in _EMBEDDER_MODELS.get(provider, [])}
            if allowed_ids and model not in allowed_ids:
                return jsonify({
                    "error": f"Invalid model '{model}' for provider '{provider}'. Allowed: {sorted(allowed_ids)}",
                }), 400
            if provider == "openai":
                kvs["KNOWLEDGE_OPENAI_MODEL"] = model
            elif provider == "gemini":
                kvs["KNOWLEDGE_GEMINI_MODEL"] = model

    # --- Parser default ---
    parser = data.get("parser_default")
    if parser is not None:
        parser = str(parser).lower()
        if parser not in _ALLOWED_PARSERS:
            return jsonify({"error": f"Invalid parser_default. Must be one of: {sorted(_ALLOWED_PARSERS)}"}), 400
        kvs["KNOWLEDGE_DEFAULT_PARSER"] = parser

    # --- OpenAI API key (only accepted when provider is openai) ---
    openai_key = data.get("openai_api_key")
    if openai_key is not None:
        openai_key = str(openai_key).strip()
        if openai_key:
            if provider != "openai":
                return jsonify({
                    "error": "openai_api_key can only be set when embedder_provider is 'openai'.",
                }), 400
            if not openai_key.startswith("sk-"):
                return jsonify({"error": "OpenAI API key must start with 'sk-'."}), 400
            kvs["OPENAI_API_KEY"] = openai_key

    # --- Gemini API key (only accepted when provider is gemini) ---
    gemini_key = data.get("gemini_api_key")
    if gemini_key is not None:
        gemini_key = str(gemini_key).strip()
        if gemini_key:
            if provider != "gemini":
                return jsonify({
                    "error": "gemini_api_key can only be set when embedder_provider is 'gemini'.",
                }), 400
            if not re.match(_GEMINI_API_KEY_PATTERN, gemini_key):
                return jsonify({
                    "error": "Gemini API key must match the Google AI Studio "
                             "pattern 'AIzaSy...' (39 chars total).",
                }), 400
            kvs["GEMINI_API_KEY"] = gemini_key

    # --- Gemini dim (MRL: 768, 1536, or 3072; only for provider=gemini) ---
    gemini_dim = data.get("gemini_dim")
    if gemini_dim is not None:
        try:
            d = int(gemini_dim)
        except (TypeError, ValueError):
            return jsonify({"error": "gemini_dim must be an integer."}), 400
        if d not in _GEMINI_ALLOWED_DIMS:
            return jsonify({
                "error": f"gemini_dim must be one of {sorted(_GEMINI_ALLOWED_DIMS)}.",
            }), 400
        if provider != "gemini":
            return jsonify({
                "error": "gemini_dim can only be set when embedder_provider is 'gemini'.",
            }), 400
        kvs["KNOWLEDGE_GEMINI_DIM"] = str(d)

    if not kvs:
        return jsonify(_current_settings())

    env_path = _WORKSPACE_ROOT / ".env"
    _upsert_env_vars(env_path, kvs, section_comment="knowledge-settings")

    # Update in-process env so subsequent requests see the change without restart.
    # NOTE: assumes single-process deployment (app.py app.run). Under gunicorn
    # multi-worker mode, other workers keep stale env until restart.
    for k, v in kvs.items():
        os.environ[k] = v

    # Audit: record who changed which keys (values are never logged).
    try:
        audit(
            current_user,
            "update_settings",
            "knowledge",
            f"keys={sorted(kvs.keys())}",
        )
    except Exception:
        # Audit failure must never block the settings write.
        log.warning("knowledge.update_settings: audit() failed (non-fatal)", exc_info=True)

    return jsonify(_current_settings())
