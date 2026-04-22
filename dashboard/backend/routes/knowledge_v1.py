"""Knowledge V1 REST endpoints — spaces, units, documents, search.

URL prefix: /api/knowledge/v1
Auth: handled by knowledge.middleware.knowledge_auth_required (before_request)

Connection resolution:
  - External (API key): connection_id = g.knowledge_api_key["connection_id"]
  - Internal (dashboard token): X-Knowledge-Connection: <slug> header (400 if absent)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, g, jsonify, request

from knowledge.middleware import knowledge_auth_required

bp = Blueprint("knowledge_v1", __name__, url_prefix="/api/knowledge/v1")
bp.before_request(knowledge_auth_required)


# ---------------------------------------------------------------------------
# Connection resolution helper
# ---------------------------------------------------------------------------

def _resolve_connection_id() -> tuple:
    """Return (connection_id, error_response).

    For external auth: reads from g.knowledge_api_key.
    For internal auth: reads X-Knowledge-Connection header.
    Returns (None, (response, status)) on failure.
    """
    if getattr(g, "knowledge_auth_mode", None) == "internal":
        conn_header = request.headers.get("X-Knowledge-Connection", "").strip()
        if not conn_header:
            return None, (jsonify({"error": "X-Knowledge-Connection header required"}), 400)
        return conn_header, None

    api_key = getattr(g, "knowledge_api_key", None)
    if api_key is None:
        return None, (jsonify({"error": "unauthorized"}), 401)
    return api_key["connection_id"], None


# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------

@bp.route("/spaces", methods=["GET"])
def list_spaces():
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.spaces import list_spaces as _list

    owner_id = request.args.get("owner_id")
    visibility = request.args.get("visibility")
    spaces = _list(conn_id, owner_id=owner_id, visibility=visibility)
    return jsonify({"spaces": spaces}), 200


@bp.route("/spaces", methods=["POST"])
def create_space():
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    data = request.get_json(force=True) or {}
    if not data.get("slug") or not data.get("name"):
        return jsonify({"error": "slug and name are required"}), 400

    from knowledge.spaces import create_space as _create

    try:
        space = _create(conn_id, data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 409

    return jsonify(space), 201


@bp.route("/spaces/<space_id>", methods=["GET"])
def get_space(space_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.spaces import get_space as _get

    space = _get(conn_id, space_id)
    if space is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(space), 200


@bp.route("/spaces/<space_id>", methods=["PATCH"])
def update_space(space_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    data = request.get_json(force=True) or {}
    from knowledge.spaces import update_space as _update

    space = _update(conn_id, space_id, data)
    if space is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(space), 200


@bp.route("/spaces/<space_id>", methods=["DELETE"])
def delete_space(space_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.spaces import delete_space as _delete

    deleted = _delete(conn_id, space_id)
    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"ok": True}), 200


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

@bp.route("/spaces/<space_id>/units", methods=["GET"])
def list_units(space_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.units import list_units as _list

    parent_id = request.args.get("parent_id")
    units = _list(conn_id, space_id, parent_id=parent_id)
    return jsonify({"units": units}), 200


@bp.route("/spaces/<space_id>/units", methods=["POST"])
def create_unit(space_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    data = request.get_json(force=True) or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400

    data["space_id"] = space_id
    from knowledge.units import create_unit as _create

    try:
        unit = _create(conn_id, data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 409

    return jsonify(unit), 201


@bp.route("/spaces/<space_id>/units/<unit_id>", methods=["GET"])
def get_unit(space_id: str, unit_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.units import get_unit as _get

    unit = _get(conn_id, unit_id)
    if unit is None or unit.get("space_id") != space_id:
        return jsonify({"error": "not_found"}), 404
    return jsonify(unit), 200


@bp.route("/spaces/<space_id>/units/<unit_id>", methods=["PATCH"])
def update_unit(space_id: str, unit_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    data = request.get_json(force=True) or {}
    from knowledge.units import update_unit as _update

    unit = _update(conn_id, unit_id, data)
    if unit is None or unit.get("space_id") != space_id:
        return jsonify({"error": "not_found"}), 404
    return jsonify(unit), 200


@bp.route("/spaces/<space_id>/units/<unit_id>", methods=["DELETE"])
def delete_unit(space_id: str, unit_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.units import delete_unit as _delete, get_unit as _get

    unit = _get(conn_id, unit_id)
    if unit is None or unit.get("space_id") != space_id:
        return jsonify({"error": "not_found"}), 404

    _delete(conn_id, unit_id)
    return jsonify({"ok": True}), 200


@bp.route("/spaces/<space_id>/units/reorder", methods=["POST"])
def reorder_units(space_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    data = request.get_json(force=True) or {}
    ordered_ids = data.get("ordered_ids", [])
    if not isinstance(ordered_ids, list):
        return jsonify({"error": "ordered_ids must be a list"}), 400

    from knowledge.units import reorder_units as _reorder

    units = _reorder(conn_id, space_id, ordered_ids)
    return jsonify({"units": units}), 200


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@bp.route("/spaces/<space_id>/documents", methods=["GET"])
def list_documents(space_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.documents import list_documents as _list

    docs = _list(
        conn_id,
        space_id,
        unit_id=request.args.get("unit_id"),
        status=request.args.get("status"),
        content_type=request.args.get("content_type"),
        q=request.args.get("q"),
        limit=int(request.args.get("limit", 50)),
        offset=int(request.args.get("offset", 0)),
    )
    return jsonify({"documents": docs}), 200


@bp.route("/spaces/<space_id>/documents/upload", methods=["POST"])
def upload_document(space_id: str):
    """Upload a document for async ingestion.

    Request: multipart/form-data or JSON with fields:
      - file_path (str): absolute path to the file (or multipart 'file' field)
      - unit_id (str, optional)
      - metadata (JSON string or object, optional)
    """
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    # Accept either multipart or JSON
    if request.files and "file" in request.files:
        f = request.files["file"]
        upload_dir = Path(os.environ.get("KNOWLEDGE_UPLOAD_DIR", "/tmp/evonexus_uploads"))
        upload_dir.mkdir(parents=True, exist_ok=True)
        save_path = upload_dir / f.filename
        f.save(str(save_path))
        file_path = str(save_path)
    else:
        body = request.get_json(force=True) or {}
        file_path = body.get("file_path")

    if not file_path:
        return jsonify({"error": "file or file_path required"}), 400

    body = request.form.to_dict() if request.files else (request.get_json(force=True) or {})
    unit_id = body.get("unit_id")
    raw_meta = body.get("metadata")
    if isinstance(raw_meta, str):
        try:
            import json as _json
            metadata = _json.loads(raw_meta)
        except ValueError:
            metadata = {}
    elif isinstance(raw_meta, dict):
        metadata = raw_meta
    else:
        metadata = {}

    from knowledge.documents import upload_document as _upload

    doc = _upload(conn_id, space_id, file_path, metadata=metadata, unit_id=unit_id)
    return jsonify(doc), 202


@bp.route("/spaces/<space_id>/documents/<document_id>", methods=["GET"])
def get_document(space_id: str, document_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.documents import get_document as _get

    doc = _get(conn_id, document_id)
    if doc is None or doc.get("space_id") != space_id:
        return jsonify({"error": "not_found"}), 404
    return jsonify(doc), 200


@bp.route("/spaces/<space_id>/documents/<document_id>", methods=["PATCH"])
def update_document(space_id: str, document_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    data = request.get_json(force=True) or {}
    from knowledge.documents import update_document as _update, get_document as _get

    existing = _get(conn_id, document_id)
    if existing is None or existing.get("space_id") != space_id:
        return jsonify({"error": "not_found"}), 404

    doc = _update(conn_id, document_id, data)
    return jsonify(doc), 200


@bp.route("/spaces/<space_id>/documents/<document_id>", methods=["DELETE"])
def delete_document(space_id: str, document_id: str):
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    from knowledge.documents import delete_document as _delete, get_document as _get

    existing = _get(conn_id, document_id)
    if existing is None or existing.get("space_id") != space_id:
        return jsonify({"error": "not_found"}), 404

    _delete(conn_id, document_id)
    return jsonify({"ok": True}), 200


@bp.route("/spaces/<space_id>/documents/<document_id>/status", methods=["GET"])
def document_ingestion_status(space_id: str, document_id: str):
    """Return the ingestion progress from the worker status file."""
    from knowledge.documents import get_ingestion_status, get_document as _get

    conn_id, err = _resolve_connection_id()
    if err:
        return err

    existing = _get(conn_id, document_id)
    if existing is None or existing.get("space_id") != space_id:
        return jsonify({"error": "not_found"}), 404

    status = get_ingestion_status(document_id)
    if status is None:
        return jsonify({"document_id": document_id, "phase": "unknown"}), 200
    return jsonify(status), 200


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@bp.route("/spaces/<space_id>/search", methods=["POST"])
def search(space_id: str):
    """Hybrid search endpoint.

    Request JSON:
      {
        "query": "...",
        "top_k": 10,
        "filters": {
          "unit_id": "...",         (optional)
          "chunk_type": "...",      (optional)
          "content_type": "..."     (optional)
        }
      }
    """
    conn_id, err = _resolve_connection_id()
    if err:
        return err

    body = request.get_json(force=True) or {}
    query = body.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    top_k = int(body.get("top_k", 10))
    filters = body.get("filters") or {}

    from knowledge.search import hybrid_search

    try:
        results = hybrid_search(
            connection_id=conn_id,
            space_id=space_id,
            query=query,
            top_k=top_k,
            filters=filters,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"results": results, "total": len(results)}), 200
