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

import sqlite3
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from routes.auth_routes import require_permission

bp = Blueprint("knowledge", __name__)


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
