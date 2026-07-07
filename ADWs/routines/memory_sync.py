#!/usr/bin/env python3
"""ADW: Memory Sync — deterministic daily memory snapshot."""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner import banner, run_script, summary

WORKSPACE = Path(__file__).resolve().parents[2]
MEMORY_DIR = WORKSPACE / "memory"
SNAPSHOT_PATH = MEMORY_DIR / "context" / "automated-memory-sync.md"
INDEX_PATH = MEMORY_DIR / "index.md"
MEMORY_INDEX_PATH = MEMORY_DIR / "MEMORY.md"
LOG_PATH = MEMORY_DIR / "log.md"


def _latest_file(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    files = [
        path for path in root.rglob("*")
        if path.is_file() and path.name != ".gitkeep" and path.suffix.lower() in {".md", ".html", ".txt"}
    ]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _read_excerpt(path: Path | None, limit: int = 7000) -> str:
    if path is None:
        return "_No source file found._"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"_Could not read {path}: {exc}_"
    if path.suffix.lower() == ".html":
        text = re.sub(r"<script\\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<style\\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\\s+", " ", text).strip()
    return text[:limit].strip() or "_Source file is empty._"


def _git_output(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=WORKSPACE,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"_Command failed: {' '.join(args)} ({exc})_"
    output = (result.stdout or result.stderr or "").strip()
    return output or "_No output._"


def _ensure_index_entry(path: Path) -> None:
    entry = "- [Automated Memory Sync](memory/context/automated-memory-sync.md) — Latest bounded daily memory snapshot"
    try:
        content = path.read_text(encoding="utf-8") if path.exists() else "# Memory Index\n"
    except OSError:
        return
    if "memory/context/automated-memory-sync.md" in content:
        return
    if "## Context" in content:
        content = content.replace("## Context\n", f"## Context\n{entry}\n", 1)
    else:
        content = content.rstrip() + f"\n\n## Context\n{entry}\n"
    path.write_text(content, encoding="utf-8")


def _run_sync() -> dict:
    MEMORY_DIR.mkdir(exist_ok=True)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    daily = _latest_file(WORKSPACE / "workspace" / "daily-logs")
    meeting = _latest_file(WORKSPACE / "workspace" / "meetings" / "summaries")
    git_log = _git_output(["git", "log", "--oneline", "--since=24 hours ago", "-n", "20"])
    diff_stat = _git_output(["git", "diff", "--stat", "HEAD~5"])

    daily_label = daily.relative_to(WORKSPACE) if daily else "none"
    meeting_label = meeting.relative_to(WORKSPACE) if meeting else "none"
    content = f"""# Automated Memory Sync

Last updated: {now}

This file is maintained by `ADWs/routines/memory_sync.py`. It is intentionally bounded so the daily routine stays reliable and does not spend an open-ended agent session scanning the workspace.

## Sources

- Daily log: `{daily_label}`
- Meeting summary: `{meeting_label}`
- Git window: last 24 hours, max 20 commits
- Diff window: `git diff --stat HEAD~5`

## Recent Daily Log Excerpt

{_read_excerpt(daily)}

## Recent Meeting Summary Excerpt

{_read_excerpt(meeting)}

## Git Activity

```text
{git_log}
```

## Diff Stat

```text
{diff_stat}
```
"""
    SNAPSHOT_PATH.write_text(content, encoding="utf-8")

    _ensure_index_entry(INDEX_PATH)
    _ensure_index_entry(MEMORY_INDEX_PATH)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(
            f"\n[{datetime.now().date()}] SYNC — Automated bounded snapshot updated "
            f"from daily log `{daily_label}`, meeting `{meeting_label}`, and git activity. "
            "1 memory updated, 0 created, 0 cross-references propagated."
        )

    return {
        "ok": True,
        "summary": "updated automated-memory-sync.md from bounded sources",
    }


def main():
    banner("Memory Sync", "Bounded snapshot -> memory/context")
    results = [run_script(_run_sync, log_name="memory-sync", timeout=60)]
    summary(results, "Memory Sync")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
