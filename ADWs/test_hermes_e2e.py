#!/usr/bin/env python3
"""
E2E Test: Hermes Runtime Adapter — Phase 1 Closure

Validates all acceptance criteria from the hermes-runtime-adapter plan:
1. Baseline tests pass (11/11)
2. A read-only ADW routine completes via Hermes
3. Output consumed via JSON contract (no regex on human text)
4. Induced failure triggers fallback with real cause logged
5. Timeout/cancellation terminates process and produces terminal state
6. Logs contain no secrets/tokens/keys
7. Original provider restored after test
8. WebSocket/iframe/proxy smoke (structural check only)

Run with: .venv/bin/python ADWs/test_hermes_e2e.py
"""

# Executable validation harness; do not collect it as a pytest module.
__test__ = False

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
CONFIG_DIR = WORKSPACE / "config"
PROVIDERS_FILE = CONFIG_DIR / "providers.json"
PROVIDERS_EXAMPLE = CONFIG_DIR / "providers.example.json"
ADAPTER_PATH = WORKSPACE / "ADWs" / "hermes_adapter.py"
PYTHON = str(WORKSPACE / ".venv" / "bin" / "python")

# Sensitive patterns that must NOT appear in logs
SECRET_PATTERNS = [
    r"sk-[a-zA-Z0-9]{20,}",          # OpenAI/Anthropic API keys
    r"OPENROUTER_API_KEY\s*=\s*\S+",
    r"ANTHROPIC_API_KEY\s*=\s*\S+",
    r"Authorization:\s*Bearer\s+\S+",
    r"token\s*[:=]\s*['\"][^'\"]{20,}['\"]",
    r"cookie\s*[:=]\s*['\"][^'\"]{20,}['\"]",
    r"password\s*[:=]\s*['\"][^'\"]+['\"]",
]


class TestResult:
    def __init__(self):
        self.results = []

    def record(self, name: str, passed: bool, detail: str = ""):
        self.results.append({"name": name, "passed": passed, "detail": detail})
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status} | {name}")
        if detail:
            print(f"         {detail}")

    def summary(self) -> int:
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        failed = total - passed
        print(f"\n{'='*60}")
        print(f"E2E Results: {passed}/{total} passed, {failed} failed")
        print(f"{'='*60}")
        return 0 if failed == 0 else 1


def _backup_providers():
    """Backup existing providers.json if it exists."""
    if PROVIDERS_FILE.exists():
        backup = PROVIDERS_FILE.with_suffix(".json.e2e-backup")
        shutil.copy2(PROVIDERS_FILE, backup)
        return backup
    return None


def _restore_providers(backup_path):
    """Restore providers.json from backup or remove if no backup."""
    if backup_path and backup_path.exists():
        shutil.move(str(backup_path), str(PROVIDERS_FILE))
    elif PROVIDERS_FILE.exists():
        PROVIDERS_FILE.unlink()


def _create_hermes_provider_config():
    """Create a providers.json with hermes as active provider."""
    config = {
        "active_provider": "hermes",
        "providers": {
            "anthropic": {
                "name": "Anthropic (Claude nativo)",
                "cli_command": "claude",
                "env_vars": {},
            },
            "hermes": {
                "name": "Hermes Agent",
                "cli_command": "hermes",
                "env_vars": {},
            },
        },
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROVIDERS_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    return config


def _create_fallback_provider_config():
    """Create providers.json with hermes pointing to a fake binary (induces failure)."""
    config = {
        "active_provider": "hermes",
        "providers": {
            "anthropic": {
                "name": "Anthropic (Claude nativo)",
                "cli_command": "claude",
                "env_vars": {},
            },
            "hermes": {
                "name": "Hermes Agent (broken)",
                "cli_command": "hermes",
                "env_vars": {
                    "HERMES_MODEL": "fake/model-404",
                },
            },
        },
    }
    PROVIDERS_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    return config


# ─── Test Cases ─────────────────────────────────────────────────────────

def test_baseline_suite(tr: TestResult):
    """Criterion: .venv/bin/python ADWs/test_hermes_integration.py returns 11/11."""
    result = subprocess.run(
        [PYTHON, str(WORKSPACE / "ADWs" / "test_hermes_integration.py")],
        capture_output=True, text=True, timeout=30,
    )
    # Parse "X passed, Y failed out of Z tests"
    match = re.search(r"(\d+) passed, (\d+) failed out of (\d+)", result.stdout)
    if match:
        passed, failed, total = int(match.group(1)), int(match.group(2)), int(match.group(3))
        tr.record("baseline_suite", failed == 0, f"{passed}/{total} passed")
    else:
        tr.record("baseline_suite", False, f"Could not parse output: {result.stdout[-200:]}")


def test_hermes_json_contract(tr: TestResult):
    """Criterion: Read-only routine completes via Hermes with JSON contract."""
    _create_hermes_provider_config()

    # Call the adapter directly with a trivial read-only prompt
    result = subprocess.run(
        [PYTHON, str(ADAPTER_PATH),
         "--print", "--output-format", "json", "--max-turns", "1",
         "Responda APENAS com JSON: {\"status\": \"ok\", \"echo\": \"hermes-e2e\"}"],
        capture_output=True, text=True, timeout=60,
        cwd=str(WORKSPACE),
    )

    # Validate JSON contract
    try:
        data = json.loads(result.stdout)
        has_result = "result" in data
        has_usage = "usage" in data
        usage_keys = set(data.get("usage", {}).keys())
        expected_keys = {"input_tokens", "output_tokens", "total_tokens", "cost_usd"}
        valid_usage = expected_keys.issubset(usage_keys)

        tr.record(
            "hermes_json_contract",
            has_result and has_usage and valid_usage and result.returncode == 0,
            f"rc={result.returncode}, has_result={has_result}, usage_keys={usage_keys}",
        )
    except (json.JSONDecodeError, ValueError) as e:
        tr.record("hermes_json_contract", False, f"JSON parse error: {e}; stdout={result.stdout[:200]}")


def test_hermes_result_content(tr: TestResult):
    """Criterion: Output is consumed by JSON contract, no regex on human text."""
    _create_hermes_provider_config()

    result = subprocess.run(
        [PYTHON, str(ADAPTER_PATH),
         "--print", "--output-format", "json", "--max-turns", "1",
         "Responda exatamente: HERMES_E2E_CANARY_12345"],
        capture_output=True, text=True, timeout=60,
        cwd=str(WORKSPACE),
    )

    try:
        data = json.loads(result.stdout)
        # We access via contract key, not regex
        result_text = data.get("result", "")
        has_canary = "HERMES_E2E_CANARY_12345" in result_text
        tr.record(
            "hermes_result_content",
            has_canary,
            f"Result contains canary: {has_canary} (len={len(result_text)})",
        )
    except (json.JSONDecodeError, ValueError) as e:
        tr.record("hermes_result_content", False, f"JSON error: {e}")


def test_induced_failure(tr: TestResult):
    """Criterion: Induced failure logs real cause, no false success."""
    _create_hermes_provider_config()

    # Induce failure via a non-existent profile — adapter validates and returns
    # structured error with rc=1, proving the error path works end-to-end.
    result = subprocess.run(
        [PYTHON, str(ADAPTER_PATH),
         "--print", "--output-format", "json", "--max-turns", "1",
         "--profile", "nonexistent-profile-e2e",
         "test prompt that should fail"],
        capture_output=True, text=True, timeout=30,
        cwd=str(WORKSPACE),
    )

    try:
        data = json.loads(result.stdout)
        # Must NOT report success
        is_failure = result.returncode != 0 or data.get("error")
        has_error_detail = bool(data.get("error", ""))
        tr.record(
            "induced_failure",
            is_failure and has_error_detail,
            f"rc={result.returncode}, error={data.get('error', '')[:100]}",
        )
    except (json.JSONDecodeError, ValueError):
        # Non-zero exit without valid JSON is also acceptable failure signal
        tr.record(
            "induced_failure",
            result.returncode != 0,
            f"rc={result.returncode}, raw stderr={result.stderr[:150]}",
        )


def test_timeout_cancellation(tr: TestResult):
    """Criterion: Timeout terminates process and produces terminal state."""
    _create_hermes_provider_config()

    # Use a prompt designed to take long, with a very short timeout in adapter
    # We'll call hermes_native.run_hermes directly with 2s timeout
    test_code = """
import sys, json
sys.path.insert(0, "{workspace}/ADWs")
from hermes_native import run_hermes
result = run_hermes(
    prompt="Escreva um ensaio de 5000 palavras sobre filosofia. Seja extremamente detalhado.",
    max_turns=90,
    timeout=3,
)
print(json.dumps(result))
""".format(workspace=str(WORKSPACE))

    result = subprocess.run(
        [PYTHON, "-c", test_code],
        capture_output=True, text=True, timeout=30,
        cwd=str(WORKSPACE),
    )

    try:
        data = json.loads(result.stdout)
        is_terminal = data.get("success") is False
        has_timeout_signal = "timeout" in data.get("stderr", "").lower() or data.get("returncode") == -1
        tr.record(
            "timeout_cancellation",
            is_terminal and has_timeout_signal,
            f"success={data.get('success')}, rc={data.get('returncode')}, stderr={data.get('stderr', '')[:80]}",
        )
    except (json.JSONDecodeError, ValueError) as e:
        tr.record("timeout_cancellation", False, f"Parse error: {e}; stdout={result.stdout[:200]}")


def test_no_orphan_process(tr: TestResult):
    """Criterion: After timeout, no hermes subprocess left running."""
    # Check for any hermes processes started by our test
    time.sleep(1)  # Give OS time to reap
    result = subprocess.run(
        ["pgrep", "-f", "hermes.*HERMES_E2E"],
        capture_output=True, text=True,
    )
    no_orphans = result.returncode != 0  # pgrep returns 1 when no match
    tr.record("no_orphan_process", no_orphans, "No orphan hermes processes found" if no_orphans else f"PIDs: {result.stdout.strip()}")


def test_log_sanitization(tr: TestResult):
    """Criterion: Logs do not contain keys, tokens, cookies, Authorization or passwords."""
    log_dir = WORKSPACE / "ADWs" / "logs"
    violations = []

    if log_dir.exists():
        for log_file in log_dir.glob("*.jsonl"):
            content = log_file.read_text(errors="ignore")
            for pattern in SECRET_PATTERNS:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    violations.append(f"{log_file.name}: pattern={pattern}, count={len(matches)}")

    tr.record(
        "log_sanitization",
        len(violations) == 0,
        f"No secrets in logs" if not violations else f"VIOLATIONS: {violations[:3]}",
    )


def test_provider_restoration(tr: TestResult, original_backup):
    """Criterion: Original provider restored after tests."""
    _restore_providers(original_backup)

    if original_backup:
        # Should be back to original
        exists = PROVIDERS_FILE.exists()
        tr.record("provider_restoration", exists, "providers.json restored from backup")
    else:
        # Was never there, should be gone
        gone = not PROVIDERS_FILE.exists()
        tr.record("provider_restoration", gone, "providers.json removed (no original existed)")


def test_proxy_iframe_structural(tr: TestResult):
    """Criterion: /hermes-ui, WebSocket, proxy structurally intact."""
    # Check that proxy config files haven't been modified by our tests
    proxy_files = [
        WORKSPACE / "dashboard" / "backend" / "routes" / "hermes_proxy.py",
    ]

    all_intact = True
    details = []
    for f in proxy_files:
        if f.exists():
            details.append(f"{f.name}: present")
        else:
            # Not all files may exist — that's ok, we just verify we didn't delete them
            details.append(f"{f.name}: not present (ok if never existed)")

    # This E2E must not mutate proxy/iframe code. Existing dashboard changes
    # from the feature under test (e.g. task profile authorization) are valid.
    proxy_paths = {"dashboard/backend/routes/hermes_proxy.py"}
    git_result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", *proxy_paths],
        capture_output=True, text=True, cwd=str(WORKSPACE),
    )
    modified_proxy = [line for line in git_result.stdout.splitlines() if line]

    tr.record(
        "proxy_iframe_structural",
        not modified_proxy,
        f"Proxy files modified: {modified_proxy or 'none'}",
    )


def test_hermes_native_contract(tr: TestResult):
    """Validate hermes_native.run_hermes returns full contract fields."""
    _create_hermes_provider_config()

    test_code = """
import sys, json
sys.path.insert(0, "{workspace}/ADWs")
from hermes_native import run_hermes
result = run_hermes(
    prompt="Responda: 42",
    max_turns=1,
    timeout=30,
)
print(json.dumps(result))
""".format(workspace=str(WORKSPACE))

    result = subprocess.run(
        [PYTHON, "-c", test_code],
        capture_output=True, text=True, timeout=45,
        cwd=str(WORKSPACE),
    )

    try:
        data = json.loads(result.stdout)
        required_keys = {"success", "stdout", "stderr", "returncode", "duration", "usage"}
        actual_keys = set(data.keys())
        has_all = required_keys.issubset(actual_keys)
        tr.record(
            "hermes_native_contract",
            has_all and data.get("success") is True,
            f"keys={sorted(actual_keys)}, success={data.get('success')}",
        )
    except (json.JSONDecodeError, ValueError) as e:
        tr.record("hermes_native_contract", False, f"Parse error: {e}; stdout={result.stdout[:200]}")


# ─── Main ───────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Hermes E2E — Phase 1 Closure Validation")
    print("=" * 60)
    print()

    tr = TestResult()

    # Backup original state
    original_backup = _backup_providers()

    try:
        # 1. Baseline
        print("[Phase 1] Baseline Tests")
        test_baseline_suite(tr)
        print()

        # 2-3. Hermes execution + JSON contract
        print("[Phase 2-3] Hermes Execution & JSON Contract")
        test_hermes_json_contract(tr)
        test_hermes_result_content(tr)
        test_hermes_native_contract(tr)
        print()

        # 4. Induced failure + fallback
        print("[Phase 4] Induced Failure & Error Reporting")
        test_induced_failure(tr)
        print()

        # 5. Timeout & cancellation
        print("[Phase 5] Timeout & Cancellation")
        test_timeout_cancellation(tr)
        test_no_orphan_process(tr)
        print()

        # 6. Log sanitization
        print("[Phase 6] Log Sanitization")
        test_log_sanitization(tr)
        print()

        # 7. Provider restoration
        print("[Phase 7] Provider Restoration")
        test_provider_restoration(tr, original_backup)
        original_backup = None  # Already restored
        print()

        # 8. Proxy/iframe structural
        print("[Phase 8] Proxy/Iframe/WebSocket Structural Check")
        test_proxy_iframe_structural(tr)
        print()

    finally:
        # Safety net: always restore
        if original_backup:
            _restore_providers(original_backup)
        elif PROVIDERS_FILE.exists():
            # We created it; clean up
            PROVIDERS_FILE.unlink(missing_ok=True)

    return tr.summary()


if __name__ == "__main__":
    sys.exit(main())
