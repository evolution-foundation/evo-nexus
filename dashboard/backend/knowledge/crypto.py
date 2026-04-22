"""Fernet-based encryption for Knowledge connection strings at rest.

Functions:
    encrypt_secret(plaintext) -> bytes       stored in knowledge_connections.connection_string_encrypted
    decrypt_secret(ciphertext) -> str
    mask_connection_string(cs) -> str        safe for logs / API responses
"""

import os
import re

from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_fernet() -> Fernet:
    key = os.environ.get("KNOWLEDGE_MASTER_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "KNOWLEDGE_MASTER_KEY not set. Run `evonexus init-key` (or `make init-key`) first."
        )
    return Fernet(key.encode())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a connection string. Returns Fernet token bytes."""
    return _get_fernet().encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a Fernet token. Returns plaintext connection string."""
    return _get_fernet().decrypt(ciphertext).decode()


_CS_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+\-.]+://)"  # postgres://
    r"(?P<user>[^:@]*)(?::(?P<pass>[^@]+))?"  # user:pass
    r"(?P<rest>@.*)",                          # @host/db
    re.IGNORECASE,
)


def mask_connection_string(cs: str) -> str:
    """Replace the password in a connection string with ***.

    postgres://user:password@host/db  →  postgres://user:***@host/db
    """
    m = _CS_RE.match(cs)
    if not m:
        # Not a parseable connection string — return a fully masked version
        return "<masked>"
    parts = m.group("scheme") + m.group("user")
    if m.group("pass"):
        parts += ":***"
    parts += m.group("rest")
    return parts
