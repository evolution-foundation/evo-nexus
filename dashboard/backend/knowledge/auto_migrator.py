"""Knowledge Base auto-migrator — "Connect & Configure" 1-click flow.

Implements the full configure pipeline:
  1. Detect pgbouncer transaction-pool (ADR-009) — block with 422
  2. Basic connectivity check (SELECT 1)
  3. Postgres >= 14 version check
  4. pgvector >= 0.5 extension check (or CREATE EXTENSION IF NOT EXISTS)
  5. vector_dim mismatch check (ADR-001)
  6. Alembic `upgrade head` — idempotent
  7. Seed knowledge_config if empty
  8. Drift detection: check current alembic revision vs HEAD

Public API:
    configure_connection(connection_id, connection_string, db_conn) -> dict
    check_drift(connection_id, connection_string) -> dict
    get_alembic_head() -> str
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text

from .connection_pool import get_engine

# ---------------------------------------------------------------------------
# Alembic migrations directory
# ---------------------------------------------------------------------------
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _get_alembic_config(connection_string: str):
    """Return an Alembic Config object configured for *connection_string*."""
    from alembic.config import Config

    cfg = Config(str(_MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", connection_string)
    return cfg


# ---------------------------------------------------------------------------
# ADR-009: pgbouncer transaction-pool detection
# ---------------------------------------------------------------------------

def detect_pgbouncer_transaction_pool(cs: str) -> tuple[bool, str]:
    """Return (is_pgbouncer, reason) — 3 heuristics in OR.

    Blocks port 6543 (Supabase pooler), hostname containing 'pooler', and
    explicit query param pgbouncer=true.
    """
    try:
        parsed = urlparse(cs)
    except Exception:
        return (False, "")

    # Heuristic 1: port 6543 (Supabase/Neon default transaction pooler)
    if parsed.port == 6543:
        return (True, "port 6543 (Supabase transaction pooler)")

    # Heuristic 2: hostname contains 'pooler'
    hostname = (parsed.hostname or "").lower()
    if "pooler" in hostname:
        return (True, f"hostname contains 'pooler': {parsed.hostname}")

    # Heuristic 3: explicit query param pgbouncer=true
    qs = parse_qs(parsed.query)
    if qs.get("pgbouncer", [None])[0] == "true":
        return (True, "query param pgbouncer=true")

    return (False, "")


# ---------------------------------------------------------------------------
# Helper: record an audit event in knowledge_connection_events (SQLite)
# ---------------------------------------------------------------------------

def _record_event(
    sqlite_conn,
    connection_id: str,
    event_type: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Insert a row into knowledge_connection_events (SQLite local DB)."""
    import json
    import uuid

    sqlite_conn.execute(
        """INSERT INTO knowledge_connection_events (connection_id, event_type, details)
           VALUES (?, ?, ?)""",
        (connection_id, event_type, json.dumps(details or {})),
    )
    sqlite_conn.commit()


def _update_connection_status(
    sqlite_conn,
    connection_id: str,
    status: str,
    last_error: Optional[str] = None,
    extra: Optional[Dict[str, str]] = None,
) -> None:
    """Update knowledge_connections.status (and optional columns) in SQLite."""
    fields = {"status": status}
    if last_error is not None:
        fields["last_error"] = last_error
    if extra:
        fields.update(extra)

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [connection_id]
    sqlite_conn.execute(
        f"UPDATE knowledge_connections SET {set_clause} WHERE id = ?",
        values,
    )
    sqlite_conn.commit()


# ---------------------------------------------------------------------------
# Alembic head revision
# ---------------------------------------------------------------------------

def get_alembic_head() -> str:
    """Return the HEAD revision from the local Alembic script directory."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    scripts = ScriptDirectory.from_config(cfg)
    return scripts.get_current_head()


# ---------------------------------------------------------------------------
# configure_connection — full "Connect & Configure" pipeline
# ---------------------------------------------------------------------------

def configure_connection(
    connection_id: str,
    connection_string: str,
    sqlite_conn,
) -> Dict[str, Any]:
    """Run the full configure pipeline.

    Returns a dict:
      {"status": "ready", ...}  — success
      {"status": "error", "error": "...", "code": "...", ...}  — failure

    *sqlite_conn* is an open sqlite3.Connection for local metadata updates.
    """
    from .crypto import decrypt_secret

    # ---------------------------------------------------------------
    # Step 0: decrypt if needed (bytes stored in DB → plaintext URL)
    # ---------------------------------------------------------------
    if isinstance(connection_string, (bytes, memoryview)):
        try:
            connection_string = decrypt_secret(bytes(connection_string))
        except Exception as exc:
            _update_connection_status(sqlite_conn, connection_id, "error", str(exc))
            return {"status": "error", "error": str(exc), "code": "decrypt_failed"}

    # ---------------------------------------------------------------
    # Step 1: pgbouncer detection (ADR-009)
    # ---------------------------------------------------------------
    is_pooler, reason = detect_pgbouncer_transaction_pool(connection_string)
    if is_pooler:
        msg = (
            "Knowledge is not compatible with PgBouncer in transaction pooling mode. "
            f"Reason: {reason}. "
            "Alembic migrations, CREATE INDEX ... USING hnsw, and SQLAlchemy prepared "
            "statements require session mode. Use the direct connection string "
            "(Supabase exposes it on port 5432, not 6543). "
            "If you need pooling, use session pooler (a separate endpoint)."
        )
        _update_connection_status(sqlite_conn, connection_id, "error", msg)
        _record_event(sqlite_conn, connection_id, "pgbouncer_detected", {"reason": reason})
        return {
            "status": "error",
            "error": "pgbouncer_transaction_mode_unsupported",
            "reason": reason,
            "message": msg,
            "docs": "https://supabase.com/docs/guides/database/connecting-to-postgres#connection-pooler",
            "code": "pgbouncer_blocked",
        }

    # ---------------------------------------------------------------
    # Step 1.5: auto-create database if it doesn't exist
    # EvoNexus creates the target DB when user has CREATEDB permission.
    # Falls back to a clear 422 if not permitted.
    # ---------------------------------------------------------------
    try:
        _ensure_database_exists(connection_string)
    except _DatabaseCreationError as exc:
        _update_connection_status(sqlite_conn, connection_id, "error", str(exc))
        _record_event(sqlite_conn, connection_id, "create_db_failed", {"error": str(exc)})
        return {
            "status": "error",
            "error": str(exc),
            "code": "database_create_failed",
        }

    # ---------------------------------------------------------------
    # Step 2-7: connect and run migrations
    # ---------------------------------------------------------------
    try:
        engine = get_engine(connection_id, connection_string)

        with engine.connect() as conn:
            # Step 2: basic connectivity
            conn.execute(text("SELECT 1"))

            # Step 3: Postgres version >= 14
            pg_version_row = conn.execute(text("SELECT version()")).fetchone()
            pg_version_str = pg_version_row[0] if pg_version_row else ""
            # version() returns e.g. "PostgreSQL 15.2 on ..."
            import re
            m = re.search(r"PostgreSQL (\d+)", pg_version_str)
            pg_major = int(m.group(1)) if m else 0
            if pg_major < 14:
                raise ValueError(
                    f"Postgres >= 14 required; found version: {pg_version_str}"
                )

            # Step 4: pgvector extension
            ext_row = conn.execute(
                text(
                    "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                )
            ).fetchone()
            if ext_row is None:
                # Try to create it
                try:
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                    conn.commit()
                    ext_row = conn.execute(
                        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                    ).fetchone()
                except Exception as ext_exc:
                    raise ValueError(
                        f"pgvector extension is not installed and could not be created: {ext_exc}. "
                        "Run `CREATE EXTENSION vector;` as a superuser on the target database."
                    )
            if ext_row is None:
                raise ValueError(
                    "pgvector extension not found. Run `CREATE EXTENSION vector;` as superuser."
                )

            pgvector_version = ext_row[0]

            # Check pgvector >= 0.5 (major.minor comparison)
            pv_parts = pgvector_version.split(".")
            if len(pv_parts) >= 2:
                pv_minor = float(f"{pv_parts[0]}.{pv_parts[1]}")
                if pv_minor < 0.5:
                    raise ValueError(
                        f"pgvector >= 0.5 required for HNSW index support; found {pgvector_version}"
                    )

            # Step 5: vector_dim mismatch (ADR-001)
            evonexus_dim = _get_evonexus_dim(conn)
            table_exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'knowledge_config'"
                )
            ).fetchone()

            if table_exists:
                row = conn.execute(
                    text("SELECT vector_dim FROM knowledge_config WHERE id = 1")
                ).fetchone()
                if row and row[0] != evonexus_dim:
                    remote_dim = row[0]
                    err = {
                        "error": "vector_dim_mismatch",
                        "remote_dim": remote_dim,
                        "evonexus_dim": evonexus_dim,
                        "message": (
                            f"Remote Postgres was already initialized with vector_dim={remote_dim}. "
                            f"EvoNexus is configured with vector_dim={evonexus_dim}. "
                            "Options: (i) use an empty database, or "
                            "(ii) manually execute `DROP TABLE knowledge_config CASCADE` "
                            "on the remote Postgres and click 'Connect & Configure' again. "
                            "EvoNexus does not modify a non-empty knowledge_config."
                        ),
                        "code": "vector_dim_mismatch",
                    }
                    _update_connection_status(
                        sqlite_conn, connection_id, "error",
                        err["message"],
                        {
                            "pgvector_version": pgvector_version,
                            "postgres_version": pg_version_str[:100],
                        },
                    )
                    _record_event(
                        sqlite_conn, connection_id, "vector_dim_mismatch",
                        {"remote_dim": remote_dim, "evonexus_dim": evonexus_dim},
                    )
                    return {"status": "error", **err}

        # Step 6: run Alembic upgrade head
        _run_alembic_upgrade(connection_string)

        # Step 7: record success
        head = get_alembic_head()
        _update_connection_status(
            sqlite_conn, connection_id, "ready", None,
            {
                "schema_version": head or "001",
                "pgvector_version": pgvector_version,
                "postgres_version": pg_version_str[:100],
                "last_error": None,
            },
        )
        _record_event(sqlite_conn, connection_id, "configured", {"schema_version": head})
        return {"status": "ready", "schema_version": head, "pgvector_version": pgvector_version}

    except Exception as exc:
        error_msg = str(exc)
        _update_connection_status(sqlite_conn, connection_id, "error", error_msg)
        _record_event(sqlite_conn, connection_id, "configure_error", {"error": error_msg})
        return {"status": "error", "error": error_msg, "code": "configure_failed"}


_OPENAI_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
_PROVIDER_DEFAULT_DIMS = {"local": 768, "openai": 1536}


def _clean(name: str) -> str:
    raw = os.environ.get(name, "")
    return raw.strip().strip('"').strip("'")


def _get_evonexus_dim(conn) -> int:
    """Resolve expected vector dim from the active embedder provider/model."""
    provider = (_clean("KNOWLEDGE_EMBEDDER_PROVIDER") or "local").lower()
    if provider == "openai":
        model = _clean("KNOWLEDGE_OPENAI_MODEL") or "text-embedding-3-small"
        return _OPENAI_MODEL_DIMS.get(model, 1536)
    return _PROVIDER_DEFAULT_DIMS.get(provider, 768)


def _run_alembic_upgrade(connection_string: str) -> None:
    """Run `alembic upgrade head` against *connection_string*."""
    from alembic import command

    cfg = _get_alembic_config(connection_string)
    command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# check_drift — compare remote alembic_version vs local HEAD
# ---------------------------------------------------------------------------

def check_drift(connection_id: str, connection_string: str, sqlite_conn) -> Dict[str, Any]:
    """Check if the remote schema is behind the local Alembic HEAD.

    Returns {"needs_migration": bool, "remote_rev": str, "head": str}.
    Updates connection status to 'needs_migration' or 'version_mismatch_future' if drift.
    """
    try:
        engine = get_engine(connection_id, connection_string)
        head = get_alembic_head()

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).fetchone()
            remote_rev = row[0] if row else None

        if remote_rev is None:
            # alembic_version table not present → needs full migration
            _update_connection_status(sqlite_conn, connection_id, "needs_migration")
            _record_event(
                sqlite_conn, connection_id, "drift_detected",
                {"remote_rev": None, "head": head},
            )
            return {"needs_migration": True, "remote_rev": None, "head": head}

        if remote_rev == head:
            return {"needs_migration": False, "remote_rev": remote_rev, "head": head}

        # Determine direction: compare revision numbers lexicographically
        # (works for sequential int-prefixed revisions like "001", "002")
        if remote_rev > head:
            # Remote is newer — EvoNexus is outdated
            _update_connection_status(
                sqlite_conn, connection_id, "version_mismatch_future",
                f"Remote schema ({remote_rev}) is newer than this EvoNexus ({head}). "
                "Upgrade EvoNexus or use a different connection.",
            )
            return {
                "needs_migration": False,
                "remote_rev": remote_rev,
                "head": head,
                "status": "version_mismatch_future",
            }

        # Remote is behind — needs migration
        _update_connection_status(sqlite_conn, connection_id, "needs_migration")
        _record_event(
            sqlite_conn, connection_id, "drift_detected",
            {"remote_rev": remote_rev, "head": head},
        )
        return {"needs_migration": True, "remote_rev": remote_rev, "head": head}

    except Exception as exc:
        # If we can't reach the DB, mark as disconnected (don't mark needs_migration)
        _update_connection_status(
            sqlite_conn, connection_id, "disconnected", str(exc)
        )
        return {"needs_migration": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Auto-create database — one-click setup
# ---------------------------------------------------------------------------

class _DatabaseCreationError(Exception):
    """Raised when the target database does not exist and could not be created."""


def _ensure_database_exists(connection_string: str) -> None:
    """Ensure the target database exists; create it if the user has CREATEDB permission.

    Strategy:
      1. Try to connect to the target database directly. If it succeeds, no-op.
      2. If connection fails with 'database "X" does not exist', connect instead to
         the maintenance database 'postgres' on the same server with the same
         credentials and run CREATE DATABASE.
      3. If the role lacks CREATEDB permission (or 'postgres' DB also doesn't exist),
         raise _DatabaseCreationError with an actionable message.
    """
    import psycopg2
    from psycopg2 import sql
    from sqlalchemy.engine.url import make_url

    url = make_url(connection_string)
    target_db = url.database
    if not target_db:
        raise _DatabaseCreationError(
            "Connection string is missing the database name."
        )

    # Step 1: try the target database directly
    try:
        conn = psycopg2.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            dbname=target_db,
            connect_timeout=10,
        )
        conn.close()
        return  # target DB exists and we can connect — done
    except psycopg2.OperationalError as exc:
        msg = str(exc)
        if "does not exist" not in msg or f'"{target_db}"' not in msg:
            # Not a missing-database error — re-raise so the outer flow surfaces it
            raise

    # Step 2: connect to maintenance DB 'postgres' and try CREATE DATABASE
    try:
        admin_conn = psycopg2.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            dbname="postgres",
            connect_timeout=10,
        )
    except psycopg2.OperationalError as exc:
        raise _DatabaseCreationError(
            f"Database '{target_db}' does not exist, and EvoNexus could not reach "
            f"the maintenance database 'postgres' to create it: {exc}. "
            "Create the database manually or use a user with access to 'postgres'."
        )

    try:
        admin_conn.autocommit = True  # CREATE DATABASE cannot run in a transaction
        with admin_conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
    except psycopg2.errors.InsufficientPrivilege:
        raise _DatabaseCreationError(
            f"User '{url.username}' does not have CREATEDB permission. "
            f"Ask a DBA to run: CREATE DATABASE {target_db}; "
            "or grant CREATEDB to the user: ALTER ROLE "
            f"{url.username} CREATEDB;"
        )
    except psycopg2.errors.DuplicateDatabase:
        # Race: created by something else between step 1 and step 2 — fine
        pass
    except Exception as exc:
        raise _DatabaseCreationError(
            f"Failed to create database '{target_db}': {exc}"
        )
    finally:
        admin_conn.close()
