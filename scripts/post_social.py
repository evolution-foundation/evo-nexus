#!/usr/bin/env python3
"""Dispatch a social post to available channels.

Current state (2026-06-16):
  - X (Twitter): publication works via scripts/post_to_x.py (OAuth2 user-context,
    auto-refresh, retry on 429). Wired here.
  - Instagram: publishes via scripts/post_to_instagram.py (Graph API, Instagram
    Login or Facebook Login token). Requires media at a public https URL; local
    files fall back to the manual queue with a reason.
  - LinkedIn: no API integration in this workspace. Falls back to a manual queue.

Usage:
  python3 scripts/post_social.py x "tweet text" [--media img.png] [--account 1] [--dry-run]
  python3 scripts/post_social.py instagram "caption" --media https://host/foto.jpg [--dry-run]
  python3 scripts/post_social.py all "post body" [--media https://host/foto.jpg] [--dry-run]
  python3 scripts/post_social.py --show-queue
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_SCRIPT = ROOT / "scripts" / "post_to_x.py"
IG_SCRIPT = ROOT / "scripts" / "post_to_instagram.py"
QUEUE_PATH = ROOT / "workspace" / "social" / "manual_post_queue.jsonl"
LOG_PATH = ROOT / "workspace" / "social" / "post_dispatch_log.jsonl"

SUPPORTED_AUTO = {"x": "X (Twitter)", "instagram": "Instagram"}
MANUAL_CHANNELS = {"linkedin": "LinkedIn"}

VIDEO_EXTS = (".mp4", ".mov", ".m4v")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def dispatch_x(text: str, media: Path | None, account: int | None, dry_run: bool) -> dict:
    cmd = [sys.executable, str(POST_SCRIPT), text]
    if account is not None:
        cmd.extend(["--account", str(account)])
    if media is not None:
        cmd.extend(["--media", str(media)])
    if dry_run:
        cmd.append("--dry-run")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"channel": "x", "status": "error", "error": "timeout posting to X"}
    return {
        "channel": "x",
        "status": "ok" if result.returncode == 0 else "error",
        "stdout": result.stdout.strip()[:2000],
        "stderr": result.stderr.strip()[:1000],
        "returncode": result.returncode,
    }


def dispatch_instagram(text: str, media: Path | None, account: int | None, dry_run: bool) -> dict:
    """Publish to Instagram via Graph API.

    Media may be a public https URL or a local file — local files are uploaded to
    S3 (presigned URL) by post_to_instagram.py since the API fetches media
    server-side. With no media at all, falls back to the manual queue.
    """
    media_str = str(media) if media else ""
    if not media_str:
        reason = "Instagram requires media (image or video) — none provided."
        return queue_manual_reason("instagram", "Instagram", text, media, reason)

    # Local files are uploaded to S3 by post_to_instagram.py; https URLs pass through.
    is_video = media_str.lower().split("?")[0].endswith(VIDEO_EXTS)
    cmd = [sys.executable, str(IG_SCRIPT), "--caption", text]
    if is_video:
        cmd.extend(["--video-url", media_str, "--reels"])
    else:
        cmd.extend(["--image-url", media_str])
    if account is not None:
        cmd.extend(["--account", str(account)])
    if dry_run:
        cmd.append("--dry-run")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=360)
    except subprocess.TimeoutExpired:
        return {"channel": "instagram", "status": "error", "error": "timeout publishing to Instagram"}
    return {
        "channel": "instagram",
        "status": "ok" if result.returncode == 0 else "error",
        "stdout": result.stdout.strip()[:2000],
        "stderr": result.stderr.strip()[:1000],
        "returncode": result.returncode,
    }


def queue_manual_reason(channel: str, label: str, text: str, media: Path | None, reason: str) -> dict:
    record = {
        "queued_at": now_iso(),
        "channel": channel,
        "label": label,
        "text": text,
        "media": str(media) if media else None,
        "length": len(text),
        "reason": reason,
    }
    append_jsonl(QUEUE_PATH, record)
    return {
        "channel": channel,
        "status": "queued_manual",
        "reason": reason,
        "queue_path": str(QUEUE_PATH.relative_to(ROOT)),
    }


def queue_manual(channel: str, text: str, media: Path | None) -> dict:
    record = {
        "queued_at": now_iso(),
        "channel": channel,
        "label": MANUAL_CHANNELS[channel],
        "text": text,
        "media": str(media) if media else None,
        "length": len(text),
    }
    append_jsonl(QUEUE_PATH, record)
    return {
        "channel": channel,
        "status": "queued_manual",
        "queue_path": str(QUEUE_PATH.relative_to(ROOT)),
        "instruction": (
            f"Open {MANUAL_CHANNELS[channel]} and paste the text. "
            f"Row written to {QUEUE_PATH.relative_to(ROOT)}."
        ),
    }


def show_queue() -> int:
    if not QUEUE_PATH.exists():
        print(json.dumps({"queue_path": str(QUEUE_PATH.relative_to(ROOT)), "pending": []}, indent=2))
        return 0
    rows = []
    for line in QUEUE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    print(json.dumps({"queue_path": str(QUEUE_PATH.relative_to(ROOT)), "pending": rows}, indent=2, ensure_ascii=False))
    return 0


def parse_channel_arg(raw: str) -> list[str]:
    if raw == "all":
        return list(SUPPORTED_AUTO) + list(MANUAL_CHANNELS)
    return [c.strip() for c in raw.split(",") if c.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch a social post across channels")
    parser.add_argument("channel", nargs="?", default="all",
                        help="x | instagram | linkedin | comma list | all (default)")
    parser.add_argument("text", nargs="?", help="Post body. Reads stdin if omitted.")
    parser.add_argument("--media", help="Media to attach: local file path (X) or public https URL (Instagram).")
    parser.add_argument("--account", type=int, help="SOCIAL_TWITTER_<N> account index")
    parser.add_argument("--dry-run", action="store_true", help="Validate without publishing")
    parser.add_argument("--show-queue", action="store_true", help="Print manual post queue and exit")
    args = parser.parse_args()

    if args.show_queue:
        return show_queue()

    if not args.text:
        args.text = sys.stdin.read().strip()
    if not args.text:
        raise SystemExit("post body is required (arg or stdin)")

    channels = parse_channel_arg(args.channel)
    unknown = [c for c in channels if c not in SUPPORTED_AUTO and c not in MANUAL_CHANNELS]
    if unknown:
        raise SystemExit(f"unknown channels: {unknown}. supported: {list(SUPPORTED_AUTO | MANUAL_CHANNELS)}")

    results: list[dict] = []
    text_for_x = args.text if len(args.text) <= 280 else args.text[:277] + "..."
    for ch in channels:
        if ch == "x":
            results.append(dispatch_x(text_for_x, args.media, args.account, args.dry_run))
        elif ch == "instagram":
            results.append(dispatch_instagram(args.text, args.media, args.account, args.dry_run))
        else:
            results.append(queue_manual(ch, args.text, args.media))

    summary = {
        "dispatched_at": now_iso(),
        "channels": channels,
        "dry_run": args.dry_run,
        "results": results,
    }
    append_jsonl(LOG_PATH, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    failed_auto = [r for r in results if r.get("channel") in SUPPORTED_AUTO and r.get("status") != "ok"]
    return 1 if failed_auto else 0


if __name__ == "__main__":
    raise SystemExit(main())
