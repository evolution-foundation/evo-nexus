"""Wave 2.3 — Atomic read/write of ~/.claude.json for MCP server injection.

ADR decisions implemented:
  #1  Effective name: plugin-{slug}-{server.name}
  #2  Target path: projects[WORKSPACE_ABS].mcpServers
  #3  Atomic write: flock → backup → mutate → tmp → re-parse → os.replace → verify → unlock
  #4  Ownership in DB (manifest_json["mcp_servers_installed"]), zero custom fields in ~/.claude.json
  #5  Global flock on ~/.claude.json.evonexus.lock (blocks, does not fail)
  #7  Uninstall idempotent: match effective_name + args_hash[:16]; drift → skip + audit log
  #8  Update: tudo-ou-nada delta via apply_mcp_delta()

Restore semantics (advisor refinement):
  Restore ONLY on json.JSONDecodeError of the tmp file or the post-write verify read.
  Do NOT restore when expected MCPs are absent in post-write verify — that would clobber
  concurrent Claude CLI writes to tipsHistory/numStartups etc.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WORKSPACE = Path(__file__).resolve().parent.parent.parent
CLAUDE_JSON = Path.home() / ".claude.json"
_LOCK_FILE = Path.home() / ".claude.json.evonexus.lock"
_BACKUP_PREFIX = ".claude.json.evonexus-backup-"
_TMP_PREFIX = ".claude.json.evonexus-tmp-"
_MAX_BACKUPS = 10

# Interpolation: ${WORKSPACE}, ${PLUGIN_DIR}, ${ENV:NAME}
_INTERP_RE = re.compile(r"\$\{([^}]+)\}")

# Shell metacharacters must not appear in resolved interpolation values
_SHELL_METACHAR_RE = re.compile(r"[;&|<>`\\]")

# Allowed MCP commands (whitelist)
_ALLOWED_COMMANDS = frozenset({"npx", "node", "python", "python3", "uv", "uvx", "deno"})


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

def _interpolate(value: str, workspace: Path, plugin_dir: Path, env_file: Path | None) -> str:
    """Resolve ${WORKSPACE}, ${PLUGIN_DIR}, and ${ENV:NAME} tokens.

    Pure string-replace — no shell invocation.

    Raises:
        ValueError: If ${ENV:NAME} references a key absent from .env file.
        ValueError: If the resolved value contains shell metacharacters.
    """
    env_vars: dict[str, str] | None = None

    def _replace(m: re.Match) -> str:
        nonlocal env_vars
        token = m.group(1)
        if token == "WORKSPACE":
            return str(workspace.resolve())
        if token == "PLUGIN_DIR":
            return str(plugin_dir.resolve())
        if token.startswith("ENV:"):
            key = token[4:]
            if env_vars is None:
                env_vars = _load_env_file(env_file)
            if key not in env_vars:
                raise ValueError(
                    f"MCP interpolation ${{ENV:{key}}} — key '{key}' not found "
                    f"in .env file ({env_file})."
                )
            return env_vars[key]
        # Unknown token — leave as-is (safe: just means the literal string)
        return m.group(0)

    resolved = _INTERP_RE.sub(_replace, value)

    # Post-interpolation metachar check (advisor refinement)
    if _SHELL_METACHAR_RE.search(resolved):
        raise ValueError(
            f"Resolved MCP value '{resolved}' contains shell metacharacter. "
            "Characters [;&|<>`\\] are not allowed after interpolation."
        )

    return resolved


def _load_env_file(env_file: Path | None) -> dict[str, str]:
    """Parse KEY=VALUE pairs from a .env file. Returns empty dict if file absent."""
    result: dict[str, str] = {}
    if env_file is None or not env_file.exists():
        return result
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# MCP entry construction
# ---------------------------------------------------------------------------

def _build_mcp_entry(
    server_dict: dict[str, Any],
    workspace: Path,
    plugin_dir: Path,
    env_file: Path | None,
) -> dict[str, Any]:
    """Build the ~/.claude.json mcpServers entry dict for one MCP server.

    Resolves interpolations. Returns {command, args, env} (schema-clean — no extra keys).
    """
    command = server_dict["command"]
    if command not in _ALLOWED_COMMANDS:
        raise ValueError(
            f"MCP command '{command}' not in whitelist {sorted(_ALLOWED_COMMANDS)}."
        )

    resolved_args = [
        _interpolate(a, workspace, plugin_dir, env_file) for a in server_dict.get("args", [])
    ]
    resolved_env = {
        k: _interpolate(v, workspace, plugin_dir, env_file)
        for k, v in server_dict.get("env", {}).items()
    }

    entry: dict[str, Any] = {"command": command, "args": resolved_args}
    if resolved_env:
        entry["env"] = resolved_env
    return entry


def _args_hash(server_dict: dict[str, Any]) -> str:
    """16-char prefix of SHA256 over {command, args, env_keys} — for drift detection."""
    fingerprint = {
        "command": server_dict.get("command"),
        "args": server_dict.get("args", []),
        "env_keys": sorted(server_dict.get("env", {}).keys()),
    }
    return hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True).encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Atomic ~/.claude.json writer
# ---------------------------------------------------------------------------

class _ClaudeJsonWriter:
    """Context manager that holds the global flock and provides read/write to ~/.claude.json."""

    def __init__(self) -> None:
        self._lock_fd: int | None = None
        self._backup_path: Path | None = None

    def __enter__(self) -> "_ClaudeJsonWriter":
        # Step 1: acquire global flock (LOCK_EX, blocking — concurrent installs queue)
        self._lock_fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_WRONLY, 0o644)
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None

    def read_claude_json(self) -> dict[str, Any]:
        """Read ~/.claude.json, returning empty dict if absent."""
        if not CLAUDE_JSON.exists():
            return {}
        return json.loads(CLAUDE_JSON.read_bytes())

    def write_atomic(self, data: dict[str, Any]) -> Path:
        """Write data atomically to ~/.claude.json.

        Steps (ADR decision #3):
          1. Backup current file (retain last _MAX_BACKUPS)
          2. json.dump to tmp in same directory
          3. Re-parse tmp → abort if invalid JSON
          4. os.replace (POSIX atomic rename)
          5. Post-write verify (re-read); restore backup only on JSONDecodeError

        Returns the backup path created before the write.
        """
        # Step 2 (of ADR): backup — stored alongside CLAUDE_JSON (not hardcoded to ~/)
        epoch_ms = int(time.time() * 1000)
        config_dir = CLAUDE_JSON.parent
        backup_path = config_dir / f"{_BACKUP_PREFIX}{epoch_ms}"
        if CLAUDE_JSON.exists():
            shutil.copy2(CLAUDE_JSON, backup_path)
            self._backup_path = backup_path
            self._prune_backups()
        else:
            self._backup_path = None

        tmp_path = config_dir / f"{_TMP_PREFIX}{os.getpid()}-{epoch_ms}"
        try:
            # Step 3 (of ADR): write to tmp
            tmp_content = json.dumps(data, indent=2, ensure_ascii=False)
            tmp_path.write_text(tmp_content, encoding="utf-8")

            # Step 4 (of ADR): re-parse tmp to verify JSON validity before replacing
            try:
                json.loads(tmp_path.read_bytes())
            except json.JSONDecodeError as exc:
                # Abort — tmp is corrupt; do not replace original
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"MCP config tmp write produced invalid JSON: {exc}. "
                    f"Original ~/.claude.json was not modified."
                ) from exc

            # Step 5 (of ADR): atomic rename
            os.replace(tmp_path, CLAUDE_JSON)

            # Step 6 (of ADR): post-write verify — restore only on JSONDecodeError
            try:
                json.loads(CLAUDE_JSON.read_bytes())
            except json.JSONDecodeError as exc:
                logger.error("Post-write verify failed — restoring backup: %s", exc)
                if self._backup_path and self._backup_path.exists():
                    os.replace(self._backup_path, CLAUDE_JSON)
                raise RuntimeError(
                    f"~/.claude.json was corrupted after write. "
                    f"Backup restored from {self._backup_path}."
                ) from exc

        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        return backup_path if self._backup_path else Path("/dev/null")

    def _prune_backups(self) -> None:
        """Keep only the last _MAX_BACKUPS backup files."""
        config_dir = CLAUDE_JSON.parent
        backups = sorted(
            config_dir.glob(f"{_BACKUP_PREFIX}*"),
            key=lambda p: p.stat().st_mtime,
        )
        for old in backups[:-_MAX_BACKUPS]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_mcp_servers(
    slug: str,
    mcp_servers: list[dict[str, Any]],
    *,
    workspace: Path | None = None,
    env_file: Path | None = None,
) -> list[dict[str, Any]]:
    """Inject MCP servers from a plugin into projects[WORKSPACE].mcpServers in ~/.claude.json.

    Args:
        slug:        Plugin slug (e.g. "pm-essentials").
        mcp_servers: List of raw server dicts from manifest (name, command, args, env).
        workspace:   Absolute workspace path. Defaults to WORKSPACE constant.
        env_file:    Path to .env file for ${ENV:NAME} interpolation.

    Returns:
        List of installed server records:
            [{effective_name, command, args_hash, env_keys}]

    Raises:
        RuntimeError: On any write failure (backup is preserved).
    """
    if not mcp_servers:
        return []

    ws = (workspace or WORKSPACE).resolve()
    plugin_dir = ws / "plugins" / slug
    _env_file = env_file or (ws / ".env")
    ws_key = str(ws)

    installed: list[dict[str, Any]] = []

    with _ClaudeJsonWriter() as writer:
        data = writer.read_claude_json()

        # Scaffold missing keys (ADR decision #2)
        projects = data.setdefault("projects", {})
        project_entry = projects.setdefault(ws_key, {})
        mcp_map: dict[str, Any] = project_entry.setdefault("mcpServers", {})

        for server in mcp_servers:
            effective_name = f"plugin-{slug}-{server['name']}"
            entry = _build_mcp_entry(server, ws, plugin_dir, _env_file)
            mcp_map[effective_name] = entry
            installed.append({
                "effective_name": effective_name,
                "command": server["command"],
                "args_hash": _args_hash(server),
                "env_keys": sorted(server.get("env", {}).keys()),
            })

        writer.write_atomic(data)
        logger.info(
            "add_mcp_servers: injected %d MCP entries for plugin '%s': %s",
            len(installed),
            slug,
            [r["effective_name"] for r in installed],
        )

    return installed


def remove_mcp_servers(
    slug: str,
    installed_records: list[dict[str, Any]],
    *,
    workspace: Path | None = None,
) -> dict[str, str]:
    """Remove plugin MCP entries from projects[WORKSPACE].mcpServers in ~/.claude.json.

    ADR decision #7: idempotent. Matches by effective_name + args_hash[:16].
    Drift (entry absent or hash mismatch) → skip + return audit info.

    Args:
        slug:              Plugin slug.
        installed_records: Records from manifest_json["mcp_servers_installed"].
        workspace:         Absolute workspace path.

    Returns:
        Dict {effective_name: "removed"|"absent"|"drift_detected"} for audit log.
    """
    if not installed_records:
        return {}

    ws = (workspace or WORKSPACE).resolve()
    ws_key = str(ws)
    audit: dict[str, str] = {}

    with _ClaudeJsonWriter() as writer:
        data = writer.read_claude_json()
        mcp_map: dict[str, Any] = (
            data.get("projects", {})
                .get(ws_key, {})
                .get("mcpServers", {})
        )

        changed = False
        for record in installed_records:
            effective_name = record.get("effective_name", "")
            saved_hash = record.get("args_hash", "")

            if effective_name not in mcp_map:
                logger.info(
                    "remove_mcp_servers: '%s' absent from ~/.claude.json — skipping",
                    effective_name,
                )
                audit[effective_name] = "absent"
                continue

            # Reconstruct current args_hash from the live entry for comparison
            live_entry = mcp_map[effective_name]
            live_fingerprint = {
                "command": live_entry.get("command"),
                "args": live_entry.get("args", []),
                "env_keys": sorted(live_entry.get("env", {}).keys()),
            }
            live_hash = hashlib.sha256(
                json.dumps(live_fingerprint, sort_keys=True).encode()
            ).hexdigest()[:16]

            if live_hash != saved_hash:
                logger.warning(
                    "remove_mcp_servers: drift_detected for '%s' "
                    "(saved hash %s, live hash %s) — not removing",
                    effective_name, saved_hash, live_hash,
                )
                audit[effective_name] = "drift_detected"
                continue

            del mcp_map[effective_name]
            audit[effective_name] = "removed"
            changed = True

        if changed:
            writer.write_atomic(data)
            logger.info(
                "remove_mcp_servers: removed MCP entries for plugin '%s': %s",
                slug,
                [k for k, v in audit.items() if v == "removed"],
            )

    return audit


def apply_mcp_delta(
    slug: str,
    old_servers: list[dict[str, Any]],
    new_servers: list[dict[str, Any]],
    old_installed_records: list[dict[str, Any]],
    *,
    workspace: Path | None = None,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Update MCP entries in ~/.claude.json as a tudo-ou-nada delta (ADR decision #8).

    Computes added/removed/modified sets by server ``name``, then applies all
    changes inside a single flock window. On any failure the backup created at
    the start of the write is restored (JSONDecodeError path only — see module
    docstring for advisor refinement).

    Args:
        slug:                 Plugin slug.
        old_servers:          mcp_servers list from the OLD manifest.
        new_servers:          mcp_servers list from the NEW manifest.
        old_installed_records: mcp_servers_installed from DB for old manifest.
        workspace:            Absolute workspace path.
        env_file:             Path to .env file.

    Returns:
        {"added": [...], "removed": {...audit...}, "modified": [...]}
    """
    ws = (workspace or WORKSPACE).resolve()
    plugin_dir = ws / "plugins" / slug
    _env_file = env_file or (ws / ".env")
    ws_key = str(ws)

    old_by_name = {s["name"]: s for s in old_servers}
    new_by_name = {s["name"]: s for s in new_servers}

    added_names = set(new_by_name) - set(old_by_name)
    removed_names = set(old_by_name) - set(new_by_name)
    modified_names = {
        name for name in set(old_by_name) & set(new_by_name)
        if old_by_name[name] != new_by_name[name]
    }

    # Build lookup: name → installed_record (for drift check on removes/modifies)
    installed_by_name: dict[str, dict] = {}
    for rec in old_installed_records:
        eff = rec.get("effective_name", "")
        # effective_name is "plugin-{slug}-{name}"
        prefix = f"plugin-{slug}-"
        if eff.startswith(prefix):
            name = eff[len(prefix):]
            installed_by_name[name] = rec

    result: dict[str, Any] = {"added": [], "removed": {}, "modified": []}

    with _ClaudeJsonWriter() as writer:
        data = writer.read_claude_json()
        projects = data.setdefault("projects", {})
        project_entry = projects.setdefault(ws_key, {})
        mcp_map: dict[str, Any] = project_entry.setdefault("mcpServers", {})

        # --- Removes (including old side of modifieds) ---
        names_to_remove = removed_names | modified_names
        for name in names_to_remove:
            effective_name = f"plugin-{slug}-{name}"
            record = installed_by_name.get(name)
            if record:
                saved_hash = record.get("args_hash", "")
                if effective_name in mcp_map:
                    live_entry = mcp_map[effective_name]
                    live_fp = {
                        "command": live_entry.get("command"),
                        "args": live_entry.get("args", []),
                        "env_keys": sorted(live_entry.get("env", {}).keys()),
                    }
                    live_hash = hashlib.sha256(
                        json.dumps(live_fp, sort_keys=True).encode()
                    ).hexdigest()[:16]
                    if live_hash != saved_hash and name not in modified_names:
                        result["removed"][effective_name] = "drift_detected"
                        continue
                    del mcp_map[effective_name]
                    if name in removed_names:
                        result["removed"][effective_name] = "removed"
            elif effective_name in mcp_map:
                del mcp_map[effective_name]
                if name in removed_names:
                    result["removed"][effective_name] = "removed"

        # --- Adds (including new side of modifieds) ---
        names_to_add = added_names | modified_names
        for name in names_to_add:
            server = new_by_name[name]
            effective_name = f"plugin-{slug}-{name}"
            entry = _build_mcp_entry(server, ws, plugin_dir, _env_file)
            mcp_map[effective_name] = entry
            record = {
                "effective_name": effective_name,
                "command": server["command"],
                "args_hash": _args_hash(server),
                "env_keys": sorted(server.get("env", {}).keys()),
            }
            if name in added_names:
                result["added"].append(record)
            else:
                result["modified"].append(record)

        writer.write_atomic(data)

    return result


def verify_mcp_servers(
    slug: str,
    installed_records: list[dict[str, Any]],
    *,
    workspace: Path | None = None,
) -> dict[str, str]:
    """Check that installed MCP entries are present in ~/.claude.json.

    Used by plugin-health check (ADR decision #7 follow-up).

    Returns:
        Dict {effective_name: "ok"|"absent"|"drift_detected"}
    """
    ws = (workspace or WORKSPACE).resolve()
    ws_key = str(ws)
    status: dict[str, str] = {}

    if not CLAUDE_JSON.exists():
        return {r.get("effective_name", ""): "absent" for r in installed_records}

    try:
        data = json.loads(CLAUDE_JSON.read_bytes())
    except json.JSONDecodeError:
        return {r.get("effective_name", ""): "absent" for r in installed_records}

    mcp_map: dict[str, Any] = (
        data.get("projects", {})
            .get(str(ws), {})
            .get("mcpServers", {})
    )

    for record in installed_records:
        effective_name = record.get("effective_name", "")
        saved_hash = record.get("args_hash", "")

        if effective_name not in mcp_map:
            status[effective_name] = "absent"
            continue

        live_entry = mcp_map[effective_name]
        live_fp = {
            "command": live_entry.get("command"),
            "args": live_entry.get("args", []),
            "env_keys": sorted(live_entry.get("env", {}).keys()),
        }
        live_hash = hashlib.sha256(
            json.dumps(live_fp, sort_keys=True).encode()
        ).hexdigest()[:16]

        if live_hash != saved_hash:
            status[effective_name] = "drift_detected"
        else:
            status[effective_name] = "ok"

    return status
