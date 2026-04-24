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
import threading
import time
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(__file__).parent
PYTHON = "uv run python" if os.system("command -v uv > /dev/null 2>&1") == 0 else "python3"
ROUTINES_DIR = WORKSPACE / "ADWs" / "routines"
PID_FILE = WORKSPACE / "ADWs" / "logs" / "scheduler.pid"

# SIGHUP reload flag — set by handler, cleared by main loop (ADR-2)
_reload_flag = threading.Event()


def _handle_sighup(signum, frame):
    """POSIX: only async-signal-safe ops here. Event.set() qualifies."""
    _reload_flag.set()


def acquire_lock() -> bool:
    """Ensure only one scheduler instance runs. Returns False if another is alive.

    Uses O_CREAT|O_EXCL for atomic creation, then validates the PID inside.
    Avoids the TOCTOU race where two processes both see a stale PID file and
    both proceed to start.
    """
    import fcntl
    # ADWs/logs/ is not in git (no .gitkeep) and setup.py's create_folders
    # only makes the user-facing workspace dirs, so on a fresh clone the
    # parent of PID_FILE doesn't exist and os.open() raises FileNotFoundError
    # before the scheduler can even start. Make it idempotently.
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
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


def _load_routines_from_yaml(schedule, config_path: Path, is_plugin: bool = False,
                             disabled_make_ids: set | None = None):
    """Load routines from a single YAML file into the schedule.

    For plugin files, errors are swallowed (broken plugin doesn't kill core).
    For the core config, errors are re-raised.

    Wave 1.1: if disabled_make_ids is provided, skip matching make-ids.
    The make-id for a plugin routine is derived as: plugin-{slug}-{name.lower().replace(' ','-')}.
    """
    import yaml

    if not config_path.exists():
        return

    _disabled = disabled_make_ids or set()

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        if not config:
            return

        source_label = f"plugin:{config_path.parent.name}" if is_plugin else "core"
        # Determine slug for make-id derivation (only used for plugin routines)
        plugin_slug = config_path.parent.name if is_plugin else ""

        for r in config.get("daily", []) or []:
            if not r.get("enabled", True):
                continue
            script = r.get("script", "")
            name = r.get("name", script)
            args = r.get("args", "")
            # Wave 1.1: check if this routine is individually disabled
            if _disabled and is_plugin:
                make_id = f"plugin-{plugin_slug}-{name.lower().replace(' ', '-')}"
                if make_id in _disabled:
                    print(f"  [{source_label}] skipped disabled routine '{name}' ({make_id})")
                    continue
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
            # Wave 1.1: check if this routine is individually disabled
            if _disabled and is_plugin:
                make_id = f"plugin-{plugin_slug}-{name.lower().replace(' ', '-')}"
                if make_id in _disabled:
                    print(f"  [{source_label}] skipped disabled routine '{name}' ({make_id})")
                    continue
            day = r.get("day", "friday").lower()
            time_str = r.get("time", "09:00")
            days = r.get("days", [day])
            for d in days:
                getattr(schedule.every(), d, schedule.every().friday).at(time_str).do(
                    run_adw, name, f"custom/{script}", args
                )

        global _monthly_routines
        monthly = config.get("monthly", []) or []
        # Wave 1.1: filter disabled monthly routines for plugins
        if _disabled and is_plugin:
            filtered_monthly = []
            for r in monthly:
                name = r.get("name", r.get("script", ""))
                make_id = f"plugin-{plugin_slug}-{name.lower().replace(' ', '-')}"
                if make_id in _disabled:
                    print(f"  [{source_label}] skipped disabled monthly routine '{name}' ({make_id})")
                else:
                    filtered_monthly.append(r)
            monthly = filtered_monthly
        # Plugin monthly routines are appended; core replaces the list
        if is_plugin:
            _monthly_routines.extend(monthly)
        else:
            _monthly_routines = monthly

    except Exception as e:
        if is_plugin:
            print(f"  Warning: Failed to load plugin routines from {config_path}: {e}")
        else:
            raise


def _load_disabled_routines() -> dict[str, set]:
    """Load per-plugin disabled routines from capabilities_disabled column.

    Wave 1.1 (ADR BN-1): open short-lived read-only connection at setup_schedule() time.
    Returns {slug -> set of disabled make-ids} — empty dict if DB unavailable (degrade gracefully).
    """
    result: dict[str, set] = {}
    db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
    try:
        import sqlite3 as _sqlite3
        import json as _json
        conn = _sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT slug, capabilities_disabled FROM plugins_installed "
            "WHERE enabled = 1 AND status = 'active'"
        ).fetchall()
        conn.close()
        for row in rows:
            try:
                caps = _json.loads(row["capabilities_disabled"] or "{}")
                disabled = caps.get("routines", [])
                if disabled:
                    result[row["slug"]] = set(disabled)
            except Exception:
                pass
    except Exception:
        pass  # DB unavailable — degrade to "nothing disabled", scheduler must not crash
    return result


def _load_custom_routines(schedule):
    """Load custom routines from config/routines.yaml + plugins/*/routines.yaml (ADR-2).

    Wave 1.1: skips plugin routines whose make-id is in capabilities_disabled["routines"].
    """
    # 1. Core config
    _load_routines_from_yaml(schedule, WORKSPACE / "config" / "routines.yaml", is_plugin=False)

    # 2. Plugin routines — sorted for deterministic ordering (ADR-2)
    #    Supports both layouts:
    #      plugins/{slug}/routines.yaml          (flat file)
    #      plugins/{slug}/routines/*.yaml        (directory, GAP-7)
    plugins_dir = WORKSPACE / "plugins"
    if plugins_dir.exists():
        # Wave 1.1: fetch disabled routines once before iterating plugins
        disabled_routines = _load_disabled_routines()

        plugin_routine_files: list[Path] = []
        plugin_routine_files.extend(plugins_dir.glob("*/routines.yaml"))
        plugin_routine_files.extend(plugins_dir.glob("*/routines/*.yaml"))
        for plugin_routines in sorted(plugin_routine_files):
            plugin_slug = plugin_routines.parent.name
            _load_routines_from_yaml(
                schedule, plugin_routines, is_plugin=True,
                disabled_make_ids=disabled_routines.get(plugin_slug, set()),
            )


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
    signal.signal(signal.SIGHUP, _handle_sighup)  # ADR-2: hot-reload on SIGHUP

    monthly_ran = False
    while True:
        # Hot-reload: check flag before running pending jobs (ADR-2)
        if _reload_flag.is_set():
            _reload_flag.clear()
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  {ts} [reload] SIGHUP received — clearing schedule and re-reading routines")
            schedule.clear()
            setup_schedule()
            total = len(schedule.get_jobs())
            print(f"  {ts} [reload] {total} routines scheduled")

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
