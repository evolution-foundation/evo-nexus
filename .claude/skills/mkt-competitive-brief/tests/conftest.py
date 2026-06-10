"""conftest.py — pytest config para mkt-competitive-brief.

Bloqueia toda rede real (urllib.request autouse).
Carrega competitor_intel.py via importlib (não é package instalado).

competitor_intel.py usa importlib internamente para carregar dataforseo_client.py
de mkt-seo-ops. Os testes mocam _load_client() diretamente no módulo carregado
(monkeypatch) — padrão mais simples e robusto que mocar o importlib.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _TESTS_DIR.parent
_SCRIPTS_DIR = _SKILL_ROOT / "scripts"


def _load(name: str):
    """Carrega módulo de scripts/ via importlib (skill scripts não são package)."""
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[name] = mod
    return mod


# Carrega o módulo uma vez por sessão (tests são stateless via monkeypatch).
_ci_mod = _load("competitor_intel")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Bloqueia TODA rede real para todos os testes.

    Substitui urllib.request.urlopen por uma função que levanta URLError.
    Testes que precisam de comportamento específico usam mock_client.
    """

    def _deny(*args, **kwargs):
        raise urllib.error.URLError(
            "PROIBIDO: teste tentou abrir conexão de rede real. "
            "Use mock_client fixture para configurar respostas stub."
        )

    monkeypatch.setattr("urllib.request.urlopen", _deny)
    yield


@pytest.fixture()
def ci_mod():
    """Retorna o módulo competitor_intel carregado."""
    return _ci_mod


def _make_client(available: bool = True, keyword_intel_resp=None, post_resp=None):
    """Cria um mock de DataForSEOClient com comportamento configurável."""
    client = MagicMock()
    client.available.return_value = available
    if keyword_intel_resp is not None:
        client.keyword_intel.return_value = keyword_intel_resp
    if post_resp is not None:
        client._post.return_value = post_resp
    return client


@pytest.fixture()
def mock_client(monkeypatch):
    """
    Helper para mockar _load_client() em competitor_intel.

    Uso:
        def test_foo(mock_client, ci_mod):
            client = mock_client(available=True, keyword_intel_resp={...})
            result = ci_mod.serp_intel(["kw1"])
            ...

    Retorna o client mock configurado.
    """

    def _setup(available=True, keyword_intel_resp=None, post_resp=None, load_error=None):
        if load_error:
            monkeypatch.setattr(_ci_mod, "_load_client", lambda: (None, load_error))
            return None
        client = _make_client(available, keyword_intel_resp, post_resp)
        monkeypatch.setattr(_ci_mod, "_load_client", lambda: (client, None))
        return client

    return _setup
