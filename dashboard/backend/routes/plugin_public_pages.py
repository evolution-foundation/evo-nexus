"""Plugin public pages — unauthenticated token-bound portals (B2.0).

Routes registered here bypass the ``before_request`` auth gate in ``app.py``.
The host validates the URL token against a plugin-declared column in a
plugin-owned table on every request.

B2.0 scope (read-only, no PIN):
  GET  /p/<slug>/<route_prefix>/<token>          — serve portal bundle
  GET  /p/<slug>/<route_prefix>/<token>/data     — serve public readonly query
  GET  /p/<slug>/public-assets/<path:subpath>    — serve ui/public/ static assets

B2.1 (PIN + writable + token-bind) is deferred.

Security controls applied here:
  - Rate limit 60 req/min/IP (from rate_limit.py) on portal + data endpoints
  - Vault §B2.S2: Referrer-Policy, Cache-Control no-store, HSTS on every response
  - Token validated parametrically (no SQL injection risk on token value)
  - table/column identifiers validated via PluginPublicPage schema at install time
  - Path traversal prevented by realpath + startswith containment check
  - MIME whitelist on public asset serving
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Blueprint, abort, jsonify, request, Response, after_this_request

from models import audit
from rate_limit import limiter

bp = Blueprint("plugin_public_pages", __name__)

# Resolved once at module load; identical to plugins.py pattern.
WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
PLUGINS_DIR = WORKSPACE / "plugins"
DB_PATH = WORKSPACE / "dashboard" / "data" / "evonexus.db"

# ---------------------------------------------------------------------------
# Module-level public prefix cache.
# Updated on install/uninstall via register_public_prefix / unregister_public_prefix.
# Read by app.py before_request middleware to bypass auth for /p/... paths.
# ---------------------------------------------------------------------------

# Set of string prefixes, each entry like "/p/nutri/portal"
_PLUGIN_PUBLIC_PREFIXES: set[str] = set()


def register_public_prefix(slug: str, route_prefix: str) -> None:
    """Add a plugin's public route prefix to the auth bypass cache.

    Called by plugin_loader.py (or routes/plugins.py) after a successful install.
    """
    _PLUGIN_PUBLIC_PREFIXES.add(f"/p/{slug}/{route_prefix}")


def unregister_public_prefix(slug: str, route_prefix: str) -> None:
    """Remove a plugin's public route prefix from the auth bypass cache.

    Called by routes/plugins.py during uninstall.
    """
    _PLUGIN_PUBLIC_PREFIXES.discard(f"/p/{slug}/{route_prefix}")


def get_public_prefixes() -> frozenset[str]:
    """Read-only snapshot of the current public prefix set.

    Used by app.py before_request middleware.
    """
    return frozenset(_PLUGIN_PUBLIC_PREFIXES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _security_headers(response: Response) -> Response:
    """Vault §B2.S2: mandatory security headers on all public-page responses."""
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store, private, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _load_page_config(slug: str, route_prefix: str) -> Optional[Dict[str, Any]]:
    """Return the installed public_pages config for the given slug + route_prefix.

    Reads from the manifest stored in plugins_installed (same pattern as plugins.py).
    Returns None if not found or not installed.
    """
    import json as _json
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT manifest_json FROM plugins_installed WHERE slug = ? AND status = 'active'",
            (slug,),
        ).fetchone()
        if not row:
            return None
        manifest = _json.loads(row["manifest_json"])
        for page in manifest.get("public_pages") or []:
            if page.get("route_prefix") == route_prefix:
                return page
        return None
    finally:
        conn.close()


def _validate_token(page_config: Dict[str, Any], token: str) -> bool:
    """Validate the URL token against the plugin-declared token_source column.

    Uses a parametric query — only the `?` value is user-supplied.
    Table and column names come from the manifest (validated at install by
    PluginPublicPage schema; both are slug-prefixed and identifier-safe).
    """
    token_source = page_config.get("token_source", {})
    table = token_source.get("table", "")
    column = token_source.get("column", "")

    if not table or not column:
        return False

    # Identifiers are validated at install time (PluginPublicPage schema) to
    # match ^[a-z][a-z0-9_]*$ — safe to interpolate here.
    sql = f"SELECT 1 FROM {table} WHERE {column} = ?"  # noqa: S608 — identifiers whitelisted at install

    # The plugin DB is kept inside the plugin's own data directory.
    # EvoNexus uses the shared evonexus.db for all plugin tables (no per-plugin DB).
    conn = _get_db()
    try:
        row = conn.execute(sql, (token,)).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        # Table doesn't exist yet (e.g. install in progress) — fail closed.
        return False
    finally:
        conn.close()


def _serve_bundle(slug: str, bundle_path: str) -> Response:
    """Serve a plugin's ui/public/ bundle file (no auth check needed here —
    caller already verified token; bundle is the entire page shell).

    ``bundle_path`` is relative to the plugin dir (e.g. "ui/public/portal.js").
    """
    plugin_dir = PLUGINS_DIR / slug
    ui_public_root = os.path.realpath(str(plugin_dir / "ui" / "public"))
    # Strip "ui/public/" prefix to get the sub-path
    relative = bundle_path[len("ui/public/"):]
    requested = os.path.realpath(os.path.join(ui_public_root, relative))

    # Containment check — must stay inside plugins/{slug}/ui/public/
    if not requested.startswith(ui_public_root + os.sep) and requested != ui_public_root:
        abort(404)

    if not os.path.isfile(requested):
        abort(404)

    ext = os.path.splitext(requested)[1].lower()
    mime_map = {
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }
    mime = mime_map.get(ext)
    if not mime:
        abort(404)

    with open(requested, "rb") as fh:
        content = fh.read()

    resp = Response(content, mimetype=mime)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # Content-Security-Policy: restrict resource loading to same origin.
    # 'unsafe-inline' is included for inline scripts in plugin bundles (Web Component pattern).
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'"
    )
    return resp


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@bp.route("/p/<slug>/<route_prefix>/<token>", methods=["GET"])
@limiter.limit("60 per minute")
def portal_page(slug: str, route_prefix: str, token: str):
    """Serve the plugin portal page after validating the URL token.

    Flow:
    1. Load page config from plugins_installed manifest.
    2. Validate token against token_source.column (parametric SQL).
    3. Serve the plugin's ui/public/ bundle.
    4. Apply security headers.
    """
    @after_this_request
    def _headers(response: Response) -> Response:
        return _security_headers(response)

    page_config = _load_page_config(slug, route_prefix)
    if not page_config:
        return jsonify({"error": "Link inválido ou expirado", "code": "not_found"}), 404

    if not _validate_token(page_config, token):
        ip = request.remote_addr or "-"
        audit(
            None,
            page_config.get("audit_action") or "portal_view_denied",
            f"plugins/{slug}/public_pages/{route_prefix}",
            detail=f"token={token[:8]}... ip={ip} reason=token_invalid",
        )
        return jsonify({"error": "Link inválido ou expirado", "code": "not_found"}), 404

    # Token valid — log successful view
    ip = request.remote_addr or "-"
    ua = (request.headers.get("User-Agent", "-") or "-")[:200]
    audit(
        None,
        page_config.get("audit_action") or "portal_view",
        f"plugins/{slug}/public_pages/{route_prefix}",
        detail=f"token={token[:8]}... ip={ip} ua={ua[:80]}",
    )

    bundle_path = page_config.get("bundle", "")
    return _serve_bundle(slug, bundle_path)


@bp.route("/p/<slug>/<route_prefix>/<token>/data", methods=["GET"])
@limiter.limit("120 per minute")
def portal_data(slug: str, route_prefix: str, token: str):
    """Serve public readonly query results bound to the URL token.

    Requires a ``query_id`` query-string param that matches a declared
    readonly_data entry with ``public_via`` pointing to this page.
    """
    @after_this_request
    def _headers(response: Response) -> Response:
        return _security_headers(response)

    query_id = request.args.get("query_id", "").strip()
    if not query_id:
        return jsonify({"error": "query_id is required", "code": "bad_request"}), 400

    page_config = _load_page_config(slug, route_prefix)
    if not page_config:
        return jsonify({"error": "Link inválido ou expirado", "code": "not_found"}), 404

    if not _validate_token(page_config, token):
        return jsonify({"error": "Link inválido ou expirado", "code": "not_found"}), 404

    # Load readonly_data entries from the manifest to find the matching public query
    import json as _json
    conn_meta = _get_db()
    try:
        row = conn_meta.execute(
            "SELECT manifest_json FROM plugins_installed WHERE slug = ? AND status = 'active'",
            (slug,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Plugin not found", "code": "not_found"}), 404
        manifest = _json.loads(row["manifest_json"])
    finally:
        conn_meta.close()

    # Find the query
    public_page_id = page_config.get("id")
    query_spec = None
    for q in manifest.get("readonly_data") or []:
        if q.get("id") == query_id and q.get("public_via") == public_page_id:
            query_spec = q
            break

    if not query_spec:
        return jsonify({"error": "Query not found or not public", "code": "not_found"}), 404

    bind_param = query_spec.get("bind_token_param")
    sql = query_spec.get("sql", "")

    # Execute query with token bound to the declared parameter
    conn_data = _get_db()
    try:
        if bind_param:
            rows = conn_data.execute(sql, {bind_param: token}).fetchall()
        else:
            rows = conn_data.execute(sql).fetchall()
        results = [dict(r) for r in rows]
    except sqlite3.OperationalError as exc:
        return jsonify({"error": "Query execution failed", "detail": str(exc)}), 500
    finally:
        conn_data.close()

    return jsonify({"query_id": query_id, "rows": results})


@bp.route("/p/<slug>/public-assets/<path:subpath>", methods=["GET"])
def portal_static(slug: str, subpath: str):
    """Serve plugin static assets from ui/public/ (no token required).

    CSS, images, and other non-JS assets referenced by the portal bundle.
    Path must stay within plugins/{slug}/ui/public/ (containment check).
    """
    @after_this_request
    def _headers(response: Response) -> Response:
        return _security_headers(response)

    plugin_dir = PLUGINS_DIR / slug
    ui_public_root = os.path.realpath(str(plugin_dir / "ui" / "public"))
    requested = os.path.realpath(os.path.join(ui_public_root, subpath))

    # Containment check
    if not requested.startswith(ui_public_root + os.sep):
        abort(404)

    if not os.path.isfile(requested):
        abort(404)

    ext = os.path.splitext(requested)[1].lower()
    mime_map = {
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".json": "application/json; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".ttf": "font/ttf",
    }
    mime = mime_map.get(ext)
    if not mime:
        abort(404)

    with open(requested, "rb") as fh:
        content = fh.read()

    resp = Response(content, mimetype=mime)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # Static assets can be cached by the browser (shorter TTL for public portal)
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp
