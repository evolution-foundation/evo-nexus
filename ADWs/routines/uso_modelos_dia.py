#!/usr/bin/env python3
"""
Routines/uso_modelos_dia.py — Exercita visivelmente cada modelo NVIDIA disponível.

Roda diariamente via scheduler.py + pode ser disparada manualmente.
Cada modelo é pingado com um prompt curto que pede um resumo em PT-BR
+ registra token usage e custo no log/JSONL de routines (ADWs/logs/metrics.json).
Resultado: fica visível no /costs qual modelo está sendo usado.

Outputs:
    ADWs/logs/routines/uso_modelos_dia-{DATE}.jsonl  — 1 entry por modelo

Usage:
    python3 uso_modelos_dia.py                # roda todos os modelos do chain
    python3 uso_modelos_dia.py --only nvidia  # só NVIDIA (default)
    python3 uso_modelos_dia.py --models model1,model2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, date
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE / "dashboard" / "backend"))

# Mirrors the chain in provider_fallback.NVIDIA_MODEL_CHAIN — kept in sync
# intentionally so the rotina exercises the same models the dispatcher rotates
# through on 429. Authoritative order from Felipe (2026-06-16) — validated by
# POST /v1/chat/completions on each model returning 200.
NVIDIA_DAILY_MODELS = [
    "minimaxai/minimax-m3",                  # 1
    "stepfun-ai/step-3.7-flash",             # 2
    "moonshotai/kimi-k2.6",                  # 3
    "deepseek-ai/deepseek-v4-flash",         # 4
    "z-ai/glm-5.1",                          # 5
    "nvidia/nemotron-3-ultra-550b-a55b",     # 6
    "nvidia/nemotron-3-super-120b-a12b",     # 7
    "qwen/qwen3.5-122b-a10b",                # 8
    "qwen/qwen3.5-397b-a17b",                # 9
    "openai/gpt-oss-120b",                   # 10
    "microsoft/phi-4-multimodal-instruct",   # 11
    "stepfun-ai/step-3.5-flash",             # 12
]

OPENROUTER_DAILY_MODELS = [
    "openrouter/owl-alpha",
]

PROMPT = (
    "Responda em PT-BR em 1-2 frases: por que modelos NVIDIA NIM são úteis "
    "para múltiplos agentes operando em paralelo com budget limitado? "
    "Cite 1 benefício operacional concreto."
)

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

LOGS_DIR = WORKSPACE / "ADWs" / "logs" / "routines"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _get_api_key(provider: str) -> str:
    """Read API key from env first, then config/providers.json."""
    if provider == "nvidia":
        env_key = os.environ.get("NVIDIA_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        if env_key:
            return env_key
        cfg_path = WORKSPACE / "config" / "providers.json"
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                ek = (cfg.get("providers", {}).get("nvidia", {})
                      .get("env_vars", {}).get("OPENAI_API_KEY", ""))
                if ek and "****" not in ek:
                    return ek
            except Exception:
                pass
        return ""
    if provider == "openrouter":
        return os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    return ""


def _call_chat(base_url: str, model: str, api_key: str, prompt: str, timeout: int = 120) -> dict:
    """Single chat completion call. Returns {ok, output, error, duration_ms, ...}."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.3,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            text = (msg.get("content") or "").strip()
            refusal = msg.get("refusal")
            if not text and refusal:
                text = f"[refusal] {refusal}"
            usage = data.get("usage", {}) or {}
            return {
                "ok": True,
                "output": text,
                "tokens_in": usage.get("prompt_tokens"),
                "tokens_out": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "duration_ms": int((time.time() - start) * 1000),
                "error": None,
                "status_code": 200,
                "finish_reason": choice.get("finish_reason"),
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1500]
        return {
            "ok": False, "output": "", "error": body,
            "duration_ms": int((time.time() - start) * 1000),
            "status_code": e.code, "tokens_in": None, "tokens_out": None, "total_tokens": None,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {
            "ok": False, "output": "", "error": str(e),
            "duration_ms": int((time.time() - start) * 1000),
            "status_code": None, "tokens_in": None, "tokens_out": None, "total_tokens": None,
        }


def _log_entry(name: str, entry: dict, today: str) -> Path:
    log_file = LOGS_DIR / f"{name}-{today}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return log_file


def run(nvidia_only: bool = True, only: list[str] | None = None) -> dict:
    """Exercise each model once. Returns summary for stdout and metrics."""
    started = _now_iso()
    today = date.today().isoformat()
    results: list[dict] = []
    summary = {"nvidia_ok": 0, "nvidia_fail": 0, "openrouter_ok": 0, "openrouter_fail": 0}

    targets_nvidia = [m for m in NVIDIA_DAILY_MODELS if not only or m in only] if only else NVIDIA_DAILY_MODELS
    nvidia_key = _get_api_key("nvidia")
    if not nvidia_key:
        print("[uso_modelos_dia] ERRO: NVIDIA API key não encontrada (env NVIDIA_API_KEY ou config/providers.json)")
        return summary

    print(f"[uso_modelos_dia] Exercitando {len(targets_nvidia)} modelos NVIDIA…", flush=True)
    for model in targets_nvidia:
        r = _call_chat(NVIDIA_BASE_URL, model, nvidia_key, PROMPT)
        entry = {
            "ts": _now_iso(),
            "routine": "uso_modelos_dia",
            "provider": "nvidia",
            "model": model,
            "ok": r["ok"],
            "duration_ms": r["duration_ms"],
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            "total_tokens": r["total_tokens"],
            "status_code": r["status_code"],
            "error": r["error"],
            "output_preview": (r["output"] or "")[:160],
        }
        results.append(entry)
        _log_entry("uso_modelos_dia", entry, today)
        if r["ok"]:
            summary["nvidia_ok"] += 1
            print(f"  ✓ NVIDIA:{model} ok ({r['duration_ms']}ms, t={r['total_tokens']})", flush=True)
        else:
            summary["nvidia_fail"] += 1
            err_snippet = (r["error"] or "").splitlines()[0][:120] if r["error"] else "?"
            print(f"  ✗ NVIDIA:{model} FAIL ({r['status_code']}) {err_snippet}", flush=True)

    # OpenRouter only on explicit request — avoid burning credits for daily exercise
    if not nvidia_only and not only:
        or_key = _get_api_key("openrouter")
        if or_key:
            print("[uso_modelos_dia] Exercitando OpenRouter…", flush=True)
            for model in OPENROUTER_DAILY_MODELS:
                r = _call_chat(OPENROUTER_BASE_URL, model, or_key, PROMPT)
                entry = {
                    "ts": _now_iso(),
                    "routine": "uso_modelos_dia",
                    "provider": "openrouter",
                    "model": model,
                    "ok": r["ok"],
                    "duration_ms": r["duration_ms"],
                    "tokens_in": r["tokens_in"],
                    "tokens_out": r["tokens_out"],
                    "total_tokens": r["total_tokens"],
                    "status_code": r["status_code"],
                    "error": r["error"],
                    "output_preview": (r["output"] or "")[:160],
                }
                results.append(entry)
                _log_entry("uso_modelos_dia", entry, today)
                if r["ok"]:
                    summary["openrouter_ok"] += 1
                    print(f"  ✓ OpenRouter:{model} ok ({r['duration_ms']}ms)", flush=True)
                else:
                    summary["openrouter_fail"] += 1
                    print(f"  ✗ OpenRouter:{model} FAIL ({r['status_code']})", flush=True)

    # Update metrics.json keyed by routine name
    _update_metrics(results)

    finished = _now_iso()
    print(f"[uso_modelos_dia] Done in {started} → {finished}. "
          f"NVIDIA ok={summary['nvidia_ok']} fail={summary['nvidia_fail']}.",
          flush=True)
    return summary


def _update_metrics(results: list[dict]) -> None:
    """Light-touch append to ADWs/logs/metrics.json so /costs reflects usage."""
    metrics_path = WORKSPACE / "ADWs" / "logs" / "metrics.json"
    today = date.today().isoformat()

    try:
        existing = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.is_file() else {}
    except Exception:
        existing = {}

    name = "uso_modelos_dia"
    entry = existing.setdefault(name, {
        "agent": "system", "runs": 0, "successes": 0, "success_rate": 0,
        "avg_seconds": 0, "total_input_tokens": 0, "total_output_tokens": 0,
        "total_cost_usd": 0.0, "avg_cost_usd": 0.0, "last_run": None,
    })

    total_runs_before = entry.get("runs", 0)
    new_runs = len(results)
    successes = sum(1 for r in results if r.get("ok"))
    new_total_tokens = sum((r.get("total_tokens") or 0) for r in results)
    new_in = sum((r.get("tokens_in") or 0) for r in results)
    new_out = sum((r.get("tokens_out") or 0) for r in results)
    total_ms = sum((r.get("duration_ms") or 0) for r in results)

    entry["runs"] += new_runs
    entry["successes"] += successes
    if entry["runs"] > 0:
        # 0-100 percentage, matching ADWs/runner.py's convention for every
        # other routine's success_rate field — this one used to store a 0-1
        # fraction instead, which every consumer (Routines.tsx, overview.py)
        # reads assuming 0-100. Confirmed live: 25/30 real successes stored
        # as 0.8333 displayed/classified as "0.8333% success" / "critical"
        # instead of the real 83.3% / "healthy".
        entry["success_rate"] = round((entry["successes"] / entry["runs"]) * 100, 1)
    # Aggregate token totals
    entry["total_input_tokens"] = entry.get("total_input_tokens", 0) + new_in
    entry["total_output_tokens"] = entry.get("total_output_tokens", 0) + new_out
    # Running average seconds (weighted by call count)
    prev_avg_s = entry.get("avg_seconds", 0) or 0
    prev_count = total_runs_before
    entry["avg_seconds"] = round(
        ((prev_avg_s * prev_count) + (total_ms / 1000)) / max(1, entry["runs"]), 3
    )
    # Light cost estimate: ~$0.00018/1k input, $0.0006/1k output (rough NVIDIA tier-1 estimate)
    est_cost = (new_in / 1_000_000) * 0.18 + (new_out / 1_000_000) * 0.60
    entry["total_cost_usd"] = round(entry.get("total_cost_usd", 0) + est_cost, 6)
    entry["avg_cost_usd"] = round(entry["total_cost_usd"] / max(1, entry["runs"]), 6)
    entry["last_run"] = _now_iso()

    metrics_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--all-providers", action="store_true",
                   help="Também exercita OpenRouter (desligado por padrão).")
    p.add_argument("--models", type=str, default="",
                   help="Lista separada por vírgula — só exercita esses modelos.")
    args = p.parse_args()

    only = [m.strip() for m in args.models.split(",") if m.strip()] or None
    summary = run(nvidia_only=not args.all_providers, only=only)
    # Exit 0 if at least one NVIDIA model succeeded
    return 0 if (summary["nvidia_ok"] > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
