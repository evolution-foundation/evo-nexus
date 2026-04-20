"""Thread-safe SQLAlchemy engine cache keyed by connection_id.

Each Knowledge connection gets its own QueuePool (psycopg2) with conservative
sizing (pool_size=3, max_overflow=2) per ADR-004. Engines are lazily created on
first use and evicted after 1800 s of inactivity.

Public API:
    get_engine(connection_id, connection_string) -> Engine
    dispose_engine(connection_id)
    gc_idle_engines()    — called by a background timer every 60 s
"""

import threading
import time
from typing import Dict, Optional, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
# { connection_id: (engine, last_used_ts) }
_engines: Dict[str, Tuple[Engine, float]] = {}

_GC_INTERVAL_S = 60
_TTL_S = 1800  # 30 min idle → dispose

_gc_timer: Optional[threading.Timer] = None
_gc_started = False
_gc_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def _create_engine_for(connection_string: str) -> Engine:
    """Create a new SQLAlchemy engine with Knowledge-tuned pool settings (ADR-004)."""
    return create_engine(
        connection_string,
        pool_size=3,
        max_overflow=2,
        pool_recycle=1800,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 10,
            "application_name": "evonexus-knowledge",
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_engine(connection_id: str, connection_string: str) -> Engine:
    """Return a cached engine for *connection_id*, creating one if absent."""
    with _lock:
        if connection_id in _engines:
            engine, _ = _engines[connection_id]
            _engines[connection_id] = (engine, time.monotonic())
            return engine
        engine = _create_engine_for(connection_string)
        _engines[connection_id] = (engine, time.monotonic())
        return engine


def dispose_engine(connection_id: str) -> None:
    """Remove and dispose the engine for *connection_id* if it exists."""
    with _lock:
        entry = _engines.pop(connection_id, None)
    if entry:
        try:
            entry[0].dispose()
        except Exception:
            pass


def gc_idle_engines() -> None:
    """Dispose engines idle for more than TTL_S seconds."""
    now = time.monotonic()
    stale = []
    with _lock:
        for cid, (engine, last_used) in list(_engines.items()):
            if now - last_used > _TTL_S:
                stale.append((cid, engine))
                del _engines[cid]
    for cid, engine in stale:
        try:
            engine.dispose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background GC timer (start once, re-arms itself)
# ---------------------------------------------------------------------------

def _gc_loop() -> None:
    gc_idle_engines()
    _schedule_gc()


def _schedule_gc() -> None:
    global _gc_timer
    _gc_timer = threading.Timer(_GC_INTERVAL_S, _gc_loop)
    _gc_timer.daemon = True
    _gc_timer.start()


def start_gc_thread() -> None:
    """Start the background GC timer (idempotent — safe to call multiple times)."""
    global _gc_started
    with _gc_lock:
        if _gc_started:
            return
        _gc_started = True
    _schedule_gc()


# ---------------------------------------------------------------------------
# Utility: pre-check max_connections before registering a connection
# ---------------------------------------------------------------------------

def _resolve_sqlite_db_path() -> str:
    """Locate the EvoNexus SQLite DB, in order of preference.

    1. Flask current_app.config["SQLALCHEMY_DATABASE_URI"] (the real source)
    2. SQLALCHEMY_DATABASE_URI env var (dev/test override)
    3. Derived from workspace root: <workspace>/dashboard/data/evonexus.db

    Always returns an absolute filesystem path (not a sqlite:// URI).
    """
    import os
    from pathlib import Path

    # 1. Flask app config — authoritative when running inside a request.
    try:
        from flask import current_app
        uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if uri:
            return uri.replace("sqlite:///", "")
    except RuntimeError:
        # Outside Flask app context (CLI, worker) — fall through.
        pass

    # 2. Env var override.
    uri = os.environ.get("SQLALCHEMY_DATABASE_URI", "")
    if uri:
        return uri.replace("sqlite:///", "")

    # 3. Derive from this file's location:
    # dashboard/backend/knowledge/connection_pool.py → workspace/dashboard/data/evonexus.db
    workspace = Path(__file__).resolve().parent.parent.parent.parent
    return str(workspace / "dashboard" / "data" / "evonexus.db")


def get_dsn(connection_id: str) -> str:
    """Resolve the plaintext DSN for *connection_id* from the SQLite store.

    Reads ``connection_string_encrypted`` from ``knowledge_connections``, decrypts
    it via ``crypto.decrypt_secret``, and returns the plaintext DSN.

    Raises ``KeyError`` if the connection is not found.
    Raises ``ValueError`` if no connection string is stored for this connection.
    """
    import sqlite3

    from knowledge.crypto import decrypt_secret

    db_path = _resolve_sqlite_db_path()
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT connection_string_encrypted FROM knowledge_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise KeyError(f"Knowledge connection '{connection_id}' not found in local store.")
    if row[0] is None:
        raise ValueError(
            f"No connection string stored for connection_id='{connection_id}'. "
            "Configure the connection via POST /api/knowledge/connections/<id>/configure."
        )
    return decrypt_secret(bytes(row[0]))


def check_max_connections(engine: Engine, n_existing_connections: int) -> Optional[str]:
    """Return a warning string if the pool footprint > 50% of max_connections.

    Returns None if OK, or a human-readable warning string to surface in the UI.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT current_setting('max_connections')")).fetchone()
            max_conn = int(row[0])
        # Each connection uses pool_size + max_overflow = 5 conn max
        total_pool = (n_existing_connections + 1) * 5
        if total_pool > max_conn * 0.5:
            return (
                f"Warning: this Postgres has max_connections={max_conn} and you already have "
                f"{n_existing_connections} Knowledge connection(s) configured. "
                f"Total pool footprint will be {total_pool} connections "
                f"({total_pool / max_conn * 100:.0f}% of max). "
                "Consider increasing max_connections or using a separate database."
            )
    except Exception:
        pass  # Non-fatal — warning only
    return None
