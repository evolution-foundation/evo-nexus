"""provider_fallback.py — cwd extension (ADR-5): isolated job workspaces run
subprocesses in their own directory and skip the shared /workspace lock.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))

import provider_fallback as pf


def test_invoke_cli_run_honors_custom_cwd(tmp_path):
    result = pf._invoke_cli_run(["/bin/pwd"], {"PATH": "/usr/bin:/bin"}, 5, tmp_path, output_mode="envelope")
    assert result["status"] == "success"
    assert result["output"].strip() == str(tmp_path)


def test_invoke_cli_run_defaults_to_workspace_when_cwd_omitted():
    result = pf._invoke_cli_run(["/bin/pwd"], {"PATH": "/usr/bin:/bin"}, 5, pf.WORKSPACE, output_mode="envelope")
    assert result["output"].strip() == str(pf.WORKSPACE)


def test_invoke_with_fallback_skips_workspace_lock_when_cwd_given(tmp_path, monkeypatch):
    """When cwd is provided, invoke_with_fallback must go straight to
    _invoke_with_fallback_locked without acquiring _workspace_bash_lock —
    an isolated media-job directory is not part of the shared /workspace
    git tree and must not serialize behind heartbeats/Telegram sessions.
    """
    calls = {"lock_entered": False}

    class _ExplodingLock:
        def __enter__(self):
            calls["lock_entered"] = True
            raise AssertionError("must not acquire the shared workspace lock when cwd is given")

        def __exit__(self, *a):
            return False

    def _fake_workspace_bash_lock(holder):
        return _ExplodingLock()

    monkeypatch.setattr(pf, "_workspace_bash_lock", _fake_workspace_bash_lock)
    monkeypatch.setattr(pf, "_invoke_with_fallback_locked", lambda **kwargs: {"status": "success", "output": "ok"})

    result = pf.invoke_with_fallback(prompt="hi", cwd=tmp_path)
    assert result["status"] == "success"
    assert calls["lock_entered"] is False


def test_invoke_with_fallback_still_uses_workspace_lock_without_cwd(monkeypatch):
    """Backward compatibility: existing callers (heartbeats, ADWs) that never
    pass cwd keep going through the shared-workspace mutex exactly as before.
    """
    calls = {"lock_entered": False}

    import contextlib

    @contextlib.contextmanager
    def _fake_lock(holder):
        calls["lock_entered"] = True
        yield

    monkeypatch.setattr(pf, "_workspace_bash_lock", _fake_lock)
    monkeypatch.setattr(pf, "_invoke_with_fallback_locked", lambda **kwargs: {"status": "success", "output": "ok"})

    pf.invoke_with_fallback(prompt="hi")
    assert calls["lock_entered"] is True
