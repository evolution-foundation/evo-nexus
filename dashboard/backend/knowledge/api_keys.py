"""Knowledge API key CRUD — creation, verification, and revocation.

Token format: ``evo_k_<8char_prefix>.<base64url_secret>``

The prefix is stored as plain-text for O(1) lookup; only the full token is
bcrypt-hashed (rounds=12).  The plain token is returned exactly once, at
creation time.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import uuid
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from typing import Any

import bcrypt

# ---------------------------------------------------------------------------
# DB path helpers — mirrors app.py's resolution so both use the same file.
# ---------------------------------------------------------------------------

def _db_path() -> str:
    # Reuse the single source of truth so we never drift from app.py / get_dsn.
    from knowledge.connection_pool import _resolve_sqlite_db_path
    return _resolve_sqlite_db_path()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------

_PREFIX_LEN = 8
_SECRET_BYTES = 33  # ceil(44 * 6/8) — produces exactly 44 base64url chars


def _generate_token() -> tuple[str, str, str]:
    """Return (full_token, prefix, token_hash).

    full_token  — shown to the user exactly once
    prefix      — first 8 chars after ``evo_k_``, stored plain for lookup
    token_hash  — bcrypt digest of full_token, stored in DB
    """
    prefix = secrets.token_urlsafe(_PREFIX_LEN)[:_PREFIX_LEN]
    secret = urlsafe_b64encode(os.urandom(_SECRET_BYTES)).rstrip(b"=").decode()[:44]
    full_token = f"evo_k_{prefix}.{secret}"
    hashed = bcrypt.hashpw(full_token.encode(), bcrypt.gensalt(rounds=12)).decode()
    return full_token, prefix, hashed


# ---------------------------------------------------------------------------
# Ensure table exists
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_api_keys (
    id TEXT PRIMARY KEY,
    name TEXT,
    prefix TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    space_ids TEXT NOT NULL DEFAULT '[]',
    scopes TEXT NOT NULL DEFAULT '["read"]',
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    rate_limit_per_day INTEGER NOT NULL DEFAULT 10000,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_kak_prefix ON knowledge_api_keys(prefix);
"""


def ensure_table() -> None:
    """Idempotent — safe to call multiple times."""
    with _connect() as conn:
        conn.executescript(_CREATE_TABLE)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def _now() -> str:
    # Use SQLite-compatible format for datetime comparisons (datetime('now') returns this format)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def create_api_key(
    *,
    name: str | None,
    connection_id: str,
    space_ids: list[str] | None = None,
    scopes: list[str] | None = None,
    rate_limit_per_min: int = 60,
    rate_limit_per_day: int = 10000,
    expires_at: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Create a new API key.

    Returns ``(row_dict, plain_token)``.  ``plain_token`` is shown once only.
    """
    import json

    ensure_table()
    full_token, prefix, token_hash = _generate_token()
    key_id = str(uuid.uuid4())
    now = _now()
    space_ids = space_ids or []
    scopes = scopes or ["read"]

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_api_keys
                (id, name, prefix, token_hash, connection_id, space_ids, scopes,
                 rate_limit_per_min, rate_limit_per_day, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_id,
                name,
                prefix,
                token_hash,
                connection_id,
                json.dumps(space_ids),
                json.dumps(scopes),
                rate_limit_per_min,
                rate_limit_per_day,
                now,
                expires_at,
            ),
        )

    row = get_api_key(key_id)
    return row, full_token  # type: ignore[return-value]


def get_api_key(key_id: str) -> dict[str, Any] | None:
    import json

    ensure_table()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_api_keys WHERE id = ?", (key_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["space_ids"] = json.loads(d["space_ids"])
    d["scopes"] = json.loads(d["scopes"])
    return d


def list_api_keys(connection_id: str | None = None) -> list[dict[str, Any]]:
    import json

    ensure_table()
    with _connect() as conn:
        if connection_id:
            rows = conn.execute(
                "SELECT * FROM knowledge_api_keys WHERE connection_id = ? ORDER BY created_at DESC",
                (connection_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM knowledge_api_keys ORDER BY created_at DESC"
            ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["space_ids"] = json.loads(d["space_ids"])
        d["scopes"] = json.loads(d["scopes"])
        result.append(d)
    return result


def revoke_api_key(key_id: str) -> bool:
    """Soft-delete by setting ``expires_at`` to 1 second in the past.  Returns True if the key existed."""
    from datetime import timedelta as _td

    ensure_table()
    # Set expires_at 1 second in the past so SQLite's datetime('now') comparison reliably excludes it.
    past = (datetime.now(timezone.utc).replace(microsecond=0) - _td(seconds=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE knowledge_api_keys SET expires_at = ? WHERE id = ? AND (expires_at IS NULL OR expires_at > ?)",
            (past, key_id, past),
        )
    return cur.rowcount > 0


def verify_token(bearer: str) -> dict[str, Any] | None:
    """Verify a bearer token.

    Returns the api_key row if valid and not expired, else None.
    Uses prefix-first lookup (O(1)) then a single bcrypt.checkpw call.
    """
    import json

    if not bearer.startswith("evo_k_"):
        return None
    rest = bearer[len("evo_k_"):]
    parts = rest.split(".", 1)
    if len(parts) != 2:
        return None
    prefix = parts[0]

    ensure_table()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM knowledge_api_keys
            WHERE prefix = ?
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            """,
            (prefix,),
        ).fetchall()

    for row in rows:
        d = dict(row)
        try:
            if bcrypt.checkpw(bearer.encode(), d["token_hash"].encode()):
                # Update last_used_at (fire and forget, best-effort)
                try:
                    with _connect() as conn:
                        conn.execute(
                            "UPDATE knowledge_api_keys SET last_used_at = ? WHERE id = ?",
                            (_now(), d["id"]),
                        )
                except Exception:
                    pass
                d["space_ids"] = json.loads(d["space_ids"])
                d["scopes"] = json.loads(d["scopes"])
                return d
        except Exception:
            continue

    return None
