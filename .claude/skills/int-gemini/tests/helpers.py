"""Helpers de teste para int-gemini — constroem respostas HTTP mock."""
from __future__ import annotations

import json
import urllib.error
from typing import Union


class MockHTTPResponse:
    """Response simulada compatível com urllib.request.urlopen context manager."""

    def __init__(self, body: Union[dict, list, str], status: int = 200):
        if isinstance(body, str):
            self._body = body.encode()
        else:
            self._body = json.dumps(body).encode()
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def make_response(body: Union[dict, list, str], status: int = 200) -> MockHTTPResponse:
    return MockHTTPResponse(body, status)


def make_generate_response(text: str, *, prompt_tokens: int = 10,
                           candidates_tokens: int = 5, total_tokens: int = 15,
                           finish: str = "STOP",
                           grounding_chunks: list | None = None) -> MockHTTPResponse:
    """Resposta no formato generateContent da Gemini API."""
    candidate: dict = {
        "content": {"parts": [{"text": text}]},
        "finishReason": finish,
    }
    if grounding_chunks is not None:
        candidate["groundingMetadata"] = {"groundingChunks": grounding_chunks}
    return make_response({
        "candidates": [candidate],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": candidates_tokens,
            "totalTokenCount": total_tokens,
        },
    })


def make_models_response(names: list[str]) -> MockHTTPResponse:
    return make_response({
        "models": [
            {
                "name": f"models/{n}",
                "inputTokenLimit": 1048576,
                "outputTokenLimit": 65536,
                "supportedGenerationMethods": ["generateContent"],
            }
            for n in names
        ]
    })


class _MockBody:
    """fp truthy com .read() — o _request usa `e.fp` pra decidir se lê o corpo."""

    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self, *_a, **_k) -> bytes:
        return self._raw

    def close(self) -> None:
        pass


def make_http_error(status: int, message: str = "boom") -> urllib.error.HTTPError:
    """HTTPError simulado para casos de falha (429/5xx/etc.)."""
    body = json.dumps({"error": {"message": message}}).encode()
    err = urllib.error.HTTPError(
        url="http://test/x", code=status, msg=message,
        hdrs={}, fp=_MockBody(body),
    )
    return err
