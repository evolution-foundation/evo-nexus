"""Share links — create, list, revoke, and public view endpoints for workspace files."""

import mimetypes
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request, Response
from flask_login import login_required, current_user

from models import db, FileShare, audit, has_workspace_folder_access
from routes.auth_routes import require_permission

bp = Blueprint("shares", __name__)

# Resolve REPO_ROOT relative to this file: backend/routes/ → workspace root (3 levels up)
REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_DIR = REPO_ROOT / "workspace"

_EXPIRY_MAP = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".ogv"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".aac", ".flac", ".m4a", ".wma"}
_PDF_EXTS = {".pdf"}
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".sh", ".bash", ".zsh", ".env", ".tf", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".css", ".scss", ".xml", ".sql",
}


def _resolve_path_safe(path_str: str) -> Path | None:
    """Resolve a repo-relative path for share serving (no user context needed).

    Returns the resolved Path if valid, or None on any security violation.
    Only allows paths within WORKSPACE_DIR (no admin paths via shares).
    """
    if not path_str or "\x00" in path_str:
        return None
    p = Path(path_str)
    if p.is_absolute():
        return None

    full = (REPO_ROOT / path_str).resolve()

    # Must stay inside WORKSPACE_DIR
    try:
        full.relative_to(WORKSPACE_DIR.resolve())
    except ValueError:
        return None

    return full


def _content_type_for(path: Path) -> str:
    """Return the MIME type for the given path."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return "text/html; charset=utf-8"
    if suffix in _IMAGE_EXTS:
        mime, _ = mimetypes.guess_type(path.name)
        return mime or "application/octet-stream"
    return "application/json"


# ── Authenticated endpoints ─────────────────────────────────────────────────

@bp.route("/api/shares", methods=["POST"])
@login_required
@require_permission("workspace", "manage")
def create_share():
    """Create a new public share link for a workspace file."""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "").strip()
    expires_in = data.get("expires_in", "7d")  # e.g. "1h", "24h", "7d", "30d", null

    if not path:
        return jsonify({"error": "path is required", "code": "bad_path"}), 400

    # Validate path resolves to a real file inside WORKSPACE_DIR
    full = _resolve_path_safe(path)
    if full is None:
        return jsonify({"error": "Invalid or disallowed path", "code": "bad_path"}), 400
    if not full.exists() or not full.is_file():
        return jsonify({"error": "File not found", "code": "not_found"}), 404

    # Enforce folder access before creating a share
    if not has_workspace_folder_access(current_user.role, path):
        return jsonify({"error": "Access to this workspace folder is restricted", "code": "forbidden"}), 403

    # Calculate expiry
    expires_at = None
    if expires_in and expires_in in _EXPIRY_MAP:
        expires_at = datetime.now(timezone.utc) + _EXPIRY_MAP[expires_in]

    token = secrets.token_urlsafe(32)
    share = FileShare(
        token=token,
        path=path,
        created_by_id=current_user.id,
        expires_at=expires_at,
    )
    db.session.add(share)
    db.session.commit()

    audit(current_user, "share_create", "shares", detail=f"path={path} expiry={expires_in}")

    base_url = request.host_url.rstrip("/")
    return jsonify({
        "token": token,
        "path": path,
        "url": f"{base_url}/share/{token}",
        "expires_at": share.expires_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if share.expires_at else None,
        "created_at": share.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if share.created_at else None,
    }), 201


@bp.route("/api/shares", methods=["GET"])
@login_required
@require_permission("workspace", "manage")
def list_shares():
    """List all share links."""
    shares = FileShare.query.order_by(FileShare.created_at.desc()).all()
    return jsonify({"shares": [s.to_dict() for s in shares]})


@bp.route("/api/shares/by-path", methods=["GET"])
@login_required
@require_permission("workspace", "manage")
def get_active_share_by_path():
    """Return the most recent ACTIVE (enabled + not expired) share for a path,
    so the UI can reuse it instead of generating a new token every time."""
    path = (request.args.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path is required", "code": "bad_path"}), 400

    if not has_workspace_folder_access(current_user.role, path):
        return jsonify({"error": "Access to this workspace folder is restricted", "code": "forbidden"}), 403

    now = datetime.now(timezone.utc)
    candidates = (
        FileShare.query
        .filter_by(path=path, enabled=True)
        .order_by(FileShare.created_at.desc())
        .all()
    )
    for share in candidates:
        if share.expires_at is not None:
            expires = share.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                continue
        base_url = request.host_url.rstrip("/")
        return jsonify({
            **share.to_dict(),
            "url": f"{base_url}/share/{share.token}",
        })
    return jsonify({"error": "No active share for this path", "code": "not_found"}), 404


@bp.route("/api/shares/<token>", methods=["DELETE"])
@login_required
@require_permission("workspace", "manage")
def revoke_share(token: str):
    """Revoke a share link (set enabled=False)."""
    share = FileShare.query.filter_by(token=token).first()
    if not share:
        return jsonify({"error": "Share not found", "code": "not_found"}), 404

    share.enabled = False
    db.session.commit()

    audit(current_user, "share_revoke", "shares", detail=f"token={token} path={share.path}")
    return jsonify({"ok": True, "token": token})


# ── Public endpoint (no auth required) ──────────────────────────────────────

@bp.route("/api/shares/<token>/view", methods=["GET"])
def view_share(token: str):
    """Serve the file content for a valid share token. No authentication required."""
    share = FileShare.query.filter_by(token=token).first()

    if not share or not share.enabled:
        return jsonify({"error": "Link inválido ou expirado", "code": "not_found"}), 404

    # Check expiry
    if share.expires_at:
        now = datetime.now(timezone.utc)
        expires = share.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now > expires:
            return jsonify({"error": "Link inválido ou expirado", "code": "expired"}), 404

    # Resolve path without current_user dependency
    full = _resolve_path_safe(share.path)
    if full is None or not full.exists() or not full.is_file():
        return jsonify({"error": "Arquivo não encontrado", "code": "not_found"}), 404

    # Increment view count
    share.view_count = (share.view_count or 0) + 1
    db.session.commit()

    # Log the view (user=None for anonymous)
    ip = request.remote_addr or "-"
    ua = (request.headers.get("User-Agent", "-") or "-")[:200]
    audit(None, "share_view", "shares", detail=f"token={token} ip={ip} ua={ua[:80]}")

    suffix = full.suffix.lower()

    # HTML/HTM: serve raw so browser renders it as a full page
    if suffix in (".html", ".htm"):
        content = full.read_bytes()
        return Response(content, mimetype="text/html; charset=utf-8")

    # Images: serve binary with correct MIME type
    if suffix in _IMAGE_EXTS:
        mime, _ = mimetypes.guess_type(full.name)
        content = full.read_bytes()
        return Response(content, mimetype=mime or "application/octet-stream")

    # Video: serve binary with correct MIME type
    if suffix in _VIDEO_EXTS:
        mime, _ = mimetypes.guess_type(full.name)
        content = full.read_bytes()
        return Response(content, mimetype=mime or "video/mp4")

    # Audio: serve binary with correct MIME type
    if suffix in _AUDIO_EXTS:
        mime, _ = mimetypes.guess_type(full.name)
        content = full.read_bytes()
        return Response(content, mimetype=mime or "audio/mpeg")

    # PDF: serve binary
    if suffix in _PDF_EXTS:
        content = full.read_bytes()
        return Response(content, mimetype="application/pdf")

    # Markdown: return as JSON for frontend rendering
    if suffix in (".md", ".markdown"):
        try:
            content = full.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            return jsonify({"error": "Erro ao ler arquivo", "code": "read_error"}), 500
        return jsonify({"content": content, "type": "markdown", "path": share.path})

    # Code files: return as JSON with type=code
    if suffix in _CODE_EXTS:
        try:
            content = full.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            return jsonify({"error": "Erro ao ler arquivo", "code": "read_error"}), 500
        return jsonify({"content": content, "type": "code", "path": share.path, "extension": suffix.lstrip(".")})

    # Default: try to read as text
    try:
        content = full.read_text("utf-8")
        return jsonify({"content": content, "type": "text", "path": share.path})
    except UnicodeDecodeError:
        # Binary file — not shareable in v1
        return jsonify({"error": "Arquivo binário não suportado", "code": "unsupported"}), 415
