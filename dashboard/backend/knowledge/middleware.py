"""Auth middleware for /api/knowledge/v1/* endpoints.

Two auth modes:

1. **Internal** — ``Authorization: Bearer <DASHBOARD_API_TOKEN>``
   Bypasses rate limiting.  Sets ``g.knowledge_auth_mode = "internal"``.

2. **External** — ``Authorization: Bearer evo_k_<prefix>.<secret>``
   Verifies against SQLite ``knowledge_api_keys``, checks scope, applies
   fixed-window rate limit against remote Postgres.
   Sets ``g.knowledge_auth_mode = "external"`` and ``g.knowledge_api_key``.

On any error the response is fail-closed:
* Wrong/missing token   → 401 ``{"error": "unauthorized"}``
* Scope violation       → 403 ``{"error": "scope_violation"}``
* Rate limit exceeded   → 429 ``{"error": "rate_limit_exceeded", "retry_after": N}``
* Rate limiter down     → 503 ``{"error": "service_unavailable", "retry_after": 5}``
"""

from __future__ import annotations

import os

from flask import g, jsonify, request

from knowledge.api_keys import verify_token
from knowledge.rate_limiter import (
    RateLimitExceeded,
    RateLimiterUnavailable,
    check_rate_limit,
    get_dsn_for_connection,
)


def _internal_token_matches(bearer: str) -> bool:
    expected = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not expected:
        return False
    import secrets as _sec
    return _sec.compare_digest(bearer, expected)


def knowledge_auth_required():
    """Flask before_request handler — attach to the knowledge_public blueprint.

    Returns a Flask response on auth/rate-limit failure, or None to continue.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return jsonify({"error": "unauthorized"}), 401

    bearer = header[len("Bearer "):].strip()
    if not bearer:
        return jsonify({"error": "unauthorized"}), 401

    # --- Internal path ---
    if _internal_token_matches(bearer):
        g.knowledge_auth_mode = "internal"
        g.knowledge_api_key = None
        return None  # proceed, no rate limit

    # --- External path ---
    api_key = verify_token(bearer)
    if api_key is None:
        return jsonify({"error": "unauthorized"}), 401

    # Scope check — requested connection must match the key's connection
    connection_id = request.view_args.get("connection_id") if request.view_args else None
    if connection_id and api_key.get("connection_id") != connection_id:
        return jsonify({"error": "scope_violation"}), 403

    # Space scope check — if the route carries a space_id, verify it's listed
    space_id = request.view_args.get("space_id") if request.view_args else None
    if space_id:
        allowed_spaces: list = api_key.get("space_ids", [])
        if allowed_spaces and space_id not in allowed_spaces:
            return jsonify({"error": "scope_violation"}), 403

    # Rate limit (fail-closed)
    try:
        dsn = get_dsn_for_connection(api_key["connection_id"])
        check_rate_limit(
            dsn=dsn,
            key_id=api_key["id"],
            rate_limit_per_min=api_key.get("rate_limit_per_min", 60),
            rate_limit_per_day=api_key.get("rate_limit_per_day", 10000),
        )
    except RateLimitExceeded as exc:
        resp = jsonify({"error": "rate_limit_exceeded", "retry_after": exc.retry_after})
        resp.headers["Retry-After"] = str(exc.retry_after)
        return resp, 429
    except RateLimiterUnavailable:
        resp = jsonify({"error": "service_unavailable", "retry_after": 5})
        resp.headers["Retry-After"] = "5"
        return resp, 503

    g.knowledge_auth_mode = "external"
    g.knowledge_api_key = api_key
    return None  # proceed
