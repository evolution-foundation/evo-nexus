"""Pytest configuration for knowledge tests.

Real-Postgres tests are gated behind the ``requires_postgres`` marker.
Set ``KNOWLEDGE_TEST_DATABASE_URL`` to a valid psycopg2 DSN to enable them.

Example (local):
    KNOWLEDGE_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/knowledge_test pytest

Tables are created via Alembic upgrade head per test session and dropped on teardown.
"""

from __future__ import annotations

import os
import sys

import psycopg2
import pytest


# Ensure backend is importable
def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_postgres: skip unless KNOWLEDGE_TEST_DATABASE_URL is set",
    )


def pytest_collection_modifyitems(config, items):
    skip_pg = pytest.mark.skip(reason="KNOWLEDGE_TEST_DATABASE_URL not set")
    for item in items:
        if "requires_postgres" in item.keywords:
            if not os.environ.get("KNOWLEDGE_TEST_DATABASE_URL"):
                item.add_marker(skip_pg)


# ---------------------------------------------------------------------------
# Postgres fixtures
# ---------------------------------------------------------------------------

_USAGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_api_usage (
    api_key_id  UUID        NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INT       NOT NULL DEFAULT 0,
    PRIMARY KEY (api_key_id, window_start)
);
CREATE INDEX IF NOT EXISTS idx_usage_window ON knowledge_api_usage(window_start);
"""

_TEARDOWN_TABLES = [
    "knowledge_classify_queue",
    "knowledge_chunks",
    "knowledge_documents",
    "knowledge_units",
    "knowledge_spaces",
    "knowledge_events",
    "knowledge_api_usage",
    "knowledge_api_keys",
    "knowledge_config",
    "alembic_version",
]


@pytest.fixture(scope="session")
def pg_dsn():
    """Return the test DSN or skip if not configured."""
    dsn = os.environ.get("KNOWLEDGE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("KNOWLEDGE_TEST_DATABASE_URL not set")
    return dsn


@pytest.fixture(scope="session")
def pg_full_schema(pg_dsn):
    """Session-scoped: run Alembic upgrade head to create the full Knowledge schema.

    Drops any pre-existing knowledge tables before running Alembic so the
    migration is idempotent when tests are re-run without a clean DB.
    """
    _add_backend()
    from knowledge.auto_migrator import _run_alembic_upgrade

    # Pre-clean: drop tables that may have been left by a previous test run
    # (e.g., knowledge_api_usage created by pg_conn before pg_full_schema).
    _pre_conn = psycopg2.connect(pg_dsn)
    _pre_conn.autocommit = True
    with _pre_conn.cursor() as cur:
        for table in _TEARDOWN_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    _pre_conn.close()

    _run_alembic_upgrade(pg_dsn)

    conn = psycopg2.connect(pg_dsn)
    conn.autocommit = True

    # Drop the FK constraint on knowledge_api_usage so rate_limiter tests can
    # use arbitrary UUIDs without inserting into knowledge_api_keys first.
    with conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE knowledge_api_usage
            DROP CONSTRAINT IF EXISTS knowledge_api_usage_api_key_id_fkey
            """
        )

    yield conn

    # Teardown: drop all knowledge tables
    with conn.cursor() as cur:
        for table in _TEARDOWN_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    conn.close()


@pytest.fixture(scope="session")
def pg_conn(pg_dsn, pg_full_schema):
    """Session-scoped Postgres connection with the usage table available.

    Depends on pg_full_schema so the Alembic migration (which creates
    knowledge_api_usage WITH the FK constraint) runs first.
    The legacy _USAGE_SCHEMA_SQL uses IF NOT EXISTS so it is a no-op.
    """
    conn = psycopg2.connect(pg_dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(_USAGE_SCHEMA_SQL)  # idempotent — IF NOT EXISTS
    yield conn
    conn.close()


@pytest.fixture()
def clean_usage(pg_conn):
    """Truncate the usage table before each test that needs a clean slate."""
    with pg_conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE knowledge_api_usage")
    yield


# ---------------------------------------------------------------------------
# SQLite api_keys fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def in_memory_db(tmp_path_factory):
    """Point api_keys module at a temp SQLite file for the whole session."""
    db_file = tmp_path_factory.mktemp("db") / "test_evonexus.db"
    os.environ["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_file}"
    yield str(db_file)
