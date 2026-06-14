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
import tempfile
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
INBOX_DIR = TELEGRAM_STATE / "inbox"
GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_TRANSCRIPTION_MODEL = os.environ.get("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
MAX_MEMORY_MESSAGES = 8
MAX_MEMORY_CHARS = 4500
TELEGRAM_CODEX_MODELS = ("codexplan",)
GROQ_AUDIO_SUFFIXES = {
    ".oga": ".ogg",
    ".opus": ".opus",
    ".ogg": ".ogg",
    ".mp3": ".mp3",
    ".m4a": ".m4a",
    ".mp4": ".mp4",
    ".mpeg": ".mpeg",
    ".mpga": ".mpga",
    ".wav": ".wav",
    ".webm": ".webm",
    ".flac": ".flac",
}

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(NVIDIA_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|TELEGRAM_BOT_TOKEN)\b\s*[:=]\s*['\"]?[^'\"\s;]+"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "[REDACTED]"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{16,}\b"), "[REDACTED]"),
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


def read_env_value(path: Path, key: str) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def read_groq_api_key() -> str:
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    for path in (ROOT / ".env", TELEGRAM_ENV):
        value = read_env_value(path, "GROQ_API_KEY")
        if value:
            return value
    config = read_json(PROVIDERS_PATH, {})
    for provider in config.get("providers", {}).values():
        env = provider.get("env_vars", {})
        value = env.get("GROQ_API_KEY")
        if value:
            return str(value)
    raise RuntimeError("GROQ_API_KEY nao configurada")


def write_env_value(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    replaced = False
    try:
        existing = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    for line in existing:
        if line.strip().startswith(f"{key}="):
            lines.append(f"{key}={value}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_groq_command(text: str) -> str | None:
    if not text.startswith("/groq"):
        return None
    parts = text.split(maxsplit=2)
    if len(parts) == 1:
        return "status"
    action = parts[1].strip().lower()
    if action in {"status", "set"}:
        return action if action == "status" else f"set {parts[2].strip() if len(parts) > 2 else ''}"
    return "help"


def handle_groq_command(command: str) -> str:
    if command == "status":
        try:
            read_groq_api_key()
            return f"Groq configurado. Modelo de transcricao: {GROQ_TRANSCRIPTION_MODEL}"
        except Exception as exc:
            return f"Groq nao configurado: {exc}\nUse: /groq set <GROQ_API_KEY>"
    if command.startswith("set "):
        value = command.split(" ", 1)[1].strip()
        if not value.startswith("gsk_"):
            return "Chave Groq invalida. Ela deve comecar com gsk_."
        write_env_value(TELEGRAM_ENV, "GROQ_API_KEY", value)
        return "Groq configurado para transcricao de audio."
    return "Comandos: /groq status | /groq set <GROQ_API_KEY>"


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


def download_telegram_file(token: str, file_id: str) -> tuple[Path, str]:
    data = api(token, "getFile", {"file_id": file_id}, timeout=20)
    file_path = data.get("result", {}).get("file_path")
    if not file_path:
        raise RuntimeError("Telegram nao retornou file_path")
    suffix = GROQ_AUDIO_SUFFIXES.get(Path(file_path).suffix.lower(), ".ogg")
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        audio_bytes = resp.read()
    tmp = tempfile.NamedTemporaryFile(prefix="telegram-audio-", suffix=suffix, delete=False)
    try:
        tmp.write(audio_bytes)
        return Path(tmp.name), suffix
    finally:
        tmp.close()


def save_telegram_file(token: str, file_id: str, *, suffix: str | None = None) -> Path:
    data = api(token, "getFile", {"file_id": file_id}, timeout=20)
    file_path = data.get("result", {}).get("file_path")
    if not file_path:
        raise RuntimeError("Telegram nao retornou file_path")
    file_suffix = suffix or Path(file_path).suffix or ".bin"
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        content = resp.read()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    target = INBOX_DIR / f"{int(time.time() * 1000)}-{Path(file_path).stem}{file_suffix}"
    target.write_bytes(content)
    return target


def multipart_form_data(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----evonexus-{int(time.time() * 1000)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    for name, (filename, content, content_type) in files.items():
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8"),
            content,
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def transcribe_audio(audio_path: Path) -> str:
    api_key = read_groq_api_key()
    upload_suffix = GROQ_AUDIO_SUFFIXES.get(audio_path.suffix.lower(), ".ogg")
    upload_path = audio_path
    temp_upload: Path | None = None
    if audio_path.suffix.lower() != upload_suffix:
        temp = tempfile.NamedTemporaryFile(prefix="groq-upload-", suffix=upload_suffix, delete=False)
        try:
            temp.write(audio_path.read_bytes())
            temp_upload = Path(temp.name)
            upload_path = temp_upload
        finally:
            temp.close()
    try:
        proc = subprocess.run(
            [
                "curl",
                "-sS",
                "-H",
                f"Authorization: Bearer {api_key}",
                "-F",
                f"file=@{upload_path}",
                "-F",
                f"model={GROQ_TRANSCRIPTION_MODEL}",
                "-F",
                "response_format=json",
                "-F",
                "language=pt",
                GROQ_TRANSCRIPTION_URL,
            ],
            text=True,
            capture_output=True,
            timeout=120,
        )
    finally:
        if temp_upload:
            try:
                temp_upload.unlink()
            except OSError:
                pass
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "curl failed").strip()[:240])
    data = json.loads(proc.stdout)
    if data.get("error"):
        raise RuntimeError(str(data["error"].get("message") or data["error"])[:240])
    text = str(data.get("text") or "").strip()
    if not text:
        raise RuntimeError("Groq nao retornou transcricao")
    return text


def message_audio_file_id(message: dict) -> str | None:
    voice = message.get("voice") or {}
    audio = message.get("audio") or {}
    document = message.get("document") or {}
    mime_type = str(document.get("mime_type") or "")
    if voice.get("file_id"):
        return str(voice["file_id"])
    if audio.get("file_id"):
        return str(audio["file_id"])
    if mime_type.startswith("audio/") and document.get("file_id"):
        return str(document["file_id"])
    return None


def message_image_file_id(message: dict) -> tuple[str, str] | None:
    photos = message.get("photo") or []
    if photos:
        largest = max(photos, key=lambda item: int(item.get("file_size") or item.get("width") or 0))
        if largest.get("file_id"):
            return str(largest["file_id"]), ".jpg"
    document = message.get("document") or {}
    mime_type = str(document.get("mime_type") or "")
    if mime_type.startswith("image/") and document.get("file_id"):
        suffix = Path(str(document.get("file_name") or "")).suffix or f".{mime_type.split('/', 1)[1]}"
        return str(document["file_id"]), suffix
    return None


def handle_audio_message(token: str, chat_id: str, file_id: str) -> str:
    audio_path: Path | None = None
    try:
        audio_path, _suffix = download_telegram_file(token, file_id)
        return transcribe_audio(audio_path)
    finally:
        if audio_path:
            try:
                audio_path.unlink()
            except OSError:
                pass


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
    override_id = os.environ.get("TELEGRAM_PROVIDER") or config.get("telegram_provider")
    active_id = (
        override_id
        or config.get("active_provider")
        or "anthropic"
    )
    active = providers.get(active_id, {})
    ids = [active_id]
    if not override_id:
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
        "Voce e o runtime Telegram do EvoNexus, operando dentro do workspace local.",
        "Responda em portugues, curto e objetivo.",
        "Nao diga que nao tem acesso a ferramentas de forma generica.",
        "Quando o usuario pedir uma acao, tente executar pelo workspace/integracoes disponiveis.",
        "Se houver bloqueio real, responda somente o bloqueio concreto: credencial, arquivo, permissao, endpoint ou erro.",
        "Use a memoria recente abaixo apenas quando for relevante; ignore respostas antigas que negaram acesso genericamente.",
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


def provider_models(provider_id: str, provider: dict) -> list[str | None]:
    if provider_id == "codex_auth":
        return [model for model in TELEGRAM_CODEX_MODELS if model]
    return models_for(provider)


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


def invoke_cli(provider_id: str, provider: dict, prompt: str, model: str | None = None) -> str:
    cli = provider.get("cli_command") or ("claude" if provider_id == "anthropic" else "openclaude")
    if provider_id == "anthropic":
        cli = "claude"
    elif provider_id == "codex_auth":
        cli = "openclaude"
    env = os.environ.copy()
    for key, value in provider.get("env_vars", {}).items():
        if value:
            env[key] = str(value)
    if model:
        env["OPENAI_MODEL"] = model
    max_turns = int(os.environ.get("TELEGRAM_CLI_MAX_TURNS") or provider.get("telegram_max_turns") or (5 if provider_id == "codex_auth" else 2))
    timeout = int(os.environ.get("TELEGRAM_CLI_TIMEOUT") or provider.get("telegram_timeout_seconds") or (240 if provider_id == "codex_auth" else 120))
    cmd = [cli, "--print", "--max-turns", str(max_turns), "--dangerously-skip-permissions", "--", prompt]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"{cli} failed").strip()[:500])
    return proc.stdout.strip()


def invoke_provider(prompt: str) -> tuple[str, str]:
    errors: list[str] = []
    for provider_id, provider in provider_chain():
        for model in provider_models(provider_id, provider):
            try:
                if provider_id in {"anthropic", "codex_auth"}:
                    text = invoke_cli(provider_id, provider, prompt, model)
                    return text, f"{provider_id}:{model or 'default'}"
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
                if not chat_id:
                    continue
                if not allowed_chat(chat_id, from_id):
                    log(f"dropped non-allowlisted chat={chat_id}")
                    continue
                audio_file_id = message_audio_file_id(message)
                if audio_file_id:
                    api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
                    try:
                        transcription = handle_audio_message(token, chat_id, audio_file_id)
                        api(
                            token,
                            "sendMessage",
                            {"chat_id": chat_id, "text": f"Transcricao:\n\n{transcription[:3600]}"},
                        )
                        prompt = build_prompt(chat_id, transcription, speaker=sender_name)
                        answer, used = invoke_provider(prompt)
                    except Exception as exc:
                        answer = f"Falhei ao transcrever/processar audio: {exc}"
                        used = "error"
                    log(f"audio chat={chat_id} via {used}")
                    api(token, "sendMessage", {"chat_id": chat_id, "text": answer[:3900]})
                    if used != "error":
                        append_chat_memory(chat_id, "user", f"[audio transcrito] {transcription}", speaker=sender_name)
                        append_chat_memory(chat_id, "assistant", answer, speaker="Magneto")
                    continue
                image_info = message_image_file_id(message)
                if image_info:
                    api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
                    try:
                        image_file_id, suffix = image_info
                        image_path = save_telegram_file(token, image_file_id, suffix=suffix)
                        caption = (message.get("caption") or "").strip()
                        prompt_text = (
                            "Analise a imagem recebida no Telegram.\n"
                            f"Caminho local da imagem: {image_path}\n"
                            f"Legenda/mensagem do usuario: {caption or '(sem legenda)'}\n"
                            "Se conseguir acessar o arquivo, descreva o que ve e responda ao pedido do usuario."
                        )
                        prompt = build_prompt(chat_id, prompt_text, speaker=sender_name)
                        answer, used = invoke_provider(prompt)
                    except Exception as exc:
                        answer = f"Falhei ao processar imagem: {exc}"
                        used = "error"
                    log(f"image chat={chat_id} via {used}")
                    api(token, "sendMessage", {"chat_id": chat_id, "text": answer[:3900]})
                    if used != "error":
                        append_chat_memory(chat_id, "user", f"[imagem] {caption or image_path}", speaker=sender_name)
                        append_chat_memory(chat_id, "assistant", answer, speaker="Magneto")
                    continue
                if not text:
                    continue
                if text.startswith("/start"):
                    api(token, "sendMessage", {"chat_id": chat_id, "text": "EvoNexus online. Pode mandar."})
                    continue
                if text.startswith("/new"):
                    clear_chat_memory(chat_id)
                    api(token, "sendMessage", {"chat_id": chat_id, "text": "Sessao nova iniciada. Memoria local limpa."})
                    continue
                groq_command = parse_groq_command(text)
                if groq_command is not None:
                    answer = handle_groq_command(groq_command)
                    api(token, "sendMessage", {"chat_id": chat_id, "text": answer})
                    log(f"groq-command chat={chat_id} {groq_command.split(' ', 1)[0]}")
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
