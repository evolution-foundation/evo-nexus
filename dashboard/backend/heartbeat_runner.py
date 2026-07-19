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

# Agents that monitor external state (Linear, Stripe, Omie, …) and may have work
# to do even with an empty ticket inbox. These bypass the empty-inbox cost guard;
# every other agent skips (without invoking Claude) when it has no assigned work.
# goal-planner is here for the same reason: it decomposes Goals, not tickets —
# its own ticket inbox is always empty, so without this it would never run.
STATE_MONITOR_AGENTS = {
    "atlas-project", "flux-finance", "goal-planner",
    # ai-hierarchy-suggestions: same zero-inbox, event-only shape as
    # goal-planner, one rung each higher in the Mission -> Project -> Goal
    # -> Ticket chain.
    "project-planner", "goal-suggester",
    # growth-content-heartbeat: pixel-social-media's growth heartbeat exists
    # specifically to top up a Goal's content-ticket queue WHEN IT'S LOW —
    # the exact case an empty inbox would otherwise cost-guard away.
    "pixel-social-media",
}

# Review-loop subagent instructions (goal-ticket-unification Step 6, ADR SPEC
# 2c): appended to the executor's prompt ONLY when the pre-run active_provider
# is 'anthropic' (native subagent/Task support). Any other provider (nvidia
# default) gets none of this — the verdict comes from
# heartbeat_outcome.verdict_via_nvidia after the run's lock has released.
_VERDICT_JSON_HINT = (
    '{"verdict": "pass"|"fail", "critique": "<1-3 frases pt-BR>", '
    '"blocking_issues": ["<opcional>"], "confidence": "high"|"medium"|"low"}'
)

REVIEW_LOOP_SUBAGENT_INSTRUCTIONS = f"""

---

## Revisão em sessão (só se você decidir new_status="review")

Se, e SOMENTE se, o `new_status` da sua resposta for "review": ANTES de
responder, invoque os subagentes `raven-critic` e depois `oath-verifier`
(Task tool) na MESMA sessão para revisar criticamente o que você acabou de
entregar. Depois de rodar os dois, decida o veredito final e ANEXE este
segundo JSON de veredito logo após o JSON de outcome, na mesma resposta (uma
linha, nada entre eles):

{_VERDICT_JSON_HINT}
"""


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
                required_secrets, decision_prompt, handler, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hb.id, hb.agent, hb.interval_seconds, hb.max_turns,
                hb.timeout_seconds, hb.lock_timeout_seconds,
                json.dumps(hb.wake_triggers), int(hb.enabled), hb.goal_id,
                json.dumps(hb.required_secrets), hb.decision_prompt, hb.handler,
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
    content = agent_file.read_text(encoding="utf-8")
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            content = parts[2]
    for marker in ("\n# Persistent Agent Memory", "\n## MEMORY.md"):
        if marker in content:
            content = content.split(marker, 1)[0]
    return content.strip()


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
               WHERE assignee_agent = ? AND status IN ('open','in_progress','review')
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


def _load_trigger_payload(trigger_id: str | None, conn) -> dict | None:
    """Load the JSON payload attached to a non-interval wake trigger, if any.

    e.g. the goal_created trigger's {"goal_id": <id>} — see step6's docstring.
    """
    if not trigger_id:
        return None
    try:
        row = conn.execute(
            "SELECT payload FROM heartbeat_triggers WHERE id = ?", (trigger_id,)
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    raw = row["payload"] if hasattr(row, "keys") else row[0]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Step 6: Assemble context ──────────────────────────────────────────────────

def step6_assemble_context(
    identity: str,
    decision_context: dict,
    goal_id: str | None,
    trigger_payload: dict | None = None,
) -> str:
    """Build the full prompt for Claude. Injects goal chain (Mission→Project→Goal) if goal_id is set.

    trigger_payload surfaces the data attached to a non-interval wake (e.g. the
    goal_created trigger's {"goal_id": <id>}, needed by goal-planner to know
    WHICH goal to decompose — its ticket inbox is always empty, see
    STATE_MONITOR_AGENTS).
    """
    inbox_summary = ""
    if decision_context.get("inbox_count", 0) > 0:
        inbox_summary = f"\n\nPending inbox items: {decision_context['inbox_count']}"
        if decision_context.get("inbox_preview"):
            inbox_summary += f"\nTop items: {json.dumps(decision_context['inbox_preview'], indent=2)}"

    approvals_summary = ""
    if decision_context.get("pending_approvals", 0) > 0:
        approvals_summary = f"\n\nPending approvals: {decision_context['pending_approvals']}"

    trigger_summary = ""
    if trigger_payload:
        trigger_summary = f"\n\nTrigger payload: {json.dumps(trigger_payload, ensure_ascii=False)}"

    base_prompt = f"""{identity}

---

## Heartbeat Decision Context

{decision_context['decision_prompt']}{inbox_summary}{approvals_summary}{trigger_summary}

---

## Sua tarefa — responda em UMA única mensagem

Decida o que fazer com o item de MAIOR prioridade da sua inbox acima, usando o que
você já sabe sobre ele (título e descrição). NÃO narre ("vou fazer", "primeiro
preciso ler"), NÃO peça para abrir arquivos — DECIDA AGORA e entregue o resultado
na própria resposta.

- `work` — você consegue entregar/avançar agora: uma decisão, um plano objetivo,
  um texto pronto, uma resposta. Coloque o conteúdo/resultado concreto em `result`
  e ajuste `new_status`.
- `blocked` — depende de algo que só o Felipe fornece (credencial, acesso, dado,
  aprovação, uma decisão dele). Preencha `blocked_reason` e `needs`.
- `skip` — a inbox está vazia ou nada é acionável agora.

## Responda SOMENTE com este JSON — nada antes, nada depois, tudo em uma linha:

{{"action": "work"|"skip"|"blocked", "ticket_id": "<id da inbox ou null>", "result": "<resultado concreto em pt-BR>", "new_status": "in_progress"|"review"|"resolved"|null, "blocked_reason": "<por que travou, se blocked>", "needs": "<o que precisa do Felipe, se blocked>", "publish_intent": true|false|null, "publish_target": "instagram"|"linkedin"|null, "publish_content": "<texto EXATO a publicar ou null>", "publish_media": ["<URL HTTPS de mídia>"]|null}}

`result` deve dizer o RESULTADO entregue, não "analisei" — ex.: "Plano P0: 3 canais
(WhatsApp, IG, e-mail), oferta X, 1 post/dia; sucesso = 20 leads", não "vou montar o plano".
Se `publish_intent=true`, `publish_content` é obrigatório e deve conter o texto
final completo; nunca coloque somente um resumo em `result`. Para Instagram,
`publish_media` deve conter ao menos uma URL HTTPS da mídia final.
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
    """Invoke the agent CLI with automatic provider fallback.

    Routes through provider_fallback.invoke_with_fallback so a 429/quota error
    on the active provider rotates to the next model/provider in the chain
    (ending at native `claude`) instead of failing the whole heartbeat run.

    Disable with HEARTBEAT_PROVIDER_FALLBACK=0 (or if provider_fallback is
    unavailable) → falls back to a direct native `claude` call. The returned
    dict keeps the same contract step8_persist expects.
    """
    use_fallback = os.environ.get("HEARTBEAT_PROVIDER_FALLBACK", "1").lower() not in (
        "0", "false", "no",
    )
    if use_fallback:
        try:
            from provider_fallback import invoke_with_fallback

            result = invoke_with_fallback(
                prompt=prompt,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
                # The heartbeat prompt already embeds the full agent identity
                # from .claude/agents/{agent}.md. Passing --agent here breaks
                # OpenClaude-backed providers, which can misparse the expanded
                # agent markdown as CLI options.
                agent="",
            )
            # Preserve the step7 contract; provider_fallback already returns
            # status/output/error/duration_ms/tokens_*/cost_usd and adds
            # provider_id/model/attempt metadata for observability.
            result.setdefault("tokens_in", None)
            result.setdefault("tokens_out", None)
            result.setdefault("cost_usd", None)
            if result.get("status") == "success" and result.get("attempt_number", 0) > 1:
                print(
                    f"[heartbeat_runner] step7 fallback succeeded via "
                    f"{result.get('provider_id')}:{result.get('model')} "
                    f"(attempt #{result.get('attempt_number')})",
                    flush=True,
                )
            elif result.get("status") != "success" and result.get("attempt_number", 0) > 1:
                print(
                    f"[heartbeat_runner] step7 fallback exhausted; last "
                    f"{result.get('provider_id')}:{result.get('model')} "
                    f"(attempt #{result.get('attempt_number')})",
                    flush=True,
                )
            return result
        except Exception as exc:
            print(
                f"[heartbeat_runner] provider_fallback unavailable ({exc}); "
                f"using native claude",
                flush=True,
            )

    return _step7_invoke_claude_native(agent, prompt, max_turns, timeout_seconds)


def _step7_invoke_claude_native(
    agent: str,
    prompt: str,
    max_turns: int,
    timeout_seconds: int,
) -> dict:
    """Invoke native `claude` via subprocess with hard timeout. Returns result dict.

    Legacy direct path — used when provider fallback is disabled or unavailable.
    """
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
        "--",
        prompt,  # positional argument — Claude CLI does not have a -p flag
    ]

    start_time = time.time()
    proc = None
    output = ""
    error = None
    status = "success"

    try:
        # V10: this is the SECOND agent-subprocess call site (the first is
        # provider_fallback._invoke_cli) — reached when provider fallback is
        # disabled/unavailable. Without env=_build_agent_run_env(), Popen
        # inherits the full parent env unfiltered, including
        # APPROVAL_BRIDGE_TOKEN, making the env-isolation in
        # provider_fallback.py bypassable just by disabling fallback.
        # DASHBOARD_API_TOKEN is intentionally NOT denylisted — see the
        # comment on _AGENT_ENV_DENYLIST_EXACT in provider_fallback.py.
        try:
            from provider_fallback import _build_agent_run_env
            agent_env = _build_agent_run_env()
        except Exception:
            # provider_fallback unavailable — apply the same denylist inline
            # rather than let this fallback become the leak V10 closed.
            _denylist_exact = {"APPROVAL_BRIDGE_TOKEN", "POSTIZ_API_KEY"}
            _denylist_prefixes = ("SOCIAL_", "INSTAGRAM_", "LINKEDIN_", "TWITTER_", "DISCORD_")
            agent_env = {
                k: v for k, v in os.environ.items()
                if k not in _denylist_exact and not k.startswith(_denylist_prefixes)
            }
            agent_env["DISABLE_AUTOUPDATER"] = "1"

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(WORKSPACE),
            start_new_session=True,  # new process group for clean kill
            env=agent_env,
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

def step9_release_checkout(ticket_id: str | None, agent_slug: str, conn):
    """Release ticket lock on the tickets table (Feature 1.3)."""
    if not ticket_id:
        return
    try:
        conn.execute(
            """UPDATE tickets SET locked_at = NULL, locked_by = NULL
               WHERE id = ? AND locked_by = ?""",
            (ticket_id, agent_slug),
        )
        conn.execute(
            """INSERT INTO ticket_activity (ticket_id, event, actor, metadata)
               VALUES (?, 'release', ?, '{}')""",
            (ticket_id, f"agent:{agent_slug}"),
        )
        conn.commit()
    except Exception as exc:
        print(f"[heartbeat_runner] step9 release WARNING ticket={ticket_id}: {exc}", flush=True)


# ── Failure alert (runs between persist and release) ──────────────────────────

def _classify_failure(result: dict) -> str:
    """Return a short failure category: timeout | provider_exhausted | auth | unknown."""
    error = (result.get("error") or "").lower()
    status = result.get("status", "")
    if status == "timeout":
        return "timeout"
    if result.get("fallback_exhausted"):
        return "provider_exhausted"
    if any(kw in error for kw in ("401", "403", "unauthorized", "invalid_api_key", "authentication")):
        return "auth"
    return "unknown"


def step_alert_on_failure(heartbeat_id: str, result: dict) -> bool:
    """Send a compact alert when a heartbeat run fails. Returns True if sent.

    Alert is sent only for non-success statuses (fail, timeout).
    Categories: timeout, provider_exhausted, auth, unknown.
    """
    status = result.get("status", "success")
    if status == "success":
        return False

    category = _classify_failure(result)
    agent = result.get("agent", heartbeat_id)
    duration_s = ""
    if result.get("duration_ms"):
        duration_s = f"{result['duration_ms'] / 1000:.1f}s"

    error_preview = ""
    if result.get("error"):
        error_preview = result["error"][:200].replace("\n", " ")

    # Build compact Telegram message
    lines = [
        f"⚠️ <b>Heartbeat Fail</b>",
        f"",
        f"🔧 <b>{heartbeat_id}</b>  |  🤖 {agent}",
        f"📌 Status: <code>{status}</code>  |  🏷 {category}",
    ]
    if duration_s:
        lines.append(f"⏱ {duration_s}")
    if result.get("provider_id"):
        lines.append(f"🔗 Provider: {result['provider_id']}:{result.get('model', '?')}")
    if result.get("attempt_number"):
        lines.append(f"🔄 Attempt #{result['attempt_number']}")
    if error_preview:
        lines.append(f"")
        lines.append(f"📝 <pre>{error_preview}</pre>")

    text = "\n".join(lines)

    from notifications import send_telegram_alert
    sent = send_telegram_alert(text)
    if sent:
        print(f"[heartbeat_runner] alert sent for {heartbeat_id} fail ({category})", flush=True)
    else:
        print(f"[heartbeat_runner] alert NOT sent (Telegram not configured) for {heartbeat_id}", flush=True)
    return sent


# ── Success report ───────────────────────────────────────────────────────────

def _extract_progress_preview(result: dict, limit: int = 3) -> str:
    """Extract a short user-facing progress preview from provider output."""
    raw = result.get("result") or result.get("output") or result.get("handler_result") or ""
    if isinstance(raw, dict):
        raw = json.dumps(raw, ensure_ascii=False)
    raw = str(raw).strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for key in ("summary", "result", "message", "answer", "progress", "report"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        raw = value.strip()
                        break
        except (json.JSONDecodeError, TypeError):
            pass
    lines = [line.strip(" -•*") for line in raw.splitlines()]
    lines = [line for line in lines if line and not line.lower().startswith(("[fallback]", "stop_reason", "session_id"))]
    preview_lines = []
    for line in lines:
        line = " ".join(line.split())
        if line and line not in preview_lines:
            preview_lines.append(line)
        if len(preview_lines) >= limit:
            break
    return "\n".join(f"• {line}" for line in preview_lines[:limit])


def _step_report_success(heartbeat_id: str, result: dict) -> bool:
    """Send a compact Telegram notification when a heartbeat run completes.

    Sent only for status = 'success'.
    Returns True if sent, False if not (Telegram not configured, status not success).
    """
    status = result.get("status", "fail")
    if status != "success":
        return False

    # Debounce by heartbeat_id — skip if same heartbeat reported <30s ago
    _last_reported = getattr(_step_report_success, "_last_sent", {})
    now = datetime.now(timezone.utc)
    last = _last_reported.get(heartbeat_id)
    if last and (now - last).total_seconds() < 30:
        return False
    _last_reported[heartbeat_id] = now
    _step_report_success._last_sent = _last_reported  # type: ignore[attr-defined]

    agent = result.get("agent", heartbeat_id)
    duration_s = ""
    if result.get("duration_ms"):
        duration_s = f"{result['duration_ms'] / 1000:.1f}s"

    cost_str = ""
    if result.get("cost_usd") is not None and result["cost_usd"] > 0:
        cost_str = f"  |  💰 US${result['cost_usd']:.4f}"

    provider_str = ""
    if result.get("provider_id"):
        provider_str = f"  |  🔗 {result['provider_id']}:{result.get('model', '?')}"

    lines = [
        f"✅ <b>Heartbeat OK</b>",
        f"",
        f"🔧 <b>{heartbeat_id}</b>  |  🤖 {agent}",
        f"⏱ {duration_s}{cost_str}{provider_str}",
    ]

    progress = _extract_progress_preview(result)
    if progress:
        lines.extend([
            "",
            "📌 Progresso:",
            progress[:650],
        ])
    else:
        lines.extend([
            "",
            "📌 Progresso: execução concluída sem saída detalhada.",
        ])

    if result.get("tokens_in") is not None or result.get("tokens_out") is not None:
        tok_in = result.get("tokens_in") or 0
        tok_out = result.get("tokens_out") or 0
        lines.append(f"📊 Tokens: {tok_in:,} in / {tok_out:,} out")

    text = "\n".join(lines)

    from notifications import send_telegram_alert
    sent = send_telegram_alert(text)
    if sent:
        print(f"[heartbeat_runner] success report sent for {heartbeat_id}", flush=True)
    else:
        print(f"[heartbeat_runner] success report NOT sent (Telegram not configured) for {heartbeat_id}", flush=True)
    return sent


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
        ticket_id = None
        try:
            # Special case: legacy agent='system' heartbeats without explicit handler
            # run a Python script directly, resolved by heartbeat id.
            # System heartbeats with `handler` use the in-process handler path below.
            if hb["agent"] == "system" and not hb.get("handler"):
                full_prompt = f"[system heartbeat] {heartbeat_id}"
                result = _run_system_heartbeat(heartbeat_id, hb["timeout_seconds"])
                result["agent"] = "system"
                result["started_at"] = started_at
            elif hb.get("handler"):
                # In-process handlers do not have/need a Claude agent identity.
                _handler_ref = hb.get("handler") or ""
                full_prompt = f"[handler heartbeat] {heartbeat_id}"
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
                    result = {
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
                    result = {
                        "status": "fail",
                        "error": traceback.format_exc(),
                        "agent": hb.get("agent", "system"),
                        "duration_ms": _duration_ms,
                        "started_at": started_at,
                    }
                    print(f"[heartbeat_runner] step7 in-process handler failed: {_h_exc}", flush=True)
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

                # Cost guard: executor agents don't burn tokens "deciding to skip".
                # If there's no assigned work and no pending approvals, skip the
                # Claude invocation entirely (silent, ~zero cost). State-monitor
                # agents (Linear/Stripe/etc.) opt out via STATE_MONITOR_AGENTS.
                if (not inbox and not approvals
                        and hb["agent"] not in STATE_MONITOR_AGENTS):
                    print(f"[heartbeat_runner] cost-guard: empty inbox/approvals for {hb['agent']}, skipping without Claude", flush=True)
                    result = {"status": "success", "error": None, "agent": hb["agent"],
                              "duration_ms": 0, "output": '{"action":"skip","reason":"no assigned work"}'}
                    step8_persist(run_id, heartbeat_id, result, trigger_id, triggered_by, "", conn)
                    return

                # Step 4
                decision_ctx = step4_pick_priority(identity, approvals, inbox, hb["decision_prompt"])
                print(f"[heartbeat_runner] step4 decision context assembled", flush=True)

                # Step 6
                trigger_payload = _load_trigger_payload(trigger_id, conn)
                full_prompt = step6_assemble_context(identity, decision_ctx, hb.get("goal_id"), trigger_payload)

                # Self-healing review loop (Step 6, ADR SPEC 2c): read
                # active_provider PRE-run (not provider_id, which only
                # exists post-fallback, Raven-F2) to decide whether the
                # executor should be asked to review its own "review"-bound
                # work in-session via subagents. Read-only lookup — does not
                # touch run_env/Popen construction (Step 4 scope).
                try:
                    from provider_fallback import _read_providers_config
                    active_provider = _read_providers_config().get("active_provider", "nvidia")
                except Exception:
                    active_provider = "nvidia"
                if active_provider == "anthropic":
                    full_prompt += REVIEW_LOOP_SUBAGENT_INSTRUCTIONS
                print(f"[heartbeat_runner] step6 prompt assembled ({len(full_prompt)} chars, active_provider={active_provider})", flush=True)

                # Step 7 — Claude CLI subprocess
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

                # Extract ticket_id from Claude's JSON output for atomic checkout
                spec = None
                if invoke_result.get("status") == "success" and invoke_result.get("output"):
                    try:
                        from heartbeat_outcome import parse_agent_outcome
                        spec = parse_agent_outcome(invoke_result["output"])
                        if spec and spec.get("action") == "work":
                            ticket_id = spec.get("ticket_id")
                    except Exception as parse_exc:
                        print(f"[heartbeat_runner] WARNING outcome parse failed: {parse_exc}", flush=True)

                # Step 5 (moved after step7 — checkout the ticket Claude chose)
                if ticket_id:
                    agent_slug = hb.get("agent", "unknown")
                    now = _now_iso()
                    cur = conn.execute(
                        """UPDATE tickets SET locked_at = ?, locked_by = ?, lock_timeout_seconds = 1800
                           WHERE id = ? AND locked_at IS NULL""",
                        (now, agent_slug, ticket_id),
                    )
                    if cur.rowcount == 0:
                        print(f"[heartbeat_runner] step5 checkout CONFLICT ticket={ticket_id} already locked", flush=True)
                        ticket_id = None
                    else:
                        conn.execute(
                            """INSERT INTO ticket_activity (ticket_id, event, actor, metadata)
                               VALUES (?, 'checkout', ?, ?)""",
                            (ticket_id, f"agent:{agent_slug}", f'{{"run_id":"{run_id}","triggered_by":"{triggered_by}"}}'),
                        )
                        conn.commit()
                        print(f"[heartbeat_runner] step5 checkout OK ticket={ticket_id} agent={agent_slug}", flush=True)
                else:
                    print(f"[heartbeat_runner] step5 no ticket to checkout (action={spec.get('action','unknown') if spec else 'no_output'})", flush=True)

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

        # Notifications — success or failure via centralized module
        try:
            _send_heartbeat_notification(heartbeat_id, hb["agent"], result, conn)
        except Exception as notif_exc:
            print(f"[heartbeat_runner] notification error (non-fatal): {notif_exc}", flush=True)

        # Step 9
        step9_release_checkout(ticket_id, hb["agent"], conn)
        print(f"[heartbeat_runner] step9 checkout released", flush=True)

        print(f"[heartbeat_runner] DONE run_id={run_id} status={result['status']}", flush=True)

    finally:
        conn.close()

    return run_id


def _send_heartbeat_notification(heartbeat_id: str, agent: str, result: dict, conn=None):
    """Outcome-driven notification + kanban movement.

    Policy (decided with Felipe 2026-06-17): no more "Heartbeat OK". We only
    message Telegram when an agent actually advanced/finished a task (the result)
    or got blocked and needs intervention. Skips and empty runs stay silent.
    See heartbeat_outcome.apply_outcome for the agent JSON contract.
    """
    from heartbeat_outcome import apply_outcome

    own_conn = conn is None
    if own_conn:
        conn = _get_db()
    try:
        spec = apply_outcome(heartbeat_id, agent, result, conn)
    finally:
        if own_conn:
            conn.close()

    if not spec:
        return  # silence — nothing worth reporting

    kind = spec.get("kind")
    if kind == "result":
        from notifications import notify_agent_result
        notify_agent_result(spec["agent"], spec.get("ticket_title", ""),
                            spec.get("new_status", ""), spec.get("summary", ""))
    elif kind == "blocked":
        from notifications import notify_agent_blocked
        notify_agent_blocked(spec["agent"], spec.get("ticket_title", ""),
                            spec.get("reason", ""), spec.get("needs", ""),
                            ticket_id=spec.get("ticket_id", ""))
    elif kind == "tech_fail":
        from notifications import notify_heartbeat_failure
        notify_heartbeat_failure(
            heartbeat_id=heartbeat_id, agent=agent,
            error=spec.get("error", "unknown"),
            duration_ms=result.get("duration_ms") or 0,
            attempt=result.get("attempt_number", 0),
        )


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
