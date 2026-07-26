"""Tests for resolve_plugin_sql() — dialect-aware SQL file discovery (ADR PG-Q7).

Scenarios covered
-----------------
SQLite backend:
  - plugin ships install.sqlite.sql                → returns it (no warning)
  - plugin ships only legacy install.sql           → returns it + DeprecationWarning
  - plugin ships both .sqlite.sql and .sql         → returns .sqlite.sql (preferred)
  - plugin ships neither                           → FileNotFoundError

PostgreSQL backend:
  - plugin ships install.postgres.sql              → returns it (no warning)
  - plugin ships only legacy install.sql           → PluginCompatError
  - plugin ships both .postgres.sql and .sql       → returns .postgres.sql
  - plugin ships neither                           → FileNotFoundError
"""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import patch
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from plugin_loader import (  # noqa: E402
    PluginCompatError,
    resolve_plugin_sql,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_migrations(tmp_path: Path, files: list[str]) -> Path:
    """Create a fake plugin directory with the given SQL files in migrations/."""
    plugin_dir = tmp_path / "my-plugin"
    mig_dir = plugin_dir / "migrations"
    mig_dir.mkdir(parents=True)
    for fname in files:
        (mig_dir / fname).write_text(f"-- {fname} stub\n", encoding="utf-8")
    return mig_dir


def _patch_dialect(name: str):
    """Return a context manager that patches db.engine.dialect.name."""
    class _FakeDialect:
        pass

    fake = _FakeDialect()
    fake.name = name  # type: ignore[attr-defined]
    return patch("plugin_loader.resolve_plugin_sql.__globals__", {"_dialect": fake})


# We use a simpler patch that injects directly into the function's module scope.
# The function does `from db.engine import dialect as _dialect` at call time, so
# we need to patch at the point of use: plugin_loader's local import.

def _patch_engine_dialect(name: str):
    """Patch db.engine.dialect.name seen by resolve_plugin_sql at import time."""
    class _FakeDlct:
        pass

    d = _FakeDlct()
    d.name = name  # type: ignore[attr-defined]
    # plugin_loader does `from db.engine import dialect as _dialect` inside the
    # function body; we patch the `db.engine` module attribute so the import
    # picks up our fake.
    import db.engine as _eng
    return patch.object(_eng, "dialect", d)


# ---------------------------------------------------------------------------
# SQLite backend tests
# ---------------------------------------------------------------------------

class TestSQLiteDialect:
    """resolve_plugin_sql on a SQLite-active backend."""

    def test_prefers_sqlite_specific_file(self, tmp_path):
        mig = _make_migrations(tmp_path, ["install.sqlite.sql"])
        with _patch_engine_dialect("sqlite"):
            result = resolve_plugin_sql(mig, "install")
        assert result.name == "install.sqlite.sql"
        assert result.exists()

    def test_returns_legacy_with_deprecation_warning(self, tmp_path):
        mig = _make_migrations(tmp_path, ["install.sql"])
        with _patch_engine_dialect("sqlite"):
            with pytest.warns(DeprecationWarning, match="legacy install.sql"):
                result = resolve_plugin_sql(mig, "install")
        assert result.name == "install.sql"
        assert result.exists()

    def test_prefers_dialect_file_over_legacy(self, tmp_path):
        mig = _make_migrations(tmp_path, ["install.sqlite.sql", "install.sql"])
        with _patch_engine_dialect("sqlite"):
            with warnings.catch_warnings():
                warnings.simplefilter("error")  # any warning = test failure
                result = resolve_plugin_sql(mig, "install")
        assert result.name == "install.sqlite.sql"

    def test_raises_when_no_file_found(self, tmp_path):
        mig = _make_migrations(tmp_path, [])
        with _patch_engine_dialect("sqlite"):
            with pytest.raises(FileNotFoundError, match="no SQL file found"):
                resolve_plugin_sql(mig, "install")

    def test_uninstall_hook_sqlite(self, tmp_path):
        mig = _make_migrations(tmp_path, ["uninstall.sqlite.sql"])
        with _patch_engine_dialect("sqlite"):
            result = resolve_plugin_sql(mig, "uninstall")
        assert result.name == "uninstall.sqlite.sql"

    def test_uninstall_hook_legacy_warning(self, tmp_path):
        mig = _make_migrations(tmp_path, ["uninstall.sql"])
        with _patch_engine_dialect("sqlite"):
            with pytest.warns(DeprecationWarning):
                result = resolve_plugin_sql(mig, "uninstall")
        assert result.name == "uninstall.sql"


# ---------------------------------------------------------------------------
# PostgreSQL backend tests
# ---------------------------------------------------------------------------

class TestPostgresDialect:
    """resolve_plugin_sql on a PostgreSQL-active backend."""

    def test_returns_postgres_specific_file(self, tmp_path):
        mig = _make_migrations(tmp_path, ["install.postgres.sql"])
        with _patch_engine_dialect("postgresql"):
            result = resolve_plugin_sql(mig, "install")
        assert result.name == "install.postgres.sql"
        assert result.exists()

    def test_fails_fast_with_compat_error_when_only_legacy(self, tmp_path):
        mig = _make_migrations(tmp_path, ["install.sql"])
        with _patch_engine_dialect("postgresql"):
            with pytest.raises(PluginCompatError, match="install.sql.*legacy SQLite format"):
                resolve_plugin_sql(mig, "install")

    def test_compat_error_mentions_migration_guide(self, tmp_path):
        mig = _make_migrations(tmp_path, ["install.sql"])
        with _patch_engine_dialect("postgresql"):
            with pytest.raises(PluginCompatError, match="plugin-contract.md"):
                resolve_plugin_sql(mig, "install")

    def test_prefers_postgres_file_over_legacy(self, tmp_path):
        mig = _make_migrations(tmp_path, ["install.postgres.sql", "install.sql"])
        with _patch_engine_dialect("postgresql"):
            result = resolve_plugin_sql(mig, "install")
        assert result.name == "install.postgres.sql"

    def test_raises_file_not_found_when_no_file(self, tmp_path):
        mig = _make_migrations(tmp_path, [])
        with _patch_engine_dialect("postgresql"):
            with pytest.raises(FileNotFoundError, match="no SQL file found"):
                resolve_plugin_sql(mig, "install")

    def test_no_fallback_to_sqlite_file(self, tmp_path):
        """A .sqlite.sql file must NOT be used on Postgres."""
        mig = _make_migrations(tmp_path, ["install.sqlite.sql"])
        with _patch_engine_dialect("postgresql"):
            with pytest.raises(FileNotFoundError):
                resolve_plugin_sql(mig, "install")

    def test_uninstall_hook_postgres(self, tmp_path):
        mig = _make_migrations(tmp_path, ["uninstall.postgres.sql"])
        with _patch_engine_dialect("postgresql"):
            result = resolve_plugin_sql(mig, "uninstall")
        assert result.name == "uninstall.postgres.sql"

    def test_uninstall_hook_legacy_postgres_fails(self, tmp_path):
        mig = _make_migrations(tmp_path, ["uninstall.sql"])
        with _patch_engine_dialect("postgresql"):
            with pytest.raises(PluginCompatError):
                resolve_plugin_sql(mig, "uninstall")


# ---------------------------------------------------------------------------
# Error message quality
# ---------------------------------------------------------------------------

class TestErrorMessages:
    """Verify error messages name the plugin slug and provide actionable guidance."""

    def test_compat_error_names_plugin_slug(self, tmp_path):
        tmp_path_plugin = tmp_path / "my-plugin"
        mig = tmp_path_plugin / "migrations"
        mig.mkdir(parents=True)
        (mig / "install.sql").write_text("-- stub\n")
        with _patch_engine_dialect("postgresql"):
            with pytest.raises(PluginCompatError) as exc_info:
                resolve_plugin_sql(mig, "install")
        assert "my-plugin" in str(exc_info.value)

    def test_deprecation_warning_names_plugin_slug(self, tmp_path):
        tmp_path_plugin = tmp_path / "my-plugin"
        mig = tmp_path_plugin / "migrations"
        mig.mkdir(parents=True)
        (mig / "install.sql").write_text("-- stub\n")
        with _patch_engine_dialect("sqlite"):
            with pytest.warns(DeprecationWarning) as record:
                resolve_plugin_sql(mig, "install")
        assert "my-plugin" in str(record[0].message)
        assert "plugin-contract.md" in str(record[0].message)

    def test_file_not_found_names_hook(self, tmp_path):
        mig = _make_migrations(tmp_path, [])
        with _patch_engine_dialect("sqlite"):
            with pytest.raises(FileNotFoundError, match="'install'"):
                resolve_plugin_sql(mig, "install")
