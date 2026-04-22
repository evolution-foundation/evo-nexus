"""CLI entry point for EvoNexus Knowledge management.

Usage:
    evonexus init-key      # Generate KNOWLEDGE_MASTER_KEY and append to .env

Also exports :func:`ensure_master_key` so that higher-level installers
(setup.py, entrypoint.sh) can invoke the same idempotent generator
without going through the CLI surface.
"""

import sys
from pathlib import Path
from typing import Tuple


def _find_env_file() -> Path:
    """Locate the .env file used by EvoNexus.

    Search order:
      1. WORKSPACE_ROOT/.env  (two levels up from this file's package root)
      2. Current working directory .env
    Returns the first existing file found, or the workspace root path if none exist.
    """
    # dashboard/backend/knowledge/cli.py  →  go up 3 levels to workspace root
    workspace = Path(__file__).resolve().parent.parent.parent.parent
    candidate = workspace / ".env"
    if candidate.exists():
        return candidate
    cwd_candidate = Path.cwd() / ".env"
    if cwd_candidate.exists():
        return cwd_candidate
    # Default: workspace root (will be created)
    return candidate


def _read_env_var(env_path: Path, key: str) -> str:
    """Return the value of *key* in *env_path*, or empty string if absent."""
    if not env_path.exists():
        return ""
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped[len(key) + 1:].strip().strip('"').strip("'")
    return ""


def _append_to_env(env_path: Path, key: str, value: str, comment: str = "") -> None:
    """Append a KEY=value pair (with optional preceding comment) to *env_path*."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    content = env_path.read_text() if env_path.exists() else ""
    # Ensure a trailing newline before appending
    if content and not content.endswith("\n"):
        content += "\n"
    if comment:
        content += "\n" + comment + "\n"
    content += f"{key}={value}\n"
    env_path.write_text(content)
    try:
        env_path.chmod(0o600)
    except OSError:
        pass  # Windows or permissions issue — best-effort


_MASTER_KEY_NAME = "KNOWLEDGE_MASTER_KEY"
_MASTER_KEY_COMMENT = (
    "# Knowledge encryption key — DO NOT delete, DO NOT commit.\n"
    "# Losing this key = losing access to ALL configured connections."
)


def ensure_master_key(env_path: Path) -> Tuple[bool, str]:
    """Ensure *env_path* contains a valid ``KNOWLEDGE_MASTER_KEY``.

    Idempotent. Generates a Fernet key only when one is not already set.

    Args:
        env_path: absolute path to the ``.env`` file. Parent is created if
            missing. If the file itself does not exist, it is created.

    Returns:
        ``(was_generated, key_value)`` — ``was_generated`` is ``True`` when
        a new key was written on this call, ``False`` when an existing one
        was found. ``key_value`` is the active key in either case (useful
        for logging truncated previews).

    Raises:
        RuntimeError: if the ``cryptography`` package is not installed.
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "cryptography package is not installed. "
            "Run: pip install cryptography  (or: uv sync)"
        ) from exc

    current = _read_env_var(env_path, _MASTER_KEY_NAME)
    if current:
        return False, current

    key = Fernet.generate_key().decode()
    _append_to_env(env_path, _MASTER_KEY_NAME, key, comment=_MASTER_KEY_COMMENT)
    return True, key


def cmd_init_key(args: list[str]) -> int:
    """Generate and persist KNOWLEDGE_MASTER_KEY (CLI wrapper).

    Thin wrapper over :func:`ensure_master_key` that prints user-facing
    output. Exit code is always 0 — both "generated" and "already present"
    are success cases.
    """
    env_path = _find_env_file()
    was_generated, _key = ensure_master_key(env_path)
    if not was_generated:
        print("KNOWLEDGE_MASTER_KEY is already set. No-op.")
        print(f"  env file: {env_path}")
        return 0

    print(f"KNOWLEDGE_MASTER_KEY generated and written to: {env_path}")
    print(
        "WARNING: Back up your .env file. "
        "Losing this key means losing access to all encrypted connections."
    )
    return 0


def main() -> None:
    """Main entry point dispatched by `evonexus <command>`."""
    args = sys.argv[1:]
    if not args:
        print("Usage: evonexus <command> [options]")
        print("Commands:")
        print("  init-key    Generate KNOWLEDGE_MASTER_KEY and append to .env")
        sys.exit(0)

    command = args[0]
    rest = args[1:]

    if command == "init-key":
        sys.exit(cmd_init_key(rest))
    else:
        print(f"Unknown command: {command!r}", file=sys.stderr)
        print("Run `evonexus` with no arguments to see available commands.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
