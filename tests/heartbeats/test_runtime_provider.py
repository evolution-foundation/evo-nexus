from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def test_heartbeat_uses_normalized_runtime_service():
    import heartbeat_runner as runner

    runtime = MagicMock()
    runtime_result = MagicMock(
        status="succeeded", provider="hermes", requested_profile=None,
        resolved_profile="atlas", output='{"action":"skip"}', error=None,
        exit_code=0, duration_ms=12, tokens_in=None, tokens_out=None, cost_usd=None,
    )
    runtime.invoke.return_value = runtime_result

    with patch("runtime_service.RuntimeService", return_value=runtime):
        result = runner.step7_invoke_runtime("atlas-project", "prompt", 3, 10, heartbeat_id="atlas-4h")

    assert result["status"] == "success"
    assert result["provider"] == "hermes"
    assert result["resolved_profile"] == "atlas"
    assert runtime.invoke.call_args.args[0].origin_type == "heartbeat"


def test_disabled_heartbeat_never_invokes_runtime():
    import heartbeat_runner as runner

    heartbeat = {"id": "atlas-4h", "enabled": False}
    with patch.object(runner, "_load_heartbeat", return_value=heartbeat), patch.object(runner, "step7_invoke_runtime") as invoke:
        runner.run_heartbeat("atlas-4h")

    invoke.assert_not_called()
