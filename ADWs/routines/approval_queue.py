#!/usr/bin/env python3
"""
ADWs/routines/approval_queue.py — Fila de aprovação para posts e publicações.

Quando um agente (mako, pixel, social) gera conteúdo para publicar,
o conteúdo vai pra fila de aprovação em vez de ser publicado direto.
O Telegram notifica você com o conteúdo + comandos para aprovar/reprovar.

Tabela no DB: approval_queue
    id, content_type, title, body, media_url, agent, status, created_at, decided_at

Comandos:
    /aprovar <id> — aprova e publica
    /reprovar <id> — reprova e descarta
    /fila — mostra pendentes
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_table():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approval_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_type VARCHAR(50) NOT NULL,
            title VARCHAR(500),
            body TEXT,
            media_url VARCHAR(500),
            agent VARCHAR(100),
            status VARCHAR(20) DEFAULT 'pending',
            created_at VARCHAR(30),
            decided_at VARCHAR(30),
            rejection_reason VARCHAR(500)
        )
    """)
    conn.commit()
    conn.close()


def add_to_queue(content_type: str, title: str = "", body: str = "",
                  media_url: str = "", agent: str = "") -> int:
    """Add content to approval queue. Returns the queue item id."""
    _ensure_table()
    conn = _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    cur = conn.execute("""
        INSERT INTO approval_queue (content_type, title, body, media_url, agent, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (content_type, title, body, media_url, agent, now))
    conn.commit()
    item_id = cur.lastrowid or 0
    conn.close()
    return item_id


def get_pending() -> list[dict]:
    """Get all pending approval items."""
    _ensure_table()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM approval_queue WHERE status='pending' ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def approve(item_id: int) -> bool:
    """Approve and item. Returns True if found."""
    conn = _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    cur = conn.execute(
        "UPDATE approval_queue SET status='approved', decided_at=? WHERE id=? AND status='pending'",
        (now, item_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def reject(item_id: int, reason: str = "") -> bool:
    """Reject an item. Returns True if found."""
    conn = _get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    cur = conn.execute(
        "UPDATE approval_queue SET status='rejected', decided_at=?, rejection_reason=? WHERE id=? AND status='pending'",
        (now, reason, item_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_stats() -> dict:
    """Get approval queue stats."""
    _ensure_table()
    conn = _get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM approval_queue GROUP BY status"
    ).fetchall()
    conn.close()
    return {r["status"]: r["cnt"] for r in rows}


def format_telegram_notification(item: dict) -> str:
    """Format a queue item for Telegram notification."""
    ct = item.get("content_type", "post")
    title = item.get("title", "Sem título")[:100]
    body = item.get("body", "")[:300]
    agent = item.get("agent", "sistema")
    item_id = item["id"]

    return (
        f"🔔 <b>Aprovação Pendente</b>\n\n"
        f"📝 <b>{title}</b> ({ct})\n"
        f"🤖 Por: {agent}\n"
        f"📋 {body}\n\n"
        f"✅ <code>/aprovar {item_id}</code>\n"
        f"❌ <code>/reprovar {item_id}</code>"
    )


def format_pending_list(items: list[dict]) -> str:
    """Format pending items list for Telegram."""
    if not items:
        return "📋 Fila de aprovação: vazia ✨"

    lines = [f"📋 <b>Fila de Aprovação</b> ({len(items)} pendentes)\n"]
    for item in items[:10]:
        title = item.get("title", "Sem título")[:60]
        ct = item.get("content_type", "?")
        agent = item.get("agent", "?")
        lines.append(f"  #{item['id']} | {ct} | {title} | 🤖 {agent}")

    if len(items) > 10:
        lines.append(f"\n... e mais {len(items) - 10}")

    lines.append(f"\n✅ <code>/aprovar &lt;id&gt;</code> | ❌ <code>/reprovar &lt;id&gt;</code>")
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    _ensure_table()
    stats = get_stats()
    print(f"Stats: {stats}")
    pending = get_pending()
    print(f"Pending: {len(pending)}")
    for p in pending[:3]:
        print(f"  #{p['id']} | {p['content_type']} | {p.get('title','')[:50]}")
