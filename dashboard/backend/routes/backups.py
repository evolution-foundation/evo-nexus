"""Backups endpoint — list, create, restore, download, delete workspace backups."""

import json
import os
import threading
from flask import Blueprint, jsonify, request, send_file, abort
from flask_login import current_user
from models import has_permission, audit
from routes._helpers import WORKSPACE

bp = Blueprint("backups", __name__)

BACKUPS_DIR = WORKSPACE / "backups"

# Track running backup jobs
_running_jobs = {}


def _post_restore_migrate():
    """Run schema fixes after restoring a backup (old DBs may have missing columns/bad data)."""
    import sqlite3
    from flask import current_app

    db_path = current_app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Ensure all tables exist (db.create_all equivalent for new models)
    from models import db as _db
    _db.create_all()

    # Add missing columns
    existing = {row[1] for row in cur.execute("PRAGMA table_info(roles)").fetchall()}
    if "agent_access_json" not in existing:
        cur.execute("ALTER TABLE roles ADD COLUMN agent_access_json TEXT DEFAULT '{\"mode\": \"all\"}'")

    # Fix corrupted datetime columns (NULL or non-string crash SQLAlchemy)
    for tbl, col in [("roles", "created_at"), ("users", "created_at"), ("users", "last_login")]:
        try:
            tbl_cols = {row[1] for row in cur.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if col in tbl_cols:
                cur.execute(f"UPDATE {tbl} SET {col} = datetime('now') WHERE {col} IS NOT NULL AND typeof({col}) != 'text'")
                cur.execute(f"UPDATE {tbl} SET {col} = datetime('now') WHERE {col} IS NOT NULL AND {col} != '' AND {col} NOT LIKE '____-__-__%'")
        except Exception:
            pass

    conn.commit()
    conn.close()

    # Re-seed roles to ensure new permissions exist
    from models import seed_roles, seed_systems
    seed_roles()
    seed_systems()


def _require(resource: str, action: str):
    if not has_permission(current_user.role, resource, action):
        return jsonify({"error": "Forbidden"}), 403
    return None


@bp.route("/api/backups")
def list_backups():
    """List all local backup files with manifest info."""
    denied = _require("config", "view")
    if denied:
        return denied

    if not BACKUPS_DIR.exists():
        return jsonify({"backups": [], "total": 0})

    backups = []
    for z in sorted(BACKUPS_DIR.glob("evonexus-backup-*.zip"), reverse=True):
        info = {
            "filename": z.name,
            "size": z.stat().st_size,
            "modified": z.stat().st_mtime,
            "manifest": None,
        }
        # Try to read manifest from ZIP
        try:
            import zipfile
            with zipfile.ZipFile(z, "r") as zf:
                if "manifest.json" in zf.namelist():
                    info["manifest"] = json.loads(zf.read("manifest.json"))
        except Exception:
            pass
        backups.append(info)

    return jsonify({"backups": backups, "total": len(backups)})


@bp.route("/api/backups", methods=["POST"])
def create_backup():
    """Trigger a new backup. Runs in background thread."""
    denied = _require("config", "manage")
    if denied:
        return denied

    if _running_jobs.get("backup"):
        return jsonify({"error": "A backup is already running"}), 409

    target = request.get_json(silent=True) or {}
    s3_upload = target.get("target") == "s3"

    def _run():
        try:
            import sys
            sys.path.insert(0, str(WORKSPACE))
            import backup as backup_module
            zip_path = backup_module.backup_local(s3_upload=s3_upload)
            # S3 mode: remove local copy after successful upload
            if s3_upload and zip_path and zip_path.exists():
                zip_path.unlink(missing_ok=True)
            _running_jobs["backup"] = {"status": "done"}
        except Exception as e:
            _running_jobs["backup"] = {"status": "error", "error": str(e)}

    _running_jobs["backup"] = {"status": "running"}
    t = threading.Thread(target=_run, daemon=True)
    t.start()

    audit(current_user, "create", "backups", f"Started backup (target={'s3' if s3_upload else 'local'})")
    return jsonify({"status": "started", "target": "s3" if s3_upload else "local"}), 202


@bp.route("/api/backups/status")
def backup_status():
    """Check status of running backup job."""
    denied = _require("config", "view")
    if denied:
        return denied
    job = _running_jobs.get("backup", {"status": "idle"})
    return jsonify(job)


@bp.route("/api/backups/<filename>/restore", methods=["POST"])
def restore_backup(filename):
    """Restore from a specific backup file."""
    denied = _require("config", "manage")
    if denied:
        return denied

    zip_path = BACKUPS_DIR / filename
    if not zip_path.exists() or not filename.endswith(".zip"):
        abort(404, description="Backup not found")

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "merge")
    if mode not in ("merge", "replace"):
        return jsonify({"error": "Invalid mode. Use 'merge' or 'replace'"}), 400

    def _run():
        try:
            import sys
            sys.path.insert(0, str(WORKSPACE))
            import backup as backup_module
            backup_module.restore_local(zip_path, mode=mode)

            # After restore, run auto-migrate to fix schema differences
            # (old backups may have missing columns or corrupted data)
            _post_restore_migrate()

            _running_jobs["restore"] = {"status": "done", "mode": mode}
        except Exception as e:
            _running_jobs["restore"] = {"status": "error", "error": str(e)}

    _running_jobs["restore"] = {"status": "running"}
    t = threading.Thread(target=_run, daemon=True)
    t.start()

    audit(current_user, "restore", "backups", f"Restoring {filename} (mode={mode})")
    return jsonify({"status": "started", "mode": mode}), 202


@bp.route("/api/backups/<filename>/download")
def download_backup(filename):
    """Download a backup ZIP file."""
    denied = _require("config", "view")
    if denied:
        return denied

    zip_path = BACKUPS_DIR / filename
    if not zip_path.exists() or not filename.endswith(".zip"):
        abort(404, description="Backup not found")

    return send_file(str(zip_path), as_attachment=True, download_name=filename)


@bp.route("/api/backups/<filename>", methods=["DELETE"])
def delete_backup(filename):
    """Delete a backup file."""
    denied = _require("config", "manage")
    if denied:
        return denied

    zip_path = BACKUPS_DIR / filename
    if not zip_path.exists() or not filename.endswith(".zip"):
        abort(404, description="Backup not found")

    zip_path.unlink()
    audit(current_user, "delete", "backups", f"Deleted backup {filename}")
    return jsonify({"status": "deleted"})


@bp.route("/api/backups/upload", methods=["POST"])
def upload_backup():
    """Import an external backup ZIP file into the local backups directory."""
    denied = _require("config", "manage")
    if denied:
        return denied

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.endswith(".zip"):
        return jsonify({"error": "Only .zip files are accepted"}), 400

    # Sanitize filename
    import re
    safe_name = re.sub(r"[^\w\-.]", "_", f.filename)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUPS_DIR / safe_name

    # Prevent overwrite
    if dest.exists():
        return jsonify({"error": f"File {safe_name} already exists"}), 409

    f.save(str(dest))

    # Validate it's a real ZIP
    import zipfile
    try:
        with zipfile.ZipFile(dest, "r") as zf:
            zf.testzip()
    except (zipfile.BadZipFile, Exception):
        dest.unlink()
        return jsonify({"error": "Invalid ZIP file"}), 400

    audit(current_user, "upload", "backups", f"Imported backup {safe_name} ({dest.stat().st_size} bytes)")
    return jsonify({
        "status": "uploaded",
        "filename": safe_name,
        "size": dest.stat().st_size,
    }), 201


@bp.route("/api/backups/s3")
def list_s3_backups():
    """List backup files stored in S3."""
    denied = _require("config", "view")
    if denied:
        return denied

    bucket = os.environ.get("BACKUP_S3_BUCKET")
    if not bucket:
        return jsonify({"backups": [], "error": "S3 not configured"})

    try:
        import boto3
    except ImportError:
        return jsonify({"backups": [], "error": "boto3 not installed"})

    try:
        endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
        s3 = boto3.client("s3", endpoint_url=endpoint_url) if endpoint_url else boto3.client("s3")
        prefix = os.environ.get("BACKUP_S3_PREFIX", "")
        response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        backups = []
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".zip"):
                continue
            backups.append({
                "key": key,
                "filename": key.rsplit("/", 1)[-1],
                "size": obj["Size"],
                "modified": obj["LastModified"].isoformat(),
            })
        backups.sort(key=lambda x: x["modified"], reverse=True)
        return jsonify({"backups": backups, "total": len(backups)})
    except Exception as e:
        return jsonify({"backups": [], "error": str(e)})


@bp.route("/api/backups/s3/<path:key>/download")
def download_s3_backup(key):
    """Download a backup from S3 to local backups dir, then serve it."""
    denied = _require("config", "manage")
    if denied:
        return denied

    bucket = os.environ.get("BACKUP_S3_BUCKET")
    if not bucket:
        return jsonify({"error": "S3 not configured"}), 400

    try:
        import boto3
        endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
        s3 = boto3.client("s3", endpoint_url=endpoint_url) if endpoint_url else boto3.client("s3")
        filename = key.rsplit("/", 1)[-1]
        local_path = BACKUPS_DIR / filename
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(local_path))
        return send_file(str(local_path), as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/backups/config")
def backup_config():
    """Return backup configuration (Local always, S3 + Brain Repo if configured)."""
    denied = _require("config", "view")
    if denied:
        return denied

    s3_configured = bool(os.environ.get("BACKUP_S3_BUCKET"))
    s3_bucket = os.environ.get("BACKUP_S3_BUCKET", "")

    try:
        import boto3
        boto3_available = True
    except ImportError:
        boto3_available = False

    # Brain Repo status — best-effort. Hidden from response if user has no
    # config yet (so the UI can render a 'not configured' card without
    # depending on a 404 path). Returned object mirrors BrainRepoConfig.to_dict.
    brain_repo_configured = False
    brain_repo_info = None
    try:
        from models import BrainRepoConfig
        cfg = BrainRepoConfig.query.filter_by(user_id=current_user.id).first()
        if cfg is not None and cfg.github_token_encrypted:
            brain_repo_configured = True
            brain_repo_info = cfg.to_dict()
    except Exception:
        # Fail open — if BrainRepoConfig isn't migrated yet, just hide the section
        pass

    # Crypto-readiness — the UI uses this to render a warning if the brain
    # repo has a config but the server can't actually encrypt/decrypt tokens
    # right now (e.g. master key lost, cryptography module missing).
    try:
        from brain_repo import is_crypto_ready
        brain_crypto_ready = is_crypto_ready()
    except Exception:
        brain_crypto_ready = False

    return jsonify({
        "s3_configured": s3_configured,
        "s3_bucket": s3_bucket,
        "boto3_available": boto3_available,
        "backups_dir": str(BACKUPS_DIR.relative_to(WORKSPACE)),
        "brain_repo_configured": brain_repo_configured,
        "brain_repo": brain_repo_info,
        "brain_crypto_ready": brain_crypto_ready,
    })
