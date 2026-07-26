from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def test_redact_secrets_removes_auth_headers_assignments_urls_and_env_values(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "provider-secret-value")

    from secret_redaction import redact_secrets

    text = (
        "Authorization: Bearer bearer-secret-value "
        "api_key=inline-secret-value "
        "https://alice:url-password@example.invalid/path "
        "provider-secret-value"
    )

    redacted = redact_secrets(text)

    for secret in (
        "bearer-secret-value",
        "inline-secret-value",
        "url-password",
        "provider-secret-value",
    ):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") >= 4


def test_redact_secrets_applies_limit_after_redaction():
    from secret_redaction import redact_secrets

    assert redact_secrets("token=very-secret-value trailing", limit=20) == (
        "token=[REDACTED] tra"
    )
