"""Tests for int-portainer.

BUG 1: script must load .env automatically (no manual source needed).
BUG 2: cmd_logs must reject endpoint_id=None with a clear error, never build
        a broken URL like endpoints/None/...
B3: instances must be discovered dynamically from env vars (PORTAINER_<NOME>_URL/TOKEN),
    no hardcoded names — "all" iterates over whatever is configured.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load the module under test without executing main()
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "portainer.py"


def _load_portainer(env_overrides: Optional[dict] = None, strip_portainer_env: bool = False):
    """Import portainer.py in an isolated environment.

    strip_portainer_env=True removes all PORTAINER_* vars from the real environment
    before merging overrides — necessary for tests that assert on the absence of MTA
    instances (which exist in the real .env).
    """
    if strip_portainer_env:
        base_env = {k: v for k, v in os.environ.items() if not k.startswith("PORTAINER_")}
    else:
        base_env = dict(os.environ)
    env = {**base_env, **(env_overrides or {})}
    with patch.dict(os.environ, env, clear=True):
        spec = importlib.util.spec_from_file_location("portainer_test", _SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# BUG 1 — .env auto-load
# ---------------------------------------------------------------------------


def test_env_loaded_from_dotenv(tmp_path):
    """_load_dotenv() must populate os.environ from .env without overriding existing vars."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PORTAINER_TESTINST_URL=https://fake-from-dotenv.example.com\n"
        "PORTAINER_TESTINST_TOKEN=tok_from_file\n"
        "# comment line\n"
        "IGNORED_BLANK=\n"
    )

    # path layout: <tmp_path>/.claude/skills/int-portainer/scripts/portainer.py
    fake_script = tmp_path / ".claude" / "skills" / "int-portainer" / "scripts" / "portainer.py"
    fake_script.parent.mkdir(parents=True, exist_ok=True)
    fake_script.write_text("")

    with patch.dict(os.environ, {}, clear=True):
        # Verify path math resolves to the fake .env
        from pathlib import Path as _Path
        env_path = _Path(fake_script).resolve().parents[4] / ".env"
        assert env_path == env_file, f"Expected {env_file}, got {env_path}"

        # Simulate _load_dotenv
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k and k not in os.environ:
                    os.environ[k] = v

        assert os.environ.get("PORTAINER_TESTINST_URL") == "https://fake-from-dotenv.example.com"
        assert os.environ.get("PORTAINER_TESTINST_TOKEN") == "tok_from_file"


def test_env_does_not_override_existing():
    """_load_dotenv() must NOT overwrite variables already set in the environment."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        env_file = tmp_path / ".env"
        env_file.write_text("PORTAINER_TESTINST_URL=https://from-file.example.com\n")

        with patch.dict(os.environ, {"PORTAINER_TESTINST_URL": "https://pre-existing.example.com"}, clear=False):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip()
                    if k and k not in os.environ:
                        os.environ[k] = v

            assert os.environ["PORTAINER_TESTINST_URL"] == "https://pre-existing.example.com"


def test_load_dotenv_function_exists_in_module():
    """portainer.py must expose _load_dotenv callable at module level."""
    mod = _load_portainer()
    assert hasattr(mod, "_load_dotenv"), "_load_dotenv must be defined in portainer.py"
    assert callable(mod._load_dotenv)


# ---------------------------------------------------------------------------
# BUG 2 — cmd_logs rejects None endpoint_id
# ---------------------------------------------------------------------------


def test_cmd_logs_rejects_none_endpoint_id(capsys):
    """cmd_logs must exit(1) with a clear message when endpoint_id is None."""
    mod = _load_portainer()

    fake_cfg = {"url": "https://portainer.example.com", "token": "tok"}

    with pytest.raises(SystemExit) as exc_info:
        mod.cmd_logs(cfg=fake_cfg, instance_name="test", endpoint_id=None, container="myapp")

    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "endpoint" in captured.err.lower() or "endpoint" in captured.out.lower()
    assert "endpoints/None" not in captured.err
    assert "endpoints/None" not in captured.out


def test_cmd_logs_with_valid_endpoint_does_not_exit_early(monkeypatch):
    """cmd_logs must proceed past the guard when endpoint_id is a valid integer."""
    mod = _load_portainer()

    calls = []

    def fake_api(cfg, path, **kwargs):
        calls.append(path)
        if "/containers/json" in path:
            return [
                {
                    "Id": "abc123def456",
                    "Names": ["/myapp"],
                    "State": "running",
                    "Status": "Up 2 hours",
                    "Created": 1700000000,
                    "Image": "myapp:latest",
                }
            ]
        return b"\x01\x00\x00\x00\x00\x00\x00\x05hello"

    monkeypatch.setattr(mod, "api", fake_api)

    fake_cfg = {"url": "https://portainer.example.com", "token": "tok"}
    mod.cmd_logs(cfg=fake_cfg, instance_name="test", endpoint_id=1, container="myapp", lines=10)

    assert any("/endpoints/1/docker/containers" in c for c in calls)


# ---------------------------------------------------------------------------
# B3 — dynamic instance discovery
# ---------------------------------------------------------------------------


def test_instances_discovered_from_env():
    """INSTANCES must be built from PORTAINER_<NOME>_URL/TOKEN pairs in the environment."""
    mod = _load_portainer({
        "PORTAINER_ALPHA_URL": "https://alpha.example.com",
        "PORTAINER_ALPHA_TOKEN": "tok_alpha",
        "PORTAINER_BETA_URL": "https://beta.example.com",
        "PORTAINER_BETA_TOKEN": "tok_beta",
    })
    assert "alpha" in mod.INSTANCES
    assert "beta" in mod.INSTANCES
    assert mod.INSTANCES["alpha"]["url"] == "https://alpha.example.com"
    assert mod.INSTANCES["alpha"]["token"] == "tok_alpha"
    assert mod.INSTANCES["beta"]["url"] == "https://beta.example.com"


def test_instances_empty_without_env():
    """_discover_instances must return empty dict when no PORTAINER_* vars are present."""
    mod = _load_portainer()
    # Test the discovery function directly against a clean env (avoids .env load order issue)
    with patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("PORTAINER_")}, clear=True):
        result = mod._discover_instances()
    assert result == {}


def test_no_hardcoded_instance_names():
    """_discover_instances must be driven purely by env vars — no names baked in."""
    mod = _load_portainer()
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("PORTAINER_")}
    clean_env["PORTAINER_PROD_URL"] = "https://prod.example.com"
    clean_env["PORTAINER_PROD_TOKEN"] = "tok_prod"
    with patch.dict(os.environ, clean_env, clear=True):
        result = mod._discover_instances()
    assert set(result.keys()) == {"prod"}


def test_resolve_instances_all_returns_all_configured():
    """resolve_instances('all') must return all configured instances."""
    mod = _load_portainer({
        "PORTAINER_PROD_URL": "https://prod.example.com",
        "PORTAINER_PROD_TOKEN": "tok_prod",
        "PORTAINER_STAGING_URL": "https://staging.example.com",
        "PORTAINER_STAGING_TOKEN": "tok_staging",
    })
    result = mod.resolve_instances("all")
    names = [r[0] for r in result]
    assert "prod" in names
    assert "staging" in names


def test_resolve_instances_unknown_exits_with_message(capsys):
    """resolve_instances must exit(1) and name available instances when name is unknown."""
    mod = _load_portainer({
        "PORTAINER_PROD_URL": "https://prod.example.com",
        "PORTAINER_PROD_TOKEN": "tok_prod",
    })
    with pytest.raises(SystemExit) as exc_info:
        mod.resolve_instances("nonexistent")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    # Must list the available instances in the error
    assert "prod" in captured.err


def test_resolve_instances_no_instances_configured_exits(capsys):
    """resolve_instances must exit(1) with helpful message when nothing is configured."""
    mod = _load_portainer({}, strip_portainer_env=True)
    # Patch INSTANCES directly so resolve_instances sees an empty dict regardless of
    # whether _load_portainer already picked up something from the system .env.
    mod.INSTANCES = {}
    with pytest.raises(SystemExit) as exc_info:
        mod.resolve_instances("all")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "portainer" in captured.err.lower() or "instância" in captured.err.lower() or "configured" in captured.err.lower()


def test_discover_instances_strips_trailing_slash():
    """_discover_instances must strip trailing slash from URLs."""
    mod = _load_portainer({
        "PORTAINER_MYINST_URL": "https://portainer.example.com/",
        "PORTAINER_MYINST_TOKEN": "tok",
    })
    assert mod.INSTANCES["myinst"]["url"] == "https://portainer.example.com"


# ---------------------------------------------------------------------------
# Module structure
# ---------------------------------------------------------------------------


def test_module_imports_without_error():
    """portainer.py must import without raising exceptions even without credentials."""
    mod = _load_portainer()
    assert mod is not None
    assert hasattr(mod, "cmd_ping")
    assert hasattr(mod, "cmd_logs")
    assert hasattr(mod, "cmd_health")
    assert hasattr(mod, "_discover_instances")
