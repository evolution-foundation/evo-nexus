#!/usr/bin/env python3
"""int-gemini — wrapper CLI para a Google Gemini API (AI Studio).

Chama a Generative Language API (https://generativelanguage.googleapis.com) via
`GEMINI_API_KEY`. Pensado para OFFLOAD de tarefas baratas/volumosas dos modelos
Claude/Opus: classificação, sumarização, 1º rascunho, pesquisa web com grounding.

Comandos:
  ask "<prompt>" [--model <id>] [--system "<sys>"] [--json] [--grounding]
                    Gera resposta. --json liga responseMimeType application/json.
                    --grounding liga Google Search grounding (quando suportado).
  models            Lista modelos acessíveis pela chave (generateContent).
  smoke             Health-check: exit 0 SEMPRE + JSON {overall, steps[], duration_ms}.

Modelos default e alternativas (free tier AI Studio):
  gemini-2.5-flash        (default — bom custo/qualidade)
  gemini-2.5-flash-lite   (mais barato/rápido — classificação em volume)
  gemini-2.5-pro          (raciocínio — janela 1M, RPD baixo no free)

Sem dependências externas — só stdlib (urllib). Chave sempre via env
`GEMINI_API_KEY` (com fallback do .env loader). Nunca hardcode segredo.

Retry: 1 retry após backoff em 429 / 5xx / timeout (custom-integration-retry).
Logging estruturado em stderr. Saída em stdout sempre JSON estruturado.
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
from pathlib import Path


# ── env ──────────────────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[4] / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()


_load_dotenv()

API_BASE = os.environ.get(
    "GEMINI_API_BASE", "https://generativelanguage.googleapis.com"
).rstrip("/")
API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
VERSION = "1.1.0"
UA = f"evo-nexus-int-gemini/{VERSION}"
TIMEOUT = 60
RETRY_AFTER_S = 20
RETRYABLE_CODES = {429, 500, 502, 503, 504}


# ── logging ──────────────────────────────────────────────────────────────────

def _log(level: str, msg: str) -> None:
    print(f"[int-gemini] {level}: {msg}", file=sys.stderr)


# ── guard de confidencialidade ─────────────────────────────────────────────────
# O free tier do Gemini TREINA o modelo com prompts+respostas. Antes de mandar
# um prompt, varremos heurísticas e, por padrão, BLOQUEAMOS. `--allow-sensitive`
# é o override consciente do operador.
#
# IMPORTANTE — guard-rail PARCIAL, não garantia:
#   • CONFIÁVEL p/ dado ESTRUTURADO: CPF, CNPJ, cartão, CEP, e-mail, R$.
#   • BEST-EFFORT (gera falso-negativo): NOME de pessoa, ENDEREÇO, telefone falado,
#     valor por extenso ("cinquenta mil"). Heurística não substitui julgamento.
#   Tarefas que carregam dado de cliente por natureza (resumo de transcrição de reunião, ficha de
#   lead) DEVEM ser redigidas/anonimizadas ou ir p/ tier no-train — NÃO confie só
#   no guard. Aceitamos falso-positivo de propósito (o --allow-sensitive resolve);
#   o que perseguimos é reduzir o falso-NEGATIVO nos vetores reais.

# A ordem importa: padrões mais específicos primeiro (cartão/CEP antes de telefone)
# para que o de-dup por sobreposição não rotule um cartão como "telefone".
_CONFIDENTIAL_PATTERNS: list[tuple[str, str]] = [
    # CPF: 000.000.000-00 ou 11 dígitos isolados
    ("cpf", r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
    # CNPJ: 00.000.000/0000-00
    ("cnpj", r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
    # E-mail
    ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # CEP: 00000-000 ou 00000000 (8 dígitos)
    ("cep", r"(?<!\d)\d{5}-?\d{3}(?!\d)"),
    # Cartão de crédito: grupos de 4 separados por espaço/hífen, OU 13-16 dígitos
    # colados. Não casa "+55..." (exige começar em dígito). Vem ANTES do telefone
    # p/ que uma sequência longa de dígitos seja rotulada cartão, não telefone.
    ("cartao", r"\b(?:\d{4}(?:[ -]\d{4}){2,3}|\d{13,16})\b"),
    # Telefone BR — cobre: DDD opcional com (), +55/55/0 prefixo, nono dígito
    # SEPARADO ("(11) 9 9999-9999"), celular junto, e fixo de 8 dígitos com
    # separador ("9999-8888"). O separador no fixo evita casar qualquer 8 dígitos.
    ("telefone",
     r"(?<![\d.])(?:(?:\+?55[\s-]?|0)?\(?\d{2}\)?[\s-]?9?[\s-]?\d{4}[\s-]?\d{4}"
     r"|\d{4}[\s-]\d{4})(?![\d.])"),
    # Endereço: logradouro + número
    ("endereco",
     r"\b(?:rua|av\.?|avenida|alameda|al\.?|travessa|trav\.?|rodovia|rod\.?|"
     r"praça|praca|estrada|estr\.?|largo)\s+[A-Za-zÀ-ÿ0-9.\- ]{2,40}?,?\s*\d{1,5}\b"),
    # Valor financeiro em R$ (numérico)
    ("valor_financeiro", r"R\$\s?\d[\d.,]*"),
    # Valor por extenso: "<número> mil/milhão/milhões/bilhão" (best-effort)
    ("valor_extenso",
     r"\b(?:\d{1,3}|um|dois|tr[êe]s|quatro|cinco|seis|sete|oito|nove|dez|vinte|"
     r"trinta|quarenta|cinquenta|cem|cento|duzentos|trezentos|quinhentos|mil)\s+"
     r"(?:mil|milh[õo]es?|bilh[õo]es?)\b"),
    # PIX explícito
    ("pix", r"\bpix\b"),
    # Termos de contrato/confidencialidade
    ("contrato", r"\b(?:contrato|confidencial|sigiloso|cl[áa]usula|nda|honor[áa]rios?)\b"),
    # Chave de API / segredo (evita vazar segredo no prompt)
    ("segredo", r"\b(?:api[_-]?key|secret|token|password|senha|bearer)\b"),
]

_CONFIDENTIAL_RE = [(name, re.compile(pat, re.IGNORECASE))
                    for name, pat in _CONFIDENTIAL_PATTERNS]

# Nome de pessoa perto de contexto de cliente/lead. Best-effort: sequência de 2+
# palavras Capitalizadas (com de/da/dos opcional) a até ~40 chars de um termo de
# papel. Pega "lead João Silva", "Maria Souza, Diretora", "sócio João Silva".
_ROLE_CONTEXT = (r"lead|leads|cliente|clientes|contato|contatos|prospect|prospects|"
                 r"diretor[ae]?|s[óo]ci[oa]|propriet[áa]ri[oa]|gerente|respons[áa]vel|"
                 r"corretor[ae]?|comprador[ae]?|titular|sr\.?|sra\.?|dr\.?|dra\.?")
_FULLNAME = r"[A-ZÀ-Ý][a-zà-ÿ]{1,}(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]{1,}){1,3}"
_NAME_CONTEXT_RE = re.compile(
    rf"(?:(?:{_ROLE_CONTEXT})[\s:,\-]+(?P<n1>{_FULLNAME}))"
    rf"|(?:(?P<n2>{_FULLNAME})[\s,\-]+(?:{_ROLE_CONTEXT})\b)",
    re.IGNORECASE,
)


def scan_confidential(text: str) -> list[dict]:
    """Varre o texto e devolve achados de confidencialidade [{type, sample}].

    Heurística — guard-rail PARCIAL (ver nota no topo da seção). Falsos-positivos
    são esperados e aceitos (o operador resolve com `--allow-sensitive`); o foco é
    minimizar o falso-NEGATIVO. Lista vazia = nada detectado.

    De-dup por sobreposição: um mesmo trecho não vira dois tipos (ex.: um cartão
    não é também rotulado "telefone"). O primeiro padrão a casar vence o span.
    """
    findings: list[dict] = []
    claimed: list[tuple[int, int]] = []  # spans já reivindicados por outro tipo

    def _overlaps(s: int, e: int) -> bool:
        return any(s < ce and cs < e for cs, ce in claimed)

    for name, rx in _CONFIDENTIAL_RE:
        for m in rx.finditer(text):
            s, e = m.span()
            if _overlaps(s, e):
                continue
            claimed.append((s, e))
            sample = m.group(0)
            masked = sample[:3] + "…" if len(sample) > 4 else sample
            findings.append({"type": name, "sample": masked})
            break  # um achado por tipo basta p/ reportar

    # nome+contexto: padrão composto (grupo n1 OU n2), tratado à parte
    nm = _NAME_CONTEXT_RE.search(text)
    if nm:
        name_str = nm.group("n1") or nm.group("n2") or ""
        if name_str:
            findings.append({"type": "nome_contexto", "sample": name_str[:3] + "…"})

    return findings


# ── HTTP ─────────────────────────────────────────────────────────────────────

class GeminiError(Exception):
    """Erro de chamada à API Gemini (status + corpo)."""


def _request(method: str, path: str, *, body: dict | None = None, _attempt: int = 0) -> dict:
    """Faz um request à Gemini API e devolve sempre um dict parseado.

    Levanta GeminiError em falha não-retryável. Type-safe: nunca retorna bytes
    nem string crua — sempre dict.
    """
    if not API_KEY:
        raise GeminiError("GEMINI_API_KEY ausente no ambiente/.env")

    url = f"{API_BASE}{path}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}key={API_KEY}"

    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        if e.code in RETRYABLE_CODES and _attempt < 1:
            _log("WARN", f"HTTP {e.code} em {path} — retry em {RETRY_AFTER_S}s")
            time.sleep(RETRY_AFTER_S)
            return _request(method, path, body=body, _attempt=_attempt + 1)
        # extrai a mensagem da API se houver
        detail = raw[:500]
        try:
            detail = json.loads(raw).get("error", {}).get("message", detail)
        except (ValueError, AttributeError):
            pass
        raise GeminiError(f"HTTP {e.code} em {path}: {detail}")
    except (urllib.error.URLError, TimeoutError) as e:
        if _attempt < 1:
            _log("WARN", f"conexão falhou em {path} ({e}) — retry em {RETRY_AFTER_S}s")
            time.sleep(RETRY_AFTER_S)
            return _request(method, path, body=body, _attempt=_attempt + 1)
        raise GeminiError(f"conexão falhou em {path}: {e}")

    try:
        parsed = json.loads(raw)
    except ValueError:
        raise GeminiError(f"resposta não-JSON em {path}: {raw[:200]}")
    if not isinstance(parsed, dict):
        return {"raw": parsed}
    return parsed


# ── roteamento de modelo por tipo de tarefa ───────────────────────────────────
# Mapeia um "tipo de tarefa" pro modelo Gemini certo. Centraliza a tabela
# "Quando usar Gemini vs Claude" do SKILL.md em código testável. NÃO decide
# Gemini-vs-Claude (isso é do agente) — só escolhe o modelo Gemini quando o
# offload já foi decidido.

_ROUTING: dict[str, str] = {
    # classificação/triagem em volume → mais barato/rápido
    "classify": "gemini-2.5-flash-lite",
    "triage": "gemini-2.5-flash-lite",
    "extract": "gemini-2.5-flash-lite",
    # sumarização / rascunho / texto longo → flash (janela 1M, custo baixo)
    "summarize": "gemini-2.5-flash",
    "draft": "gemini-2.5-flash",
    "translate": "gemini-2.5-flash",
    # pesquisa web factual → flash + grounding (grounding ligado pelo caller)
    "research": "gemini-2.5-flash",
    # raciocínio mais pesado dentro do que o offload cobre → pro (pago no free)
    "reason": "gemini-2.5-pro",
}
DEFAULT_ROUTE_MODEL = "gemini-2.5-flash"


def route_model(task_type: str) -> str:
    """Devolve o modelo Gemini recomendado p/ um tipo de tarefa (fallback flash)."""
    return _ROUTING.get(task_type.strip().lower(), DEFAULT_ROUTE_MODEL)


# ── parsing de resposta ──────────────────────────────────────────────────────

def _extract_text(resp: dict) -> str:
    """Concatena as partes de texto do primeiro candidate."""
    candidates = resp.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", []) or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def _extract_grounding(resp: dict) -> list[dict]:
    """Extrai as fontes do groundingMetadata (quando --grounding usado)."""
    candidates = resp.get("candidates") or []
    if not candidates:
        return []
    gm = candidates[0].get("groundingMetadata") or {}
    chunks = gm.get("groundingChunks") or []
    out = []
    for c in chunks:
        web = c.get("web") or {}
        if web.get("uri"):
            out.append({"title": web.get("title", ""), "uri": web["uri"]})
    return out


# ── comandos ─────────────────────────────────────────────────────────────────

def cmd_ask(args) -> int:
    model = args.model or DEFAULT_MODEL

    # system instruction pode vir de --system OU --system-file (auto-melhoria #12)
    system = args.system
    if getattr(args, "system_file", None):
        try:
            system = Path(args.system_file).read_text(encoding="utf-8")
        except OSError as e:
            print(json.dumps({"ok": False, "error": f"system-file: {e}", "model": model}))
            return 1

    # guard de confidencialidade — bloqueia por padrão se detectar dado sensível
    if not args.allow_sensitive:
        findings = scan_confidential(args.prompt)
        if system:
            findings += scan_confidential(system)
        if findings:
            types = sorted({f["type"] for f in findings})
            print(json.dumps({
                "ok": False,
                "error": "BLOQUEADO: dado potencialmente confidencial detectado "
                         "(free tier do Gemini treina com o prompt). Redija/anonimize "
                         "ou use --allow-sensitive para override consciente.",
                "confidential_findings": findings,
                "detected_types": types,
                "model": model,
            }, ensure_ascii=False, indent=2))
            _log("WARN", f"prompt bloqueado por guard: {', '.join(types)}")
            return 2

    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": args.prompt}]}],
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    gen_config: dict = {}
    if args.json:
        gen_config["responseMimeType"] = "application/json"
    if gen_config:
        payload["generationConfig"] = gen_config
    if args.grounding:
        # Tool de Google Search grounding (Gemini 2.x).
        payload["tools"] = [{"google_search": {}}]

    # fallback transparente p/ flash quando o modelo escolhido falha (auto-melhoria #5)
    # — útil pro flash-lite, que devolve 503 "high demand" no free tier.
    tried = [model]
    if getattr(args, "fallback", False) and model != DEFAULT_ROUTE_MODEL:
        tried.append(DEFAULT_ROUTE_MODEL)

    resp = None
    used_model = model
    last_err: GeminiError | None = None
    for m in tried:
        try:
            resp = _request("POST", f"/v1beta/models/{m}:generateContent", body=payload)
            used_model = m
            if m != model:
                _log("WARN", f"fallback: {model} falhou, respondido por {m}")
            break
        except GeminiError as e:
            last_err = e
    if resp is None:
        print(json.dumps({"ok": False, "error": str(last_err), "model": model}), file=sys.stdout)
        _log("ERROR", str(last_err))
        return 1

    text = _extract_text(resp)
    usage = resp.get("usageMetadata", {})
    out = {
        "ok": True,
        "model": used_model,
        "text": text,
        "grounded": bool(args.grounding),
        "sources": _extract_grounding(resp) if args.grounding else [],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount"),
            "candidates_tokens": usage.get("candidatesTokenCount"),
            "total_tokens": usage.get("totalTokenCount"),
        },
        "finish_reason": (resp.get("candidates") or [{}])[0].get("finishReason"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_models(_args) -> int:
    try:
        resp = _request("GET", "/v1beta/models")
    except GeminiError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        _log("ERROR", str(e))
        return 1
    models = []
    for m in resp.get("models", []):
        methods = m.get("supportedGenerationMethods", []) or []
        if "generateContent" in methods:
            models.append({
                "name": m.get("name", "").replace("models/", ""),
                "input_token_limit": m.get("inputTokenLimit"),
                "output_token_limit": m.get("outputTokenLimit"),
            })
    print(json.dumps({"ok": True, "count": len(models), "models": models}, indent=2))
    return 0


def cmd_route(args) -> int:
    """Imprime o modelo recomendado para um tipo de tarefa."""
    model = route_model(args.task_type)
    known = args.task_type.strip().lower() in _ROUTING
    if not known:
        _log("WARN", f"tipo de tarefa desconhecido '{args.task_type}' — fallback {model}")
    print(json.dumps({
        "ok": True, "task_type": args.task_type, "model": model, "known": known,
    }, ensure_ascii=False))
    return 0


def cmd_scan(args) -> int:
    """Roda só o guard de confidencialidade num texto — sem chamar a API."""
    findings = scan_confidential(args.text)
    print(json.dumps({
        "ok": True, "safe": not findings,
        "confidential_findings": findings,
        "detected_types": sorted({f["type"] for f in findings}),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_smoke(_args) -> int:
    """Health-check — sempre exit 0, JSON estruturado. Gracioso sem chave."""
    t0 = time.time()
    steps: list[dict] = []
    overall = "PASS"

    # step 1: env — chave presente?
    ts = time.time()
    if API_KEY:
        steps.append({"step": "env", "status": "PASS", "duration_ms": round((time.time() - ts) * 1000)})
    else:
        steps.append({
            "step": "env", "status": "FAIL",
            "error": "GEMINI_API_KEY ausente",
            "duration_ms": round((time.time() - ts) * 1000),
        })
        overall = "FAIL"

    # step 2: chamada mínima (só se a chave existir) — não desperdiça quota à toa
    ts = time.time()
    if not API_KEY:
        steps.append({"step": "generate", "status": "SKIP", "duration_ms": 0})
    else:
        try:
            resp = _request("POST", f"/v1beta/models/{DEFAULT_MODEL}:generateContent", body={
                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": 5},
            })
            if _extract_text(resp) or resp.get("candidates"):
                steps.append({"step": "generate", "status": "PASS", "duration_ms": round((time.time() - ts) * 1000)})
            else:
                steps.append({
                    "step": "generate", "status": "FAIL",
                    "error": "resposta sem candidates",
                    "duration_ms": round((time.time() - ts) * 1000),
                })
                overall = "FAIL"
        except GeminiError as e:
            steps.append({
                "step": "generate", "status": "FAIL",
                "error": str(e)[:300],
                "duration_ms": round((time.time() - ts) * 1000),
            })
            overall = "FAIL"

    # step 3: guard de confidencialidade funcional (sem rede — sempre roda)
    ts = time.time()
    pos = scan_confidential("meu CPF é 123.456.789-00")
    neg = scan_confidential("resumir transcript de reunião interna")
    if pos and not neg:
        steps.append({"step": "guard", "status": "PASS", "duration_ms": round((time.time() - ts) * 1000)})
    else:
        steps.append({
            "step": "guard", "status": "FAIL",
            "error": f"guard inconsistente (pos={bool(pos)} neg={bool(neg)})",
            "duration_ms": round((time.time() - ts) * 1000),
        })
        overall = "FAIL"

    # step 4: modelos esperados acessíveis pela chave (só com chave)
    ts = time.time()
    if not API_KEY:
        steps.append({"step": "models", "status": "SKIP", "duration_ms": 0})
    else:
        try:
            resp = _request("GET", "/v1beta/models")
            names = {m.get("name", "").replace("models/", "")
                     for m in resp.get("models", [])
                     if "generateContent" in (m.get("supportedGenerationMethods") or [])}
            if DEFAULT_MODEL in names:
                steps.append({
                    "step": "models", "status": "PASS",
                    "found": len(names), "default_accessible": True,
                    "duration_ms": round((time.time() - ts) * 1000),
                })
            else:
                steps.append({
                    "step": "models", "status": "FAIL",
                    "error": f"modelo default {DEFAULT_MODEL} não acessível pela chave",
                    "duration_ms": round((time.time() - ts) * 1000),
                })
                overall = "FAIL"
        except GeminiError as e:
            steps.append({
                "step": "models", "status": "FAIL",
                "error": str(e)[:300],
                "duration_ms": round((time.time() - ts) * 1000),
            })
            overall = "FAIL"

    print(json.dumps({"overall": overall, "steps": steps, "duration_ms": round((time.time() - t0) * 1000)}, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="gemini_client.py", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=f"int-gemini {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("ask", help="Gera resposta a partir de um prompt")
    a.add_argument("prompt", help="Texto do prompt")
    a.add_argument("--model", default=None, help=f"Modelo (default {DEFAULT_MODEL})")
    a.add_argument("--system", default=None, help="System instruction")
    a.add_argument("--system-file", default=None, help="System instruction de um arquivo")
    a.add_argument("--json", action="store_true",
                   help="Pede ao MODELO p/ responder em JSON (responseMimeType)")
    a.add_argument("--grounding", action="store_true", help="Liga Google Search grounding")
    a.add_argument("--fallback", action="store_true",
                   help="Se o modelo falhar, tenta gemini-2.5-flash (ex.: 503 do flash-lite)")
    a.add_argument("--allow-sensitive", action="store_true",
                   help="Override consciente do guard de confidencialidade")
    a.set_defaults(func=cmd_ask)

    r = sub.add_parser("route", help="Modelo recomendado p/ um tipo de tarefa")
    r.add_argument("task_type", help="classify|triage|extract|summarize|draft|translate|research|reason")
    r.set_defaults(func=cmd_route)

    sc = sub.add_parser("scan", help="Roda só o guard de confidencialidade (sem rede)")
    sc.add_argument("text", help="Texto a varrer")
    sc.set_defaults(func=cmd_scan)

    sub.add_parser("models", help="Lista modelos acessíveis").set_defaults(func=cmd_models)
    sub.add_parser("smoke", help="Health-check — sempre exit 0, JSON").set_defaults(func=cmd_smoke)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
