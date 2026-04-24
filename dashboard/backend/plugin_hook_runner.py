"""Lifecycle hook runner for plugins.

Executes pre-install, post-install, pre-uninstall, post-uninstall shell scripts
found in a plugin's hooks/ directory. Applies:

- Env scoping (Vault condition R2/F9): only a whitelist of safe env vars
  is passed to the subprocess, plus plugin-declared env_vars_needed.
- 30-second timeout by default (plan step 5 specifies timeout=60 for long ops).
- Logs stdout/stderr to ADWs/logs/plugins/{slug}-{hook_name}-{timestamp}.log.
- SHA256 of the script is recorded for audit.
- Exit != 0 → LifecycleHookError.
- Timeout → subprocess killed + TimeoutError re-raised.

Plan reference: plan-plugins-v1a.md step 5 (RF2 steps 1+11, RNF3)
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent
PLUGIN_LOGS_DIR = WORKSPACE / "ADWs" / "logs" / "plugins"

# Env vars always passed to lifecycle hooks (Vault R2/F9 scoping)
_SAFE_ENV_PASSTHROUGH = frozenset(
    {"HOME", "PATH", "USER", "LANG", "SHELL"}
)

# Evonexus-specific vars injected by the runner
_EVONEXUS_HOOK_VARS = ("EVONEXUS_PLUGIN_SLUG", "EVONEXUS_PLUGIN_DIR")


class LifecycleHookError(Exception):
    """Raised when a lifecycle hook script exits with a non-zero code."""

    def __init__(
        self,
        hook_name: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        log_path: Optional[Path] = None,
    ) -> None:
        self.hook_name = hook_name
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.log_path = log_path
        super().__init__(
            f"Lifecycle hook '{hook_name}' exited with code {exit_code}. "
            f"Log: {log_path}"
        )


def _scoped_env(
    slug: str,
    plugin_dir: Path,
    env_vars_needed: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Build a restricted environment dict for a lifecycle hook subprocess.

    Includes:
    - Whitelisted safe vars from os.environ (_SAFE_ENV_PASSTHROUGH).
    - EVONEXUS_PLUGIN_SLUG and EVONEXUS_PLUGIN_DIR (injected).
    - Any var declared in env_vars_needed (if present in os.environ).

    Args:
        slug: Plugin slug.
        plugin_dir: Absolute path to the plugin's installed directory.
        env_vars_needed: List of additional env var names from plugin manifest.

    Returns:
        Restricted environment dict for subprocess.run(env=...).
    """
    scoped: Dict[str, str] = {}

    # Safe passthrough from current process
    for key in _SAFE_ENV_PASSTHROUGH:
        val = os.environ.get(key)
        if val is not None:
            scoped[key] = val

    # Plugin-specific injected vars
    scoped["EVONEXUS_PLUGIN_SLUG"] = slug
    scoped["EVONEXUS_PLUGIN_DIR"] = str(plugin_dir.resolve())

    # Declared env vars needed by the plugin
    for key in (env_vars_needed or []):
        val = os.environ.get(key)
        if val is not None:
            scoped[key] = val
        else:
            logger.warning(
                "env_vars_needed var '%s' not set in environment (hook may fail)", key
            )

    return scoped


def _ensure_executable(script_path: Path) -> None:
    """Add executable bit if the script is not already executable.

    Tarballs may lose the executable bit during extraction. We add it only
    for the owner (u+x) to avoid granting wider permissions than needed.
    """
    current_mode = script_path.stat().st_mode
    if not (current_mode & stat.S_IXUSR):
        script_path.chmod(current_mode | stat.S_IXUSR)
        logger.debug("Added execute permission to %s", script_path)


def _write_hook_log(
    slug: str,
    hook_name: str,
    timestamp: str,
    script_sha256: str,
    stdout: str,
    stderr: str,
    exit_code: Optional[int],
    timed_out: bool,
) -> Path:
    """Write hook execution log to ADWs/logs/plugins/.

    Returns:
        Path to the written log file.
    """
    PLUGIN_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = PLUGIN_LOGS_DIR / f"{slug}-{hook_name}-{timestamp}.log"

    lines = [
        f"plugin: {slug}",
        f"hook: {hook_name}",
        f"timestamp: {timestamp}",
        f"script_sha256: {script_sha256}",
        f"timed_out: {timed_out}",
        f"exit_code: {exit_code}",
        "--- stdout ---",
        stdout or "(empty)",
        "--- stderr ---",
        stderr or "(empty)",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def run_lifecycle_hook(
    plugin_dir: Path,
    hook_name: str,
    slug: str,
    timeout: int = 60,
    env_vars_needed: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a lifecycle hook script from a plugin's hooks/ directory.

    Looks for `{plugin_dir}/hooks/{hook_name}.sh`. If the script does not
    exist, returns immediately without error (optional hooks are OK).

    Args:
        plugin_dir: Absolute path to the installed plugin directory.
        hook_name: One of: pre-install, post-install, pre-uninstall, post-uninstall.
        slug: Plugin slug (used for log naming and env injection).
        timeout: Seconds before the subprocess is killed (default 60).
        env_vars_needed: Env var names from plugin manifest to pass through.

    Returns:
        Dict with keys:
            ran: bool — whether the script existed and was executed
            exit_code: int | None
            stdout: str
            stderr: str
            log_path: str | None
            script_sha256: str | None

    Raises:
        LifecycleHookError: If the script exits with non-zero code.
        subprocess.TimeoutExpired: If the script exceeds timeout seconds
                                   (process is killed before re-raising).
    """
    script_path = plugin_dir / "hooks" / f"{hook_name}.sh"

    if not script_path.exists():
        logger.debug("No %s hook for plugin '%s' (optional, skipping)", hook_name, slug)
        return {
            "ran": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "log_path": None,
            "script_sha256": None,
        }

    # Ensure script is executable (tarball extraction may strip the bit)
    _ensure_executable(script_path)

    # Compute SHA256 of the script for audit
    script_sha256 = hashlib.sha256(script_path.read_bytes()).hexdigest()

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    env = _scoped_env(slug, plugin_dir, env_vars_needed)

    stdout = ""
    stderr = ""
    exit_code: Optional[int] = None
    timed_out = False
    proc: Optional[subprocess.CompletedProcess] = None

    logger.info("Running lifecycle hook '%s' for plugin '%s' (timeout=%ds)", hook_name, slug, timeout)

    try:
        proc = subprocess.run(
            ["bash", str(script_path)],
            cwd=str(plugin_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode

    except subprocess.TimeoutExpired as exc:
        timed_out = True
        # Collect any partial output
        if exc.stdout:
            stdout = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode(errors="replace")
        if exc.stderr:
            stderr = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode(errors="replace")
        logger.error(
            "Lifecycle hook '%s' for plugin '%s' timed out after %ds — killed",
            hook_name, slug, timeout,
        )
        log_path = _write_hook_log(
            slug, hook_name, timestamp, script_sha256,
            stdout, stderr, exit_code, timed_out=True,
        )
        raise subprocess.TimeoutExpired(
            cmd=exc.cmd, timeout=timeout,
            output=exc.stdout, stderr=exc.stderr,
        )

    log_path = _write_hook_log(
        slug, hook_name, timestamp, script_sha256,
        stdout, stderr, exit_code, timed_out=False,
    )

    if exit_code != 0:
        logger.error(
            "Lifecycle hook '%s' for plugin '%s' exited %d. Log: %s",
            hook_name, slug, exit_code, log_path,
        )
        raise LifecycleHookError(
            hook_name=hook_name,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            log_path=log_path,
        )

    logger.info(
        "Lifecycle hook '%s' for plugin '%s' completed successfully. Log: %s",
        hook_name, slug, log_path,
    )
    return {
        "ran": True,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "log_path": str(log_path),
        "script_sha256": script_sha256,
    }
