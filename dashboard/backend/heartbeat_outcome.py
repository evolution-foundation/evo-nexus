"""Interpret an agent's heartbeat result and apply it to the kanban.

The old model fired "✅ Heartbeat OK" for every successful run, including
no-op skips — pure noise. This module replaces that with outcome-driven
behaviour: the agent ends its run with a structured JSON block describing what
it did, and we (1) move the ticket on the board, (2) record a comment + activity,
and (3) decide whether anything is worth telling Felipe on Telegram.

Agent output contract (the agent appends this JSON to its final message):

    {
      "action": "work" | "skip" | "blocked",
      "ticket_id": "<id or null>",
      "result": "<one-line natural-language summary of what was done>",
      "new_status": "in_progress" | "review" | "resolved" | "blocked" | null,
      "blocked_reason": "<why it is stuck, if blocked>",
      "needs": "<what Felipe must provide to unblock: data, credential, decision>"
    }

Notification policy (decided with Felipe 2026-06-17):
  - action=skip  → silent (nothing to report)
  - action=work  → notify the *result* (not tokens/cost)
  - action=blocked → notify, because Felipe needs to intervene
  - unparseable / no JSON → silent (no more "heartbeat ok" spam)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

# Valid ticket states (see .claude/rules/tickets.md)
_VALID_STATUS = {"open", "in_progress", "blocked", "review", "resolved", "closed"}
_STATUS_ALIASES = {
    "done": "resolved",
    "complete": "resolved",
    "completed": "resolved",
    "finished": "resolved",
    "in progress": "in_progress",
    "inprogress": "in_progress",
    "review_needed": "review",
    "needs_review": "review",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_status(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().lower()
    s = _STATUS_ALIASES.get(s, s)
    return s if s in _VALID_STATUS else None


def _unwrap_provider_output(text: str) -> str:
    """Unwrap the CLI/provider JSON envelope to get the assistant's actual text.

    step7 returns the raw CLI output, which for `--output-format json` is an
    envelope like {"type":"result","result":"<assistant text>","usage":{…}}.
    The agent's outcome JSON lives INSIDE that text with escaped quotes. We must
    json.loads the envelope (which un-escapes the inner text) before scanning for
    the {"action":…} block. Handles stream-json (multi-line) too.
    """
    s = (text or "").strip()
    if not s.startswith("{"):
        return text

    def _content_of(obj):
        if not isinstance(obj, dict):
            return None
        if "action" in obj:
            return json.dumps(obj)  # already the outcome itself
        for key in ("result", "content", "text", "message", "response", "output"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v
        return None

    # Single JSON envelope
    try:
        got = _content_of(json.loads(s))
        if got is not None:
            return got
    except json.JSONDecodeError:
        pass
    # stream-json: scan lines bottom-up for the last decodable envelope with content
    for line in reversed(s.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            got = _content_of(json.loads(line))
        except json.JSONDecodeError:
            continue
        if got is not None:
            return got
    return text


def parse_agent_outcome(output) -> dict | None:
    """Extract the structured outcome JSON from an agent's free-form output.

    Returns the parsed dict (with at least an "action" key) or None if no
    structured outcome could be found.
    """
    if isinstance(output, dict):
        return output if "action" in output else None
    if not output:
        return None
    text = _unwrap_provider_output(str(output))

    candidates: list[str] = []
    # 1. fenced ```json ... ``` blocks
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        candidates.append(m.group(1))
    # 2. raw JSON objects scanned with the decoder (handles nesting)
    decoder = json.JSONDecoder()
    idx = 0
    while True:
        start = text.find("{", idx)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
            if isinstance(obj, dict):
                candidates.append(json.dumps(obj))
            idx = start + max(end, 1)
        except json.JSONDecodeError:
            idx = start + 1

    # Prefer the last candidate that carries an "action" key
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and "action" in obj:
            return obj
    return None


_OUTCOME_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["work", "skip", "blocked"]},
        "ticket_id": {"type": ["string", "null"]},
        "result": {"type": "string"},
        "new_status": {"type": ["string", "null"]},
        "blocked_reason": {"type": "string"},
        "needs": {"type": "string"},
    },
    "required": ["action", "result"],
}

# Large models that reliably honor response_format=json_schema with good content.
# A short chain so a 429 on one rotates to the next (NVIDIA is free; the limit is
# rate, not cost — see the model-chain rationale).
_STRUCTURER_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "qwen/qwen3.5-397b-a17b",
    "qwen/qwen3.5-122b-a10b",
]


def _nvidia_key_and_base() -> tuple[str, str]:
    import os
    key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    base = "https://integrate.api.nvidia.com/v1"
    if not key:
        # last resort: read NVIDIA_API_KEY from .env at repo root
        try:
            from pathlib import Path
            env = Path(__file__).resolve().parents[2] / ".env"
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("NVIDIA_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
        except Exception:  # noqa: BLE001
            pass
    return key, base


def structure_via_nvidia(agent_output, agent: str, conn) -> dict | None:
    """Structure the agent's report into outcome JSON using NVIDIA (free).

    The executor models run via the agentic CLI and rarely emit clean JSON. This
    makes a single, cheap completion call with response_format=json_schema (strict)
    on a large model, which guarantees structurally-valid JSON. Free — keeps the
    whole loop on NVIDIA. Falls through to Claude only if every NVIDIA model fails.
    """
    import json as _json
    import urllib.request
    import urllib.error

    report = _unwrap_provider_output(str(agent_output or "")).strip()
    if not report:
        return None
    key, base = _nvidia_key_and_base()
    if not key:
        return None

    rows = conn.execute(
        "SELECT id, title FROM tickets WHERE assignee_agent = ? "
        "AND status IN ('open','in_progress') ORDER BY priority_rank DESC LIMIT 10",
        (agent,),
    ).fetchall()
    tickets = [{"id": (r["id"] if hasattr(r, "keys") else r[0]),
                "title": (r["title"] if hasattr(r, "keys") else r[1])} for r in rows]

    prompt = (
        "Converta o relatório de um agente em um JSON de outcome.\n"
        f"Tickets atribuídos ao agente: {_json.dumps(tickets, ensure_ascii=False)}\n\n"
        f"Relatório do agente:\n{report[:4000]}\n\n"
        "Regras:\n"
        "- action='work' se o agente ENTREGOU ou avançou algo concreto (existe um "
        "resultado). Defina new_status: 'resolved' se concluiu, 'review' se precisa "
        "revisão, 'in_progress' se avançou parcialmente.\n"
        "- action='blocked' se depende de algo que só o humano fornece (credencial, "
        "acesso, dado, decisão). Preencha blocked_reason e needs.\n"
        "- action='skip' SOMENTE se nada acionável foi feito.\n"
        "- result: 1 frase em pt-BR com o resultado concreto. ticket_id: o id tratado."
    )
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 600,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "outcome", "schema": _OUTCOME_SCHEMA,
                                            "strict": True}},
    }
    for model in _STRUCTURER_MODELS:
        body["model"] = model
        try:
            req = urllib.request.Request(
                base + "/chat/completions",
                data=_json.dumps(body).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if content:
                parsed = _json.loads(content)
                if isinstance(parsed, dict) and "action" in parsed:
                    return parsed
        except urllib.error.HTTPError as e:
            if e.code == 429:
                continue  # rate-limited → next model
            continue
        except Exception:  # noqa: BLE001
            continue
    return None


def structure_via_claude(agent_output, agent: str, conn) -> dict | None:
    """Hybrid fallback: NVIDIA executes (free, heavy), Claude structures (cheap).

    The NVIDIA models do the actual work but often fail to emit the outcome JSON
    reliably (they narrate, hit max turns, or answer generically). When the raw
    parse fails, we ask the native `claude` CLI (Anthropic subscription) to turn
    the agent's report into the outcome JSON — one turn, no tools, ~hundreds of
    tokens, so it barely touches the Anthropic quota. Returns the outcome dict or
    None if Claude is unavailable / also fails.
    """
    import os
    import shutil
    import subprocess

    claude_bin = shutil.which("claude")
    if not claude_bin:
        return None

    report = _unwrap_provider_output(str(agent_output or "")).strip()
    if not report:
        return None

    # Inbox the agent could have acted on (id + title), so Claude can pick ticket_id
    rows = conn.execute(
        "SELECT id, title FROM tickets WHERE assignee_agent = ? "
        "AND status IN ('open','in_progress') ORDER BY priority_rank DESC LIMIT 10",
        (agent,),
    ).fetchall()
    tickets = []
    for r in rows:
        try:
            tickets.append({"id": r["id"], "title": r["title"]})
        except (TypeError, KeyError, IndexError):
            tickets.append({"id": r[0], "title": r[1]})

    prompt = (
        "Você converte o relatório de um agente em um único JSON de outcome. "
        "Tickets atribuídos ao agente (escolha o ticket_id correto):\n"
        f"{json.dumps(tickets, ensure_ascii=False)}\n\n"
        f"Relatório do agente:\n{report[:4000]}\n\n"
        "Responda SOMENTE com este JSON (uma linha, nada antes/depois):\n"
        '{"action":"work"|"skip"|"blocked","ticket_id":"<id ou null>",'
        '"result":"<o que o agente concluiu, 1 frase pt-BR>",'
        '"new_status":"in_progress"|"review"|"resolved"|null,'
        '"blocked_reason":"<se blocked>","needs":"<se blocked, o que precisa do humano>"}\n'
        "Regras: action=work se o agente entregou/avançou algo; blocked se ele "
        "depende de dado/credencial/decisão humana; skip se nada foi feito."
    )

    # Clean env so the `claude` CLI uses the Anthropic subscription, not the
    # NVIDIA/OpenAI override vars that may be set for the rest of the workspace.
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("OPENAI_") or k.startswith("CLAUDE_CODE_USE_")
                   or k in ("ANTHROPIC_BASE_URL", "NVIDIA_API_KEY"))}
    try:
        proc = subprocess.run(
            [claude_bin, "--print", "--output-format", "json",
             "--max-turns", "1", "--tools", "", "--", prompt],
            capture_output=True, text=True, timeout=90, env=env,
        )
    except (subprocess.TimeoutExpired, Exception):  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    return parse_agent_outcome(proc.stdout)


def _ticket_title(ticket_id: str, conn) -> str:
    row = conn.execute("SELECT title FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not row:
        return ticket_id
    try:
        return row["title"] or ticket_id
    except (TypeError, KeyError, IndexError):
        return (row[0] if row[0] else ticket_id)


def _move_ticket(ticket_id: str, new_status: str, agent: str, comment: str, conn) -> None:
    """Update ticket status + log a comment and an activity event."""
    now = _now_iso()
    resolved_at = now if new_status in ("resolved", "closed") else None
    conn.execute(
        "UPDATE tickets SET status = ?, updated_at = ?, "
        "resolved_at = COALESCE(?, resolved_at) WHERE id = ?",
        (new_status, now, resolved_at, ticket_id),
    )
    if comment:
        import uuid
        conn.execute(
            "INSERT INTO ticket_comments (id, ticket_id, author, body, mentions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), ticket_id, f"agent:{agent}", comment, "[]", now),
        )
        conn.execute(
            "INSERT INTO ticket_activity (id, ticket_id, actor, action, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), ticket_id, f"agent:{agent}", "status_changed",
             json.dumps({"new_status": new_status}), now),
        )
    conn.commit()


def apply_outcome(heartbeat_id: str, agent: str, result: dict, conn) -> dict | None:
    """Apply the agent's outcome to the kanban and return a notification spec.

    Returns a dict {kind, ...} describing what (if anything) to notify, or None
    for silence. The caller (heartbeat_runner) turns this into a Telegram message.
    """
    status = result.get("status", "fail")

    # Technical failure of the run itself — surface it compactly, it may be real.
    if status != "success":
        return {
            "kind": "tech_fail",
            "agent": agent,
            "heartbeat_id": heartbeat_id,
            "error": (result.get("error") or "execução falhou")[:300],
        }

    raw_output = result.get("output") or result.get("result") or result.get("handler_result")
    outcome = parse_agent_outcome(raw_output)
    if not outcome:
        # NVIDIA executed but didn't emit clean JSON → structure it. Try NVIDIA
        # first (free, json_schema-forced), then Claude as a last resort.
        outcome = structure_via_nvidia(raw_output, agent, conn)
    if not outcome:
        outcome = structure_via_claude(raw_output, agent, conn)
    if not outcome:
        return None  # nobody could structure it → silent no-op (no spam)

    action = str(outcome.get("action", "")).strip().lower()
    ticket_id = outcome.get("ticket_id") or None
    summary = (outcome.get("result") or "").strip()

    if action == "skip":
        return None

    if action == "blocked":
        reason = (outcome.get("blocked_reason") or summary or "sem detalhes").strip()
        needs = (outcome.get("needs") or "").strip()
        title = _ticket_title(ticket_id, conn) if ticket_id else ""
        if ticket_id:
            _move_ticket(ticket_id, "blocked", agent,
                         f"Bloqueado: {reason}" + (f"\nPrecisa: {needs}" if needs else ""), conn)
        return {
            "kind": "blocked",
            "agent": agent,
            "ticket_title": title,
            "reason": reason,
            "needs": needs,
        }

    if action == "work":
        new_status = _normalize_status(outcome.get("new_status")) or "in_progress"
        title = _ticket_title(ticket_id, conn) if ticket_id else ""
        if ticket_id:
            _move_ticket(ticket_id, new_status, agent, summary or "Trabalho realizado.", conn)
        if not summary:
            return None  # worked but reported nothing meaningful → stay silent
        return {
            "kind": "result",
            "agent": agent,
            "ticket_title": title,
            "new_status": new_status,
            "summary": summary,
        }

    return None
