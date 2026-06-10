"""test_autentique_client.py — Suite de testes para int-autentique.

Cobertura:
- gql: sucesso, erros GQL, HTTP 4xx/5xx, retry, connection error
- gql_upload: arquivo não encontrado (anti-fabricação)
- cmd_whoami: retorno correto
- cmd_documents_list: paginação + campos
- cmd_documents_get: campos completos incluindo status de signatários
- cmd_documents_create: validação --signers (JSON inválido, array vazio)
- cmd_documents_download: URL ausente → error; download OK
- cmd_documents_delete: sem --confirm → error; com --confirm → sucesso
- cmd_folders_list: retorno correto
- cmd_smoke: exit 0, JSON steps/overall, auth fail não trava
- Anti-fabricação: erros retornam campo _error (via json), não fabricam dados
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# conftest bootstrap carrega autentique_client em sys.modules
import autentique_client as aut

from helpers import make_gql_response, make_gql_error, make_http_error


# ===========================================================================
# gql — camada de transporte
# ===========================================================================

class TestGql:
    """Testa gql() — JSON simples (queries/mutations sem upload)."""

    def test_sucesso_retorna_data(self, monkeypatch):
        resp = make_gql_response({"me": {"id": "u1", "name": "Jane Doe", "email": "jane@example.com"}})
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "token-test")
        with patch("urllib.request.urlopen", return_value=resp):
            data = aut.gql("query { me { id name email } }")
        assert data["me"]["email"] == "jane@example.com"

    def test_erros_gql_emite_error_e_exits2(self, monkeypatch, capsys):
        errors = [{"message": "Unauthorized"}]
        resp = make_gql_error(errors)
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "token-test")
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(SystemExit) as exc:
                aut.gql("query { me { id } }")
        assert exc.value.code == 2
        out = json.loads(capsys.readouterr().out)
        assert "error" in out
        assert out["error"] == "GraphQL errors"

    def test_http_4xx_emite_error_e_exits2(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "token-test")
        with patch("urllib.request.urlopen", side_effect=make_http_error(401, "Unauthorized")):
            with pytest.raises(SystemExit) as exc:
                aut.gql("query { me { id } }")
        assert exc.value.code == 2
        out = json.loads(capsys.readouterr().out)
        assert "HTTP 401" in out["error"]

    def test_http_5xx_retries_e_exits2(self, monkeypatch, capsys):
        """500 deve retryar 3x e depois emitir erro."""
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "token-test")
        call_count = 0
        def _fail(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise make_http_error(500, "Internal Server Error")
        with patch("urllib.request.urlopen", side_effect=_fail):
            with patch("time.sleep"):  # evita espera real
                with pytest.raises(SystemExit) as exc:
                    aut.gql("query { me { id } }")
        assert exc.value.code == 2
        assert call_count == aut.RETRY_ATTEMPTS

    def test_connection_error_exits2(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "token-test")
        err = urllib.error.URLError("Connection refused")
        with patch("urllib.request.urlopen", side_effect=err):
            with patch("time.sleep"):
                with pytest.raises(SystemExit) as exc:
                    aut.gql("query { me { id } }")
        assert exc.value.code == 2


class TestTokenMissing:
    def test_sem_token_exits1(self, monkeypatch, capsys):
        monkeypatch.delenv("AUTENTIQUE_API_TOKEN", raising=False)
        with pytest.raises(SystemExit) as exc:
            aut._token()
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert "AUTENTIQUE_API_TOKEN" in out["error"]


# ===========================================================================
# gql_upload — multipart
# ===========================================================================

class TestGqlUpload:
    def test_arquivo_inexistente_exits1(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "token-test")
        with pytest.raises(SystemExit) as exc:
            aut.gql_upload("mutation {}", {}, "/tmp/nao-existe-xpto-9999.pdf")
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert "error" in out


# ===========================================================================
# cmd_whoami
# ===========================================================================

class TestCmdWhoami:
    def test_retorna_dados_usuario(self, monkeypatch, capsys):
        me_data = {"me": {"id": "u-123", "name": "Jane Doe", "email": "jane@example.com"}}
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: me_data)
        args = argparse.Namespace()
        aut.cmd_whoami(args)
        out = json.loads(capsys.readouterr().out)
        assert out["me"]["email"] == "jane@example.com"


# ===========================================================================
# cmd_documents_list
# ===========================================================================

class TestCmdDocumentsList:
    def test_lista_documentos(self, monkeypatch, capsys):
        payload = {
            "documents": {
                "total": 2,
                "data": [
                    {"id": "d1", "name": "Distrato A", "created_at": "2026-06-01", "signatures": []},
                    {"id": "d2", "name": "Contrato B", "created_at": "2026-06-02", "signatures": []},
                ],
            }
        }
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: payload)
        args = argparse.Namespace(page=1, limit=20)
        aut.cmd_documents_list(args)
        out = json.loads(capsys.readouterr().out)
        assert out["documents"]["total"] == 2
        assert out["documents"]["data"][0]["id"] == "d1"

    def test_lista_vazia(self, monkeypatch, capsys):
        payload = {"documents": {"total": 0, "data": []}}
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: payload)
        args = argparse.Namespace(page=1, limit=20)
        aut.cmd_documents_list(args)
        out = json.loads(capsys.readouterr().out)
        assert out["documents"]["total"] == 0


# ===========================================================================
# cmd_documents_get
# ===========================================================================

class TestCmdDocumentsGet:
    def test_retorna_detalhes_e_status_signatarios(self, monkeypatch, capsys):
        payload = {
            "document": {
                "id": "doc-uuid-1",
                "name": "Contrato de Prestação de Serviço",
                "created_at": "2026-06-01",
                "sandbox": False,
                "refusable": True,
                "qualified": False,
                "files": {"original": "https://cdn.autentique.com.br/doc.pdf", "signed": None},
                "signatures": [
                    {
                        "public_id": "sig-1",
                        "name": "Cliente X",
                        "email": "cliente@empresa.com",
                        "action": {"name": "SIGN"},
                        "viewed": None,
                        "signed": None,
                        "rejected": None,
                    },
                    {
                        "public_id": "sig-2",
                        "name": "Jane Doe",
                        "email": "responsavel@empresa.com",
                        "action": {"name": "SIGN"},
                        "viewed": {"created_at": "2026-06-02T10:00:00"},
                        "signed": {"created_at": "2026-06-02T10:05:00"},
                        "rejected": None,
                    },
                ],
            }
        }
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: payload)
        args = argparse.Namespace(document_id="doc-uuid-1")
        aut.cmd_documents_get(args)
        out = json.loads(capsys.readouterr().out)
        assert out["document"]["id"] == "doc-uuid-1"
        sigs = out["document"]["signatures"]
        assert len(sigs) == 2
        # Signatário que assinou tem signed preenchido
        responsavel = next(s for s in sigs if s["email"] == "responsavel@empresa.com")
        assert responsavel["signed"] is not None
        # Cliente ainda não assinou
        cliente = next(s for s in sigs if s["email"] == "cliente@empresa.com")
        assert cliente["signed"] is None


# ===========================================================================
# cmd_documents_create — validação de --signers
# ===========================================================================

class TestCmdDocumentsCreate:
    def test_signers_json_invalido_exits1(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        args = argparse.Namespace(
            file="/tmp/fake.pdf",
            name="Contrato",
            signers="nao-e-json",
            message=None, reminder=None, qualified=False, sandbox=False,
        )
        with pytest.raises(SystemExit) as exc:
            aut.cmd_documents_create(args)
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert "error" in out

    def test_signers_array_vazio_exits1(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        args = argparse.Namespace(
            file="/tmp/fake.pdf",
            name="Contrato",
            signers="[]",
            message=None, reminder=None, qualified=False, sandbox=False,
        )
        with pytest.raises(SystemExit) as exc:
            aut.cmd_documents_create(args)
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert "error" in out

    def test_signers_nao_array_exits1(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        args = argparse.Namespace(
            file="/tmp/fake.pdf",
            name="Contrato",
            signers='{"email": "a@b.com"}',  # objeto, não array
            message=None, reminder=None, qualified=False, sandbox=False,
        )
        with pytest.raises(SystemExit) as exc:
            aut.cmd_documents_create(args)
        assert exc.value.code == 1

    def test_create_chama_gql_upload_com_signers_validos(self, monkeypatch, capsys, tmp_path):
        """Com args válidos, deve chamar gql_upload (anti-fabricação: arquivo real)."""
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.delenv("AUTENTIQUE_ORGANIZATION_ID", raising=False)
        pdf = tmp_path / "contrato.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        signers = '[{"email":"cliente@a.com","action":"SIGN"}]'

        captured_args = {}
        def _fake_upload(query, variables, file_path):
            captured_args["query"] = query
            captured_args["variables"] = variables
            captured_args["file_path"] = file_path
            return {"createDocument": {"id": "new-doc-id", "name": "Contrato", "sandbox": False,
                                       "files": {"original": "http://x.com/doc.pdf"},
                                       "signatures": [{"public_id": "p1", "name": "Cliente", "email": "cliente@a.com", "link": {"short_link": "http://s.lnk"}}]}}

        monkeypatch.setattr(aut, "gql_upload", _fake_upload)
        args = argparse.Namespace(
            file=str(pdf),
            name="Contrato",
            signers=signers,
            message="Por favor assine.",
            reminder="WEEKLY",
            qualified=False,
            sandbox=False,
        )
        aut.cmd_documents_create(args)
        assert captured_args["file_path"] == str(pdf)
        assert captured_args["variables"]["document"]["name"] == "Contrato"
        assert captured_args["variables"]["document"]["message"] == "Por favor assine."

    def test_create_sandbox_flag(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.delenv("AUTENTIQUE_ORGANIZATION_ID", raising=False)
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        captured = {}
        def _fake_upload(query, variables, file_path):
            captured["sandbox"] = variables["document"].get("sandbox", False)
            return {"createDocument": {"id": "x", "name": "T", "sandbox": True,
                                       "files": {"original": "u"}, "signatures": []}}

        monkeypatch.setattr(aut, "gql_upload", _fake_upload)
        args = argparse.Namespace(
            file=str(pdf), name="T", signers='[{"email":"a@b.com","action":"SIGN"}]',
            message=None, reminder=None, qualified=False, sandbox=True,
        )
        aut.cmd_documents_create(args)
        assert captured["sandbox"] is True


# ===========================================================================
# cmd_documents_download
# ===========================================================================

class TestCmdDocumentsDownload:
    def test_url_ausente_exits2(self, monkeypatch, capsys, tmp_path):
        """Se URL da versão solicitada não estiver disponível → error, exit 2."""
        payload = {
            "document": {
                "id": "doc1",
                "name": "Distrato",
                "files": {"original": "http://x.com/orig.pdf", "signed": None},
            }
        }
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: payload)
        args = argparse.Namespace(
            document_id="doc1",
            version="signed",
            output=str(tmp_path / "out.pdf"),
        )
        with pytest.raises(SystemExit) as exc:
            aut.cmd_documents_download(args)
        assert exc.value.code == 2
        out = json.loads(capsys.readouterr().out)
        assert "error" in out

    def test_download_original_salva_arquivo(self, monkeypatch, capsys, tmp_path):
        payload = {
            "document": {
                "id": "doc2",
                "name": "Contrato",
                "files": {"original": "http://cdn.autentique.com.br/doc.pdf", "signed": None},
            }
        }
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: payload)
        out_path = tmp_path / "original.pdf"

        class _FakeURLResp:
            def read(self):
                return b"%PDF-1.4 conteudo"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        with patch("urllib.request.urlopen", return_value=_FakeURLResp()):
            args = argparse.Namespace(
                document_id="doc2", version="original", output=str(out_path)
            )
            aut.cmd_documents_download(args)

        out = json.loads(capsys.readouterr().out)
        assert out["saved"] == str(out_path)
        assert out["version"] == "original"
        assert out_path.read_bytes() == b"%PDF-1.4 conteudo"


# ===========================================================================
# cmd_documents_delete
# ===========================================================================

class TestCmdDocumentsDelete:
    def test_sem_confirm_exits1(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        args = argparse.Namespace(document_id="doc1", confirm=False)
        with pytest.raises(SystemExit) as exc:
            aut.cmd_documents_delete(args)
        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert "Confirmação obrigatória" in out["error"]

    def test_com_confirm_deleta(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: {"deleteDocument": True})
        args = argparse.Namespace(document_id="doc1", confirm=True)
        aut.cmd_documents_delete(args)
        out = json.loads(capsys.readouterr().out)
        assert out["deleteDocument"] is True


# ===========================================================================
# cmd_folders_list
# ===========================================================================

class TestCmdFoldersList:
    def test_lista_pastas(self, monkeypatch, capsys):
        payload = {
            "folders": {
                "data": [
                    {"id": "f1", "name": "Contratos"},
                    {"id": "f2", "name": "Distratos"},
                ]
            }
        }
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: payload)
        args = argparse.Namespace()
        aut.cmd_folders_list(args)
        out = json.loads(capsys.readouterr().out)
        assert len(out["folders"]["data"]) == 2

    def test_pastas_vazias(self, monkeypatch, capsys):
        payload = {"folders": {"data": []}}
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: payload)
        args = argparse.Namespace()
        aut.cmd_folders_list(args)
        out = json.loads(capsys.readouterr().out)
        assert out["folders"]["data"] == []


# ===========================================================================
# cmd_smoke
# ===========================================================================

class TestCmdSmoke:
    """Smoke sempre exit 0 mesmo com falhas internas."""

    def test_smoke_pass(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        responses = [
            {"me": {"id": "u1", "name": "M", "email": "jane@example.com"}},
            {"documents": {"total": 3}},
        ]
        call_idx = 0
        def _fake_gql(*a, **kw):
            nonlocal call_idx
            r = responses[call_idx]
            call_idx += 1
            return r
        monkeypatch.setattr(aut, "gql", _fake_gql)
        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc:
            aut.cmd_smoke(args)
        assert exc.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["overall"] == "PASS"
        assert len(out["steps"]) == 2
        assert "duration_ms" in out

    def test_smoke_falha_auth_ainda_exit0(self, monkeypatch, capsys):
        monkeypatch.delenv("AUTENTIQUE_API_TOKEN", raising=False)
        # gql vai chamar _token() que vai sys.exit(1) — smoke deve capturar
        args = argparse.Namespace()
        # Smoke captura exceção de _token via try/except + sys.exit(0)
        with pytest.raises(SystemExit) as exc:
            aut.cmd_smoke(args)
        assert exc.value.code == 0
        out = json.loads(capsys.readouterr().out)
        # overall pode ser PASS ou FAIL dependendo da captura
        assert "overall" in out

    def test_smoke_falha_gql_ainda_exit0(self, monkeypatch, capsys):
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        def _fail(*a, **kw):
            raise RuntimeError("API indisponível")
        monkeypatch.setattr(aut, "gql", _fail)
        args = argparse.Namespace()
        with pytest.raises(SystemExit) as exc:
            aut.cmd_smoke(args)
        assert exc.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert "overall" in out
        assert "steps" in out


# ===========================================================================
# Anti-fabricação — erros HTTP retornam campo error, não dados fabricados
# ===========================================================================

class TestAntiFabricacao:
    def test_gql_nao_retorna_dados_em_erro(self, monkeypatch, capsys):
        """Em caso de erro GraphQL, o output contém 'error', não dados fabricados."""
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        resp = make_gql_error([{"message": "Not authorized"}])
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(SystemExit):
                aut.gql("query { me { id } }")
        out = json.loads(capsys.readouterr().out)
        assert "error" in out
        assert "me" not in out  # sem dados fabricados

    def test_documento_sem_url_signed_nao_fabrica_url(self, monkeypatch, capsys, tmp_path):
        """Se URL signed é None, retorna error — nunca fabrica uma URL."""
        payload = {
            "document": {
                "id": "d1", "name": "Doc",
                "files": {"original": "http://x.com/a.pdf", "signed": None},
            }
        }
        monkeypatch.setenv("AUTENTIQUE_API_TOKEN", "tok")
        monkeypatch.setattr(aut, "gql", lambda *a, **kw: payload)
        args = argparse.Namespace(
            document_id="d1", version="signed", output=str(tmp_path / "out.pdf")
        )
        with pytest.raises(SystemExit) as exc:
            aut.cmd_documents_download(args)
        assert exc.value.code == 2
        out = json.loads(capsys.readouterr().out)
        assert "error" in out
        # Jamais retornar uma URL fabricada
        assert "http" not in out.get("error", "")


# ===========================================================================
# Argparse — smoke command presente
# ===========================================================================

class TestArgparse:
    def test_smoke_subcommand_existe(self):
        parser = aut.build_parser()
        args = parser.parse_args(["smoke"])
        assert args.command == "smoke"

    def test_documents_list_defaults(self):
        parser = aut.build_parser()
        args = parser.parse_args(["documents", "list"])
        assert args.page == 1
        assert args.limit == 20
        assert args.subcommand == "list"

    def test_documents_create_required_args(self):
        parser = aut.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["documents", "create", "--name", "X"])  # --file obrigatório

    def test_delete_sem_confirm_flag_default_false(self):
        parser = aut.build_parser()
        args = parser.parse_args(["documents", "delete", "doc-uuid"])
        assert args.confirm is False
