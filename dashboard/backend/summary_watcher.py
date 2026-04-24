"""Summary Watcher — thread-areas heartbeat safety net (Passo 4c).

Invoked by the heartbeat runner for 'summary-watcher'. This is NOT a Claude
session — it runs directly as a Python script. It:

1. Queries active thread tickets (memory_md_path IS NOT NULL, status != 'archived')
2. For each: counts JSONL lines (streaming) to get actual message_count
3. Pre-filters by mtime to skip unchanged JSONLs (avoids O(N) scan on idle threads)
4. If actual count != DB message_count: updates message_count (monotonic)
5. If delta since last_summary_at_message >= SUMMARY_EVERY_N: enqueues summary job

ADR reference: [C]architecture-summary-trigger.md — Option B (safety net component
of the chosen Option E hybrid D+B approach).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
LOGS_DIR = WORKSPACE / "ADWs" / "logs" / "chat"
SUMMARY_EVERY_N = 20   # must match tickets.py SUMMARY_EVERY_N


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _count_lines(path: Path) -> int:
    """Count newline-delimited JSON messages in a JSONL file via streaming."""
    count = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    count += 1
    except OSError:
        pass
    return count


def _find_jsonl(session_id: str, agent_name: str | None) -> Path | None:
    """Locate JSONL file for a given session_id."""
    if agent_name:
        candidate = LOGS_DIR / agent_name / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    flat = LOGS_DIR / f"{session_id}.jsonl"
    if flat.exists():
        return flat
    return None


def _enqueue_summary(ticket_id: str, memory_md_path: str, up_to_turn: int) -> None:
    """Spawn summary_worker.py as a fire-and-forget subprocess."""
    worker = Path(__file__).resolve().parent / "summary_worker.py"
    if not worker.exists():
        print(f"[summary_watcher] summary_worker.py not found at {worker}", flush=True)
        return
    subprocess.Popen(
        [sys.executable, str(worker),
         "--ticket-id", ticket_id,
         "--memory-path", memory_md_path,
         "--up-to-turn", str(up_to_turn)],
        start_new_session=True,
    )
    print(f"[summary_watcher] summary job queued for ticket {ticket_id} (turn {up_to_turn})", flush=True)


def run_watcher() -> dict:
    """Main logic — returns stats dict."""
    db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
    if not db_path.exists():
        return {"error": f"DB not found: {db_path}"}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    stats = {"checked": 0, "updated": 0, "queued": 0, "skipped": 0}

    try:
        rows = conn.execute(
            "SELECT id, assignee_agent, thread_session_id, memory_md_path, "
            "message_count, last_summary_at_message "
            "FROM tickets "
            "WHERE memory_md_path IS NOT NULL AND status != 'archived'"
        ).fetchall()

        now = _now_iso()

        for row in rows:
            ticket_id = row["id"]
            session_id = row["thread_session_id"]
            agent_name = row["assignee_agent"]
            memory_md_path = row["memory_md_path"]
            db_count = row["message_count"]
            last_summary = row["last_summary_at_message"]

            stats["checked"] += 1

            if not session_id:
                stats["skipped"] += 1
                continue  # No session yet — nothing to count

            jsonl = _find_jsonl(session_id, agent_name)
            if jsonl is None:
                stats["skipped"] += 1
                continue

            # mtime pre-filter: skip if file hasn't changed since DB was last updated
            # We use last_summary_at_message as a rough proxy — if mtime older than
            # 61s ago (heartbeat period), the file was likely already processed.
            try:
                mtime = jsonl.stat().st_mtime
                import time
                if time.time() - mtime > 70:  # heartbeat is 60s; 10s buffer
                    stats["skipped"] += 1
                    continue
            except OSError:
                stats["skipped"] += 1
                continue

            actual_count = _count_lines(jsonl)
            if actual_count == db_count:
                stats["skipped"] += 1
                continue

            # Update message_count monotonically
            if actual_count > db_count:
                conn.execute(
                    "UPDATE tickets SET message_count = ?, updated_at = ? "
                    "WHERE id = ? AND message_count < ?",
                    (actual_count, now, ticket_id, actual_count),
                )
                conn.commit()
                stats["updated"] += 1
                new_count = actual_count
            else:
                new_count = db_count  # actual < db: trust DB (rewind scenario)

            delta = new_count - last_summary
            if delta >= SUMMARY_EVERY_N:
                updated = conn.execute(
                    "UPDATE tickets SET last_summary_at_message = ?, updated_at = ? "
                    "WHERE id = ? AND last_summary_at_message < ?",
                    (new_count, now, ticket_id, new_count),
                )
                conn.commit()
                if updated.rowcount > 0:
                    _enqueue_summary(ticket_id, memory_md_path, new_count)
                    stats["queued"] += 1

    finally:
        conn.close()

    return stats


def main():
    print(f"[summary_watcher] Starting at {_now_iso()}", flush=True)
    stats = run_watcher()
    print(f"[summary_watcher] Done: {json.dumps(stats)}", flush=True)
    if "error" in stats:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
