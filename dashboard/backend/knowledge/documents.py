"""CRUD for knowledge_documents + async upload via subprocess worker.

Public API:
    upload_document(connection_id, space_id, file_path, metadata, unit_id) -> dict
        Creates a 'pending' row, spawns _knowledge_worker.py, returns immediately.
    list_documents(connection_id, space_id, **filters) -> list[dict]
    get_document(connection_id, document_id) -> dict | None
    update_document(connection_id, document_id, data) -> dict | None
    delete_document(connection_id, document_id) -> bool
    get_ingestion_status(document_id) -> dict | None
        Reads the JSON status file written by the worker.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from knowledge.connection_pool import get_dsn, get_engine


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sql(stmt: str):
    return text(stmt)


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row._mapping)
    for col in ("tags", "metadata"):
        if col in d and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except (ValueError, TypeError):
                pass
    return d


def _get_engine(connection_id: str):
    dsn = get_dsn(connection_id)
    return get_engine(connection_id, dsn)


def _status_dir() -> Path:
    """Return the ingestion status directory, creating it if needed."""
    base = (
        Path(__file__).parent.parent.parent  # dashboard/
        / "data"
        / "knowledge"
        / "ingestion"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
# Async upload (spawns worker subprocess)
# ---------------------------------------------------------------------------

def upload_document(
    connection_id: str,
    space_id: str,
    file_path: str | Path,
    metadata: Optional[Dict[str, Any]] = None,
    unit_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a 'pending' document row and spawn the ingestion worker.

    Returns immediately with the document dict (status='pending').
    The worker updates the row to 'processing' → 'ready' | 'error' asynchronously.
    """
    file_path = Path(file_path)
    if metadata is None:
        metadata = {}

    document_id = str(uuid.uuid4())
    doc_title = metadata.get("title") or file_path.stem
    now_iso = datetime.now(timezone.utc).isoformat()

    engine = _get_engine(connection_id)

    # Create 'pending' row before spawning worker (so callers can poll status)
    with engine.begin() as pg:
        pg.execute(
            _sql(
                """
                INSERT INTO knowledge_documents
                    (id, space_id, unit_id, title, description, tags, owner_id,
                     source_uri, mime_type, size_bytes, metadata, status,
                     error_message, created_at)
                VALUES
                    (:id, :space_id, :unit_id, :title, :description, :tags, :owner_id,
                     :source_uri, :mime_type, :size_bytes, CAST(:metadata AS jsonb), :status,
                     NULL, :created_at)
                """
            ),
            {
                "id": document_id,
                "space_id": space_id,
                "unit_id": unit_id,
                "title": doc_title,
                "description": metadata.get("description"),
                "tags": metadata.get("tags") or [],
                "owner_id": metadata.get("owner_id"),
                "source_uri": str(file_path),
                "mime_type": metadata.get("mime_type"),
                "size_bytes": file_path.stat().st_size if file_path.exists() else None,
                "metadata": json.dumps(metadata),
                "status": "pending",
                "created_at": now_iso,
            },
        )
        row = pg.execute(
            _sql("SELECT * FROM knowledge_documents WHERE id = :id"),
            {"id": document_id},
        ).fetchone()

    doc = _row_to_dict(row)

    # Spawn worker subprocess (non-blocking)
    worker_script = str(Path(__file__).parent / "_knowledge_worker.py")
    payload = json.dumps(
        {
            "connection_id": connection_id,
            "space_id": space_id,
            "unit_id": unit_id,
            "document_id": document_id,
            "file_path": str(file_path),
            "metadata": metadata,
        }
    )
    # Popen doesn't accept `input=` (that's subprocess.run only).
    # Write payload into stdin and close to signal EOF to the worker.
    process = subprocess.Popen(
        [sys.executable, worker_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdin is not None
    process.stdin.write(payload.encode("utf-8"))
    process.stdin.close()

    return doc


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_documents(
    connection_id: str,
    space_id: str,
    unit_id: Optional[str] = None,
    status: Optional[str] = None,
    content_type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return documents, filtered and paginated."""
    engine = _get_engine(connection_id)
    filters = ["space_id = :space_id"]
    params: Dict[str, Any] = {"space_id": space_id, "limit": limit, "offset": offset}

    if unit_id is not None:
        filters.append("unit_id = :unit_id")
        params["unit_id"] = unit_id
    if status is not None:
        filters.append("status = :status")
        params["status"] = status
    if content_type is not None:
        filters.append("content_type = :content_type")
        params["content_type"] = content_type
    if q is not None:
        filters.append("title ILIKE :q")
        params["q"] = f"%{q}%"

    where = "WHERE " + " AND ".join(filters)
    with engine.connect() as pg:
        rows = pg.execute(
            _sql(
                f"""
                SELECT * FROM knowledge_documents
                {where}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).fetchall()

    return [_row_to_dict(r) for r in rows]


def get_document(connection_id: str, document_id: str) -> Optional[Dict[str, Any]]:
    engine = _get_engine(connection_id)
    with engine.connect() as pg:
        row = pg.execute(
            _sql("SELECT * FROM knowledge_documents WHERE id = :id"),
            {"id": document_id},
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_document(
    connection_id: str, document_id: str, data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Update mutable document fields (title, description, tags, unit_id, content_type)."""
    engine = _get_engine(connection_id)

    allowed = {"title", "description", "tags", "unit_id", "content_type", "owner_id"}
    updates = []
    params: Dict[str, Any] = {"id": document_id}

    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key == "tags":
            updates.append("tags = :tags")
            params["tags"] = value if value is not None else []
        elif key == "metadata":
            updates.append("metadata = CAST(:metadata AS jsonb)")
            params["metadata"] = json.dumps(value if value is not None else {})
        else:
            updates.append(f"{key} = :{key}")
            params[key] = value

    if not updates:
        return get_document(connection_id, document_id)

    set_clause = ", ".join(updates)
    with engine.begin() as pg:
        pg.execute(
            _sql(f"UPDATE knowledge_documents SET {set_clause} WHERE id = :id"),
            params,
        )
        row = pg.execute(
            _sql("SELECT * FROM knowledge_documents WHERE id = :id"),
            {"id": document_id},
        ).fetchone()

    return _row_to_dict(row) if row else None


def delete_document(connection_id: str, document_id: str) -> bool:
    """Delete a document and its chunks (via CASCADE). Returns True if deleted."""
    engine = _get_engine(connection_id)
    with engine.begin() as pg:
        result = pg.execute(
            _sql("DELETE FROM knowledge_documents WHERE id = :id"),
            {"id": document_id},
        )
    return (result.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# Ingestion status (from worker status file)
# ---------------------------------------------------------------------------

def get_ingestion_status(document_id: str) -> Optional[Dict[str, Any]]:
    """Read the progress JSON file written by the ingestion worker."""
    status_file = _status_dir() / f"{document_id}.json"
    if not status_file.exists():
        return None
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
