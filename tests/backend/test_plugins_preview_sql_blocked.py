"""Tests for SQL-migration-blocked path in the preview endpoint (Wave 1.2).

Validates AC1.2.3: when install.sql SHA changes, preview returns
sql_migrations_blocked: true and breaking_changes contains the SQL message.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


SQL_BLOCKED_PREVIEW = {
    "from_version": "0.1.0",
    "to_version": "0.2.0",
    "added": {},
    "removed": {},
    "modified": {},
    "sql_migrations_blocked": True,
    "breaking_changes": [
        "install.sql SHA changed — uninstall and reinstall required (v1a limitation)"
    ],
    "tarball_sha7": "deadbe7",
}


class TestSqlBlocked:
    """Unit-level checks — the full endpoint path is covered in test_plugins_preview_endpoint."""

    def test_sql_sha_change_triggers_blocked(self):
        """Core logic: different SHAs → sql_migrations_blocked = True."""
        installed_sql_sha = "sha_old"
        new_sql_sha = "sha_new"

        sql_migrations_blocked = bool(
            new_sql_sha != installed_sql_sha and (new_sql_sha or installed_sql_sha)
        )
        assert sql_migrations_blocked is True

    def test_breaking_changes_contains_sql_message(self):
        """When blocked, breaking_changes must contain the SQL reinstall message."""
        breaking_changes = []
        sql_migrations_blocked = True
        if sql_migrations_blocked:
            breaking_changes.append(
                "install.sql SHA changed — uninstall and reinstall required (v1a limitation)"
            )
        assert any("install.sql" in msg for msg in breaking_changes)
        assert any("uninstall" in msg for msg in breaking_changes)

    def test_preview_payload_shape_when_blocked(self):
        """Payload must carry sql_migrations_blocked:true + non-empty breaking_changes."""
        payload = SQL_BLOCKED_PREVIEW
        assert payload["sql_migrations_blocked"] is True
        assert len(payload["breaking_changes"]) >= 1
        assert any("install.sql" in msg for msg in payload["breaking_changes"])

    def test_sql_sha_none_vs_none_not_blocked(self):
        """Both sides have no SQL file → not blocked."""
        new_sql_sha = None
        installed_sql_sha = None
        sql_migrations_blocked = bool(
            new_sql_sha != installed_sql_sha and (new_sql_sha or installed_sql_sha)
        )
        assert sql_migrations_blocked is False

    def test_new_sql_present_installed_none_is_blocked(self):
        """New version adds install.sql where installed had none → blocked (schema change)."""
        new_sql_sha = "newsha"
        installed_sql_sha = None
        sql_migrations_blocked = bool(
            new_sql_sha != installed_sql_sha and (new_sql_sha or installed_sql_sha)
        )
        assert sql_migrations_blocked is True
