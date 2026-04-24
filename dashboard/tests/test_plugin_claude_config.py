"""Tests for Wave 2.3 — plugin_claude_config atomic write / MCP injection."""
import hashlib
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import plugin_claude_config as pcc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_claude_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_claude_json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _minimal_server(name="sprint-board", command="npx"):
    return {"name": name, "command": command, "args": ["-y", "@acme/sprint-mcp"], "env": {}}


# ---------------------------------------------------------------------------
# Interpolation tests
# ---------------------------------------------------------------------------

class TestInterpolation:
    def test_workspace_token(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        plugin_dir = ws / "plugins" / "pm-essentials"
        plugin_dir.mkdir(parents=True)
        result = pcc._interpolate("${WORKSPACE}/foo", ws, plugin_dir, None)
        assert str(ws.resolve()) in result
        assert result.endswith("/foo")

    def test_plugin_dir_token(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        plugin_dir = ws / "plugins" / "pm-essentials"
        plugin_dir.mkdir(parents=True)
        result = pcc._interpolate("${PLUGIN_DIR}/config.json", ws, plugin_dir, None)
        assert str(plugin_dir.resolve()) in result

    def test_env_token_resolved(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        env_file = ws / ".env"
        env_file.write_text('PM_API_KEY="secret123"\n', encoding="utf-8")
        plugin_dir = ws / "plugins" / "pm"
        plugin_dir.mkdir(parents=True)
        result = pcc._interpolate("${ENV:PM_API_KEY}", ws, plugin_dir, env_file)
        assert result == "secret123"

    def test_env_token_missing_raises(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        plugin_dir = ws / "plugins" / "pm"
        plugin_dir.mkdir(parents=True)
        env_file = ws / ".env"
        env_file.write_text("OTHER_KEY=x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="PM_API_KEY"):
            pcc._interpolate("${ENV:PM_API_KEY}", ws, plugin_dir, env_file)

    def test_shell_metachar_in_resolved_value_raises(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        env_file = ws / ".env"
        env_file.write_text("EVIL=val;rm -rf /\n", encoding="utf-8")
        plugin_dir = ws / "plugins" / "pm"
        plugin_dir.mkdir(parents=True)
        with pytest.raises(ValueError, match="shell metacharacter"):
            pcc._interpolate("${ENV:EVIL}", ws, plugin_dir, env_file)


# ---------------------------------------------------------------------------
# add_mcp_servers
# ---------------------------------------------------------------------------

class TestAddMcpServers:
    def test_injects_into_new_file(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        claude_json = tmp_path / ".claude.json"
        plugin_dir = ws / "plugins" / "pm-essentials"
        plugin_dir.mkdir(parents=True)

        monkeypatch.setattr(pcc, "CLAUDE_JSON", claude_json)
        monkeypatch.setattr(pcc, "_LOCK_FILE", tmp_path / ".lock")
        monkeypatch.setattr(pcc, "WORKSPACE", ws)

        servers = [_minimal_server()]
        records = pcc.add_mcp_servers("pm-essentials", servers, workspace=ws)

        assert len(records) == 1
        assert records[0]["effective_name"] == "plugin-pm-essentials-sprint-board"
        assert records[0]["command"] == "npx"

        data = _read_claude_json(claude_json)
        ws_key = str(ws.resolve())
        mcp_map = data["projects"][ws_key]["mcpServers"]
        assert "plugin-pm-essentials-sprint-board" in mcp_map

    def test_preserves_existing_user_mcps(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        claude_json = tmp_path / ".claude.json"
        ws_key = str(ws.resolve())
        _write_claude_json(claude_json, {
            "projects": {ws_key: {"mcpServers": {"linear-server": {"command": "npx", "args": []}}}}
        })

        monkeypatch.setattr(pcc, "CLAUDE_JSON", claude_json)
        monkeypatch.setattr(pcc, "_LOCK_FILE", tmp_path / ".lock")
        monkeypatch.setattr(pcc, "WORKSPACE", ws)

        plugin_dir = ws / "plugins" / "pm-essentials"
        plugin_dir.mkdir(parents=True)

        pcc.add_mcp_servers("pm-essentials", [_minimal_server()], workspace=ws)

        data = _read_claude_json(claude_json)
        mcp_map = data["projects"][ws_key]["mcpServers"]
        assert "linear-server" in mcp_map  # user MCP preserved
        assert "plugin-pm-essentials-sprint-board" in mcp_map

    def test_empty_servers_returns_empty(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        monkeypatch.setattr(pcc, "WORKSPACE", ws)
        records = pcc.add_mcp_servers("pm-essentials", [], workspace=ws)
        assert records == []

    def test_backup_created(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(claude_json, {"projects": {}})
        monkeypatch.setattr(pcc, "CLAUDE_JSON", claude_json)
        monkeypatch.setattr(pcc, "_LOCK_FILE", tmp_path / ".lock")
        monkeypatch.setattr(pcc, "WORKSPACE", ws)

        plugin_dir = ws / "plugins" / "pm"
        plugin_dir.mkdir(parents=True)

        pcc.add_mcp_servers("pm", [_minimal_server()], workspace=ws)

        backups = list(tmp_path.glob(".claude.json.evonexus-backup-*"))
        assert len(backups) == 1


# ---------------------------------------------------------------------------
# remove_mcp_servers
# ---------------------------------------------------------------------------

class TestRemoveMcpServers:
    def _setup(self, tmp_path, ws, claude_json, ws_key, eff_name, command="npx"):
        plugin_dir = ws / "plugins" / "pm-essentials"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        _write_claude_json(claude_json, {
            "projects": {ws_key: {"mcpServers": {
                eff_name: {"command": command, "args": ["-y", "@acme/sprint-mcp"], "env": {}}
            }}}
        })
        fingerprint = {"command": command, "args": ["-y", "@acme/sprint-mcp"], "env_keys": []}
        h = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:16]
        return [{"effective_name": eff_name, "command": command, "args_hash": h, "env_keys": []}]

    def test_removes_matching_entry(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        ws_key = str(ws.resolve())
        claude_json = tmp_path / ".claude.json"
        eff_name = "plugin-pm-essentials-sprint-board"
        records = self._setup(tmp_path, ws, claude_json, ws_key, eff_name)

        monkeypatch.setattr(pcc, "CLAUDE_JSON", claude_json)
        monkeypatch.setattr(pcc, "_LOCK_FILE", tmp_path / ".lock")

        audit = pcc.remove_mcp_servers("pm-essentials", records, workspace=ws)

        assert audit[eff_name] == "removed"
        data = _read_claude_json(claude_json)
        assert eff_name not in data["projects"][ws_key]["mcpServers"]

    def test_absent_entry_returns_absent(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        ws_key = str(ws.resolve())
        claude_json = tmp_path / ".claude.json"
        _write_claude_json(claude_json, {"projects": {ws_key: {"mcpServers": {}}}})

        monkeypatch.setattr(pcc, "CLAUDE_JSON", claude_json)
        monkeypatch.setattr(pcc, "_LOCK_FILE", tmp_path / ".lock")

        records = [{"effective_name": "plugin-pm-essentials-ghost", "args_hash": "abcd1234abcd1234", "env_keys": []}]
        audit = pcc.remove_mcp_servers("pm-essentials", records, workspace=ws)
        assert audit["plugin-pm-essentials-ghost"] == "absent"

    def test_drift_detected_skips_removal(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        ws_key = str(ws.resolve())
        claude_json = tmp_path / ".claude.json"
        eff_name = "plugin-pm-essentials-sprint-board"
        # Write different args than what's recorded in the hash
        _write_claude_json(claude_json, {
            "projects": {ws_key: {"mcpServers": {
                eff_name: {"command": "npx", "args": ["different-arg"], "env": {}}
            }}}
        })

        monkeypatch.setattr(pcc, "CLAUDE_JSON", claude_json)
        monkeypatch.setattr(pcc, "_LOCK_FILE", tmp_path / ".lock")

        # Record hash computed from original args (will not match modified)
        fingerprint = {"command": "npx", "args": ["-y", "@acme/sprint-mcp"], "env_keys": []}
        saved_hash = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:16]
        records = [{"effective_name": eff_name, "args_hash": saved_hash, "env_keys": []}]

        audit = pcc.remove_mcp_servers("pm-essentials", records, workspace=ws)
        assert audit[eff_name] == "drift_detected"
        # Entry must still be present
        data = _read_claude_json(claude_json)
        assert eff_name in data["projects"][ws_key]["mcpServers"]


# ---------------------------------------------------------------------------
# args_hash
# ---------------------------------------------------------------------------

class TestArgsHash:
    def test_hash_is_16_chars(self):
        h = pcc._args_hash({"command": "npx", "args": ["-y", "pkg"], "env": {"K": "V"}})
        assert len(h) == 16
        assert h.isalnum()

    def test_same_input_same_hash(self):
        srv = {"command": "npx", "args": ["-y", "pkg"], "env": {}}
        assert pcc._args_hash(srv) == pcc._args_hash(srv)

    def test_different_args_different_hash(self):
        a = pcc._args_hash({"command": "npx", "args": ["a"], "env": {}})
        b = pcc._args_hash({"command": "npx", "args": ["b"], "env": {}})
        assert a != b
