"""
tests/backend/test_whatsapp_notifications.py

Panorama 2026-07-17, item 3 — notifications.send_whatsapp used to hardcode
the Evolution Go instance to "nature" (a different client's WhatsApp number),
so every call silently sent from the wrong number. Now defaults to
"sistema-britto" (Sistema Britto's own connected instance) and accepts an
override.

Run:
    cd /path/to/workspace && pytest tests/backend/test_whatsapp_notifications.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import notifications  # noqa: E402


@pytest.fixture(autouse=True)
def _evolution_go_env(monkeypatch):
    monkeypatch.setenv("EVOLUTION_GO_URL", "https://evo.example.com")
    monkeypatch.setenv("EVOLUTION_GO_KEY", "test-key")
    monkeypatch.delenv("EVOLUTION_GO_INSTANCE", raising=False)


def _mock_response():
    resp = MagicMock()
    resp.status = 200
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_send_whatsapp_defaults_to_sistema_britto_instance():
    with patch("urllib.request.urlopen", return_value=_mock_response()) as mock_urlopen:
        ok = notifications.send_whatsapp("oi", "5511999999999")
    assert ok is True
    request_obj = mock_urlopen.call_args[0][0]
    assert request_obj.full_url.endswith("/message/sendText/sistema-britto")


def test_send_whatsapp_respects_explicit_instance_override():
    with patch("urllib.request.urlopen", return_value=_mock_response()) as mock_urlopen:
        notifications.send_whatsapp("oi", "5511999999999", instance="outro-cliente")
    request_obj = mock_urlopen.call_args[0][0]
    assert request_obj.full_url.endswith("/message/sendText/outro-cliente")


def test_send_whatsapp_respects_env_instance_override(monkeypatch):
    monkeypatch.setenv("EVOLUTION_GO_INSTANCE", "env-instance")
    with patch("urllib.request.urlopen", return_value=_mock_response()) as mock_urlopen:
        notifications.send_whatsapp("oi", "5511999999999")
    request_obj = mock_urlopen.call_args[0][0]
    assert request_obj.full_url.endswith("/message/sendText/env-instance")


def test_send_whatsapp_returns_false_without_credentials(monkeypatch):
    monkeypatch.delenv("EVOLUTION_GO_URL", raising=False)
    monkeypatch.delenv("EVOLUTION_GO_KEY", raising=False)
    assert notifications.send_whatsapp("oi", "5511999999999") is False
