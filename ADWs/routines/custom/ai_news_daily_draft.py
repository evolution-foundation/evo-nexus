#!/usr/bin/env python3
"""ADW: AI News Daily Draft -- cria draft diario e pede aprovacao | @sage/@quill/@raven/@mako"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from runner import banner, run_claude, send_telegram, summary  # noqa: E402


WORKSPACE = Path(__file__).resolve().parents[3]
AI_NEWS_DIR = WORKSPACE / "workspace" / "marketing" / "ai-news"
QUEUE_PATH = AI_NEWS_DIR / "queue.json"
DRAFTS_DIR = AI_NEWS_DIR / "drafts"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _notify(text: str) -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if chat_id:
        send_telegram(text, chat_id=chat_id)


def _load_next_item() -> dict:
    if not QUEUE_PATH.exists():
        raise FileNotFoundError(f"Fila nao encontrada: {QUEUE_PATH}")
    data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    items = data.get("items", [])
    for item in sorted(items, key=lambda x: x.get("priority", 999)):
        if item.get("status") in ("queued", "ready"):
            return item
    raise RuntimeError("Fila sem itens queued/ready")


def _update_queue_item(item_id: str, updates: dict) -> None:
    data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    for item in data.get("items", []):
        if item.get("id") == item_id:
            item.update(updates)
            break
    else:
        raise RuntimeError(f"Item nao encontrado na fila: {item_id}")
    data["updated_at"] = datetime.now().isoformat()
    QUEUE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stdout = result.get("stdout", "") or ""
    path.write_text(stdout.strip() + "\n", encoding="utf-8")


def _has_substantive_output(path: Path, min_chars: int = 400) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8").strip()
    weak_starts = (
        "Vou ler ",
        "Vou começar ",
        "Let me ",
    )
    if len(text) < min_chars:
        return False
    return not text.startswith(weak_starts)


def main() -> None:
    banner("AI News Daily Draft", "fila -> draft Ghost -> aprovacao Telegram")
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

    item = _load_next_item()
    item_id = item["id"]
    today = _today()
    run_dir = DRAFTS_DIR / f"{today}-{item_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    selection_path = run_dir / "01-sage-selection.md"
    draft_path = run_dir / "02-quill-draft.md"
    review_path = run_dir / "03-raven-review.md"
    final_path = run_dir / "04-final-ghost-brief.md"

    item_json = json.dumps(item, ensure_ascii=False, indent=2)
    results = []
    _update_queue_item(item_id, {
        "status": "drafting",
        "draft_started_at": datetime.now().isoformat(),
        "draft_dir": str(run_dir),
    })

    sage_result = run_claude(
        f"""
Voce e Sage. Selecione e refine este item da fila para o AI News de hoje:

{item_json}

Leia tambem {QUEUE_PATH} para contexto.
Escreva {selection_path} com:
- tese editorial
- por que isso importa agora
- promessa do post
- publico-alvo
- 3 fontes/links a validar
- angulo Sistema Britto/Evolution
""".strip(),
        log_name="ai-news-daily-sage",
        timeout=600,
        agent="sage-strategy",
    )
    results.append(sage_result)
    _write_result(selection_path, sage_result)

    quill_result = run_claude(
        f"""
Voce e Quill. Leia {selection_path}.

Escreva o blogpost AI News em portugues BR em {draft_path}.
Formato:
- titulo SEO
- slug sugerido
- meta description
- HTML/Markdown do artigo
- fontes citadas com links
- bloco "por que isso importa para negocios brasileiros"
- CTA sutil para Sistema Britto/Evolution

Nao invente fatos. Se uma fonte nao puder ser validada, marque como pendente.
""".strip(),
        log_name="ai-news-daily-quill",
        timeout=900,
        agent="quill-writer",
    )
    results.append(quill_result)
    _write_result(draft_path, quill_result)

    raven_result = run_claude(
        f"""
Voce e Raven. Revise {draft_path}.

Escreva {review_path} com:
- problemas factuais
- riscos de promessa/clickbait
- links fracos ou faltantes
- ajustes obrigatorios antes do draft Ghost
- veredito: APPROVE_DRAFT ou REVISE
""".strip(),
        log_name="ai-news-daily-raven",
        timeout=600,
        agent="raven-critic",
    )
    results.append(raven_result)
    _write_result(review_path, raven_result)

    mako_result = run_claude(
        f"""
Voce e Mako. Leia:
- {draft_path}
- {review_path}
- .claude/skills/social-ai-trends-blog/SKILL.md
- .claude/skills/custom-int-ghost/SKILL.md
- /home/sistemabritto/.codex/skills/evolution-blog-post-ghost/SKILL.md se acessivel

Tarefa:
1. Aplicar revisao/humanizacao no texto.
2. Fazer SEO e link building interno quando houver links relevantes.
3. Gerar ou preparar imagem/thumbnail usando o padrao Sistema Britto.
4. Criar um Ghost draft, status draft. Nao publicar.
5. Escrever {final_path} com titulo, slug, preview/admin URL, caminho da imagem, pendencias e texto da mensagem de aprovacao.

Regra absoluta: nao publicar e nao compartilhar no LinkedIn. Aprovacao humana primeiro.
Inclua no final uma linha:
TELEGRAM_APPROVAL: [mensagem curta com titulo + link/caminho + pergunta de OK]
""".strip(),
        log_name="ai-news-daily-mako",
        timeout=1200,
        agent="mako-marketing",
    )
    results.append(mako_result)
    _write_result(final_path, mako_result)

    summary(results, "AI News Daily Draft")

    output_ok = all(
        _has_substantive_output(path)
        for path in (selection_path, draft_path, review_path, final_path)
    )

    if all(r.get("success") for r in results) and output_ok:
        _update_queue_item(item_id, {
            "status": "drafted",
            "drafted_at": datetime.now().isoformat(),
            "draft_dir": str(run_dir),
            "final_brief": str(final_path),
            "approval_status": "pending",
        })
        approval = ""
        stdout = results[-1].get("stdout", "")
        for line in reversed(stdout.splitlines()):
            if line.strip().startswith("TELEGRAM_APPROVAL:"):
                approval = line.split(":", 1)[1].strip()
                break
        if not approval:
            approval = f"AI News draft pronto para aprovacao: {final_path}"
        _notify(approval)
    else:
        _update_queue_item(item_id, {
            "status": "failed",
            "failed_at": datetime.now().isoformat(),
            "draft_dir": str(run_dir),
            "final_brief": str(final_path),
            "failure_reason": "agent_failed_or_empty_output",
        })


if __name__ == "__main__":
    main()
