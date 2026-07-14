#!/usr/bin/env python3
"""
ADWs/routines/hourly_report.py — Relatório horário de atividade do EvoNexus.

Roda de hora em hora durante horário comercial (08h-20h BRT).
Coleta: heartbeats executados, tasks completadas, rotinas, erros.
Envia resumo compacto via Telegram.

Usage:
    python3 hourly_report.py                    # gera e envia
    python3 hourly_report.py --dry-run          # só imprime, não envia
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE / "dashboard" / "backend"))

DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"
BRT_OFFSET = timedelta(hours=-3)


def _now_brt() -> datetime:
    return datetime.now(timezone.utc) + BRT_OFFSET


def _get_db():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def generate_report() -> str:
    """Generate hourly activity report as formatted string."""
    conn = _get_db()
    now_brt = _now_brt()
    hour_ago_utc = (now_brt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    today_utc = now_brt.strftime("%Y-%m-%d") + "T00:00:00.000000Z"

    # ── Heartbeats (last hour) ──
    hb_runs = conn.execute(
        """SELECT heartbeat_id, status, duration_ms, error, triggered_by
           FROM heartbeat_runs
           WHERE started_at > ?
           ORDER BY started_at DESC""",
        (hour_ago_utc,)
    ).fetchall()

    # ── Heartbeats (today) ──
    hb_today = conn.execute(
        """SELECT status, COUNT(*) as cnt
           FROM heartbeat_runs
           WHERE started_at > ?
           GROUP BY status""",
        (today_utc,)
    ).fetchall()
    hb_today_stats = {r["status"]: r["cnt"] for r in hb_today}

    # ── Tasks (today) ──
    tasks_today = conn.execute(
        """SELECT status, COUNT(*) as cnt
           FROM scheduled_tasks
           WHERE created_at > ?
           GROUP BY status""",
        (today_utc,)
    ).fetchall()
    task_stats = {r["status"]: r["cnt"] for r in tasks_today}

    # ── Goal Tasks ──
    goal_tasks = conn.execute(
        """SELECT status, COUNT(*) as cnt
           FROM goal_tasks
           GROUP BY status"""
    ).fetchall()
    gt_stats = {r["status"]: r["cnt"] for r in goal_tasks}

    # ── Pending approvals (tickets) ──
    pending_tickets = conn.execute(
        """SELECT COUNT(*) as cnt FROM tickets WHERE status='open'"""
    ).fetchone()["cnt"]

    # ── Zombie runs ──
    zombies = conn.execute(
        """SELECT COUNT(*) as cnt FROM heartbeat_runs
           WHERE status='running' AND started_at < ?""",
        ((now_brt - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),)
    ).fetchone()["cnt"]

    # ── Top failures (last hour) ──
    failures = [r for r in hb_runs if r["status"] == "fail"]

    conn.close()

    # ── Format ──
    hour_str = now_brt.strftime("%H:%M")
    lines = [f"📊 <b>Relatório {hour_str} BRT</b>"]

    # Heartbeat summary
    ok = hb_today_stats.get("success", 0)
    fail = hb_today_stats.get("fail", 0)
    running = hb_today_stats.get("running", 0)
    total = ok + fail + running
    rate = f"{(ok / max(1, ok + fail) * 100):.0f}%" if (ok + fail) > 0 else "N/A"
    lines.append(f"❤️ Heartbeats hoje: <b>{ok} ok</b> / {fail} fail / {running} running ({rate})")

    # Last hour detail
    if hb_runs:
        last_hour_ok = sum(1 for r in hb_runs if r["status"] == "success")
        last_hour_fail = sum(1 for r in hb_runs if r["status"] == "fail")
        lines.append(f"⏱ Última hora: +{last_hour_ok} ok / -{last_hour_fail} fail")

    # Failures detail
    if failures:
        lines.append(f"\n⚠️ <b>Falhas ({len(failures)})</b>:")
        for f in failures[:5]:  # max 5
            err = (f["error"] or "exit code 1")[:80]
            lines.append(f"  • {f['heartbeat_id']}: {err}")

    # Tasks
    if task_stats:
        t_pending = task_stats.get("pending", 0)
        t_completed = task_stats.get("completed", 0)
        t_failed = task_stats.get("failed", 0)
        lines.append(f"\n📌 Tasks: {t_completed} ok / {t_failed} fail / {t_pending} pending")

    # Goal tasks
    if gt_stats:
        gt_open = gt_stats.get("open", 0)
        gt_done = gt_stats.get("done", 0)
        lines.append(f"🎯 Goals: {gt_done} done / {gt_open} open")

    # Pending tickets
    if pending_tickets > 0:
        lines.append(f"🎫 Tickets abertos: {pending_tickets}")

    # Zombies
    if zombies > 0:
        lines.append(f"🧟 Zombie runs: {zombies} (recomendo limpeza)")

    # Footer
    lines.append(f"\n🕐 Próximo relatório: {(now_brt + timedelta(hours=1)).strftime('%H:%M')}")

    return "\n".join(lines)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Only print, don't send")
    args = p.parse_args()

    report = generate_report()

    if args.dry_run:
        print(report)
        return 0

    from notifications import notify_hourly_report
    sent = notify_hourly_report(report)
    if sent:
        print(f"[hourly_report] sent at {_now_brt().strftime('%H:%M BRT')}")
    else:
        print(f"[hourly_report] NOT sent (Telegram not configured)")
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
