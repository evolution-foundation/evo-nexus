#!/usr/bin/env python3
"""
ADWs/routines/daily_status_report.py — Report diário de status para WhatsApp.

Coleta: rotinas executadas, tasks/goals, tickets abertos, falhas recentes.
Envia resumo compacto via WhatsApp (Evolution Go API).

Usage:
  python3 daily_status_report.py --phone 5511999999999
  python3 daily_status_report.py --dry-run   # só imprime, não envia
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE / "dashboard" / "backend"))

DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"
BRT_OFFSET = timedelta(hours=-3)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_brt() -> datetime:
    return _now_utc() + BRT_OFFSET


def _get_db():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def generate_report() -> str:
    """Gera report diário como string formatada."""
    import os
    conn = _get_db()
    now_brt = _now_brt()
    today_utc = (now_brt - BRT_OFFSET).strftime("%Y-%m-%d") + "T00:00:00.000000Z"
    yesterday_utc = (_now_brt() - timedelta(days=1)).strftime("%Y-%m-%d") + "T00:00:00.000000Z"

    lines = [f"📋 <b>Status Diário — {now_brt.strftime('%d/%m/%Y')}</b>"]

    # ── Scheduled Tasks (today) ──
    try:
        task_rows = conn.execute(
            "SELECT name, status, last_run, error FROM scheduled_tasks WHERE created_at > ? ORDER BY status DESC, name ASC",
            (today_utc,),
        ).fetchall()
        if task_rows:
            ok_tasks = [r for r in task_rows if r["status"] == "success"]
            fail_tasks = [r for r in task_rows if r["status"] == "fail"]
            lines.append(f"\n📌 <b>Rotinas hoje:</b> {len(ok_tasks)} ok / {len(fail_tasks)} fail")
            for r in fail_tasks[:8]:
                err = (r["error"] or "sem detalhe")[:60]
                lines.append(f"  ❌ {r['name']}: {err}")
    except Exception:
        pass

    # ── Goal Tasks ──
    try:
        gt_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM goal_tasks GROUP BY status"
        ).fetchall()
        gt_stats = {r["status"]: r["cnt"] for r in gt_rows}
        if gt_stats:
            done = gt_stats.get("done", 0)
            open_ = gt_stats.get("open", 0)
            in_prog = gt_stats.get("in_progress", 0)
            lines.append(f"\n🎯 <b>Goals:</b> {done} done / {in_prog} em andamento / {open_} abertos")
    except Exception:
        pass

    # ── Tickets ──
    try:
        open_tickets = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE status='open'"
        ).fetchone()["cnt"]
        blocked_tickets = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE status='blocked'"
        ).fetchone()["cnt"]
        in_progress_tickets = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE status='in_progress'"
        ).fetchone()["cnt"]
        lines.append(f"\n🎫 <b>Tickets:</b> {open_tickets} open / {in_progress_tickets} em andamento / {blocked_tickets} bloqueados")

        pending_assign = conn.execute(
            "SELECT title, priority, assignee_agent FROM tickets WHERE status IN ('open','in_progress','blocked') ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 ELSE 3 END, created_at ASC LIMIT 5"
        ).fetchall()
        if pending_assign:
            lines.append("\n  <b>Top tickets:</b>")
            for t in pending_assign:
                pri_icon = "🔴" if t["priority"] == "urgent" else ("🟡" if t["priority"] == "high" else "🔵")
                assignee = t["assignee_agent"] or "sem agente"
                title = t["title"][:45]
                lines.append(f"  {pri_icon} {title} — {assignee}")
    except Exception:
        pass

    # ── Heartbeat failures (last 24h) ──
    try:
        last24 = (_now_utc() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        hb_fails = conn.execute(
            "SELECT heartbeat_id, error FROM heartbeat_runs WHERE started_at > ? AND status='fail' ORDER BY started_at DESC LIMIT 5"
        , (last24,)).fetchall()
        if hb_fails:
            lines.append(f"\n⚠️ <b>Falhas (24h):</b> {len(hb_fails)}")
            for h in hb_fails:
                err = (h["error"] or "exit code 1")[:70]
                lines.append(f"  • {h['heartbeat_id']}: {err}")
    except Exception:
        pass

    # ── Footer ──
    lines.append(f"\n🕐 Gerado em {now_brt.strftime('%H:%M BRT')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report diário de status via WhatsApp")
    parser.add_argument("--phone", required=False, help="Número WhatsApp (ex: 5511999999999). Também lê WHATSAPP_PHONE do .env")
    parser.add_argument("--dry-run", action="store_true", help="Apenas imprime, não envia")
    args = parser.parse_args()

    phone = args.phone or os.environ.get("WHATSAPP_PHONE", "")

    report = generate_report()

    if args.dry_run:
        print(report)
        return 0

    if not phone:
        print("[daily_status_report] ERRO: passe --phone ou defina WHATSAPP_PHONE no .env")
        return 1

    try:
        from notifications import send_whatsapp
    except ImportError:
        print("[daily_status_report] ERRO: não foi importar notifications.send_whatsapp")
        return 1

    ok = send_whatsapp(report, phone)
    if ok:
        print(f"[daily_status_report] enviado para {phone}")
    else:
        print(f"[daily_status_report] FALHA ao enviar (cheque EVOLUTION_GO_URL/KEY no .env)")
        return 1

    return 0


if __name__ == "__main__":
    import os
    sys.exit(main())
