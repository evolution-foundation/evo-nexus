"""Unit tests for knowledge/connections.py (SQLite CRUD).

Uses an in-memory SQLite database — no external dependencies.

Run with:
    pytest dashboard/backend/knowledge/tests/test_connections.py -v
"""

import sqlite3
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE knowledge_connections (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    connection_string_encrypted BLOB,
    host TEXT,
    port INTEGER,
    database_name TEXT,
    username TEXT,
    ssl_mode TEXT,
    status TEXT DEFAULT 'disconnected',
    schema_version TEXT,
    pgvector_version TEXT,
    postgres_version TEXT,
    last_health_check TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE knowledge_connection_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT REFERENCES knowledge_connections(id) ON DELETE CASCADE,
    event_type TEXT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def db():
    """Provide an in-memory SQLite connection with the knowledge schema."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Tests: create_connection
# ---------------------------------------------------------------------------

def test_create_minimal(db):
    from dashboard.backend.knowledge.connections import create_connection

    result = create_connection(db, {"name": "My DB", "slug": "my-db"})
    assert result["name"] == "My DB"
    assert result["slug"] == "my-db"
    assert result["status"] == "disconnected"
    assert "id" in result
    assert "connection_string_encrypted" not in result


def test_create_with_all_fields(db):
    from dashboard.backend.knowledge.connections import create_connection

    data = {
        "name": "Full DB",
        "slug": "full-db",
        "host": "db.example.com",
        "port": 5432,
        "database_name": "knowledge",
        "username": "admin",
        "ssl_mode": "require",
        "connection_string_encrypted": b"encrypted-blob",
    }
    result = create_connection(db, data)
    assert result["host"] == "db.example.com"
    assert result["port"] == 5432
    assert result["database_name"] == "knowledge"
    assert "connection_string_encrypted" not in result


def test_create_duplicate_slug_raises(db):
    from dashboard.backend.knowledge.connections import create_connection

    create_connection(db, {"name": "DB1", "slug": "dup-slug"})
    with pytest.raises(ValueError, match="already exists"):
        create_connection(db, {"name": "DB2", "slug": "dup-slug"})


# ---------------------------------------------------------------------------
# Tests: list_connections
# ---------------------------------------------------------------------------

def test_list_empty(db):
    from dashboard.backend.knowledge.connections import list_connections

    assert list_connections(db) == []


def test_list_multiple(db):
    from dashboard.backend.knowledge.connections import create_connection, list_connections

    create_connection(db, {"name": "A", "slug": "a"})
    create_connection(db, {"name": "B", "slug": "b"})
    rows = list_connections(db)
    assert len(rows) == 2
    slugs = {r["slug"] for r in rows}
    assert slugs == {"a", "b"}


def test_list_does_not_expose_encrypted_field(db):
    from dashboard.backend.knowledge.connections import create_connection, list_connections

    create_connection(db, {"name": "Sec", "slug": "sec", "connection_string_encrypted": b"secret"})
    for row in list_connections(db):
        assert "connection_string_encrypted" not in row


# ---------------------------------------------------------------------------
# Tests: get_connection
# ---------------------------------------------------------------------------

def test_get_existing(db):
    from dashboard.backend.knowledge.connections import create_connection, get_connection

    created = create_connection(db, {"name": "Get Test", "slug": "get-test"})
    fetched = get_connection(db, created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["slug"] == "get-test"


def test_get_not_found(db):
    from dashboard.backend.knowledge.connections import get_connection

    result = get_connection(db, "nonexistent-id")
    assert result is None


# ---------------------------------------------------------------------------
# Tests: update_connection
# ---------------------------------------------------------------------------

def test_update_status(db):
    from dashboard.backend.knowledge.connections import create_connection, update_connection

    created = create_connection(db, {"name": "Upd", "slug": "upd"})
    updated = update_connection(db, created["id"], {"status": "ready"})
    assert updated["status"] == "ready"


def test_update_multiple_fields(db):
    from dashboard.backend.knowledge.connections import create_connection, update_connection

    created = create_connection(db, {"name": "Multi", "slug": "multi"})
    updated = update_connection(
        db, created["id"],
        {"status": "ready", "schema_version": "001", "pgvector_version": "0.7.0"},
    )
    assert updated["status"] == "ready"
    assert updated["schema_version"] == "001"
    assert updated["pgvector_version"] == "0.7.0"


def test_update_ignores_immutable_fields(db):
    from dashboard.backend.knowledge.connections import create_connection, update_connection

    created = create_connection(db, {"name": "Immut", "slug": "immut"})
    original_id = created["id"]
    # Try to change id (not in mutable set) — should be silently ignored
    updated = update_connection(db, original_id, {"id": "new-id", "status": "ready"})
    assert updated["id"] == original_id
    assert updated["status"] == "ready"


def test_update_not_found(db):
    from dashboard.backend.knowledge.connections import update_connection

    result = update_connection(db, "ghost-id", {"status": "ready"})
    assert result is None


# ---------------------------------------------------------------------------
# Tests: delete_connection
# ---------------------------------------------------------------------------

def test_delete_existing(db):
    from dashboard.backend.knowledge.connections import create_connection, delete_connection, get_connection

    created = create_connection(db, {"name": "Del", "slug": "del"})
    assert delete_connection(db, created["id"]) is True
    assert get_connection(db, created["id"]) is None


def test_delete_not_found(db):
    from dashboard.backend.knowledge.connections import delete_connection

    assert delete_connection(db, "ghost") is False


# ---------------------------------------------------------------------------
# Tests: get_connection_events
# ---------------------------------------------------------------------------

def test_events_empty(db):
    from dashboard.backend.knowledge.connections import create_connection, get_connection_events

    created = create_connection(db, {"name": "Ev", "slug": "ev"})
    assert get_connection_events(db, created["id"]) == []


def test_events_returned_newest_first(db):
    from dashboard.backend.knowledge.connections import create_connection, get_connection_events

    import json
    created = create_connection(db, {"name": "Ev2", "slug": "ev2"})
    cid = created["id"]
    db.execute(
        "INSERT INTO knowledge_connection_events (connection_id, event_type, details, created_at) VALUES (?, ?, ?, ?)",
        (cid, "configured", json.dumps({"schema_version": "001"}), "2026-01-01 10:00:00"),
    )
    db.execute(
        "INSERT INTO knowledge_connection_events (connection_id, event_type, details, created_at) VALUES (?, ?, ?, ?)",
        (cid, "drift_detected", json.dumps({"head": "002"}), "2026-01-02 10:00:00"),
    )
    db.commit()
    events = get_connection_events(db, cid)
    assert events[0]["event_type"] == "drift_detected"
    assert events[1]["event_type"] == "configured"
    assert isinstance(events[0]["details"], dict)
