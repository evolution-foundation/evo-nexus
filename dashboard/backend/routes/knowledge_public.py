"""Public Knowledge API blueprint — /api/knowledge/v1.

This blueprint is for external callers using knowledge API keys.
Auth is handled by ``knowledge.middleware.knowledge_auth_required``
registered as a before_request hook.

Endpoints implemented here (Step 4 scope):
  POST  /api/knowledge/v1/_ping     — health check, validates auth

Search, spaces, and document endpoints are implemented in Steps 2 and 3.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify

from knowledge.middleware import knowledge_auth_required

bp = Blueprint("knowledge_public", __name__, url_prefix="/api/knowledge/v1")

bp.before_request(knowledge_auth_required)


@bp.route("/_ping", methods=["POST"])
def ping():
    """Health check — confirms that auth and rate limit passed.

    Response body:
    ```json
    {"ok": true, "auth_mode": "internal" | "external"}
    ```
    """
    auth_mode = getattr(g, "knowledge_auth_mode", "unknown")
    return jsonify({"ok": True, "auth_mode": auth_mode}), 200


@bp.route("/spaces/<space_id>/_probe", methods=["GET"])
def probe_space(space_id: str):
    """Minimal probe endpoint for space-scoped auth testing (also used by Steps 2/3)."""
    return jsonify({"space_id": space_id}), 200
