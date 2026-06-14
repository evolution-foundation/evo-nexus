#!/usr/bin/env python3
"""Telegram bot runtime backed by the active EvoNexus provider.

This intentionally bypasses Claude Code Channels for provider-backed chat:
non-Anthropic models can answer in the terminal without reliably calling the
Telegram `reply` MCP tool, which means users see no message in Telegram.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROVIDERS_PATH = ROOT / "config" / "providers.json"
TELEGRAM_STATE = Path.home() / ".claude" / "channels" / "telegram"
TELEGRAM_ENV = TELEGRAM_STATE / ".env"
ACCESS_FILE = TELEGRAM_STATE / "access.json"
DIRECT_STATE = TELEGRAM_STATE / "direct_state.json"
CHAT_MEMORY_DIR = TELEGRAM_STATE / "memory"
MAX_MEMORY_MESSAGES = 14
MAX_MEMORY_CHARS = 9000

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(NVIDIA_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|TELEGRAM_BOT_TOKEN)\b\s*[:=]\s*['\"]?[^'\"\s;]+"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"), "[REDACTED]"),
    (re.compile(r"\boxb-[A-Za-z0-9-]{16,}\b"), "[REDACTED]"),
)


def log(message: str) -> None:
    print(f"[telegram-provider] {message}", flush=True)


def read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def read_telegram_token() -> str:
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        return os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        for line in TELEGRAM_ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError(f"TELEGRAM_BOT_TOKEN missing in {TELEGRAM_ENV}")


def api(token: str, method: str, payload: dict | None = None, timeout: int = 35) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def allowed_chat(chat_id: str, from_id: str) -> bool:
    access = read_json(ACCESS_FILE, {"allowFrom": [], "groups": {}, "dmPolicy": "pairing"})
    if chat_id in {str(x) for x in access.get("allowFrom", [])}:
        return True
    if from_id in {str(x) for x in access.get("allowFrom", [])}:
        return True
    return chat_id in access.get("groups", {})


def provider_chain() -> list[tuple[str, dict]]:
    config = read_json(PROVIDERS_PATH, {"active_provider": "anthropic", "providers": {}})
    providers = config.get("providers", {})
    active_id = (
        os.environ.get("TELEGRAM_PROVIDER")
        or config.get("telegram_provider")
        or config.get("active_provider")
        or "anthropic"
    )
    active = providers.get(active_id, {})
    ids = [active_id]
    ids.extend(pid for pid in active.get("fallback_providers", []) if pid not in ids)
    return [(pid, providers.get(pid, {})) for pid in ids if providers.get(pid) is not None]


def active_provider_info() -> tuple[str, str | None, str | None]:
    config = read_json(PROVIDERS_PATH, {"active_provider": "anthropic", "providers": {}})
    provider_id = (
        os.environ.get("TELEGRAM_PROVIDER")
        or config.get("telegram_provider")
        or config.get("active_provider")
        or "anthropic"
    )
    provider = config.get("providers", {}).get(provider_id, {})
    env = provider.get("env_vars", {})
    model = env.get("OPENAI_MODEL") or env.get("GEMINI_MODEL") or provider.get("default_model")
    base_url = env.get("OPENAI_BASE_URL") or provider.get("default_base_url")
    return provider_id, model, base_url


def set_telegram_provider(provider_id: str | None) -> str:
    config = read_json(PROVIDERS_PATH, {"active_provider": "anthropic", "providers": {}})
    if provider_id in {"", "active", "global", "default", "none", None}:
        config.pop("telegram_provider", None)
        PROVIDERS_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        active = config.get("active_provider") or "anthropic"
        return f"Telegram agora segue o provider global: {active}"
    if provider_id not in config.get("providers", {}):
        available = ", ".join(sorted(config.get("providers", {}).keys()))
        return f"Provider invalido: {provider_id}. Disponiveis: {available}"
    config["telegram_provider"] = provider_id
    PROVIDERS_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    provider = config.get("providers", {}).get(provider_id, {})
    env = provider.get("env_vars", {})
    model = env.get("OPENAI_MODEL") or env.get("GEMINI_MODEL") or provider.get("default_model") or "default"
    return f"Telegram agora usa provider: {provider_id}\nmodel: {model}"


def chat_memory_path(chat_id: str) -> Path:
    safe_chat_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(chat_id))
    return CHAT_MEMORY_DIR / f"{safe_chat_id}.jsonl"


def load_chat_memory(chat_id: str) -> list[dict]:
    path = chat_memory_path(chat_id)
    if not path.exists():
        return []
    messages: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("role") in {"user", "assistant"} and entry.get("text"):
                messages.append(entry)
    except OSError:
        return []
    return messages


def append_chat_memory(chat_id: str, role: str, text: str, *, speaker: str | None = None) -> None:
    CHAT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = chat_memory_path(chat_id)
    entry = {
        "role": role,
        "text": redact_secrets(text),
        "ts": int(time.time()),
    }
    if speaker:
        entry["speaker"] = speaker
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def clear_chat_memory(chat_id: str) -> None:
    path = chat_memory_path(chat_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def format_chat_memory(messages: list[dict], *, current_speaker: str | None = None) -> str:
    lines: list[str] = []
    for entry in messages[-MAX_MEMORY_MESSAGES:]:
        role = entry.get("role", "?")
        text = redact_secrets(str(entry.get("text", "")).strip())
        if not text:
            continue
        speaker = entry.get("speaker")
        if role == "user":
            label = f"Usuário{f' ({speaker})' if speaker else ''}"
        else:
            label = "Assistente"
        lines.append(f"{label}: {text}")
    if current_speaker:
        lines.append(f"Usuário ({current_speaker}):")
    return "\n".join(lines).strip()


def build_prompt(chat_id: str, prompt_text: str, *, speaker: str | None = None) -> str:
    memory = format_chat_memory(load_chat_memory(chat_id), current_speaker=speaker)
    clean_prompt = redact_secrets(prompt_text.strip())
    parts = [
        "Voce e o assistente Telegram do EvoNexus.",
        "Responda em portugues, de forma direta e util.",
        "Use a memoria recente abaixo quando ela for relevante.",
        "",
    ]
    if memory:
        parts.extend([
            "Memoria recente da conversa:",
            memory,
            "",
        ])
    parts.extend([
        "Mensagem atual:",
        clean_prompt,
    ])
    prompt = "\n".join(parts).strip()
    if len(prompt) > MAX_MEMORY_CHARS:
        return prompt[-MAX_MEMORY_CHARS:]
    return prompt


def is_provider_question(text: str) -> bool:
    lower = text.lower()
    provider_terms = ("provider", "modelo", "model", "llm", "api", "nvidia", "codex", "openai")
    question_terms = ("qual", "quem", "usando", "rodando", "usa", "ta com", "tá com")
    return any(term in lower for term in provider_terms) and any(term in lower for term in question_terms)


def parse_provider_command(text: str) -> str | None:
    if not text.startswith("/provider"):
        return None
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else "status"


def models_for(provider: dict) -> list[str | None]:
    env = provider.get("env_vars", {})
    primary = env.get("OPENAI_MODEL") or provider.get("default_model")
    models: list[str | None] = []
    if primary:
        models.append(primary)
    for model in provider.get("fallback_models", []):
        if model and model not in models:
            models.append(model)
    if not models:
        models.append(None)
    return models


def invoke_openai_compatible(provider_id: str, provider: dict, model: str, prompt: str) -> str:
    env = provider.get("env_vars", {})
    base_url = (env.get("OPENAI_BASE_URL") or provider.get("default_base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key = env.get("OPENAI_API_KEY") or env.get("NVIDIA_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(f"{provider_id} has no API key")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Voce e o assistente Telegram do EvoNexus. Responda em portugues, "
                    "de forma direta e util. Runtime atual: "
                    f"provider={provider_id}, model={model}, base_url={base_url}. "
                    "Se perguntarem qual LLM, provider ou modelo voce usa, responda exatamente com esses dados."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 900,
        "temperature": 0.4,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError(f"{provider_id}:{model} returned empty content")
    return content.strip()


def invoke_cli(provider_id: str, provider: dict, prompt: str) -> str:
    cli = provider.get("cli_command") or ("claude" if provider_id == "anthropic" else "openclaude")
    if provider_id == "anthropic":
        cli = "claude"
    elif provider_id == "codex_auth":
        cli = "openclaude"
    env = os.environ.copy()
    for key, value in provider.get("env_vars", {}).items():
        if value:
            env[key] = str(value)
    cmd = [cli, "--print", "--max-turns", "1", "--dangerously-skip-permissions", "--", prompt]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"{cli} failed").strip()[:500])
    return proc.stdout.strip()


def invoke_provider(prompt: str) -> tuple[str, str]:
    errors: list[str] = []
    for provider_id, provider in provider_chain():
        for model in models_for(provider):
            try:
                if provider_id in {"anthropic", "codex_auth"}:
                    text = invoke_cli(provider_id, provider, prompt)
                    return text, provider_id
                if model:
                    text = invoke_openai_compatible(provider_id, provider, model, prompt)
                    return text, f"{provider_id}:{model}"
            except Exception as exc:
                errors.append(f"{provider_id}:{model or 'default'}: {exc}")
                log(f"fallback after {provider_id}:{model or 'default'} failed: {exc}")
    raise RuntimeError("All providers failed: " + " | ".join(errors[-3:]))


def load_offset() -> int | None:
    state = read_json(DIRECT_STATE, {})
    offset = state.get("offset")
    return int(offset) if isinstance(offset, int) else None


def save_offset(offset: int) -> None:
    TELEGRAM_STATE.mkdir(parents=True, exist_ok=True)
    DIRECT_STATE.write_text(json.dumps({"offset": offset}, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    token = read_telegram_token()
    me = api(token, "getMe")
    username = me.get("result", {}).get("username", "unknown")
    log(f"polling @{username}; provider follows {PROVIDERS_PATH}")

    offset = load_offset()
    while True:
        try:
            payload = {"timeout": 25, "limit": 20}
            if offset is not None:
                payload["offset"] = offset
            updates = api(token, "getUpdates", payload, timeout=35).get("result", [])
            for update in updates:
                offset = int(update["update_id"]) + 1
                save_offset(offset)
                message = update.get("message") or update.get("edited_message") or {}
                text = (message.get("text") or "").strip()
                chat = message.get("chat") or {}
                sender = message.get("from") or {}
                chat_id = str(chat.get("id", ""))
                from_id = str(sender.get("id", ""))
                sender_name = (
                    sender.get("username")
                    or " ".join(part for part in [sender.get("first_name"), sender.get("last_name")] if part)
                    or None
                )
                if not text or not chat_id:
                    continue
                if not allowed_chat(chat_id, from_id):
                    log(f"dropped non-allowlisted chat={chat_id}")
                    continue
                if text.startswith("/start"):
                    api(token, "sendMessage", {"chat_id": chat_id, "text": "EvoNexus online. Pode mandar."})
                    continue
                if text.startswith("/new"):
                    clear_chat_memory(chat_id)
                    api(token, "sendMessage", {"chat_id": chat_id, "text": "Sessao nova iniciada. Memoria local limpa."})
                    continue
                provider_command = parse_provider_command(text)
                if provider_command is not None:
                    if provider_command == "status":
                        provider_id, model, base_url = active_provider_info()
                        answer = f"provider: {provider_id}\nmodel: {model or 'default'}"
                        if base_url:
                            answer += f"\nbase_url: {base_url}"
                    else:
                        answer = set_telegram_provider(provider_command)
                    api(token, "sendMessage", {"chat_id": chat_id, "text": answer})
                    log(f"provider-command chat={chat_id} {provider_command}")
                    continue
                if is_provider_question(text):
                    provider_id, model, base_url = active_provider_info()
                    answer = (
                        "Estou usando o provider ativo do EvoNexus:\n"
                        f"provider: {provider_id}\n"
                        f"model: {model or 'default'}"
                    )
                    if base_url:
                        answer += f"\nbase_url: {base_url}"
                    api(token, "sendMessage", {"chat_id": chat_id, "text": answer})
                    log(f"provider-info chat={chat_id} {provider_id}:{model or 'default'}")
                    continue
                api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
                try:
                    prompt = build_prompt(chat_id, text, speaker=sender_name)
                    answer, used = invoke_provider(prompt)
                except Exception as exc:
                    answer = f"Falhei ao consultar o provider: {exc}"
                    used = "error"
                log(f"reply chat={chat_id} via {used}")
                api(token, "sendMessage", {"chat_id": chat_id, "text": answer[:3900]})
                if used != "error":
                    append_chat_memory(chat_id, "user", text, speaker=sender_name)
                    append_chat_memory(chat_id, "assistant", answer, speaker="Magneto")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            log(f"telegram http error {exc.code}: {body[:300]}")
            time.sleep(5)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            log(f"loop error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
