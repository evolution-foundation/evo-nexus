"""Brain repo management endpoints."""

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, request, jsonify, abort, Response, stream_with_context
from flask_login import login_required, current_user
from werkzeug.exceptions import HTTPException
from models import db, BrainRepoConfig

log = logging.getLogger(__name__)

bp = Blueprint("brain_repo", __name__)


@bp.errorhandler(HTTPException)
def _http_exception_to_json(exc: HTTPException):
    """Convert all abort() calls inside this blueprint into JSON responses.

    Flask's default for ``abort(400, description="...")`` is HTML — the description
    is buried inside an HTML body that the SPA can't usefully parse. Returning
    JSON lets ``lib/api.ts`` surface ``data.error`` to the user verbatim
    (e.g. "Failed to create repo: name already exists on this account").
    """
    return jsonify({"error": exc.description, "code": exc.code}), exc.code or 500


def _get_master_key() -> bytes | None:
    """Return the Fernet master key from env, or None if missing/empty.

    Callers must handle a None return gracefully — ``Fernet(b"")`` would
    crash, so we surface the absence explicitly instead. ``app.py`` is
    supposed to auto-generate ``BRAIN_REPO_MASTER_KEY`` at startup, so a
    None here means that bootstrap did not run (e.g. tests, bare imports).
    """
    key = os.environ.get("BRAIN_REPO_MASTER_KEY", "")
    if not key:
        return None
    return key.encode()


def _get_config() -> BrainRepoConfig | None:
    return BrainRepoConfig.query.filter_by(user_id=current_user.id).first()


# _WATCH_PATHS is also declared in brain_repo.job_runner; both must stay in sync.
# Left here because watcher.py imports it indirectly via _sync_workspace_to_brain_repo's
# module scope in older builds — safe to remove once we confirm no call sites rely on it.
_WATCH_PATHS = ["memory", "workspace", "customizations", "config-safe"]


def _sync_workspace_to_brain_repo(workspace: Path, brain_dir: Path) -> tuple[int, int]:
    """Mirror watched workspace folders into the local brain repo working tree.

    Delegates to :func:`brain_repo.job_runner.build_ignore_callback` for the
    exclusion rules so the mirror and the job-runner pipeline stay in sync.
    Used by the file watcher's auto-sync callback; the manual /sync/force and
    /tag/milestone paths go through the job runner directly.

    Returns ``(files_copied, secrets_removed)``.
    """
    import shutil

    files_copied = 0
    try:
        from brain_repo.job_runner import build_ignore_callback
        _ignore = build_ignore_callback(workspace)
    except ImportError:
        # job_runner unavailable — fall back to "ignore nothing" (old behaviour).
        def _ignore(src_dir: str, names: list[str]) -> list[str]:
            return []

    for watch in _WATCH_PATHS:
        src = workspace / watch
        if not src.is_dir():
            continue
        dst = brain_dir / watch
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore)
            for _ in dst.rglob("*"):
                files_copied += 1
        except Exception as exc:
            log.warning("sync_workspace: failed to copy %s -> %s: %s", src, dst, exc)

    # Secrets scan after copy — drop any offending file from the brain_dir
    # before the commit. Never push a secret.
    secrets_removed = 0
    try:
        from brain_repo import secrets_scanner
        findings = secrets_scanner.scan_directory(brain_dir, exclude=[".git"])
        offending = {f["file"] for f in findings}
        for path_str in offending:
            try:
                Path(path_str).unlink(missing_ok=True)
                secrets_removed += 1
                log.warning("sync_workspace: removed file with secret(s): %s", path_str)
            except Exception as exc:
                log.warning("sync_workspace: could not remove %s: %s", path_str, exc)
    except ImportError:
        log.warning("sync_workspace: secrets_scanner unavailable, skipping scan")

    return files_copied, secrets_removed


def _initialize_remote_brain_repo(
    token: str,
    repo_url: str,
    repo_name: str,
    owner_username: str,
    github_username: str,
) -> str | None:
    """Bootstrap a freshly-created (empty) GitHub repo with the brain-repo skeleton.

    Steps:
        1. Create a local working copy under
           ``<WORKSPACE>/dashboard/data/brain-repos/<repo_name>``
        2. ``git init`` + ``remote add origin`` (token-embedded URL)
        3. Call ``manifest.initialize_brain_repo`` to drop in the directory
           structure, ``.evo-brain`` marker, ``manifest.yaml``, README, and
           ``.gitignore``
        4. Configure git author (commits show as the GitHub user)
        5. Commit everything and push to ``origin/main``

    Returns the local path on success, or None on failure (callers should not
    abort the connect — the GitHub repo exists, only the bootstrap commit is
    missing, and a future sync will repopulate it).
    """
    import subprocess

    workspace = Path(__file__).resolve().parent.parent.parent.parent
    base_dir = workspace / "dashboard" / "data" / "brain-repos"
    base_dir.mkdir(parents=True, exist_ok=True)
    local_path = base_dir / repo_name

    # Wipe stale clone if present (re-connect after a disconnect)
    if local_path.exists():
        import shutil
        shutil.rmtree(local_path, ignore_errors=True)

    try:
        from brain_repo import git_ops, manifest
    except ImportError as exc:
        log.warning("brain_repo helpers unavailable, skipping bootstrap: %s", exc)
        return None

    try:
        local_path.mkdir(parents=True, exist_ok=True)

        # We use ``git init`` + ``remote add`` rather than ``git clone`` because
        # the remote is empty and ``clone`` of an empty repo emits warnings and
        # leaves an unhelpful state.
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=local_path, check=True, capture_output=True, timeout=30,
        )
        # Token-embedded auth URL — never logged
        if "://" in repo_url:
            scheme, rest = repo_url.split("://", 1)
            auth_url = f"{scheme}://{token}@{rest}"
        else:
            auth_url = repo_url
        subprocess.run(
            ["git", "remote", "add", "origin", auth_url],
            cwd=local_path, check=True, capture_output=True, timeout=30,
        )

        # Drop in the brain-repo skeleton (.evo-brain marker, manifest.yaml, dirs)
        manifest.initialize_brain_repo(local_path, {
            "workspace_name": owner_username or "",
            "owner_username": owner_username or "",
            "github_username": github_username or "",
        })

        # Commit author — use the GitHub username so the commit attributes correctly
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

        committed = git_ops.commit_all(local_path, "feat(brain-repo): initial structure")
        if committed:
            pushed, push_err = git_ops.push(local_path, token, with_tags=False)
            if not pushed:
                log.warning("brain repo initial push failed for %s: %s", repo_name, push_err)
        return str(local_path)
    except Exception as exc:
        log.warning("Failed to bootstrap brain repo %s: %s", repo_name, exc)
        return None


def _decrypt_token(config: BrainRepoConfig) -> str:
    """Decrypt the stored GitHub PAT.

    Returns empty string when decryption is not possible — the caller is
    expected to treat an empty token as a hard failure (every current caller
    does ``if not token: abort(...)``).

    SECURITY: previous versions fell back to ``blob.decode("utf-8")`` when
    cryptography was unavailable, which returned the stored blob verbatim as
    if it were the plaintext token. Combined with the matching encrypt-side
    fallback, that meant a failed bootstrap silently downgraded the whole
    feature to plaintext-at-rest without the user noticing. Now every failure
    path logs at ERROR/CRITICAL and returns the empty string — so the next
    sync/restore surfaces ``Could not decrypt stored token`` to the user.
    """
    if not config or not config.github_token_encrypted:
        return ""
    master_key = _get_master_key()
    if master_key is None:
        log.error(
            "_decrypt_token: BRAIN_REPO_MASTER_KEY missing — cannot decrypt stored token "
            "(caller will get empty string and should abort)",
        )
        return ""
    try:
        from brain_repo.github_oauth import decrypt_token
    except ImportError as exc:
        log.error("_decrypt_token: cryptography module unavailable (%s)", exc)
        return ""
    try:
        return decrypt_token(config.github_token_encrypted, master_key)
    except Exception as exc:
        log.error(
            "_decrypt_token: Fernet decryption failed (%s) — key mismatch or corrupted blob",
            exc,
        )
        return ""


# ── Status ────────────────────────────────────────────

@bp.route("/api/brain-repo/status")
@login_required
def status():
    """Return current BrainRepoConfig plus crypto readiness.

    ``crypto_ready`` reflects whether the server can actually encrypt/decrypt
    tokens right now — covers the case where a config exists but the master
    key was lost (e.g. after a restart where the .env write failed, or key
    was rotated without re-connecting). When false, the UI renders the
    Brain Repo card with a warning and offers Reconnect instead of pretending
    everything is fine.
    """
    try:
        from brain_repo import is_crypto_ready
        crypto_ready = is_crypto_ready()
    except Exception:
        crypto_ready = False

    config = _get_config()
    if config is None:
        return jsonify({"connected": False, "crypto_ready": crypto_ready})
    return jsonify({**config.to_dict(), "crypto_ready": crypto_ready})


# ── Validate token ────────────────────────────────────

@bp.route("/api/brain-repo/validate-token", methods=["POST"])
@login_required
def validate_token():
    """Validate a GitHub PAT without persisting anything.

    Body (JSON):
        token - GitHub PAT (required)

    Returns on success:
        {"ok": true, "scopes": [...], "username": "..."}
    On invalid token:
        400 {"ok": false, "error": "..."}
    """
    data = request.get_json() or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"ok": False, "error": "token required"}), 400

    try:
        from brain_repo.github_api import validate_pat_scopes, get_github_username
    except ImportError:
        # Graceful fallback — module unavailable in this environment
        return jsonify({"ok": False, "error": "brain_repo.github_api unavailable"}), 400

    try:
        ok, scopes = validate_pat_scopes(token)
    except Exception as exc:
        log.warning("validate_pat_scopes failed: %s", exc)
        return jsonify({"ok": False, "error": f"validation failed: {exc}"}), 400

    if not ok:
        return jsonify({
            "ok": False,
            "error": "GitHub PAT validation failed — check token and ensure 'repo' scope is granted",
            "scopes": scopes,
        }), 400

    try:
        username = get_github_username(token)
    except Exception as exc:
        log.warning("get_github_username failed: %s", exc)
        username = ""

    return jsonify({"ok": True, "scopes": scopes, "username": username})


# ── Connect ───────────────────────────────────────────

@bp.route("/api/brain-repo/connect", methods=["POST"])
@login_required
def connect():
    """Connect (or reconfigure) the brain repo.

    Body (JSON):
        token      - GitHub PAT (required)
        repo_url   - URL of an existing private repo to connect to
        create_repo - Name of a new private repo to create (mutually exclusive with repo_url)
    """
    data = request.get_json() or {}
    token = data.get("token", "").strip()
    repo_url = data.get("repo_url", "").strip()
    create_repo = data.get("create_repo", "").strip()

    if not token:
        abort(400, description="token required")
    if not repo_url and not create_repo:
        abort(400, description="Either repo_url or create_repo is required")

    # Validate PAT scopes
    try:
        from brain_repo.github_api import validate_pat_scopes
        ok, scopes = validate_pat_scopes(token)
    except ImportError:
        ok, scopes = True, ["repo"]  # graceful fallback if module not yet present

    if not ok:
        abort(400, description="GitHub PAT validation failed — check token scopes (needs 'repo')")

    # Create or validate the repo. The bootstrap (clone-init + initialize_brain_repo
    # + first commit + push) moved to job_runner.enqueue_bootstrap below — it's the
    # slow part and Cloudflare's 100 s request limit was killing first-time connects.
    bootstrap_pending = False
    bootstrap_params: dict | None = None
    if create_repo:
        try:
            from brain_repo.github_api import create_private_repo, get_github_username
            repo_info = create_private_repo(token, create_repo)
        except ImportError:
            # Fallback stub
            repo_info = {
                "html_url": f"https://github.com/user/{create_repo}",
                "owner": {"login": "user"},
                "name": create_repo,
            }
        except Exception as exc:
            # Friendlier message for the common case (repo already exists → 422)
            err_str = str(exc)
            if "422" in err_str or "already exists" in err_str.lower():
                abort(400, description=(
                    f"O repositório '{create_repo}' já existe na sua conta. "
                    "Use a opção 'Usar existente' para conectá-lo, "
                    "ou escolha um nome diferente."
                ))
            abort(400, description=f"Falha ao criar repo: {exc}")
        repo_url = repo_info.get("html_url", "")
        repo_owner = repo_info.get("owner", {}).get("login", "")
        repo_name = repo_info.get("name", create_repo)

        # Defer the bootstrap to a background thread. The config row gets persisted
        # below with local_path=NULL so the UI knows the repo is "initializing"
        # (sync_in_progress=1 until bootstrap finishes writing local_path).
        try:
            github_username = get_github_username(token)
        except Exception:
            github_username = repo_owner
        bootstrap_pending = True
        bootstrap_params = {
            "token": token,
            "repo_url": repo_url,
            "repo_name": repo_name,
            "owner_username": current_user.username or repo_owner,
            "github_username": github_username,
        }
    else:
        # Validate existing repo is private
        try:
            from brain_repo.github_api import get_repo_info
            ok_private, repo_info = get_repo_info(token, repo_url)
        except ImportError:
            ok_private, repo_info = True, {}  # graceful fallback

        if not ok_private:
            abort(400, description="Repository must be private")
        repo_owner = repo_info.get("owner", {}).get("login", "")
        repo_name = repo_info.get("name", "")

    # Encrypt and store token.
    #
    # SECURITY: fail loud if crypto isn't available. The previous version fell
    # through to ``encrypted = token.encode("utf-8")`` with only a log.warning
    # — which in production silently stored PATs as plaintext (exact vector
    # flagged in the PR review). Nobody reads warning logs in prod, and the
    # app.py bootstrap that auto-generates the master key can fail silently
    # in several plausible scenarios (read-only filesystem, missing
    # cryptography module, empty env var override, gunicorn worker race).
    # The right answer is to refuse the write and surface the failure to the
    # user, who will then escalate to the admin.
    master_key = _get_master_key()
    if master_key is None:
        log.critical(
            "connect: BRAIN_REPO_MASTER_KEY missing — refusing to store PAT without encryption",
        )
        return jsonify({
            "error": (
                "Token encryption unavailable: BRAIN_REPO_MASTER_KEY is not configured. "
                "Check server .env and startup logs; do not retry until the admin restores the key."
            ),
            "code": "CRYPTO_UNAVAILABLE",
        }), 500
    try:
        from brain_repo.github_oauth import PATAuthProvider
    except ImportError as _exc:
        log.critical("connect: cryptography module unavailable (%s) — refusing to store PAT", _exc)
        return jsonify({
            "error": "Token encryption module unavailable. Check server dependencies.",
            "code": "CRYPTO_UNAVAILABLE",
        }), 500
    try:
        encrypted = PATAuthProvider(token, master_key).encrypt_token()
    except Exception as _exc:
        log.critical("connect: token encryption failed (%s)", _exc)
        return jsonify({
            "error": f"Token encryption failed: {_exc}",
            "code": "CRYPTO_UNAVAILABLE",
        }), 500

    config = _get_config()
    if config is None:
        config = BrainRepoConfig(user_id=current_user.id)
        db.session.add(config)

    config.github_token_encrypted = encrypted
    config.repo_url = repo_url
    config.repo_owner = repo_owner
    config.repo_name = repo_name
    # local_path stays NULL when bootstrap is deferred — it gets filled in by
    # job_runner.run_bootstrap_pipeline after the push succeeds. UI reads
    # sync_in_progress + local_path==null as "initializing".
    config.sync_enabled = True
    config.last_error = None
    db.session.commit()

    # Kick off async bootstrap *after* commit so the enqueue's DB lock update
    # doesn't race with the config insert above.
    if bootstrap_pending and bootstrap_params is not None:
        try:
            from brain_repo import job_runner
            from flask import current_app
            job_runner.enqueue_bootstrap(
                current_app._get_current_object(),  # type: ignore[attr-defined]
                current_user.id,
                **bootstrap_params,
            )
        except ImportError:
            log.error("connect: job_runner unavailable — bootstrap will not run")

    return jsonify(config.to_dict())


# ── Disconnect ────────────────────────────────────────

@bp.route("/api/brain-repo/disconnect", methods=["POST"])
@login_required
def disconnect():
    """Remove stored credentials and disable sync."""
    config = _get_config()
    if config:
        config.github_token_encrypted = None
        config.sync_enabled = False
        db.session.commit()
    return jsonify({"ok": True})


# ── Detect ────────────────────────────────────────────

@bp.route("/api/brain-repo/detect")
@login_required
def detect():
    """Detect candidate brain repos for the authenticated token."""
    token = request.args.get("token", "").strip()
    if not token:
        # Try to use stored token
        config = _get_config()
        if config:
            token = _decrypt_token(config)
    if not token:
        abort(400, description="token required (query param or stored config)")

    try:
        from brain_repo.github_api import detect_brain_repos
        repos = detect_brain_repos(token)
    except ImportError:
        repos = []  # graceful fallback

    return jsonify({"repos": repos})


# ── Snapshots ─────────────────────────────────────────

@bp.route("/api/brain-repo/snapshots")
@login_required
def snapshots():
    """List available restore snapshots (daily / weekly / milestones / head)."""
    config = _get_config()
    if not config or not config.github_token_encrypted:
        abort(400, description="Brain repo not connected")

    token = _decrypt_token(config)
    if not token:
        abort(400, description="Could not decrypt stored token")

    try:
        from brain_repo.github_api import list_snapshots
        result = list_snapshots(token, config.repo_owner, config.repo_name)
    except ImportError:
        result = {"daily": [], "weekly": [], "milestones": [], "head": None}

    return jsonify(result)


# ── Restore (SSE) ─────────────────────────────────────

@bp.route("/api/brain-repo/restore/start", methods=["POST"])
@login_required
def restore_start():
    """Begin a restore operation; streams progress via Server-Sent Events.

    Body (JSON):
        ref         - git ref / tag to restore from (required)
        include_kb  - bool, whether to restore knowledge base (default True)

    SSE event format:
        data: {"step": "<name>", "progress": <0-100>, "message": "...", "error": false}
    """
    data = request.get_json() or {}
    ref = data.get("ref", "").strip()
    include_kb = bool(data.get("include_kb", False))
    # kb_key_matches is declared by the user ("I still have the original
    # master key") — when False, KB import silently degrades to metadata-only,
    # which is the safe default.
    kb_key_matches = bool(data.get("kb_key_matches", False))

    if not ref:
        abort(400, description="ref required")

    config = _get_config()
    if not config or not config.github_token_encrypted:
        abort(400, description="Brain repo not connected")

    token = _decrypt_token(config)
    if not token:
        abort(400, description="Could not decrypt stored token")

    # Capture needed values before entering generator (avoids app context issues)
    repo_url = config.repo_url
    # install_dir is where SWAP_DIRS (memory/workspace/customizations/config-safe)
    # get replaced — i.e. the EvoNexus workspace root, NOT the brain-repo clone
    # path. Confusing these two is what broke the restore endpoint.
    install_dir = Path(__file__).resolve().parent.parent.parent.parent

    def generate():
        try:
            from brain_repo import restore
            for event in restore.execute_restore(
                repo_url=repo_url,
                ref=ref,
                token=token,
                install_dir=install_dir,
                include_kb=include_kb,
                kb_key_matches=kb_key_matches,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except ImportError:
            # Module not yet implemented — yield a single completion event
            yield f"data: {json.dumps({'step': 'done', 'progress': 100, 'message': 'restore module not yet available', 'error': False})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'step': 'error', 'progress': 0, 'message': str(exc), 'error': True})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Sync (force) ──────────────────────────────────────

@bp.route("/api/brain-repo/sync/force", methods=["POST"])
@login_required
def sync_force():
    """Enqueue a workspace → brain-repo sync (async — 202 Accepted).

    The actual work (mirror → commit → tag → push) runs in a daemon thread
    behind brain_repo.job_runner's global lock. On a VPS behind Cloudflare
    the synchronous version routinely exceeded the 100 s request limit on
    first-time syncs or slow uplinks. The frontend polls
    ``/api/brain-repo/status`` for ``sync_in_progress=False`` + ``last_sync``
    to detect completion.

    Returns:
        202 Accepted ``{"ok": true, "status": "queued", "tag": <name>}`` on
        successful enqueue.
        409 Conflict ``{"error": "...", "code": "SYNC_IN_PROGRESS"}`` when
        another job is already running for this user.
    """
    config = _get_config()
    if not config or not config.github_token_encrypted:
        abort(400, description="Brain repo not connected")

    local_path = config.local_path
    if not local_path:
        abort(400, description="local_path not configured — repo not yet cloned")

    repo_dir = Path(local_path)
    if not repo_dir.is_dir() or not (repo_dir / ".git").is_dir():
        abort(500, description=f"Local brain repo at {local_path} is missing or corrupt — re-connect")

    # Quick token-decryption probe so bad keys fail fast (before enqueueing).
    # The pipeline re-decrypts inside the thread — we just surface the error
    # synchronously here so the UI can react inline.
    token = _decrypt_token(config)
    if not token:
        abort(500, description="Could not decrypt stored token — re-connect the brain repo")

    try:
        from brain_repo import job_runner
    except ImportError:
        abort(500, description="job_runner module unavailable")

    workspace = Path(__file__).resolve().parent.parent.parent.parent
    now = datetime.now(timezone.utc)
    # Seconds in the tag name avoids "tag already exists" on rapid re-clicks
    tag_name = f"milestone/manual-{now.strftime('%Y-%m-%d-%H-%M-%S')}"

    from flask import current_app
    enqueued = job_runner.enqueue_sync(
        current_app._get_current_object(),  # type: ignore[attr-defined]
        current_user.id,
        workspace,
        kind=job_runner.JOB_KIND_SYNC,
        tag_name=tag_name,
        commit_message=f"manual sync {now.isoformat()}",
    )
    if not enqueued:
        return jsonify({
            "ok": False,
            "error": "Another sync is already running for this user.",
            "code": "SYNC_IN_PROGRESS",
        }), 409

    return jsonify({
        "ok": True,
        "status": "queued",
        "tag": tag_name,
    }), 202


# ── Tag milestone ─────────────────────────────────────

@bp.route("/api/brain-repo/tag/milestone", methods=["POST"])
@login_required
def tag_milestone():
    """Enqueue a named milestone snapshot (async — 202 Accepted).

    Body (JSON):
        name - tag suffix, result will be ``milestone/<name>``

    Same pipeline as ``sync_force`` but with a user-supplied tag name. Runs
    async behind the same global lock, so a milestone click while a sync is
    in flight returns 409.
    """
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        abort(400, description="name required")

    config = _get_config()
    if not config or not config.github_token_encrypted:
        abort(400, description="Brain repo not connected")

    local_path = config.local_path
    if not local_path:
        abort(400, description="local_path not configured — repo not yet cloned")

    repo_dir = Path(local_path)
    if not repo_dir.is_dir() or not (repo_dir / ".git").is_dir():
        abort(500, description=f"Local brain repo at {local_path} is missing or corrupt — re-connect")

    token = _decrypt_token(config)
    if not token:
        abort(500, description="Could not decrypt stored token — re-connect the brain repo")

    try:
        from brain_repo import job_runner
    except ImportError:
        abort(500, description="job_runner module unavailable")

    workspace = Path(__file__).resolve().parent.parent.parent.parent
    tag = f"milestone/{name}"
    now = datetime.now(timezone.utc)

    from flask import current_app
    enqueued = job_runner.enqueue_sync(
        current_app._get_current_object(),  # type: ignore[attr-defined]
        current_user.id,
        workspace,
        kind=job_runner.JOB_KIND_MILESTONE,
        tag_name=tag,
        commit_message=f"milestone: {name} ({now.isoformat()})",
    )
    if not enqueued:
        return jsonify({
            "ok": False,
            "error": "Another sync is already running for this user.",
            "code": "SYNC_IN_PROGRESS",
        }), 409

    return jsonify({
        "ok": True,
        "status": "queued",
        "tag": tag,
    }), 202


# ── Sync cancel ───────────────────────────────────────

@bp.route("/api/brain-repo/sync/cancel", methods=["POST"])
@login_required
def sync_cancel():
    """Request cancellation of the active sync/milestone/bootstrap job.

    Cancel is cooperative — the pipeline checks cancel_requested between
    watched directories, between secrets-scan batches, and before each git
    subprocess. A ``git push`` already in flight is NOT interrupted (would
    risk corrupting the remote), so the UI should show "cancelling…
    (awaiting current push)" until ``sync_in_progress`` flips to False.

    Returns ``{"ok": true, "cancel_requested": true}`` whether or not a job
    was actually running — idempotent.
    """
    try:
        from brain_repo import job_runner
    except ImportError:
        abort(500, description="job_runner module unavailable")

    from flask import current_app
    flagged = job_runner.request_cancel(
        current_app._get_current_object(),  # type: ignore[attr-defined]
        current_user.id,
    )
    return jsonify({"ok": True, "cancel_requested": True, "was_running": flagged})
