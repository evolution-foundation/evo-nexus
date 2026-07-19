"""MemPalace mining worker — per-file progress for the dashboard UI.

Spawned by the /api/mempalace/mine endpoint as a detached subprocess.
Reads a JSON payload from stdin with:
    {
        "palace_path": "...",
        "status_file": "...",
        "targets": [{"path": "...", "wing": "...", "room": "..."}]
    }

Writes progress to the status file after every processed file so the
frontend can render a real progress bar with ETA instead of a spinner.

Not meant to be imported — this is a standalone worker script.
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# When Python executes this file directly, it prepends the script's directory
# (dashboard/backend/routes) to sys.path. That shadows the installed `mempalace`
# package with our sibling `mempalace.py` Flask blueprint. Strip the script dir
# from sys.path BEFORE importing so `from mempalace.miner import ...` resolves
# to the real package in the venv.
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _script_dir]

from mempalace.miner import (  # noqa: E402
    get_collection,
    load_config,
    process_file,
    scan_project,
)


def ensure_mempalace_yaml(
    source_path: str, wing_override: str | None, room_override: str | None = None
) -> None:
    """Auto-create a minimal mempalace.yaml in the source dir if missing.

    mempalace.miner.load_config() calls sys.exit(1) when it can't find the
    config, which would abort the whole mining run. To let the dashboard
    index any folder the user adds without asking them to hand-write a yaml,
    we drop a sensible default in place on first run: one wing named after
    the folder (or the label the user gave), and one room.

    room_override lets the dashboard's hierarchy sync (Mission -> Wing,
    Project -> Room) name that single room after the owning Project instead
    of the generic "general" — every file under a Project's
    workspace_folder_path then routes to a room named after that Project
    (miner.detect_room's Priority 1 matches the folder path against the
    room name). Users can still hand-edit the file later to add more rooms.
    """
    src = Path(source_path).expanduser().resolve()
    yaml_path = src / "mempalace.yaml"
    legacy_path = src / "mempal.yaml"
    if yaml_path.exists() or legacy_path.exists():
        return

    wing = wing_override or src.name or "default"
    room = room_override or "general"
    content = (
        f"wing: {wing}\n"
        "rooms:\n"
        f"  - name: {room}\n"
        "    description: All project files\n"
    )
    try:
        yaml_path.write_text(content, encoding="utf-8")
    except Exception:
        # Source might be read-only or on a path we can't write to.
        # load_config will then sys.exit and the source gets skipped,
        # which is the same behavior as before.
        pass


def main() -> None:
    payload = json.loads(sys.stdin.read())
    palace_path: str = payload["palace_path"]
    status_file = Path(payload["status_file"])
    targets: list[dict] = payload["targets"]

    def write_status(data: dict) -> None:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def read_status() -> dict:
        try:
            return json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    pid = os.getpid()
    started_at_iso = read_status().get("started_at") or datetime.now(
        timezone.utc
    ).isoformat()
    started_monotonic = time.monotonic()

    # Phase 1 — scan all sources up front so we have a total for the progress bar
    plans = []
    total_files = 0
    for t in targets:
        try:
            files = scan_project(t["path"])
        except Exception:
            files = []
        plans.append({"source": t, "files": files})
        total_files += len(files)

    collection = get_collection(palace_path)

    state = {
        "files_done": 0,
        "files_skipped": 0,
        "drawers_added": 0,
    }

    def publish(
        current_file: str | None = None,
        current_source: str | None = None,
        phase: str = "mining",
    ) -> None:
        elapsed = time.monotonic() - started_monotonic
        rate = (
            state["files_done"] / elapsed if elapsed > 0 and state["files_done"] > 0 else 0
        )
        remaining = total_files - state["files_done"]
        eta_seconds = remaining / rate if rate > 0 else None
        write_status(
            {
                "pid": pid,
                "started_at": started_at_iso,
                "phase": phase,
                "sources": [t["path"] for t in targets],
                "current_source": current_source,
                "current_file": current_file,
                "files_done": state["files_done"],
                "files_total": total_files,
                "files_skipped": state["files_skipped"],
                "drawers_added": state["drawers_added"],
                "elapsed_seconds": round(elapsed, 1),
                "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
                "rate_files_per_sec": round(rate, 2),
            }
        )

    publish(phase="mining")

    # Phase 2 — per-file loop
    for plan in plans:
        t = plan["source"]
        source_path = t["path"]
        files = plan["files"]

        # Drop a default mempalace.yaml in the source dir if it doesn't
        # have one yet. This lets the dashboard index any folder on first
        # run instead of failing silently with 100% skipped.
        ensure_mempalace_yaml(source_path, t.get("wing"), t.get("room"))

        try:
            config = load_config(source_path)
            wing = t.get("wing") or config["wing"]
            rooms = config.get(
                "rooms", [{"name": "general", "description": "All project files"}]
            )
        except SystemExit:
            # load_config prints + sys.exit when mempalace.yaml is missing
            state["files_skipped"] += len(files)
            state["files_done"] += len(files)
            publish(current_source=source_path, phase="mining")
            continue
        except Exception:
            state["files_skipped"] += len(files)
            state["files_done"] += len(files)
            publish(current_source=source_path, phase="mining")
            continue

        project_path = Path(source_path).expanduser().resolve()

        for filepath in files:
            try:
                drawers = process_file(
                    filepath=filepath,
                    project_path=project_path,
                    collection=collection,
                    wing=wing,
                    rooms=rooms,
                    agent="mempalace",
                    dry_run=False,
                )
                if drawers == 0:
                    state["files_skipped"] += 1
                else:
                    state["drawers_added"] += drawers
            except Exception:
                state["files_skipped"] += 1

            state["files_done"] += 1
            publish(current_file=str(filepath), current_source=source_path, phase="mining")

    # Phase 3 — done. The next /api/mempalace/status poll will see the PID
    # is gone and clear the file, so we can leave a final summary here.
    publish(phase="done")


if __name__ == "__main__":
    # On any crash, write the traceback to a sibling log file next to the
    # status file so /api/mempalace/status can surface it instead of leaving
    # the worker as a silent zombie.
    try:
        main()
    except Exception:  # noqa: BLE001
        try:
            # Best-effort: peek at stdin-consumed payload via env, else
            # fall back to a temp log in the palace dir.
            err_path = Path.home() / ".evonexus_mempalace_worker_error.log"
            err_path.write_text(
                f"[{datetime.now(timezone.utc).isoformat()}]\n"
                + traceback.format_exc(),
                encoding="utf-8",
            )
        except Exception:
            pass
        raise
