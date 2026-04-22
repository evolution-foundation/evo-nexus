"""Fixed-window rate limiter using remote Postgres.

Two windows are checked per request:
* Per-minute  — ``date_trunc('minute', now())``
* Per-day     — ``date_trunc('day', now())``

Both use UPSERT with ``ON CONFLICT DO UPDATE RETURNING request_count`` so the
counter increment and check are done in a single round-trip.

**Fail-closed:** any exception raises ``RateLimiterUnavailable`` which the
middleware converts to HTTP 503 + ``Retry-After: 5``.  The rate limit is NEVER
silently bypassed.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RateLimitExceeded(Exception):
    """Raised when a request exceeds the allowed rate."""

    def __init__(self, retry_after: int, window: str) -> None:
        self.retry_after = retry_after
        self.window = window
        super().__init__(f"Rate limit exceeded ({window}), retry after {retry_after}s")


class RateLimiterUnavailable(Exception):
    """Raised when the rate limiter Postgres is unreachable (fail-closed)."""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_STATEMENT_TIMEOUT_MS = int(os.environ.get("RATE_LIMITER_STATEMENT_TIMEOUT_MS", "2000"))


def _get_pg_conn(dsn: str) -> "psycopg2.connection":
    """Open a psycopg2 connection with a short statement_timeout."""
    conn = psycopg2.connect(dsn, connect_timeout=3)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")
    return conn


# ---------------------------------------------------------------------------
# Seconds until next minute/day bucket (for Retry-After)
# ---------------------------------------------------------------------------

def _seconds_to_next_minute() -> int:
    now = datetime.now(timezone.utc)
    return 60 - now.second


def _seconds_to_next_day() -> int:
    now = datetime.now(timezone.utc)
    seconds_in_day = now.hour * 3600 + now.minute * 60 + now.second
    return 86400 - seconds_in_day


# ---------------------------------------------------------------------------
# Core UPSERT
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
INSERT INTO knowledge_api_usage (api_key_id, window_start, request_count)
VALUES (%(key_id)s, date_trunc(%(trunc)s, now()), 1)
ON CONFLICT (api_key_id, window_start)
DO UPDATE SET request_count = knowledge_api_usage.request_count + 1
RETURNING request_count;
"""


def _check_window(
    cur: Any,
    key_id: str,
    trunc: str,
    limit: int,
    retry_after_fn: Any,
) -> None:
    """Execute UPSERT and raise RateLimitExceeded if count > limit."""
    cur.execute(_UPSERT_SQL, {"key_id": key_id, "trunc": trunc})
    row = cur.fetchone()
    if row is None:
        raise RateLimiterUnavailable("UPSERT returned no row")
    count = row[0]
    if count > limit:
        raise RateLimitExceeded(retry_after=retry_after_fn(), window=trunc)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_rate_limit(
    *,
    dsn: str,
    key_id: str,
    rate_limit_per_min: int,
    rate_limit_per_day: int,
) -> None:
    """Check and increment both rate limit windows.

    Raises:
        RateLimitExceeded   — limit reached; caller should return HTTP 429.
        RateLimiterUnavailable — Postgres error; caller should return HTTP 503.
    """
    try:
        conn = _get_pg_conn(dsn)
    except Exception as exc:
        raise RateLimiterUnavailable(f"Cannot connect to rate limiter DB: {exc}") from exc

    try:
        with conn.cursor() as cur:
            # Per-minute check
            _check_window(cur, key_id, "minute", rate_limit_per_min, _seconds_to_next_minute)
            # Per-day check
            _check_window(cur, key_id, "day", rate_limit_per_day, _seconds_to_next_day)
    except RateLimitExceeded:
        raise
    except Exception as exc:
        raise RateLimiterUnavailable(f"Rate limiter DB error: {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DSN resolver — stub until Step 1 (connection_pool) is merged
# ---------------------------------------------------------------------------

def get_dsn_for_connection(connection_id: str) -> str:
    """Resolve the Postgres DSN for a given connection_id.

    TODO: replace this stub with ``connection_pool.get_dsn(connection_id)``
    once Step 1 is merged.  For now falls back to KNOWLEDGE_TEST_DATABASE_URL
    (used by tests) or KNOWLEDGE_POSTGRES_DSN environment variable.
    """
    # Attempt to use Step 1's connection pool if already available
    try:
        from knowledge.connection_pool import get_dsn  # type: ignore[import]
        return get_dsn(connection_id)
    except ImportError:
        pass

    dsn = os.environ.get("KNOWLEDGE_TEST_DATABASE_URL") or os.environ.get("KNOWLEDGE_POSTGRES_DSN")
    if not dsn:
        raise RateLimiterUnavailable(
            f"No Postgres DSN available for connection_id={connection_id!r}. "
            "Set KNOWLEDGE_POSTGRES_DSN or wait for Step 1 to be merged."
        )
    return dsn
