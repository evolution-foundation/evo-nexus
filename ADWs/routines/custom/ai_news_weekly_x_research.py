#!/usr/bin/env python3
"""ADW: AI News Weekly X Research -- coleta X e monta fila editorial | @sage/@mako"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from runner import banner, run_claude, send_telegram, summary  # noqa: E402


WORKSPACE = Path(__file__).resolve().parents[3]
SKILL_DIR = WORKSPACE / ".claude" / "skills" / "social-ai-trends-blog"
OUT_DIR = WORKSPACE / "workspace" / "marketing" / "ai-news"
RAW_OUT = OUT_DIR / "trends_raw.json"
QUEUE_OUT = OUT_DIR / "queue.json"


def _week_id() -> str:
    year, week, _ = datetime.now().isocalendar()
    return f"{year}-W{week:02d}"


def _notify(text: str) -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if chat_id:
        send_telegram(text, chat_id=chat_id)


def _write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stdout = result.get("stdout", "") or ""
    path.write_text(stdout.strip() + "\n", encoding="utf-8")


def _fallback_queue(raw: dict, week: str) -> dict:
    product_hooks = {
        "Agentes de IA / Agentic": "Evo AI: agentes conectados ao CRM e ao WhatsApp.",
        "IA para código/dev": "Evolution API: automacao e infraestrutura aberta para times tecnicos.",
        "Modelos novos (GPT/Claude/Gemini/Llama)": "Sistema Britto: escolha pragmatica de modelos por uso real.",
        "OpenAI / Anthropic / Big Labs": "Evo AI: arquitetura multi-modelo sem dependencia cega de um unico lab.",
        "IA generativa de imagem/vídeo": "Marketing com IA: producao visual com aprovacao humana.",
        "Automação / Workflows com IA": "Evo AI + Evolution API: workflows reais em canais de atendimento.",
        "Chatbots / Atendimento / WhatsApp": "Evolution API: WhatsApp open source para atendimento com IA.",
        "IA no trabalho / produtividade": "Sistema Britto: IA operacional, nao palestra.",
        "Open source / modelos abertos": "Evolution API: open source, controle de infra e menor lock-in.",
        "Regulação / ética / segurança": "Sistema Britto: governanca e uso responsavel em operacoes reais.",
        "Negócios / startups / investimento": "Sistema Britto: separar hype de ROI operacional.",
    }
    items = []
    topics_ranked = raw.get("topics_ranked", [])[:12]
    if not topics_ranked:
        topics_ranked = [
            {"topic": "Agentes de IA / Agentic", "count": 0, "total_score": 0, "top_tweets": []},
            {"topic": "Automação / Workflows com IA", "count": 0, "total_score": 0, "top_tweets": []},
            {"topic": "Chatbots / Atendimento / WhatsApp", "count": 0, "total_score": 0, "top_tweets": []},
            {"topic": "IA para código/dev", "count": 0, "total_score": 0, "top_tweets": []},
            {"topic": "Open source / modelos abertos", "count": 0, "total_score": 0, "top_tweets": []},
            {"topic": "Modelos novos (GPT/Claude/Gemini/Llama)", "count": 0, "total_score": 0, "top_tweets": []},
            {"topic": "Regulação / ética / segurança", "count": 0, "total_score": 0, "top_tweets": []},
        ]

    for idx, topic in enumerate(topics_ranked, 1):
        top_tweets = topic.get("top_tweets", [])[:3]
        urls = [tweet.get("url", "") for tweet in top_tweets if tweet.get("url")]
        label = topic.get("topic", "IA geral")
        items.append({
            "id": f"ai-news-{week}-{idx:02d}",
            "status": "queued",
            "priority": idx,
            "title": f"{label}: o sinal do X para negocios com IA",
            "angle": (
                f"O tema '{label}' concentrou {topic.get('count', 0)} sinais no X "
                f"e score agregado {round(topic.get('total_score', 0), 1)}."
            ),
            "product_hook": product_hooks.get(label, "Sistema Britto: transformar tendencia em execucao."),
            "source_urls": urls,
            "target_reader": "fundadores, operadores e lideres que precisam aplicar IA em negocios reais",
            "draft_due": "daily-19h",
        })
    return {
        "generated_at": datetime.now().isoformat(),
        "week": week,
        "status": "ready",
        "source": "x-api" if raw.get("topics_ranked") else "fallback-editorial",
        "items": items,
    }


def _queue_needs_fallback(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    return not data.get("items")


def main() -> None:
    banner("AI News Weekly X Research", "X trends -> Sage -> Mako -> fila editorial")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fetch_script = SKILL_DIR / "fetch_ai_trends.py"
    result = subprocess.run(
        [sys.executable, str(fetch_script), "--days", "7", "--per-query", "100", "--out", str(RAW_OUT)],
        cwd=str(WORKSPACE),
        timeout=300,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "fetch_ai_trends failed")

    week = _week_id()
    pauta_path = OUT_DIR / f"[C]pauta-{week}.md"
    sage_path = OUT_DIR / f"[C]sage-curadoria-{week}.md"

    raw_preview = json.loads(RAW_OUT.read_text(encoding="utf-8"))
    collected = raw_preview.get("total_tweets_collected", 0)
    topics = len(raw_preview.get("topics_ranked", []))
    api_errors = raw_preview.get("api_errors", [])

    results = []
    sage_result = run_claude(
        f"""
Voce e Sage. Leia:
- {RAW_OUT}
- .claude/skills/social-ai-trends-blog/SKILL.md
- memory/index.md se existir

Objetivo: escolher os viral topics mais relevantes para Sistema Britto/Evolution.
Nao copie tweets. Use os tweets como sinal de demanda.

Escreva {sage_path} com:
- ranking dos 15 temas
- motivo estrategico para Sistema Britto
- gancho de produto quando fizer sentido
- risco/editorial guardrail
- prioridade: P0/P1/P2
""".strip(),
        log_name="ai-news-weekly-sage",
        timeout=900,
        agent="sage-strategy",
    )
    results.append(sage_result)
    _write_result(sage_path, sage_result)

    mako_result = run_claude(
        f"""
Voce e Mako. Leia:
- {RAW_OUT}
- {sage_path}
- .claude/skills/social-ai-trends-blog/SKILL.md

Monte a pauta semanal e a fila diaria de AI News.

Crie/atualize:
1. {pauta_path}
2. {QUEUE_OUT}

O queue.json deve ser JSON valido, com:
{{
  "generated_at": "...",
  "week": "{week}",
  "status": "ready",
  "items": [
    {{
      "id": "ai-news-{week}-01",
      "status": "queued",
      "priority": 1,
      "title": "...",
      "angle": "...",
      "product_hook": "...",
      "source_urls": ["..."],
      "target_reader": "...",
      "draft_due": "daily-19h"
    }}
  ]
}}

Inclua 7 a 15 itens. Priorize relevancia para Sistema Britto, nao hype vazio.
""".strip(),
        log_name="ai-news-weekly-mako",
        timeout=900,
        agent="mako-marketing",
    )
    results.append(mako_result)
    _write_result(pauta_path, mako_result)

    if _queue_needs_fallback(QUEUE_OUT):
        QUEUE_OUT.write_text(
            json.dumps(_fallback_queue(raw_preview, week), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    summary(results, "AI News Weekly X Research")

    if all(r.get("success") for r in results):
        queue_source = "x-api"
        try:
            queue_source = json.loads(QUEUE_OUT.read_text(encoding="utf-8")).get("source", queue_source)
        except Exception:
            pass
        error_note = ""
        if api_errors:
            first = api_errors[0]
            error_note = f" X API: HTTP {first.get('status', '?')}."
        _notify(
            f"AI News semanal pronto: {collected} tweets, {topics} temas. "
            f"Fonte da fila: {queue_source}.{error_note} Fila: {QUEUE_OUT}"
        )


if __name__ == "__main__":
    main()
