"""Tests for Wave 2.3 — PluginMcpServer schema validation (AC-W2.3-1..5)."""
import pytest
from pydantic import ValidationError

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from plugin_schema import PluginMcpServer, PluginManifest


# ---------------------------------------------------------------------------
# PluginMcpServer unit tests
# ---------------------------------------------------------------------------

class TestPluginMcpServerName:
    def test_valid_simple_name(self):
        s = PluginMcpServer(name="sprint-board", command="npx", args=[])
        assert s.name == "sprint-board"

    def test_valid_single_char_name(self):
        s = PluginMcpServer(name="a", command="node", args=[])
        assert s.name == "a"

    def test_name_uppercase_rejected(self):
        with pytest.raises(ValidationError, match="must match"):
            PluginMcpServer(name="Sprint-Board", command="npx", args=[])

    def test_name_leading_hyphen_rejected(self):
        with pytest.raises(ValidationError, match="must match"):
            PluginMcpServer(name="-sprint", command="npx", args=[])

    def test_name_trailing_hyphen_rejected(self):
        with pytest.raises(ValidationError, match="must match"):
            PluginMcpServer(name="sprint-", command="npx", args=[])

    def test_name_too_long_rejected(self):
        with pytest.raises(ValidationError):
            PluginMcpServer(name="a" * 51, command="npx", args=[])


class TestPluginMcpServerCommand:
    @pytest.mark.parametrize("cmd", ["npx", "node", "python", "python3", "uv", "uvx", "deno"])
    def test_all_allowed_commands(self, cmd):
        s = PluginMcpServer(name="srv", command=cmd, args=[])
        assert s.command == cmd

    def test_arbitrary_binary_rejected(self):
        with pytest.raises(ValidationError):
            PluginMcpServer(name="srv", command="bash", args=[])

    def test_curl_rejected(self):
        with pytest.raises(ValidationError):
            PluginMcpServer(name="srv", command="curl", args=[])


class TestPluginMcpServerArgs:
    def test_args_with_shell_semicolon_rejected(self):
        with pytest.raises(ValidationError, match="shell metacharacter"):
            PluginMcpServer(name="srv", command="npx", args=["foo; rm -rf /"])

    def test_args_with_pipe_rejected(self):
        with pytest.raises(ValidationError, match="shell metacharacter"):
            PluginMcpServer(name="srv", command="npx", args=["foo|bar"])

    def test_args_with_backtick_rejected(self):
        with pytest.raises(ValidationError, match="shell metacharacter"):
            PluginMcpServer(name="srv", command="npx", args=["`evil`"])

    def test_valid_args_pass(self):
        s = PluginMcpServer(name="srv", command="npx", args=["-y", "@acme/sprint-mcp"])
        assert s.args == ["-y", "@acme/sprint-mcp"]


class TestPluginMcpServerEnv:
    def test_env_key_lowercase_rejected(self):
        with pytest.raises(ValidationError, match="uppercase identifier"):
            PluginMcpServer(name="srv", command="npx", env={"api_key": "value"})

    def test_env_value_with_semicolon_rejected(self):
        with pytest.raises(ValidationError, match="shell metacharacter"):
            PluginMcpServer(name="srv", command="npx", env={"API_KEY": "val;evil"})

    def test_valid_env_passes(self):
        s = PluginMcpServer(name="srv", command="npx", env={"API_KEY": "abc123", "WORKSPACE_DIR": "/tmp"})
        assert s.env["API_KEY"] == "abc123"

    def test_env_with_interpolation_placeholder_passes(self):
        # Interpolation tokens are not resolved at schema level
        s = PluginMcpServer(name="srv", command="npx", env={"API_KEY": "${ENV:PM_API_KEY}"})
        assert "${ENV:PM_API_KEY}" in s.env["API_KEY"]


class TestPluginManifestMcpServers:
    _BASE = {
        "id": "pm-essentials",
        "name": "PM Essentials",
        "version": "1.0.0",
        "description": "Project management plugin",
        "author": "Acme",
        "license": "MIT",
        "min_evonexus_version": "0.30.0",
    }

    def _make(self, mcp_servers):
        return PluginManifest.model_validate({**self._BASE, "mcp_servers": mcp_servers})

    def test_no_mcp_servers_valid(self):
        m = PluginManifest.model_validate(self._BASE)
        assert m.mcp_servers is None

    def test_single_valid_mcp_server(self):
        m = self._make([{"name": "sprint-board", "command": "npx", "args": ["-y", "@acme/sprint-mcp"]}])
        assert len(m.mcp_servers) == 1
        assert m.mcp_servers[0].name == "sprint-board"

    def test_duplicate_mcp_names_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate MCP server name"):
            self._make([
                {"name": "sprint-board", "command": "npx"},
                {"name": "sprint-board", "command": "node"},
            ])

    def test_multiple_unique_names_allowed(self):
        m = self._make([
            {"name": "sprint-board", "command": "npx"},
            {"name": "kanban", "command": "node"},
        ])
        assert len(m.mcp_servers) == 2
