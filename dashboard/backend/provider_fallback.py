"""Provider Fallback Engine — automatic 429/quota detection & provider rotation.

Reads the active provider from config/providers.json and its fallback_models /
fallback_providers configuration. When a subprocess call returns 429/quota
errors, this module cycles to the next model (within the same provider) or
to the next provider entirely.

Cooldown tracking prevents thrashing — a model/provider that 429'd gets a
cooldown window before we try it again.

Usage:
    from provider_fallback import FallbackEngine

    engine = FallbackEngine()
    for attempt in engine.attempts(prompt, max_turns=10, timeout=600):
        result = attempt.run()
        if result["status"] == "success":
            break
        # engine automatically records 429 and advances to next model/provider
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PROVIDERS_CONFIG = WORKSPACE / "config" / "providers.json"

# ── Error patterns that trigger fallback ──────────────────────────────────────

_429_PATTERNS = [
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"quota", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"resource.?exhausted", re.IGNORECASE),
    re.compile(r"capacity", re.IGNORECASE),
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"service.?unavailable", re.IGNORECASE),
    re.compile(r"temporarily.?unavailable", re.IGNORECASE),
    re.compile(r"insufficient_quota", re.IGNORECASE),
    re.compile(r"billing.?limit", re.IGNORECASE),
    re.compile(r"plan.?limit", re.IGNORECASE),
]

# Fatal errors that should NOT trigger fallback (auth / config issues)
_FATAL_PATTERNS = [
    re.compile(r"401", re.IGNORECASE),
    re.compile(r"403", re.IGNORECASE),
    re.compile(r"invalid.?api.?key", re.IGNORECASE),
    re.compile(r"authentication", re.IGNORECASE),
    re.compile(r"unauthorized", re.IGNORECASE),
    re.compile(r"forbidden", re.IGNORECASE),
]


def is_429_error(error_text: str) -> bool:
    """Check if error text indicates a 429/rate-limit/quota issue."""
    if not error_text:
        return False
    has_429 = any(p.search(error_text) for p in _429_PATTERNS)
    has_fatal = any(p.search(error_text) for p in _FATAL_PATTERNS)
    if has_fatal and not has_429:
        return False
    return has_429


# ── Cooldown tracking ─────────────────────────────────────────────────────────

_cooldowns: dict[str, float] = {}
DEFAULT_COOLDOWN_SECONDS = 300  # 5 min


def set_cooldown(key: str, duration_seconds: float = DEFAULT_COOLDOWN_SECONDS):
    _cooldowns[key] = time.time() + duration_seconds


def is_on_cooldown(key: str) -> bool:
    deadline = _cooldowns.get(key)
    if deadline is None:
        return False
    if time.time() > deadline:
        _cooldowns.pop(key, None)
        return False
    return True


def clear_cooldown(key: str):
    _cooldowns.pop(key, None)


def clear_all_cooldowns():
    _cooldowns.clear()


# ── Config reading ─────────────────────────────────────────────────────────────

def _read_providers_config() -> dict:
    try:
        if PROVIDERS_CONFIG.is_file():
            return json.loads(PROVIDERS_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {"active_provider": "nvidia", "providers": {}}


# ── Default fallback chains ───────────────────────────────────────────────────

# NVIDIA: models are independent — quota on one doesn't block others.
NVIDIA_MODEL_CHAIN = [
    "minimaxi/minimax-m3",            # Primary
    "z-ai/glm-5.1",                   # 2nd
    "deepseek-ai/deepseek-v4-flash",  # 3rd
    "qwen/qwen3.5-397b-a17b",        # 4th
    "stepfun-ai/step-3.7-flash",     # 5th (haiku tier)
]

# Provider chain: NVIDIA → OpenRouter (owl-alpha) → Codex → Claude nativo
DEFAULT_PROVIDER_CHAIN = [
    {
        "provider_id": "nvidia",
        "cli_command": "openclaude",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "env_vars": {
            "CLAUDE_CODE_USE_OPENAI": "1",
            "OPENAI_BASE_URL": "https://integrate.api.nvidia.com/v1",
        },
        "model_chain": NVIDIA_MODEL_CHAIN,
    },
    {
        "provider_id": "openrouter",
        "cli_command": "openclaude",
        "base_url": "https://openrouter.ai/api/v1",
        "env_vars": {
            "CLAUDE_CODE_USE_OPENAI": "1",
            "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
        },
        "model_chain": ["openrouter/owl-alpha"],
    },
    {
        "provider_id": "codex_auth",
        "cli_command": "openclaude",
        "base_url": None,
        "env_vars": {
            "CLAUDE_CODE_USE_OPENAI": "1",
        },
        "model_chain": ["codexplan", "codexspark"],
    },
    {
        "provider_id": "anthropic",
        "cli_command": "claude",
        "base_url": None,
        "env_vars": {},
        "model_chain": [None],  # claude binary uses its own model
    },
]


def _resolve_provider_chain(config: dict) -> list[dict]:
    """Build the provider chain from config, falling back to defaults."""
    active_id = config.get("active_provider", "nvidia")
    providers = config.get("providers", {})
    active_prov = providers.get(active_id, {})

    explicit_chain = active_prov.get("fallback_providers")
    if isinstance(explicit_chain, list) and explicit_chain:
        chain = [_build_provider_entry(active_id, providers)]
        for pid in explicit_chain:
            if pid in providers:
                chain.append(_build_provider_entry(pid, providers))
        return chain if len(chain) >= 2 else DEFAULT_PROVIDER_CHAIN

    return DEFAULT_PROVIDER_CHAIN


def _build_provider_entry(provider_id: str, providers: dict) -> dict:
    prov = providers.get(provider_id, {})
    env_vars = {k: v for k, v in prov.get("env_vars", {}).items()
                if v and k not in ("OPENAI_API_KEY", "OPENAI_MODEL")}
    model_chain = prov.get("fallback_models",
        [prov.get("default_model") or prov.get("env_vars", {}).get("OPENAI_MODEL")])

    return {
        "provider_id": provider_id,
        "cli_command": prov.get("cli_command", "openclaude"),
        "base_url": prov.get("default_base_url") or prov.get("env_vars", {}).get("OPENAI_BASE_URL"),
        "env_vars": env_vars,
        "model_chain": [m for m in model_chain if m],
    }


def _get_api_key(provider_id: str, config: dict) -> str:
    prov = config.get("providers", {}).get(provider_id, {})
    env_vars = prov.get("env_vars", {})
    for key_name in ("OPENAI_API_KEY", "NVIDIA_API_KEY", "GEMINI_API_KEY"):
        val = env_vars.get(key_name, "")
        if val and "****" not in val:
            return val
    return os.environ.get("OPENAI_API_KEY", "")


# ── Attempt record ─────────────────────────────────────────────────────────────

@dataclass
class FallbackAttempt:
    attempt_number: int
    provider_id: str
    model: str | None
    cli_command: str
    prompt: str
    max_turns: int
    timeout_seconds: int
    env_overrides: dict = field(default_factory=dict)
    _result: dict | None = field(default=None, repr=False)

    def run(self) -> dict:
        self._result = _invoke_cli(
            cli_command=self.cli_command,
            prompt=self.prompt,
            max_turns=self.max_turns,
            timeout_seconds=self.timeout_seconds,
            env_overrides=self.env_overrides,
        )
        return self._result

    @property
    def result(self) -> dict | None:
        return self._result

    @property
    def is_429(self) -> bool:
        if not self._result:
            return False
        error = self._result.get("error") or ""
        output = self._result.get("output") or ""
        return is_429_error(error) or is_429_error(output)

    @property
    def is_fatal(self) -> bool:
        if not self._result:
            return False
        if self._result.get("status") == "success":
            return False
        if self.is_429:
            return False
        return True


# ── Core invocation ─────────────────────────────────────────────────────────────

def _invoke_cli(
    cli_command: str,
    prompt: str,
    max_turns: int,
    timeout_seconds: int,
    env_overrides: dict | None = None,
) -> dict:
    cli_bin = shutil.which(cli_command)
    if not cli_bin:
        return {
            "status": "fail",
            "error": f"{cli_command} binary not found in PATH",
            "output": "",
            "duration_ms": 0,
            "tokens_in": None, "tokens_out": None, "cost_usd": None,
        }

    cmd = [cli_bin, "--print", "--max-turns", str(max_turns),
           "--dangerously-skip-permissions", "--output-format", "json", "--", prompt]

    run_env = dict(os.environ)
    if env_overrides:
        for k, v in env_overrides.items():
            if v is not None:
                run_env[k] = str(v)

    start_time = time.time()
    proc = None
    output = ""
    error = None
    status = "success"

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(WORKSPACE), start_new_session=True, env=run_env,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            output = stdout or ""
            if proc.returncode != 0:
                status = "fail"
                error = stderr[:2000] if stderr else f"exit code {proc.returncode}"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            status = "timeout"
            error = f"Killed after {timeout_seconds}s timeout"
    except Exception as exc:
        status = "fail"
        error = str(exc)

    duration_ms = int((time.time() - start_time) * 1000)

    return {
        "status": status, "output": output, "error": error,
        "duration_ms": duration_ms,
        "tokens_in": None, "tokens_out": None, "cost_usd": None,
    }


# ── Fallback Engine ─────────────────────────────────────────────────────────────

class FallbackEngine:
    """Iterates through provider + model chains, falling back on 429/quota errors."""

    def __init__(self, provider_chain: list[dict] | None = None):
        if provider_chain is None:
            config = _read_providers_config()
            provider_chain = _resolve_provider_chain(config)
        self.provider_chain = provider_chain
        self._attempts_log: list[FallbackAttempt] = []

    def attempts(
        self,
        prompt: str,
        max_turns: int = 10,
        timeout_seconds: int = 600,
        agent: str = "",
        force_provider: str | None = None,
        force_model: str | None = None,
    ) -> Iterator[FallbackAttempt]:
        config = _read_providers_config()
        attempt_num = 0

        chain = self.provider_chain
        if force_provider:
            chain = [p for p in chain if p["provider_id"] == force_provider]
            if not chain and force_provider == "nvidia":
                chain = [DEFAULT_PROVIDER_CHAIN[0]]

        for provider_entry in chain:
            provider_id = provider_entry["provider_id"]
            cli_command = provider_entry["cli_command"]
            base_url = provider_entry.get("base_url")
            model_chain = provider_entry.get("model_chain", [None])
            base_env = dict(provider_entry.get("env_vars", {}))

            if base_url:
                base_env["OPENAI_BASE_URL"] = base_url

            api_key = _get_api_key(provider_id, config)
            if api_key:
                base_env["OPENAI_API_KEY"] = api_key

            for model in model_chain:
                if force_model and model != force_model:
                    continue

                cooldown_key = f"{provider_id}:{model}" if model else provider_id
                if is_on_cooldown(cooldown_key):
                    continue

                # Provider-level cooldown (NVIDIA exempt — models independent)
                if is_on_cooldown(provider_id) and provider_id != "nvidia":
                    continue

                attempt_num += 1

                env_overrides = dict(base_env)
                if model:
                    env_overrides["OPENAI_MODEL"] = model

                attempt = FallbackAttempt(
                    attempt_number=attempt_num,
                    provider_id=provider_id,
                    model=model,
                    cli_command=cli_command,
                    prompt=prompt,
                    max_turns=max_turns,
                    timeout_seconds=timeout_seconds,
                    env_overrides=env_overrides,
                )
                self._attempts_log.append(attempt)
                yield attempt

                # After caller runs the attempt, check result
                if attempt.result is None:
                    continue

                if attempt.result.get("status") == "success":
                    return

                if attempt.is_429:
                    set_cooldown(cooldown_key)
                    print(f"[fallback] 429 on {provider_id}:{model} — "
                          f"cooldown {DEFAULT_COOLDOWN}s, trying next", flush=True)
                    continue

                if attempt.is_fatal:
                    print(f"[fallback] fatal error on {provider_id}:{model} — "
                          f"skipping to next provider", flush=True)
                    break

                continue

    @property
    def attempts_log(self) -> list[FallbackAttempt]:
        return list(self._attempts_log)

    def last_successful_attempt(self) -> FallbackAttempt | None:
        for a in reversed(self._attempts_log):
            if a.result and a.result.get("status") == "success":
                return a
        return None


# ── Convenience: single call with automatic fallback ───────────────────────────

def invoke_with_fallback(
    prompt: str,
    max_turns: int = 10,
    timeout_seconds: int = 600,
    agent: str = "",
    force_provider: str | None = None,
    force_model: str | None = None,
) -> dict:
    """Invoke CLI with automatic 429 fallback. Returns the first successful result."""
    engine = FallbackEngine()
    last_result = None

    for attempt in engine.attempts(
        prompt=prompt, max_turns=max_turns, timeout_seconds=timeout_seconds,
        agent=agent, force_provider=force_provider, force_model=force_model,
    ):
        result = attempt.run()
        result["provider_id"] = attempt.provider_id
        result["model"] = attempt.model
        result["attempt_number"] = attempt.attempt_number

        if result["status"] == "success":
            if attempt.attempt_number > 1:
                print(f"[fallback] SUCCESS on attempt #{attempt.attempt_number} "
                      f"({attempt.provider_id}:{attempt.model})", flush=True)
            return result

        last_result = result
        print(f"[fallback] attempt #{attempt.attempt_number} failed "
              f"({attempt.provider_id}:{attempt.model}) status={result['status']}", flush=True)

    if last_result:
        last_result["fallback_exhausted"] = True
        last_result["total_attempts"] = len(engine.attempts_log)
    else:
        last_result = {
            "status": "fail",
            "error": "No attempts made — all providers on cooldown or no chain configured",
            "output": "", "duration_ms": 0,
            "fallback_exhausted": True, "total_attempts": 0,
        }

    return last_result
