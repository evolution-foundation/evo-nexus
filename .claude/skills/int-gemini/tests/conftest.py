"""conftest.py — pytest config para int-gemini.

Stuba urllib.request.urlopen em autouse para que NENHUM teste toque a rede real.
Padrão idêntico ao int-woocommerce / int-wordpress.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Sequence

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

_SKILL_ROOT = _TESTS_DIR.parent
_SCRIPT_PATH = _SKILL_ROOT / "scripts" / "gemini_client.py"

# Garante que a chave existe no import (cmd não falha por env ausente nos testes
# que não testam o caminho "sem chave"). Os testes que querem testar a ausência
# manipulam gemini_client.API_KEY diretamente via monkeypatch.
import os  # noqa: E402

os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")

_spec = importlib.util.spec_from_file_location("gemini_client", _SCRIPT_PATH)
_gemini = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gemini)

sys.modules["gemini_client"] = _gemini


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Bloqueia toda rede real — qualquer tentativa levanta RuntimeError."""
    def _deny(*args, **kwargs):
        raise RuntimeError(
            "PROIBIDO: teste tentou abrir conexão de rede real. "
            "Use o fixture mock_urlopen para configurar respostas stub."
        )

    monkeypatch.setattr("urllib.request.urlopen", _deny)
    monkeypatch.setattr(_gemini.urllib.request, "urlopen", _deny)
    # neutraliza o sleep do retry pra teste não travar 20s
    monkeypatch.setattr(_gemini.time, "sleep", lambda *_a, **_k: None)
    yield


@pytest.fixture()
def mock_urlopen(monkeypatch):
    """Substitui urlopen por uma fila de respostas (ou exceções).

    Uso:
        mock_urlopen([make_response({...}, 200)])
    """
    responses: list = []

    def _fake_urlopen(req, timeout=60):
        if not responses:
            raise RuntimeError("mock_urlopen: fila de respostas esgotada")
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr(_gemini.urllib.request, "urlopen", _fake_urlopen)

    def _setup(resp_list: Sequence):
        responses.clear()
        responses.extend(resp_list)

    return _setup
