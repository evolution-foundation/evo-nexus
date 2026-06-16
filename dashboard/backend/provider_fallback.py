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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PROVIDERS_CONFIG = WORKSPACE / "config" / "providers.json"


def _usable_secret(value: str | None) -> bool:
    if not value:
        return False
    value = value.strip()
    return value not in {"[REDACTED]", "REDACTED", "your_bot_token_here", "your_chat_id_here"}


def _load_workspace_env() -> None:
    """Load root .env without depending on python-dotenv."""
    env_file = WORKSPACE / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and _usable_secret(value):
            os.environ.setdefault(key, value)


_load_workspace_env()

# ── Per-model inflight lock ──────────────────────────────────────────────────
# Prevents two heartbeat threads from picking the same model at the same
# instant and getting back-to-back 429s. Each call acquires the model key for
# the duration of its HTTP round-trip; the next caller will pick a different
# model from the chain instead of doubling up.
_inflight_locks: dict[str, threading.Lock] = {}
_inflight_locks_guard = threading.Lock()


def _model_lock(model_key: str) -> threading.Lock:
    with _inflight_locks_guard:
        lk = _inflight_locks.get(model_key)
        if lk is None:
            lk = threading.Lock()
            _inflight_locks[model_key] = lk
        return lk

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
DEFAULT_COOLDOWN_SECONDS = 60   # 1 min — faster rotation through the NVIDIA chain


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


def _agent_prompt(agent: str | None) -> str:
    """Return agent persona text without YAML frontmatter."""
    if not agent:
        return ""
    agent_file = WORKSPACE / ".claude" / "agents" / f"{agent}.md"
    try:
        content = agent_file.read_text(encoding="utf-8")
    except OSError:
        return f"You are the {agent} agent."
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            content = parts[2]
    for marker in ("\n# Persistent Agent Memory", "\n## MEMORY.md"):
        if marker in content:
            content = content.split(marker, 1)[0]
    return content.strip()


def _embed_agent_for_openclaude(prompt: str, agent: str | None) -> str:
    """OpenClaude can misparse --agent frontmatter; embed persona in prompt."""
    persona = _agent_prompt(agent)
    if not persona:
        return prompt
    return (
        f"{persona}\n\n"
        f"CRITICAL: You MUST fully embody this agent persona. "
        f"You are NOT Claude, OpenClaude, or a generic assistant — you ARE {agent}. "
        f"Never break character. Follow ALL instructions above.\n\n"
        f"---\n\nTask:\n{prompt}"
    )


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
# Primary is GLM 5.1 on NVIDIA; if quota/rate-limit hits, rotate inside
# NVIDIA first, then leave the provider only after these core models fail.
# NVIDIA chain — 12 models validated on integrate.api.nvidia.com/v1.
# Two principles shape the order:
#   1. Don't double up on the same model — multiple agents pulling the same
#      model at once produces burst 429s that waste quotas. The dispatcher
#      uses per-model inflight locks above to stagger picks; this list is the
#      fallback rotation when one model hits quota.
#   2. Variety matters more than "best first" because of cooldown windows —
#      if z-ai/glm-5.1 just bounced, we should be able to keep working
#      without leaving NVIDIA. Hence deepseek + kimi + mistral + stepfun all
#      having slots equal to their flagship counterparts.
#
# Felipe-specified order (validated 2026-06-16, returning 200 on /chat/completions).
NVIDIA_MODEL_CHAIN = [
    "stepfun-ai/step-3.7-flash",            # 1  — StepFun 3.7 Flash
    "deepseek-ai/deepseek-v4-flash",        # 2  — DeepSeek V4 Flash
    "z-ai/glm-5.1",                         # 3  — Z.AI GLM 5.1 (flagship)
    "moonshotai/kimi-k2.6",                 # 4  — Moonshot Kimi K2.6
    "nvidia/nemotron-3-ultra-550b-a55b",    # 5  — NVIDIA Nemotron 3 Ultra 550B
    "nvidia/nemotron-3-super-120b-a12b",    # 6  — NVIDIA Nemotron 3 Super 120B
    "qwen/qwen3.5-122b-a10b",               # 7  — Qwen 3.5 122B A10B
    "qwen/qwen3.5-397b-a17b",               # 8  — Qwen 3.5 397B A17B (big MoE)
    "openai/gpt-oss-120b",                  # 9  — OpenAI GPT-OSS 120B
    "microsoft/phi-4-multimodal-instruct",  # 10 — Microsoft Phi-4 Multimodal
    "stepfun-ai/step-3.5-flash",            # 11 — StepFun 3.5 Flash
    "minimaxai/minimax-m3",                 # 12 — MiniMax M3
]

# Provider chain: NVIDIA (12 models) → OpenRouter (owl-alpha + nex-n2-pro free) → Claude
# NVIDIA is always first — OpenRouter only as last resort when all 12 NVIDIA models are on cooldown.
# OpenRouter models must be FREE (no paid tier) to avoid burning credits unintentionally.
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
        "model_chain": [
            "openrouter/owl-alpha",
            "nex-agi/nex-n2-pro:free",
        ],
    },
    {
        "provider_id": "anthropic",
        "cli_command": "claude",
        "base_url": None,
        "env_vars": {},
        "model_chain": [None],
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

    primary_model = prov.get("default_model") or prov.get("env_vars", {}).get("OPENAI_MODEL")
    model_chain = []
    if primary_model:
        model_chain.append(primary_model)
    for fallback_model in prov.get("fallback_models", []):
        if fallback_model and fallback_model not in model_chain:
            model_chain.append(fallback_model)

    if not model_chain and prov.get("cli_command") == "claude":
        model_chain = [None]

    return {
        "provider_id": provider_id,
        "cli_command": prov.get("cli_command", "openclaude"),
        "base_url": prov.get("default_base_url") or prov.get("env_vars", {}).get("OPENAI_BASE_URL"),
        "env_vars": env_vars,
        "model_chain": model_chain,
    }


def _get_api_key(provider_id: str, config: dict) -> str:
    if provider_id in {"codex_auth", "anthropic"}:
        return ""

    # Prefer real process env vars first. config/providers.json may contain
    # [REDACTED] placeholders; never send those to the API.
    for key_name in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        value = os.environ.get(key_name, "")
        if _usable_secret(value):
            return value

    prov = config.get("providers", {}).get(provider_id, {})
    env_vars = prov.get("env_vars", {})
    for key_name in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        val = env_vars.get(key_name, "")
        if _usable_secret(val):
            return val

    return ""


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
    agent: str = ""
    env_overrides: dict = field(default_factory=dict)
    _result: dict | None = field(default=None, repr=False)

    def run(self) -> dict:
        self._result = _invoke_cli(
            cli_command=self.cli_command,
            prompt=self.prompt,
            max_turns=self.max_turns,
            timeout_seconds=self.timeout_seconds,
            agent=self.agent,
            env_overrides=self.env_overrides,
            provider_id=self.provider_id,
            model=self.model,
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
        """Fatal means auth/config errors that are unlikely to be fixed by another model."""
        if not self._result:
            return False
        if self._result.get("status") == "success":
            return False
        if self.is_429:
            return False
        error = (self._result.get("error") or self._result.get("output") or "").lower()
        fatal_terms = (
            "invalid_api_key", "invalid api key", "401", "403",
            "unauthorized", "forbidden", "authentication", "api key",
            "no usable api key", "has no usable api key",
        )
        return any(term in error for term in fatal_terms)


# ── Core invocation ─────────────────────────────────────────────────────────────

def _invoke_cli(
    cli_command: str,
    prompt: str,
    max_turns: int,
    timeout_seconds: int,
    agent: str = "",
    env_overrides: dict | None = None,
    provider_id: str | None = None,
    model: str | None = None,
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
           "--dangerously-skip-permissions", "--output-format", "json"]
    if cli_command == "openclaude":
        prompt = _embed_agent_for_openclaude(prompt, agent)
        agent = ""
    if agent:
        cmd.extend(["--agent", agent])
    cmd.extend(["--", prompt])

    run_env = dict(os.environ)
    if env_overrides:
        for k, v in env_overrides.items():
            if v is not None:
                run_env[k] = str(v)

    # Acquire a per-model inflight lock so concurrent calls cannot pick the same
    # model at the same instant (the canonical cause of burst 429s on shared
    # providers). Non-blocking acquire: if another worker is mid-flight against
    # this model, return a sentinel and let the caller skip to the next chain
    # member instead of doubling up the quota burn.
    model_key = f"{provider_id}:{model}" if model else f"{provider_id}:native-{os.getpid()}"
    lk = _model_lock(model_key)
    if not lk.acquire(blocking=False):
        return {
            "status": "fail",
            "error": f"model {model} busy (concurrent call in flight) — skip to next model",
            "output": "",
            "duration_ms": 0,
            "tokens_in": None, "tokens_out": None, "cost_usd": None,
            "fallback_exhausted": False,
            "skip_advance_model": True,
        }
    try:
        return _invoke_cli_run(cmd, run_env, timeout_seconds, WORKSPACE)
    finally:
        lk.release()

    # The CLI emits a JSON result envelope (--output-format json) carrying token
    # usage and cost — parse it so heartbeat runs land accurate numbers on the
    # /costs page instead of nulls. Best-effort: never let parsing break a run.
    tokens_in = tokens_out = cost_usd = None
    try:
        envelope = json.loads(output)
        usage = envelope.get("usage") or {}
        tokens_in = usage.get("input_tokens")
        tokens_out = usage.get("output_tokens")
        cost_usd = envelope.get("total_cost_usd")
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    return {
        "status": status, "output": output, "error": error,
        "duration_ms": duration_ms,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
    }



def _invoke_cli_run(cmd: list, run_env: dict, timeout_seconds: int, workspace: Path) -> dict:
    """Inner run — assume the per-model inflight lock is already held.
    Holds the subprocess, parses tokens, applies backoff on 429, returns dict.
    """
    import subprocess as _sp
    start_time = time.time()
    proc = None
    output = ""
    error = None
    status = "success"

    try:
        proc = _sp.Popen(
            cmd, stdout=_sp.PIPE, stderr=_sp.PIPE,
            text=True, cwd=str(workspace), start_new_session=True, env=run_env,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            output = stdout or ""
            if proc.returncode != 0:
                status = "fail"
                error = stderr[:2000] if stderr else f"exit code {proc.returncode}"
        except _sp.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                proc.kill()
            try:
                proc.communicate(timeout=5)
            except _sp.TimeoutExpired:
                pass
            status = "timeout"
            error = f"Killed after {timeout_seconds}s timeout"
    except Exception as exc:
        status = "fail"
        error = str(exc)

    duration_ms = int((time.time() - start_time) * 1000)

    tokens_in = tokens_out = cost_usd = None
    try:
        envelope = json.loads(output)
        usage = envelope.get("usage") or {}
        tokens_in = usage.get("input_tokens")
        tokens_out = usage.get("output_tokens")
        cost_usd = envelope.get("total_cost_usd")
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    return {
        "status": status, "output": output, "error": error,
        "duration_ms": duration_ms,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
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
                # openclaude ≥0.18 detects the NVIDIA NIM base URL and requires
                # the key in NVIDIA_API_KEY — derive it so auth doesn't fail and
                # wrongly skip to the next provider. Mirrors provider-config.js.
                if "nvidia.com" in (base_url or "") and "NVIDIA_API_KEY" not in base_env:
                    base_env["NVIDIA_API_KEY"] = api_key

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
                    agent=agent,
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
                          f"cooldown {DEFAULT_COOLDOWN_SECONDS}s, trying next", flush=True)
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

        if result.get("skip_advance_model"):
            print(f"[fallback] skip {attempt.provider_id}:{attempt.model} ({result.get('error')})", flush=True)
            continue

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
