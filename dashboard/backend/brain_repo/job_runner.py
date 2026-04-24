"""Brain Repo — async job runner with global lock and cooperative cancel.

The three long-running operations (sync_force, tag_milestone, connect-bootstrap)
all run the same underlying pipeline: mirror workspace → secrets scan → commit →
optional tag → push. On a VPS behind Cloudflare that pipeline routinely exceeds
the 100 s request limit, so every entry point here is fire-and-forget — the
route returns 202 Accepted, a daemon thread executes the pipeline, and the
frontend polls BrainRepoConfig.{sync_in_progress, last_sync, last_error} for
completion.

Concurrency model:
    - Exactly one job runs at a time per process (module-level threading.Lock).
    - A DB flag (sync_in_progress) is the *authoritative* "is a job running"
      signal for multi-request visibility and for the janitor's stale-lock
      reclaim. The Lock only protects the in-process thread from racing with
      itself when the watcher fires concurrently with a manual trigger.
    - Cancel is cooperative: cancel_requested=1 is checked at well-defined
      checkpoints (between watched dirs, between secrets-scan batches, before
      each git subprocess). A git push already in flight is NEVER interrupted
      — truncating it mid-transfer is how you corrupt the remote. The UI must
      reflect "cancelling… (awaiting current push)" accurately.

State transitions (DB-visible):
    idle
      └─ enqueue_sync() ─→ sync_in_progress=1, sync_started_at=now,
                           sync_job_kind=<kind>, cancel_requested=0
                            │
                            ├─ success ──→ sync_in_progress=0, last_sync=now,
                            │              last_error=NULL
                            ├─ failure ──→ sync_in_progress=0,
                            │              last_error=<truncated msg>
                            └─ cancelled → sync_in_progress=0,
                                           last_error="cancelled by user"
"""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level lock: only one brain-repo pipeline runs per process at a time.
# The watcher debounce thread and the HTTP handler both acquire this, so a
# file change arriving the same second as a manual sync queues behind it
# instead of racing.
_job_lock = threading.Lock()

# Active kind when _job_lock is held — routed to BrainRepoConfig.sync_job_kind
# so the UI shows "Sync in progress", "Creating milestone", or "Initializing
# brain repo" without needing a separate field.
JOB_KIND_SYNC = "sync"
JOB_KIND_MILESTONE = "milestone"
JOB_KIND_BOOTSTRAP = "bootstrap"
JOB_KIND_WATCHER = "watcher"

# Max time a job may hold sync_in_progress before the janitor reclaims it.
# 20 min covers a slow push of a first-time sync over a modest VPS uplink;
# anything longer almost certainly means the worker crashed.
JOB_STALE_SECONDS = 1200


class JobCancelled(Exception):
    """Raised inside the pipeline when cancel_requested flips to 1.

    Callers (run_sync_pipeline) catch this and treat it as a clean stop —
    last_error gets "cancelled by user" instead of a traceback.
    """


# ────────────────────────────────────────────────────────────────────────
# DB helpers — every function takes flask_app + user_id and opens a scoped
# context, because we're running in a daemon thread that has none.
# ────────────────────────────────────────────────────────────────────────

def _acquire_db_lock(flask_app, user_id: int, kind: str) -> bool:
    """Atomically mark the config as busy. Returns False if already busy.

    Atomic via SQL: UPDATE ... WHERE sync_in_progress=0. Row count = 1 means
    we got it; 0 means another worker (or a stuck job past the janitor window)
    already holds it.
    """
    from models import BrainRepoConfig, db  # type: ignore[import]

    with flask_app.app_context():
        # SQLAlchemy 2.x bulk-update syntax; .update() returns affected row count.
        rows = (
            BrainRepoConfig.query
            .filter_by(user_id=user_id, sync_in_progress=False)
            .update({
                "sync_in_progress": True,
                "sync_started_at": datetime.now(timezone.utc),
                "sync_job_kind": kind,
                "cancel_requested": False,
            }, synchronize_session=False)
        )
        db.session.commit()
        return rows == 1


def _release_db_lock(flask_app, user_id: int, *, success: bool, error: str | None) -> None:
    """Clear the busy flag and persist success/error. Always runs in finally."""
    from models import BrainRepoConfig, db  # type: ignore[import]

    with flask_app.app_context():
        config = BrainRepoConfig.query.filter_by(user_id=user_id).first()
        if config is None:
            return
        config.sync_in_progress = False
        config.sync_started_at = None
        config.sync_job_kind = None
        config.cancel_requested = False
        if success:
            config.last_sync = datetime.now(timezone.utc)
            config.last_error = None
            config.pending_count = 0
        elif error:
            # Trim to fit the column; the UI surface only needs the top line.
            config.last_error = error[:300]
        db.session.commit()


def _check_cancel(flask_app, user_id: int) -> None:
    """Raise JobCancelled if the DB flag has been flipped."""
    from models import BrainRepoConfig  # type: ignore[import]

    with flask_app.app_context():
        config = BrainRepoConfig.query.filter_by(user_id=user_id).first()
        if config is not None and config.cancel_requested:
            raise JobCancelled()


def _load_config_snapshot(flask_app, user_id: int) -> dict | None:
    """Copy the fields the pipeline needs out of the session.

    We snapshot because SQLAlchemy instances don't travel across app contexts
    cleanly; passing plain values is simpler than re-opening the session for
    every field read inside the pipeline.
    """
    from models import BrainRepoConfig  # type: ignore[import]

    with flask_app.app_context():
        config = BrainRepoConfig.query.filter_by(user_id=user_id).first()
        if config is None or not config.github_token_encrypted:
            return None
        return {
            "encrypted_token": bytes(config.github_token_encrypted),
            "local_path": config.local_path,
            "repo_url": config.repo_url,
            "repo_owner": config.repo_owner,
            "repo_name": config.repo_name,
        }


# ────────────────────────────────────────────────────────────────────────
# Pipeline steps — each checks cancel before doing work.
# ────────────────────────────────────────────────────────────────────────

_WATCH_PATHS = ["memory", "workspace", "customizations", "config-safe"]

# Relative paths (POSIX) that are NEVER mirrored into the brain repo.
# `workspace/projects/` is where user-cloned git repos live (per the project's
# CLAUDE.md: "SHARED: git repos (Evo AI, Evolution Summit, EvoNexus…)") — often
# tens of gigabytes of code that already have their own GitHub. Mirroring them
# would double-store the world and make every sync unbearably slow.
_EXCLUDE_RELATIVE_PATHS = [
    "memory/raw-transcripts",
    "workspace/projects",
]

# Directory names skipped anywhere in the tree. Nested .git catches submodules
# and accidental clones inside watched folders; the build/cache dirs are the
# usual suspects that have millions of tiny files and are never worth syncing.
_EXCLUDE_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".cache",
    ".next",
    "target",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

# Files above this size skip the mirror entirely. GitHub refuses >100 MB; we
# cap at 10 MB because "brain" content (markdown, YAML, JSON, small PDFs) is
# never that big, and anything larger is almost certainly binary junk
# (videos, training data dumps, DB exports).
_MAX_FILE_BYTES = 10 * 1024 * 1024


def build_ignore_callback(
    workspace: Path,
    cancel_check: "callable | None" = None,
):
    """Return a shutil.copytree ignore callback wired to the exclusion rules.

    When ``cancel_check`` is provided, it's called once per visited directory;
    if it returns True, the callback ignores EVERY name in that directory and
    every subsequent directory — effectively short-circuiting copytree from
    inside its own walk. That's the only cooperative cancel mechanism we have
    against shutil.copytree, which doesn't expose a per-entry hook.
    """
    workspace_root = workspace.resolve()
    # Mutable flag the closure shares so once a cancel fires we keep returning
    # "ignore all names" for every remaining directory without asking again.
    cancelled = {"flag": False}

    def _ignore(src_dir: str, names: list[str]) -> list[str]:
        # Cancel check first so the user doesn't wait for the filter loop to
        # evaluate thousands of entries before bailing.
        if cancel_check is not None and not cancelled["flag"]:
            try:
                if cancel_check():
                    cancelled["flag"] = True
            except Exception:
                # A broken cancel_check must not take down the mirror; the
                # pipeline's outer _check_cancel will still catch the flag.
                pass
        if cancelled["flag"]:
            return list(names)

        ignored: list[str] = []
        src_dir_path = Path(src_dir)
        for n in names:
            full = src_dir_path / n
            try:
                rel = full.resolve().relative_to(workspace_root).as_posix()
            except Exception:
                continue
            for excl in _EXCLUDE_RELATIVE_PATHS:
                if rel == excl or rel.startswith(excl + "/"):
                    ignored.append(n)
                    break
            else:
                if full.is_dir() and n in _EXCLUDE_DIR_NAMES:
                    ignored.append(n)
                    continue
                try:
                    if full.is_file() and full.stat().st_size > _MAX_FILE_BYTES:
                        ignored.append(n)
                        continue
                except OSError:
                    pass
        return ignored

    return _ignore


def _mirror_workspace(
    flask_app,
    user_id: int,
    workspace: Path,
    brain_dir: Path,
) -> tuple[int, int]:
    """Copy watched dirs workspace → brain_dir, scanning for secrets.

    Mirrors the legacy routes._sync_workspace_to_brain_repo logic but with
    cancel checkpoints between directories and aggressive exclusions so the
    mirror doesn't try to copy ``workspace/projects/`` (gigabytes of cloned
    git repos).
    """
    files_copied = 0
    secrets_removed = 0

    # Cancel check closure — cheap DB read, invoked once per directory visited
    # by copytree. Lets the user interrupt a 20 GB first-time mirror without
    # waiting for it to finish.
    def _cancel_probe() -> bool:
        from models import BrainRepoConfig  # type: ignore[import]
        with flask_app.app_context():
            cfg = BrainRepoConfig.query.filter_by(user_id=user_id).first()
            return cfg is not None and bool(cfg.cancel_requested)

    _ignore = build_ignore_callback(workspace, cancel_check=_cancel_probe)

    for watch in _WATCH_PATHS:
        _check_cancel(flask_app, user_id)
        src = workspace / watch
        if not src.is_dir():
            continue
        dst = brain_dir / watch
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore)
            for _ in dst.rglob("*"):
                files_copied += 1
        except Exception as exc:
            log.warning("job_runner mirror: copy %s failed: %s", src, exc)

    # Secrets scan — drop any offending file before commit.
    _check_cancel(flask_app, user_id)
    try:
        from brain_repo import secrets_scanner  # type: ignore[import]
        findings = secrets_scanner.scan_directory(brain_dir, exclude=[".git"])
        offending = {f["file"] for f in findings}
        for path_str in offending:
            _check_cancel(flask_app, user_id)
            try:
                Path(path_str).unlink(missing_ok=True)
                secrets_removed += 1
                log.warning("job_runner mirror: removed file with secret(s): %s", path_str)
            except Exception as exc:
                log.warning("job_runner mirror: unlink %s failed: %s", path_str, exc)
    except ImportError:
        log.warning("job_runner mirror: secrets_scanner unavailable")

    return files_copied, secrets_removed


def _decrypt_snapshot_token(encrypted: bytes) -> str:
    """Decrypt the token using BRAIN_REPO_MASTER_KEY from env.

    Returns "" on any failure — caller treats empty as hard fail. We don't
    raise here so the pipeline's top-level error message stays user-friendly
    (see run_sync_pipeline's try/except).
    """
    import os
    key = os.environ.get("BRAIN_REPO_MASTER_KEY", "")
    if not key:
        return ""
    try:
        from brain_repo.github_oauth import decrypt_token  # type: ignore[import]
        return decrypt_token(encrypted, key.encode())
    except Exception as exc:
        log.error("job_runner: token decrypt failed: %s", exc)
        return ""


# ────────────────────────────────────────────────────────────────────────
# Top-level pipelines
# ────────────────────────────────────────────────────────────────────────

def run_sync_pipeline(
    flask_app,
    user_id: int,
    workspace: Path,
    *,
    kind: str,
    tag_name: str | None = None,
    commit_message: str | None = None,
) -> None:
    """Mirror → commit → (tag) → push. Blocks on _job_lock.

    Called from a daemon thread. Never raises — all errors funnel into
    _release_db_lock(error=...) so the UI gets a status and the lock
    always releases.
    """
    with _job_lock:
        # The DB lock is already set by enqueue_sync before the thread
        # starts; this block only runs the pipeline.
        error: str | None = None
        success = False
        try:
            snap = _load_config_snapshot(flask_app, user_id)
            if snap is None:
                error = "Brain repo not connected"
                return

            local_path = snap["local_path"]
            if not local_path:
                error = "local_path not configured — repo not yet cloned"
                return

            repo_dir = Path(local_path)
            if not repo_dir.is_dir() or not (repo_dir / ".git").is_dir():
                error = f"Local brain repo at {local_path} is missing or corrupt — re-connect"
                return

            token = _decrypt_snapshot_token(snap["encrypted_token"])
            if not token:
                error = "Could not decrypt stored token — re-connect the brain repo"
                return

            from brain_repo import git_ops  # type: ignore[import]

            _check_cancel(flask_app, user_id)
            copied, dropped = _mirror_workspace(flask_app, user_id, workspace, repo_dir)
            log.info(
                "job_runner %s: mirrored %d files, removed %d with secrets",
                kind, copied, dropped,
            )

            _check_cancel(flask_app, user_id)
            msg = commit_message or f"auto: {kind} {datetime.now(timezone.utc).isoformat()}"
            git_ops.commit_all(repo_dir, msg)

            if tag_name:
                _check_cancel(flask_app, user_id)
                git_ops.create_tag(
                    repo_dir, tag_name,
                    f"{kind.capitalize()}: {tag_name} ({datetime.now(timezone.utc).isoformat()})",
                    force=True,
                )

            # git push is the point of no return — don't check cancel inside
            # the subprocess; truncating a push corrupts the remote.
            _check_cancel(flask_app, user_id)
            ok, push_err = git_ops.push(repo_dir, token, with_tags=True)
            if not ok:
                error = f"git push failed: {push_err}"
                return

            success = True
        except JobCancelled:
            error = "cancelled by user"
            log.info("job_runner %s: cancelled by user (user_id=%s)", kind, user_id)
        except Exception as exc:
            error = str(exc)
            log.exception("job_runner %s raised unexpectedly", kind)
        finally:
            _release_db_lock(flask_app, user_id, success=success, error=error)


def run_bootstrap_pipeline(
    flask_app,
    user_id: int,
    *,
    token: str,
    repo_url: str,
    repo_name: str,
    owner_username: str,
    github_username: str,
) -> None:
    """Bootstrap a freshly-created empty GitHub repo with the skeleton.

    Runs the same logic as routes.brain_repo._initialize_remote_brain_repo
    but inside the job_runner lock so it serializes with sync operations,
    and persists the final local_path into BrainRepoConfig so the UI stops
    showing "initializing…".
    """
    import subprocess
    from models import BrainRepoConfig, db  # type: ignore[import]

    with _job_lock:
        error: str | None = None
        local_path_str: str | None = None
        try:
            workspace = Path(__file__).resolve().parent.parent.parent.parent
            base_dir = workspace / "dashboard" / "data" / "brain-repos"
            base_dir.mkdir(parents=True, exist_ok=True)
            local_path = base_dir / repo_name

            if local_path.exists():
                shutil.rmtree(local_path, ignore_errors=True)

            from brain_repo import git_ops, manifest  # type: ignore[import]

            local_path.mkdir(parents=True, exist_ok=True)

            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=local_path, check=True, capture_output=True, timeout=30,
            )
            _check_cancel(flask_app, user_id)

            # Token-embedded remote for the bootstrap push. A later follow-up
            # should move this to `git credential helper` so the PAT never
            # hits .git/config, but that's a separate change.
            if "://" in repo_url:
                scheme, rest = repo_url.split("://", 1)
                auth_url = f"{scheme}://{token}@{rest}"
            else:
                auth_url = repo_url
            subprocess.run(
                ["git", "remote", "add", "origin", auth_url],
                cwd=local_path, check=True, capture_output=True, timeout=30,
            )
            _check_cancel(flask_app, user_id)

            manifest.initialize_brain_repo(local_path, {
                "workspace_name": owner_username or "",
                "owner_username": owner_username or "",
                "github_username": github_username or "",
            })

            author_name = github_username or owner_username or "EvoNexus"
            author_email = (
                f"{github_username}@users.noreply.github.com"
                if github_username else "evonexus@users.noreply.github.com"
            )
            subprocess.run(
                ["git", "config", "user.name", author_name],
                cwd=local_path, check=True, capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "config", "user.email", author_email],
                cwd=local_path, check=True, capture_output=True, timeout=10,
            )
            _check_cancel(flask_app, user_id)

            committed = git_ops.commit_all(local_path, "feat(brain-repo): initial structure")
            if committed:
                _check_cancel(flask_app, user_id)
                pushed, push_err = git_ops.push(local_path, token, with_tags=False)
                if not pushed:
                    log.warning("bootstrap push failed for %s: %s", repo_name, push_err)
                    error = f"bootstrap push failed: {push_err}"
                    return

            local_path_str = str(local_path)
            # Persist local_path — this is the signal the UI uses to know
            # bootstrap is done (null = initializing, set = ready).
            with flask_app.app_context():
                config = BrainRepoConfig.query.filter_by(user_id=user_id).first()
                if config is not None:
                    config.local_path = local_path_str
                    db.session.commit()

        except JobCancelled:
            error = "cancelled by user"
        except Exception as exc:
            error = f"bootstrap failed: {exc}"
            log.exception("bootstrap pipeline raised")
        finally:
            _release_db_lock(
                flask_app, user_id,
                success=error is None,
                error=error,
            )


# ────────────────────────────────────────────────────────────────────────
# Public enqueue API — what route handlers call.
# ────────────────────────────────────────────────────────────────────────

def enqueue_sync(
    flask_app,
    user_id: int,
    workspace: Path,
    *,
    kind: str,
    tag_name: str | None = None,
    commit_message: str | None = None,
) -> bool:
    """Spawn a daemon thread running run_sync_pipeline. Returns False if busy."""
    if not _acquire_db_lock(flask_app, user_id, kind):
        return False

    t = threading.Thread(
        target=run_sync_pipeline,
        args=(flask_app, user_id, workspace),
        kwargs={"kind": kind, "tag_name": tag_name, "commit_message": commit_message},
        name=f"brain-repo-{kind}-{user_id}",
        daemon=True,
    )
    t.start()
    return True


def enqueue_bootstrap(
    flask_app,
    user_id: int,
    *,
    token: str,
    repo_url: str,
    repo_name: str,
    owner_username: str,
    github_username: str,
) -> bool:
    """Spawn daemon thread running run_bootstrap_pipeline. Returns False if busy."""
    if not _acquire_db_lock(flask_app, user_id, JOB_KIND_BOOTSTRAP):
        return False

    t = threading.Thread(
        target=run_bootstrap_pipeline,
        args=(flask_app, user_id),
        kwargs={
            "token": token,
            "repo_url": repo_url,
            "repo_name": repo_name,
            "owner_username": owner_username,
            "github_username": github_username,
        },
        name=f"brain-repo-bootstrap-{user_id}",
        daemon=True,
    )
    t.start()
    return True


def request_cancel(flask_app, user_id: int) -> bool:
    """Set cancel_requested=1 on the active job. No-op if idle. Returns True if a job was flagged."""
    from models import BrainRepoConfig, db  # type: ignore[import]

    with flask_app.app_context():
        rows = (
            BrainRepoConfig.query
            .filter_by(user_id=user_id, sync_in_progress=True)
            .update({"cancel_requested": True}, synchronize_session=False)
        )
        db.session.commit()
        return rows == 1


def reclaim_stale_locks(flask_app) -> int:
    """Release sync_in_progress rows older than JOB_STALE_SECONDS.

    Called by the janitor thread (see janitor.py). Returns the count of
    reclaimed locks. Uses a raw datetime comparison to avoid the "what
    timezone does SQLAlchemy think this is" rabbit hole.
    """
    from datetime import timedelta
    from models import BrainRepoConfig, db  # type: ignore[import]

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=JOB_STALE_SECONDS)
    with flask_app.app_context():
        stale = (
            BrainRepoConfig.query
            .filter(
                BrainRepoConfig.sync_in_progress == True,  # noqa: E712
                BrainRepoConfig.sync_started_at < cutoff,
            )
            .all()
        )
        count = 0
        for config in stale:
            config.sync_in_progress = False
            config.sync_started_at = None
            config.sync_job_kind = None
            config.cancel_requested = False
            config.last_error = (
                f"job exceeded {JOB_STALE_SECONDS}s and was reclaimed by janitor"
            )
            count += 1
        if count:
            db.session.commit()
            log.warning("job_runner janitor reclaimed %d stale lock(s)", count)
        return count
