"""Tests for knowledge/rate_limiter.py — real Postgres required.

Run with:
    KNOWLEDGE_TEST_DATABASE_URL=postgresql://... pytest knowledge/tests/test_rate_limiter.py -v

Boundary-burst note (documented, accepted trade-off):
    Fixed-window counters allow a burst of up to 2× the limit at a window
    boundary (last N requests in window N-1 plus first N in window N).
    This is expected behaviour for fixed-window implementations and is
    explicitly accepted in ADR-006.
"""

from __future__ import annotations

import concurrent.futures
import uuid

import pytest

from knowledge.rate_limiter import (
    RateLimitExceeded,
    RateLimiterUnavailable,
    check_rate_limit,
)

pytestmark = pytest.mark.requires_postgres


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _key_id() -> str:
    return str(uuid.uuid4())


def _check(dsn, key_id, *, per_min=60, per_day=10000):
    check_rate_limit(
        dsn=dsn,
        key_id=key_id,
        rate_limit_per_min=per_min,
        rate_limit_per_day=per_day,
    )


# ---------------------------------------------------------------------------
# AC-10: 61st request in the same minute returns 429
# ---------------------------------------------------------------------------

class TestPerMinuteLimit:
    def test_60_pass_61st_fails(self, pg_dsn, clean_usage):
        key = _key_id()
        # First 60 must pass
        for i in range(60):
            _check(pg_dsn, key, per_min=60)

        # 61st must raise RateLimitExceeded on the minute window
        with pytest.raises(RateLimitExceeded) as exc_info:
            _check(pg_dsn, key, per_min=60)

        exc = exc_info.value
        assert exc.window == "minute"
        assert 0 < exc.retry_after <= 60

    def test_retry_after_is_positive(self, pg_dsn, clean_usage):
        key = _key_id()
        for _ in range(1):
            _check(pg_dsn, key, per_min=1)
        with pytest.raises(RateLimitExceeded) as exc_info:
            _check(pg_dsn, key, per_min=1)
        assert exc_info.value.retry_after > 0


# ---------------------------------------------------------------------------
# Per-day limit
# ---------------------------------------------------------------------------

class TestPerDayLimit:
    def test_day_limit(self, pg_dsn, clean_usage):
        key = _key_id()
        for _ in range(3):
            _check(pg_dsn, key, per_min=1000, per_day=3)

        with pytest.raises(RateLimitExceeded) as exc_info:
            _check(pg_dsn, key, per_min=1000, per_day=3)

        exc = exc_info.value
        assert exc.window == "day"
        assert 0 < exc.retry_after <= 86400


# ---------------------------------------------------------------------------
# Independent keys don't share counters
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_different_keys_isolated(self, pg_dsn, clean_usage):
        key_a = _key_id()
        key_b = _key_id()
        for _ in range(5):
            _check(pg_dsn, key_a, per_min=5)
        # key_b should still be fresh
        _check(pg_dsn, key_b, per_min=5)

    def test_same_key_accumulates(self, pg_dsn, clean_usage):
        key = _key_id()
        _check(pg_dsn, key, per_min=2)
        _check(pg_dsn, key, per_min=2)
        with pytest.raises(RateLimitExceeded):
            _check(pg_dsn, key, per_min=2)


# ---------------------------------------------------------------------------
# Concurrency: 100 parallel requests at limit=50 → exactly 50 pass
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_50_of_100_pass(self, pg_dsn, clean_usage):
        """AC-10 concurrency variant: 100 parallel requests at limit=50."""
        key = _key_id()
        limit = 50
        total = 100

        passed = 0
        limited = 0

        def do_request(_):
            try:
                check_rate_limit(
                    dsn=pg_dsn,
                    key_id=key,
                    rate_limit_per_min=limit,
                    rate_limit_per_day=10000,
                )
                return "pass"
            except RateLimitExceeded:
                return "limit"

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(do_request, range(total)))

        passed = results.count("pass")
        limited = results.count("limit")

        assert passed == limit, f"Expected {limit} passed, got {passed}"
        assert limited == total - limit, f"Expected {total - limit} limited, got {limited}"


# ---------------------------------------------------------------------------
# Fail-closed: bad DSN raises RateLimiterUnavailable
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_bad_dsn_raises_unavailable(self):
        with pytest.raises(RateLimiterUnavailable):
            check_rate_limit(
                dsn="postgresql://invalid:invalid@localhost:1/noexist",
                key_id=_key_id(),
                rate_limit_per_min=60,
                rate_limit_per_day=10000,
            )
