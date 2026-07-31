#!/usr/bin/env python3
"""ADW: Daily Backup — Export workspace gitignored data to local ZIP (+ S3 if configured)"""

import sys
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runner import run_script, banner, summary

# Import backup logic from root backup.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import backup as backup_module


def _do_backup():
    """Run backup and return structured result for runner.

    If S3 is configured: backup to S3 only, remove local ZIP after upload.
    If S3 is not configured: backup to local only (fallback).
    """
    files = backup_module.collect_files()
    if not files:
        return {"ok": True, "summary": "No files to backup"}

    s3_bucket = os.environ.get("BACKUP_S3_BUCKET")

    if s3_bucket:
        # S3 mode: create local ZIP, upload to S3, then delete local copy
        zip_path = backup_module.backup_local(s3_upload=True)
        zip_size = zip_path.stat().st_size
        size_str = backup_module._format_size(zip_size)
        zip_path.unlink(missing_ok=True)
        return {"ok": True, "summary": f"{len(files)} files → s3://{s3_bucket}/{zip_path.name} ({size_str})"}
    else:
        # Local fallback
        zip_path = backup_module.backup_local(s3_upload=False)
        zip_size = zip_path.stat().st_size
        size_str = backup_module._format_size(zip_size)
        return {"ok": True, "summary": f"{len(files)} files → {zip_path.name} ({size_str}) [local]"}


def main():
    banner("💾 Daily Backup", "Workspace data export | systematic")
    results = []
    results.append(run_script(_do_backup, log_name="backup", timeout=300))
    sys.exit(1 if summary(results, "Daily Backup") else 0)
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠ Cancelado.")
