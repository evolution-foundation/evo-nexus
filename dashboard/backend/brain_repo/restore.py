"""Brain Repo — Restore engine with streaming progress events."""

import logging
import shutil
import tempfile
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

STAGING_DIR = Path("/tmp/brain-restore-staging")

# Directories to swap during restore
_SWAP_DIRS = ["memory", "workspace", "customizations", "config-safe"]


def _event(step: str, progress: int, message: str, error: bool = False) -> dict:
    return {"step": step, "progress": progress, "message": message, "error": error}


def _cleanup_staging(staging: Path) -> None:
    try:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    except Exception as exc:
        log.warning("restore: could not clean staging dir %s: %s", staging, exc)


def execute_restore(
    repo_url: str,
    ref: str,
    token: str,
    install_dir: Path,
    include_kb: bool,
    kb_key_matches: bool,
) -> Generator[dict, None, None]:
    """Restore a brain repo snapshot to install_dir.

    Yields progress events:
        {"step": str, "progress": int, "message": str, "error": bool}

    Steps:
        clone (5) → validate_manifest (20) → migrate (35) → secrets_scan (50)
        → kb_validate (60) → backup_originals (65) → swap (75)
        → kb_import (85) → manifest_update (95) → complete (100)
    """
    from brain_repo import git_ops, manifest as manifest_mod, migrations, secrets_scanner  # type: ignore[import]

    staging = STAGING_DIR / f"restore-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    staging.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 1. clone
    yield _event("clone", 5, f"Cloning {ref} from repository…")
    try:
        git_ops.clone(repo_url, token, staging)
        # If ref is not HEAD/default branch, checkout that specific ref
        if ref and ref not in ("HEAD", "main", "master"):
            ref_staging = staging / "_ref_extract"
            git_ops.checkout_ref(staging, ref, ref_staging)
            # Use extracted ref content instead of full clone
            shutil.rmtree(staging, ignore_errors=True)
            ref_staging.rename(staging)
    except Exception as exc:
        yield _event("clone", 5, f"Clone failed: {exc}", error=True)
        _cleanup_staging(staging)
        return

    # -------------------------------------------------------- 2. validate_manifest
    yield _event("validate_manifest", 20, "Validating manifest schema…")
    try:
        manifest_data = manifest_mod.read_manifest(staging)
        schema_ok, migration_needed = manifest_mod.validate_schema(manifest_data)
        if not schema_ok and not manifest_data:
            yield _event("validate_manifest", 20, "Warning: manifest.yaml not found or empty — proceeding")
            migration_needed = False
        elif not schema_ok:
            yield _event("validate_manifest", 20, "Warning: manifest schema incomplete, attempting migration")
            migration_needed = True
    except Exception as exc:
        yield _event("validate_manifest", 20, f"Manifest validation error: {exc}", error=True)
        _cleanup_staging(staging)
        return

    # --------------------------------------------------------------- 3. migrate
    yield _event("migrate", 35, "Checking migrations…")
    if migration_needed:
        from_version = manifest_data.get("schema_version", "0")
        to_version = manifest_mod.MANIFEST_SCHEMA_VERSION
        try:
            migrations.migrate(staging, from_version, to_version)
            yield _event("migrate", 35, f"Migrated from {from_version} to {to_version}")
        except Exception as exc:
            yield _event("migrate", 35, f"Migration failed: {exc}", error=True)
            _cleanup_staging(staging)
            return
    else:
        yield _event("migrate", 35, "No migrations needed")

    # --------------------------------------------------------- 4. secrets_scan
    yield _event("secrets_scan", 50, "Scanning for secrets…")
    try:
        violations = secrets_scanner.scan_directory(staging)
        if violations:
            detail = "; ".join(
                f"{v['file']}:{v['line']} [{v['pattern']}]" for v in violations[:5]
            )
            yield _event(
                "secrets_scan",
                50,
                f"Security scan found {len(violations)} potential secret(s): {detail}",
                error=True,
            )
            _cleanup_staging(staging)
            return
        yield _event("secrets_scan", 50, "No secrets found")
    except Exception as exc:
        yield _event("secrets_scan", 50, f"Secrets scan error: {exc}", error=True)
        _cleanup_staging(staging)
        return

    # --------------------------------------------------------- 5. kb_validate
    yield _event("kb_validate", 60, "Validating KB key…")
    if include_kb and not kb_key_matches:
        yield _event(
            "kb_validate",
            60,
            "Warning: master key mismatch — KB content will not be imported (metadata only)",
        )
        include_kb = False
    else:
        yield _event("kb_validate", 60, "KB validation OK")

    # ------------------------------------------------------ 6. backup_originals
    backup_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_dir = Path(tempfile.gettempdir()) / f"brain-restore-backup-{backup_ts}"
    yield _event("backup_originals", 65, f"Backing up originals to {backup_dir}…")
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for d in _SWAP_DIRS:
            src = install_dir / d
            if src.exists():
                shutil.copytree(str(src), str(backup_dir / d))
        yield _event("backup_originals", 65, "Backup complete")
    except Exception as exc:
        yield _event("backup_originals", 65, f"Backup failed: {exc}", error=True)
        _cleanup_staging(staging)
        return

    # ---------------------------------------------------------------- 7. swap
    yield _event("swap", 75, "Applying restore…")
    try:
        for d in _SWAP_DIRS:
            src = staging / d
            dest = install_dir / d
            if not src.exists():
                log.debug("restore swap: %s not in staging, skipping", d)
                continue
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(str(src), str(dest))
        yield _event("swap", 75, "Swap complete")
    except Exception as exc:
        yield _event("swap", 75, f"Swap failed: {exc}", error=True)
        # Do NOT remove backup — user needs to recover manually
        _cleanup_staging(staging)
        return

    # --------------------------------------------------------------- 8. kb_import
    yield _event("kb_import", 85, "Importing KB…")
    if include_kb:
        try:
            from brain_repo import kb_mirror  # type: ignore[import]
            result = kb_mirror.import_markdown_to_kb(
                staging / "kb-mirror",
                master_key_matches=kb_key_matches,
            )
            yield _event("kb_import", 85, f"KB import done: {result['imported']} imported, {result['skipped']} skipped")
        except Exception as exc:
            yield _event("kb_import", 85, f"KB import error (non-fatal): {exc}")
    else:
        yield _event("kb_import", 85, "KB import skipped")

    # -------------------------------------------------- 9. manifest_update
    yield _event("manifest_update", 95, "Updating manifest…")
    try:
        brain_repo_dir = install_dir  # install_dir IS the brain repo local path
        updated_manifest = manifest_mod.read_manifest(brain_repo_dir)
        updated_manifest["last_sync"] = datetime.now(timezone.utc).isoformat()
        manifest_mod.write_manifest(brain_repo_dir, updated_manifest)
        yield _event("manifest_update", 95, "Manifest updated")
    except Exception as exc:
        yield _event("manifest_update", 95, f"Manifest update warning: {exc}")

    # ---------------------------------------------------------------- 10. complete
    _cleanup_staging(staging)
    yield _event("complete", 100, "Restore complete")
