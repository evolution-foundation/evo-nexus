#!/usr/bin/env python3
"""Schedule and dispatch the day's social posts from the editorial calendar.

Reads `workspace/marketing/calendars/[C]calendario-editorial-*.md`, finds the
posts scheduled for *today* (BRT), and when the local BRT clock passes each
slot's target time, invokes `scripts/post_social.py` once per post. A JSONL
ledger at `workspace/social/scheduled_posts.jsonl` keeps idempotency — a post
is only dispatched once.

State (2026-06-16):
- X path: automated via scripts/post_to_x.py → scripts/post_social.py x
- IG / LinkedIn: no API in this workspace → fall back to manual queue log
  inside post_social.py. Scheduled posts still get queued; the cron-like
  behaviour here just moves clock-watching out of the operator's hands.

Window logic: a slot fires when current BRT time is within
`[target, target + window_minutes]` AND the post was not yet dispatched.
Default window = 60 minutes so a routine run every 15-30 minutes won't miss
slots if the scheduler hiccups.

Usage:
    python3 scripts/publish_scheduled.py                    # process today's slots
    python3 scripts/publish_scheduled.py --date 2026-06-17  # specific day
    python3 scripts/publish_scheduled.py --dry-run          # show plan, no dispatch
    python3 scripts/publish_scheduled.py --window 90        # widen or narrow window
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CALENDAR_DIR = ROOT / "workspace" / "marketing" / "calendars"
DISPATCH_SCRIPT = ROOT / "scripts" / "post_social.py"
LEDGER_PATH = ROOT / "workspace" / "social" / "scheduled_posts.jsonl"

BRT = ZoneInfo("America/Sao_Paulo")

# Legacy format: "### Dia 1 — Seg 16/06"
DAY_HEADER_RE = re.compile(r"^###\s+Dia\s+(\d+)\s+—\s+\w+\s+(\d{2})/(\d{2})\b")
# V2 format: "### 🟦 Seg 16/06 — DIA 1 · EIXO ..."
DAY_HEADER_V2_RE = re.compile(
    r"^###\s+[^\w]*\s*(\w{3})\s+(\d{2})/(\d{2})\s+—\s+DIA\s+(\d+)\b"
)
SLOT_LINE_RE = re.compile(
    r"^\|\s*(X|LinkedIn|Instagram)\s*\|\s*(\d{1,2}):(\d{2})\s*\|"
)
CHANNEL_ALIAS = {"X": "x", "LinkedIn": "linkedin", "Instagram": "instagram"}

# Cadência table parser — matches the "Cadência e horários" section
# Format: | Dia | X | LinkedIn | Instagram |
#         | Seg 16 | — | 09:00 | — |   (v2: with date)
#         | Seg | — | 09:00 | — |        (legacy: day name only)
CADENCIA_HEADER_RE = re.compile(r"^\|\s*Dia\s*\|\s*X\s*\|\s*LinkedIn\s*\|\s*Instagram\s*\|")
CADENCIA_ROW_RE = re.compile(
    r"^\|\s*(\w+(?:\s+\d{2})?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|"
)
DAY_NAME_TO_INDEX = {
    "seg": 0, "ter": 1, "qua": 2, "qui": 3, "sex": 4, "sáb": 5, "dom": 5,
    "sab": 5,  # without accent
}
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


def brt_now() -> datetime:
    return datetime.now(BRT)


def brt_today_str() -> str:
    return brt_now().strftime("%d/%m")


def parse_day_label(label: str) -> datetime:
    """Convert 'Dia 1 — Seg 16/06' → datetime in current year (BRT-naive date)."""
    m = DAY_HEADER_RE.search(label)
    if not m:
        raise ValueError(f"unrecognized day header: {label!r}")
    _, dd, mm = m.groups()
    today = brt_now().date()
    return datetime(today.year, int(mm), int(dd))


def parse_day_label_v2(day_name: str, dd: str, mm: str) -> datetime:
    """Convert ('Seg', '16', '06') → datetime in current year (BRT-naive date)."""
    today = brt_now().date()
    return datetime(today.year, int(mm), int(dd))


def parse_any_day_header(line: str) -> tuple[int, datetime] | None:
    """Return (day_index, date) for a day header in legacy or v2 format, else None.

    Legacy: '### Dia 1 — Seg 16/06'
    V2:     '### 🟦 Seg 16/06 — DIA 1 · EIXO ...'
    """
    m = DAY_HEADER_RE.search(line)
    if m:
        day_index, dd, mm = m.groups()
        today = brt_now().date()
        return int(day_index), datetime(today.year, int(mm), int(dd))
    m = DAY_HEADER_V2_RE.search(line)
    if m:
        day_name, dd, mm, day_index = m.groups()
        return int(day_index), parse_day_label_v2(day_name, dd, mm)
    return None


def find_latest_calendar() -> Path:
    if not CALENDAR_DIR.exists():
        raise SystemExit(f"calendar dir not found: {CALENDAR_DIR}")
    files = [p for p in CALENDAR_DIR.iterdir()
             if p.is_file() and p.name.startswith("[C]calendario-editorial-") and p.suffix == ".md"]
    files.sort()
    if not files:
        raise SystemExit(f"no editorial calendar under {CALENDAR_DIR}")
    return files[-1]


def parse_cadencia_table(calendar_text: str) -> dict[int, dict[str, time]]:
    """Parse the cadência/horários table → {day_index: {channel: time}}.

    The cadência table maps day-of-week names to channel times:
      | Dia | X | LinkedIn | Instagram |
      | Seg | — | 09:00 | — |

    We match day names to Dia headers by order (1st day = first row, etc.).
    """
    in_cadencia = False
    rows: list[tuple[str, str, str, str]] = []
    for raw in calendar_text.splitlines():
        stripped = raw.strip()
        if CADENCIA_HEADER_RE.match(stripped):
            in_cadencia = True
            continue
        if in_cadencia:
            if not stripped.startswith("|"):
                break  # end of table
            m = CADENCIA_ROW_RE.match(stripped)
            if m:
                rows.append(m.groups())

    # Map rows to day indices by matching day names to Dia headers
    # First, collect all Dia header dates in order (legacy + v2 formats)
    day_dates: list[tuple[int, datetime]] = []
    for raw in calendar_text.splitlines():
        parsed = parse_any_day_header(raw)
        if parsed:
            day_dates.append(parsed)

    result: dict[int, dict[str, time]] = {}
    for row_day_name, x_cell, li_cell, ig_cell in rows:
        day_key = row_day_name.strip().lower()[:3]
        # Find matching Dia header by day-of-week
        day_idx = None
        for idx, dt in day_dates:
            if dt.strftime("%a").lower()[:3] == day_key:
                day_idx = idx
                break
        if day_idx is None:
            # Fallback: match by position
            row_pos = list(DAY_NAME_TO_INDEX.get(day_key, -1) for _ in [0])
            continue

        slots: dict[str, time] = {}
        for channel_key, cell in [("x", x_cell), ("linkedin", li_cell), ("instagram", ig_cell)]:
            cell = cell.strip()
            if cell == "—" or cell == "-" or not cell:
                continue
            m_time = TIME_RE.search(cell)
            if m_time:
                slots[channel_key] = time(int(m_time.group(1)), int(m_time.group(2)))
        if slots:
            result[day_idx] = slots

    return result


def parse_slots(calendar_text: str) -> list[dict]:
    """Return one record per day with its (channel, time, slug_day).

    Tries the inline SLOT_LINE_RE first (legacy format). If no slots found,
    falls back to parsing the cadência table at the bottom of the calendar.
    """
    # Try legacy inline format first
    days: list[dict] = []
    current: dict | None = None
    for raw in calendar_text.splitlines():
        m_header = DAY_HEADER_RE.search(raw)
        if m_header:
            if current is not None:
                days.append(current)
            current = {"day_index": int(m_header.group(1)), "date": parse_day_label(raw), "slots": []}
            continue
        if current is None:
            continue
        m_slot = SLOT_LINE_RE.match(raw)
        if not m_slot:
            continue
        channel_raw, hh, mm = m_slot.groups()
        channel = CHANNEL_ALIAS[channel_raw]
        current["slots"].append({"channel": channel, "time": time(int(hh), int(mm))})
    if current is not None:
        days.append(current)

    if any(d["slots"] for d in days):
        return days  # legacy format worked

    # Fallback: parse cadência table
    cadencia = parse_cadencia_table(calendar_text)
    if not cadencia:
        return days  # return empty (will show no_slots)

    # Build day records from Dia headers + cadencia mapping (legacy + v2)
    days = []
    for raw in calendar_text.splitlines():
        parsed = parse_any_day_header(raw)
        if not parsed:
            continue
        idx, dt = parsed
        day_slots = []
        for channel, t in cadencia.get(idx, {}).items():
            day_slots.append({"channel": channel, "time": t})
        days.append({"day_index": idx, "date": dt, "slots": day_slots})

    return days


# Tolerate v2 label suffixes: "Hook Reels", "CTA slide 7", "Hook T1", etc.
HOOK_RE = re.compile(r"\|\s*\*{0,2}Hook[^|]*\|\s*(.+?)\s*\|")
BODY_RE = re.compile(r"\|\s*\*{0,2}Ângulo[^|]*\|\s*(.+?)\s*\|")
CTA_RE = re.compile(r"\|\s*\*{0,2}CTA[^|]*\|\s*(.+?)\s*\|")


def extract_post_text(calendar_text: str, day_index: int) -> str:
    """Extract post body from a day's section in the calendar.

    Looks for the Hook field in the day's content table and builds a
    post-ready string: hook + angle + CTA.
    """
    # Find the section for this day (matches legacy '### Dia N' and v2 headers)
    lines = calendar_text.splitlines()
    in_section = False
    hook = angle = cta = ""
    for line in lines:
        parsed = parse_any_day_header(line)
        if parsed:
            if parsed[0] == day_index:
                in_section = True
                continue
            if in_section:
                break  # reached the next day's section
            continue
        if in_section:
            m = HOOK_RE.match(line)
            if m:
                hook = m.group(1).strip().strip('"').strip("'")
                continue
            m = BODY_RE.match(line)
            if m:
                angle = m.group(1).strip()
                continue
            m = CTA_RE.match(line)
            if m:
                cta = m.group(1).strip()
                continue

    parts = [p for p in [hook, angle, cta] if p]
    if parts:
        return " ".join(parts)
    return ""


def slot_key(day_index: int, channel: str, slot_time: time) -> str:
    return f"d{day_index}-{channel}-{slot_time.strftime('%H%M')}"


def load_ledger() -> set[str]:
    if not LEDGER_PATH.exists():
        return set()
    seen: set[str] = set()
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("key"):
            seen.add(row["key"])
    return seen


def append_ledger(row: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def dispatch(text: str, channel: str, day_index: int, dry_run: bool) -> dict:
    """Delegate to scripts/post_social.py so we keep one source of dispatch truth."""
    cmd = [sys.executable, str(DISPATCH_SCRIPT), channel, text]
    if dry_run:
        cmd.append("--dry-run")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"channel": channel, "status": "error", "error": "timeout in post_social.py"}
    return {
        "channel": channel,
        "status": "dispatched" if proc.returncode == 0 else "error",
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout.strip().splitlines()[-3:],
        "stderr_tail": proc.stderr.strip().splitlines()[-3:],
    }


def select_target_day(days: list[dict], force_date: datetime | None) -> dict | None:
    target = force_date.date() if force_date else brt_now().date()
    for day in days:
        if day["date"].date() == target:
            return day
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch scheduled social posts for the day")
    parser.add_argument("--date", help="Target date dd/mm/yyyy; default = today BRT")
    parser.add_argument("--window", type=int, default=60,
                        help="Minutes after slot time the post is still eligible (default 60)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be dispatched; don't write anything")
    parser.add_argument("--calendar", help="Path to a specific calendar file (overrides latest detection)")
    args = parser.parse_args()

    calendar_path = Path(args.calendar) if args.calendar else find_latest_calendar()
    days = parse_slots(calendar_path.read_text(encoding="utf-8"))

    force_date = None
    if args.date:
        try:
            force_date = datetime.strptime(args.date, "%d/%m/%Y").replace(tzinfo=BRT)
        except ValueError:
            raise SystemExit(f"--date must be dd/mm/yyyy, got {args.date!r}")

    day = select_target_day(days, force_date)
    if day is None:
        target = (force_date or brt_now()).strftime("%d/%m/%Y")
        print(json.dumps({"status": "no_slots", "calendar": calendar_path.name,
                          "target_date": target, "dispatched": []}, indent=2, ensure_ascii=False))
        return 0

    now = brt_now()
    seen = load_ledger()
    dispatched: list[dict] = []
    untouched: list[dict] = []

    for slot in day["slots"]:
        slot_dt = now.replace(hour=slot["time"].hour, minute=slot["time"].minute,
                              second=0, microsecond=0)
        slot_end = slot_dt + timedelta(minutes=args.window)
        key = slot_key(day["day_index"], slot["channel"], slot["time"])

        if key in seen:
            untouched.append({"key": key, "reason": "already_dispatched",
                              "slot_brt": slot_dt.strftime("%H:%M")})
            continue

        if now < slot_dt:
            untouched.append({"key": key, "reason": "not_yet",
                              "slot_brt": slot_dt.strftime("%H:%M"),
                              "window_ends_brt": slot_end.strftime("%H:%M")})
            continue
        if now >= slot_end:
            untouched.append({"key": key, "reason": "window_expired",
                              "slot_brt": slot_dt.strftime("%H:%M"),
                              "window_ends_brt": slot_end.strftime("%H:%M")})
            continue

        post_text = extract_post_text(calendar_path.read_text(encoding="utf-8"), day["day_index"])
        if not post_text:
            post_text = f"[W{calendar_path.stem.split('-')[-1]}-D{day['day_index']}-{slot['channel']}]"
        result = dispatch(post_text, slot["channel"], day["day_index"], args.dry_run)
        record = {
            "key": key,
            "slot_brt": slot_dt.strftime("%H:%M"),
            "channel": slot["channel"],
            "day_index": day["day_index"],
            "calendar": calendar_path.name,
            "dispatched_at": now.isoformat(timespec="seconds"),
            "dry_run": args.dry_run,
            **result,
        }
        if not args.dry_run:
            append_ledger(record)
        dispatched.append(record)

    summary = {
        "status": "ok",
        "calendar": calendar_path.name,
        "target_date": day["date"].strftime("%d/%m/%Y"),
        "brt_now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "window_minutes": args.window,
        "dry_run": args.dry_run,
        "dispatched_count": len(dispatched),
        "dispatched": dispatched,
        "untouched": untouched,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    failed = [r for r in dispatched if r.get("status") == "error"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
