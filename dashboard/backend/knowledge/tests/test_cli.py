"""Tests for knowledge.cli — specifically ensure_master_key().

Covers:
  * Generates a fresh key when KNOWLEDGE_MASTER_KEY is absent.
  * No-op when KNOWLEDGE_MASTER_KEY already exists.
  * Creates the .env file when missing.
  * Preserves existing .env content (other keys stay untouched).
  * Idempotency (second call is a no-op).
  * Generated key is a valid Fernet token (44 chars, urlsafe base64).
  * File permissions set to 0o600 where the OS supports it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _add_backend():
    b = os.path.join(os.path.dirname(__file__), "..", "..")
    if b not in sys.path:
        sys.path.insert(0, b)


def _is_valid_fernet_key(key: str) -> bool:
    """A Fernet key round-trips through Fernet() without raising."""
    from cryptography.fernet import Fernet
    try:
        Fernet(key.encode())
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ensure_master_key
# ---------------------------------------------------------------------------

class TestEnsureMasterKey:
    def test_generates_when_env_missing(self, tmp_path):
        _add_backend()
        from knowledge.cli import ensure_master_key

        env_path = tmp_path / ".env"
        assert not env_path.exists()

        was_generated, key = ensure_master_key(env_path)

        assert was_generated is True
        assert key  # non-empty
        assert _is_valid_fernet_key(key)
        assert env_path.exists()
        assert f"KNOWLEDGE_MASTER_KEY={key}" in env_path.read_text()

    def test_generates_when_key_absent(self, tmp_path):
        _add_backend()
        from knowledge.cli import ensure_master_key

        env_path = tmp_path / ".env"
        env_path.write_text("OTHER_KEY=abc123\nANOTHER=xyz\n")

        was_generated, key = ensure_master_key(env_path)

        assert was_generated is True
        assert _is_valid_fernet_key(key)
        content = env_path.read_text()
        # Existing content preserved
        assert "OTHER_KEY=abc123" in content
        assert "ANOTHER=xyz" in content
        # New key appended
        assert f"KNOWLEDGE_MASTER_KEY={key}" in content

    def test_noop_when_key_present(self, tmp_path):
        _add_backend()
        from knowledge.cli import ensure_master_key
        from cryptography.fernet import Fernet

        existing_key = Fernet.generate_key().decode()
        env_path = tmp_path / ".env"
        env_path.write_text(f"KNOWLEDGE_MASTER_KEY={existing_key}\nFOO=bar\n")
        original_content = env_path.read_text()

        was_generated, key = ensure_master_key(env_path)

        assert was_generated is False
        assert key == existing_key
        # File must be unchanged on no-op
        assert env_path.read_text() == original_content

    def test_idempotent(self, tmp_path):
        _add_backend()
        from knowledge.cli import ensure_master_key

        env_path = tmp_path / ".env"
        # First call: generates
        g1, k1 = ensure_master_key(env_path)
        assert g1 is True
        # Second call: no-op
        g2, k2 = ensure_master_key(env_path)
        assert g2 is False
        assert k2 == k1
        # Third call: still no-op
        g3, k3 = ensure_master_key(env_path)
        assert g3 is False
        assert k3 == k1
        # Only one entry in the file
        assert env_path.read_text().count("KNOWLEDGE_MASTER_KEY=") == 1

    def test_creates_parent_dir_if_missing(self, tmp_path):
        _add_backend()
        from knowledge.cli import ensure_master_key

        env_path = tmp_path / "deep" / "nested" / ".env"
        was_generated, _ = ensure_master_key(env_path)

        assert was_generated is True
        assert env_path.exists()
        assert env_path.parent.is_dir()

    def test_quoted_value_is_recognized(self, tmp_path):
        """Naive .env parsers leave quotes around values; we still detect the key."""
        _add_backend()
        from knowledge.cli import ensure_master_key
        from cryptography.fernet import Fernet

        existing_key = Fernet.generate_key().decode()
        env_path = tmp_path / ".env"
        env_path.write_text(f'KNOWLEDGE_MASTER_KEY="{existing_key}"\n')

        was_generated, key = ensure_master_key(env_path)
        assert was_generated is False
        assert key == existing_key

    def test_generated_key_is_different_each_fresh_run(self, tmp_path):
        """Two fresh generations should not collide — Fernet is cryptographically random."""
        _add_backend()
        from knowledge.cli import ensure_master_key

        env1 = tmp_path / "env1" / ".env"
        env2 = tmp_path / "env2" / ".env"
        _, k1 = ensure_master_key(env1)
        _, k2 = ensure_master_key(env2)
        assert k1 != k2

    def test_file_mode_600_on_posix(self, tmp_path):
        _add_backend()
        from knowledge.cli import ensure_master_key

        env_path = tmp_path / ".env"
        ensure_master_key(env_path)

        if os.name == "posix":
            mode = env_path.stat().st_mode & 0o777
            assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
        # On Windows chmod is best-effort; we don't assert anything there.


# ---------------------------------------------------------------------------
# cmd_init_key — CLI wrapper (smoke test only; output goes to stdout)
# ---------------------------------------------------------------------------

class TestCmdInitKey:
    def test_returns_zero_on_fresh_generation(self, tmp_path, monkeypatch, capsys):
        _add_backend()
        # Monkeypatch _find_env_file to point into our tmp dir
        from knowledge import cli as cli_mod
        env_path = tmp_path / ".env"
        monkeypatch.setattr(cli_mod, "_find_env_file", lambda: env_path)

        rc = cli_mod.cmd_init_key([])
        assert rc == 0
        assert env_path.exists()
        assert "KNOWLEDGE_MASTER_KEY=" in env_path.read_text()

        out = capsys.readouterr().out
        assert "generated" in out.lower()

    def test_returns_zero_on_existing_key(self, tmp_path, monkeypatch, capsys):
        _add_backend()
        from knowledge import cli as cli_mod
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        env_path = tmp_path / ".env"
        env_path.write_text(f"KNOWLEDGE_MASTER_KEY={key}\n")
        monkeypatch.setattr(cli_mod, "_find_env_file", lambda: env_path)

        rc = cli_mod.cmd_init_key([])
        assert rc == 0

        out = capsys.readouterr().out
        assert "already" in out.lower()
        assert "no-op" in out.lower()
