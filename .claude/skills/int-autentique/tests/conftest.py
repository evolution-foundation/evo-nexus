"""conftest.py — pytest config para int-autentique.

Stuba urllib.request.urlopen em autouse para que NENHUM teste toque a rede real.
Padrão idêntico ao das skills de referência (int-agendor, int-wordpress).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: garantir que tests/ está no sys.path para imports de helpers
# ---------------------------------------------------------------------------
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# ---------------------------------------------------------------------------
# Bootstrap: importar autentique_client via path (não é package instalado)
# ---------------------------------------------------------------------------
_SKILL_ROOT = _TESTS_DIR.parent
_SCRIPT_PATH = _SKILL_ROOT / "scripts" / "autentique_client.py"

_spec = importlib.util.spec_from_file_location("autentique_client", _SCRIPT_PATH)
_aut_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_aut_module)
sys.modules["autentique_client"] = _aut_module


# ---------------------------------------------------------------------------
# Fixture autouse — bloqueia toda rede real
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Substitui urlopen por stub que falha com RuntimeError.

    Garante que nenhum teste toque a rede real. Testes que precisam de
    comportamento HTTP específico usam monkeypatch em aut.gql diretamente.
    """
    def _deny(*args, **kwargs):
        raise RuntimeError(
            "PROIBIDO: teste tentou abrir conexão de rede real. "
            "Use monkeypatch.setattr(aut, 'gql', ...) no teste."
        )

    monkeypatch.setattr("urllib.request.urlopen", _deny)
    yield
