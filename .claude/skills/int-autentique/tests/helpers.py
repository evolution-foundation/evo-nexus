"""helpers.py — factories de resposta para testes de int-autentique."""
from __future__ import annotations

import io
import json
import urllib.error


def make_gql_response(data: dict) -> object:
    """Context-manager que simula urlopen bem-sucedido com payload GQL."""
    body = json.dumps({"data": data}).encode()

    class _FakeResp:
        def read(self):
            return body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    return _FakeResp()


def make_gql_error(errors: list) -> object:
    """Resposta GraphQL com campo 'errors'."""
    body = json.dumps({"errors": errors}).encode()

    class _FakeResp:
        def read(self):
            return body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    return _FakeResp()


def make_http_error(code: int, msg: str = "error") -> urllib.error.HTTPError:
    body = json.dumps({"message": msg}).encode()
    return urllib.error.HTTPError(
        url="https://api.autentique.com.br/v2/graphql",
        code=code,
        msg=msg,
        hdrs={},
        fp=io.BytesIO(body),
    )
