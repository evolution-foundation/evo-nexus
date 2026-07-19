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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard" / "backend"))
# Same agentic-CLI fallback engine the heartbeats already run in production
# (dashboard/backend/heartbeat_runner.py) — real tool-use (Bash, Agent/Task
# spawning) instead of a bare chat completion. _parse_opencode_ndjson is
# reused as-is to extract plain text from opencode's event stream.
from provider_fallback import invoke_with_fallback, _parse_opencode_ndjson  # noqa: E402

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
MAX_MEMORY_CHARS = 1800
MAX_STORED_MESSAGE_CHARS = 1600

# Orchestrator tuning — real agentic runs (Bash/Agent/Task spawning) need far
# more headroom than a plain chat completion; defaults mirror heartbeat ranges
# (10-50 turns, several hundred seconds) instead of the old 2 turns/120s.
TELEGRAM_MAX_TURNS = int(os.environ.get("TELEGRAM_MAX_TURNS", "25"))
TELEGRAM_TIMEOUT = int(os.environ.get("TELEGRAM_TIMEOUT", "600"))
TELEGRAM_MAX_CONCURRENT = int(os.environ.get("TELEGRAM_MAX_CONCURRENT", "4"))


def _load_workspace_env() -> None:
    """Load root .env without depending on python-dotenv."""
    env_file = ROOT / ".env"
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


def _usable_secret(value: str | None) -> bool:
    if not value:
        return False
    value = value.strip()
    return value not in {"[REDACTED]", "your_bot_token_here", "your_chat_id_here", "REDACTED"}


_load_workspace_env()
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


def approval_approvers() -> set[str]:
    """Individuals allowed to press an approval button (Vault V3).

    Deliberately per-INDIVIDUAL, not per-chat like allowed_chat() — a Telegram
    group's `allowed_chat` entry would let any member of that group approve a
    publish/decomposition gate, which is a materially different (weaker)
    guarantee than "Felipe personally pressed the button". access.json
    doesn't have a dedicated "approvers" key yet; it reuses `allowFrom`,
    which today already only lists individual Telegram user ids (not chat/
    group ids) for DM pairing — see the seeding in telegram_swarm_entry.sh.
    An explicit "approvers" list, if ever added to access.json, takes
    precedence. APPROVAL_APPROVER_IDS (env, documented in .env.example and
    passed via evonexus-vps.stack.yml) is merged in too — union, not
    override, so operators setting the env don't silently lose whoever is
    already paired via access.json.allowFrom.
    """
    access = read_json(ACCESS_FILE, {"allowFrom": [], "approvers": [], "groups": {}})
    explicit = access.get("approvers")
    approvers = {str(x) for x in explicit} if explicit else {str(x) for x in access.get("allowFrom", [])}
    env_ids = os.environ.get("APPROVAL_APPROVER_IDS", "")
    for part in re.split(r"[,;]", env_ids):
        part = part.strip()
        if part:
            approvers.add(part)
    return approvers


def decide_approval_via_api(approval_id: int, decision: str, from_id: str) -> dict:
    """Call POST /api/approvals/{id}/decision with the dedicated bridge token.

    Molde de unblock_ticket (mesmo motivo: o serviço telegram não monta o
    volume do DB, então a decisão vai pela API REST do dashboard). Usa
    APPROVAL_BRIDGE_TOKEN — NUNCA DASHBOARD_API_TOKEN — e manda `from_id` no
    corpo, nunca `decided_by`: quem deriva decided_by é o servidor, depois de
    revalidar from_id contra a allowlist (Vault V4 — um decided_by vindo do
    corpo seria forjável por qualquer um com o bridge token).
    """
    base_url = os.environ.get("EVONEXUS_API_URL", "").strip().rstrip("/")
    token = os.environ.get("APPROVAL_BRIDGE_TOKEN", "").strip()
    if not base_url or not token:
        return {"ok": False, "toast": "Bridge de aprovação não configurado neste serviço."}

    payload = json.dumps({"decision": decision, "from_id": from_id}).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(
        f"{base_url}/api/approvals/{approval_id}/decision", data=payload, headers=headers, method="POST",
    )
    try:
        # Publish approval can synchronously wait for Postiz to confirm
        # state=PUBLISHED (dashboard default: up to 90s). Keep the bot's HTTP
        # timeout slightly above that window instead of failing at 15s while
        # the server continues publishing in the background.
        timeout = float(os.environ.get("APPROVAL_DECISION_TIMEOUT_SECONDS", "105"))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        label = "aprovada ✅" if decision == "approve" else "rejeitada ❌"
        return {"ok": True, "toast": f"Decisão registrada: {label}", "body": body}
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return {"ok": False, "toast": "Já decidido antes — nada mudou."}
        body = exc.read().decode("utf-8", "ignore")
        return {"ok": False, "toast": f"Erro ao decidir (HTTP {exc.code})", "detail": body[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "toast": f"Erro ao decidir: {exc}"}


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
        "text": redact_secrets(text)[:MAX_STORED_MESSAGE_CHARS],
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


def env_presence(keys: tuple[str, ...]) -> str:
    values: list[str] = []
    for key in keys:
        value = os.environ.get(key, "")
        values.append(f"{key}={'present' if value else 'missing'}")
    return ", ".join(values)


def skill_context(name: str) -> str:
    skill_md = ROOT / ".claude" / "skills" / name / "SKILL.md"
    if not skill_md.is_file():
        return f"Skill {name}: nao encontrada."
    text = skill_md.read_text(encoding="utf-8")
    excerpt = "\n".join(line for line in text.splitlines() if line.startswith(("name:", "description:", "envKeys:", "# Ghost Blog Integration", "## Configuração", "## Auth", "- **Admin API**", "- **Content API**", "### Admin API"))).strip()
    return f"Skill {name} disponível no workspace:\n{excerpt}"


def workspace_context() -> str:
    nexus_api_url = os.environ.get("EVONEXUS_API_URL", "")
    has_api = bool(nexus_api_url and os.environ.get("DASHBOARD_API_TOKEN", "").strip())
    nexus_line = (
        f"Nexus REST API: {nexus_api_url}" if nexus_api_url
        else "Nexus REST API: (environ EVONEXUS_API_URL nao definida)"
    )
    if has_api:
        nexus_line += (
            " — disponivel com DASHBOARD_API_TOKEN. "
            "Endpoints: GET/POST /api/goals, /api/missions, /api/projects, "
            "/api/tickets, /api/mempalace/search?q=&n="
        )
    else:
        nexus_line += " — sem token, modo leitura URL apenas."
    return "\n".join([
        skill_context("custom-int-ghost"),
        f"Env Ghost: {env_presence(('GHOST_URL', 'GHOST_CONTENT_API_KEY', 'GHOST_ADMIN_API_KEY'))}.",
        "Ghost Admin API usa JWT HS256 gerado de GHOST_ADMIN_API_KEY no formato id:secret; header Authorization: Ghost ***",
        nexus_line,
    ])


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
    memory = "\n".join(lines).strip()
    if len(memory) <= MAX_MEMORY_CHARS:
        return memory
    return memory[-MAX_MEMORY_CHARS:]


_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


def fetch_url_context(text: str, max_urls: int = 3, max_chars: int = 6000) -> str:
    """Fetch the content of URLs mentioned in the message so the model can 'read'
    them. The bot talks to NVIDIA via plain chat completions (no browser tool), so
    without this it always answers 'não consigo navegar'. We fetch server-side and
    inject the text."""
    urls = []
    for u in _URL_RE.findall(text or ""):
        u = u.rstrip(".,;")
        if u not in urls:
            urls.append(u)
        if len(urls) >= max_urls:
            break
    if not urls:
        return ""
    blocks = []
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (EvoNexus)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                ctype = resp.headers.get("Content-Type", "")
                raw = resp.read(300000).decode("utf-8", "replace")
            if "html" in ctype.lower() or raw.lstrip()[:1] == "<":
                raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.DOTALL | re.I)
                raw = re.sub(r"<[^>]+>", " ", raw)
                raw = re.sub(r"\s+", " ", raw)
            blocks.append(f"[Conteudo de {u}]\n{raw.strip()[:max_chars]}")
        except Exception as e:  # noqa: BLE001
            blocks.append(f"[Falha ao acessar {u}: {e}]")
    return "\n\n".join(blocks)


def fetch_mempalace_context(text: str, max_results: int = 3) -> str:
    """Search MemPalace for context relevant to the user's question.

    Uses EVONEXUS_API_URL + DASHBOARD_API_TOKEN from env (set inside the Docker
    container on the VPS). Falls back silently if unreachable/unconfigured.
    """
    if not text or len(text.strip()) < 5:
        return ""
    base_url = os.environ.get("EVONEXUS_API_URL", "").strip().rstrip("/")
    token = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not base_url or not token:
        return ""
    params = urllib.parse.urlencode({"q": text.strip()[:200], "n": max_results})
    url = f"{base_url}/api/mempalace/search?{params}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return ""
    results = data.get("results") or data.get("data", {}).get("results") or []
    if not results:
        return ""
    blocks = []
    for r in results[:max_results]:
        sim = r.get("similarity", r.get("score", 0))
        source = r.get("source_file", r.get("source", "?"))
        content = r.get("content", r.get("text", "")).strip()[:600]
        if content:
            blocks.append(f"[MemPalace {sim:.2f} — {source}]\n{content}")
    ctx = "\n\n".join(blocks)
    return ctx[:2500]


_NEXUS_STATUS_TERMS = (
    "heartbeat", "heart beat", "cron", "rotina", "routine",
    "scheduler", "agendad", "agendamento", "adw",
)


def _nexus_api_get(path: str, timeout: int = 10) -> dict | None:
    """GET on the Nexus REST API using env credentials. None if unavailable."""
    base_url = os.environ.get("EVONEXUS_API_URL", "").strip().rstrip("/")
    token = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not base_url or not token:
        return None
    req = urllib.request.Request(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def fetch_nexus_status_context(text: str) -> str:
    """Fetch real cron/heartbeat status server-side and inject it in the prompt.

    The model behind this bot is a plain chat completion — it cannot execute
    HTTP calls by itself. Whenever the user asks about crons/heartbeats we do
    the API calls here and hand the model the data, same pattern as
    fetch_url_context/fetch_mempalace_context.
    """
    lower = (text or "").lower()
    if not any(term in lower for term in _NEXUS_STATUS_TERMS):
        return ""
    blocks: list[str] = []

    hb_data = _nexus_api_get("/api/heartbeats")
    if hb_data:
        lines = []
        for hb in hb_data.get("heartbeats", []):
            last = hb.get("last_run") or {}
            status = last.get("status", "nunca rodou")
            when = last.get("started_at", "")
            err = (last.get("error") or "").strip().replace("\n", " ")[:180]
            line = (
                f"- {hb.get('id')} (agente {hb.get('agent')}, "
                f"{'ativo' if hb.get('enabled') else 'desativado'}): "
                f"última run {status}{f' em {when}' if when else ''}"
            )
            if err:
                line += f" — erro: {err}"
            lines.append(line)
        if lines:
            blocks.append("Heartbeats:\n" + "\n".join(lines))

    rt_data = _nexus_api_get("/api/routines")
    if rt_data:
        metrics = rt_data.get("metrics", {})
        ran = [
            (rid, m) for rid, m in metrics.items()
            if isinstance(m, dict) and m.get("last_run")
        ]
        ran.sort(key=lambda kv: kv[1].get("last_run") or "", reverse=True)
        lines = [
            f"- {rid}: última run {m.get('last_run')}, "
            f"{m.get('runs', 0)} runs, {m.get('success_rate', 0)}% sucesso"
            for rid, m in ran[:12]
        ]
        if lines:
            blocks.append("Rotinas (cron) com execução registrada:\n" + "\n".join(lines))

    if not blocks:
        return ""
    return "\n\n".join(blocks)[:3000]


def build_prompt(chat_id: str, prompt_text: str, *, speaker: str | None = None) -> str:
    memory = format_chat_memory(load_chat_memory(chat_id), current_speaker=speaker)
    clean_prompt = redact_secrets(prompt_text.strip())
    parts = [
        "Voce e o runtime Telegram do EvoNexus, operando dentro do workspace local.",
        "Responda em portugues, curto e objetivo.",
        "Nao diga que nao tem acesso a ferramentas de forma generica.",
        "Quando a mensagem contiver URLs, o conteudo delas ja foi buscado e esta abaixo em 'Conteudo das URLs' — USE esse conteudo; nunca diga que nao consegue navegar.",
        "Quando o usuario pedir uma acao, tente executar pelo workspace/integracoes disponiveis.",
        "O runtime deste bot consulta a API REST do Nexus por voce: quando a pergunta envolve cron/rotinas/heartbeats, os dados reais ja vem injetados abaixo em 'Status atual do Nexus'. "
        "Responda com base nesses dados. Se um bloco de status nao veio, diga que a API nao respondeu (nao diga que 'falta endpoint' ou que 'nao tem acesso').",
        "Se houver bloqueio real, responda somente o bloqueio concreto: credencial, arquivo, permissao, endpoint ou erro.",
        "Se a mensagem veio de audio transcrito, use a transcricao apenas como entrada interna; nao repita a transcricao ao usuario.",
        "Use a memoria recente abaixo apenas quando for relevante; ignore respostas antigas que negaram acesso genericamente.",
        "Contexto de integracoes locais:",
        workspace_context(),
        "",
    ]
    url_ctx = fetch_url_context(clean_prompt)
    if url_ctx:
        parts.extend(["Conteudo das URLs mencionadas:", url_ctx, ""])
    status_ctx = fetch_nexus_status_context(clean_prompt)
    if status_ctx:
        parts.extend([
            "Status atual do Nexus (dados REAIS buscados agora na API — use-os para responder; nao diga que falta endpoint):",
            status_ctx,
            "",
        ])
    if memory:
        parts.extend([
            "Memoria recente da conversa:",
            memory,
            "",
        ])
    mem_ctx = fetch_mempalace_context(clean_prompt)
    if mem_ctx:
        parts.extend([
            "Contexto do MemPalace (memoria persistente do workspace):",
            mem_ctx,
            "",
        ])
    parts.extend([
        "Mensagem atual:",
        clean_prompt,
    ])
    return "\n".join(parts).strip()


def is_provider_question(text: str) -> bool:
    # Only a SHORT message can be a "which model are you using?" question. Pasted
    # docs (llms.txt, etc.) mention "api/model/nvidia/usar" and were wrongly caught
    # here, making the bot reply the provider status instead of answering.
    if len(text.strip()) > 80:
        return False
    lower = text.lower()
    provider_terms = ("provider", "modelo", "model", "llm", "nvidia", "codex", "openai")
    question_terms = ("qual", "quem", "usando", "rodando", "ta com", "tá com")
    return any(term in lower for term in provider_terms) and any(term in lower for term in question_terms)


def parse_provider_command(text: str) -> str | None:
    if not text.startswith("/provider"):
        return None
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else "status"


_chat_run_locks: dict[str, threading.Lock] = {}
_chat_run_locks_guard = threading.Lock()


def _chat_lock(chat_id: str) -> threading.Lock:
    with _chat_run_locks_guard:
        lk = _chat_run_locks.get(chat_id)
        if lk is None:
            lk = threading.Lock()
            _chat_run_locks[chat_id] = lk
        return lk


_executor = ThreadPoolExecutor(max_workers=TELEGRAM_MAX_CONCURRENT, thread_name_prefix="telegram-orch")


def _extract_reply_text(result: dict) -> str:
    """Pull the assistant's final text out of a provider_fallback result.

    result["output"] is the raw subprocess stdout — either a Claude/OpenClaude
    JSON envelope ({"type":"result","result":"..."}) or opencode's ndjson
    event stream, depending on which CLI answered. Falls back to the raw
    output if neither shape parses, rather than raising.
    """
    output = (result.get("output") or "").strip()
    try:
        envelope = json.loads(output)
        if isinstance(envelope, dict) and envelope.get("result"):
            return str(envelope["result"]).strip()
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        parsed = _parse_opencode_ndjson(output)
        if parsed.get("text"):
            return str(parsed["text"]).strip()
    except Exception:
        pass
    return output


def invoke_orchestrator(prompt: str) -> tuple[str, str]:
    """Run the message through the same agentic-CLI fallback engine the
    heartbeats use — real tool-use (Bash, Agent/Task spawning of any
    .claude/agents/*.md specialist), routed through the active provider
    chain in config/providers.json (opencode by default).
    """
    result = invoke_with_fallback(
        prompt=prompt,
        agent="",  # no fixed persona — the model self-dispatches to any of
                   # the 38 specialist agents via the Agent/Task tool, same
                   # as a normal interactive Claude Code session would.
        max_turns=TELEGRAM_MAX_TURNS,
        timeout_seconds=TELEGRAM_TIMEOUT,
    )
    if result.get("status") == "busy":
        raise RuntimeError(
            "sistema ocupado com outra execução (heartbeat ou outro chat) — tenta de novo em instantes"
        )
    if result.get("status") != "success":
        raise RuntimeError(result.get("error") or f"status={result.get('status')}")
    text = _extract_reply_text(result)
    if not text:
        raise RuntimeError("orchestrator returned empty response")
    used = f"{result.get('provider_id') or '?'}:{result.get('model') or 'default'}"
    return text, used


def _typing_loop(token: str, chat_id: str, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
        except Exception:
            pass
        stop_event.wait(4)


def run_orchestrated_reply(
    token: str,
    chat_id: str,
    prompt: str,
    *,
    memory_user_text: str,
    speaker: str | None,
) -> None:
    """Background task: ack, keep 'typing' alive, run the orchestrator, reply.

    Serialized per chat_id (a lock, not the executor) so one chat's messages
    never interleave while other chats keep running concurrently — this is
    what lets the poll loop submit long agentic runs without stalling.
    """
    with _chat_lock(chat_id):
        try:
            api(token, "sendMessage", {"chat_id": chat_id, "text": "Recebido, trabalhando nisso..."})
        except Exception:
            pass
        stop_typing = threading.Event()
        typing_thread = threading.Thread(target=_typing_loop, args=(token, chat_id, stop_typing), daemon=True)
        typing_thread.start()
        try:
            answer, used = invoke_orchestrator(prompt)
        except Exception as exc:
            answer = f"Falhei ao orquestrar: {exc}"
            used = "error"
        finally:
            stop_typing.set()
            typing_thread.join(timeout=2)
        log(f"orchestrated-reply chat={chat_id} via {used}")
        api(token, "sendMessage", {"chat_id": chat_id, "text": answer[:3900]})
        if used != "error":
            append_chat_memory(chat_id, "user", memory_user_text, speaker=speaker)
            append_chat_memory(chat_id, "assistant", answer, speaker="Magneto")


def load_offset() -> int | None:
    state = read_json(DIRECT_STATE, {})
    offset = state.get("offset")
    return int(offset) if isinstance(offset, int) else None


def save_offset(offset: int) -> None:
    TELEGRAM_STATE.mkdir(parents=True, exist_ok=True)
    DIRECT_STATE.write_text(json.dumps({"offset": offset}, indent=2) + "\n", encoding="utf-8")


def unblock_ticket(ticket_id: str, reply_text: str, author: str) -> str:
    """Ponte Telegram→ticket: anexa a resposta do humano como comentário e reabre
    o ticket (blocked→open) para o orquestrador retomar. Cada round da entrevista
    é um ciclo: agente pergunta (blocked) → humano responde (reply) → reabre.

    Vai pela API REST do dashboard (EVONEXUS_API_URL + DASHBOARD_API_TOKEN), não
    por sqlite direto: o serviço telegram não monta o volume
    evonexus_dashboard_data (só o dashboard monta) — abrir
    dashboard/data/evonexus.db aqui sempre resultava em "Banco de tickets não
    encontrado". Confirmado ao vivo 2026-07-15/16.
    """
    base_url = os.environ.get("EVONEXUS_API_URL", "").strip().rstrip("/")
    token = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not base_url or not token:
        return "Não consigo desbloquear — EVONEXUS_API_URL/DASHBOARD_API_TOKEN não configurados neste serviço."

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        req = urllib.request.Request(f"{base_url}/api/tickets/{ticket_id}", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            ticket = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return f"Ticket {ticket_id[:8]} não encontrado."
        return f"Erro ao buscar ticket: HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"Erro ao buscar ticket: {exc}"

    tid = ticket["id"]
    try:
        comment_payload = json.dumps({"body": reply_text, "author": f"human:{author}"}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/tickets/{tid}/comments", data=comment_payload, headers=headers, method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()

        status_payload = json.dumps({"status": "open"}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/tickets/{tid}", data=status_payload, headers=headers, method="PATCH",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:  # noqa: BLE001
        return f"Erro ao desbloquear: {exc}"

    return (f"✅ Desbloqueado: {ticket['title'][:60]}\n"
            f"Sua resposta foi anexada — {ticket.get('assignee_agent')} retoma na próxima rodada.")


def main() -> int:
    token = read_telegram_token()
    me = api(token, "getMe")
    username = me.get("result", {}).get("username", "unknown")
    log(f"polling @{username}; provider follows {PROVIDERS_PATH}")

    offset = load_offset()
    while True:
        try:
            payload = {
                "timeout": 25, "limit": 20,
                "allowed_updates": ["message", "edited_message", "callback_query"],
            }
            if offset is not None:
                payload["offset"] = offset
            updates = api(token, "getUpdates", payload, timeout=35).get("result", [])
            for update in updates:
                offset = int(update["update_id"]) + 1
                save_offset(offset)

                cq = update.get("callback_query")
                if cq:
                    data = cq.get("data") or ""
                    from_id = str((cq.get("from") or {}).get("id", ""))
                    cq_message = cq.get("message") or {}
                    cq_chat_id = str((cq_message.get("chat") or {}).get("id", ""))
                    m = re.match(r"^apr:(\d+):([ar])$", data)
                    decision_registered = False
                    if m and from_id in approval_approvers():
                        decision = "approve" if m.group(2) == "a" else "reject"
                        resp = decide_approval_via_api(int(m.group(1)), decision, from_id)
                        api(token, "answerCallbackQuery", {"callback_query_id": cq["id"], "text": resp["toast"]})
                        log(f"approval-decision chat={cq_chat_id} approval={m.group(1)} decision={decision} ok={resp['ok']}")
                        decision_registered = resp.get("ok") is True
                    else:
                        api(token, "answerCallbackQuery", {"callback_query_id": cq["id"], "text": "não autorizado"})
                        log(f"approval-decision dropped from_id={from_id} data={data!r}")
                    # Only remove the buttons once the decision is actually
                    # registered — an unauthorized press or a transient API
                    # failure (5xx/timeout) must leave the keyboard intact so
                    # the legitimate approver can still press it.
                    if decision_registered and cq_chat_id and cq_message.get("message_id"):
                        api(token, "editMessageReplyMarkup", {
                            "chat_id": cq_chat_id, "message_id": cq_message["message_id"],
                        })
                    continue

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
                # Ponte de tickets: reply a uma notificação de bloqueio (#tkt:<id>)
                # anexa a resposta e reabre o ticket para o orquestrador retomar.
                reply_src = (message.get("reply_to_message") or {}).get("text") or ""
                m_tkt = re.search(r"#tkt:([0-9a-fA-F-]+)", reply_src)
                if m_tkt and text:
                    result = unblock_ticket(m_tkt.group(1), text, sender_name or "humano")
                    api(token, "sendMessage", {"chat_id": chat_id, "text": result})
                    log(f"ticket-unblock chat={chat_id} ticket={m_tkt.group(1)[:8]}")
                    continue
                audio_file_id = message_audio_file_id(message)
                if audio_file_id:
                    api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
                    try:
                        transcription = handle_audio_message(token, chat_id, audio_file_id)
                    except Exception as exc:
                        api(token, "sendMessage", {"chat_id": chat_id, "text": f"Falhei ao transcrever audio: {exc}"})
                        log(f"audio-transcribe-fail chat={chat_id}: {exc}")
                        continue
                    prompt = build_prompt(chat_id, transcription, speaker=sender_name)
                    _executor.submit(
                        run_orchestrated_reply, token, chat_id, prompt,
                        memory_user_text=f"[audio transcrito] {transcription}", speaker=sender_name,
                    )
                    continue
                image_info = message_image_file_id(message)
                if image_info:
                    api(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
                    try:
                        image_file_id, suffix = image_info
                        image_path = save_telegram_file(token, image_file_id, suffix=suffix)
                    except Exception as exc:
                        api(token, "sendMessage", {"chat_id": chat_id, "text": f"Falhei ao processar imagem: {exc}"})
                        log(f"image-download-fail chat={chat_id}: {exc}")
                        continue
                    caption = (message.get("caption") or "").strip()
                    prompt_text = (
                        "Analise a imagem recebida no Telegram.\n"
                        f"Caminho local da imagem: {image_path}\n"
                        f"Legenda/mensagem do usuario: {caption or '(sem legenda)'}\n"
                        "Se conseguir acessar o arquivo, descreva o que ve e responda ao pedido do usuario."
                    )
                    prompt = build_prompt(chat_id, prompt_text, speaker=sender_name)
                    _executor.submit(
                        run_orchestrated_reply, token, chat_id, prompt,
                        memory_user_text=f"[imagem] {caption or image_path}", speaker=sender_name,
                    )
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
                prompt = build_prompt(chat_id, text, speaker=sender_name)
                _executor.submit(
                    run_orchestrated_reply, token, chat_id, prompt,
                    memory_user_text=text, speaker=sender_name,
                )
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
