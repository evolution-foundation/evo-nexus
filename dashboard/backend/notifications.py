#!/usr/bin/env python3
"""
notifications.py — Canal único de feedback pro Telegram.

Centraliza TODAS as notificações positivas e negativas do EvoNexus.
Qualquer módulo pode chamar:
    from notifications import notify_success, notify_failure, notify_info

Estratégia de debounce por categoria:
- Heartbeat success: 30s por heartbeat_id (evita spam de 15min interval)
- Task completion: 60s por task_id
- Rotina: 120s por rotina
- Aprovação pendente: sem debounce (sempre notifica)
- Relatório horário: sem debounce
"""
from __future__ import annotations

import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent

# ── Load .env so TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are available ──
_env_file = WORKSPACE / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Debounce state ──────────────────────────────────────────────────────────
# In-memory only — resets on restart. Good enough for debounce within a session.
_debounce_state: dict[str, float] = {}
DEFAULT_DEBOUNCE = {
    "heartbeat_success": 30,
    "heartbeat_failure": 900,  # 15min por heartbeat — evita spam em falhas recorrentes
    "task_success": 60,
    "task_failure": 0,
    "routine_success": 120,
    "routine_failure": 0,
    "approval_pending": 0,       # always notify
    "hourly_report": 0,         # always notify
    "agent_event": 60,
}


def _should_send(category: str, key: str) -> bool:
    """Check debounce. Returns True if should send."""
    debounce_secs = DEFAULT_DEBOUNCE.get(category, 0)
    if debounce_secs == 0:
        return True
    now = time.time()
    full_key = f"{category}:{key}"
    last = _debounce_state.get(full_key, 0)
    if now - last >= debounce_secs:
        _debounce_state[full_key] = now
        return True
    return False


def _append_bot_memory(chat_id: str, text: str) -> None:
    """Mirror a system-pushed notification into the Telegram bot's own chat memory.

    scripts/telegram_provider_bot.py runs as a SEPARATE service/container and
    builds conversation context purely from what flowed through its own reply
    loop (append_chat_memory calls in run_orchestrated_reply). Messages this
    module sends via _send_telegram — heartbeat alerts, outcome notifications,
    approval pings — go straight to the Telegram HTTP API from the dashboard
    container and never touch that memory file, so the bot has no idea it (or
    the system) already told the user something. Reported live 2026-07-15:
    user asked the bot about content from a notification it had just received
    and the bot had no memory of it ever being sent.
    /root/.claude is the evonexus_claude_auth volume, mounted at the same
    path in dashboard, telegram AND scheduler — writing the exact JSONL
    format telegram_provider_bot.py itself uses closes the gap without any
    network call between the two services. Best-effort: a failure here must
    never break the actual notification send.
    """
    try:
        scripts_dir = str(WORKSPACE / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import telegram_provider_bot as _tpb
        _tpb.append_chat_memory(chat_id, "assistant", text, speaker="Sistema")
    except Exception:
        pass


def _send_telegram(text: str) -> bool:
    """Send Telegram message. Returns True on success."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cid = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not cid:
        return False
    try:
        payload = urllib.parse.urlencode({
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
    except Exception:
        return False
    if ok:
        _append_bot_memory(cid, text)
    return ok


def send_telegram_alert(text: str) -> bool:
    """Send a preformatted Telegram alert.

    Used by heartbeat_runner and other modules that already build the final
    HTML message. Returns False only when Telegram credentials are missing or
    Telegram rejects/fails the request.
    """
    return _send_telegram(text)


def send_approval_request(approval_id: int, title: str, body: str) -> int | None:
    """Send a Telegram approval prompt with an inline approve/reject keyboard.

    Shared by both goal-ticket-unification gates (publish + decomposition,
    ADR §3c) — the two gates differ only in what they park, not in how they
    notify. `title`/`body` are agent-authored content (an agent could write
    arbitrary text into a ticket/goal payload), so both are HTML-escaped
    before going into a parse_mode=HTML message (Vault V8) — otherwise a
    stray `<`/`&` in an agent's summary would either break the Telegram
    render or, worse, let injected markup control the approval prompt itself.
    Returns the sent message's message_id (for future reference), or None if
    Telegram credentials are missing or the send fails.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cid = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not cid:
        return None
    safe_title = html.escape(title or "")
    safe_body = html.escape(body or "")
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Aprovar", "callback_data": f"apr:{approval_id}:a"},
            {"text": "❌ Rejeitar", "callback_data": f"apr:{approval_id}:r"},
        ]]
    }
    text = f"🔔 <b>{safe_title}</b>\n\n{safe_body}"
    try:
        payload = json.dumps({
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": reply_markup,
        }).encode()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not result.get("ok"):
        return None
    _append_bot_memory(cid, text)
    message_id = (result.get("result") or {}).get("message_id")
    return int(message_id) if message_id is not None else None


def send_whatsapp(text: str, phone: str) -> bool:
    """Send a WhatsApp message via Evolution Go API.

    Reads EVOLUTION_GO_URL and EVOLUTION_GO_KEY from environment (.env).
    Returns True if sent, False on any failure.
    """
    import json
    import urllib.request
    import urllib.error

    url = os.environ.get("EVOLUTION_GO_URL", "").rstrip("/")
    key = os.environ.get("EVOLUTION_GO_KEY", "")
    if not url or not key:
        return False
    try:
        payload = json.dumps({"number": phone, "text": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/message/sendText/nature",
            data=payload,
            method="POST",
            headers={"apikey": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201)
    except Exception:
        return False


def _esc(s: str) -> str:
    """Escape HTML special chars for Telegram parse_mode=HTML."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notify_agent_result(agent: str, ticket_title: str, new_status: str,
                        summary: str) -> bool:
    """Outcome-driven: an agent advanced/finished a task. Report the RESULT.

    This is the signal Felipe wants — what got done, not that a heartbeat ran.
    """
    status_emoji = "✅" if new_status in ("resolved", "closed") else "🟢"
    status_label = {
        "resolved": "concluída", "closed": "fechada", "review": "em revisão",
        "in_progress": "em andamento", "blocked": "bloqueada",
    }.get(new_status, new_status)
    title = f" · <b>{_esc(ticket_title)}</b>" if ticket_title else ""
    text = (
        f"{status_emoji} <b>{_esc(agent)}</b>{title}\n"
        f"➡️ {status_label}\n\n"
        f"{_esc(summary[:600])}"
    )
    return _send_telegram(text)


def notify_agent_blocked(agent: str, ticket_title: str, reason: str,
                         needs: str = "", ticket_id: str = "") -> bool:
    """Outcome-driven: an agent is blocked and needs Felipe to intervene.

    The ticket id is embedded as a marker so a Telegram REPLY to this message is
    routed back to the ticket (telegram_provider_bot unblocks it with the reply).
    """
    title = f" · <b>{_esc(ticket_title)}</b>" if ticket_title else ""
    needs_line = f"\n\n🙋 <b>Preciso de você:</b> {_esc(needs[:300])}" if needs else ""
    hint = "\n\n<i>Responda (reply) esta mensagem para desbloquear.</i>" if ticket_id else ""
    marker = f"\n#tkt:{ticket_id}" if ticket_id else ""
    text = (
        f"🔴 <b>{_esc(agent)}</b> bloqueado{title}\n\n"
        f"{_esc(reason[:400])}"
        f"{needs_line}"
        f"{hint}"
        f"{marker}"
    )
    return _send_telegram(text)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


# ── Public API ──────────────────────────────────────────────────────────────

def notify_heartbeat_success(heartbeat_id: str, agent: str, duration_ms: int,
                            model: str = "", cost_usd: float = 0,
                            tokens_in: int = 0, tokens_out: int = 0) -> bool:
    """Report heartbeat success to Telegram."""
    if not _should_send("heartbeat_success", heartbeat_id):
        return False

    dur = f"{duration_ms / 1000:.1f}s" if duration_ms else "?"
    cost = f" | 💰 ${cost_usd:.4f}" if cost_usd and cost_usd > 0 else ""
    model_str = f" | 🔗 {model}" if model else ""
    tok = ""
    if tokens_in and tokens_out:
        tok = f"\n📊 {tokens_in:,} in / {tokens_out:,} out"

    text = (
        f"✅ <b>Heartbeat OK</b>\n\n"
        f"🔧 <b>{heartbeat_id}</b> | 🤖 {agent}\n"
        f"⏱ {dur}{cost}{model_str}{tok}"
    )
    return _send_telegram(text)


def notify_heartbeat_failure(heartbeat_id: str, agent: str, error: str,
                             duration_ms: int = 0, attempt: int = 0) -> bool:
    """Report heartbeat failure to Telegram."""
    if not _should_send("heartbeat_failure", heartbeat_id):
        return False

    dur = f"{duration_ms / 1000:.1f}s" if duration_ms else "?"
    att = f" | 🔄 attempt #{attempt}" if attempt > 1 else ""
    err = error[:200].replace("\n", " ") if error else "unknown"

    text = (
        f"⚠️ <b>Heartbeat FAIL</b>\n\n"
        f"🔧 <b>{heartbeat_id}</b> | 🤖 {agent}\n"
        f"⏱ {dur}{att}\n"
        f"📝 <code>{err}</code>"
    )
    return _send_telegram(text)


def notify_task_success(task_name: str, task_type: str = "",
                       result_preview: str = "") -> bool:
    """Report task completion to Telegram."""
    if not _should_send("task_success", task_name):
        return False

    preview = f"\n📋 {result_preview[:150]}" if result_preview else ""
    text = (
        f"✅ <b>Task Completa</b>\n\n"
        f"📌 <b>{task_name}</b> ({task_type}){preview}"
    )
    return _send_telegram(text)


def notify_task_failure(task_name: str, error: str) -> bool:
    """Report task failure to Telegram."""
    if not _should_send("task_failure", f"{task_name}:{int(time.time())}"):
        return False

    text = (
        f"❌ <b>Task Falhou</b>\n\n"
        f"📌 <b>{task_name}</b>\n"
        f"📝 <code>{error[:200]}</code>"
    )
    return _send_telegram(text)


def notify_routine_success(routine_name: str, duration_s: float = 0) -> bool:
    """Report routine completion to Telegram."""
    if not _should_send("routine_success", routine_name):
        return False

    dur = f"⏱ {duration_s:.0f}s" if duration_s > 0 else ""
    text = (
        f"✅ <b>Rotina OK</b>\n\n"
        f"🔄 <b>{routine_name}</b> {dur}"
    )
    return _send_telegram(text)


def notify_approval_pending(item_name: str, item_type: str = "post",
                            details: str = "", approve_cmd: str = "",
                            reject_cmd: str = "") -> bool:
    """Report pending approval — ALWAYS sends (no debounce)."""
    text = (
        f"🔔 <b>Aprovação Pendente</b>\n\n"
        f"📝 <b>{item_name}</b> ({item_type})\n"
    )
    if details:
        text += f"📋 {details[:300]}\n"
    text += f"\n"
    if approve_cmd:
        text += f"✅ <code>{approve_cmd}</code>\n"
    if reject_cmd:
        text += f"❌ <code>{reject_cmd}</code>\n"
    if not approve_cmd and not reject_cmd:
        text += f"\nResponda com o ID para aprovar/reprovar"

    return _send_telegram(text)


def notify_hourly_report(report_text: str) -> bool:
    """Send hourly activity report — ALWAYS sends."""
    text = f"📊 <b>Relatório Horário</b>\n\n{report_text}"
    return _send_telegram(text)


def notify_agent_event(agent: str, event: str, details: str = "") -> bool:
    """Report agent events (started, completed action, etc)."""
    if not _should_send("agent_event", f"{agent}:{event}"):
        return False

    text = (
        f"🤖 <b>{agent}</b>\n"
        f"📌 {event}\n"
    )
    if details:
        text += f"📋 {details[:200]}"

    return _send_telegram(text)


def notify_info(title: str, message: str) -> bool:
    """Generic info notification."""
    if not _should_send("info", f"{title}:{int(time.time())}"):
        return False
    text = f"ℹ️ <b>{title}</b>\n\n{message[:400]}"
    return _send_telegram(text)
