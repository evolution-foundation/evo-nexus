"""CLI entry point for EvoNexus Knowledge management.

Usage:
    evonexus init-key      # Generate KNOWLEDGE_MASTER_KEY and append to .env
"""

import sys
from pathlib import Path


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


def cmd_init_key(args: list[str]) -> int:
    """Generate and persist KNOWLEDGE_MASTER_KEY."""
    from cryptography.fernet import Fernet

    env_path = _find_env_file()
    current = _read_env_var(env_path, "KNOWLEDGE_MASTER_KEY")
    if current:
        print("KNOWLEDGE_MASTER_KEY is already set. No-op.")
        print(f"  env file: {env_path}")
        return 0

    key = Fernet.generate_key().decode()
    _append_to_env(
        env_path,
        "KNOWLEDGE_MASTER_KEY",
        key,
        comment=(
            "# Knowledge encryption key — DO NOT delete, DO NOT commit.\n"
            "# Losing this key = losing access to ALL configured connections."
        ),
    )
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
