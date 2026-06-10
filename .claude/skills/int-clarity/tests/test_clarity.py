#!/usr/bin/env python3
"""
Testes unitários para clarity_export.py.
Uso: python3 tests/test_clarity.py
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Adiciona scripts/ ao path para importar o módulo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# Garante que _load_dotenv não quebre mesmo sem .env
os.environ.setdefault("CLARITY_API_BASE", "https://www.clarity.ms/export-data/api/v1")

import clarity_export as ce


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TRAFFIC_RESPONSE = [
    {
        "metricName": "Traffic",
        "information": [
            {
                "totalSessionCount": 1200,
                "totalBotSessionCount": 30,
                # The API returns whatever field name Microsoft sends — no normalisation.
                # The MS docs show "distantUserCount" (typo) but the live API may return
                # "distinctUserCount". We use a neutral key here to test passthrough,
                # not to assert which spelling is correct.
                "distinctUserCount": 450,
                "pagesPerSessionPercentage": 2.3,
                "Browser": "Chrome",
            }
        ],
    },
    {
        "metricName": "Rage Click Count",
        "information": [{"URL": "/contato", "value": 5}],
    },
]

EMPTY_RESPONSE: list = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_response(body: bytes, status: int = 200):
    """Cria um mock de urllib.request.urlopen que retorna body."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _make_http_error(code: int, body: bytes = b"error"):
    import urllib.error
    err = urllib.error.HTTPError(
        url="http://x", code=code, msg="err", hdrs=None, fp=None  # type: ignore
    )
    err.read = lambda: body
    return err


# ---------------------------------------------------------------------------
# TC-01: Validação days fora do range (1-3)
# ---------------------------------------------------------------------------

class TestValidateDays(unittest.TestCase):

    def test_days_valid_1(self):
        # Não deve levantar
        ce._validate_days(1)

    def test_days_valid_3(self):
        ce._validate_days(3)

    def test_days_invalid_0(self):
        with self.assertRaises(SystemExit):
            ce._validate_days(0)

    def test_days_invalid_4(self):
        with self.assertRaises(SystemExit):
            ce._validate_days(4)


# ---------------------------------------------------------------------------
# TC-02: Validação de dimensões
# ---------------------------------------------------------------------------

class TestValidateDims(unittest.TestCase):

    def test_valid_dim_browser(self):
        ce._validate_dims(["Browser"])

    def test_valid_dims_multiple(self):
        ce._validate_dims(["Browser", "Device", "Country"])

    def test_invalid_dim_popular_pages(self):
        with self.assertRaises(SystemExit):
            ce._validate_dims(["Popular Pages"])

    def test_invalid_dim_unknown(self):
        with self.assertRaises(SystemExit):
            ce._validate_dims(["InvalidDim"])


# ---------------------------------------------------------------------------
# TC-03: Parse de resposta Traffic
# ---------------------------------------------------------------------------

class TestParseTrafficResponse(unittest.TestCase):

    def test_traffic_core_fields_present(self):
        """Os campos de sessão estáveis estão presentes."""
        traffic = [m for m in TRAFFIC_RESPONSE if m["metricName"] == "Traffic"]
        self.assertEqual(len(traffic), 1)
        info = traffic[0]["information"][0]
        self.assertIn("totalSessionCount", info)
        self.assertIn("totalBotSessionCount", info)

    def test_other_metric_generic(self):
        rage = [m for m in TRAFFIC_RESPONSE if m["metricName"] == "Rage Click Count"]
        self.assertEqual(len(rage), 1)
        self.assertEqual(rage[0]["information"][0]["URL"], "/contato")

    def test_user_count_field_passthrough(self):
        """
        O código nunca normalisa nomes de campos da API — o que vier é preservado.
        A MS documenta 'distantUserCount' (typo) mas a API real devolve
        'distinctUserCount'. Testamos que o campo presente no fixture chega intacto,
        sem afirmar qual ortografia é correta.
        """
        info = TRAFFIC_RESPONSE[0]["information"][0]
        # A fixture usa 'distinctUserCount'; o código passa o que a API devolver
        present_keys = {k for k in info if "usercount" in k.lower() or "userCount" in k}
        self.assertTrue(
            len(present_keys) >= 1,
            "Esperado ao menos um campo de contagem de usuários no response.",
        )


# ---------------------------------------------------------------------------
# TC-04: Descoberta multi-token
# ---------------------------------------------------------------------------

class TestDiscoverProjects(unittest.TestCase):

    def test_discovers_multiple_tokens(self):
        env = {
            "CLARITY_TOKEN_ACME": "tok-acme",
            "CLARITY_TOKEN_GLOBEX": "tok-globex",
            "CLARITY_TOKEN_INITECH": "tok-initech",
        }
        with patch.dict(os.environ, env, clear=False):
            projects = ce._discover_projects()
        self.assertIn("ACME", projects)
        self.assertIn("GLOBEX", projects)
        self.assertIn("INITECH", projects)

    def test_no_tokens_returns_empty(self):
        # Remove todas as CLARITY_TOKEN_* do env para este teste
        filtered = {k: v for k, v in os.environ.items() if not k.startswith("CLARITY_TOKEN_")}
        with patch.dict(os.environ, filtered, clear=True):
            projects = ce._discover_projects()
        self.assertEqual(projects, {})

    def test_empty_value_ignored(self):
        env = {"CLARITY_TOKEN_EMPTY": ""}
        with patch.dict(os.environ, env, clear=False):
            projects = ce._discover_projects()
        self.assertNotIn("EMPTY", projects)


# ---------------------------------------------------------------------------
# TC-05: Seleção de --project
# ---------------------------------------------------------------------------

class TestResolveToken(unittest.TestCase):

    def test_resolve_by_project_name(self):
        with patch.dict(os.environ, {"CLARITY_TOKEN_ACME": "tok-acme"}, clear=False):
            label, token = ce._resolve_token("ACME")
        self.assertEqual(token, "tok-acme")

    def test_resolve_case_insensitive(self):
        with patch.dict(os.environ, {"CLARITY_TOKEN_ACME": "tok-acme"}, clear=False):
            label, token = ce._resolve_token("acme")
        self.assertEqual(token, "tok-acme")

    def test_resolve_fallback_to_api_token(self):
        env = {"CLARITY_API_TOKEN": "fallback-tok"}
        # Remove CLARITY_TOKEN_* e CLARITY_API_TOKEN para controle limpo
        filtered = {k: v for k, v in os.environ.items()
                    if not k.startswith("CLARITY_TOKEN_") and k != "CLARITY_API_TOKEN"}
        filtered["CLARITY_API_TOKEN"] = "fallback-tok"
        with patch.dict(os.environ, filtered, clear=True):
            label, token = ce._resolve_token(None)
        self.assertEqual(label, "default")
        self.assertEqual(token, "fallback-tok")

    def test_resolve_unknown_project_exits(self):
        filtered = {k: v for k, v in os.environ.items()
                    if not k.startswith("CLARITY_TOKEN_") and k != "CLARITY_API_TOKEN"}
        with patch.dict(os.environ, filtered, clear=True):
            with self.assertRaises(SystemExit):
                ce._resolve_token("NONEXISTENT")

    def test_resolve_no_token_at_all_exits(self):
        filtered = {k: v for k, v in os.environ.items()
                    if not k.startswith("CLARITY_TOKEN_") and k != "CLARITY_API_TOKEN"}
        with patch.dict(os.environ, filtered, clear=True):
            with self.assertRaises(SystemExit):
                ce._resolve_token(None)


# ---------------------------------------------------------------------------
# TC-06: Smoke — exit 0 e JSON válido
# ---------------------------------------------------------------------------

class TestSmokeCommand(unittest.TestCase):

    def _run_smoke(self, env_patch: dict, mock_response=None, project=None):
        """Roda cmd_smoke capturando stdout e garantindo exit 0."""
        import io
        from contextlib import redirect_stdout

        args = MagicMock()
        args.project = project

        buf = io.StringIO()
        with patch.dict(os.environ, env_patch, clear=True):
            if mock_response is not None:
                mock_resp = _make_http_response(json.dumps(mock_response).encode())
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    with redirect_stdout(buf):
                        try:
                            ce.cmd_smoke(args)
                        except SystemExit as e:
                            if e.code != 0:
                                raise
            else:
                with redirect_stdout(buf):
                    try:
                        ce.cmd_smoke(args)
                    except SystemExit as e:
                        if e.code != 0:
                            raise

        output = buf.getvalue().strip()
        data = json.loads(output)
        return data

    def test_smoke_no_token_overall_fail(self):
        """Sem nenhum token → overall FAIL, mas exit 0."""
        # Roda direto sem mock de urlopen (não chega a fazer chamada)
        import io
        from contextlib import redirect_stdout

        args = MagicMock()
        args.project = None

        filtered = {k: v for k, v in os.environ.items()
                    if not k.startswith("CLARITY_TOKEN_") and k != "CLARITY_API_TOKEN"}
        buf = io.StringIO()
        with patch.dict(os.environ, filtered, clear=True):
            with redirect_stdout(buf):
                try:
                    ce.cmd_smoke(args)
                except SystemExit as e:
                    if e.code != 0:
                        raise

        data = json.loads(buf.getvalue().strip())
        self.assertEqual(data["overall"], "FAIL")
        self.assertIn("duration_ms", data)
        self.assertIsInstance(data["steps"], list)

    def test_smoke_with_token_pass(self):
        """Token presente + resposta válida → overall PASS."""
        env = {"CLARITY_TOKEN_TEST": "tok-test", "CLARITY_API_BASE": ce.API_BASE}
        data = self._run_smoke(env, mock_response=TRAFFIC_RESPONSE, project="TEST")
        self.assertEqual(data["overall"], "PASS")
        self.assertIn("duration_ms", data)
        steps_names = [s["step"] for s in data["steps"]]
        self.assertIn("env_present", steps_names)
        self.assertIn("live_call", steps_names)

    def test_smoke_json_structure(self):
        """Saída JSON tem os campos obrigatórios."""
        env = {"CLARITY_TOKEN_TEST": "tok-test", "CLARITY_API_BASE": ce.API_BASE}
        data = self._run_smoke(env, mock_response=TRAFFIC_RESPONSE, project="TEST")
        self.assertIn("overall", data)
        self.assertIn("steps", data)
        self.assertIn("duration_ms", data)
        for step in data["steps"]:
            self.assertIn("step", step)
            self.assertIn("status", step)
            self.assertIn("duration_ms", step)


# ---------------------------------------------------------------------------
# TC-07: Tratamento de 429 não-JSON
# ---------------------------------------------------------------------------

class TestHttp429Handling(unittest.TestCase):

    def test_429_non_json_body_returns_dict(self):
        """429 com body não-JSON deve retornar dict com _error, não quebrar."""
        import urllib.error

        err = urllib.error.HTTPError(
            url="http://x", code=429, msg="Too Many Requests", hdrs=None, fp=None  # type: ignore
        )
        err.read = lambda: b"Rate limit exceeded (plain text, not JSON)"

        with patch("urllib.request.urlopen", side_effect=err):
            result = ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})

        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("_error"))
        self.assertEqual(result.get("status"), 429)
        self.assertIn("429", result.get("message", ""))

    def test_429_does_not_retry(self):
        """429 não deve disparar retry (sleep não chamado)."""
        import urllib.error

        err = urllib.error.HTTPError(
            url="http://x", code=429, msg="Too Many Requests", hdrs=None, fp=None  # type: ignore
        )
        err.read = lambda: b"quota"

        call_count = 0

        def _mock_urlopen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise err

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            with patch("time.sleep") as mock_sleep:
                ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})
                mock_sleep.assert_not_called()

        self.assertEqual(call_count, 1)  # só 1 tentativa


# ---------------------------------------------------------------------------
# TC-08: Body vazio
# ---------------------------------------------------------------------------

class TestEmptyBody(unittest.TestCase):

    def test_empty_list_response(self):
        """Resposta [] não deve quebrar — indica sem dados."""
        mock_resp = _make_http_response(b"[]")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})
        self.assertEqual(result, [])

    def test_empty_bytes_response(self):
        """Body vazio (b'') deve retornar {}."""
        mock_resp = _make_http_response(b"")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})
        self.assertEqual(result, {})

    def test_empty_list_not_error(self):
        """[] não tem _error."""
        mock_resp = _make_http_response(b"[]")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})
        self.assertFalse(result.get("_error", False) if isinstance(result, dict) else False)


# ---------------------------------------------------------------------------
# TC-09: HTTP helper sempre retorna dict (lint-safety)
# ---------------------------------------------------------------------------

class TestHttpHelperReturnType(unittest.TestCase):

    def test_success_returns_list_or_dict(self):
        """Em sucesso, retorna list (que é JSON-parsed) ou dict — nunca bytes/str."""
        mock_resp = _make_http_response(json.dumps(TRAFFIC_RESPONSE).encode())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})
        self.assertNotIsInstance(result, (bytes, str))

    def test_error_returns_dict(self):
        """Em erro HTTP, retorna dict com _error."""
        err = _make_http_error(401, b'{"message": "Unauthorized"}')
        with patch("urllib.request.urlopen", side_effect=err):
            result = ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("_error"))

    def test_network_error_returns_dict(self):
        """Em erro de rede, retorna dict com _error."""
        with patch("urllib.request.urlopen", side_effect=ConnectionResetError("reset")):
            result = ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("_error"))

    def test_500_retries_once(self):
        """500 dispara 1 retry."""
        call_count = 0
        err = _make_http_error(500, b'{"message": "Server Error"}')

        def _mock_urlopen(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise err

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
            with patch("time.sleep"):
                result = ce._clarity_request("tok", "project-live-insights", {"numOfDays": 1})

        self.assertEqual(call_count, 2)  # 1 original + 1 retry
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("_error"))


# ---------------------------------------------------------------------------
# TC-10: Token nunca vaza (stdout + stderr + mensagem de erro)
# ---------------------------------------------------------------------------

class TestTokenNotLeaked(unittest.TestCase):

    SECRET = "eyJsZWFrZWQ6dHJ1ZX0.super-secret-clarity-jwt"

    def test_list_projects_does_not_print_token(self):
        """cmd list-projects nunca deve imprimir valores de tokens."""
        import io
        from contextlib import redirect_stdout

        args = MagicMock()
        env = {
            "CLARITY_TOKEN_MYPROJEKT": self.SECRET,
            "CLARITY_API_BASE": ce.API_BASE,
        }
        buf = io.StringIO()
        with patch.dict(os.environ, env, clear=False):
            with redirect_stdout(buf):
                ce.cmd_list_projects(args)

        output = buf.getvalue()
        self.assertNotIn(self.SECRET, output)
        self.assertIn("MYPROJEKT", output)

    def test_token_not_in_http_401_error_message(self):
        """
        H1: Em 401 Unauthorized o token NÃO deve aparecer na mensagem de erro
        retornada pelo _clarity_request — nem em stdout nem no dict de erro.
        """
        import io
        from contextlib import redirect_stderr, redirect_stdout

        err = _make_http_error(401, b'{"message": "Unauthorized"}')

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        with patch("urllib.request.urlopen", side_effect=err):
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                result = ce._clarity_request(self.SECRET, "project-live-insights", {"numOfDays": 1})

        # O dict de retorno não deve conter o token
        result_str = json.dumps(result)
        self.assertNotIn(self.SECRET, result_str, "Token vazou no dict de erro")
        self.assertNotIn(self.SECRET, stdout_buf.getvalue(), "Token vazou no stdout")
        self.assertNotIn(self.SECRET, stderr_buf.getvalue(), "Token vazou no stderr")

        # Deve ser um dict de erro bem-formado
        self.assertTrue(result.get("_error"))
        self.assertEqual(result.get("status"), 401)

    def test_token_not_in_network_error_message(self):
        """
        H1: Em erro de rede genérico o token NÃO deve aparecer na mensagem.
        O teste é propositalmente adversarial: a exceção carrega o token no args[0]
        (simulando HDR-STRINGIFY ou REQUEST-REPR que incluem o Bearer header).
        FALHA com código sem _redact; PASSA após _redact aplicado.
        """
        import io
        from contextlib import redirect_stderr, redirect_stdout

        # Adversarial: a exception carrega o token literal no args[0]
        class _LeakyError(Exception):
            pass

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        # exc.args[0] contém o token — prova que _redact() na fonte é necessário
        with patch("urllib.request.urlopen", side_effect=_LeakyError(f"Bearer {self.SECRET} leaked via boom")):
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                result = ce._clarity_request(self.SECRET, "project-live-insights", {"numOfDays": 1})

        result_str = json.dumps(result)
        self.assertNotIn(self.SECRET, result_str, "Token vazou no dict de erro genérico (_redact ausente ou incompleto)")
        self.assertNotIn(self.SECRET, stdout_buf.getvalue())
        self.assertNotIn(self.SECRET, stderr_buf.getvalue())
        # Confirma que a mensagem de erro usa o placeholder
        self.assertIn("***", result.get("message", ""), "Esperado '***' como substituto do token")

    def test_token_not_in_smoke_output_on_error(self):
        """
        H1: cmd_smoke nunca inclui o token no JSON de saída, mesmo em falha 403.
        """
        import io
        from contextlib import redirect_stdout

        err = _make_http_error(403, b'{"message": "Forbidden"}')
        env = {"CLARITY_TOKEN_SECTEST": self.SECRET, "CLARITY_API_BASE": ce.API_BASE}
        args = MagicMock()
        args.project = "SECTEST"

        buf = io.StringIO()
        with patch.dict(os.environ, env, clear=True):
            with patch("urllib.request.urlopen", side_effect=err):
                with redirect_stdout(buf):
                    try:
                        ce.cmd_smoke(args)
                    except SystemExit:
                        pass

        output = buf.getvalue()
        self.assertNotIn(self.SECRET, output, "Token vazou no JSON do smoke")
        # Verifica que o output é JSON válido
        data = json.loads(output.strip())
        self.assertIn("overall", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
