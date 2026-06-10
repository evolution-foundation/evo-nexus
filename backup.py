#!/usr/bin/env python3
"""
EvoNexus — Workspace Backup & Restore
Export/import all gitignored user data (memory, config, logs, customizations).
Usage: python backup.py backup [--target local|s3] [--s3-bucket BUCKET]
       python backup.py restore <file> [--mode merge|replace]
       python backup.py list [--target local|s3] [--s3-bucket BUCKET]
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(__file__).parent
BACKUPS_DIR = WORKSPACE / "backups"

# External paths to include in backup (outside WORKSPACE), mapped to ZIP prefix.
# auto-memory path is derived from workspace location to avoid hardcoding.
# Use as_posix() so the slug is consistent across platforms.
_workspace_slug = WORKSPACE.as_posix().replace("/", "-")  # matches Claude's project slug format
EXTERNAL_PATHS = {
    Path.home() / ".claude" / "projects" / _workspace_slug / "memory": "_external/auto-memory",
}

# Optional macOS LaunchAgent plist — opt-in via env var so upstream code doesn't
# bake a deployment-specific identifier. Set BACKUP_LAUNCHAGENT_PLIST to the
# absolute path of your plist file (e.g. ~/Library/LaunchAgents/com.example.plist).
_launchagent_plist = os.environ.get("BACKUP_LAUNCHAGENT_PLIST", "").strip()
if _launchagent_plist:
    _la_path = Path(_launchagent_plist).expanduser()
    EXTERNAL_PATHS[_la_path] = f"_external/launchagents/{_la_path.name}"


# Directories to exclude wherever they appear in the path (reconstructible / heavy)
EXCLUDE_DIRS = {
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    ".git",
    ".next",
    ".cache",
    ".local",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

# Top-level directories to exclude entirely (relative to workspace root).
# These are reconstructible from source (npm install, build, git clone, etc.)
# and don't contain user data worth backing up.
EXCLUDE_TOP_LEVEL = {
    "site",         # static site (rebuilds from docs/)
    "backups",      # previous backups (don't nest backups inside backups)
    ".venv",        # Python virtualenv
    "_evo",         # Evo method snapshot
    "_evo-output",  # Evo method output
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".db-shm",
    ".db-wal",
}

EXCLUDE_FILES = {
    ".DS_Store",
    "Thumbs.db",
}

# ANSI colors (for non-rich fallback)
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
    from rich.table import Table

    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def banner(title: str):
    if HAS_RICH:
        console.print(f"\n[bold green]  ╔══════════════════════════════════════╗[/]")
        console.print(f"[bold green]  ║   [bold white]{title:^32s}[/bold white]   ║[/]")
        console.print(f"[bold green]  ╚══════════════════════════════════════╝[/]\n")
    else:
        print(f"\n{GREEN}  ╔══════════════════════════════════════╗")
        print(f"  ║   {BOLD}{title:^32s}{RESET}{GREEN}   ║")
        print(f"  ╚══════════════════════════════════════╝{RESET}\n")


def _get_version() -> str:
    """Read version from pyproject.toml."""
    pyproject = WORKSPACE / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.strip().startswith("version"):
                return line.split("=")[1].strip().strip('"').strip("'")
    return "unknown"


def _get_workspace_name() -> str:
    """Read workspace name from config/workspace.yaml."""
    config_file = WORKSPACE / "config" / "workspace.yaml"
    if config_file.exists():
        try:
            import yaml
            config = yaml.safe_load(config_file.read_text())
            return config.get("name", "EvoNexus Workspace")
        except Exception:
            pass
    return "EvoNexus Workspace"


def _should_exclude(rel_path: str) -> bool:
    """Check if a file should be excluded from backup."""
    parts = Path(rel_path).parts
    # Exclude entire top-level directories (site/, backups/, etc.)
    if parts and parts[0] in EXCLUDE_TOP_LEVEL:
        return True
    # Exclude anywhere in the path (node_modules, .venv, __pycache__, etc.)
    for part in parts:
        if part in EXCLUDE_DIRS:
            return True
    p = Path(rel_path)
    if p.suffix in EXCLUDE_EXTENSIONS:
        return True
    if p.name in EXCLUDE_FILES:
        return True
    return False


def _walk_dynamic(root_rel: str) -> list[str]:
    """Walk a directory from the filesystem, skipping:
    - EXCLUDE_DIRS anywhere in the path (node_modules, .venv, __pycache__, ...)
    - EXCLUDE_FILES / EXCLUDE_EXTENSIONS
    - any sub-directory that contains a `.git` entry (treated as a sub-repo,
      which has its own backup via upstream remotes). The root itself is
      exempt from this check (WORKSPACE has .git of its own).
    """
    root_abs = WORKSPACE / root_rel
    if not root_abs.is_dir():
        return []
    out: list[str] = []
    for current, dirs, filenames in os.walk(root_abs):
        current_path = Path(current)
        # Prune excluded dirs in-place so os.walk doesn't descend.
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        # If this dir is a git sub-repo (has .git) AND it's not the workspace root, skip it.
        if current_path != WORKSPACE and ".git" in os.listdir(current):
            dirs[:] = []
            continue
        for fname in filenames:
            if fname in EXCLUDE_FILES:
                continue
            if Path(fname).suffix in EXCLUDE_EXTENSIONS:
                continue
            rel = str((current_path / fname).relative_to(WORKSPACE))
            out.append(rel)
    return out


def collect_files() -> list[str]:
    """Collect files to back up using two complementary strategies:

    1. Dynamic filesystem walk of `workspace/`, `memory/`, and `plugins/` —
       captures anything the user has created (including dashboard uploads
       and installed plugin directories). Skips sub-directories that
       contain their own `.git` (those have upstream remotes and backing
       up blobs duplicates state).
    2. `git ls-files --others --ignored` for the rest of the repo —
       captures .env, config/*.yaml, .claude/agent-memory, and the
       namespaced plugin artifacts under .claude/agents/plugin-*,
       .claude/skills/plugin-*, .claude/rules/plugin-*,
       .claude/commands/plugin-*.
    """
    files: set[str] = set()

    # Strategy 1 — dynamic walk of user-data roots.
    # `plugins/` is listed explicitly so installed plugin artifacts land
    # in the backup even if an author's file pattern doesn't match
    # .gitignore (Strategy 2 would miss it in that case).
    for root in ("workspace", "memory", "plugins"):
        for rel in _walk_dynamic(root):
            if not _should_exclude(rel):
                files.add(rel)

    # Strategy 2 — git-reported ignored files (covers everything else).
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
            capture_output=True, text=True, cwd=WORKSPACE, timeout=30
        )
        if result.returncode != 0:
            print(f"{RED}Error running git ls-files: {result.stderr.strip()}{RESET}")
            sys.exit(1)
    except FileNotFoundError:
        print(f"{RED}git not found. Backup requires git.{RESET}")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"{RED}git ls-files timed out. Repo may have too many ignored files.{RESET}")
        sys.exit(1)

    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if _should_exclude(line):
            continue
        # Skip files inside sub-repos (workspace/projects/<repo>/...).
        # We detect by walking parents looking for .git.
        if line.startswith("workspace/"):
            parts = Path(line).parts
            inside_subrepo = False
            for depth in range(2, len(parts)):
                candidate = WORKSPACE.joinpath(*parts[:depth]) / ".git"
                if candidate.exists():
                    inside_subrepo = True
                    break
            if inside_subrepo:
                continue
        full_path = WORKSPACE / line
        if full_path.is_file():
            files.add(line)

    return sorted(files)


def _format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ── Backup ───────────────────────────────────────


def _detect_db_backend() -> tuple[str, str]:
    """Return (backend, url): backend in {"sqlite", "postgres", "none"}.

    Reads DATABASE_URL env var. SQLite is the default when unset.
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url.startswith("postgresql"):
        return ("postgres", db_url)
    if db_url.startswith("sqlite") or not db_url:
        return ("sqlite", db_url or "sqlite:///dashboard/data/evonexus.db")
    return ("none", db_url)


def _pg_dump_to_file(database_url: str, output_path: Path) -> dict:
    """Run pg_dump --format=custom against database_url, write to output_path.

    Returns metadata dict for inclusion in manifest. Raises on failure.
    """
    if not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    # Verify pg_dump is available
    try:
        which = subprocess.run(
            ["which", "pg_dump"], capture_output=True, text=True, timeout=5
        )
        if which.returncode != 0:
            raise RuntimeError(
                "pg_dump not found in PATH. Install postgresql-client (Linux) "
                "or postgresql (macOS via Homebrew) to back up Postgres databases."
            )
    except FileNotFoundError:
        raise RuntimeError("'which' command not available; cannot verify pg_dump")

    # --format=custom is compressed and supports parallel restore + selective restore.
    # --no-owner/--no-acl keeps the dump portable across users/roles.
    print(f"  Running pg_dump (custom format)...")
    result = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--file", str(output_path),
            database_url,
        ],
        capture_output=True,
        text=True,
        timeout=600,  # 10 min for large DBs
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed:\n{result.stderr.strip()}")

    size = output_path.stat().st_size
    return {
        "format": "pg_custom",
        "size": size,
        "compressed": True,
        "no_owner": True,
        "no_acl": True,
    }


def _pg_restore_from_file(database_url: str, dump_path: Path, clean: bool = False) -> None:
    """Run pg_restore from dump_path against database_url.

    With clean=True, drops existing schema first (replace mode). Otherwise
    appends — idempotent inserts via --data-only would be nicer but require
    schema match; we default to append-and-let-conflicts-fail.
    """
    try:
        which = subprocess.run(
            ["which", "pg_restore"], capture_output=True, text=True, timeout=5
        )
        if which.returncode != 0:
            raise RuntimeError(
                "pg_restore not found in PATH. Install postgresql-client to "
                "restore Postgres backups."
            )
    except FileNotFoundError:
        raise RuntimeError("'which' command not available")

    cmd = [
        "pg_restore",
        "--no-owner",
        "--no-acl",
        "--dbname", database_url,
    ]
    if clean:
        cmd.extend(["--clean", "--if-exists"])
    cmd.append(str(dump_path))

    print(f"  Running pg_restore ({'replace' if clean else 'merge'} mode)...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    # pg_restore exits non-zero on per-object errors (e.g. role does not exist)
    # but the data still loads. Treat stderr-only as warning.
    if result.returncode != 0:
        # Check if any stderr lines look like real errors vs benign warnings
        stderr = result.stderr.strip()
        fatal_markers = ("FATAL:", "could not connect", "password authentication failed")
        if any(m in stderr for m in fatal_markers):
            raise RuntimeError(f"pg_restore failed:\n{stderr}")
        # Otherwise: print warnings but continue
        print(f"  {YELLOW}pg_restore reported non-fatal warnings:{RESET}")
        for line in stderr.splitlines()[:10]:
            print(f"    {line}")


def backup_local(s3_upload: bool = False, s3_bucket: str = None) -> Path:
    """Create a ZIP backup of gitignored user data plus database content.

    In SQLite mode the .db file is captured by the gitignored-files walk.
    In Postgres mode, the database is dumped via ``pg_dump --format=custom``
    and embedded in the ZIP as ``database.dump``.
    """
    banner("Backup — Export")

    backend, db_url = _detect_db_backend()
    if backend == "postgres":
        print(f"  {DIM}Postgres mode detected — pg_dump will be embedded.{RESET}")

    files = collect_files()
    if not files and backend != "postgres":
        print(f"{YELLOW}No gitignored files found to backup.{RESET}")
        sys.exit(0)

    # Collect external files (e.g. auto-memory outside workspace)
    # Each entry in EXTERNAL_PATHS can be a file or a directory.
    external_files: list[tuple[Path, str]] = []
    for ext_root, zip_prefix in EXTERNAL_PATHS.items():
        if not ext_root.exists():
            continue
        if ext_root.is_file():
            # Single-file entry: zip_prefix is the full path inside the ZIP
            external_files.append((ext_root, zip_prefix))
        else:
            # Directory entry: rglob all files, preserving relative structure
            for f in sorted(ext_root.rglob("*")):
                if f.is_file():
                    rel_in_zip = zip_prefix + "/" + f.relative_to(ext_root).as_posix()
                    external_files.append((f, rel_in_zip))


    BACKUPS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_name = f"evonexus-backup-{timestamp}.zip"
    zip_path = BACKUPS_DIR / zip_name

    # If Postgres, dump the database to a tempfile that we'll embed in the ZIP
    pg_dump_meta = None
    pg_dump_path = None
    if backend == "postgres":
        pg_dump_path = Path(tempfile.mkstemp(suffix=".dump", prefix="evonexus-pg-")[1])
        try:
            pg_dump_meta = _pg_dump_to_file(db_url, pg_dump_path)
            print(f"  pg_dump complete: {_format_size(pg_dump_meta['size'])}")
        except Exception as exc:
            print(f"{RED}pg_dump failed: {exc}{RESET}")
            pg_dump_path.unlink(missing_ok=True)
            sys.exit(1)

    # Build manifest
    file_entries = []
    total_size = 0
    for rel in files:
        full = WORKSPACE / rel
        size = full.stat().st_size
        total_size += size
        file_entries.append({"path": rel, "size": size})

    manifest = {
        "version": _get_version(),
        "workspace_name": _get_workspace_name(),
        "created_at": datetime.now().isoformat(),
        "hostname": platform.node(),
        "file_count": len(files),
        "total_size": total_size,
        "files": file_entries,
        "db_backend": backend,
    }
    if pg_dump_meta:
        manifest["db_dump"] = {"path": "database.dump", **pg_dump_meta}

    # Create ZIP — adds 1 entry for the pg_dump if present
    pg_step = 1 if pg_dump_path else 0
    total_steps = len(files) + pg_step

    try:
        if HAS_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                console=console,
            ) as progress:
                task = progress.add_task("Compressing...", total=total_steps)
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                    for rel in files:
                        zf.write(WORKSPACE / rel, rel)
                        progress.advance(task)
                    if pg_dump_path:
                        zf.write(pg_dump_path, "database.dump")
                        progress.advance(task)
        else:
            print(f"  Compressing {len(files)} files{' + database.dump' if pg_dump_path else ''}...")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))
                for rel in files:
                    zf.write(WORKSPACE / rel, rel)
                if pg_dump_path:
                    zf.write(pg_dump_path, "database.dump")
    finally:
        if pg_dump_path:
            pg_dump_path.unlink(missing_ok=True)

    zip_size = zip_path.stat().st_size

    if HAS_RICH:
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_row("[bold]Files[/]", str(len(files)))
        table.add_row("[bold]Original size[/]", _format_size(total_size))
        table.add_row("[bold]ZIP size[/]", _format_size(zip_size))
        table.add_row("[bold]Saved to[/]", str(zip_path.relative_to(WORKSPACE)))
        console.print()
        console.print(table)
        console.print(f"\n  [bold green]✓ Backup complete[/]")
    else:
        print(f"\n  Files:         {len(files)}")
        print(f"  Original size: {_format_size(total_size)}")
        print(f"  ZIP size:      {_format_size(zip_size)}")
        print(f"  Saved to:      {zip_path.relative_to(WORKSPACE)}")
        print(f"\n  {GREEN}✓ Backup complete{RESET}")

    # S3 upload if requested
    if s3_upload:
        backup_s3_upload(zip_path, s3_bucket)

    # Auto-cleanup old backups based on retention settings
    cleanup_old_backups(s3_bucket=s3_bucket)

    return zip_path


# ── Cleanup ─────────────────────────────────────────


def cleanup_old_backups(s3_bucket: str = None):
    """Remove old backups beyond retention limits (BACKUP_RETAIN_LOCAL, BACKUP_RETAIN_S3)."""

    # Local cleanup
    retain_local = os.environ.get("BACKUP_RETAIN_LOCAL", "").strip()
    if retain_local and retain_local.isdigit() and int(retain_local) > 0:
        limit = int(retain_local)
        if BACKUPS_DIR.exists():
            zips = sorted(BACKUPS_DIR.glob("evonexus-backup-*.zip"), reverse=True)
            to_delete = zips[limit:]
            for z in to_delete:
                z.unlink(missing_ok=True)
            if to_delete:
                msg = f"  Cleaned {len(to_delete)} old local backup(s) (keeping {limit})"
                if HAS_RICH:
                    console.print(f"  [dim]{msg}[/]")
                else:
                    print(f"  {DIM}{msg}{RESET}")

    # S3 cleanup
    retain_s3 = os.environ.get("BACKUP_RETAIN_S3", "").strip()
    bucket = s3_bucket or os.environ.get("BACKUP_S3_BUCKET", "")
    if retain_s3 and retain_s3.isdigit() and int(retain_s3) > 0 and bucket:
        limit = int(retain_s3)
        try:
            import boto3
            endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
            s3 = boto3.client("s3", endpoint_url=endpoint_url) if endpoint_url else boto3.client("s3")
            prefix = os.environ.get("BACKUP_S3_PREFIX", "evonexus-backups/")
            if not prefix.endswith("/"):
                prefix += "/"
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            objects = [o for o in resp.get("Contents", []) if o["Key"].endswith(".zip")]
            objects.sort(key=lambda o: o["LastModified"], reverse=True)
            to_delete = objects[limit:]
            for obj in to_delete:
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
            if to_delete:
                msg = f"  Cleaned {len(to_delete)} old S3 backup(s) (keeping {limit})"
                if HAS_RICH:
                    console.print(f"  [dim]{msg}[/]")
                else:
                    print(f"  {DIM}{msg}{RESET}")
        except Exception as e:
            print(f"  {YELLOW}S3 cleanup skipped: {e}{RESET}")


# ── Restore ──────────────────────────────────────


def restore_local(zip_path: Path, mode: str = "merge"):
    """Restore workspace from a ZIP backup.

    If the backup contains ``database.dump`` (Postgres mode), it is restored
    via ``pg_restore`` against the current ``DATABASE_URL``. In replace mode,
    existing schema is dropped first (--clean --if-exists). In merge mode,
    pg_restore appends and per-row conflicts are reported as warnings.

    Mismatches (PG dump → SQLite host, or vice versa) abort with a clear error.
    """
    banner("Backup — Restore")

    if not zip_path.exists():
        print(f"{RED}File not found: {zip_path}{RESET}")
        sys.exit(1)

    current_backend, current_db_url = _detect_db_backend()

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Validate manifest
        if "manifest.json" not in zf.namelist():
            print(f"{RED}Invalid backup: missing manifest.json{RESET}")
            sys.exit(1)

        manifest = json.loads(zf.read("manifest.json"))
        file_list = [e["path"] for e in manifest["files"]]
        backup_backend = manifest.get("db_backend", "sqlite")
        has_pg_dump = "database.dump" in zf.namelist()

        # Backend mismatch detection
        if backup_backend == "postgres" and current_backend != "postgres":
            print(
                f"{RED}Backend mismatch: this backup contains a Postgres dump, "
                f"but DATABASE_URL points to '{current_backend}'. Set "
                f"DATABASE_URL=postgresql://... before restoring.{RESET}"
            )
            sys.exit(1)
        if backup_backend == "sqlite" and current_backend == "postgres":
            print(
                f"{YELLOW}Note: this backup was made in SQLite mode but the "
                f"current host is Postgres. Files will be restored, but the "
                f"SQLite .db files inside the ZIP won't be applied to PG. "
                f"Use 'evonexus-migrate' for cross-backend data migration.{RESET}"
            )

        restored = 0
        skipped = 0

        if HAS_RICH:
            console.print(f"  [bold]Backup from:[/] {manifest['created_at']}")
            console.print(f"  [bold]Version:[/]     {manifest['version']}")
            console.print(f"  [bold]Files:[/]       {manifest['file_count']}")
            console.print(f"  [bold]Backend:[/]     {backup_backend}"
                          f"{' (with pg_dump)' if has_pg_dump else ''}")
            console.print(f"  [bold]Mode:[/]        {mode}\n")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                console=console,
            ) as progress:
                task = progress.add_task("Restoring files...", total=len(file_list))
                for rel in file_list:
                    dest = WORKSPACE / rel
                    if mode == "merge" and dest.exists():
                        skipped += 1
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        data = zf.read(rel)
                        dest.write_bytes(data)
                        restored += 1
                    progress.advance(task)
        else:
            print(f"  Backup from: {manifest['created_at']}")
            print(f"  Version:     {manifest['version']}")
            print(f"  Files:       {manifest['file_count']}")
            print(f"  Backend:     {backup_backend}"
                  f"{' (with pg_dump)' if has_pg_dump else ''}")
            print(f"  Mode:        {mode}\n")
            print(f"  Restoring {len(file_list)} files...")

            for rel in file_list:
                dest = WORKSPACE / rel
                if mode == "merge" and dest.exists():
                    skipped += 1
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    data = zf.read(rel)
                    dest.write_bytes(data)
                    restored += 1

        # Restore Postgres dump if present and we're in PG mode
        pg_restored = False
        if has_pg_dump and current_backend == "postgres":
            tmp_dump = Path(tempfile.mkstemp(suffix=".dump", prefix="evonexus-pg-restore-")[1])
            try:
                tmp_dump.write_bytes(zf.read("database.dump"))
                _pg_restore_from_file(
                    current_db_url,
                    tmp_dump,
                    clean=(mode == "replace"),
                )
                pg_restored = True
            except Exception as exc:
                print(f"{RED}pg_restore failed: {exc}{RESET}")
                # Don't sys.exit — files were already restored
            finally:
                tmp_dump.unlink(missing_ok=True)

    if HAS_RICH:
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_row("[bold]Files restored[/]", str(restored))
        table.add_row("[bold]Files skipped[/]", str(skipped))
        if has_pg_dump and current_backend == "postgres":
            table.add_row(
                "[bold]Database[/]",
                "✓ pg_restore complete" if pg_restored else "✗ pg_restore failed",
            )
        console.print()
        console.print(table)
        console.print(f"\n  [bold green]✓ Restore complete ({mode} mode)[/]")
    else:
        print(f"\n  Files restored: {restored}")
        print(f"  Files skipped:  {skipped}")
        if has_pg_dump and current_backend == "postgres":
            status = "✓ pg_restore complete" if pg_restored else "✗ pg_restore failed"
            print(f"  Database:       {status}")
        print(f"\n  {GREEN}✓ Restore complete ({mode} mode){RESET}")


# ── S3 ───────────────────────────────────────────


def _require_boto3():
    """Import boto3 or exit with a clear message."""
    try:
        import boto3
        return boto3
    except ImportError:
        print(f"{RED}boto3 not installed. Run: uv add boto3{RESET}")
        sys.exit(1)


def _get_s3_config(s3_bucket: str = None) -> tuple[str, str]:
    """Get S3 bucket and prefix from args or env vars."""
    bucket = s3_bucket or os.environ.get("BACKUP_S3_BUCKET")
    if not bucket:
        print(f"{RED}S3 bucket not configured. Set BACKUP_S3_BUCKET env var or use --s3-bucket.{RESET}")
        sys.exit(1)
    prefix = os.environ.get("BACKUP_S3_PREFIX", "evonexus-backups/")
    if not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def backup_s3_upload(zip_path: Path, s3_bucket: str = None):
    """Upload a local backup ZIP to S3."""
    boto3 = _require_boto3()
    bucket, prefix = _get_s3_config(s3_bucket)
    s3_key = prefix + zip_path.name

    if HAS_RICH:
        console.print(f"\n  [bold]Uploading to S3...[/]")
        console.print(f"  Bucket: {bucket}")
        console.print(f"  Key:    {s3_key}")

    s3 = boto3.client("s3")
    s3.upload_file(str(zip_path), bucket, s3_key)

    if HAS_RICH:
        console.print(f"  [bold green]✓ Uploaded to s3://{bucket}/{s3_key}[/]")
    else:
        print(f"  {GREEN}✓ Uploaded to s3://{bucket}/{s3_key}{RESET}")


def restore_s3(s3_key: str = None, s3_bucket: str = None, mode: str = "merge"):
    """Download a backup from S3 and restore it."""
    boto3 = _require_boto3()
    bucket, prefix = _get_s3_config(s3_bucket)
    s3 = boto3.client("s3")

    # If no key specified, list available and let user pick the latest
    if not s3_key:
        if HAS_RICH:
            console.print(f"  [bold]Listing backups in s3://{bucket}/{prefix}...[/]\n")
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        contents = resp.get("Contents", [])
        zips = [c for c in contents if c["Key"].endswith(".zip")]
        if not zips:
            print(f"{YELLOW}No backups found in s3://{bucket}/{prefix}{RESET}")
            sys.exit(0)
        # Use the most recent
        zips.sort(key=lambda c: c["LastModified"], reverse=True)
        s3_key = zips[0]["Key"]
        if HAS_RICH:
            console.print(f"  Using latest: [bold]{s3_key}[/]\n")
        else:
            print(f"  Using latest: {s3_key}")

    # Download to temp
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    if HAS_RICH:
        console.print(f"  [bold]Downloading s3://{bucket}/{s3_key}...[/]")
    s3.download_file(bucket, s3_key, str(tmp_path))

    restore_local(tmp_path, mode)
    tmp_path.unlink(missing_ok=True)


# ── List ─────────────────────────────────────────


def list_backups(target: str = "local", s3_bucket: str = None):
    """List available backups."""
    banner("Backup — List")

    if target == "local":
        if not BACKUPS_DIR.exists():
            print(f"  {YELLOW}No backups directory found.{RESET}")
            return
        zips = sorted(BACKUPS_DIR.glob("evonexus-backup-*.zip"), reverse=True)
        if not zips:
            print(f"  {YELLOW}No local backups found.{RESET}")
            return

        if HAS_RICH:
            table = Table(title="Local Backups")
            table.add_column("File", style="cyan")
            table.add_column("Size", justify="right")
            table.add_column("Date", style="dim")
            for z in zips:
                table.add_row(
                    z.name,
                    _format_size(z.stat().st_size),
                    datetime.fromtimestamp(z.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                )
            console.print(table)
        else:
            for z in zips:
                size = _format_size(z.stat().st_size)
                date = datetime.fromtimestamp(z.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                print(f"  {z.name}  {size:>10s}  {date}")

    elif target == "s3":
        boto3 = _require_boto3()
        bucket, prefix = _get_s3_config(s3_bucket)
        s3 = boto3.client("s3")
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        contents = resp.get("Contents", [])
        zips = [c for c in contents if c["Key"].endswith(".zip")]
        if not zips:
            print(f"  {YELLOW}No backups found in s3://{bucket}/{prefix}{RESET}")
            return
        zips.sort(key=lambda c: c["LastModified"], reverse=True)

        if HAS_RICH:
            table = Table(title=f"S3 Backups — s3://{bucket}/{prefix}")
            table.add_column("Key", style="cyan")
            table.add_column("Size", justify="right")
            table.add_column("Date", style="dim")
            for z in zips:
                table.add_row(
                    z["Key"].removeprefix(prefix),
                    _format_size(z["Size"]),
                    z["LastModified"].strftime("%Y-%m-%d %H:%M"),
                )
            console.print(table)
        else:
            for z in zips:
                name = z["Key"].removeprefix(prefix)
                size = _format_size(z["Size"])
                date = z["LastModified"].strftime("%Y-%m-%d %H:%M")
                print(f"  {name}  {size:>10s}  {date}")


# ── GitHub (Brain Repo) ──────────────────────────


def backup_to_github(config: dict = None) -> None:
    """Trigger brain repo commit+push and create a milestone tag."""
    tag = datetime.now().strftime("manual-backup-%Y-%m-%d")
    banner("Backup — GitHub (Brain Repo)")

    try:
        from brain_repo.git_ops import commit_all, push, create_tag  # type: ignore
        from models import BrainRepoConfig  # type: ignore

        cfg = config or BrainRepoConfig.get_default()
        if not cfg:
            print(f"{YELLOW}Brain Repo is not configured. Run setup or use the web UI.{RESET}")
            return

        commit_all(cfg, message=f"manual-backup {datetime.now().isoformat()}")
        push(cfg)
        create_tag(cfg, tag)

        if HAS_RICH:
            console.print(f"  [bold green]✓ Brain Repo backup complete — tag: {tag}[/]")
        else:
            print(f"  {GREEN}✓ Brain Repo backup complete — tag: {tag}{RESET}")

    except ImportError:
        print(f"{YELLOW}Brain repo modules not available. Run setup first.{RESET}")
        return
    except Exception as exc:
        print(f"{RED}Brain Repo backup failed: {exc}{RESET}")
        raise


# ── CLI ──────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="EvoNexus — Workspace Backup & Restore",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python backup.py backup                  # Local backup
  python backup.py backup --target s3      # Local + S3 upload
  python backup.py backup --target github  # Brain Repo commit+push
  python backup.py restore backups/evonexus-backup-20260409-120000.zip
  python backup.py restore backups/latest.zip --mode replace
  python backup.py restore --target s3     # Restore latest from S3
  python backup.py list                    # List local backups
  python backup.py list --target s3        # List S3 backups
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backup
    bp = sub.add_parser("backup", help="Export workspace data to ZIP")
    bp.add_argument("--target", choices=["local", "s3", "github"], default="local", help="Backup target (default: local)")
    bp.add_argument("--s3-bucket", help="S3 bucket (overrides BACKUP_S3_BUCKET env var)")

    # restore
    rp = sub.add_parser("restore", help="Import workspace data from ZIP")
    rp.add_argument("file", nargs="?", help="Path to backup ZIP (or S3 key)")
    rp.add_argument("--mode", choices=["merge", "replace"], default="merge", help="Restore mode (default: merge)")
    rp.add_argument("--target", choices=["local", "s3"], default="local", help="Restore source (default: local)")
    rp.add_argument("--s3-bucket", help="S3 bucket (overrides BACKUP_S3_BUCKET env var)")

    # list
    lp = sub.add_parser("list", help="List available backups")
    lp.add_argument("--target", choices=["local", "s3"], default="local", help="List target (default: local)")
    lp.add_argument("--s3-bucket", help="S3 bucket (overrides BACKUP_S3_BUCKET env var)")

    args = parser.parse_args()

    if args.command == "backup":
        if args.target == "github":
            backup_to_github()
        else:
            s3_upload = args.target == "s3"
            zip_path = backup_local(s3_upload=s3_upload, s3_bucket=args.s3_bucket)
            # S3 mode: remove local copy after successful upload
            if s3_upload and zip_path and zip_path.exists():
                zip_path.unlink(missing_ok=True)
                print(f"  {GREEN}✓ Local copy removed (S3 only){RESET}")

    elif args.command == "restore":
        if args.target == "s3":
            restore_s3(s3_key=args.file, s3_bucket=args.s3_bucket, mode=args.mode)
        else:
            if not args.file:
                parser.error("restore requires a file path (or use --target s3)")
            restore_local(Path(args.file), mode=args.mode)

    elif args.command == "list":
        list_backups(target=args.target, s3_bucket=args.s3_bucket)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Cancelled.{RESET}")
