"""Bootstrap .claude/settings.json with EvoNexus plugin dispatcher hooks.

Idempotent: detects managed entries via `_evonexus_managed: true` sentinel field.
Preserves ALL pre-existing hook entries (e.g., agent-tracker).

C3 (Vault F3): only registers the 4 v1a whitelisted events.
ADR-3: sentinel field `_evonexus_managed` instead of marker comment (JSON-valid).
F11 (Vault): creates rotating backup before each write (R3 — non-blocking).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
SETTINGS_PATH = WORKSPACE / ".claude" / "settings.json"

# C3 (Vault F3): must stay in sync with claude_hook_dispatcher.V1A_EVENTS
V1A_EVENTS: list[str] = ["PreToolUse", "PostToolUse", "Stop", "SubagentStop"]

# Command template for the dispatcher.
# Uses $CLAUDE_PROJECT_DIR (exported by Claude Code) so the hook works regardless
# of the cwd from which Claude was launched. The python3 -m form requires the
# workspace root on sys.path, which is not guaranteed when a user runs Claude
# from a subdirectory.
DISPATCHER_CMD_TEMPLATE = (
    'python3 "$CLAUDE_PROJECT_DIR/dashboard/backend/claude_hook_dispatcher.py" {event}'
)

# Number of settings.json backups to retain (F11 Vault R3)
_BACKUP_COUNT = 5


def _managed_entry(event: str) -> dict:
    """Build a hook entry with the EvoNexus managed sentinel."""
    return {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": DISPATCHER_CMD_TEMPLATE.format(event=event),
            "_evonexus_managed": True,  # ADR-3 sentinel — JSON-valid, ignored by Claude Code
        }],
    }


def _has_managed_hook(entries: list[dict]) -> bool:
    """Return True if any hook entry in the list has our sentinel."""
    for entry in entries or []:
        for hook in entry.get("hooks", []) or []:
            if hook.get("_evonexus_managed") is True:
                return True
    return False


def _rotate_backups() -> None:
    """Keep the last _BACKUP_COUNT backups of settings.json (F11 Vault R3)."""
    backup_dir = SETTINGS_PATH.parent
    # Collect existing backups sorted newest-first
    pattern = ".settings.json.backup-"
    backups = sorted(
        [p for p in backup_dir.iterdir() if p.name.startswith(".settings.json.backup-")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Prune oldest beyond limit
    for old in backups[_BACKUP_COUNT - 1:]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass


def _write_backup() -> None:
    """Write a timestamped backup of settings.json before overwriting (F11 Vault R3)."""
    if not SETTINGS_PATH.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = SETTINGS_PATH.parent / f".settings.json.backup-{ts}"
    try:
        shutil.copy2(SETTINGS_PATH, backup_path)
        _rotate_backups()
    except OSError:
        pass  # backup failure is non-blocking


def run() -> bool:
    """Idempotent bootstrap. Call unconditionally from app.py on startup.

    Returns:
        True if settings.json is now correct (whether or not it was mutated).
        False if settings.json could not be read (corrupted) — skips write.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if SETTINGS_PATH.exists():
        try:
            current = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[claude-hook-bootstrap] WARN: {SETTINGS_PATH} is not valid JSON "
                f"({exc}) — skipping bootstrap to avoid corruption"
            )
            return False
    else:
        current = {}

    hooks = current.setdefault("hooks", {})
    mutated = False

    for event in V1A_EVENTS:
        entries = hooks.setdefault(event, [])
        if not _has_managed_hook(entries):
            entries.append(_managed_entry(event))
            mutated = True

    if not mutated:
        return True  # already configured — no-op

    # F11 (Vault R3): backup before write
    _write_backup()

    # Atomic write: tmp + os.replace (POSIX atomic within same FS)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=SETTINGS_PATH.parent,
        prefix=".settings.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, SETTINGS_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(
        f"[claude-hook-bootstrap] Registered dispatcher for events: {V1A_EVENTS}"
    )
    return True
