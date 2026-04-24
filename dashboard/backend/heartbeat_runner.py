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

# Workspace root
WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"
LOGS_DIR = WORKSPACE / "ADWs" / "logs" / "heartbeats"
AGENTS_DIR = WORKSPACE / ".claude" / "agents"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_db():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _load_heartbeat(heartbeat_id: str) -> dict | None:
    """Load heartbeat config from DB."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM heartbeats WHERE id = ?", (heartbeat_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)
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
        conn.execute(
            """INSERT OR REPLACE INTO heartbeats
               (id, agent, interval_seconds, max_turns, timeout_seconds,
                lock_timeout_seconds, wake_triggers, enabled, goal_id,
                required_secrets, decision_prompt, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hb.id, hb.agent, hb.interval_seconds, hb.max_turns,
                hb.timeout_seconds, hb.lock_timeout_seconds,
                json.dumps(hb.wake_triggers), int(hb.enabled), hb.goal_id,
                json.dumps(hb.required_secrets), hb.decision_prompt,
                now, now,
            ),
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
        rows = conn.execute(
            "SELECT * FROM approvals WHERE assignee_agent = ? AND status = 'pending' LIMIT 10",
            (agent,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # approvals table may not exist yet
        return []


# ── Step 3: Query inbox (integrated with Tickets F1.3) ───────────────────────

def step3_query_inbox(agent: str, conn) -> list:
    """Query tickets assigned to agent from the tickets table (F1.3)."""
    try:
        rows = conn.execute(
            """SELECT id, title, description, priority, status, goal_id, project_id, created_at
               FROM tickets
               WHERE assignee_agent = ? AND status IN ('open','in_progress')
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
            (agent,),
        ).fetchall()
        return [dict(r) for r in rows]
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
# Locking semantics live in `ticket_inbox.checkout_ticket` (Feature 1.3).
# When the heartbeat decides to act on a ticket from step 3, the work code
# (Claude subprocess in step 7) is responsible for calling `ticket_inbox` to
# lock it. This step is a no-op pass-through — kept for protocol numbering.

def step5_atomic_checkout(task_id: str | None, run_id: str, conn) -> bool:
    """No-op pass-through. See ticket_inbox.checkout_ticket for real lock semantics."""
    return True


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


# ── Step 7: Work — invoke Claude ──────────────────────────────────────────────

def step7_invoke_claude(
    agent: str,
    prompt: str,
    max_turns: int,
    timeout_seconds: int,
) -> dict:
    """Invoke Claude via subprocess with hard timeout. Returns result dict."""
    import shutil

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return {
            "status": "fail",
            "error": "claude binary not found in PATH",
            "output": "",
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
        }

    cmd = [
        claude_bin,
        "--print",
        "--max-turns", str(max_turns),
        "--dangerously-skip-permissions",
        "--output-format", "json",
        prompt,  # positional argument — Claude CLI does not have a -p flag
    ]

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


# ── Step 8: Persist status ────────────────────────────────────────────────────

def step8_persist(run_id: str, heartbeat_id: str, result: dict, trigger_id: str | None, triggered_by: str, prompt_preview: str, conn):
    """Write heartbeat_runs row and append JSONL log."""
    now = _now_iso()

    # Upsert run (idempotent: if run_id already exists with status != running, skip)
    existing = conn.execute(
        "SELECT run_id, status FROM heartbeat_runs WHERE run_id = ?", (run_id,)
    ).fetchone()

    if existing and existing["status"] != "running":
        print(f"[heartbeat_runner] run_id={run_id} already finalized ({existing['status']}), skipping duplicate persist", flush=True)
        return

    conn.execute(
        """INSERT INTO heartbeat_runs
           (run_id, heartbeat_id, trigger_id, started_at, ended_at, duration_ms,
            tokens_in, tokens_out, cost_usd, status, prompt_preview, error, triggered_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_id) DO UPDATE SET
               ended_at=excluded.ended_at,
               duration_ms=excluded.duration_ms,
               status=excluded.status,
               error=excluded.error""",
        (
            run_id, heartbeat_id, trigger_id,
            result.get("started_at", now), now,
            result.get("duration_ms"),
            result.get("tokens_in"), result.get("tokens_out"), result.get("cost_usd"),
            result["status"],
            prompt_preview[:1000] if prompt_preview else None,
            result.get("error"),
            triggered_by,
        ),
    )
    conn.commit()

    # Append JSONL log
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
        "error": result.get("error"),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# ── Step 9: Release checkout ──────────────────────────────────────────────────

def step9_release_checkout(task_id: str | None, run_id: str, conn):
    """Release task lock. Stub in F1.1."""
    if not task_id:
        return
    try:
        conn.execute(
            """UPDATE tasks SET locked_at = NULL, locked_by = NULL
               WHERE id = ? AND locked_by = ?""",
            (task_id, run_id),
        )
        conn.commit()
    except Exception:
        pass  # Table may not exist in F1.1


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

    conn = _get_db()

    try:
        # Idempotence check: abort if this run_id already exists in a final state
        existing = conn.execute(
            "SELECT run_id, status FROM heartbeat_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing and existing["status"] != "running":
            print(f"[heartbeat_runner] run_id={run_id} already finalized, aborting", flush=True)
            return

        # Insert initial row (so we can track "running" state)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO heartbeat_runs
                   (run_id, heartbeat_id, trigger_id, started_at, status, triggered_by)
                   VALUES (?, ?, ?, ?, 'running', ?)""",
                (run_id, heartbeat_id, trigger_id, started_at, triggered_by),
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
            if hb["agent"] == "system":
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

                # Step 5
                task_id = None  # no specific task in F1.1
                checkout_ok = step5_atomic_checkout(task_id, run_id, conn)
                if not checkout_ok:
                    print(f"[heartbeat_runner] step5 checkout conflict, skipping", flush=True)
                    result = {"status": "success", "error": None, "agent": hb["agent"], "duration_ms": 0}
                    step8_persist(run_id, heartbeat_id, result, trigger_id, triggered_by, "", conn)
                    return

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
                    # Standard Claude CLI subprocess
                    print(f"[heartbeat_runner] step7 invoking claude agent={hb['agent']} max_turns={hb['max_turns']} timeout={hb['timeout_seconds']}s", flush=True)
                    invoke_result = step7_invoke_claude(
                        agent=hb["agent"],
                        prompt=full_prompt,
                        max_turns=hb["max_turns"],
                        timeout_seconds=hb["timeout_seconds"],
                    )
                    invoke_result["agent"] = hb["agent"]
                    invoke_result["started_at"] = started_at
                    result = invoke_result
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
