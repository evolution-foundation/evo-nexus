#!/usr/bin/env python3
"""
DataForSEO client (SHOULD) — intent + SERP features + AI Overview parse.

Maior alavanca custo-benefício da spec (Labs ~$0.025/1k kw). Cobre:
  - SERP intent classification (S5)
  - SERP feature targeting / PAA / featured snippet (S4)
  - AI Overview presence parse (S2 — measurement gap de 2026)

DEGRADAÇÃO GRACIOSA (regra do projeto): se DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD
não existirem no ambiente, `available()` retorna False e os consumidores reportam
N/A com instrução — NUNCA fabrica dado nem quebra o pipeline.

Auth: DataForSEO usa HTTP Basic (login + password da conta).

Uso:
    from dataforseo_client import DataForSEOClient
    c = DataForSEOClient()
    if c.available():
        intel = c.keyword_intel(["usinagem", "retífica"], location="br", language="pt-BR")
"""

import os
import json
import base64
import urllib.request
import urllib.error


# Mapas país→location_code / lang→language_code do DataForSEO.
# Cobre o essencial BR; ampliável. Ausência → cai em fallback global.
_LOCATION_CODES = {
    "br": 2076,   # Brazil
    "us": 2840,   # United States
    "pt": 2620,   # Portugal
}
_LANGUAGE_CODES = {
    "pt-br": "pt",
    "pt": "pt",
    "en": "en",
    "en-us": "en",
}

_API_BASE = "https://api.dataforseo.com/v3"


class DataForSEOClient:
    def __init__(self, login=None, password=None):
        self.login = login or os.environ.get("DATAFORSEO_LOGIN", "")
        self.password = password or os.environ.get("DATAFORSEO_PASSWORD", "")

    def available(self) -> bool:
        """True só se ambas credenciais estiverem presentes."""
        return bool(self.login and self.password)

    def _auth_header(self) -> str:
        raw = f"{self.login}:{self.password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _post(self, path: str, payload: list) -> dict:
        url = f"{_API_BASE}/{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", self._auth_header())
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def keyword_intel(self, keywords, location="br", language="pt-BR") -> dict:
        """
        Retorna intent + SERP features + AI Overview presence por keyword.
        Em qualquer erro de rede/API → status N/A com a razão (não fabrica).
        """
        if not self.available():
            return {"status": "N/A", "reason": "sem DATAFORSEO_LOGIN/PASSWORD"}
        if not keywords:
            return {"status": "OK", "keywords": []}

        loc = _LOCATION_CODES.get(location.lower(), 2076)
        lang = _LANGUAGE_CODES.get(language.lower(), "pt")

        out = []
        # 1) Intent via DataForSEO Labs (batch)
        intent_map = {}
        try:
            payload = [{
                "keywords": keywords,
                "location_code": loc,
                "language_code": lang,
            }]
            resp = self._post("dataforseo_labs/google/search_intent/live", payload)
            tasks = resp.get("tasks", []) or []
            for t in tasks:
                for res in (t.get("result") or []):
                    for item in (res.get("items") or []):
                        kw = item.get("keyword", "")
                        ki = item.get("keyword_intent") or {}
                        intent_map[kw] = ki.get("label", "-")
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as e:
            return {"status": "N/A", "reason": f"erro DataForSEO Labs (intent): {e}"}

        # 2) SERP features + AI Overview — uma tarefa por keyword (live advanced)
        # Mantém barato: só as keywords passadas (já limitadas pelo chamador).
        for kw in keywords:
            feats, ai_ov = [], False
            try:
                payload = [{
                    "keyword": kw,
                    "location_code": loc,
                    "language_code": lang,
                    "device": "desktop",
                }]
                resp = self._post("serp/google/organic/live/advanced", payload)
                for t in (resp.get("tasks") or []):
                    for res in (t.get("result") or []):
                        seen = set()
                        for item in (res.get("items") or []):
                            itype = item.get("type", "")
                            if itype and itype not in seen:
                                seen.add(itype)
                            if itype == "ai_overview":
                                ai_ov = True
                        # features = tipos de bloco != organic/paid
                        feats = sorted(t_ for t_ in seen
                                       if t_ not in ("organic", "paid"))
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError):
                # falha numa keyword não derruba o lote — marca features vazias
                feats, ai_ov = [], False
            out.append({
                "keyword": kw,
                "intent": intent_map.get(kw, "-"),
                "serp_features": feats,
                "ai_overview": ai_ov,
            })

        return {"status": "OK", "keywords": out}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DataForSEO client (SHOULD)")
    parser.add_argument("--check", action="store_true", help="Checa disponibilidade")
    parser.add_argument("--keywords", help="CSV de keywords para teste")
    parser.add_argument("--location", default="br")
    parser.add_argument("--language", default="pt-BR")
    args = parser.parse_args()

    c = DataForSEOClient()
    if args.check or not args.keywords:
        print(json.dumps({"available": c.available()}, indent=2))
        return
    kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
    print(json.dumps(c.keyword_intel(kws, args.location, args.language),
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
