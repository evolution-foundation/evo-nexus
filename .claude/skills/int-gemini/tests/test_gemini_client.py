"""Suíte de testes do int-gemini — 100% mockada (sem rede real).

Cobre: HTTP/_request, parsing, guard de confidencialidade, roteamento e os
comandos da CLI (ask/models/route/scan/smoke). conftest bloqueia rede em autouse.
"""
from __future__ import annotations

import argparse
import json
import urllib.error

import pytest

import gemini_client as gc
from helpers import (
    make_generate_response,
    make_http_error,
    make_models_response,
    make_response,
)


# ── _request: HTTP layer ───────────────────────────────────────────────────────

def test_request_success(mock_urlopen):
    mock_urlopen([make_response({"hello": "world"})])
    out = gc._request("GET", "/v1beta/models")
    assert out == {"hello": "world"}


def test_request_missing_key_raises(monkeypatch):
    monkeypatch.setattr(gc, "API_KEY", "")
    with pytest.raises(gc.GeminiError, match="GEMINI_API_KEY ausente"):
        gc._request("GET", "/v1beta/models")


def test_request_retries_on_429_then_succeeds(mock_urlopen):
    mock_urlopen([make_http_error(429), make_response({"ok": 1})])
    out = gc._request("GET", "/x")
    assert out == {"ok": 1}


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_request_retries_on_5xx(mock_urlopen, code):
    mock_urlopen([make_http_error(code), make_response({"ok": 1})])
    assert gc._request("GET", "/x") == {"ok": 1}


def test_request_gives_up_after_one_retry(mock_urlopen):
    mock_urlopen([make_http_error(503), make_http_error(503)])
    with pytest.raises(gc.GeminiError, match="HTTP 503"):
        gc._request("GET", "/x")


def test_request_non_retryable_4xx_raises_immediately(mock_urlopen):
    mock_urlopen([make_http_error(400, "bad request")])
    with pytest.raises(gc.GeminiError, match="bad request"):
        gc._request("GET", "/x")


def test_request_extracts_api_error_message(mock_urlopen):
    mock_urlopen([make_http_error(403, "permission denied")])
    with pytest.raises(gc.GeminiError, match="permission denied"):
        gc._request("GET", "/x")


def test_request_url_error_retries_then_raises(mock_urlopen):
    err = urllib.error.URLError("conn refused")
    mock_urlopen([err, err])
    with pytest.raises(gc.GeminiError, match="conexão falhou"):
        gc._request("GET", "/x")


def test_request_non_json_raises(mock_urlopen):
    mock_urlopen([make_response("not json at all")])
    with pytest.raises(gc.GeminiError, match="não-JSON"):
        gc._request("GET", "/x")


def test_request_non_dict_json_wrapped(mock_urlopen):
    mock_urlopen([make_response([1, 2, 3])])
    out = gc._request("GET", "/x")
    assert out == {"raw": [1, 2, 3]}


def test_request_always_returns_dict(mock_urlopen):
    """Type-safety: _request nunca devolve bytes/str/list crua."""
    mock_urlopen([make_response("42")])  # JSON válido, mas é int
    out = gc._request("GET", "/x")
    assert isinstance(out, dict)


# ── parsing ─────────────────────────────────────────────────────────────────────

def test_extract_text_concatenates_parts():
    resp = {"candidates": [{"content": {"parts": [{"text": "ab"}, {"text": "cd"}]}}]}
    assert gc._extract_text(resp) == "abcd"


def test_extract_text_empty_when_no_candidates():
    assert gc._extract_text({}) == ""
    assert gc._extract_text({"candidates": []}) == ""


def test_extract_text_skips_non_dict_parts():
    resp = {"candidates": [{"content": {"parts": [{"text": "x"}, "junk"]}}]}
    assert gc._extract_text(resp) == "x"


def test_extract_grounding_returns_sources():
    resp = {"candidates": [{"groundingMetadata": {"groundingChunks": [
        {"web": {"title": "T1", "uri": "http://a"}},
        {"web": {"title": "T2", "uri": "http://b"}},
    ]}}]}
    out = gc._extract_grounding(resp)
    assert out == [{"title": "T1", "uri": "http://a"}, {"title": "T2", "uri": "http://b"}]


def test_extract_grounding_skips_chunks_without_uri():
    resp = {"candidates": [{"groundingMetadata": {"groundingChunks": [
        {"web": {"title": "no uri"}},
        {"retrievedContext": {}},
    ]}}]}
    assert gc._extract_grounding(resp) == []


def test_extract_grounding_empty_when_no_metadata():
    assert gc._extract_grounding({"candidates": [{}]}) == []


# ── guard de confidencialidade ──────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_type", [
    ("meu CPF é 123.456.789-00", "cpf"),
    ("CPF 12345678900", "cpf"),
    ("CNPJ 12.345.678/0001-90", "cnpj"),
    ("ligue (11) 99999-8888", "telefone"),
    ("contato +55 11 98888-7777", "telefone"),
    ("email joao@cliente.com.br", "email"),
    ("valor R$ 1.097,00", "valor_financeiro"),
    ("paga via PIX", "pix"),
    ("a cláusula 4 do contrato", "contrato"),
    ("os honorários acordados", "contrato"),
    ("isto é confidencial", "contrato"),
    ("a api_key vazou", "segredo"),
    ("o token bearer", "segredo"),
    ("minha senha é", "segredo"),
])
def test_scan_detects_sensitive(text, expected_type):
    findings = gc.scan_confidential(text)
    assert expected_type in {f["type"] for f in findings}


@pytest.mark.parametrize("text", [
    "resumir transcript de reunião interna",
    "gerar calendário editorial do mês seguinte",
    "classificar sentimento de feedback público",
    "rascunho de post sobre marketing digital",
    "",
])
def test_scan_safe_returns_empty(text):
    assert gc.scan_confidential(text) == []


def test_scan_masks_sample():
    findings = gc.scan_confidential("CPF 123.456.789-00")
    cpf = next(f for f in findings if f["type"] == "cpf")
    assert cpf["sample"].endswith("…")
    assert "456" not in cpf["sample"]  # não ecoa o número inteiro


def test_scan_short_sample_not_masked():
    findings = gc.scan_confidential("PIX agora")
    pix = next(f for f in findings if f["type"] == "pix")
    assert pix["sample"].lower() == "pix"


def test_scan_multiple_findings():
    findings = gc.scan_confidential("R$ 500 via PIX, CPF 111.222.333-44")
    types = {f["type"] for f in findings}
    assert {"valor_financeiro", "pix", "cpf"} <= types


# ── guard: vetores reais reforçados (achados Opus #1, #2) ───────────────────────

@pytest.mark.parametrize("text,expected", [
    # CEP (#1)
    ("CEP 01310-100", "cep"),
    ("cep 01310100", "cep"),
    # endereço (#1)
    ("Rua das Flores, 123", "endereco"),
    ("av. Paulista 1500", "endereco"),
    ("Avenida Brasil 200", "endereco"),
    ("Alameda Santos, 45", "endereco"),
    # nome + contexto de cliente/lead (#1) — a PII mais comum
    ("lead João Silva", "nome_contexto"),
    ("Maria Souza, Diretora Financeira", "nome_contexto"),
    ("sócio João da Silva Santos", "nome_contexto"),
    ("contato Maria Aparecida", "nome_contexto"),
    # valor por extenso (#1)
    ("negócio fechado em 50 mil", "valor_extenso"),
    ("cinquenta mil reais", "valor_extenso"),
    # telefone — nono dígito separado, com 0, fixo 8 dígitos (#2)
    ("(11) 9 9999-9999", "telefone"),
    ("11 9 9999 9999", "telefone"),
    ("011 99999-8888", "telefone"),
    ("ligue 9999-8888", "telefone"),
    ("+55 11 99999-8888", "telefone"),
])
def test_scan_detects_reinforced_vectors(text, expected):
    assert expected in {f["type"] for f in gc.scan_confidential(text)}


def test_scan_card_not_labeled_phone():
    """#4 — sequência longa de dígitos é rotulada cartão, não telefone."""
    types = {f["type"] for f in gc.scan_confidential("cartão 4111 1111 1111 1111")}
    assert "cartao" in types
    assert "telefone" not in types  # de-dup por sobreposição


def test_scan_phone_with_55_not_labeled_card():
    types = {f["type"] for f in gc.scan_confidential("ligar +55 11 99999-8888")}
    assert "telefone" in types
    assert "cartao" not in types


def test_scan_dedup_no_double_label_same_span():
    """Um mesmo trecho (16 dígitos colados) vira exatamente um tipo."""
    findings = gc.scan_confidential("4111111111111111")
    assert len(findings) == 1


# ── guard: corpus de FALSO-NEGATIVO documentado (achado Opus #3) ────────────────
# Estes casos DOCUMENTAM o limite do guard: é guard-rail PARCIAL, não garantia.
# Cada assert registra o que o guard NÃO pega e POR QUÊ — a regressão avisa se o
# comportamento mudar.

@pytest.mark.parametrize("text,why", [
    ("João Silva ligou ontem", "nome solto sem termo de papel próximo"),
    ("entrega no 123 do bloco B", "sem palavra de logradouro (rua/av)"),
    ("meu número é nove nove nove nove", "dígitos por extenso não são capturados"),
    ("falar com a Ana", "primeiro nome isolado é ambíguo demais p/ heurística"),
])
def test_scan_known_false_negatives(text, why):
    """Guard NÃO pega estes vetores — limite conhecido e aceito (ver SKILL.md).

    Se algum passar a ser detectado no futuro, ótimo — mas HOJE não é, então
    tarefas com dado de cliente exigem redação/tier no-train, não confiança cega.
    """
    assert gc.scan_confidential(text) == [], why


def test_scan_does_not_flag_normal_operational_text():
    """Texto operacional típico (sem PII) não dispara o guard — anti-ruído."""
    assert gc.scan_confidential("Migrar runtime EvoNexus para Contabo") == []
    assert gc.scan_confidential("gerar calendário editorial do mês seguinte") == []


# ── roteamento ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("task,model", [
    ("classify", "gemini-2.5-flash-lite"),
    ("triage", "gemini-2.5-flash-lite"),
    ("extract", "gemini-2.5-flash-lite"),
    ("summarize", "gemini-2.5-flash"),
    ("draft", "gemini-2.5-flash"),
    ("translate", "gemini-2.5-flash"),
    ("research", "gemini-2.5-flash"),
    ("reason", "gemini-2.5-pro"),
])
def test_route_model_known(task, model):
    assert gc.route_model(task) == model


def test_route_model_case_insensitive():
    assert gc.route_model("CLASSIFY") == "gemini-2.5-flash-lite"
    assert gc.route_model("  Summarize  ") == "gemini-2.5-flash"


def test_route_model_unknown_falls_back_to_flash():
    assert gc.route_model("nonsense") == gc.DEFAULT_ROUTE_MODEL == "gemini-2.5-flash"


# ── cmd_ask ─────────────────────────────────────────────────────────────────────

def _ask_args(prompt="oi", model=None, system=None, json_=False,
              grounding=False, allow_sensitive=False, system_file=None,
              fallback=False):
    return argparse.Namespace(
        prompt=prompt, model=model, system=system, json=json_,
        grounding=grounding, allow_sensitive=allow_sensitive,
        system_file=system_file, fallback=fallback,
    )


def test_cmd_ask_success(mock_urlopen, capsys):
    mock_urlopen([make_generate_response("resposta!", total_tokens=42)])
    rc = gc.cmd_ask(_ask_args(prompt="resumir isto"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["text"] == "resposta!"
    assert out["usage"]["total_tokens"] == 42
    assert out["finish_reason"] == "STOP"


def test_cmd_ask_uses_default_model(mock_urlopen, capsys):
    mock_urlopen([make_generate_response("x")])
    gc.cmd_ask(_ask_args())
    out = json.loads(capsys.readouterr().out)
    assert out["model"] == gc.DEFAULT_MODEL


def test_cmd_ask_honors_explicit_model(mock_urlopen, capsys):
    mock_urlopen([make_generate_response("x")])
    gc.cmd_ask(_ask_args(model="gemini-2.5-flash-lite"))
    out = json.loads(capsys.readouterr().out)
    assert out["model"] == "gemini-2.5-flash-lite"


def test_cmd_ask_grounding_returns_sources(mock_urlopen, capsys):
    mock_urlopen([make_generate_response(
        "factual", grounding_chunks=[{"web": {"title": "src", "uri": "http://s"}}])])
    gc.cmd_ask(_ask_args(prompt="pesquisar novidades", grounding=True))
    out = json.loads(capsys.readouterr().out)
    assert out["grounded"] is True
    assert out["sources"] == [{"title": "src", "uri": "http://s"}]


def test_cmd_ask_no_grounding_no_sources(mock_urlopen, capsys):
    mock_urlopen([make_generate_response(
        "x", grounding_chunks=[{"web": {"title": "src", "uri": "http://s"}}])])
    gc.cmd_ask(_ask_args(grounding=False))
    out = json.loads(capsys.readouterr().out)
    assert out["sources"] == []


def test_cmd_ask_api_error_returns_1(mock_urlopen, capsys):
    mock_urlopen([make_http_error(400, "invalid")])
    rc = gc.cmd_ask(_ask_args())
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "invalid" in out["error"]


# ── guard integrado ao cmd_ask ──────────────────────────────────────────────────

def test_cmd_ask_blocks_sensitive_prompt(capsys):
    """Guard bloqueia ANTES de qualquer rede (rede está bloqueada no conftest)."""
    rc = gc.cmd_ask(_ask_args(prompt="cliente CPF 123.456.789-00"))
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "cpf" in out["detected_types"]


def test_cmd_ask_blocks_sensitive_system(capsys):
    rc = gc.cmd_ask(_ask_args(prompt="ok", system="honorários R$ 5.000"))
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert {"valor_financeiro", "contrato"} & set(out["detected_types"])


def test_cmd_ask_allow_sensitive_bypasses_guard(mock_urlopen, capsys):
    mock_urlopen([make_generate_response("processado")])
    rc = gc.cmd_ask(_ask_args(prompt="CPF 111.222.333-44", allow_sensitive=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True


def test_cmd_ask_safe_prompt_passes_guard(mock_urlopen, capsys):
    mock_urlopen([make_generate_response("ok")])
    rc = gc.cmd_ask(_ask_args(prompt="resumir reunião interna"))
    assert rc == 0


# ── cmd_ask: auto-melhorias (fallback / system-file) ───────────────────────────

def test_cmd_ask_fallback_on_model_error(mock_urlopen, capsys):
    """flash-lite falha (503), --fallback usa flash e responde."""
    from helpers import make_http_error
    mock_urlopen([
        make_http_error(503), make_http_error(503),  # flash-lite + retry falham
        make_generate_response("via flash"),          # flash responde
    ])
    rc = gc.cmd_ask(_ask_args(model="gemini-2.5-flash-lite", fallback=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["model"] == "gemini-2.5-flash"  # reporta o modelo que de fato respondeu


def test_cmd_ask_no_fallback_propagates_error(mock_urlopen, capsys):
    mock_urlopen([make_http_error(503), make_http_error(503)])
    rc = gc.cmd_ask(_ask_args(model="gemini-2.5-flash-lite", fallback=False))
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["model"] == "gemini-2.5-flash-lite"


def test_cmd_ask_fallback_both_fail(mock_urlopen, capsys):
    from helpers import make_http_error
    mock_urlopen([make_http_error(503), make_http_error(503),  # flash-lite
                  make_http_error(503), make_http_error(503)])  # flash fallback
    rc = gc.cmd_ask(_ask_args(model="gemini-2.5-flash-lite", fallback=True))
    assert rc == 1


def test_cmd_ask_system_file(mock_urlopen, capsys, tmp_path):
    sysf = tmp_path / "sys.txt"
    sysf.write_text("Você é um classificador.", encoding="utf-8")
    mock_urlopen([make_generate_response("ok")])
    rc = gc.cmd_ask(_ask_args(prompt="classificar", system_file=str(sysf)))
    assert rc == 0


def test_cmd_ask_system_file_missing(capsys):
    rc = gc.cmd_ask(_ask_args(system_file="/nao/existe.txt"))
    assert rc == 1
    assert "system-file" in json.loads(capsys.readouterr().out)["error"]


def test_cmd_ask_system_file_scanned_by_guard(capsys, tmp_path):
    """system-file também passa pelo guard de confidencialidade."""
    sysf = tmp_path / "sys.txt"
    sysf.write_text("processar CPF 123.456.789-00", encoding="utf-8")
    rc = gc.cmd_ask(_ask_args(prompt="ok", system_file=str(sysf)))
    assert rc == 2  # bloqueado


# ── cmd_models ──────────────────────────────────────────────────────────────────

def test_cmd_models_filters_generate_content(mock_urlopen, capsys):
    mock_urlopen([make_models_response(["gemini-2.5-flash", "gemini-2.5-pro"])])
    rc = gc.cmd_models(argparse.Namespace())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 2
    assert {m["name"] for m in out["models"]} == {"gemini-2.5-flash", "gemini-2.5-pro"}


def test_cmd_models_error_returns_1(mock_urlopen, capsys):
    mock_urlopen([make_http_error(403, "denied")])
    rc = gc.cmd_models(argparse.Namespace())
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


# ── cmd_route / cmd_scan ────────────────────────────────────────────────────────

def test_cmd_route(capsys):
    rc = gc.cmd_route(argparse.Namespace(task_type="classify"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["model"] == "gemini-2.5-flash-lite"
    assert out["known"] is True


def test_cmd_route_unknown(capsys):
    gc.cmd_route(argparse.Namespace(task_type="zzz"))
    out = json.loads(capsys.readouterr().out)
    assert out["known"] is False
    assert out["model"] == "gemini-2.5-flash"


def test_cmd_scan_sensitive(capsys):
    rc = gc.cmd_scan(argparse.Namespace(text="CPF 123.456.789-00"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["safe"] is False
    assert "cpf" in out["detected_types"]


def test_cmd_scan_safe(capsys):
    gc.cmd_scan(argparse.Namespace(text="texto público inofensivo"))
    out = json.loads(capsys.readouterr().out)
    assert out["safe"] is True


# ── cmd_smoke ───────────────────────────────────────────────────────────────────

def test_smoke_all_pass(mock_urlopen, capsys):
    # generate (1 call) + models (1 call)
    mock_urlopen([
        make_generate_response("pong"),
        make_models_response([gc.DEFAULT_MODEL, "gemini-2.5-flash-lite"]),
    ])
    rc = gc.cmd_smoke(argparse.Namespace())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overall"] == "PASS"
    steps = {s["step"]: s["status"] for s in out["steps"]}
    assert steps == {"env": "PASS", "generate": "PASS", "guard": "PASS", "models": "PASS"}


def test_smoke_exit_zero_even_when_no_key(monkeypatch, capsys):
    monkeypatch.setattr(gc, "API_KEY", "")
    rc = gc.cmd_smoke(argparse.Namespace())
    assert rc == 0  # smoke SEMPRE exit 0
    out = json.loads(capsys.readouterr().out)
    assert out["overall"] == "FAIL"
    steps = {s["step"]: s["status"] for s in out["steps"]}
    assert steps["env"] == "FAIL"
    assert steps["generate"] == "SKIP"
    assert steps["models"] == "SKIP"
    assert steps["guard"] == "PASS"  # guard não depende de rede


def test_smoke_fails_when_default_model_missing(mock_urlopen, capsys):
    mock_urlopen([
        make_generate_response("pong"),
        make_models_response(["algum-outro-modelo"]),
    ])
    rc = gc.cmd_smoke(argparse.Namespace())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overall"] == "FAIL"
    steps = {s["step"]: s["status"] for s in out["steps"]}
    assert steps["models"] == "FAIL"


def test_smoke_fails_on_generate_error(mock_urlopen, capsys):
    mock_urlopen([
        make_http_error(500, "down"),
        make_http_error(500, "down"),  # retry também falha
        make_models_response([gc.DEFAULT_MODEL]),
    ])
    rc = gc.cmd_smoke(argparse.Namespace())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overall"] == "FAIL"
    steps = {s["step"]: s["status"] for s in out["steps"]}
    assert steps["generate"] == "FAIL"


def test_smoke_has_duration(mock_urlopen, capsys):
    mock_urlopen([
        make_generate_response("pong"),
        make_models_response([gc.DEFAULT_MODEL]),
    ])
    gc.cmd_smoke(argparse.Namespace())
    out = json.loads(capsys.readouterr().out)
    assert "duration_ms" in out
    assert all("duration_ms" in s for s in out["steps"])


# ── output contract ─────────────────────────────────────────────────────────────

def test_ask_output_is_valid_json(mock_urlopen, capsys):
    mock_urlopen([make_generate_response("x")])
    gc.cmd_ask(_ask_args())
    json.loads(capsys.readouterr().out)  # não levanta = contrato ok
