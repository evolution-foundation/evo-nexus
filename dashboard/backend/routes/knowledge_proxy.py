"""Session-authenticated bridge routes for the Knowledge UI.

The v1 API (routes/knowledge_v1.py) uses Bearer token auth intended for
external consumers (Academy, webhooks, SDKs). The dashboard UI runs in
the user's session, so it cannot present a Bearer token from the browser.

This module exposes the same CRUD + search operations scoped under
  /api/knowledge/connections/<cid>/...
authenticated via the existing session-based @require_permission system.

The handlers delegate to the same knowledge.{spaces,units,documents,search}
functions that knowledge_v1 uses — zero logic duplication.
"""

from flask import Blueprint, jsonify, request

from routes.auth_routes import require_permission

from knowledge import spaces as spaces_mod
from knowledge import units as units_mod
from knowledge import documents as documents_mod
from knowledge import search as search_mod

bp = Blueprint("knowledge_proxy", __name__)


def _error(code: str, message: str, status: int = 400):
    return jsonify({"error": code, "message": message}), status


# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<cid>/spaces", methods=["GET"])
@require_permission("knowledge", "view")
def list_spaces(cid: str):
    try:
        owner = request.args.get("owner_id") or None
        visibility = request.args.get("visibility") or None
        items = spaces_mod.list_spaces(cid, owner_id=owner, visibility=visibility)
        return jsonify({"spaces": items})
    except Exception as exc:
        return _error("list_failed", str(exc), 500)


@bp.route("/api/knowledge/connections/<cid>/spaces", methods=["POST"])
@require_permission("knowledge", "manage")
def create_space(cid: str):
    data = request.get_json(silent=True) or {}
    try:
        space = spaces_mod.create_space(cid, data)
        return jsonify(space), 201
    except Exception as exc:
        return _error("create_failed", str(exc), 400)


@bp.route("/api/knowledge/connections/<cid>/spaces/<sid>", methods=["GET"])
@require_permission("knowledge", "view")
def get_space(cid: str, sid: str):
    try:
        space = spaces_mod.get_space(cid, sid)
        if not space:
            return _error("not_found", f"Space {sid} not found", 404)
        return jsonify(space)
    except Exception as exc:
        return _error("get_failed", str(exc), 500)


@bp.route("/api/knowledge/connections/<cid>/spaces/<sid>", methods=["PATCH"])
@require_permission("knowledge", "manage")
def update_space(cid: str, sid: str):
    data = request.get_json(silent=True) or {}
    try:
        space = spaces_mod.update_space(cid, sid, data)
        if not space:
            return _error("not_found", f"Space {sid} not found", 404)
        return jsonify(space)
    except Exception as exc:
        return _error("update_failed", str(exc), 400)


@bp.route("/api/knowledge/connections/<cid>/spaces/<sid>", methods=["DELETE"])
@require_permission("knowledge", "manage")
def delete_space(cid: str, sid: str):
    try:
        ok = spaces_mod.delete_space(cid, sid)
        if not ok:
            return _error("not_found", f"Space {sid} not found", 404)
        return jsonify({"status": "deleted"})
    except Exception as exc:
        return _error("delete_failed", str(exc), 500)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<cid>/spaces/<sid>/units", methods=["GET"])
@require_permission("knowledge", "view")
def list_units(cid: str, sid: str):
    try:
        items = units_mod.list_units(cid, sid)
        return jsonify({"units": items})
    except Exception as exc:
        return _error("list_failed", str(exc), 500)


@bp.route("/api/knowledge/connections/<cid>/spaces/<sid>/units", methods=["POST"])
@require_permission("knowledge", "manage")
def create_unit(cid: str, sid: str):
    data = request.get_json(silent=True) or {}
    data["space_id"] = sid
    try:
        unit = units_mod.create_unit(cid, data)
        return jsonify(unit), 201
    except Exception as exc:
        return _error("create_failed", str(exc), 400)


@bp.route("/api/knowledge/connections/<cid>/spaces/<sid>/units/<uid>", methods=["PATCH"])
@require_permission("knowledge", "manage")
def update_unit(cid: str, sid: str, uid: str):
    data = request.get_json(silent=True) or {}
    try:
        unit = units_mod.update_unit(cid, uid, data)
        if not unit:
            return _error("not_found", f"Unit {uid} not found", 404)
        return jsonify(unit)
    except Exception as exc:
        return _error("update_failed", str(exc), 400)


@bp.route("/api/knowledge/connections/<cid>/spaces/<sid>/units/<uid>", methods=["DELETE"])
@require_permission("knowledge", "manage")
def delete_unit(cid: str, sid: str, uid: str):
    try:
        ok = units_mod.delete_unit(cid, uid)
        if not ok:
            return _error("not_found", f"Unit {uid} not found", 404)
        return jsonify({"status": "deleted"})
    except Exception as exc:
        return _error("delete_failed", str(exc), 500)


@bp.route("/api/knowledge/connections/<cid>/spaces/<sid>/units/reorder", methods=["POST"])
@require_permission("knowledge", "manage")
def reorder_units(cid: str, sid: str):
    data = request.get_json(silent=True) or {}
    ordered = data.get("ordered_ids") or []
    try:
        units_mod.reorder_units(cid, sid, ordered)
        return jsonify({"status": "reordered", "count": len(ordered)})
    except Exception as exc:
        return _error("reorder_failed", str(exc), 400)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<cid>/documents", methods=["GET"])
@require_permission("knowledge", "view")
def list_documents(cid: str):
    try:
        filters = {
            "space_id": request.args.get("space_id") or None,
            "unit_id": request.args.get("unit_id") or None,
            "content_type": request.args.get("content_type") or None,
            "status": request.args.get("status") or None,
            "q": request.args.get("q") or None,
        }
        filters = {k: v for k, v in filters.items() if v is not None}
        limit = int(request.args.get("limit", 50))
        items = documents_mod.list_documents(cid, filters=filters, limit=limit)
        return jsonify({"documents": items})
    except Exception as exc:
        return _error("list_failed", str(exc), 500)


@bp.route("/api/knowledge/connections/<cid>/documents", methods=["POST"])
@require_permission("knowledge", "manage")
def upload_document(cid: str):
    if "file" not in request.files:
        return _error("bad_request", "Missing 'file' multipart field", 400)
    f = request.files["file"]
    space_id = request.form.get("space_id") or request.form.get("space")
    if not space_id:
        return _error("bad_request", "space_id is required", 400)

    import tempfile
    from pathlib import Path

    tmp_dir = Path(tempfile.gettempdir()) / "evonexus-knowledge-uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f.filename
    f.save(str(tmp_path))

    metadata = {
        "title": request.form.get("title") or Path(f.filename).stem,
        "tags": [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()],
    }
    unit_id = request.form.get("unit_id") or None

    try:
        result = documents_mod.upload_document(
            cid, space_id, str(tmp_path), metadata, unit_id=unit_id,
        )
        return jsonify(result), 202
    except Exception as exc:
        return _error("upload_failed", str(exc), 500)


@bp.route("/api/knowledge/connections/<cid>/documents/<did>", methods=["GET"])
@require_permission("knowledge", "view")
def get_document(cid: str, did: str):
    try:
        doc = documents_mod.get_document(cid, did)
        if not doc:
            return _error("not_found", f"Document {did} not found", 404)
        return jsonify(doc)
    except Exception as exc:
        return _error("get_failed", str(exc), 500)


@bp.route("/api/knowledge/connections/<cid>/documents/<did>", methods=["DELETE"])
@require_permission("knowledge", "manage")
def delete_document(cid: str, did: str):
    try:
        ok = documents_mod.delete_document(cid, did)
        if not ok:
            return _error("not_found", f"Document {did} not found", 404)
        return jsonify({"status": "deleted"})
    except Exception as exc:
        return _error("delete_failed", str(exc), 500)


@bp.route("/api/knowledge/connections/<cid>/documents/<did>/status", methods=["GET"])
@require_permission("knowledge", "view")
def document_status(cid: str, did: str):
    try:
        status = documents_mod.get_ingestion_status(cid, did)
        return jsonify(status)
    except Exception as exc:
        return _error("status_failed", str(exc), 500)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@bp.route("/api/knowledge/connections/<cid>/search", methods=["POST"])
@require_permission("knowledge", "view")
def search(cid: str):
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return _error("bad_request", "query is required", 400)
    try:
        results = search_mod.hybrid_search(
            connection_id=cid,
            space_id=data.get("space_id"),
            query=query,
            top_k=int(data.get("top_k", 10)),
            filters=data.get("filters") or {},
        )
        return jsonify({"results": results})
    except Exception as exc:
        return _error("search_failed", str(exc), 500)
