"""Summary Worker — thread-areas (Feature thread-areas, Passo 4b).

CLI usage:
    python summary_worker.py --ticket-id <uuid> --memory-path <rel-path> --up-to-turn <int>

Reads the last SUMMARY_CHUNK_TURNS messages from the ticket's JSONL log,
calls Claude (Haiku) to generate a summary section, and appends it to memory.md.
Spawned as fire-and-forget by tickets.py `_enqueue_summary()`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
SUMMARY_CHUNK_TURNS = 20          # matches SUMMARY_EVERY_N in tickets.py
LOGS_DIR = WORKSPACE / "ADWs" / "logs" / "chat"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _find_jsonl(ticket_id: str) -> Path | None:
    """Find the JSONL for the ticket's thread_session_id via DB lookup."""
    import sqlite3
    db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
    if not db_path.exists():
        print(f"[summary_worker] DB not found: {db_path}", flush=True)
        return None
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT thread_session_id, assignee_agent FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        print(f"[summary_worker] No thread_session_id for ticket {ticket_id}", flush=True)
        return None
    session_id, agent_name = row[0], row[1] or "unknown"
    # ChatLogger writes to ADWs/logs/chat/{agent_name}/{session_id}.jsonl
    candidate = LOGS_DIR / agent_name / f"{session_id}.jsonl"
    if candidate.exists():
        return candidate
    # Fallback: flat directory
    flat = LOGS_DIR / f"{session_id}.jsonl"
    if flat.exists():
        return flat
    print(f"[summary_worker] JSONL not found for session {session_id}", flush=True)
    return None


def _read_last_n_turns(jsonl_path: Path, n: int) -> list[dict]:
    """Stream JSONL and return last N assistant/user messages."""
    messages: list[dict] = []
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = obj.get("role")
                if role in ("user", "assistant"):
                    messages.append(obj)
    except OSError as exc:
        print(f"[summary_worker] Cannot read JSONL {jsonl_path}: {exc}", flush=True)
        return []
    return messages[-n:]


def _build_summary_prompt(messages: list[dict], ticket_title: str, up_to_turn: int) -> str:
    lines = [
        f"You are summarising a chat thread for ticket: {ticket_title!r}.",
        "Below are the last conversation turns. Write a concise summary section",
        f"(max 300 words) titled '## {datetime.now(timezone.utc).strftime('%Y-%m-%d')} — turns up to {up_to_turn}'.",
        "Focus on decisions made, conclusions reached, and open questions. Be factual and brief.",
        "",
        "--- MESSAGES ---",
    ]
    for msg in messages:
        role = msg.get("role", "?")
        if role == "user":
            text = msg.get("text", "")
            lines.append(f"[User] {text[:500]}")
        elif role == "assistant":
            blocks = msg.get("blocks", [])
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            text = " ".join(text_parts)[:500]
            lines.append(f"[Assistant] {text}")
    lines.append("--- END MESSAGES ---")
    lines.append("")
    lines.append("Write the summary section now:")
    return "\n".join(lines)


def _call_claude(prompt: str) -> str | None:
    """Call Claude SDK (Haiku) for the summary. Returns the text or None on error."""
    try:
        import anthropic
    except ImportError:
        print("[summary_worker] anthropic package not found — skipping summary", flush=True)
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[summary_worker] ANTHROPIC_API_KEY not set — skipping summary", flush=True)
        return None

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text if response.content else ""
        return text.strip()
    except Exception as exc:
        print(f"[summary_worker] Claude API error: {exc}", flush=True)
        return None


def _append_to_memory(memory_path: Path, section: str) -> None:
    """Append summary section to memory.md (append-only, Q2)."""
    separator = "\n\n---\n\n"
    try:
        with open(memory_path, "a", encoding="utf-8") as f:
            f.write(separator + section + "\n")
        print(f"[summary_worker] Appended summary to {memory_path}", flush=True)
    except OSError as exc:
        print(f"[summary_worker] Cannot write memory.md: {exc}", flush=True)


def run_summary(ticket_id: str, memory_md_path: str, up_to_turn: int) -> bool:
    """Main entry point — returns True on success."""
    memory_file = WORKSPACE / memory_md_path
    if not memory_file.exists():
        print(f"[summary_worker] memory.md not found: {memory_file}", flush=True)
        return False

    # Get ticket title from DB
    import sqlite3
    db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT title FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.close()
    ticket_title = row[0] if row else ticket_id

    jsonl_path = _find_jsonl(ticket_id)
    if jsonl_path is None:
        print(f"[summary_worker] No JSONL found for ticket {ticket_id} — skipping", flush=True)
        return False

    messages = _read_last_n_turns(jsonl_path, SUMMARY_CHUNK_TURNS)
    if not messages:
        print(f"[summary_worker] No messages found in JSONL — skipping", flush=True)
        return False

    prompt = _build_summary_prompt(messages, ticket_title, up_to_turn)
    summary = _call_claude(prompt)
    if not summary:
        return False

    _append_to_memory(memory_file, summary)
    return True


def main():
    parser = argparse.ArgumentParser(description="Thread summary worker")
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--memory-path", required=True)
    parser.add_argument("--up-to-turn", type=int, required=True)
    args = parser.parse_args()

    success = run_summary(args.ticket_id, args.memory_path, args.up_to_turn)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
