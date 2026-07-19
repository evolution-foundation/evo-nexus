"""Per-job persistent workspace management for MediaJob (social-media-production).

Layout (briefing Etapa 7):

    MEDIA_WORKSPACE/jobs/<job-id>/
        input/job.json          # manifest the backend writes for OpenCode to read
        input/brand/            # visual identity assets, copied in (allowlisted types)
        input/assets/           # other authorized assets
        project/                # HyperFrames composition, authored by OpenCode
        output/final.mp4
        output/publication_manifest.json
        logs/opencode.ndjson
        logs/render.log

Every function here enforces "a job can only touch its own directory" — no
path derived from a manifest, an uploaded filename, or a job_id must ever be
allowed to resolve outside MEDIA_WORKSPACE/jobs/<job-id>/.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

ALLOWED_ASSET_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif",
    ".mp4", ".mov", ".webm",
    ".mp3", ".wav", ".m4a",
    ".ttf", ".otf", ".woff", ".woff2",
    ".json",
})

_JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class PathSecurityError(ValueError):
    """Raised whenever a resolved path would escape the job's own workspace."""


def media_workspace_root() -> Path:
    return Path(os.environ.get("MEDIA_WORKSPACE", "/workspace/media")).resolve()


def new_job_id() -> str:
    return str(uuid.uuid4())


def is_valid_job_id(job_id: str) -> bool:
    return bool(job_id) and bool(_JOB_ID_RE.match(job_id))


def job_dir(job_id: str) -> Path:
    """The root directory for one job. Never accepts anything but a
    server-generated uuid4 — job_id is never taken from client-controlled
    path segments beyond the DB primary key itself.
    """
    if not is_valid_job_id(job_id):
        raise PathSecurityError(f"job_id inválido (esperado uuid4): {job_id!r}")
    return media_workspace_root() / "jobs" / job_id


def ensure_job_scaffold(job_id: str) -> Path:
    base = job_dir(job_id)
    for sub in ("input/brand", "input/assets", "project", "output", "logs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    _link_skills_dir(base)
    return base


def _link_skills_dir(job_workspace: Path) -> None:
    """Make the HyperFrames + social-media-production skills discoverable at
    `<job_workspace>/.claude/skills/` — OpenCode's `skill` tool looks for
    skills relative to its cwd (same convention as Claude Code), and
    media_executor.run_opencode_media_job() always runs with
    cwd=job_workspace (ADR-5/ADR-6). A symlink avoids copying the skill
    catalog into every job directory. Best-effort: a job scaffold must never
    fail just because this convenience symlink couldn't be created (e.g. the
    shared skills dir isn't mounted yet in a dev environment).
    """
    skills_source = Path(os.environ.get("MEDIA_SKILLS_DIR", "/workspace/.claude/skills-media"))
    if not skills_source.is_dir():
        return
    claude_dir = job_workspace / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    link_path = claude_dir / "skills"
    if link_path.is_symlink() or link_path.exists():
        return
    try:
        link_path.symlink_to(skills_source, target_is_directory=True)
    except OSError:
        pass


def resolve_within(base: Path, relative_path: str) -> Path:
    """Resolve `relative_path` against `base` and refuse anything that
    escapes it — rejects absolute paths, `..` traversal, and symlink escapes.
    This is the single choke point for any path an agent or a manifest hands
    back to the backend (briefing: 'não aceite paths absolutos enviados pelo
    agente').
    """
    base = base.resolve()
    if Path(relative_path).is_absolute():
        raise PathSecurityError(f"Path absoluto não permitido: {relative_path!r}")
    candidate = (base / relative_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise PathSecurityError(f"Path escapa do workspace do job: {relative_path!r}")
    return candidate


def safe_asset_filename(original_name: str) -> str:
    """Sanitize an uploaded/asset filename: strip directory components,
    replace unsafe characters, and enforce an extension allowlist.
    """
    name = Path(original_name).name  # drop any directory component
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_ASSET_EXTENSIONS:
        raise PathSecurityError(f"Extensão de asset não permitida: {ext!r}")
    stem = _SAFE_NAME_RE.sub("_", Path(name).stem).strip("._") or "asset"
    return f"{stem}{ext}"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Stream the file in chunks — never loads the whole video into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def retention_cutoff_days() -> int:
    return int(os.environ.get("MEDIA_RETENTION_DAYS", "7"))
