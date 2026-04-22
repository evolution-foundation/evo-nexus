"""CRUD operations for knowledge_connections (SQLite local store).

All functions take a sqlite3.Connection as the first argument so that callers
(Flask routes, tests) control transaction lifecycle.

Public API:
    list_connections(conn) -> list[dict]
    get_connection(conn, connection_id) -> dict | None
    create_connection(conn, data) -> dict
    update_connection(conn, connection_id, data) -> dict | None
    delete_connection(conn, connection_id) -> bool
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _row_to_dict(row, cursor) -> Dict[str, Any]:
    """Convert a sqlite3 Row (or tuple) to a dict using cursor.description."""
    if row is None:
        return None
    cols = [d[0] for d in cursor.description]
    d = dict(zip(cols, row))
    # connection_string_encrypted must never appear in API responses
    d.pop("connection_string_encrypted", None)
    return d


def list_connections(conn) -> List[Dict[str, Any]]:
    """Return all connections, ordered by created_at DESC."""
    cur = conn.execute(
        "SELECT * FROM knowledge_connections ORDER BY created_at DESC"
    )
    return [_row_to_dict(row, cur) for row in cur.fetchall()]


def get_connection(conn, connection_id: str) -> Optional[Dict[str, Any]]:
    """Return a single connection by id, or None if not found."""
    cur = conn.execute(
        "SELECT * FROM knowledge_connections WHERE id = ?", (connection_id,)
    )
    row = cur.fetchone()
    return _row_to_dict(row, cur) if row else None


def create_connection(conn, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new connection record.

    *data* fields:
      Required: name (str), slug (str)
      Optional: host, port, database_name, username, ssl_mode, connection_string_encrypted (bytes)
    Returns the created row (without connection_string_encrypted).
    Raises ValueError for duplicate slug.
    """
    connection_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Validate slug uniqueness
    existing = conn.execute(
        "SELECT id FROM knowledge_connections WHERE slug = ?", (data["slug"],)
    ).fetchone()
    if existing:
        raise ValueError(f"A connection with slug '{data['slug']}' already exists.")

    conn.execute(
        """INSERT INTO knowledge_connections
           (id, slug, name, connection_string_encrypted, host, port,
            database_name, username, ssl_mode, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            connection_id,
            data["slug"],
            data["name"],
            data.get("connection_string_encrypted"),
            data.get("host"),
            data.get("port"),
            data.get("database_name"),
            data.get("username"),
            data.get("ssl_mode"),
            data.get("status", "disconnected"),
            now,
        ),
    )
    conn.commit()
    return get_connection(conn, connection_id)


def update_connection(
    conn, connection_id: str, data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Update mutable fields on an existing connection.

    Allowed fields: name, slug, host, port, database_name, username, ssl_mode,
    status, schema_version, pgvector_version, postgres_version,
    last_health_check, last_error, connection_string_encrypted.
    Returns the updated row, or None if not found.
    """
    mutable = {
        "name", "slug", "host", "port", "database_name", "username",
        "ssl_mode", "status", "schema_version", "pgvector_version",
        "postgres_version", "last_health_check", "last_error",
        "connection_string_encrypted",
    }
    updates = {k: v for k, v in data.items() if k in mutable}
    if not updates:
        return get_connection(conn, connection_id)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [connection_id]
    conn.execute(
        f"UPDATE knowledge_connections SET {set_clause} WHERE id = ?", values
    )
    conn.commit()
    return get_connection(conn, connection_id)


def delete_connection(conn, connection_id: str) -> bool:
    """Delete a connection. Returns True if a row was deleted."""
    cur = conn.execute(
        "DELETE FROM knowledge_connections WHERE id = ?", (connection_id,)
    )
    conn.commit()
    return cur.rowcount > 0


def get_connection_events(
    conn, connection_id: str, limit: int = 50
) -> List[Dict[str, Any]]:
    """Return recent events for a connection, newest first."""
    cur = conn.execute(
        "SELECT id, connection_id, event_type, details, created_at "
        "FROM knowledge_connection_events "
        "WHERE connection_id = ? ORDER BY created_at DESC LIMIT ?",
        (connection_id, limit),
    )
    rows = []
    for row in cur.fetchall():
        d = dict(zip([c[0] for c in cur.description], row))
        if d.get("details") and isinstance(d["details"], str):
            try:
                d["details"] = json.loads(d["details"])
            except (json.JSONDecodeError, TypeError):
                pass
        rows.append(d)
    return rows
