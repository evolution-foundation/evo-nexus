"""Testes para competitor_intel.py — mkt-competitive-brief.

Cobertura:
  - N/A graceful: sem credencial DataForSEO (client carregado mas indisponível)
  - N/A graceful: dataforseo_client.py não encontrado (load_error)
  - N/A graceful: location não mapeada → nunca default BR silencioso (anti-fabricação)
  - Truncation >50 keywords: processa só as 50 primeiras, sinaliza truncated=True
  - Erro de rede (URLError) → N/A com razão, nunca exception não tratada
  - Parsing de keyword-gap: campos extraídos corretamente de payload domain_intersection
  - Anti-fabricação: N/A nunca contém dado inventado
  - Smoke CLI: --check sem credencial retorna JSON válido
  - serp_intel OK path: status OK, source e collected_at presentes
  - keyword_gap OK path: gap_keywords com estrutura correta
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gap_payload(items: list) -> dict:
    """Monta resposta DataForSEO Labs domain_intersection."""
    return {
        "tasks": [{
            "result": [{
                "items": [
                    {
                        "keyword_data": {
                            "keyword": item["keyword"],
                            "keyword_info": {"search_volume": item.get("sv", 1000)},
                        },
                        "first_domain_serp_element": {
                            "rank_absolute": item.get("rank", 3),
                            "url": item.get("url", f"https://competitor.com.br/{item['keyword']}"),
                        },
                    }
                    for item in items
                ]
            }]
        }]
    }


def _serp_payload(kw: str) -> dict:
    """Resposta mínima de serp/google/organic/live/advanced."""
    return {
        "tasks": [{
            "result": [{
                "items": [
                    {"type": "organic"},
                    {"type": "featured_snippet"},
                ]
            }]
        }]
    }


# ─────────────────────────────────────────────────────────────────────────────
# serp_intel — N/A paths
# ─────────────────────────────────────────────────────────────────────────────

class TestSerpIntelNAGraceful:
    """serp_intel() deve retornar N/A sem fabricar dado."""

    def test_na_when_client_not_loaded(self, mock_client, ci_mod):
        """dataforseo_client.py não encontrado → N/A com reason."""
        mock_client(load_error="dataforseo_client.py não encontrado")
        result = ci_mod.serp_intel(["usinagem cnc"])
        assert result["status"] == "N/A"
        assert "reason" in result
        assert "collected_at" in result
        # Anti-fabricação: não deve ter keywords inventadas
        assert "keywords" not in result or result.get("keywords") == []

    def test_na_when_credentials_absent(self, mock_client, ci_mod):
        """Client carregado mas sem credenciais → N/A."""
        mock_client(available=False)
        result = ci_mod.serp_intel(["usinagem cnc"])
        assert result["status"] == "N/A"
        assert "reason" in result
        assert "collected_at" in result

    def test_na_reason_mentions_dataforseo(self, mock_client, ci_mod):
        """Mensagem de N/A deve mencionar DataForSEO (instrução para usuário)."""
        mock_client(available=False)
        result = ci_mod.serp_intel(["usinagem cnc"])
        assert "dataforseo" in result["reason"].lower() or "DATAFORSEO" in result["reason"]

    def test_na_unmapped_location(self, mock_client, ci_mod):
        """Location não mapeada (ex: 'jp') → N/A explícito, nunca default BR."""
        mock_client(available=True)
        result = ci_mod.serp_intel(["usinagem cnc"], location="jp")
        assert result["status"] == "N/A"
        assert "jp" in result["reason"]
        assert "collected_at" in result

    def test_na_unmapped_location_does_not_fallback(self, mock_client, ci_mod):
        """Location inválida NÃO deve silenciosamente usar BR (anti-fabricação)."""
        # O mock não deve ter sido chamado com keyword_intel se location é inválida
        client = mock_client(available=True)
        ci_mod.serp_intel(["usinagem"], location="xx")
        # keyword_intel não deve ter sido chamado (verificado via mock)
        if client is not None:
            client.keyword_intel.assert_not_called()

    def test_na_on_network_error(self, mock_client, ci_mod):
        """Erro de rede (URLError via keyword_intel) → N/A, não exception."""
        import urllib.error
        client = mock_client(available=True)
        client.keyword_intel.side_effect = urllib.error.URLError("timeout")
        result = ci_mod.serp_intel(["usinagem cnc"])
        assert result["status"] == "N/A"
        assert "reason" in result

    def test_na_on_api_error(self, mock_client, ci_mod):
        """Exceção genérica em keyword_intel → N/A, não propaga."""
        client = mock_client(available=True)
        client.keyword_intel.side_effect = ValueError("unexpected API response")
        result = ci_mod.serp_intel(["usinagem cnc"])
        assert result["status"] == "N/A"
        assert "reason" in result


# ─────────────────────────────────────────────────────────────────────────────
# serp_intel — OK path
# ─────────────────────────────────────────────────────────────────────────────

class TestSerpIntelOKPath:
    """serp_intel() com client disponível e dados reais."""

    def _ok_resp(self, kws):
        return {
            "status": "OK",
            "keywords": [
                {"keyword": kw, "intent": "informational",
                 "serp_features": ["featured_snippet"], "ai_overview": False}
                for kw in kws
            ]
        }

    def test_ok_status_and_provenance_fields(self, mock_client, ci_mod):
        """status OK deve carregar source, source_url e collected_at."""
        kws = ["usinagem cnc", "retifica industrial"]
        mock_client(available=True, keyword_intel_resp=self._ok_resp(kws))
        result = ci_mod.serp_intel(kws)
        assert result["status"] == "OK"
        assert "source" in result
        assert "source_url" in result
        assert "collected_at" in result

    def test_ok_keywords_passthrough(self, mock_client, ci_mod):
        """keywords retornadas devem estar no output."""
        kws = ["torno cnc", "fresadora"]
        mock_client(available=True, keyword_intel_resp=self._ok_resp(kws))
        result = ci_mod.serp_intel(kws)
        assert result["status"] == "OK"
        returned_kws = [k["keyword"] for k in result.get("keywords", [])]
        for kw in kws:
            assert kw in returned_kws

    def test_location_passed_to_client(self, mock_client, ci_mod):
        """location deve ser repassada ao keyword_intel."""
        kws = ["usinagem"]
        client = mock_client(available=True, keyword_intel_resp=self._ok_resp(kws))
        ci_mod.serp_intel(kws, location="br")
        client.keyword_intel.assert_called_once()
        call_kwargs = client.keyword_intel.call_args
        assert call_kwargs[1].get("location", None) == "br" or \
               (call_kwargs[0] and call_kwargs[0][1] == "br")


# ─────────────────────────────────────────────────────────────────────────────
# serp_intel — Truncation >50 keywords
# ─────────────────────────────────────────────────────────────────────────────

class TestSerpIntelTruncation:
    """Mais de 50 keywords → só 50 processadas, flag truncated=True."""

    def _ok_resp_n(self, n):
        return {
            "status": "OK",
            "keywords": [
                {"keyword": f"kw{i}", "intent": "-",
                 "serp_features": [], "ai_overview": False}
                for i in range(min(n, 50))
            ]
        }

    def test_51_keywords_sets_truncated(self, mock_client, ci_mod):
        """51 keywords → truncated=True no output."""
        kws = [f"kw{i}" for i in range(51)]
        client = mock_client(available=True, keyword_intel_resp=self._ok_resp_n(50))
        result = ci_mod.serp_intel(kws)
        assert result.get("truncated") is True

    def test_truncated_note_present(self, mock_client, ci_mod):
        """truncated=True deve vir com truncated_note explicativo."""
        kws = [f"kw{i}" for i in range(60)]
        mock_client(available=True, keyword_intel_resp=self._ok_resp_n(50))
        result = ci_mod.serp_intel(kws)
        assert "truncated_note" in result
        assert "50" in result["truncated_note"]

    def test_51_keywords_calls_with_50(self, mock_client, ci_mod):
        """keyword_intel deve ser chamado com máximo 50 keywords."""
        kws = [f"kw{i}" for i in range(51)]
        client = mock_client(available=True, keyword_intel_resp=self._ok_resp_n(50))
        ci_mod.serp_intel(kws)
        called_kws = client.keyword_intel.call_args[0][0]
        assert len(called_kws) == 50

    def test_50_keywords_no_truncated(self, mock_client, ci_mod):
        """Exatamente 50 keywords → truncated NÃO deve estar presente."""
        kws = [f"kw{i}" for i in range(50)]
        mock_client(available=True, keyword_intel_resp=self._ok_resp_n(50))
        result = ci_mod.serp_intel(kws)
        assert result.get("truncated") is not True

    def test_49_keywords_no_truncated(self, mock_client, ci_mod):
        """49 keywords → sem truncação."""
        kws = [f"kw{i}" for i in range(49)]
        mock_client(available=True, keyword_intel_resp=self._ok_resp_n(49))
        result = ci_mod.serp_intel(kws)
        assert result.get("truncated") is not True


# ─────────────────────────────────────────────────────────────────────────────
# keyword_gap — N/A paths
# ─────────────────────────────────────────────────────────────────────────────

class TestKeywordGapNAGraceful:
    """keyword_gap() deve retornar N/A sem fabricar dado."""

    def test_na_when_client_not_loaded(self, mock_client, ci_mod):
        mock_client(load_error="mkt-seo-ops não instalado")
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert result["status"] == "N/A"
        assert "reason" in result
        assert "collected_at" in result

    def test_na_when_credentials_absent(self, mock_client, ci_mod):
        mock_client(available=False)
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert result["status"] == "N/A"
        assert "collected_at" in result

    def test_na_unmapped_location(self, mock_client, ci_mod):
        """location não mapeada → N/A com o nome da location na mensagem."""
        mock_client(available=True)
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br", location="jp")
        assert result["status"] == "N/A"
        assert "jp" in result["reason"]

    def test_na_unmapped_location_no_post_call(self, mock_client, ci_mod):
        """_post NÃO deve ser chamado para location inválida."""
        client = mock_client(available=True)
        ci_mod.keyword_gap("competitor.com.br", "cliente.com.br", location="invalid")
        client._post.assert_not_called()

    def test_na_on_post_exception(self, mock_client, ci_mod):
        """Exceção em _post → N/A, não propaga."""
        client = mock_client(available=True)
        client._post.side_effect = RuntimeError("API offline")
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert result["status"] == "N/A"
        assert "reason" in result


# ─────────────────────────────────────────────────────────────────────────────
# keyword_gap — OK path e parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestKeywordGapParsing:
    """Parsing correto do payload domain_intersection."""

    def _setup_gap(self, mock_client, ci_mod, items):
        client = mock_client(available=True)
        client._post.return_value = _gap_payload(items)
        return client

    def test_ok_status_and_provenance_fields(self, mock_client, ci_mod):
        """status OK deve ter source, source_url, collected_at."""
        self._setup_gap(mock_client, ci_mod, [
            {"keyword": "usinagem cnc", "sv": 2400, "rank": 1}
        ])
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert result["status"] == "OK"
        assert "source" in result
        assert "source_url" in result
        assert "collected_at" in result

    def test_gap_keywords_list_present(self, mock_client, ci_mod):
        """gap_keywords deve ser uma lista."""
        self._setup_gap(mock_client, ci_mod, [
            {"keyword": "retifica industrial", "sv": 1100, "rank": 2}
        ])
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert "gap_keywords" in result
        assert isinstance(result["gap_keywords"], list)

    def test_gap_keywords_fields(self, mock_client, ci_mod):
        """Cada gap_keyword deve ter keyword, search_volume, competitor_rank, competitor_url."""
        items = [{"keyword": "torno cnc", "sv": 3000, "rank": 1,
                  "url": "https://competitor.com.br/torno-cnc"}]
        self._setup_gap(mock_client, ci_mod, items)
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert result["status"] == "OK"
        gaps = result["gap_keywords"]
        assert len(gaps) == 1
        g = gaps[0]
        assert g["keyword"] == "torno cnc"
        assert g["search_volume"] == 3000
        assert g["competitor_rank"] == 1
        assert "competitor.com.br" in g["competitor_url"]

    def test_gap_keywords_multiple_items(self, mock_client, ci_mod):
        """Múltiplos items devem ser todos extraídos."""
        items = [
            {"keyword": f"kw{i}", "sv": 1000 - i * 10, "rank": i + 1}
            for i in range(5)
        ]
        self._setup_gap(mock_client, ci_mod, items)
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert len(result["gap_keywords"]) == 5

    def test_gap_target_and_self_in_result(self, mock_client, ci_mod):
        """target_competitor e self_domain devem estar no resultado."""
        self._setup_gap(mock_client, ci_mod, [{"keyword": "kw1"}])
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert result["target_competitor"] == "competitor.com.br"
        assert result["self_domain"] == "cliente.com.br"

    def test_gap_note_mentions_error_range(self, mock_client, ci_mod):
        """Note deve mencionar faixa de erro (anti-fabricação — rotulagem obrigatória)."""
        self._setup_gap(mock_client, ci_mod, [{"keyword": "kw1"}])
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        note = result.get("note", "")
        # 15% ou 30% ou "15-30" deve aparecer
        assert "15" in note or "30" in note

    def test_post_called_with_correct_structure(self, mock_client, ci_mod):
        """_post deve ser chamado com domain_intersection endpoint."""
        client = self._setup_gap(mock_client, ci_mod, [])
        ci_mod.keyword_gap("competitor.com.br", "cliente.com.br", location="br")
        assert client._post.called
        call_args = client._post.call_args[0]
        assert "domain_intersection" in call_args[0]

    def test_empty_result_from_api(self, mock_client, ci_mod):
        """API retorna tasks vazias → gap_keywords = []."""
        client = mock_client(available=True)
        client._post.return_value = {"tasks": []}
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert result["status"] == "OK"
        assert result["gap_keywords"] == []

    def test_limit_passed_to_payload(self, mock_client, ci_mod):
        """limit deve ser repassado ao payload (limitado a 100)."""
        client = mock_client(available=True)
        client._post.return_value = {"tasks": []}
        ci_mod.keyword_gap("competitor.com.br", "cliente.com.br", limit=30)
        payload = client._post.call_args[0][1]
        assert payload[0]["limit"] == 30

    def test_limit_capped_at_100(self, mock_client, ci_mod):
        """limit > 100 deve ser capeado em 100 antes de enviar."""
        client = mock_client(available=True)
        client._post.return_value = {"tasks": []}
        ci_mod.keyword_gap("competitor.com.br", "cliente.com.br", limit=200)
        payload = client._post.call_args[0][1]
        assert payload[0]["limit"] <= 100


# ─────────────────────────────────────────────────────────────────────────────
# Anti-fabricação — invariantes gerais
# ─────────────────────────────────────────────────────────────────────────────

class TestAntiFabricacao:
    """Invariantes anti-fabricação: N/A nunca contém dado inventado."""

    def test_serp_na_no_keywords_in_output(self, mock_client, ci_mod):
        """serp_intel N/A (sem credencial) não deve ter lista keywords populada."""
        mock_client(available=False)
        result = ci_mod.serp_intel(["usinagem cnc"])
        assert result["status"] == "N/A"
        # keywords não deve existir ou deve ser vazia
        kws = result.get("keywords", [])
        assert kws == [] or kws is None

    def test_gap_na_no_gap_keywords_in_output(self, mock_client, ci_mod):
        """keyword_gap N/A (sem credencial) não deve ter gap_keywords."""
        mock_client(available=False)
        result = ci_mod.keyword_gap("competitor.com.br", "cliente.com.br")
        assert result["status"] == "N/A"
        assert "gap_keywords" not in result

    def test_serp_na_always_has_collected_at(self, mock_client, ci_mod):
        """Todo N/A deve ter collected_at (proveniência mínima)."""
        mock_client(load_error="not found")
        result = ci_mod.serp_intel(["kw1"])
        assert "collected_at" in result

    def test_gap_na_always_has_collected_at(self, mock_client, ci_mod):
        mock_client(load_error="not found")
        result = ci_mod.keyword_gap("a.com", "b.com")
        assert "collected_at" in result


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke — --check
# ─────────────────────────────────────────────────────────────────────────────

class TestCLISmoke:
    """CLI --check deve retornar JSON válido sem rede."""

    def test_check_without_credentials(self, mock_client, ci_mod, capsys):
        """--check sem credenciais: retorna JSON com client_loaded e available."""
        import os

        # Garante que as variáveis de env não estão presentes
        orig_login = os.environ.pop("DATAFORSEO_LOGIN", None)
        orig_pass = os.environ.pop("DATAFORSEO_PASSWORD", None)
        try:
            # Não usar mock_client: testa o _load_client real (que vai buscar o arquivo)
            # mas como dataforseo_client.py pode ou não existir no caminho,
            # verificamos só que o output é JSON válido
            sys.argv = ["competitor_intel.py", "--check"]
            try:
                ci_mod.main()
            except SystemExit:
                pass
            out = capsys.readouterr().out.strip()
            data = json.loads(out)
            assert "client_loaded" in data
            assert "available" in data
        finally:
            if orig_login is not None:
                os.environ["DATAFORSEO_LOGIN"] = orig_login
            if orig_pass is not None:
                os.environ["DATAFORSEO_PASSWORD"] = orig_pass

    def test_check_json_schema(self, mock_client, ci_mod, capsys):
        """--check sempre retorna client_loaded (bool) e available (bool)."""
        import os
        os.environ.pop("DATAFORSEO_LOGIN", None)
        os.environ.pop("DATAFORSEO_PASSWORD", None)
        sys.argv = ["competitor_intel.py", "--check"]
        try:
            ci_mod.main()
        except SystemExit:
            pass
        out = capsys.readouterr().out.strip()
        data = json.loads(out)
        assert isinstance(data["client_loaded"], bool)
        assert isinstance(data["available"], bool)

    def test_gap_missing_target_exits_nonzero(self, mock_client, ci_mod):
        """--gap sem --target ou --self deve sair com erro (sem travar)."""
        sys.argv = ["competitor_intel.py", "--gap"]
        with pytest.raises(SystemExit) as exc_info:
            ci_mod.main()
        assert exc_info.value.code != 0

    def test_no_args_prints_help(self, mock_client, ci_mod, capsys):
        """Sem argumentos deve imprimir help (sem exception não tratada)."""
        sys.argv = ["competitor_intel.py"]
        try:
            ci_mod.main()
        except SystemExit:
            pass
        # Não deve levantar exception não tratada — sucesso se chegou aqui
