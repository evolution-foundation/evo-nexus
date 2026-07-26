"""Provider-aware normalized subprocess runtime."""

from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from secret_redaction import redact_secrets

WORKSPACE = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RuntimeRequest:
    origin_type: str
    origin_id: str
    agent_slug: str
    prompt: str
    max_turns: int
    timeout_seconds: int
    requested_profile: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class RuntimeResult:
    status: str
    provider: str
    requested_profile: str | None
    resolved_profile: str | None
    agent_slug: str
    output: str = ""
    error: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    fallback_from: str | None = None


class RuntimeService:
    def __init__(self, providers_path: Path | None = None):
        self.providers_path = providers_path or WORKSPACE / "config" / "providers.json"

    def _config(self) -> dict:
        try:
            return json.loads(self.providers_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Provider configuration unavailable: {exc}") from exc

    def _provider(self) -> tuple[str, dict, dict]:
        config = self._config()
        provider_id = config.get("active_provider")
        provider = config.get("providers", {}).get(provider_id)
        if not provider_id or not provider:
            raise RuntimeError("No active provider configured")
        return provider_id, provider, config

    def _resolve_profile(self, request: RuntimeRequest, provider_id: str) -> str | None:
        if provider_id != "hermes":
            return None
        try:
            from hermes_profiles import resolve_profile
            return resolve_profile("heartbeat", request.requested_profile)[0]
        except Exception:
            # Agent identity remains distinct from privileges. Do not invent a profile.
            raise RuntimeError(f"No usable Hermes profile for agent '{request.agent_slug}'")

    @staticmethod
    def _command(provider: str, cli: str, request: RuntimeRequest, profile: str | None) -> tuple[list[str], dict]:
        env = os.environ.copy()
        if cli == "hermes" or provider == "hermes":
            if not profile:
                raise RuntimeError("Hermes invocation requires a resolved profile")
            env["HERMES_MAX_ITERATIONS"] = str(request.max_turns)
            return [cli, "-p", profile, "chat", "-Q", "-q", request.prompt], env
        return [cli, "--print", "--max-turns", str(request.max_turns), "--dangerously-skip-permissions", "--output-format", "json", request.prompt], env

    def _invoke_provider(self, provider_id: str, provider: dict, request: RuntimeRequest, fallback_from: str | None = None) -> RuntimeResult:
        cli = provider.get("cli_command")
        if not isinstance(cli, str) or not cli:
            return RuntimeResult("failed", provider_id, request.requested_profile, None, request.agent_slug, error="Active provider has no CLI command", fallback_from=fallback_from)
        if not shutil.which(cli):
            return RuntimeResult("failed", provider_id, request.requested_profile, None, request.agent_slug, error=f"Active provider CLI unavailable: {cli}", fallback_from=fallback_from)
        try:
            profile = self._resolve_profile(request, provider_id)
            cmd, env = self._command(provider_id, cli, request, profile)
        except RuntimeError as exc:
            return RuntimeResult("failed", provider_id, request.requested_profile, None, request.agent_slug, error=str(exc), fallback_from=fallback_from)

        started = time.monotonic()
        process = None
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=WORKSPACE, env=env, start_new_session=True)
            stdout, stderr = process.communicate(timeout=request.timeout_seconds)
            duration_ms = int((time.monotonic() - started) * 1000)
            safe_stdout = redact_secrets(stdout)
            safe_stderr = redact_secrets(stderr, limit=2000)
            if process.returncode:
                return RuntimeResult("failed", provider_id, request.requested_profile, profile, request.agent_slug, safe_stdout, safe_stderr or f"exit code {process.returncode}", process.returncode, duration_ms, fallback_from=fallback_from)
            return RuntimeResult("succeeded", provider_id, request.requested_profile, profile, request.agent_slug, safe_stdout, exit_code=0, duration_ms=duration_ms, fallback_from=fallback_from)
        except subprocess.TimeoutExpired:
            if process is not None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    process.kill()
                process.communicate(timeout=5)
            return RuntimeResult("timeout", provider_id, request.requested_profile, profile, request.agent_slug, error=f"Killed after {request.timeout_seconds}s timeout", exit_code=-1, duration_ms=int((time.monotonic() - started) * 1000), fallback_from=fallback_from)
        except Exception as exc:
            return RuntimeResult("failed", provider_id, request.requested_profile, profile, request.agent_slug, error=redact_secrets(exc, limit=2000), duration_ms=int((time.monotonic() - started) * 1000), fallback_from=fallback_from)

    def invoke(self, request: RuntimeRequest) -> RuntimeResult:
        provider_id, provider, config = self._provider()
        result = self._invoke_provider(provider_id, provider, request)
        if result.status != "failed" or not config.get("allow_provider_fallback"):
            return result
        for candidate, candidate_config in config.get("providers", {}).items():
            if candidate != provider_id and shutil.which(candidate_config.get("cli_command", "")):
                return self._invoke_provider(candidate, candidate_config, request, fallback_from=provider_id)
        return result
