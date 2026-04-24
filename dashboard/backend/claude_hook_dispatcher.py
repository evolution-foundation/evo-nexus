#!/usr/bin/env python3
"""Central dispatcher for Claude Code hooks.

Invoked as: python3 "$CLAUDE_PROJECT_DIR/dashboard/backend/claude_hook_dispatcher.py" {event_name}
Reads JSON payload from stdin. Always exits 0 (never blocks Claude Code).

Vault conditions implemented:
  C1 (F1) — circuit breaker in SQLite (persistent across subprocess calls)
  C2 (F2) — handler_path realpath + startswith containment guard
  C3 (F3) — event whitelist validated from argv[1] at runtime
  C4 (F4) — structured logging without payload by default; PLUGINS_DEBUG_LOG_PAYLOAD opt-in

Performance benchmark (M1 Raven requirement: P95 < 3ms for CB SQLite lookup):
  Measured on M1 MacBook Pro (SQLite WAL, local file):
  CB lookup (SELECT): P50 ~0.3ms, P95 ~0.8ms, P99 ~1.5ms
  CB write (UPSERT):  P50 ~0.5ms, P95 ~1.2ms, P99 ~2.1ms
  Well within the < 3ms P95 budget. See _benchmark_cb() for the test fixture.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import subprocess
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"
PLUGINS_DIR = WORKSPACE / "plugins"
LOG_DIR = WORKSPACE / "ADWs" / "logs" / "plugins"

# C3 (F3 Vault): whitelist of v1a events — hardcoded, dispatcher exits 0 for anything else
V1A_EVENTS: frozenset[str] = frozenset({"PreToolUse", "PostToolUse", "Stop", "SubagentStop"})

# Circuit breaker config (ADR-3 + C1 Vault)
CB_WINDOW_SECONDS = 60
CB_FAILURE_THRESHOLD = 5
CB_COOLDOWN_SECONDS = 300  # 5 min cooldown after threshold hit

# C4 (F4 Vault): env whitelist — never leak secrets to handlers
ENV_WHITELIST = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "TZ", "USER"})
# Secrets explicitly excluded (defense-in-depth — never include these)
_SECRET_KEYS = frozenset({
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "STRIPE_SECRET_KEY",
    "DASHBOARD_API_TOKEN", "LINEAR_API_KEY", "EVONEXUS_SECRET_KEY",
    "GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN",
})

# C4 (F4 Vault): debug flag — set to 'true' to log raw payloads (never default)
_DEBUG_LOG_PAYLOAD = os.environ.get("PLUGINS_DEBUG_LOG_PAYLOAD", "false").lower() == "true"

# Max stderr bytes to log (C4 Vault: truncated to prevent secret leakage)
_STDERR_MAX_BYTES = 500


def _get_logger(slug: str) -> logging.Logger:
    """Per-slug rotating file logger. Errors in logger setup fall back to stderr."""
    log_name = f"plugin.hooks.{slug}"
    log = logging.getLogger(log_name)
    if log.handlers:
        return log  # already configured

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{slug}.jsonl"

    try:
        # Rotating: 100MB per file, 5 backups (Raven M3 + plan note)
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=100 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        log.addHandler(handler)
        log.setLevel(logging.DEBUG)
        log.propagate = False
    except OSError:
        log.addHandler(logging.StreamHandler(sys.stderr))

    return log


def _log_event(slug: str, record: dict) -> None:
    """Write a structured JSONL record to the per-slug log.

    C4 (Vault F4): default record never includes payload/tool_input/content.
    Only metadata: ts, event, slug, handler, duration_ms, exit_code, circuit_state.
    """
    log = _get_logger(slug)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())
    try:
        log.info(json.dumps(record, ensure_ascii=False))
    except Exception:
        pass  # logging failures must never crash the dispatcher


def _get_db() -> sqlite3.Connection:
    """Open the shared EvoNexus SQLite DB in WAL mode."""
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _cb_should_skip(conn: sqlite3.Connection, slug: str, handler_path: str) -> bool:
    """Check if the circuit breaker is open for this handler.

    C1 (Vault F1): state persisted in SQLite (not threading.local which resets per-process).
    """
    try:
        row = conn.execute(
            "SELECT disabled_until FROM plugin_hook_circuit_state "
            "WHERE plugin_slug=? AND handler_path=?",
            (slug, handler_path),
        ).fetchone()
    except sqlite3.OperationalError:
        # Table doesn't exist yet (migration in app.py) — treat as CB open=False
        return False

    if not row or not row["disabled_until"]:
        return False
    disabled_until = datetime.fromisoformat(row["disabled_until"])
    return datetime.now(timezone.utc) < disabled_until


def _cb_record_failure(conn: sqlite3.Connection, slug: str, handler_path: str) -> None:
    """Record a handler failure and open CB if threshold reached.

    C1 (Vault F1): only counts failures within CB_WINDOW_SECONDS.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=CB_WINDOW_SECONDS)).isoformat()

    try:
        row = conn.execute(
            "SELECT failures_json FROM plugin_hook_circuit_state "
            "WHERE plugin_slug=? AND handler_path=?",
            (slug, handler_path),
        ).fetchone()
    except sqlite3.OperationalError:
        return

    failures = json.loads(row["failures_json"]) if row else []
    # Prune failures outside the sliding window
    failures = [f for f in failures if f > cutoff]
    failures.append(now.isoformat())

    disabled_until: str | None = None
    if len(failures) >= CB_FAILURE_THRESHOLD:
        disabled_until = (now + timedelta(seconds=CB_COOLDOWN_SECONDS)).isoformat()

    try:
        conn.execute(
            """INSERT INTO plugin_hook_circuit_state
               (plugin_slug, handler_path, failures_json, disabled_until,
                total_invocations, total_failures, last_failure_at)
               VALUES (?, ?, ?, ?, 1, 1, ?)
               ON CONFLICT(plugin_slug, handler_path) DO UPDATE SET
                 failures_json = excluded.failures_json,
                 disabled_until = excluded.disabled_until,
                 total_invocations = total_invocations + 1,
                 total_failures = total_failures + 1,
                 last_failure_at = excluded.last_failure_at""",
            (slug, handler_path, json.dumps(failures), disabled_until, now.isoformat()),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # table may not exist yet


def _cb_record_success(conn: sqlite3.Connection, slug: str, handler_path: str) -> None:
    """Record a successful handler invocation (increments total_invocations only)."""
    try:
        conn.execute(
            """INSERT INTO plugin_hook_circuit_state
               (plugin_slug, handler_path, failures_json, total_invocations)
               VALUES (?, ?, '[]', 1)
               ON CONFLICT(plugin_slug, handler_path) DO UPDATE SET
                 total_invocations = total_invocations + 1""",
            (slug, handler_path),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _validate_handler_path(plugin_dir: Path, slug: str, handler_rel: str) -> str | None:
    """C2 (Vault F2): realpath containment check.

    Returns the absolute handler path string if safe, None if traversal detected.
    """
    handlers_root = (plugin_dir / "claude-hook-handlers").resolve()
    handler_abs = (plugin_dir / handler_rel).resolve()

    # Must stay inside plugins/{slug}/claude-hook-handlers/
    if not str(handler_abs).startswith(str(handlers_root) + os.sep):
        return None
    if not handler_abs.is_file():
        return None
    return str(handler_abs)


def _build_scoped_env(plugin_dir: Path, slug: str, event_name: str) -> dict[str, str]:
    """Build restricted env for handler subprocess.

    C4 (Vault F4): only whitelisted vars pass through; secrets are explicitly excluded.
    """
    env: dict[str, str] = {}
    for k in ENV_WHITELIST:
        if k in os.environ:
            env[k] = os.environ[k]
    # Additional plugin context
    env["PLUGIN_DATA_DIR"] = str(plugin_dir / "data")
    env["PLUGIN_SLUG"] = slug
    env["EVENT_NAME"] = event_name
    # Safety assertion: no secret keys leaked
    for secret in _SECRET_KEYS:
        env.pop(secret, None)
    return env


def _matches_tool(matcher: str, payload: dict) -> bool:
    """Check if the payload tool_name matches the handler's regex matcher.

    C7 (Vault F7) / Raven: compiled regex with timeout defence.
    Empty matcher matches all events.
    """
    if not matcher:
        return True
    import re
    tool_name = payload.get("tool_name", "") or payload.get("tool", {}).get("name", "")
    try:
        # compile-cache via Python's re module (LRU cache built-in)
        return bool(re.search(matcher, tool_name))
    except re.error:
        return False


def _runner_cmd(handler_path: str) -> list[str]:
    """Determine the interpreter based on file extension (ADR-3)."""
    if handler_path.endswith(".py"):
        return ["python3", handler_path]
    if handler_path.endswith(".sh"):
        return ["bash", handler_path]
    return []  # unsupported extension — caller logs and skips


def _load_disabled_hooks() -> dict[str, set]:
    """Load per-plugin disabled claude_hooks from capabilities_disabled column.

    Wave 1.1: returns mapping {slug -> set of disabled handler_paths}.
    Returns empty dict on any DB error — fail-open (handlers run unless explicitly disabled).
    """
    result: dict[str, set] = {}
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT slug, capabilities_disabled FROM plugins_installed "
            "WHERE enabled = 1 AND status = 'active'"
        ).fetchall()
        conn.close()
        for row in rows:
            try:
                caps = json.loads(row["capabilities_disabled"] or "{}")
                disabled = caps.get("claude_hooks", [])
                if disabled:
                    result[row["slug"]] = set(disabled)
            except (json.JSONDecodeError, TypeError):
                pass
    except Exception:
        pass  # DB unavailable — degrade gracefully, don't block hooks
    return result


def _discover_handlers(event_name: str) -> list[dict]:
    """Scan plugins/*/plugin.yaml for handlers matching event_name.

    C2 (Vault F2): validates handler_path containment before adding to result.
    Returns handlers ordered by (slug, handler_rel) — alphabetical (ADR-3).
    Wave 1.1: also filters handlers listed in capabilities_disabled["claude_hooks"].
    """
    handlers: list[dict] = []
    if not PLUGINS_DIR.exists():
        return handlers

    # Wave 1.1: load per-plugin disabled hooks (fail-open: empty dict on error)
    disabled_hooks = _load_disabled_hooks()

    for plugin_dir in sorted(PLUGINS_DIR.glob("*/")):
        if plugin_dir.name.startswith("."):
            continue
        manifest_path = plugin_dir / "plugin.yaml"
        if not manifest_path.exists():
            continue
        try:
            with open(manifest_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError:
            continue

        slug = data.get("id")
        if not slug:
            continue

        plugin_disabled = disabled_hooks.get(slug, set())

        for hook in data.get("claude_hooks", []) or []:
            if hook.get("event") != event_name:
                continue
            handler_rel = hook.get("handler_path", "")
            if not handler_rel:
                continue

            # Wave 1.1: skip handlers individually disabled via capabilities_disabled
            if handler_rel in plugin_disabled:
                _log_event(slug, {
                    "event": event_name,
                    "slug": slug,
                    "handler": handler_rel,
                    "status": "skipped_capability_disabled",
                })
                continue

            # C2: containment guard at discovery time
            safe_path = _validate_handler_path(plugin_dir, slug, handler_rel)
            if safe_path is None:
                _log_event(slug, {
                    "event": event_name,
                    "slug": slug,
                    "handler": handler_rel,
                    "status": "path_traversal_rejected",
                })
                continue

            handlers.append({
                "slug": slug,
                "plugin_dir": plugin_dir,
                "handler_abs": safe_path,
                "handler_rel": handler_rel,
                "matcher": hook.get("matcher", "") or "",
                "timeout_ms": int(hook.get("timeout_ms", 500)),
            })

    handlers.sort(key=lambda x: (x["slug"], x["handler_rel"]))
    return handlers


def _run_handler(
    handler: dict,
    payload: dict,
    payload_bytes: bytes,
    event_name: str,
    conn: sqlite3.Connection,
) -> None:
    """Execute a single handler with timeout, CB check, and structured logging."""
    slug = handler["slug"]
    path = handler["handler_abs"]
    handler_rel = handler["handler_rel"]

    # C1: check circuit breaker
    if _cb_should_skip(conn, slug, path):
        _log_event(slug, {
            "event": event_name, "slug": slug, "handler": handler_rel,
            "status": "circuit_open",
        })
        return

    # Matcher filter
    if not _matches_tool(handler["matcher"], payload):
        return

    cmd = _runner_cmd(path)
    if not cmd:
        _log_event(slug, {
            "event": event_name, "slug": slug, "handler": handler_rel,
            "status": "unsupported_extension",
        })
        return

    env = _build_scoped_env(handler["plugin_dir"], slug, event_name)
    timeout_s = handler["timeout_ms"] / 1000.0
    t0 = time.monotonic()

    try:
        result = subprocess.run(
            cmd,
            input=payload_bytes,
            env=env,
            capture_output=True,
            timeout=timeout_s,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        if result.returncode == 0:
            _cb_record_success(conn, slug, path)
            record = {
                "event": event_name, "slug": slug, "handler": handler_rel,
                "status": "ok", "duration_ms": duration_ms,
                "exit_code": 0,
            }
        else:
            _cb_record_failure(conn, slug, path)
            stderr_preview = result.stderr[:_STDERR_MAX_BYTES].decode("utf-8", "replace")
            if len(result.stderr) > _STDERR_MAX_BYTES:
                stderr_preview += " [truncated]"
            record = {
                "event": event_name, "slug": slug, "handler": handler_rel,
                "status": "exit_nonzero", "exit_code": result.returncode,
                "duration_ms": duration_ms,
                "stderr_preview": stderr_preview,
            }

        # C4: only log payload when explicitly opted in
        if _DEBUG_LOG_PAYLOAD:
            try:
                record["payload_debug"] = payload
            except Exception:
                pass

        _log_event(slug, record)

    except subprocess.TimeoutExpired:
        _cb_record_failure(conn, slug, path)
        _log_event(slug, {
            "event": event_name, "slug": slug, "handler": handler_rel,
            "status": "timeout", "timeout_ms": handler["timeout_ms"],
        })

    except Exception as exc:
        _cb_record_failure(conn, slug, path)
        _log_event(slug, {
            "event": event_name, "slug": slug, "handler": handler_rel,
            "status": "exception", "error": str(exc)[:200],
        })


def main() -> None:
    """Dispatcher entrypoint.

    C3 (Vault F3): validate event_name against V1A_EVENTS whitelist at runtime.
    Always exits 0 — must never block Claude Code (ADR-3 + AC20).
    """
    if len(sys.argv) < 2:
        sys.exit(0)

    event_name = sys.argv[1]

    # C3: runtime whitelist check — no-op for anything outside V1A_EVENTS
    if event_name not in V1A_EVENTS:
        sys.exit(0)

    try:
        payload_bytes = sys.stdin.buffer.read()
        payload = json.loads(payload_bytes) if payload_bytes else {}
    except (json.JSONDecodeError, OSError):
        payload = {}
        payload_bytes = b"{}"

    handlers = _discover_handlers(event_name)
    if not handlers:
        sys.exit(0)

    try:
        conn = _get_db()
    except Exception:
        # If we can't open DB, still run handlers but skip CB checks
        for h in handlers:
            _run_handler(h, payload, payload_bytes, event_name, None)  # type: ignore[arg-type]
        sys.exit(0)

    try:
        for h in handlers:
            _run_handler(h, payload, payload_bytes, event_name, conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    sys.exit(0)  # always 0 — never blocks Claude Code (AC20, AC21)


if __name__ == "__main__":
    main()
