"""Media Jobs API — social-media-production pipeline orchestration.

Ownership split (see workspace/development/features/social-media-production/
[C]architecture-social-media-production.md):
  - CPU-heavy composition+render+validation (queued/retryable_failure ->
    ready_for_review) runs in the isolated media-worker service, which polls
    this API (`/run` to claim) and reports progress via PATCH — never inside
    this Flask process (Etapa 3: "em vez de executar renderizações dentro
    do processo web do dashboard").
  - I/O-bound Postiz upload/draft/schedule (approved -> draft_created ->
    scheduled) runs directly here — no CPU-bound work, and the render file
    is readable from the shared evonexus_media volume.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request, send_file
from flask_login import current_user

from models import db, MediaJob, has_permission, audit
from media_state_machine import assert_transition, can_transition, InvalidTransition, allowed_targets
from media_workspace import ensure_job_scaffold, new_job_id, media_workspace_root, PathSecurityError
from postiz_client import PostizClient, PostizError, MEDIA_JOB_PLATFORMS, build_platform_settings

bp = Blueprint("media_jobs", __name__)

_WORKER_PATCHABLE_FIELDS = (
    "render_path", "render_sha256", "render_size_bytes", "render_duration_seconds",
    "render_width", "render_height", "render_fps", "render_has_audio", "last_error",
    "title", "brief", "caption",
)


def _require(action: str):
    if not has_permission(current_user.role, "media_jobs", action):
        return jsonify({"error": "Forbidden"}), 403
    return None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _transition(job: MediaJob, target: str) -> None:
    """Single choke point for every status write — no route may set
    `job.status` directly (briefing: "Valide transições").
    """
    assert_transition(job.status, target)
    job.status = target
    job.updated_at = _now()


def _invalid_transition_response(job: MediaJob, target: str):
    return jsonify({
        "error": "invalid_transition",
        "detail": f"MediaJob está em '{job.status}', não pode ir para '{target}'.",
        "current_status": job.status,
        "allowed_from_current": allowed_targets(job.status),
    }), 409


def _localize_to_utc(local_iso: str, tz_name: str) -> str:
    """MEDIA_TIMEZONE input -> normalized ISO-8601 UTC (briefing Etapa 10:
    the user enters America/Bahia; Postiz gets UTC). Never relies on the
    container's implicit local timezone — always resolves via zoneinfo.
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:
        raise ValueError(f"timezone inválido: {tz_name!r}") from exc
    try:
        dt = datetime.fromisoformat(local_iso)
    except ValueError as exc:
        raise ValueError(f"scheduled_at inválido (esperado ISO-8601): {local_iso!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _resolve_platform_settings(job: MediaJob) -> dict:
    if job.platform_settings:
        try:
            data = json.loads(job.platform_settings)
            if isinstance(data, dict) and data.get("__type"):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    if job.platform == "youtube":
        return build_platform_settings("youtube", title=(job.title or "Vídeo")[:100])
    if job.platform in ("instagram", "linkedin", "tiktok"):
        return build_platform_settings(job.platform)
    raise ValueError(f"Plataforma sem builder de settings: {job.platform!r}")


# ── CRUD ─────────────────────────────────────────────────────────────────

@bp.route("/api/media/jobs")
def list_media_jobs():
    denied = _require("view")
    if denied:
        return denied
    query = MediaJob.query
    status = request.args.get("status")
    if status:
        query = query.filter(MediaJob.status == status)
    project_id = request.args.get("project_id", type=int)
    if project_id:
        query = query.filter(MediaJob.project_id == project_id)
    platform = request.args.get("platform")
    if platform:
        query = query.filter(MediaJob.platform == platform)
    limit = min(request.args.get("limit", default=200, type=int) or 200, 500)
    jobs = query.order_by(MediaJob.created_at.desc()).limit(limit).all()
    return jsonify([j.to_dict() for j in jobs])


@bp.route("/api/media/jobs/<string:job_id>")
def get_media_job(job_id):
    denied = _require("view")
    if denied:
        return denied
    job = MediaJob.query.get_or_404(job_id)
    return jsonify(job.to_dict())


@bp.route("/api/media/jobs", methods=["POST"])
def create_media_job():
    denied = _require("execute")
    if denied:
        return denied
    data = request.get_json() or {}

    required = ["title", "platform", "width", "height", "duration_seconds"]
    missing = [f for f in required if data.get(f) in (None, "")]
    if missing:
        return jsonify({"error": f"Campos obrigatórios ausentes: {missing}"}), 400

    platform = data["platform"]
    if platform not in MEDIA_JOB_PLATFORMS:
        return jsonify({"error": f"platform inválido: {platform!r}. Aceita: {list(MEDIA_JOB_PLATFORMS)}"}), 400

    publication_mode = data.get("publication_mode", os.environ.get("SOCIAL_DEFAULT_POST_MODE", "draft"))
    if publication_mode not in ("draft", "schedule"):
        return jsonify({"error": f"publication_mode inválido: {publication_mode!r}"}), 400

    tz_name = data.get("timezone") or os.environ.get("MEDIA_TIMEZONE", "America/Bahia")
    scheduled_at = data.get("scheduled_at")
    scheduled_at_utc = None
    if scheduled_at:
        try:
            scheduled_at_utc = _localize_to_utc(scheduled_at, tz_name)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if publication_mode == "schedule":
            scheduled_dt = datetime.strptime(scheduled_at_utc, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
            if scheduled_dt <= datetime.now(timezone.utc):
                return jsonify({"error": "scheduled_at precisa estar no futuro."}), 400

    platform_settings = data.get("platform_settings")
    if platform_settings is not None and not isinstance(platform_settings, dict):
        return jsonify({"error": "platform_settings deve ser um objeto."}), 400

    job_id = new_job_id()
    now = _now()
    job = MediaJob(
        id=job_id,
        project_id=data.get("project_id"),
        campaign_id=data.get("campaign_id"),
        goal_id=data.get("goal_id"),
        task_id=data.get("task_id"),
        created_by=getattr(current_user, "username", "system"),
        title=data["title"],
        brief=data.get("brief"),
        platform=platform,
        postiz_integration_id=data.get("postiz_integration_id"),
        format=data.get("format", "vertical"),
        width=int(data["width"]),
        height=int(data["height"]),
        fps=int(data.get("fps", 30)),
        duration_seconds=float(data["duration_seconds"]),
        language=data.get("language", "pt-BR"),
        caption=data.get("caption"),
        platform_settings=json.dumps(platform_settings) if platform_settings else None,
        publication_mode=publication_mode,
        scheduled_at=scheduled_at,
        scheduled_at_utc=scheduled_at_utc,
        timezone=tz_name,
        status="queued",
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    db.session.add(job)
    db.session.commit()

    # Scaffold the isolated workspace + write the input manifest OpenCode will read.
    base = ensure_job_scaffold(job_id)
    job.workspace_path = str(base)
    manifest = {
        "job_id": job_id, "title": job.title, "brief": job.brief, "platform": job.platform,
        "format": job.format, "width": job.width, "height": job.height, "fps": job.fps,
        "duration_seconds": job.duration_seconds, "language": job.language, "caption": job.caption,
        "platform_settings": platform_settings or {},
        "project_id": job.project_id, "campaign_id": job.campaign_id,
    }
    (base / "input" / "job.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    db.session.commit()

    audit(current_user, "execute", "media_jobs", f"create media job {job_id} ({platform})")
    return jsonify(job.to_dict()), 201


@bp.route("/api/media/jobs/<string:job_id>", methods=["PATCH"])
def update_media_job(job_id):
    """Worker progress-reporting endpoint. Also used for minor pre-run edits
    (title/brief/caption). Any `status` change goes through the same
    transition matrix every other action uses — the worker cannot bypass it.
    """
    denied = _require("execute")
    if denied:
        return denied
    job = MediaJob.query.get_or_404(job_id)
    data = request.get_json() or {}

    target_status = data.get("status")
    if target_status:
        try:
            assert_transition(job.status, target_status)
        except InvalidTransition as exc:
            return jsonify({
                "error": "invalid_transition", "detail": str(exc),
                "allowed_from_current": allowed_targets(job.status),
            }), 409
        job.status = target_status
        if target_status in ("ready_for_review", "failed", "cancelled"):
            # Worker relinquishes the lock once its portion of the pipeline ends.
            job.locked_at = None
            job.locked_by = None

    for field in _WORKER_PATCHABLE_FIELDS:
        if field in data:
            setattr(job, field, data[field])

    job.updated_at = _now()
    db.session.commit()
    audit(current_user, "execute", "media_jobs", f"update media job {job_id}")
    return jsonify(job.to_dict())


# ── Pipeline actions ─────────────────────────────────────────────────────

@bp.route("/api/media/jobs/<string:job_id>/run", methods=["POST"])
def run_media_job(job_id):
    """Atomic claim + (re)start. Used both by a human clicking "Iniciar" and
    by the media-worker's polling loop. Mirrors routes/tickets.py's
    checkout_ticket: the atomicity comes from `WHERE locked_at IS NULL` in a
    raw UPDATE, not from a read-then-write in Python.
    """
    denied = _require("execute")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    locked_by = data.get("agent") or "media-worker"

    job = MediaJob.query.get_or_404(job_id)

    if job.status == "rejected":
        _transition(job, "queued")
        job.attempt_count = 0
        job.last_error = None
        db.session.commit()

    if job.status not in ("queued", "retryable_failure"):
        return _invalid_transition_response(job, "preparing")

    now = _now()
    result = db.session.execute(
        db.text(
            "UPDATE media_jobs SET locked_at = :now, locked_by = :agent, "
            "status = 'preparing', updated_at = :now, attempt_count = attempt_count + 1 "
            "WHERE id = :id AND locked_at IS NULL AND status IN ('queued','retryable_failure')"
        ),
        {"id": job_id, "agent": locked_by, "now": now},
    )
    db.session.commit()
    if result.rowcount == 0:
        db.session.refresh(job)
        return jsonify({
            "error": "already_locked_or_invalid_state",
            "locked_by": job.locked_by, "status": job.status,
        }), 409

    db.session.refresh(job)
    audit(current_user, "execute", "media_jobs", f"run media job {job_id}")
    return jsonify(job.to_dict())


@bp.route("/api/media/jobs/<string:job_id>/cancel", methods=["POST"])
def cancel_media_job(job_id):
    denied = _require("execute")
    if denied:
        return denied
    job = MediaJob.query.get_or_404(job_id)
    if not can_transition(job.status, "cancelled"):
        return _invalid_transition_response(job, "cancelled")
    _transition(job, "cancelled")
    job.locked_at = None
    job.locked_by = None
    db.session.commit()
    audit(current_user, "execute", "media_jobs", f"cancelled media job {job_id}")
    return jsonify(job.to_dict())


@bp.route("/api/media/jobs/<string:job_id>/approve", methods=["POST"])
def approve_media_job(job_id):
    denied = _require("manage")
    if denied:
        return denied
    job = MediaJob.query.get_or_404(job_id)
    if not can_transition(job.status, "approved"):
        return _invalid_transition_response(job, "approved")
    _transition(job, "approved")
    job.approved_at = _now()
    db.session.commit()
    audit(current_user, "manage", "media_jobs", f"approved media job {job_id}")
    return jsonify(job.to_dict())


@bp.route("/api/media/jobs/<string:job_id>/reject", methods=["POST"])
def reject_media_job(job_id):
    denied = _require("manage")
    if denied:
        return denied
    data = request.get_json() or {}
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "reason (comentário) é obrigatório para rejeitar."}), 400
    job = MediaJob.query.get_or_404(job_id)
    if not can_transition(job.status, "rejected"):
        return _invalid_transition_response(job, "rejected")
    _transition(job, "rejected")
    job.reject_reason = reason
    db.session.commit()
    audit(current_user, "manage", "media_jobs", f"rejected media job {job_id}: {reason}")
    return jsonify(job.to_dict())


@bp.route("/api/media/jobs/<string:job_id>/create-draft", methods=["POST"])
def create_draft_media_job(job_id):
    """Upload the render + create a Postiz draft. I/O-bound (HTTP calls to
    Postiz), so it runs synchronously here rather than in the media-worker —
    per the briefing, rendering must be worker-isolated; upload/draft
    creation is explicitly "backend OU worker".

    Idempotent (ADR-7): re-POSTing a job that already has postiz_media_id/
    postiz_post_id never re-uploads or re-creates — it returns the existing
    state.
    """
    denied = _require("manage")
    if denied:
        return denied
    job = MediaJob.query.get_or_404(job_id)

    if job.status == "draft_created" and job.postiz_post_id:
        return jsonify(job.to_dict())  # already done — idempotent no-op

    if job.status not in ("approved", "retryable_failure", "uploading", "creating_draft"):
        return _invalid_transition_response(job, "uploading")

    if not job.render_path or not Path(job.render_path).is_file():
        return jsonify({"error": "render_missing", "detail": "MediaJob não tem um render válido em disco."}), 400

    client = PostizClient.from_env()
    if client is None:
        return jsonify({"error": "postiz_not_configured", "detail": "POSTIZ_URL/POSTIZ_API_KEY não configurados."}), 400

    if job.status == "approved":
        _transition(job, "uploading")
        db.session.commit()
    elif job.status == "retryable_failure":
        # Resume into whichever create-draft substage makes sense given what
        # already succeeded before the failure — never re-do a completed
        # step (ADR-7 idempotency).
        _transition(job, "creating_draft" if job.postiz_media_id else "uploading")
        db.session.commit()

    try:
        if not job.postiz_media_id:
            uploaded = client.upload_file(Path(job.render_path))
            job.postiz_media_id = uploaded["id"]
            job.postiz_media_path = uploaded["path"]
            job.postiz_media_name = uploaded["name"]
            db.session.commit()  # persist immediately — idempotency checkpoint

        if job.status == "uploading":
            _transition(job, "creating_draft")
            db.session.commit()

        if not job.postiz_post_id:
            integrations = client.list_integrations()
            integration = client.select_integration(job.platform, integrations)
            if not integration:
                raise PostizError(f"Nenhuma integração Postiz ativa e inequívoca para {job.platform!r}.")
            settings = _resolve_platform_settings(job)
            media = [{"id": job.postiz_media_id, "path": job.postiz_media_path}]
            # If this job will be scheduled later, store the real target date now —
            # PUT /posts/{id}/status only *resumes* publishing "at its stored date",
            # it cannot set a new one (confirmed against docs.postiz.com).
            draft_date = job.scheduled_at_utc if (job.publication_mode == "schedule" and job.scheduled_at_utc) else _now()
            created = client.create_draft(
                integration_id=integration["id"], content=job.caption or "", media=media,
                settings=settings, now_iso_utc=draft_date,
            )
            post_ids = [item.get("postId") for item in created if isinstance(item, dict) and item.get("postId")]
            if not post_ids:
                raise PostizError(f"Postiz não retornou postId: {created!r}")
            job.postiz_post_id = post_ids[0]
            db.session.commit()

        _transition(job, "draft_created")
        job.locked_at = None
        job.locked_by = None
        db.session.commit()
    except (PostizError, ValueError) as exc:
        _transition(job, "retryable_failure")
        job.last_error = str(exc)
        db.session.commit()
        return jsonify({"error": "postiz_error", "detail": str(exc)}), 502

    audit(current_user, "manage", "media_jobs", f"created postiz draft for media job {job_id}")
    return jsonify(job.to_dict())


@bp.route("/api/media/jobs/<string:job_id>/schedule", methods=["POST"])
def schedule_media_job(job_id):
    denied = _require("manage")
    if denied:
        return denied
    job = MediaJob.query.get_or_404(job_id)

    if job.publication_mode != "schedule":
        return jsonify({"error": "not_schedule_mode", "detail": "publication_mode do job não é 'schedule'."}), 400
    if not job.scheduled_at_utc:
        return jsonify({"error": "missing_scheduled_at", "detail": "scheduled_at não definido para este job."}), 400
    try:
        scheduled_dt = datetime.strptime(job.scheduled_at_utc, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
    except ValueError:
        return jsonify({"error": "invalid_scheduled_at", "detail": job.scheduled_at_utc}), 400
    if scheduled_dt <= datetime.now(timezone.utc):
        return jsonify({"error": "scheduled_at_not_future", "detail": "scheduled_at_utc precisa estar no futuro."}), 400
    if not job.postiz_post_id:
        return jsonify({"error": "missing_postiz_post_id", "detail": "Job ainda não tem draft criado no Postiz."}), 400
    if not can_transition(job.status, "scheduling"):
        return _invalid_transition_response(job, "scheduling")

    client = PostizClient.from_env()
    if client is None:
        return jsonify({"error": "postiz_not_configured"}), 400

    _transition(job, "scheduling")
    db.session.commit()
    try:
        client.change_status(job.postiz_post_id, "schedule")
    except PostizError as exc:
        _transition(job, "retryable_failure")
        job.last_error = str(exc)
        db.session.commit()
        return jsonify({"error": "postiz_error", "detail": str(exc)}), 502

    _transition(job, "scheduled")
    db.session.commit()
    audit(current_user, "manage", "media_jobs", f"scheduled media job {job_id}")
    return jsonify(job.to_dict())


# ── Observability ────────────────────────────────────────────────────────

@bp.route("/api/media/jobs/<string:job_id>/logs")
def get_media_job_logs(job_id):
    denied = _require("view")
    if denied:
        return denied
    job = MediaJob.query.get_or_404(job_id)
    logs: dict[str, str] = {}
    if job.workspace_path:
        logs_dir = Path(job.workspace_path) / "logs"
        if logs_dir.is_dir():
            for f in sorted(logs_dir.glob("*")):
                if f.is_file():
                    try:
                        content = f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    logs[f.name] = content[-32 * 1024:]  # tail last 32KB
    return jsonify({
        "job_id": job_id, "status": job.status, "attempt_count": job.attempt_count,
        "last_error": job.last_error, "logs": logs,
    })


@bp.route("/api/media/jobs/<string:job_id>/video")
def get_media_job_video(job_id):
    denied = _require("view")
    if denied:
        return denied
    job = MediaJob.query.get_or_404(job_id)
    if not job.render_path:
        return jsonify({"error": "no_render_available"}), 404

    root = media_workspace_root()
    try:
        resolved = Path(job.render_path).resolve()
        if resolved != root and root not in resolved.parents:
            raise PathSecurityError("render_path fora do workspace de mídia")
    except (OSError, PathSecurityError):
        return jsonify({"error": "invalid_render_path"}), 400
    if not resolved.is_file():
        return jsonify({"error": "file_not_found"}), 404

    # conditional=True makes Flask/Werkzeug handle Range requests (video
    # scrubbing in the browser) and If-Modified-Since automatically.
    return send_file(str(resolved), mimetype="video/mp4", conditional=True, download_name=f"{job_id}.mp4")
