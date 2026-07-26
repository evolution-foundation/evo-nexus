from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def _providers(tmp_path: Path, active: str, *, fallback: bool = False) -> Path:
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({
        "active_provider": active,
        "allow_provider_fallback": fallback,
        "providers": {
            "anthropic": {"cli_command": "claude"},
            "hermes": {"cli_command": "hermes"},
        },
    }))
    return path


def test_active_hermes_wins_even_when_claude_is_installed(tmp_path):
    from runtime_service import RuntimeRequest, RuntimeService

    service = RuntimeService(providers_path=_providers(tmp_path, "hermes"))
    request = RuntimeRequest(origin_type="heartbeat", origin_id="hb-1", agent_slug="atlas-project", prompt="decide", max_turns=3, timeout_seconds=10)

    with patch("runtime_service.shutil.which", side_effect=lambda cli: f"/bin/{cli}"), patch.object(service, "_resolve_profile", return_value="atlas"), patch("runtime_service.subprocess.Popen") as popen:
        process = popen.return_value
        process.communicate.return_value = ('{"action":"skip"}', "")
        process.returncode = 0
        result = service.invoke(request)

    assert popen.call_args.args[0][:2] == ["hermes", "-p"]
    assert result.provider == "hermes"
    assert result.resolved_profile is not None


def test_active_provider_is_fail_closed_without_explicit_fallback(tmp_path):
    from runtime_service import RuntimeRequest, RuntimeService

    service = RuntimeService(providers_path=_providers(tmp_path, "hermes"))
    request = RuntimeRequest(origin_type="heartbeat", origin_id="hb-1", agent_slug="atlas-project", prompt="decide", max_turns=1, timeout_seconds=10)

    with patch("runtime_service.shutil.which", return_value=None):
        result = service.invoke(request)

    assert result.status == "failed"
    assert result.provider == "hermes"
    assert "Active provider CLI unavailable" in result.error


def test_explicit_fallback_is_recorded(tmp_path):
    from runtime_service import RuntimeRequest, RuntimeService

    service = RuntimeService(providers_path=_providers(tmp_path, "hermes", fallback=True))
    request = RuntimeRequest(origin_type="heartbeat", origin_id="hb-1", agent_slug="atlas-project", prompt="decide", max_turns=1, timeout_seconds=10)

    with patch("runtime_service.shutil.which", side_effect=lambda cli: "/bin/claude" if cli == "claude" else None), patch("runtime_service.subprocess.Popen") as popen:
        process = popen.return_value
        process.communicate.return_value = ("ok", "")
        process.returncode = 0
        result = service.invoke(request)

    assert result.provider == "anthropic"
    assert result.fallback_from == "hermes"


def test_timeout_kills_process_group(tmp_path):
    from runtime_service import RuntimeRequest, RuntimeService
    import subprocess

    service = RuntimeService(providers_path=_providers(tmp_path, "anthropic"))
    request = RuntimeRequest(origin_type="heartbeat", origin_id="hb-1", agent_slug="atlas-project", prompt="decide", max_turns=1, timeout_seconds=1)

    with patch("runtime_service.shutil.which", return_value="/bin/claude"), patch("runtime_service.subprocess.Popen") as popen, patch("runtime_service.os.killpg") as killpg:
        process = popen.return_value
        process.pid = 123
        process.communicate.side_effect = [subprocess.TimeoutExpired("claude", 1), ("", "")]
        with patch("runtime_service.os.getpgid", return_value=123):
            result = service.invoke(request)
            assert result.status == "timeout"
        killpg.assert_called_once()


def test_provider_output_is_redacted_before_returning(tmp_path, monkeypatch):
    from runtime_service import RuntimeRequest, RuntimeService

    secret = "provider-secret-value"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    service = RuntimeService(providers_path=_providers(tmp_path, "anthropic"))
    request = RuntimeRequest(
        origin_type="heartbeat",
        origin_id="hb-1",
        agent_slug="atlas-project",
        prompt="decide",
        max_turns=1,
        timeout_seconds=10,
    )

    with patch("runtime_service.shutil.which", return_value="/bin/claude"), patch(
        "runtime_service.subprocess.Popen"
    ) as popen:
        process = popen.return_value
        process.communicate.return_value = (
            f"partial output {secret}",
            f"Authorization: Bearer {secret}",
        )
        process.returncode = 1
        result = service.invoke(request)

    assert secret not in result.output
    assert secret not in result.error
    assert "[REDACTED]" in result.output
    assert "[REDACTED]" in result.error
