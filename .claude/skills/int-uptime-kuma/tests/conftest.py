"""conftest.py — pytest config para int-uptime-kuma.

Bloqueia toda rede real (autouse). Bootstrap do módulo via path (não é package
instalado). Padrão idêntico às skills de referência (int-blue, int-agendor).

O cliente Socket.io (python-socketio) é stubado: os testes nunca abrem socket
real. Testes do caminho de escrita usam o fake `socketio.Client` para configurar
acks de resposta.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: importar uptime_kuma_client.py via path
# ---------------------------------------------------------------------------
_TESTS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _TESTS_DIR.parent
_SCRIPT_PATH = _SKILL_ROOT / "scripts" / "uptime_kuma_client.py"

_spec = importlib.util.spec_from_file_location("uptime_kuma_client", _SCRIPT_PATH)
_uk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_uk)
sys.modules.setdefault("uptime_kuma_client", _uk)


@pytest.fixture
def uk():
    """The module under test."""
    return _uk


# ---------------------------------------------------------------------------
# Fixture autouse — bloqueia toda rede real + define creds/env fake
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def _deny(*args, **kwargs):
        raise RuntimeError(
            "PROIBIDO: teste tentou abrir conexão de rede real. "
            "Use o fake socketio ou stub _fetch_metrics."
        )
    monkeypatch.setattr("urllib.request.urlopen", _deny)
    yield


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setattr(_uk, "BASE_URL", "https://status.example.com")
    monkeypatch.setattr(_uk, "API_KEY", "uk1_testkey")
    monkeypatch.setattr(_uk, "USERNAME", "tester")
    monkeypatch.setattr(_uk, "PASSWORD", "testpass")
    yield


@pytest.fixture(autouse=True)
def reset_write_count(monkeypatch):
    """O contador de writes é global no módulo — zera entre testes."""
    monkeypatch.setattr(_uk, "_write_count", 0)
    monkeypatch.setattr(_uk, "_max_writes", 5)
    yield


# ---------------------------------------------------------------------------
# Fake python-socketio client
# ---------------------------------------------------------------------------

class FakeSioClient:
    """Stand-in for socketio.Client. Records emits and returns scripted acks."""

    # class-level scripting hooks the tests can set
    connect_should_fail = 0          # number of leading connect() calls that raise
    acks: dict = {}                  # event -> ack dict to return

    def __init__(self, *args, **kwargs):
        self.calls: list = []
        FakeSioClient.last_instance = self

    def connect(self, url, **kwargs):
        if FakeSioClient.connect_should_fail > 0:
            FakeSioClient.connect_should_fail -= 1
            raise RuntimeError("simulated connect failure")
        self.connected_url = url

    def call(self, event, *args, **kwargs):
        self.calls.append((event, args))
        if event in FakeSioClient.acks:
            return FakeSioClient.acks[event]
        # default: success
        return {"ok": True, "msg": "successAdded", "monitorID": 42}

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def fake_socketio(monkeypatch):
    """Injects a fake `socketio` module so UKSocket.connect imports the fake."""
    import types
    FakeSioClient.connect_should_fail = 0
    FakeSioClient.acks = {}
    fake_module = types.ModuleType("socketio")
    fake_module.Client = FakeSioClient
    monkeypatch.setitem(sys.modules, "socketio", fake_module)
    # make time.sleep instant so retry/backoff doesn't slow tests
    monkeypatch.setattr(_uk.time, "sleep", lambda *a, **k: None)
    return FakeSioClient
