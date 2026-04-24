"""Brain Repo — Claude Code CLI transcripts mirror."""

import logging
import os
import re
import shutil
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = re.sub(r"[^A-Za-z0-9\-_]", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "project"


def find_claude_projects_dir(service_user: str | None = None) -> Path | None:
    """Locate the Claude Code CLI projects directory.

    Resolution order:
    1. /home/<service_user>/.claude/projects/ if service_user provided
    2. Path.home() / ".claude" / "projects"

    Returns None if no valid directory is found.
    """
    candidates: list[Path] = []

    if service_user:
        candidates.append(Path(f"/home/{service_user}") / ".claude" / "projects")

    candidates.append(Path.home() / ".claude" / "projects")

    for candidate in candidates:
        if candidate.is_dir():
            log.debug("find_claude_projects_dir: found %s", candidate)
            return candidate

    log.debug("find_claude_projects_dir: no projects dir found (candidates: %s)", candidates)
    return None


def _get_dir_owner_name(path: Path) -> str | None:
    """Return the username of the owner of a directory, or None."""
    try:
        import pwd
        st = path.stat()
        return pwd.getpwuid(st.st_uid).pw_name
    except Exception:
        return None


def _detect_service_user(install_dir: Path) -> str | None:
    """Detect service user from SUDO_USER env var or install_dir ownership."""
    sudo_user = os.getenv("SUDO_USER", "").strip()
    if sudo_user:
        return sudo_user
    return _get_dir_owner_name(install_dir)


def mirror_transcripts(
    install_dir: Path,
    brain_repo_dir: Path,
    days: int = 30,
) -> int:
    """Mirror recent Claude Code CLI transcript files to brain_repo.

    Copies .jsonl files modified in the last `days` days from the Claude
    projects directory to memory/raw-transcripts/<project-slug>/<session>.jsonl.

    Also prunes files in brain_repo older than `days` days (rolling window).

    Returns count of files copied/updated.
    """
    service_user = _detect_service_user(install_dir)
    projects_dir = find_claude_projects_dir(service_user)

    if projects_dir is None:
        log.info("mirror_transcripts: Claude projects dir not found, skipping")
        return 0

    dest_root = brain_repo_dir / "memory" / "raw-transcripts"
    dest_root.mkdir(parents=True, exist_ok=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    copied = 0

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        project_slug = _slugify(project_dir.name)
        dest_project = dest_root / project_slug
        dest_project.mkdir(parents=True, exist_ok=True)

        for jsonl_file in project_dir.glob("*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    continue

                dest_file = dest_project / jsonl_file.name
                # Copy if dest doesn't exist or source is newer
                if not dest_file.exists() or mtime > datetime.fromtimestamp(
                    dest_file.stat().st_mtime, tz=timezone.utc
                ):
                    shutil.copy2(str(jsonl_file), str(dest_file))
                    copied += 1
                    log.debug("mirror_transcripts: copied %s", jsonl_file.name)

            except Exception as exc:
                log.warning("mirror_transcripts: error copying %s: %s", jsonl_file, exc)

    # Pruning: remove files older than `days` days from brain_repo
    pruned = 0
    for old_file in dest_root.rglob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(old_file.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                old_file.unlink(missing_ok=True)
                pruned += 1
        except Exception as exc:
            log.debug("mirror_transcripts: could not prune %s: %s", old_file, exc)

    if pruned:
        log.info("mirror_transcripts: pruned %d old transcript files", pruned)

    log.info("mirror_transcripts: copied/updated %d files", copied)
    return copied
