"""Tests for knowledge/api_keys.py — SQLite-backed, no Postgres required."""

from __future__ import annotations

import uuid

import pytest

from knowledge.api_keys import (
    create_api_key,
    ensure_table,
    get_api_key,
    list_api_keys,
    revoke_api_key,
    verify_token,
)


@pytest.fixture(autouse=True)
def _ensure(in_memory_db):
    ensure_table()


# ---------------------------------------------------------------------------
# Token format
# ---------------------------------------------------------------------------

class TestTokenFormat:
    def test_prefix(self):
        _, token = create_api_key(name="t", connection_id="c1")
        assert token.startswith("evo_k_")

    def test_has_dot_separator(self):
        _, token = create_api_key(name="t", connection_id="c1")
        rest = token[len("evo_k_"):]
        parts = rest.split(".")
        assert len(parts) == 2, f"Expected prefix.secret, got: {rest!r}"

    def test_prefix_length(self):
        _, token = create_api_key(name="t", connection_id="c1")
        prefix = token[len("evo_k_"):].split(".")[0]
        assert len(prefix) == 8

    def test_secret_length(self):
        _, token = create_api_key(name="t", connection_id="c1")
        secret = token[len("evo_k_"):].split(".")[1]
        assert len(secret) == 44

    def test_plain_token_not_stored(self):
        """The DB must not contain the plaintext token."""
        row, token = create_api_key(name="t", connection_id="c1")
        assert token not in str(row)
        assert token not in str(row.get("token_hash", ""))


# ---------------------------------------------------------------------------
# Create + get
# ---------------------------------------------------------------------------

class TestCreateAndGet:
    def test_returns_row_and_token(self):
        row, token = create_api_key(name="mykey", connection_id="conn-1")
        assert row["id"]
        assert row["connection_id"] == "conn-1"
        assert row["name"] == "mykey"
        assert isinstance(token, str)

    def test_defaults(self):
        row, _ = create_api_key(name=None, connection_id="c")
        assert row["rate_limit_per_min"] == 60
        assert row["rate_limit_per_day"] == 10000
        assert row["scopes"] == ["read"]
        assert row["space_ids"] == []

    def test_custom_limits(self):
        row, _ = create_api_key(
            name="fast",
            connection_id="c",
            rate_limit_per_min=120,
            rate_limit_per_day=5000,
            scopes=["read", "write"],
        )
        assert row["rate_limit_per_min"] == 120
        assert row["rate_limit_per_day"] == 5000
        assert row["scopes"] == ["read", "write"]

    def test_get_nonexistent(self):
        assert get_api_key(str(uuid.uuid4())) is None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class TestList:
    def test_list_by_connection(self):
        conn_id = f"list-conn-{uuid.uuid4()}"
        create_api_key(name="a", connection_id=conn_id)
        create_api_key(name="b", connection_id=conn_id)
        create_api_key(name="other", connection_id="different")
        rows = list_api_keys(connection_id=conn_id)
        assert len(rows) == 2
        names = {r["name"] for r in rows}
        assert names == {"a", "b"}

    def test_list_all(self):
        before = len(list_api_keys())
        conn_id = f"all-{uuid.uuid4()}"
        create_api_key(name="x", connection_id=conn_id)
        create_api_key(name="y", connection_id=conn_id)
        assert len(list_api_keys()) == before + 2


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

class TestVerify:
    def test_valid_token(self):
        row, token = create_api_key(name="v", connection_id="vc")
        found = verify_token(token)
        assert found is not None
        assert found["id"] == row["id"]

    def test_wrong_token_returns_none(self):
        create_api_key(name="v2", connection_id="vc2")
        assert verify_token("evo_k_wrongpre.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") is None

    def test_garbage_returns_none(self):
        assert verify_token("not-a-token") is None
        assert verify_token("") is None
        assert verify_token("Bearer evo_k_something") is None

    def test_wrong_prefix_format(self):
        assert verify_token("evo_k_nodotsecret") is None

    def test_after_revoke(self):
        row, token = create_api_key(name="revtest", connection_id="rc")
        revoke_api_key(row["id"])
        assert verify_token(token) is None


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------

class TestRevoke:
    def test_revoke_existing(self):
        row, _ = create_api_key(name="r", connection_id="rc")
        assert revoke_api_key(row["id"]) is True

    def test_revoke_nonexistent(self):
        assert revoke_api_key(str(uuid.uuid4())) is False

    def test_revoke_idempotent(self):
        row, _ = create_api_key(name="r2", connection_id="rc")
        revoke_api_key(row["id"])
        # second call: already revoked → expires_at already set to past, rowcount = 0
        assert revoke_api_key(row["id"]) is False
