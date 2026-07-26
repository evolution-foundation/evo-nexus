"""Heartbeat Runner — 9-step proactive agent protocol.

CLI usage:
    python heartbeat_runner.py --heartbeat-id atlas-4h [--run-id <uuid>]

Each run:
1. Load identity  — read .claude/agents/{agent}.md
2. Check approvals — query approvals table (stub in F1.1)
3. Query inbox     — query tickets assigned to agent (stub in F1.1)
4. Pick priority   — apply decision_prompt with context
5. Atomic checkout — lock task (stub in F1.1, real in F1.3)
6. Assemble context — identity + goal chain (stub in F1.1)
7. Work            — invoke Claude via subprocess with max_turns + timeout
8. Persist status  — write heartbeat_runs + JSONL log
9. Release checkout — unlock task (stub in F1.1)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from secret_redaction import redact_secrets

# Workspace root
WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"
LOGS_DIR = WORKSPACE / "ADWs" / "logs" / "heartbeats"
AGENTS_DIR = WORKSPACE / ".claude" / "agents"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_db():
    """Return a SQLAlchemy Connection (replaces raw sqlite3.connect)."""
    from db.engine import get_engine
    engine = get_engine()
    conn = engine.connect()
    return conn


def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy Row or sqlite3.Row to a plain dict."""
    if row is None:
        return {}
    try:
        return dict(row._mapping)
    except AttributeError:
        return dict(row)


def _execute(conn, statement: str, params=None):
    """Execute named-parameter SQL on SQLAlchemy or legacy sqlite3 connections."""
    if hasattr(conn, "dialect"):
        return conn.execute(text(statement), params or {})
    return conn.execute(statement, params or {})


def _row_value(row, key: str):
    """Read a column from either a SQLAlchemy Row or sqlite3.Row."""
    if row is None:
        return None
    try:
        return row._mapping[key]
    except AttributeError:
        return row[key]


def _load_heartbeat(heartbeat_id: str) -> dict | None:
    """Load heartbeat config from DB."""
    conn = _get_db()
    try:
        row = _execute(
            conn, "SELECT * FROM heartbeats WHERE id = :hid", {"hid": heartbeat_id}
        ).fetchone()
        if not row:
            return None
        return dict(row._mapping)
    finally:
        conn.close()


def _upsert_heartbeat_from_yaml(heartbeat_id: str) -> dict | None:
    """Load heartbeat from YAML and mirror to DB if not present."""
    from heartbeat_schema import load_heartbeats_yaml

    cfg = load_heartbeats_yaml()
    hb = next((h for h in cfg.heartbeats if h.id == heartbeat_id), None)
    if not hb:
        return None

    now = _now_iso()
    conn = _get_db()
    try:
        _execute(
            conn, """INSERT INTO heartbeats
               (id, agent, interval_seconds, max_turns, timeout_seconds,
                lock_timeout_seconds, wake_triggers, enabled, goal_id,
                required_secrets, decision_prompt, source_plugin, handler,
                created_at, updated_at)
               VALUES (:id, :agent, :ivs, :mt, :ts, :lts, :wt, :en, :gid, :rs, :dp, :sp, :handler, :cat, :uat)
               ON CONFLICT(id) DO UPDATE SET
                   agent=excluded.agent, interval_seconds=excluded.interval_seconds,
                   max_turns=excluded.max_turns, timeout_seconds=excluded.timeout_seconds,
                   lock_timeout_seconds=excluded.lock_timeout_seconds,
                   wake_triggers=excluded.wake_triggers, enabled=excluded.enabled,
                   goal_id=excluded.goal_id, required_secrets=excluded.required_secrets,
                   decision_prompt=excluded.decision_prompt,
                   source_plugin=excluded.source_plugin, handler=excluded.handler,
                   updated_at=excluded.updated_at""",
            {
                "id": hb.id, "agent": hb.agent, "ivs": hb.interval_seconds, "mt": hb.max_turns,
                "ts": hb.timeout_seconds, "lts": hb.lock_timeout_seconds,
                "wt": json.dumps(hb.wake_triggers), "en": int(hb.enabled), "gid": hb.goal_id,
                "rs": json.dumps(hb.required_secrets), "dp": hb.decision_prompt,
                "sp": hb.source_plugin, "handler": hb.handler,
                "cat": now, "uat": now,
            },
        )
        conn.commit()
        return _load_heartbeat(heartbeat_id)
    finally:
        conn.close()


# ── Step 1: Load identity ─────────────────────────────────────────────────────

def step1_load_identity(agent: str) -> str:
    """Read .claude/agents/{agent}.md and return persona text."""
    agent_file = AGENTS_DIR / f"{agent}.md"
    if not agent_file.exists():
        raise FileNotFoundError(f"Agent file not found: {agent_file}")
    return agent_file.read_text(encoding="utf-8")


# ── Step 2: Check approvals (stub) ───────────────────────────────────────────

def step2_check_approvals(agent: str, conn) -> list:
    """Query pending approvals for this agent. Stub in F1.1."""
    try:
        rows = _execute(
            conn, "SELECT * FROM approvals WHERE assignee_agent = :agent AND status = 'pending' LIMIT 10",
            {"agent": agent},
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        # approvals table may not exist yet
        return []


# ── Step 3: Query inbox (integrated with Tickets F1.3) ───────────────────────

def step3_query_inbox(agent: str, conn) -> list:
    """Query tickets assigned to agent from the tickets table (F1.3)."""
    try:
        rows = _execute(
            conn, """SELECT id, title, description, priority, status, goal_id, project_id, created_at
               FROM tickets
               WHERE assignee_agent = :agent AND status IN ('open','in_progress')
               AND locked_at IS NULL
               ORDER BY
                 CASE priority
                   WHEN 'urgent' THEN 4
                   WHEN 'high' THEN 3
                   WHEN 'medium' THEN 2
                   WHEN 'low' THEN 1
                   ELSE 0
                 END DESC,
                 created_at ASC
               LIMIT 10""",
            {"agent": agent},
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        # tickets table may not exist yet (F1.3 not merged)
        return []


# ── Step 4: Pick priority ─────────────────────────────────────────────────────

def step4_pick_priority(identity: str, approvals: list, inbox: list, decision_prompt: str) -> dict:
    """Build context for the decision call. Returns context dict for step 7."""
    context = {
        "identity_preview": identity[:500],
        "pending_approvals": len(approvals),
        "inbox_count": len(inbox),
        "inbox_preview": inbox[:3] if inbox else [],
        "decision_prompt": decision_prompt,
    }
    return context


# ── Step 5: Atomic checkout ──────────────────────────────────────────────────

def step5_atomic_checkout(task_id: str | None, run_id: str, lock_timeout: int, conn) -> bool:
    """Atomic ticket checkout. Returns True if lock acquired or no ticket to lock."""
    if not task_id:
        return True
    try:
        now = _now_iso()
        cursor = _execute(
            conn,
            """UPDATE tickets
               SET locked_at = :now,
                   locked_by = :run_id,
                   lock_timeout_seconds = :lock_timeout
               WHERE id = :task_id AND locked_at IS NULL""",
            {
                "now": now,
                "run_id": run_id,
                "lock_timeout": lock_timeout,
                "task_id": task_id,
            },
        )
        conn.commit()
        return cursor.rowcount == 1
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[heartbeat_runner] step5 checkout failed: {exc}", flush=True)
        return False


# ── Step 6: Assemble context ──────────────────────────────────────────────────

def step6_assemble_context(identity: str, decision_context: dict, goal_id: str | None) -> str:
    """Build the full prompt for Claude. Injects goal chain (Mission→Project→Goal) if goal_id is set."""
    inbox_summary = ""
    if decision_context.get("inbox_count", 0) > 0:
        inbox_summary = f"\n\nPending inbox items: {decision_context['inbox_count']}"
        if decision_context.get("inbox_preview"):
            inbox_summary += f"\nTop items: {json.dumps(decision_context['inbox_preview'], indent=2)}"

    approvals_summary = ""
    if decision_context.get("pending_approvals", 0) > 0:
        approvals_summary = f"\n\nPending approvals: {decision_context['pending_approvals']}"

    base_prompt = f"""{identity}

---

## Heartbeat Decision Context

{decision_context['decision_prompt']}{inbox_summary}{approvals_summary}

Respond concisely. If you decide to work, describe what you are doing.
If you decide to skip, briefly explain why.
"""

    # Inject goal chain context (F1.2) if goal_id is set
    if goal_id:
        try:
            from goal_context import inject_into_prompt
            return inject_into_prompt(base_prompt, goal_id=goal_id)
        except Exception:
            # goal_context module may not be available or goal not found — fallback gracefully
            pass

    return base_prompt


# ── Step 7: Work — provider-aware normalized runtime ─────────────────────────

def step7_invoke_runtime(
    agent: str,
    prompt: str,
    max_turns: int,
    timeout_seconds: int,
    *,
    heartbeat_id: str,
    requested_profile: str | None = None,
) -> dict:
    """Invoke the configured provider; never select a CLI by local availability."""
    from runtime_service import RuntimeRequest, RuntimeService

    result = RuntimeService().invoke(RuntimeRequest(
        origin_type="heartbeat",
        origin_id=heartbeat_id,
        agent_slug=agent,
        prompt=prompt,
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        requested_profile=requested_profile,
    ))
    return {
        "status": "success" if result.status == "succeeded" else result.status,
        "output": result.output,
        "error": result.error,
        "duration_ms": result.duration_ms,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost_usd": result.cost_usd,
        "provider": result.provider,
        "requested_profile": result.requested_profile,
        "resolved_profile": result.resolved_profile,
        "exit_code": result.exit_code,
        "fallback_from": result.fallback_from,
    }


def step7_invoke_claude(
    agent: str,
    prompt: str,
    max_turns: int,
    timeout_seconds: int,
) -> dict:
    """Invoke Claude/OpenClaude/Hermes via subprocess with hard timeout. Returns result dict."""
    import shutil

    # Try Claude Code first, then OpenClaude, then Hermes (fallback chain)
    cli_bin = None
    for cli in ["claude", "openclaude", "hermes"]:
        if shutil.which(cli):
            cli_bin = cli
            break

    if not cli_bin:
        return {
            "status": "fail",
            "error": "No CLI binary found in PATH (tried: claude, openclaude, hermes)",
            "output": "",
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
        }

    # Build command based on CLI type
    if cli_bin == "hermes":
        cmd = [
            cli_bin,
            "chat",
            "-Q",
            "-q", prompt,
        ]
        if max_turns:
            env = os.environ.copy()
            env["HERMES_MAX_ITERATIONS"] = str(max_turns)
        else:
            env = os.environ.copy()
    else:
        # Claude Code / OpenClaude
        cmd = [
            cli_bin,
            "--print",
            "--max-turns", str(max_turns),
            "--dangerously-skip-permissions",
            "--output-format", "json",
            prompt,  # positional argument — Claude CLI does not have a -p flag
        ]
        env = os.environ.copy()

    start_time = time.time()
    proc = None
    output = ""
    error = None
    status = "success"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(WORKSPACE),
            env=env,
            start_new_session=True,  # new process group for clean kill
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            output = stdout or ""
            if proc.returncode != 0:
                status = "fail"
                error = stderr[:2000] if stderr else f"exit code {proc.returncode}"
        except subprocess.TimeoutExpired:
            # Hard kill the entire process group
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            status = "timeout"
            error = f"Killed after {timeout_seconds}s timeout"

    except Exception as exc:
        status = "fail"
        error = str(exc)

    duration_ms = int((time.time() - start_time) * 1000)

    return {
        "status": status,
        "output": output,
        "error": error,
        "duration_ms": duration_ms,
        "tokens_in": None,   # Claude CLI doesn't expose token counts easily
        "tokens_out": None,
        "cost_usd": None,
    }


# ── Decision parsing ─────────────────────────────────────────────────────────

def parse_decision(output: str) -> tuple[str, dict | None]:
    """Extract {"action":"work"|"skip"} from runtime output. Returns (action, raw_json_or_None)."""
    if not output:
        return "work", None
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
            action = data.get("action", "").lower()
            if action in ("work", "skip"):
                return action, data
        except (json.JSONDecodeError, AttributeError):
            continue
    return "work", None


# ── Step 8: Persist status ────────────────────────────────────────────────────

def step8_persist(run_id: str, heartbeat_id: str, result: dict, trigger_id: str | None, triggered_by: str, prompt_preview: str, conn):
    """Write heartbeat_runs row; in PG mode also write prompt_full to heartbeat_run_prompts.

    ``prompt_preview`` is actually the full prompt text — truncation to 1000 chars
    happens here for the ``heartbeat_runs.prompt_preview`` column only.  The full
    text is stored in ``heartbeat_run_prompts`` (PG mode only).
    """
    from config_store import get_dialect

    now = _now_iso()

    # Upsert run (idempotent: if run_id already exists with status != running, skip)
    existing = _execute(
        conn, "SELECT run_id, status FROM heartbeat_runs WHERE run_id = :rid", {"rid": run_id}
    ).fetchone()

    if existing and _row_value(existing, "status") != "running":
        print(f"[heartbeat_runner] run_id={run_id} already finalized ({_row_value(existing, 'status')}), skipping duplicate persist", flush=True)
        return

    decision_json = result.get("decision_json")
    _execute(
        conn, """INSERT INTO heartbeat_runs
           (run_id, heartbeat_id, trigger_id, started_at, ended_at, duration_ms,
            tokens_in, tokens_out, cost_usd, status, prompt_preview, error, triggered_by,
            decision_action, decision_json, provider, resolved_profile, stdout_tail, stderr_tail, runtime_run_id)
           VALUES (:rid, :hbid, :trid, :sat, :eat, :dms, :ti, :to, :cu, :st, :pp, :err, :tby,
                   :decision_action, :decision_json, :provider, :resolved_profile,
                   :stdout_tail, :stderr_tail, :runtime_run_id)
           ON CONFLICT(run_id) DO UPDATE SET
               ended_at=excluded.ended_at,
               duration_ms=excluded.duration_ms,
               tokens_in=excluded.tokens_in,
               tokens_out=excluded.tokens_out,
               cost_usd=excluded.cost_usd,
               status=excluded.status,
               error=excluded.error,
               decision_action=excluded.decision_action,
               decision_json=excluded.decision_json,
               provider=excluded.provider,
               resolved_profile=excluded.resolved_profile,
               stdout_tail=excluded.stdout_tail,
               stderr_tail=excluded.stderr_tail,
               runtime_run_id=excluded.runtime_run_id""",
        {
            "rid": run_id, "hbid": heartbeat_id, "trid": trigger_id,
            "sat": result.get("started_at", now), "eat": now,
            "dms": result.get("duration_ms"),
            "ti": result.get("tokens_in"), "to": result.get("tokens_out"),
            "cu": result.get("cost_usd"),
            "st": result["status"],
            "pp": prompt_preview[:1000] if prompt_preview else None,
            "err": redact_secrets(result.get("error"), limit=2000) or None,
            "tby": triggered_by,
            "decision_action": result.get("decision_action"),
            "decision_json": redact_secrets(json.dumps(decision_json)) if decision_json else None,
            "provider": result.get("provider"),
            "resolved_profile": result.get("resolved_profile"),
            "stdout_tail": redact_secrets(result.get("output"))[-2000:] or None,
            "stderr_tail": redact_secrets(result.get("error"))[-2000:] or None,
            "runtime_run_id": result.get("runtime_run_id"),
        },
    )

    # PG mode: store the full prompt (no truncation) in the companion table.
    # Both writes share the same transaction for atomicity.
    if get_dialect() == "postgresql" and prompt_preview:
        _execute(
            conn, """
                INSERT INTO heartbeat_run_prompts (run_id, prompt_full, created_at)
                VALUES (:rid, :pf, :now)
                ON CONFLICT (run_id) DO UPDATE SET prompt_full = EXCLUDED.prompt_full
            """,
            {"rid": run_id, "pf": prompt_preview, "now": now},
        )

    conn.commit()

    # SQLite mode: append JSONL log (redundant in PG — heartbeat_runs is the record).
    if get_dialect() != "postgresql":
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = LOGS_DIR / f"{heartbeat_id}-{today}.jsonl"
        log_entry = {
            "run_id": run_id,
            "heartbeat_id": heartbeat_id,
            "agent": result.get("agent", ""),
            "status": result["status"],
            "duration_ms": result.get("duration_ms"),
            "cost_usd": result.get("cost_usd"),
            "triggered_by": triggered_by,
            "ts": now,
            "error": redact_secrets(result.get("error"), limit=2000) or None,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# ── Step 9: Release checkout ──────────────────────────────────────────────────

def step9_release_checkout(task_id: str | None, run_id: str, conn):
    """Release ticket lock. Owner-only: only the run that acquired it can release."""
    if not task_id:
        return
    try:
        _execute(
            conn, """UPDATE tickets SET locked_at = NULL, locked_by = NULL
               WHERE id = :tid AND locked_by = :rid""",
            {"tid": task_id, "rid": run_id},
        )
        conn.commit()
    except Exception:
        pass  # Table may not exist


# ── System heartbeat dispatcher ───────────────────────────────────────────────

# Map heartbeat_id → Python module (relative to heartbeat_runner.py's directory)
_SYSTEM_HEARTBEAT_SCRIPTS: dict[str, str] = {
    "summary-watcher": "summary_watcher",
}


def _run_system_heartbeat(heartbeat_id: str, timeout_seconds: int) -> dict:
    """Run a system heartbeat by importing its module and calling run_watcher().

    Returns result dict compatible with step8_persist expectations.
    """
    import importlib
    import time as _time

    script_module = _SYSTEM_HEARTBEAT_SCRIPTS.get(heartbeat_id)
    if not script_module:
        print(f"[heartbeat_runner] ERROR: no script registered for system heartbeat {heartbeat_id}", flush=True)
        return {"status": "fail", "error": f"no script for {heartbeat_id}", "duration_ms": 0,
                "output": "", "tokens_in": None, "tokens_out": None, "cost_usd": None}

    print(f"[heartbeat_runner] running system heartbeat {heartbeat_id} via {script_module}.run_watcher()", flush=True)
    start = _time.time()
    try:
        mod = importlib.import_module(script_module)
        stats = mod.run_watcher()
        duration_ms = int((_time.time() - start) * 1000)
        return {
            "status": "success",
            "error": None,
            "output": json.dumps(stats),
            "duration_ms": duration_ms,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
        }
    except Exception as exc:
        import traceback
        duration_ms = int((_time.time() - start) * 1000)
        return {
            "status": "fail",
            "error": traceback.format_exc(),
            "output": "",
            "duration_ms": duration_ms,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
        }


# ── Main protocol ─────────────────────────────────────────────────────────────

def run_heartbeat(heartbeat_id: str, triggered_by: str = "manual", trigger_id: str | None = None, run_id: str | None = None):
    """Execute the full 9-step heartbeat protocol."""
    run_id = run_id or str(uuid.uuid4())
    started_at = _now_iso()

    print(f"[heartbeat_runner] START heartbeat_id={heartbeat_id} run_id={run_id} triggered_by={triggered_by}", flush=True)

    # Load config (try DB first, then YAML)
    hb = _load_heartbeat(heartbeat_id)
    if not hb:
        hb = _upsert_heartbeat_from_yaml(heartbeat_id)
    if not hb:
        print(f"[heartbeat_runner] ERROR heartbeat not found: {heartbeat_id}", flush=True)
        sys.exit(1)

    if not hb.get("enabled"):
        print(f"[heartbeat_runner] heartbeat_id={heartbeat_id} is disabled, skipping", flush=True)
        return run_id

    conn = _get_db()

    try:
        # Idempotence check: abort if this run_id already exists in a final state
        existing = _execute(
            conn, "SELECT run_id, status FROM heartbeat_runs WHERE run_id = :rid", {"rid": run_id}
        ).fetchone()
        if existing and _row_value(existing, "status") != "running":
            print(f"[heartbeat_runner] run_id={run_id} already finalized, aborting", flush=True)
            return

        # Insert initial row (so we can track "running" state)
        try:
            _execute(
                conn, """INSERT INTO heartbeat_runs
                   (run_id, heartbeat_id, trigger_id, started_at, status, triggered_by)
                   VALUES (:rid, :hbid, :trid, :sat, 'running', :tby)
                   ON CONFLICT(run_id) DO NOTHING""",
                {"rid": run_id, "hbid": heartbeat_id, "trid": trigger_id, "sat": started_at, "tby": triggered_by},
            )
            conn.commit()
        except Exception as e:
            print(f"[heartbeat_runner] WARNING could not insert initial run row: {e}", flush=True)

        result = {"status": "fail", "error": None, "duration_ms": None, "agent": hb["agent"]}
        full_prompt = ""
        task_id = None

        try:
            # Special case: agent='system' heartbeats run a Python script directly
            # instead of invoking Claude. The script path is resolved by heartbeat id.
            # Handler heartbeats use in-process dispatch (step 7) even when agent='system',
            # so skip this short-circuit when a handler is set.
            if not (hb.get("handler") or "").strip() and hb["agent"] == "system":
                full_prompt = f"[system heartbeat] {heartbeat_id}"
                result = _run_system_heartbeat(heartbeat_id, hb["timeout_seconds"])
                result["agent"] = "system"
                result["started_at"] = started_at
            else:
                # Step 1
                identity = step1_load_identity(hb["agent"])
                print(f"[heartbeat_runner] step1 identity loaded ({len(identity)} chars)", flush=True)

                # Step 2
                approvals = step2_check_approvals(hb["agent"], conn)
                print(f"[heartbeat_runner] step2 approvals={len(approvals)}", flush=True)

                # Step 3
                inbox = step3_query_inbox(hb["agent"], conn)
                print(f"[heartbeat_runner] step3 inbox={len(inbox)}", flush=True)

                # Step 4
                decision_ctx = step4_pick_priority(identity, approvals, inbox, hb["decision_prompt"])
                print(f"[heartbeat_runner] step4 decision context assembled", flush=True)

                # Step 5 occurs only after the decision selects work; a skip never locks a ticket.
                task_id = None

                # Step 6
                full_prompt = step6_assemble_context(identity, decision_ctx, hb.get("goal_id"))
                print(f"[heartbeat_runner] step6 prompt assembled ({len(full_prompt)} chars)", flush=True)

                # Step 7 — in-process handler OR Claude CLI subprocess
                _handler_ref = hb.get("handler") or ""
                if _handler_ref:
                    # Wave 2.2r: in-process Python handler (e.g. plugin_integration_health.tick)
                    # Format: "module_name.function_name"
                    print(f"[heartbeat_runner] step7 in-process handler={_handler_ref}", flush=True)
                    import importlib
                    import time as _time
                    _t0 = _time.time()
                    try:
                        _mod_name, _fn_name = _handler_ref.rsplit(".", 1)
                        _mod = importlib.import_module(_mod_name)
                        _fn = getattr(_mod, _fn_name)
                        _handler_result = _fn()
                        _duration_ms = round((_time.time() - _t0) * 1000)
                        invoke_result = {
                            "status": "success",
                            "error": None,
                            "agent": hb.get("agent", "system"),
                            "duration_ms": _duration_ms,
                            "started_at": started_at,
                            "handler_result": _handler_result,
                        }
                        print(f"[heartbeat_runner] step7 in-process handler done duration_ms={_duration_ms}", flush=True)
                    except Exception as _h_exc:
                        import traceback
                        _duration_ms = round((_time.time() - _t0) * 1000)
                        invoke_result = {
                            "status": "fail",
                            "error": traceback.format_exc(),
                            "agent": hb.get("agent", "system"),
                            "duration_ms": _duration_ms,
                            "started_at": started_at,
                        }
                        print(f"[heartbeat_runner] step7 in-process handler failed: {_h_exc}", flush=True)
                    invoke_result["agent"] = hb.get("agent", "system")
                    invoke_result["started_at"] = started_at
                    result = invoke_result
                else:
                    decision_prompt = f"{full_prompt}\n\nReturn exactly one JSON line with action=work or action=skip. Do not perform work or side effects."
                    decision_result = step7_invoke_runtime(
                        agent=hb["agent"], prompt=decision_prompt, max_turns=hb["max_turns"],
                        timeout_seconds=hb["timeout_seconds"], heartbeat_id=heartbeat_id,
                        requested_profile=hb.get("hermes_profile"),
                    )
                    decision_action, decision_data = parse_decision(decision_result.get("output", ""))
                    if decision_result["status"] != "success" or decision_action == "skip":
                        decision_result.update({"agent": hb["agent"], "started_at": started_at, "decision_action": decision_action, "decision_json": decision_data})
                        result = decision_result
                    else:
                        selected_ticket_id = (decision_data or {}).get("ticket_id") or (inbox[0].get("id") if inbox else None)
                        if selected_ticket_id:
                            if selected_ticket_id not in {ticket["id"] for ticket in inbox}:
                                raise ValueError("Heartbeat selected a ticket outside its inbox")
                            if not step5_atomic_checkout(selected_ticket_id, run_id, hb.get("lock_timeout_seconds", 1800), conn):
                                result = {"status": "fail", "error": "Ticket checkout failed", "agent": hb["agent"], "duration_ms": 0}
                            else:
                                task_id = selected_ticket_id
                        if task_id or not inbox:
                            result = step7_invoke_runtime(
                                agent=hb["agent"], prompt=full_prompt, max_turns=hb["max_turns"],
                                timeout_seconds=hb["timeout_seconds"], heartbeat_id=heartbeat_id,
                                requested_profile=hb.get("hermes_profile"),
                            )
                            result.update({"agent": hb["agent"], "started_at": started_at, "decision_action": decision_action, "decision_json": decision_data})
                    print(f"[heartbeat_runner] step7 done status={result['status']} duration_ms={result.get('duration_ms')}", flush=True)

        except Exception as exc:
            import traceback
            result = {
                "status": "fail",
                "error": traceback.format_exc(),
                "agent": hb["agent"],
                "duration_ms": None,
                "started_at": started_at,
            }
            print(f"[heartbeat_runner] ERROR in steps 1-7: {exc}", flush=True)

        # Step 8
        step8_persist(run_id, heartbeat_id, result, trigger_id, triggered_by, full_prompt, conn)
        print(f"[heartbeat_runner] step8 persisted run_id={run_id} status={result['status']}", flush=True)

        # Step 9
        step9_release_checkout(task_id, run_id, conn)
        print(f"[heartbeat_runner] step9 checkout released", flush=True)

        print(f"[heartbeat_runner] DONE run_id={run_id} status={result['status']}", flush=True)

    finally:
        conn.close()

    return run_id


def main():
    parser = argparse.ArgumentParser(description="Heartbeat Runner — 9-step proactive agent protocol")
    parser.add_argument("--heartbeat-id", required=True, help="Heartbeat ID (e.g. atlas-4h)")
    parser.add_argument("--triggered-by", default="manual", help="Trigger source (default: manual)")
    parser.add_argument("--trigger-id", default=None, help="Trigger event ID")
    parser.add_argument("--run-id", default=None, help="Preset run ID (for idempotence)")
    args = parser.parse_args()

    run_heartbeat(
        heartbeat_id=args.heartbeat_id,
        triggered_by=args.triggered_by,
        trigger_id=args.trigger_id,
        run_id=args.run_id,
    )


if __name__ == "__main__":
    main()
