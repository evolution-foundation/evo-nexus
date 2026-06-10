#!/usr/bin/env python3
"""
competitor_intel.py — coletor de inteligência competitiva REAL (MUST #2 da spec).

Reutiliza o `dataforseo_client.py` que JÁ EXISTE em `mkt-seo-ops` (infra já paga e
autenticada) via importlib, sem duplicar credencial nem cliente HTTP. Cobre dois eixos:

  1. SERP features + AI Overview presence + intent por keyword (DataForSEOClient.keyword_intel)
  2. Keyword gap domínio-vs-domínio (DataForSEO Labs domain_intersection)

REGRA DURA — anti-fabricação (spec MUST #1):
  Toda saída carrega `source`, `url` (quando aplicável) e `collected_at`.
  Sem credencial → status "N/A" com instrução. Erro de rede/API → "N/A" com razão.
  O LLM consumidor DEVE tratar N/A como "não verificado / pesquisar", nunca como zero.

LIMITES DE FONTE (spec MUST #3 — não observável por este script):
  - Spend do concorrente, keyword que disparou o anúncio, targeting Meta/Google.
  - Criativos Meta: Ad Library API restrita a político/social-issue + só EU em 2026;
    uso comercial é UI-only (facebook.com/ads/library) — NÃO existe API comercial.
  - Criativos Google: Ads Transparency Center sem API pública (até v23/jan-2026);
    não mostra spend, keyword-trigger nem targeting.
  Esses eixos exigem coletor de UI (SHOULD #4/#5 da spec) — fora do escopo aqui.

Uso:
    python3 competitor_intel.py --check
    python3 competitor_intel.py --keywords "usinagem cnc,retifica industrial" --location br
    python3 competitor_intel.py --gap --target competitor.com.br --self mycliente.com.br
"""

import os
import sys
import json
import argparse
import importlib.util
from datetime import datetime, timezone
from pathlib import Path


# ─────────────────────────────────────────────
# Reuso do dataforseo_client.py de mkt-seo-ops
# ─────────────────────────────────────────────

def _candidate_paths():
    """
    Candidatos ao dataforseo_client.py de mkt-seo-ops. Resolve o diretório real
    do script (segue symlink) antes de subir a árvore procurando por
    .claude/skills/mkt-seo-ops/dataforseo_client.py.
    """
    here = Path(__file__).resolve().parent   # .../mkt-competitive-brief/scripts
    return [
        base / ".claude" / "skills" / "mkt-seo-ops" / "dataforseo_client.py"
        for base in [here, *here.parents]
    ]


def _load_client():
    """
    Importa DataForSEOClient do mkt-seo-ops via importlib (padrão seo_ops.py).
    Retorna (client, None) ou (None, motivo_str).
    """
    for path in _candidate_paths():
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("dataforseo_client", str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.DataForSEOClient(), None
        except Exception as e:  # noqa: BLE001
            return None, f"falha ao carregar dataforseo_client.py em {path}: {e}"
    return None, (
        "dataforseo_client.py não encontrado — verifique se mkt-seo-ops está instalado "
        "em .claude/skills/mkt-seo-ops/."
    )


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────
# Eixo 1 — SERP features + AI Overview + intent
# ─────────────────────────────────────────────

def serp_intel(keywords: list, location="br", language="pt-BR") -> dict:
    """
    SERP features / AI Overview presence / intent por keyword via DataForSEO SERP API.
    Cada resultado carrega source + collected_at (proveniência obrigatória).
    N/A graceful sem fabricar dado.
    """
    client, reason = _load_client()
    if client is None:
        return {"status": "N/A", "reason": reason, "collected_at": _today()}
    if not client.available():
        return {
            "status": "N/A",
            "reason": (
                "DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD ausentes no .env — "
                "SERP features, AI Overview presence e intent indisponíveis. "
                "Configure para habilitar (Labs ~$0.025/1k kw)."
            ),
            "collected_at": _today(),
        }
    # location não-mapeada → N/A explícito, nunca default BR silencioso (anti-fabricação).
    if location.lower() not in _LOC:
        return {
            "status": "N/A",
            "reason": (
                f"location '{location}' não mapeada (suportadas: {', '.join(sorted(_LOC))}). "
                "Default silencioso para BR fabricaria SERP de outro país."
            ),
            "collected_at": _today(),
        }
    truncated = len(keywords) > 50
    kw_slice = keywords[:50]
    try:
        raw = client.keyword_intel(kw_slice, location=location, language=language)
    except Exception as e:  # noqa: BLE001
        return {
            "status": "N/A",
            "reason": f"erro DataForSEO (keyword_intel): {e}",
            "collected_at": _today(),
        }
    if raw.get("status") != "OK":
        raw.setdefault("collected_at", _today())
        return raw
    out = {
        "status": "OK",
        "source": "DataForSEO SERP API (serp/google/organic/live/advanced + Labs search_intent/live)",
        "source_url": "https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/",
        "collected_at": _today(),
        "location": location,
        "language": language,
        "keywords": raw.get("keywords", []),
    }
    if truncated:
        out["truncated"] = True
        out["truncated_note"] = (
            f"{len(keywords)} keywords recebidas; processadas as primeiras 50. "
            "As demais NÃO foram consultadas."
        )
    return out


# ─────────────────────────────────────────────
# Eixo 2 — keyword gap domínio-vs-domínio
# ─────────────────────────────────────────────

# Tabelas de location/language (replicadas aqui para não depender de acesso a
# atributos privados do módulo carregado via importlib).
_LOC = {"br": 2076, "us": 2840, "pt": 2620}
_LANG = {"pt-br": "pt", "pt": "pt", "en": "en", "en-us": "en"}


def keyword_gap(target_domain: str, self_domain: str,
                location="br", language="pt-BR", limit=50) -> dict:
    """
    Keywords que target_domain (concorrente) rankeia e self_domain (cliente) não.
    Usa DataForSEO Labs domain_intersection com intersections=False.
    Reusa _post/_auth_header do client existente — sem duplicar credencial.
    Faixa de erro típica 15-30% (estimativa DataForSEO, não dado exato).
    """
    client, reason = _load_client()
    if client is None:
        return {"status": "N/A", "reason": reason, "collected_at": _today()}
    if not client.available():
        return {
            "status": "N/A",
            "reason": "DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD ausentes no .env.",
            "collected_at": _today(),
        }

    # location não-mapeada → N/A explícito, nunca default BR silencioso (anti-fabricação).
    if location.lower() not in _LOC:
        return {
            "status": "N/A",
            "reason": (
                f"location '{location}' não mapeada (suportadas: {', '.join(sorted(_LOC))}). "
                "Default silencioso para BR fabricaria gap de outro país."
            ),
            "collected_at": _today(),
        }
    loc_code = _LOC[location.lower()]
    lang_code = _LANG.get(language.lower(), "pt")

    payload = [{
        "target1": target_domain,
        "target2": self_domain,
        "location_code": loc_code,
        "language_code": lang_code,
        "intersections": False,   # False = target1 tem, target2 não tem
        "limit": min(limit, 100),
        "order_by": ["keyword_data.keyword_info.search_volume,desc"],
    }]
    try:
        resp = client._post("dataforseo_labs/google/domain_intersection/live", payload)
    except Exception as e:  # noqa: BLE001
        return {
            "status": "N/A",
            "reason": f"erro DataForSEO Labs (domain_intersection): {e}",
            "collected_at": _today(),
        }

    gaps = []
    for t in (resp.get("tasks") or []):
        for res in (t.get("result") or []):
            for item in (res.get("items") or []):
                kd = item.get("keyword_data") or {}
                ki = kd.get("keyword_info") or {}
                first = item.get("first_domain_serp_element") or {}
                gaps.append({
                    "keyword": kd.get("keyword", ""),
                    "search_volume": ki.get("search_volume"),
                    "competitor_rank": first.get("rank_absolute"),
                    "competitor_url": first.get("url"),
                })

    return {
        "status": "OK",
        "source": "DataForSEO Labs domain_intersection/live",
        "source_url": "https://docs.dataforseo.com/v3/dataforseo_labs/google/domain_intersection/live/",
        "collected_at": _today(),
        "target_competitor": target_domain,
        "self_domain": self_domain,
        "location": location,
        "note": (
            "Estimativa 3rd-party (DataForSEO) — faixa de erro típica 15-30% em volume; "
            "rank é snapshot da SERP na data de coleta, não histórico."
        ),
        "gap_keywords": gaps,
    }


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Coletor de inteligência competitiva REAL via DataForSEO (reuso mkt-seo-ops)"
    )
    p.add_argument("--check", action="store_true",
                   help="Verifica disponibilidade do DataForSEO client")
    p.add_argument("--keywords",
                   help="CSV de keywords para SERP features/AI-Overview/intent")
    p.add_argument("--gap", action="store_true",
                   help="Keyword gap domínio-vs-domínio (exige --target e --self)")
    p.add_argument("--target", help="Domínio do concorrente")
    p.add_argument("--self", dest="self_domain", help="Domínio do cliente")
    p.add_argument("--location", default="br")
    p.add_argument("--language", default="pt-BR")
    args = p.parse_args()

    if args.check:
        client, reason = _load_client()
        print(json.dumps({
            "client_loaded": client is not None,
            "available": bool(client and client.available()),
            "reason": reason,
        }, indent=2, ensure_ascii=False))
        return

    if args.gap:
        if not (args.target and args.self_domain):
            print(json.dumps({"status": "ERROR",
                               "reason": "--gap exige --target e --self"}, ensure_ascii=False))
            sys.exit(2)
        print(json.dumps(
            keyword_gap(args.target, args.self_domain, args.location, args.language),
            indent=2, ensure_ascii=False
        ))
        return

    if args.keywords:
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        print(json.dumps(
            serp_intel(kws, args.location, args.language),
            indent=2, ensure_ascii=False
        ))
        return

    p.print_help()


if __name__ == "__main__":
    main()
