#!/usr/bin/env python3
"""
Test Hermes Runtime Integration

This script validates that Hermes is properly integrated with EvoNexus ADWs.
"""

# Executable validation harness; do not collect it as a pytest module.
__test__ = False

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Add workspace to path
WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

def test_hermes_adapter_exists():
    """Check that hermes_adapter.py exists and is executable."""
    adapter_path = WORKSPACE / "ADWs" / "hermes_adapter.py"
    if not adapter_path.exists():
        return False, f"hermes_adapter.py not found at {adapter_path}"
    if not os.access(adapter_path, os.X_OK):
        return False, "hermes_adapter.py is not executable"
    return True, "hermes_adapter.py exists and is executable"

def test_runner_uses_adapter():
    """Check that runner.py imports and uses hermes_adapter."""
    runner_path = WORKSPACE / "ADWs" / "runner.py"
    runner_content = runner_path.read_text()

    if "hermes_adapter.py" not in runner_content:
        return False, "runner.py does not reference hermes_adapter.py"

    if 'cli_command == "hermes"' not in runner_content:
        return False, "runner.py does not check for hermes CLI command"

    return True, "runner.py properly integrates hermes_adapter.py"

def test_providers_json_exists():
    """Check that config/providers example includes Hermes."""
    providers_path = WORKSPACE / "config" / "providers.example.json"
    if not providers_path.exists():
        return False, f"providers.example.json not found at {providers_path}"

    # Parse and validate structure
    try:
        config = json.loads(providers_path.read_text())
        if "hermes" not in config.get("providers", {}):
            return False, "providers.example.json does not contain hermes provider"

        hermes_config = config["providers"]["hermes"]
        if hermes_config.get("cli_command") != "hermes":
            return False, f"hermes provider has wrong cli_command: {hermes_config.get('cli_command')}"

        return True, "providers.example.json exists and contains hermes provider"
    except json.JSONDecodeError as e:
        return False, f"providers.example.json is invalid JSON: {e}"

def test_providers_route_supports_hermes():
    """Check that providers.py routes support Hermes."""
    providers_route = WORKSPACE / "dashboard" / "backend" / "routes" / "providers.py"
    content = providers_route.read_text()

    if '"hermes"' not in content:
        return False, "providers.py does not reference hermes"

    if 'ALLOWED_CLI_COMMANDS = frozenset({"claude", "openclaude", "hermes"})' not in content:
        return False, "hermes not in ALLOWED_CLI_COMMANDS"

    return True, "providers.py supports Hermes CLI command"

def test_env_vars_allowed():
    """Check that Hermes env vars are allowlisted."""
    providers_route = WORKSPACE / "dashboard" / "backend" / "routes" / "providers.py"
    content = providers_route.read_text()

    hermes_env_vars = [
        "HERMES_MODEL",
        "HERMES_MAX_ITERATIONS",
        "OPENROUTER_API_KEY",
    ]

    for var in hermes_env_vars:
        if var not in content:
            return False, f"{var} not in ALLOWED_ENV_VARS"

    return True, "All Hermes env vars are allowlisted"

def test_runner_imports():
    """Test that runner.py can be imported."""
    try:
        sys.path.insert(0, str(WORKSPACE / "ADWs"))
        import runner
        return True, "runner.py can be imported"
    except Exception as e:
        return False, f"Failed to import runner.py: {e}"

def test_runner_timeout_kills_process_group():
    """A silent child plus descendant must time out without leaking either process."""
    import runner

    pidfile = WORKSPACE / "ADWs" / ".timeout-grandchild.pid"
    pidfile.unlink(missing_ok=True)
    original_spawn = runner._spawn_cli

    child_code = (
        "import pathlib,subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )

    def spawn_silent(*_args, **_kwargs):
        return subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    try:
        runner._spawn_cli = spawn_silent
        started = time.monotonic()
        result = runner.run_claude("timeout regression", log_name="timeout-regression", timeout=1)
        elapsed = time.monotonic() - started
    finally:
        runner._spawn_cli = original_spawn

    if result["returncode"] != -1 or result["success"]:
        return False, f"Expected timeout result, got {result}"
    if elapsed > 4:
        return False, f"Timeout took {elapsed:.1f}s (expected under 4s)"
    if not pidfile.exists():
        return False, "Grandchild PID file was not created"

    grandchild_pid = int(pidfile.read_text())
    pidfile.unlink(missing_ok=True)
    try:
        os.kill(grandchild_pid, 0)
    except ProcessLookupError:
        return True, f"Timed out in {elapsed:.1f}s and reaped process group"

    # A killed child can briefly remain as a zombie until init reaps it.
    # Treat a zombie as terminated, not as a live process leak.
    status_file = Path(f"/proc/{grandchild_pid}/status")
    if status_file.exists() and "State:\tZ" in status_file.read_text():
        return True, f"Timed out in {elapsed:.1f}s and terminated process group"
    return False, f"Grandchild process {grandchild_pid} leaked after timeout"


def test_adapter_output_format():
    """Test that hermes_adapter.py outputs correct format."""
    adapter_path = WORKSPACE / "ADWs" / "hermes_adapter.py"
    content = adapter_path.read_text()

    if '"result"' not in content:
        return False, "hermes_adapter.py does not output 'result' field"

    if '"usage"' not in content:
        return False, "hermes_adapter.py does not output 'usage' field"

    return True, "hermes_adapter.py outputs correct JSON format"

def test_hermes_cli_syntax():
    """Check Hermes invocations use the supported chat subcommand."""
    adapter_path = WORKSPACE / "ADWs" / "hermes_adapter.py"
    native_path = WORKSPACE / "ADWs" / "hermes_native.py"
    heartbeat_path = WORKSPACE / "dashboard" / "backend" / "heartbeat_runner.py"

    adapter_content = adapter_path.read_text()
    native_content = native_path.read_text()
    heartbeat_content = heartbeat_path.read_text()

    if '["chat", "-Q", "-q", args.prompt]' not in adapter_content:
        return False, "hermes_adapter.py does not use hermes chat -Q -q"
    if '["chat", "-Q", "-q", prompt]' not in native_content:
        return False, "hermes_native.py does not use hermes chat -Q -q"
    if '"chat",' not in heartbeat_content or '"-Q",' not in heartbeat_content:
        return False, "heartbeat_runner.py does not use hermes chat -Q -q"

    return True, "Hermes invocations use supported chat syntax"

def test_adapter_supports_profile_flag():
    """Check the adapter accepts --profile and emits `hermes -p <slug>` (Fase 2)."""
    adapter_content = (WORKSPACE / "ADWs" / "hermes_adapter.py").read_text()

    if '"--profile"' not in adapter_content:
        return False, "hermes_adapter.py does not define --profile"
    if '"-p", args.profile' not in adapter_content:
        return False, "hermes_adapter.py does not pass -p <profile> to hermes"

    runner_content = (WORKSPACE / "ADWs" / "runner.py").read_text()
    if '"--profile", profile' not in runner_content:
        return False, "runner.py does not forward --profile to the adapter"

    return True, "Adapter + runner support Hermes profile routing"


def test_profile_registry_resolves():
    """Check the profile registry resolves task types deterministically (Fase 2)."""
    import hermes_profiles as hp

    cfg = {
        "routing": {"research": "researcher", "build": "developer"},
        "fallback_profile": "default-mac",
        "profiles_dir": "/nonexistent-so-nothing-installed",
    }
    # Nothing installed → everything must fail-safe to a RuntimeError, never escalate.
    try:
        hp.resolve_profile("research", None, config=cfg)
        return False, "resolve_profile should raise when no profile is installed"
    except RuntimeError:
        return True, "profile registry fails safe when no profiles installed"


def test_plugin_scan_supports_hermes_fallback():
    """Check plugin scan runner falls back through Claude/OpenClaude/Hermes."""
    plugin_scan_path = WORKSPACE / "dashboard" / "backend" / "plugin_scan_runner.py"
    content = plugin_scan_path.read_text()

    if '("claude", "openclaude", "hermes")' not in content:
        return False, "plugin_scan_runner.py does not include hermes fallback chain"
    if '["hermes", "chat", "-Q", "-q", prompt]' not in content:
        return False, "plugin_scan_runner.py does not use hermes chat -Q -q"

    return True, "plugin_scan_runner.py supports Hermes fallback"

def main():
    """Run all tests and report results."""
    tests = [
        ("Hermes Adapter Exists", test_hermes_adapter_exists),
        ("Runner Uses Adapter", test_runner_uses_adapter),
        ("Providers JSON Exists", test_providers_json_exists),
        ("Providers Route Supports Hermes", test_providers_route_supports_hermes),
        ("Env Vars Allowed", test_env_vars_allowed),
        ("Runner Imports", test_runner_imports),
        ("Runner Timeout Kills Process Group", test_runner_timeout_kills_process_group),
        ("Adapter Output Format", test_adapter_output_format),
        ("Hermes CLI Syntax", test_hermes_cli_syntax),
        ("Adapter Supports Profile Flag", test_adapter_supports_profile_flag),
        ("Profile Registry Resolves", test_profile_registry_resolves),
        ("Plugin Scan Hermes Fallback", test_plugin_scan_supports_hermes_fallback),
    ]

    print("=" * 60)
    print("Hermes Runtime Integration Test Suite")
    print("=" * 60)

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            success, message = test_func()
            if success:
                print(f"  [PASS] {test_name}")
                print(f"         {message}")
                passed += 1
            else:
                print(f"  [FAIL] {test_name}")
                print(f"         {message}")
                failed += 1
        except Exception as e:
            print(f"  [ERROR] {test_name}")
            print(f"          {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 60)

    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
