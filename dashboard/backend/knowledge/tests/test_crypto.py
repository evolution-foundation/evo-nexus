"""Unit tests for knowledge/crypto.py.

Run with:
    pytest dashboard/backend/knowledge/tests/test_crypto.py -v
"""

import os
import pytest
from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_master_key(monkeypatch):
    """Inject a valid Fernet key for every test in this module."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("KNOWLEDGE_MASTER_KEY", key)
    yield key


# ---------------------------------------------------------------------------
# Tests: encrypt_secret / decrypt_secret
# ---------------------------------------------------------------------------

def test_roundtrip_simple():
    from dashboard.backend.knowledge.crypto import encrypt_secret, decrypt_secret

    plaintext = "postgres://user:password@localhost:5432/mydb"
    ciphertext = encrypt_secret(plaintext)
    assert isinstance(ciphertext, bytes)
    assert decrypt_secret(ciphertext) == plaintext


def test_roundtrip_special_chars():
    from dashboard.backend.knowledge.crypto import encrypt_secret, decrypt_secret

    # Passwords with special characters
    plaintext = "postgres://admin:P@$$w0rd!#2024@db.example.com:5432/knowledge"
    assert decrypt_secret(encrypt_secret(plaintext)) == plaintext


def test_ciphertext_differs_from_plaintext():
    from dashboard.backend.knowledge.crypto import encrypt_secret

    plaintext = "postgres://user:secret@host/db"
    ciphertext = encrypt_secret(plaintext)
    # Ciphertext must not contain plaintext password
    assert b"secret" not in ciphertext


def test_two_encryptions_differ():
    """Fernet uses random IV — same plaintext produces different tokens each time."""
    from dashboard.backend.knowledge.crypto import encrypt_secret

    plaintext = "postgres://user:password@host/db"
    c1 = encrypt_secret(plaintext)
    c2 = encrypt_secret(plaintext)
    assert c1 != c2


def test_decrypt_with_wrong_key_raises():
    from dashboard.backend.knowledge.crypto import encrypt_secret, decrypt_secret
    from cryptography.fernet import InvalidToken

    # Encrypt with current key
    ciphertext = encrypt_secret("postgres://user:pass@host/db")

    # Switch to a different key
    other_key = Fernet.generate_key().decode()
    os.environ["KNOWLEDGE_MASTER_KEY"] = other_key

    with pytest.raises(InvalidToken):
        decrypt_secret(ciphertext)


def test_missing_master_key_raises(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_MASTER_KEY", raising=False)
    from dashboard.backend.knowledge.crypto import encrypt_secret

    with pytest.raises(RuntimeError, match="KNOWLEDGE_MASTER_KEY not set"):
        encrypt_secret("postgres://user:pass@host/db")


# ---------------------------------------------------------------------------
# Tests: mask_connection_string
# ---------------------------------------------------------------------------

def test_mask_standard_url():
    from dashboard.backend.knowledge.crypto import mask_connection_string

    masked = mask_connection_string("postgres://user:password@host:5432/db")
    assert masked == "postgres://user:***@host:5432/db"
    assert "password" not in masked


def test_mask_no_password():
    from dashboard.backend.knowledge.crypto import mask_connection_string

    cs = "postgres://user@host:5432/db"
    masked = mask_connection_string(cs)
    # No password — no *** inserted between user and @
    assert "***" not in masked
    assert "user" in masked


def test_mask_postgresql_scheme():
    from dashboard.backend.knowledge.crypto import mask_connection_string

    masked = mask_connection_string("postgresql://admin:s3cr3t@db.company.com/prod")
    assert "s3cr3t" not in masked
    assert "***" in masked


def test_mask_unparseable_returns_masked():
    from dashboard.backend.knowledge.crypto import mask_connection_string

    masked = mask_connection_string("not-a-url")
    assert masked == "<masked>"
