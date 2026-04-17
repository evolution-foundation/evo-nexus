#!/usr/bin/env python3
"""
EvoNexus Scheduler
Runs core routines on schedule. Custom routines loaded from config/routines.yaml.
Usage: runs automatically with make dashboard-app
"""

import subprocess
import os
import sys
import signal
import time
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(__file__).parent
PYTHON = "uv run python" if os.system("command -v uv > /dev/null 2>&1") == 0 else "python3"
ROUTINES_DIR = WORKSPACE / "ADWs" / "routines"
PID_FILE = WORKSPACE / "ADWs" / "logs" / "scheduler.pid"


def acquire_lock() -> bool:
    """Ensure only one scheduler instance runs. Returns False if another is alive.

    Uses O_CREAT|O_EXCL for atomic creation, then validates the PID inside.
    Avoids the TOCTOU race where two processes both see a stale PID file and
    both proceed to start.
    """
    import fcntl
    try:
        fd = os.open(str(PID_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        # File exists — check if the owner is still alive
        try:
            existing_pid = int(PID_FILE.read_text().strip())
            os.kill(existing_pid, 0)
            print(f"  Scheduler already running (PID {existing_pid}). Exiting.")
            return False
        except (ProcessLookupError, ValueError):
            # Stale lock — remove and retry once
            PID_FILE.unlink(missing_ok=True)
            try:
                fd = os.open(str(PID_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return True
            except FileExistsError:
                print("  Scheduler lock contention — another instance just started. Exiting.")
                return False


def release_lock():
    """Remove PID file on clean shutdown."""
    PID_FILE.unlink(missing_ok=True)


def run_adw(name: str, script: str, args: str = ""):
    """Execute a routine as subprocess."""
    now = datetime.now().strftime("%H:%M")
    script_path = ROUTINES_DIR / script
    if not script_path.exists():
        print(f"  {now} ✗ {name} — script not found: {script}")
        return

    try:
        cmd = f"{PYTHON} {script_path}"
        if args:
            cmd += f" {args}"
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(WORKSPACE),
            timeout=900,
            capture_output=True,
            text=True,
        )
        status = "✓" if result.returncode == 0 else "✗"
        print(f"  {now} {status} {name}")
    except subprocess.TimeoutExpired:
        print(f"  {now} ✗ {name} timeout (15min)")
    except Exception as e:
        print(f"  {now} ✗ {name} error: {e}")


def setup_schedule():
    """Configure core routines. Custom routines loaded from config/routines.yaml."""
    import schedule

    # ── Core routines (shipped with repo) ──
    schedule.every().day.at("07:00").do(run_adw, "Good Morning", "good_morning.py")
    schedule.every().day.at("21:00").do(run_adw, "End of Day", "end_of_day.py")
    schedule.every().day.at("21:15").do(run_adw, "Memory Sync", "memory_sync.py")
    # Disabled — replaced by Weekly Review (Team) in routines.yaml
    # schedule.every().friday.at("08:00").do(run_adw, "Weekly Review", "weekly_review.py")
    schedule.every().sunday.at("09:00").do(run_adw, "Memory Lint", "memory_lint.py")
    schedule.every().day.at("21:00").do(run_adw, "Daily Backup", "backup.py")

    # ── Custom routines (from config/routines.yaml if exists) ──
    _load_custom_routines(schedule)


def _load_custom_routines(schedule):
    """Load custom routines from config/routines.yaml."""
    config_path = WORKSPACE / "config" / "routines.yaml"
    if not config_path.exists():
        return

    try:
        import yaml
        with open(config_path) as f:
            config = yaml.safe_load(f)
        if not config:
            return

        for r in config.get("daily", []) or []:
            if not r.get("enabled", True):
                continue
            script = r.get("script", "")
            name = r.get("name", script)
            args = r.get("args", "")
            if r.get("interval"):
                schedule.every(int(r["interval"])).minutes.do(run_adw, name, f"custom/{script}", args)
            elif r.get("time"):
                schedule.every().day.at(r["time"]).do(run_adw, name, f"custom/{script}", args)

        for r in config.get("weekly", []) or []:
            if not r.get("enabled", True):
                continue
            script = r.get("script", "")
            name = r.get("name", script)
            args = r.get("args", "")
            day = r.get("day", "friday").lower()
            time_str = r.get("time", "09:00")
            days = r.get("days", [day])
            for d in days:
                getattr(schedule.every(), d, schedule.every().friday).at(time_str).do(
                    run_adw, name, f"custom/{script}", args
                )

        global _monthly_routines
        _monthly_routines = config.get("monthly", []) or []

    except Exception as e:
        print(f"  Warning: Failed to load custom routines: {e}")


_monthly_routines = []


def main():
    """Entry point — standalone scheduler."""
    import schedule

    if not acquire_lock():
        sys.exit(1)

    print("EvoNexus Scheduler")
    setup_schedule()
    total = len(schedule.get_jobs())
    print(f"  {total} routines scheduled")
    print(f"  Press Ctrl+C to stop\n")

    def shutdown(sig, frame):
        release_lock()
        print("\n  Scheduler stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    monthly_ran = False
    while True:
        schedule.run_pending()
        now = datetime.now()
        if now.day == 1 and now.hour == 8 and not monthly_ran:
            for r in _monthly_routines:
                if r.get("enabled", True):
                    run_adw(r.get("name", r.get("script", "")), f"custom/{r['script']}", r.get("args", ""))
            monthly_ran = True
        elif now.day != 1:
            monthly_ran = False
        time.sleep(30)


if __name__ == "__main__":
    main()
