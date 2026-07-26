"""Best-effort redaction for persisted or API-visible subprocess output."""

from __future__ import annotations

import os
import re

_REDACTED = "[REDACTED]"
_AUTH_HEADER_RE = re.compile(
    r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:api[_-]?key|token|secret|password|authkey)\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://[^:/@\s]+:)[^@\s]+@")
_SENSITIVE_ENV_NAME_RE = re.compile(
    r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH[_-]?KEY)", re.I
)


def redact_secrets(value: object, *, limit: int | None = None) -> str:
    """Return text with common credentials and configured secret values removed."""
    text = "" if value is None else str(value)
    text = _AUTH_HEADER_RE.sub(rf"\1{_REDACTED}", text)
    text = _ASSIGNMENT_RE.sub(rf"\1{_REDACTED}", text)
    text = _URL_CREDENTIAL_RE.sub(rf"\1{_REDACTED}@", text)

    configured = {
        secret
        for name, secret in os.environ.items()
        if _SENSITIVE_ENV_NAME_RE.search(name) and len(secret) >= 8
    }
    for secret in sorted(configured, key=len, reverse=True):
        text = text.replace(secret, _REDACTED)

    return text if limit is None else text[:limit]
