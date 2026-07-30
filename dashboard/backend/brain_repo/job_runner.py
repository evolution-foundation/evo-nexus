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

import fnmatch
import logging
import os
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

# Credential caches written by int-* skills INSIDE watched paths (OAuth token
# stores, session files). These must never even be COPIED into the brain repo —
# relying on the secrets_scanner to unlink them post-copy is defense in depth,
# not the first line, and it only works when the scanner's content patterns
# match (observed: one skill's OAuth cache was flagged and stripped every
# cycle, while another skill's token store — same family, no matching
# pattern — was committed to the remote).
#
# Deliberately NOT part of _is_policy_excluded: policy-excluded paths are
# protected from the deletion-reconciliation pass ("never a mirror candidate,
# so don't delete from dst"), but for credential caches deletion from dst is
# exactly what we want — by being skipped at copy time yet NOT policy-excluded,
# any stale copy already in the brain repo gets purged by _reconcile_deletions.
#
# Basenames only, kept narrow: broad globs like `token*.json` would swallow
# legitimate content (e.g. design `tokens.json`).
_CREDENTIAL_CACHE_BASENAME_PATTERNS = (
    ".token_cache*",   # OAuth token caches written by integration skills
    ".gsc-token*",     # OAuth token stores written by integration skills
    ".credentials*",   # generic credential stores
)


def _is_credential_cache(name: str) -> bool:
    """True if `name` (a basename) is a skill credential cache — never mirrored,
    and actively purged from the brain repo if a stale copy exists there."""
    return any(fnmatch.fnmatch(name, pat) for pat in _CREDENTIAL_CACHE_BASENAME_PATTERNS)


def _is_policy_excluded(rel: str, is_dir: bool, size_getter=None) -> bool:
    """True if `rel` (workspace-relative POSIX path) is never mirrored by policy.

    Shared by the copytree ignore callback (deciding what NOT to copy) and by
    the post-copy reconciliation pass (deciding what's safe to delete from the
    brain repo). A path that's policy-excluded must never be deleted from the
    destination just because it wasn't copied this round — it may simply be
    outside the mirror's mandate (e.g. an oversized file, a nested .git), not
    something the source actually removed.
    """
    name = rel.rsplit("/", 1)[-1]
    if name == ".gitignore":
        return True
    for excl in _EXCLUDE_RELATIVE_PATHS:
        if rel == excl or rel.startswith(excl + "/"):
            return True
    parts = rel.split("/")
    # Excluded dir names anywhere in the ancestry (not just the leaf) — a
    # directory itself matching _EXCLUDE_DIR_NAMES, or any path nested under one.
    excluded_ancestor = parts if is_dir else parts[:-1]
    if any(p in _EXCLUDE_DIR_NAMES for p in excluded_ancestor):
        return True
    if not is_dir and size_getter is not None:
        try:
            if size_getter() > _MAX_FILE_BYTES:
                return True
        except OSError:
            pass
    return False


def build_ignore_callback(
    workspace: Path,
    cancel_check: "callable | None" = None,
    record_kept: "set[str] | None" = None,
    cancel_state: "dict | None" = None,
):
    """Return a shutil.copytree ignore callback wired to the exclusion rules.

    When ``cancel_check`` is provided, it's called once per visited directory;
    if it returns True, the callback ignores EVERY name in that directory and
    every subsequent directory — effectively short-circuiting copytree from
    inside its own walk. That's the only cooperative cancel mechanism we have
    against shutil.copytree, which doesn't expose a per-entry hook.

    When ``record_kept`` is provided, every name NOT ignored (i.e. actually
    copied/recursed into) has its workspace-relative POSIX path added to it.
    The mirror's deletion-reconciliation pass uses this to know exactly what
    was copied this round.

    When ``cancel_state`` is provided, it's the caller-owned dict holding the
    ``"flag"`` the closure flips on cancel. The caller NEEDS to see that flag:
    a cancel short-circuits the walk, so ``record_kept`` ends up PARTIAL, and
    reconciling deletions against a partial "copied" set would read every
    not-yet-visited file as "vanished from source" and erase it from the
    backup. Without this the cancel is invisible from outside — copytree
    returns normally either way.
    """
    workspace_root = workspace.resolve()
    # Mutable flag the closure shares so once a cancel fires we keep returning
    # "ignore all names" for every remaining directory without asking again.
    cancelled = cancel_state if cancel_state is not None else {"flag": False}
    cancelled.setdefault("flag", False)

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
        # Resolve the DIRECTORY (handles a symlinked workspace root) but never
        # the leaf: resolving a symlinked file rewrites rel to the TARGET's path,
        # so the link's own path never lands in `copied` and the reconciliation
        # pass then reads it as "vanished from source" and deletes it from the
        # backup. Caught in production on a symlinked pointer file inside a
        # watched path: the mirror staged a delete for a file that exists on
        # disk in every location.
        try:
            rel_dir = src_dir_path.resolve().relative_to(workspace_root)
        except Exception:
            return list(names)
        for n in names:
            # Credential caches: skipped at copy time but NOT policy-excluded,
            # so _reconcile_deletions purges any stale copy from the brain repo
            # (see _CREDENTIAL_CACHE_BASENAME_PATTERNS for why).
            if _is_credential_cache(n):
                ignored.append(n)
                continue
            full = src_dir_path / n
            rel = (rel_dir / n).as_posix()
            is_dir = full.is_dir()
            if _is_policy_excluded(
                rel, is_dir,
                size_getter=(lambda f=full: f.stat().st_size) if not is_dir else None,
            ):
                ignored.append(n)
                continue
            if record_kept is not None:
                record_kept.add(rel)
        return ignored

    return _ignore


# Mass-deletion circuit breaker. A reconcile pass that would erase most of a
# watch path's backup is refused: at that scale the likeliest cause is the
# SOURCE being unavailable (unmounted volume, half-finished checkout, a mount
# that silently resolved to an empty dir), not someone deleting everything on
# purpose. Both thresholds must be exceeded, so ordinary cleanups still
# propagate — deleting the single file in a small directory is 100% of it, and
# must not be mistaken for a catastrophe.
#
# This replaces an earlier guard that skipped a watch path whose source
# directory was entirely absent. That guard was half a protection: it caught
# the "path vanished" shape but not the "path is there and empty" shape, which
# is what an unmounted volume usually looks like — and it left anything already
# in the backup orphaned forever, since no later pass would ever reconsider it.
_MASS_DELETION_MIN_FILES = 50
_MASS_DELETION_MAX_FRACTION = 0.5


def _is_mass_deletion(doomed: int, backup_files: int) -> bool:
    """True if deleting `doomed` of `backup_files` is too big to do unattended."""
    if doomed < _MASS_DELETION_MIN_FILES:
        return False
    if backup_files <= 0:
        return False
    return (doomed / backup_files) >= _MASS_DELETION_MAX_FRACTION


def _source_may_still_exist(src_file: Path) -> bool:
    """True unless we can POSITIVELY establish the source file is gone.

    Deliberately biased: any uncertainty answers True, because the caller uses
    this to authorise deleting backup content. A false "gone" destroys data; a
    false "present" merely leaves a stale file for the next round.

    ``Path.exists()`` is wrong here twice over. It follows symlinks, so a
    BROKEN symlink — a file that plainly exists on disk — reports False and
    would be erased from the backup (the mirror already lost a live symlink
    to this class of mistake once, on a symlinked pointer file). And
    it swallows OSError, reporting False for "unreachable" as readily as for
    "absent". So we use ``lstat`` and treat ONLY FileNotFoundError as proof
    of deletion; every other OSError means we cannot tell.

    A directory at mode 0000 defeats even that — the lstat fails with EACCES
    for every child. Hence the second question: is the parent directory there
    but unreadable? Then we cannot tell either, and must not delete.
    """
    try:
        src_file.lstat()
        return True
    except FileNotFoundError:
        pass
    except OSError:
        return True

    parent = src_file.parent
    try:
        if parent.is_dir() and not os.access(parent, os.R_OK | os.X_OK):
            return True
    except OSError:
        return True
    return False


def _reconcile_deletions(
    workspace: Path, brain_dir: Path, watch: str, copied: set[str],
) -> int:
    """Remove files from dst that vanished from src, honouring exclusions.

    Runs after copytree for a single watched dir. A dst file is deleted only
    if (a) it's under this watch path, (b) it was NOT copied this round, and
    (c) it's not policy-excluded (_is_policy_excluded) — an excluded path was
    never a candidate for mirroring in the first place, so its absence from
    `copied` says nothing about whether the source still has it.

    Condition (c) used to be the load-bearing one for oversized files: a file
    mirrored back when it was small, then grown past the cap, is skipped by
    ``_ignore`` now and would look "deleted". That case is subsumed by (b) —
    the file is still on disk, so ``_source_may_still_exist`` keeps it — and
    the size probe that implemented it was removed, because ``Path.is_file()``
    raises rather than swallowing EACCES and took the whole mirror down on an
    unreadable path.

    Directories left empty by file removal are pruned in a second, bottom-up
    pass (git doesn't track empty dirs anyway).
    """
    dst_root = brain_dir / watch
    if not dst_root.is_dir():
        return 0

    # Two passes: decide everything first, THEN unlink. The mass-deletion
    # circuit breaker below can only weigh a batch it can see whole, and a
    # breaker that trips halfway through the unlinking has already destroyed
    # half of what it exists to protect.
    doomed: list[Path] = []
    backup_files = 0
    for path in dst_root.rglob("*"):
        if not path.is_file():
            continue
        backup_files += 1
        try:
            rel = path.relative_to(brain_dir).as_posix()
        except ValueError:
            continue
        if rel in copied:
            continue
        src_file = workspace / rel
        # `copied` is only a PROXY for "still present at source" — it is built
        # by the ignore callback, which never runs for a subtree whose
        # directory couldn't be scanned. shutil._copytree appends that failure
        # to its error list and moves on (CPython 3.11 shutil.py, the
        # `except OSError` inside the entry loop), so the walk "completes"
        # with a silent hole: every file under an unreadable directory is
        # missing from `copied` while still existing on disk.
        #
        # So before deleting, ask the SOURCE directly. Absence from `copied`
        # alone must never authorise erasing backup content — the only case
        # where a still-present source file is deliberately purged from the
        # backup is a credential cache, which is skipped at copy time
        # precisely so this pass removes any stale copy.
        if _source_may_still_exist(src_file) and not _is_credential_cache(path.name):
            continue
        # Reached only when the source is confirmed GONE (or is a credential
        # cache we purge on purpose), so the size-based exclusion can't apply
        # and no size probe is needed. That matters: `Path.is_file()` does NOT
        # swallow EACCES (pathlib's _ignore_error covers ENOENT/ENOTDIR/EBADF/
        # ELOOP only), so probing an unreadable path here raised
        # PermissionError straight out of the mirror.
        if _is_policy_excluded(rel, False):
            continue
        doomed.append(path)

    if _is_mass_deletion(len(doomed), backup_files):
        log.error(
            "job_runner mirror: REFUSING to reconcile %s — %d of %d backed-up "
            "file(s) (%.0f%%) look deleted at source. A whole watch path going "
            "missing at once is far more often an unmounted volume, a failed "
            "mount or an interrupted checkout than a real deletion, and the "
            "backup is the only copy. Nothing was removed. If the removal is "
            "genuine, purge it deliberately from the brain repo.",
            watch, len(doomed), backup_files,
            (100.0 * len(doomed) / backup_files) if backup_files else 0.0,
        )
        return 0

    removed = 0
    for path in doomed:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            log.warning("job_runner mirror: reconcile unlink %s failed: %s", path, exc)

    # Second pass: prune directories left empty by the removals above.
    # Deepest-first so parents become eligible after their children are gone.
    for path in sorted(dst_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            rel = path.relative_to(brain_dir).as_posix()
        except ValueError:
            continue
        if _is_policy_excluded(rel, True):
            continue
        try:
            path.rmdir()  # only succeeds if empty — safe no-op otherwise
        except OSError:
            pass

    return removed


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

    ``shutil.copytree(..., dirs_exist_ok=True)`` only ever adds/overwrites —
    it never removes a dst file whose source counterpart was deleted, so a
    plain copytree-based mirror only grows over time. After each watch dir's
    copytree, ``_reconcile_deletions`` removes dst files that vanished from
    src, while never touching anything ``_is_policy_excluded`` (oversized
    files, nested .git/node_modules/etc., .gitignore) — those were never
    mirrored in the first place, so their absence from this round's "copied"
    set says nothing about whether the source still has them.
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

    files_deleted = 0

    for watch in _WATCH_PATHS:
        _check_cancel(flask_app, user_id)
        src = workspace / watch
        dst = brain_dir / watch
        if not src.is_dir():
            # Source watch path is gone (unmounted disk, typo, accidental rm).
            # Mirroring that as "delete the whole backup for this path" would
            # turn a transient/local mistake into permanent backup loss — so we
            # deliberately do NOT touch dst here, only warn.
            #
            # The cost is accepted knowingly: content already mirrored under a
            # watch path this install stops using stays in the backup forever,
            # because no later pass reconsiders it. Reconciling here under the
            # mass-deletion breaker was tried and REJECTED — the breaker needs a
            # file count to judge, and a small watch path (one or two files) is
            # under any sane threshold, so a momentarily unavailable mount would
            # silently take its backup with it. There is no local signal that
            # separates "this path is unused" from "this path is unavailable
            # right now", and for a backup the ambiguous answer must be "keep".
            # Purging such residue is a deliberate, operator-driven action.
            if dst.is_dir():
                log.warning(
                    "job_runner mirror: source watch path %s is missing but "
                    "brain repo still has content at %s — leaving it untouched",
                    src, dst,
                )
            continue
        copied_this_watch: set[str] = set()
        cancel_state: dict = {"flag": False}
        _ignore = build_ignore_callback(
            workspace, cancel_check=_cancel_probe, record_kept=copied_this_watch,
            cancel_state=cancel_state,
        )
        # Reconciliation is only sound when the walk visited the WHOLE tree —
        # otherwise `copied_this_watch` is partial and every unvisited file
        # looks "deleted at source". Tracked separately from the copy result
        # because the two failure shapes differ (see below).
        walk_complete = False
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore)
            walk_complete = True
        except shutil.Error as exc:
            # copytree accumulates PER-FILE errors and raises once at the end,
            # so the top-level walk DID finish and reconciliation can run.
            # Note the walk finishing is NOT the same as `copied_this_watch`
            # being complete: a subdirectory that fails to scan is recorded as
            # an error and skipped, leaving its whole subtree out of `copied`
            # (see _reconcile_deletions). Soundness here rests on asking the
            # SOURCE whether each file is gone, not on `copied`.
            #
            # Keeping reconciliation inside this except's try meant ONE
            # unreadable file anywhere under a watch path silently disabled
            # deletion propagation for that ENTIRE path. Measured in a
            # containerized deployment: a couple dozen files at mode 0600
            # owned by root, unreadable by the container's unprivileged uid,
            # kept already-deleted files alive in the backup indefinitely
            # while the log showed only a warning.
            walk_complete = True
            log.warning(
                "job_runner mirror: %d file(s) failed to copy under %s "
                "(walk completed — reconciliation still runs): %s",
                len(exc.args[0]) if exc.args and isinstance(exc.args[0], list) else 1,
                src, exc,
            )
        except Exception as exc:
            # Walk aborted early (unreadable root, disk error, …) — `copied`
            # is untrustworthy, so skip reconciliation rather than risk
            # deleting live backup content.
            log.warning("job_runner mirror: copy %s failed: %s", src, exc)

        if walk_complete:
            for _ in dst.rglob("*"):
                files_copied += 1
            if cancel_state["flag"]:
                log.warning(
                    "job_runner mirror: cancel fired mid-walk under %s — skipping "
                    "deletion reconciliation (copied set is partial)", src,
                )
            else:
                files_deleted += _reconcile_deletions(
                    workspace, brain_dir, watch, copied_this_watch,
                )

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

    if files_deleted:
        log.info("job_runner mirror: reconciled %d deletion(s) from source", files_deleted)

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
