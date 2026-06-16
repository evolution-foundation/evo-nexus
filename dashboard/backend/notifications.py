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

import json
import os
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
            return resp.status == 200
    except Exception:
        return False


def send_telegram_alert(text: str) -> bool:
    """Send a preformatted Telegram alert.

    Used by heartbeat_runner and other modules that already build the final
    HTML message. Returns False only when Telegram credentials are missing or
    Telegram rejects/fails the request.
    """
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
