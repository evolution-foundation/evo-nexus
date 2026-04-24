"""Plugin installer skeleton — validates and previews plugin installs.

Step 2 scope: discover, validate, conflict check, env-var warning, version check,
and preview. Actual install/uninstall (file copy, SQL migration, heartbeat sync)
is wired in later steps.

Vault conditions implemented here:
  C6 — only https:// source URLs (enforced by PluginManifest.source_url validator).
  C7 — tarfile.extractall(filter='data') prevents zip-slip attacks.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from plugin_schema import PluginManifest

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PLUGINS_DIR = WORKSPACE / "plugins"
STAGING_DIR = PLUGINS_DIR / ".staging"

# Scheduler PID file location (ADR-2 — matches scheduler.py:PID_FILE)
SCHEDULER_PID_FILE = WORKSPACE / "ADWs" / "logs" / "scheduler.pid"

# Semver comparison (major.minor.patch only)
_VER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

# Allowed tarball source URL schemes (Vault C6 — schema-level, also enforced here)
_ALLOWED_SCHEMES = frozenset({"https"})


def _reload_scheduler() -> str | None:
    """Send SIGHUP to the running scheduler to trigger a hot-reload of routines.

    ADR-2: If the scheduler PID file is absent or the process is gone,
    returns an error key to be stored in plugins.last_error — install is NOT
    blocked (AC28).

    Returns:
        None on success, or a string error key on failure.
    """
    import os as _os
    import signal as _signal

    if not SCHEDULER_PID_FILE.exists():
        logger.info("Scheduler PID file not found — marking routine_activation_pending")
        return "routine_activation_pending"

    try:
        pid = int(SCHEDULER_PID_FILE.read_text().strip())
    except (ValueError, OSError) as exc:
        logger.warning("Could not read scheduler PID file: %s", exc)
        return "routine_activation_pending"

    try:
        _os.kill(pid, 0)  # liveness check
    except ProcessLookupError:
        logger.info("Scheduler PID %s is stale — marking routine_activation_pending", pid)
        return "routine_activation_pending"
    except PermissionError:
        logger.warning("No permission to signal scheduler PID %s", pid)
        return "scheduler_permission_denied"

    try:
        _os.kill(pid, _signal.SIGHUP)
        logger.info("SIGHUP sent to scheduler PID %s", pid)
        return None
    except OSError as exc:
        logger.warning("Failed to send SIGHUP to scheduler PID %s: %s", pid, exc)
        return f"sighup_failed:{exc}"


class PluginError(Exception):
    """Base class for plugin operation errors."""


class ConflictError(PluginError):
    """Raised when a plugin slug or namespace already exists."""


class VersionError(PluginError):
    """Raised when plugin requires a newer EvoNexus version."""


def _parse_version(v: str) -> tuple[int, int, int]:
    m = _VER_RE.match(v)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _current_evonexus_version() -> str:
    """Read version from pyproject.toml at workspace root."""
    toml_path = WORKSPACE / "pyproject.toml"
    if not toml_path.exists():
        return "0.0.0"
    for line in toml_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("version") and "=" in line:
            raw = line.split("=", 1)[1].strip().strip('"').strip("'")
            if _VER_RE.match(raw):
                return raw
    return "0.0.0"


def _get_db() -> sqlite3.Connection:
    db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class PluginInstaller:
    """Validates a plugin directory and previews what would be installed."""

    def discover(self) -> List[Dict[str, Any]]:
        """Return list of currently installed plugins (stub — populated in step 9)."""
        installed: List[Dict[str, Any]] = []
        if not PLUGINS_DIR.exists():
            return installed
        for d in sorted(PLUGINS_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                manifest_path = d / "plugin.yaml"
                if manifest_path.exists():
                    try:
                        m = PluginManifest.__config__  # noqa: just trigger import
                    except Exception:
                        pass
                    installed.append({"slug": d.name, "path": str(d)})
        return installed

    def validate(self, plugin_dir: Path) -> PluginManifest:
        """Load and validate plugin.yaml from plugin_dir.

        Args:
            plugin_dir: Directory containing plugin.yaml.

        Returns:
            Validated PluginManifest.

        Raises:
            FileNotFoundError: No plugin.yaml.
            ValidationError: Invalid manifest.
        """
        from plugin_schema import load_plugin_manifest
        return load_plugin_manifest(Path(plugin_dir))

    def check_conflicts(self, manifest: PluginManifest) -> None:
        """Raise ConflictError if slug or namespace is already in use.

        Checks:
        1. `plugins/{slug}/` directory already exists.
        2. Any `.claude/agents/plugin-{slug}-*.md` files exist.
        3. DB table `plugins` has a row with this slug (future-safe, table may not exist yet).

        Args:
            manifest: Validated PluginManifest.

        Raises:
            ConflictError: If any conflict is detected.
        """
        slug = manifest.id

        # 1. Filesystem: plugin directory already installed
        plugin_dir = PLUGINS_DIR / slug
        if plugin_dir.exists():
            raise ConflictError(
                f"Plugin directory already exists: {plugin_dir}. "
                "Uninstall first or use update."
            )

        # 2. Namespace: any agent files with this plugin prefix
        agents_dir = WORKSPACE / ".claude" / "agents"
        if agents_dir.exists():
            existing = list(agents_dir.glob(f"plugin-{slug}-*.md"))
            if existing:
                raise ConflictError(
                    f"Plugin namespace collision: found {len(existing)} agent file(s) "
                    f"with prefix 'plugin-{slug}-' in .claude/agents/. "
                    "These may be leftover from a previous install. Remove them first."
                )

        # 3. DB: plugins table (if it exists)
        try:
            conn = _get_db()
            try:
                row = conn.execute(
                    "SELECT id FROM plugins WHERE slug = ? LIMIT 1", (slug,)
                ).fetchone()
                if row:
                    raise ConflictError(
                        f"Plugin '{slug}' is already registered in the database. "
                        "Uninstall first or use update."
                    )
            except sqlite3.OperationalError:
                # Table doesn't exist yet (step 9 creates it) — no conflict
                pass
            finally:
                conn.close()
        except ConflictError:
            raise
        except Exception as exc:
            logger.warning("Could not check DB for plugin conflict: %s", exc)

    def _check_env_vars(self, manifest: PluginManifest) -> List[str]:
        """Return list of missing env vars declared in env_vars_needed.

        AC26: missing env vars are warnings, NOT blockers.
        """
        import os
        missing = [v for v in manifest.env_vars_needed if not os.environ.get(v)]
        if missing:
            logger.warning(
                "Plugin '%s' declares env_vars_needed=%s but these are not set: %s. "
                "Install will proceed but the plugin may not function correctly.",
                manifest.id,
                manifest.env_vars_needed,
                missing,
            )
        return missing

    def _check_version(self, manifest: PluginManifest) -> None:
        """Raise VersionError if current EvoNexus version is too old.

        Args:
            manifest: Validated PluginManifest with min_evonexus_version.

        Raises:
            VersionError: If installed EvoNexus < manifest.min_evonexus_version.
        """
        current = _current_evonexus_version()
        required = manifest.min_evonexus_version
        if _parse_version(current) < _parse_version(required):
            raise VersionError(
                f"Plugin '{manifest.id}' requires EvoNexus >= {required}, "
                f"but installed version is {current}."
            )

    @staticmethod
    def resolve_source(source_url: str, auth_token: str | None = None) -> Path:
        """Resolve a source_url to a local directory containing plugin.yaml.

        Supported forms:
          - GitHub shorthand: `github:owner/repo` or `github:owner/repo@ref`
          - HTTPS tarball:    `https://.../archive.tar.gz`
          - HTTPS zip:        `https://.../archive.zip`

        Uploaded archives (ZIP / tar.gz) go through
        :meth:`extract_uploaded_archive` instead and never reach this function.

        Local filesystem paths and non-HTTPS schemes (`file://`, `ssh://`,
        bare `/abs/path`, `./rel`, `~/...`) are rejected — the install flow
        must not read arbitrary directories off the host.

        Args:
            source_url: One of the supported forms above.
            auth_token: Optional GitHub Personal Access Token for private
                repos. Sent as `Authorization: token <pat>` header.

        Returns:
            Path to a staging directory containing plugin.yaml.

        Raises:
            ValueError: If the source does not match a supported form.
        """
        s = str(source_url).strip()

        if s.startswith("github:"):
            spec = s[len("github:"):]
            if "@" in spec:
                repo_part, ref = spec.split("@", 1)
            else:
                repo_part, ref = spec, "main"
            if "/" not in repo_part:
                raise ValueError(f"Invalid github shorthand: {source_url}")
            owner, repo = repo_part.split("/", 1)
            tar_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{ref}"
            staging_slug = f"{owner}-{repo}-{ref}".replace("/", "-")
            try:
                return PluginInstaller.fetch_from_tarball(tar_url, staging_slug, auth_token=auth_token)
            except RuntimeError:
                # fallback: try as tag ref
                tar_url_tag = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/tags/{ref}"
                return PluginInstaller.fetch_from_tarball(tar_url_tag, staging_slug, auth_token=auth_token)

        if s.startswith("https://"):
            # Use a safe staging slug derived from the URL
            staging_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", s)[-80:]
            return PluginInstaller.fetch_from_tarball(s, staging_slug, auth_token=auth_token)

        raise ValueError(
            "Unsupported plugin source. Use `github:owner/repo[@ref]`, "
            "an `https://` tarball URL, or upload a ZIP / tar.gz archive. "
            "Local filesystem paths and other schemes are not accepted."
        )

    @staticmethod
    def extract_uploaded_archive(file_storage, staging_slug: str) -> Path:
        """Extract an uploaded .zip or .tar.gz into staging and return the root dir.

        Args:
            file_storage: Flask FileStorage (request.files['file']).
            staging_slug: Unique slug for the staging subdir.

        Returns:
            Path to the extracted plugin directory.

        Raises:
            ValueError: unsupported archive format.
            RuntimeError: extraction failure.
        """
        import zipfile

        filename = (file_storage.filename or "").lower()
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        staging_dir = STAGING_DIR / staging_slug
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            file_storage.save(tmp.name)
            tmp_path = Path(tmp.name)

        try:
            if filename.endswith(".zip"):
                with zipfile.ZipFile(tmp_path, "r") as zf:
                    for member in zf.namelist():
                        # Reject absolute paths and parent-dir traversal
                        if member.startswith("/") or ".." in Path(member).parts:
                            raise RuntimeError(f"Unsafe entry in archive: {member}")
                    zf.extractall(staging_dir)
            elif filename.endswith(".tar.gz") or filename.endswith(".tgz") or filename.endswith(".tar"):
                mode = "r:gz" if filename.endswith((".tar.gz", ".tgz")) else "r:"
                with tarfile.open(tmp_path, mode) as tf:
                    tf.extractall(staging_dir, filter="data")  # type: ignore[call-arg]
            else:
                raise ValueError(f"Unsupported archive format: {filename}. Use .zip or .tar.gz")

            # Descend into single-root-dir if present (mirrors fetch_from_tarball)
            if not (staging_dir / "plugin.yaml").exists():
                entries = [p for p in staging_dir.iterdir() if not p.name.startswith(".")]
                if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "plugin.yaml").exists():
                    return entries[0]
            return staging_dir
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to extract uploaded archive: {exc}") from exc
        finally:
            tmp_path.unlink(missing_ok=True)

    def preview(self, plugin_dir: str | Path, auth_token: str | None = None) -> Dict[str, Any]:
        """Validate and preview a plugin install without writing anything.

        Wave 2.5 — now returns ``staged_path`` and ``tarball_sha256`` so the
        caller can pass the already-extracted directory to the security scanner
        and avoid a second download (single-resolve invariant).

        Args:
            plugin_dir: Local directory, `github:owner/repo[@ref]`, or HTTPS tarball.
            auth_token: Optional GitHub PAT for private repos.

        Returns:
            Dict with keys:
                manifest:       serialized PluginManifest
                warnings:       list of warning strings
                conflicts:      list of conflict error strings (non-empty means blocked)
                version_ok:     bool
                staged_path:    Path to the extracted staging directory (or local dir)
                tarball_sha256: hex SHA-256 of the raw tarball bytes (empty for local dirs)
        """
        result: Dict[str, Any] = {
            "manifest": None,
            "warnings": [],
            "conflicts": [],
            "version_ok": True,
            "staged_path": None,
            "tarball_sha256": "",
        }

        try:
            resolved, tarball_sha256 = self.resolve_source_with_sha(
                str(plugin_dir) if not isinstance(plugin_dir, Path) else str(plugin_dir),
                auth_token=auth_token,
            )
            plugin_dir = resolved
            result["tarball_sha256"] = tarball_sha256
        except ValueError as exc:
            result["conflicts"].append(str(exc))
            return result
        except RuntimeError as exc:
            result["conflicts"].append(f"Failed to fetch plugin source: {exc}")
            return result

        result["staged_path"] = plugin_dir

        # Validate manifest
        try:
            manifest = self.validate(plugin_dir)
        except FileNotFoundError as exc:
            result["conflicts"].append(str(exc))
            return result
        except ValidationError as exc:
            result["conflicts"].append(f"Invalid plugin.yaml: {exc}")
            return result

        result["manifest"] = manifest.model_dump()

        # Conflict check
        try:
            self.check_conflicts(manifest)
        except ConflictError as exc:
            result["conflicts"].append(str(exc))

        # Env vars (warnings only)
        missing_env = self._check_env_vars(manifest)
        for var in missing_env:
            result["warnings"].append(f"Environment variable not set: {var}")

        # Version check
        try:
            self._check_version(manifest)
        except VersionError as exc:
            result["version_ok"] = False
            result["conflicts"].append(str(exc))

        return result

    def resolve_source_with_sha(
        self, source: str, auth_token: str | None = None
    ) -> tuple[Path, str]:
        """Resolve a plugin source and return (path, tarball_sha256).

        Captures the raw tarball bytes SHA-256 *before* the temp file is
        unlinked, satisfying the Wave 2.5 cache key requirement.

        Only `github:` and `https://` sources are accepted here — local
        filesystem paths are rejected upstream by
        :meth:`resolve_source`. Uploaded archives take a different entry
        point (``extract_uploaded_archive``) and do not call this.

        This is the single-resolve entry-point — ``preview()`` and
        ``install_plugin()`` must both use this instead of calling
        ``resolve_source()`` directly.
        """
        s = source.strip()

        if not s.startswith("github:") and not s.startswith("https://"):
            # Delegate error reporting to resolve_source for a single
            # consistent rejection message.
            self.resolve_source(s, auth_token=auth_token)

        # GitHub shorthand → tarball URL
        if s.startswith("github:"):
            rest = s[len("github:"):]
            owner, _, rest2 = rest.partition("/")
            if "@" in rest2:
                repo, _, ref = rest2.partition("@")
            else:
                repo, ref = rest2, "main"
            tar_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{ref}"
            staging_slug = f"{owner}-{repo}-{ref}".replace("/", "-")
            try:
                path, sha = PluginInstaller.fetch_from_tarball_with_sha(
                    tar_url, staging_slug, auth_token=auth_token
                )
                return path, sha
            except RuntimeError:
                tar_url_tag = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/tags/{ref}"
                path, sha = PluginInstaller.fetch_from_tarball_with_sha(
                    tar_url_tag, staging_slug, auth_token=auth_token
                )
                return path, sha

        # Plain https:// tarball
        staging_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", s)[-80:]
        return PluginInstaller.fetch_from_tarball_with_sha(s, staging_slug, auth_token=auth_token)

    @staticmethod
    def fetch_from_tarball_with_sha(
        url: str, staging_slug: str, auth_token: str | None = None
    ) -> tuple[Path, str]:
        """Like ``fetch_from_tarball`` but also returns the SHA-256 of the raw tarball.

        Wave 2.5 — the SHA is captured *before* the temp file is unlinked so it
        can be used as the cache key for ``plugin_scan_cache``.

        Returns:
            (extracted_path, hex_sha256)
        """
        import hashlib as _hashlib
        from urllib.parse import urlparse
        import urllib.request

        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise ValueError(
                f"Only https:// URLs are permitted for plugin sources. Got: {url}"
            )

        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        staging_dir = STAGING_DIR / staging_slug

        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)

        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            logger.info("Fetching plugin archive from %s", url)
            if auth_token:
                req = urllib.request.Request(url, headers={"Authorization": f"token {auth_token}"})
                with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as out:  # noqa: S310
                    shutil.copyfileobj(resp, out)
            else:
                urllib.request.urlretrieve(url, tmp_path)  # noqa: S310

            # Capture SHA-256 before temp file is unlinked
            tarball_sha256 = _hashlib.sha256(tmp_path.read_bytes()).hexdigest()

            with tarfile.open(tmp_path, "r:gz") as tf:
                tf.extractall(staging_dir, filter="data")  # type: ignore[call-arg]

            logger.info("Extracted plugin archive to %s (sha256=%s)", staging_dir, tarball_sha256[:12])

            if not (staging_dir / "plugin.yaml").exists():
                entries = [p for p in staging_dir.iterdir() if not p.name.startswith(".")]
                if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "plugin.yaml").exists():
                    logger.info("Descending into single-root dir %s", entries[0].name)
                    return entries[0], tarball_sha256

            return staging_dir, tarball_sha256

        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to fetch/extract plugin from {url}: {exc}") from exc
        finally:
            tmp_path.unlink(missing_ok=True)

    @staticmethod
    def fetch_from_tarball(url: str, staging_slug: str, auth_token: str | None = None) -> Path:
        """Download and extract a plugin tarball from a remote URL.

        Vault condition C7: uses tarfile.extractall(filter='data') to prevent
        zip-slip path traversal attacks.

        Args:
            url: HTTPS URL to a .tar.gz plugin archive.
            staging_slug: Unique identifier for the staging directory.
            auth_token: Optional GitHub PAT / bearer token for private repos.

        Returns:
            Path to the extracted plugin directory in .staging/.

        Raises:
            ValueError: If URL scheme is not https.
            RuntimeError: On download or extraction failure.
        """
        from urllib.parse import urlparse
        import urllib.request

        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise ValueError(
                f"Only https:// URLs are permitted for plugin sources. Got: {url}"
            )

        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        staging_dir = STAGING_DIR / staging_slug

        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)

        # Download to temp file
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            logger.info("Fetching plugin archive from %s", url)
            if auth_token:
                req = urllib.request.Request(url, headers={"Authorization": f"token {auth_token}"})
                with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as out:  # noqa: S310 — scheme validated above
                    shutil.copyfileobj(resp, out)
            else:
                urllib.request.urlretrieve(url, tmp_path)  # noqa: S310 — scheme validated above

            # Vault C7: filter='data' strips absolute paths and .. components
            with tarfile.open(tmp_path, "r:gz") as tf:
                tf.extractall(staging_dir, filter="data")  # type: ignore[call-arg]

            logger.info("Extracted plugin archive to %s", staging_dir)

            # GitHub tarballs wrap content in a single root dir like `{owner}-{repo}-{sha}/`.
            # If plugin.yaml is not at the top level but there's a single subdir containing it,
            # descend into that subdir.
            if not (staging_dir / "plugin.yaml").exists():
                entries = [p for p in staging_dir.iterdir() if not p.name.startswith(".")]
                if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "plugin.yaml").exists():
                    logger.info("Descending into single-root dir %s", entries[0].name)
                    return entries[0]

            return staging_dir

        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to fetch/extract plugin from {url}: {exc}") from exc
        finally:
            tmp_path.unlink(missing_ok=True)


class PluginUninstaller:
    """Stubs for uninstall flow (wired up in steps 4+)."""

    def preview(self, slug: str) -> Dict[str, Any]:
        """Return what would be removed for slug."""
        plugin_dir = PLUGINS_DIR / slug
        return {
            "slug": slug,
            "exists": plugin_dir.exists(),
            "path": str(plugin_dir),
        }


class PluginUpdater:
    """Stubs for update flow (wired up in steps 4+)."""

    def preview(self, plugin_dir: str | Path, current_slug: str) -> Dict[str, Any]:
        """Return diff between current install and new plugin_dir."""
        installer = PluginInstaller()
        return installer.preview(plugin_dir)
