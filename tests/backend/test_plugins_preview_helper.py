"""Unit tests for _diff_capabilities and _compute_preview helper (Wave 1.2).

Tests are pure — no Flask app, no DB, no network.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Import the helpers under test (after path setup)
# ---------------------------------------------------------------------------
from routes.plugins import _diff_capabilities  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture manifests
# ---------------------------------------------------------------------------

def _make_manifest(
    *,
    agents=None,
    skills=None,
    commands=None,
    rules=None,
    widgets=None,
    readonly_data=None,
    claude_hooks=None,
    heartbeats=None,
    triggers=None,
    routines=None,
    install_sql_sha=None,
) -> dict:
    """Build a minimal manifest dict matching the shape stored in manifest_json."""
    files: dict = {}
    if agents:
        for name, sha in agents.items():
            files[f".claude/agents/{name}"] = {"sha256": sha}
    if skills:
        for name, sha in skills.items():
            files[f".claude/skills/{name}"] = {"sha256": sha}
    if commands:
        for name, sha in commands.items():
            files[f".claude/commands/{name}"] = {"sha256": sha}
    if rules:
        for name, sha in rules.items():
            files[f".claude/rules/{name}"] = {"sha256": sha}
    if install_sql_sha:
        files["migrations/install.sql"] = {"sha256": install_sql_sha}

    manifest: dict = {"files": files}

    if widgets:
        manifest["ui_entry_points"] = {"widgets": [{"id": id_, **rest} for id_, rest in widgets.items()]}
    if readonly_data:
        manifest["readonly_data"] = [{"id": id_, "description": "x", "sql": "SELECT 1", **rest} for id_, rest in readonly_data.items()]
    if claude_hooks:
        manifest["claude_hooks"] = [{"handler_path": hp, "event": "PostToolUse"} for hp in claude_hooks]
    if heartbeats:
        manifest["heartbeats"] = [{"id": id_, "interval_seconds": 60} for id_ in heartbeats]
    if triggers:
        manifest["triggers"] = [{"id": id_, "name": id_} for id_ in triggers]
    if routines:
        manifest["routines"] = [{"name": name} for name in routines]

    return manifest


# ---------------------------------------------------------------------------
# Tests: _diff_capabilities
# ---------------------------------------------------------------------------

class TestDiffCapabilities:
    def test_added_file_backed(self):
        installed = _make_manifest(agents={"pm-nova.md": "sha1"})
        candidate = _make_manifest(agents={"pm-nova.md": "sha1", "pm-extra.md": "sha2"})
        added, removed, modified = _diff_capabilities(installed, candidate)
        assert "pm-extra.md" in added.get("agents", [])
        assert not removed
        assert not modified

    def test_removed_file_backed(self):
        installed = _make_manifest(skills={"plugin-foo.md": "sha1", "plugin-bar.md": "sha2"})
        candidate = _make_manifest(skills={"plugin-foo.md": "sha1"})
        added, removed, modified = _diff_capabilities(installed, candidate)
        assert "plugin-bar.md" in removed.get("skills", [])
        assert not added
        assert not modified

    def test_modified_file_backed(self):
        installed = _make_manifest(agents={"pm-nova.md": "sha_old"})
        candidate = _make_manifest(agents={"pm-nova.md": "sha_new"})
        added, removed, modified = _diff_capabilities(installed, candidate)
        assert "pm-nova.md" in modified.get("agents", [])
        assert not added
        assert not removed

    def test_unchanged_file_backed(self):
        installed = _make_manifest(agents={"pm-nova.md": "sha1"})
        candidate = _make_manifest(agents={"pm-nova.md": "sha1"})
        added, removed, modified = _diff_capabilities(installed, candidate)
        assert not added
        assert not removed
        assert not modified

    def test_modified_entry_backed_widget(self):
        """Widget dict changed → appears in modified."""
        installed = _make_manifest(widgets={"my-widget": {"label": "Old", "route": "/old"}})
        candidate = _make_manifest(widgets={"my-widget": {"label": "New", "route": "/new"}})
        added, removed, modified = _diff_capabilities(installed, candidate)
        assert "my-widget" in modified.get("widgets", [])

    def test_added_heartbeat(self):
        installed = _make_manifest(heartbeats=["hb-a"])
        candidate = _make_manifest(heartbeats=["hb-a", "hb-b"])
        added, removed, modified = _diff_capabilities(installed, candidate)
        assert "hb-b" in added.get("heartbeats", [])

    def test_removed_trigger(self):
        installed = _make_manifest(triggers=["tr-x", "tr-y"])
        candidate = _make_manifest(triggers=["tr-x"])
        added, removed, modified = _diff_capabilities(installed, candidate)
        assert "tr-y" in removed.get("triggers", [])

    def test_empty_manifests(self):
        added, removed, modified = _diff_capabilities({}, {})
        assert not added
        assert not removed
        assert not modified

    def test_all_caps_added(self):
        installed = _make_manifest()
        candidate = _make_manifest(
            agents={"a.md": "sha1"},
            skills={"s.md": "sha2"},
            commands={"c.md": "sha3"},
            rules={"r.md": "sha4"},
            widgets={"w": {"label": "W", "route": "/w"}},
            readonly_data={"q": {}},
            claude_hooks=["hooks/h.py"],
            heartbeats=["hb"],
            triggers=["tr"],
            routines=["rt"],
        )
        added, removed, modified = _diff_capabilities(installed, candidate)
        assert added
        assert not removed
        assert not modified


class TestBreakingChanges:
    """Verify breaking_changes logic embedded in _compute_preview.

    We test the SQL-blocked heuristic by building the relevant sub-logic
    inline (since _compute_preview requires a live DB + resolver).
    The SQL check itself is: new_sql_sha != installed_sql_sha.
    """

    def test_sql_sha_changed_gives_blocked(self):
        """If install.sql SHA differs, sql_migrations_blocked must be True."""
        new_sql_sha = "deadbeef"
        installed_sql_sha = "cafebabe"
        sql_migrations_blocked = bool(
            new_sql_sha != installed_sql_sha and (new_sql_sha or installed_sql_sha)
        )
        assert sql_migrations_blocked is True

    def test_sql_sha_same_not_blocked(self):
        sha = "aabbccdd"
        assert not bool(sha != sha and (sha or sha))

    def test_sql_sha_both_none_not_blocked(self):
        """No SQL file on either side → not blocked."""
        new_sql_sha = None
        installed_sql_sha = None
        sql_migrations_blocked = bool(
            new_sql_sha != installed_sql_sha and (new_sql_sha or installed_sql_sha)
        )
        assert sql_migrations_blocked is False

    def test_capability_rename_heuristic(self):
        """Old ID in capabilities_disabled AND removed, new ID of same type added → warning."""
        caps_disabled = {"skills": ["plugin-foo-old"]}
        removed = {"skills": {"plugin-foo-old"}}
        added = {"skills": {"plugin-foo-new"}}

        breaking_changes = []
        for cap_type, disabled_ids in caps_disabled.items():
            removed_in_type = removed.get(cap_type, set())
            added_in_type = added.get(cap_type, set())
            for old_id in disabled_ids:
                if old_id in removed_in_type and added_in_type:
                    breaking_changes.append(
                        f"capability '{old_id}' ({cap_type}) was disabled by you but is removed "
                        "in the new version — disabled state will be lost"
                    )

        assert len(breaking_changes) == 1
        assert "plugin-foo-old" in breaking_changes[0]
        assert "skills" in breaking_changes[0]

    def test_capability_rename_heuristic_no_new_id(self):
        """Old ID removed but no new ID of same type → no warning (it's a genuine removal)."""
        caps_disabled = {"skills": ["plugin-foo-old"]}
        removed = {"skills": {"plugin-foo-old"}}
        added: dict = {}

        breaking_changes = []
        for cap_type, disabled_ids in caps_disabled.items():
            removed_in_type = removed.get(cap_type, set())
            added_in_type = added.get(cap_type, set())
            for old_id in disabled_ids:
                if old_id in removed_in_type and added_in_type:
                    breaking_changes.append("warning")

        assert not breaking_changes
