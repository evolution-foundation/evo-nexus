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

import contextlib
import fcntl
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
    re.compile(r"maximum combo retry limit reached", re.IGNORECASE),
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

# A model that hangs (no response, no error, no 429) previously ate the FULL
# timeout_seconds of a single attempt before the chain could advance — with
# timeout_seconds=900 and 12 NVIDIA models, worst case was 3h stuck on one
# call. Confirmed live 2026-07-14 (ai-news-weekly-x-research: sage step stuck
# 11+ min with zero attempt-failed log lines). Cap each attempt so a hang
# gets cut and the chain rotates quickly; the overall deadline below still
# respects the caller's timeout_seconds as a TOTAL budget.
PER_ATTEMPT_TIMEOUT_CAP = 180


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
    """OpenClaude can misparse --agent frontmatter; embed persona in prompt.

    Reused as-is for opencode (validated 2026-07-12, spike/opencode-runtime):
    same embed-in-prompt pattern works unmodified, no adaptation needed.
    """
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

    prov = config.get("providers", {}).get(provider_id, {})
    env_vars = prov.get("env_vars", {})
    base_url = (env_vars.get("OPENAI_BASE_URL") or prov.get("default_base_url") or "").lower()

    # A chave do próprio provider vence. config/providers.json pode conter
    # placeholders [REDACTED] — _usable_secret filtra e caímos no env do
    # processo. A NVIDIA_API_KEY do env só vale para endpoints da NVIDIA:
    # antes ela tinha precedência global e sequestrava chamadas a outros
    # gateways (omnirouter recebia a chave NVIDIA → 401).
    for key_name in ("OPENAI_API_KEY", "NVIDIA_API_KEY", "GEMINI_API_KEY"):
        val = env_vars.get(key_name, "")
        if _usable_secret(val):
            return val

    fallback_keys = ["OPENAI_API_KEY", "GEMINI_API_KEY"]
    if "nvidia.com" in base_url:
        fallback_keys.insert(0, "NVIDIA_API_KEY")
    for key_name in fallback_keys:
        value = os.environ.get(key_name, "")
        if _usable_secret(value):
            return value

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
    cwd: "Path | None" = None
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
            cwd=self.cwd,
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


# ── Agent subprocess env isolation (goal-ticket-unification V10) ────────────
# Two call sites build the env for an agent subprocess: _invoke_cli below,
# and heartbeat_runner._step7_invoke_claude_native (the legacy direct path,
# used when provider fallback is disabled/unavailable). Both MUST go through
# this one helper — agents run with --dangerously-skip-permissions, so
# whatever lands in their env is theirs to use. Before this, both call sites
# did `dict(os.environ)` unfiltered, meaning an agent could read
# APPROVAL_BRIDGE_TOKEN out of its own env and call the approval-decision
# endpoint to approve its own publish, or read social-platform credentials
# and publish directly instead of going through the approval gate at all
# (Vault V1c/V2). DASHBOARD_API_TOKEN is NOT denylisted: it is what
# sdk_client.EvoClient uses for every legitimate agent call (create-ticket,
# manage-heartbeats, the goal-planner heartbeat, etc.), and it alone never
# authorizes /api/approvals/*/decision — that endpoint requires the separate
# APPROVAL_BRIDGE_TOKEN (see routes/_helpers.py::valid_approval_bridge_token),
# which stays denylisted below. Denylist, not allowlist, because most of
# os.environ (PATH, locale, provider config, etc.) legitimately needs to
# reach the agent.
_AGENT_ENV_DENYLIST_EXACT = {"APPROVAL_BRIDGE_TOKEN", "POSTIZ_API_KEY"}
_AGENT_ENV_DENYLIST_PREFIXES = ("SOCIAL_", "INSTAGRAM_", "LINKEDIN_", "TWITTER_", "DISCORD_")


def _build_agent_run_env(env_overrides: dict | None = None) -> dict:
    """Build the subprocess env for an agent run, minus the denylisted keys."""
    run_env = {
        k: v for k, v in os.environ.items()
        if k not in _AGENT_ENV_DENYLIST_EXACT and not k.startswith(_AGENT_ENV_DENYLIST_PREFIXES)
    }
    if env_overrides:
        for k, v in env_overrides.items():
            if v is not None:
                run_env[k] = str(v)
    # Impede o CLI de se auto-migrar pro instalador nativo no meio da run
    # (mata o processo com exit 1).
    run_env["DISABLE_AUTOUPDATER"] = "1"
    return run_env


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
    cwd: Path | None = None,
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

    output_mode = "envelope"
    if cli_command == "opencode":
        # opencode não tem --max-turns nem --dangerously-skip-permissions — o
        # equivalente de bypass de permissão é --auto (validado spike
        # 2026-07-12). Seleção de modelo é via -m provider/model, não por
        # env var OPENAI_MODEL — provider_id precisa bater com uma entry em
        # opencode.json (ver opencode.json na raiz do workspace).
        prompt = _embed_agent_for_openclaude(prompt, agent)
        agent = ""
        model_ref = f"{provider_id}/{model}" if model else f"{provider_id}/auto"
        cmd = [cli_bin, "run", prompt, "-m", model_ref, "--format", "json", "--auto"]
        output_mode = "opencode-ndjson"
    else:
        cmd = [cli_bin, "--print", "--max-turns", str(max_turns),
               "--dangerously-skip-permissions", "--output-format", "json"]
        if cli_command == "openclaude":
            prompt = _embed_agent_for_openclaude(prompt, agent)
            agent = ""
        if agent:
            cmd.extend(["--agent", agent])
        cmd.extend(["--", prompt])

    run_env = _build_agent_run_env(env_overrides)

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
        return _invoke_cli_run(cmd, run_env, timeout_seconds, cwd or WORKSPACE, output_mode=output_mode)
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



def _parse_opencode_ndjson(output: str) -> dict:
    """opencode --format json emits one JSON event per line (step_start, text,
    step_finish, ...) instead of Claude Code's single envelope. Validated
    2026-07-12 against a real OmniRoute call — step_finish carries
    tokens.{input,output} and cost; text events carry the assistant's reply.
    """
    tokens_in = tokens_out = cost_usd = None
    text_parts: list[str] = []
    saw_error = False
    error_message = ""
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        etype = event.get("type")
        part = event.get("part") or {}
        if etype == "text":
            t = part.get("text")
            if t:
                text_parts.append(t)
        elif etype == "error":
            saw_error = True
            err = event.get("error") or {}
            msg = err.get("data", {}).get("message") if isinstance(err.get("data"), dict) else None
            error_message = msg or err.get("name") or error_message
        elif etype == "step_finish":
            usage = part.get("tokens") or {}
            if usage.get("input") is not None:
                tokens_in = usage.get("input")
            if usage.get("output") is not None:
                tokens_out = usage.get("output")
            if part.get("cost") is not None:
                cost_usd = part.get("cost")
    return {
        "text": "\n".join(text_parts),
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
        "saw_error": saw_error, "error_message": error_message,
    }


def _invoke_cli_run(cmd: list, run_env: dict, timeout_seconds: int, workspace: Path,
                     output_mode: str = "envelope") -> dict:
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
    if output_mode == "opencode-ndjson":
        parsed = _parse_opencode_ndjson(output)
        tokens_in, tokens_out, cost_usd = parsed["tokens_in"], parsed["tokens_out"], parsed["cost_usd"]
        if parsed["saw_error"]:
            status = "fail"
            # troca o "exit code 1" genérico pela mensagem real do evento de
            # erro do opencode — is_429_error()/is_fatal também escaneiam
            # `output` (o ndjson bruto), então a detecção de 429/fatal já
            # funcionava antes disso; isso só melhora a legibilidade do log.
            if parsed["error_message"] and (not error or error.startswith("exit code")):
                error = parsed["error_message"]
            elif not error:
                error = "opencode emitted an error event in the ndjson stream"
        if status == "success" and not parsed["text"] and tokens_in is None:
            # nem texto nem step_finish — stream vazio/quebrado, não confia
            status = "fail"
            error = error or "opencode produced no text/step_finish events"
    else:
        try:
            envelope = json.loads(output)
            usage = envelope.get("usage") or {}
            tokens_in = usage.get("input_tokens")
            tokens_out = usage.get("output_tokens")
            cost_usd = envelope.get("total_cost_usd")
            if status != "success" and envelope.get("type") == "result" and envelope.get("result") and envelope.get("is_error") is False:
                status = "success"
                error = None
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
        cwd: Path | None = None,
    ) -> Iterator[FallbackAttempt]:
        config = _read_providers_config()
        attempt_num = 0
        deadline = time.time() + timeout_seconds

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

                remaining = deadline - time.time()
                if remaining < 20:
                    return
                attempt_timeout = int(min(remaining, PER_ATTEMPT_TIMEOUT_CAP))

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
                    timeout_seconds=attempt_timeout,
                    agent=agent,
                    env_overrides=env_overrides,
                    cwd=cwd,
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

# ── Cross-container workspace-bash mutex ─────────────────────────────────────
# Heartbeats (dashboard service) and the Telegram orchestrator (telegram
# service, since the 2026-07-15 rewire) both funnel through this function,
# each running in its OWN container, and both can spawn a real Bash/Agent-
# capable CLI session against the SAME /workspace tree with zero coordination
# — two concurrent runs (e.g. two Telegram chats, or a heartbeat + a Telegram
# message) can race on the same file or git working tree.
#
# A DB-row lock (the tickets.locked_at/locked_by pattern) won't work here:
# dashboard.db lives on the evonexus_dashboard_data volume, which is only
# mounted in the dashboard service — telegram and scheduler don't have it, so
# a sqlite-based lock would silently be per-container and coordinate nothing.
# evonexus_workspace IS mounted at the same path (/workspace/workspace) in
# all three services, so flock() on a file there is a real, OS-enforced,
# cross-container mutex — and unlike the ticket lock, it needs no janitor
# sweep: the OS releases it automatically if the holding process dies.
_ORCH_LOCK_PATH = WORKSPACE / "workspace" / ".locks" / "orchestrator-bash.lock"
_ORCH_LOCK_WAIT_SECONDS = float(os.environ.get("ORCHESTRATOR_LOCK_WAIT_SECONDS", "120"))
_ORCH_LOCK_POLL_SECONDS = 0.5


@contextlib.contextmanager
def _workspace_bash_lock(holder: str) -> Iterator[None]:
    """Block until the workspace-wide Bash mutex is free, then hold it.

    Raises TimeoutError if it can't acquire within _ORCH_LOCK_WAIT_SECONDS —
    callers should turn that into a clean "busy" result rather than letting
    it propagate as an unhandled exception.
    """
    _ORCH_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_ORCH_LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.time() + _ORCH_LOCK_WAIT_SECONDS
    acquired = False
    try:
        while time.time() < deadline:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                time.sleep(_ORCH_LOCK_POLL_SECONDS)
        if not acquired:
            raise TimeoutError(
                f"workspace busy — another agentic run held the lock for {_ORCH_LOCK_WAIT_SECONDS}s"
            )
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{holder}:pid={os.getpid()}:since={time.time()}\n".encode())
        except OSError:
            pass  # best-effort observability only, never block on it
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def invoke_with_fallback(
    prompt: str,
    max_turns: int = 10,
    timeout_seconds: int = 600,
    agent: str = "",
    force_provider: str | None = None,
    force_model: str | None = None,
    cwd: Path | None = None,
) -> dict:
    """Invoke CLI with automatic 429 fallback. Returns the first successful result.

    `cwd` (social-media-production): when given, the subprocess runs in that
    directory instead of the shared repo-root WORKSPACE, and the
    cross-container `_workspace_bash_lock` is skipped entirely — that mutex
    exists to protect concurrent Bash/Agent sessions racing on the shared
    `/workspace` git worktree (heartbeats vs. the Telegram orchestrator); an
    isolated job directory (e.g. a media-worker rendering job) is not part
    of that tree and gains nothing from serializing behind it. Concurrency
    for isolated-cwd callers is the caller's own responsibility (e.g. the
    media-worker's replicas=1 + per-job DB lock).
    """
    if cwd is not None:
        return _invoke_with_fallback_locked(
            prompt=prompt, max_turns=max_turns, timeout_seconds=timeout_seconds,
            agent=agent, force_provider=force_provider, force_model=force_model, cwd=cwd,
        )
    holder = f"agent={agent or 'none'}"
    try:
        with _workspace_bash_lock(holder):
            return _invoke_with_fallback_locked(
                prompt=prompt, max_turns=max_turns, timeout_seconds=timeout_seconds,
                agent=agent, force_provider=force_provider, force_model=force_model,
            )
    except TimeoutError as exc:
        print(f"[fallback] {exc}", flush=True)
        return {
            "status": "busy",
            "error": str(exc),
            "output": "", "duration_ms": 0,
            "fallback_exhausted": False, "total_attempts": 0,
        }


def _invoke_with_fallback_locked(
    prompt: str,
    max_turns: int = 10,
    timeout_seconds: int = 600,
    agent: str = "",
    force_provider: str | None = None,
    force_model: str | None = None,
    cwd: Path | None = None,
) -> dict:
    engine = FallbackEngine()
    last_result = None

    for attempt in engine.attempts(
        prompt=prompt, max_turns=max_turns, timeout_seconds=timeout_seconds,
        agent=agent, force_provider=force_provider, force_model=force_model, cwd=cwd,
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
