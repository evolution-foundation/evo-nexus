"""CRUD for knowledge_spaces (Postgres via SQLAlchemy).

Public API:
    create_space(connection_id, data) -> dict
    list_spaces(connection_id, owner_id=None, visibility=None) -> list[dict]
    get_space(connection_id, space_id) -> dict | None
    update_space(connection_id, space_id, data) -> dict | None
    delete_space(connection_id, space_id) -> bool
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from knowledge.connection_pool import get_dsn, get_engine


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sql(stmt: str):
    return text(stmt)


def _row_to_dict(row) -> Dict[str, Any]:
    """Convert a SQLAlchemy Row to a plain dict."""
    d = dict(row._mapping)
    # Decode JSONB columns if they come back as strings
    for col in ("access_rules", "content_type_boosts"):
        if col in d and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except (ValueError, TypeError):
                pass
    return d


def _get_engine(connection_id: str):
    dsn = get_dsn(connection_id)
    return get_engine(connection_id, dsn)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_space(connection_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a new knowledge_space row.

    Required fields: name (str), slug (str)
    Optional: description, owner_id, visibility, access_rules, content_type_boosts
    """
    engine = _get_engine(connection_id)
    space_id = str(uuid.uuid4())

    access_rules = data.get("access_rules") or {}
    content_type_boosts = data.get("content_type_boosts") or {}

    with engine.begin() as pg:
        pg.execute(
            _sql(
                """
                INSERT INTO knowledge_spaces
                    (id, slug, name, description, owner_id, visibility,
                     access_rules, content_type_boosts)
                VALUES
                    (:id, :slug, :name, :description, :owner_id, :visibility,
                     CAST(:access_rules AS jsonb), CAST(:content_type_boosts AS jsonb))
                """
            ),
            {
                "id": space_id,
                "slug": data["slug"],
                "name": data["name"],
                "description": data.get("description"),
                "owner_id": data.get("owner_id"),
                "visibility": data.get("visibility", "private"),
                "access_rules": json.dumps(access_rules),
                "content_type_boosts": json.dumps(content_type_boosts),
            },
        )
        row = pg.execute(
            _sql("SELECT * FROM knowledge_spaces WHERE id = :id"),
            {"id": space_id},
        ).fetchone()

    return _row_to_dict(row)


def list_spaces(
    connection_id: str,
    owner_id: Optional[str] = None,
    visibility: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return spaces, optionally filtered by owner_id and/or visibility."""
    engine = _get_engine(connection_id)
    filters = []
    params: Dict[str, Any] = {}

    if owner_id is not None:
        filters.append("owner_id = :owner_id")
        params["owner_id"] = owner_id
    if visibility is not None:
        filters.append("visibility = :visibility")
        params["visibility"] = visibility

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    with engine.connect() as pg:
        rows = pg.execute(
            _sql(f"SELECT * FROM knowledge_spaces {where} ORDER BY created_at DESC"),
            params,
        ).fetchall()

    return [_row_to_dict(r) for r in rows]


def get_space(connection_id: str, space_id: str) -> Optional[Dict[str, Any]]:
    """Return a single space by id, or None if not found."""
    engine = _get_engine(connection_id)
    with engine.connect() as pg:
        row = pg.execute(
            _sql("SELECT * FROM knowledge_spaces WHERE id = :id"),
            {"id": space_id},
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_space_by_slug(connection_id: str, slug: str) -> Optional[Dict[str, Any]]:
    """Return a single space by slug, or None if not found."""
    engine = _get_engine(connection_id)
    with engine.connect() as pg:
        row = pg.execute(
            _sql("SELECT * FROM knowledge_spaces WHERE slug = :slug"),
            {"slug": slug},
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_space(
    connection_id: str, space_id: str, data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Update mutable fields of a space. Returns updated row or None if not found."""
    engine = _get_engine(connection_id)

    allowed = {"name", "description", "visibility", "access_rules", "content_type_boosts"}
    updates = []
    params: Dict[str, Any] = {"id": space_id}

    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key in ("access_rules", "content_type_boosts"):
            updates.append(f"{key} = CAST(:{key} AS jsonb)")
            params[key] = json.dumps(value if value is not None else {})
        else:
            updates.append(f"{key} = :{key}")
            params[key] = value

    if not updates:
        return get_space(connection_id, space_id)

    set_clause = ", ".join(updates)
    with engine.begin() as pg:
        pg.execute(
            _sql(f"UPDATE knowledge_spaces SET {set_clause} WHERE id = :id"),
            params,
        )
        row = pg.execute(
            _sql("SELECT * FROM knowledge_spaces WHERE id = :id"),
            {"id": space_id},
        ).fetchone()

    return _row_to_dict(row) if row else None


def delete_space(connection_id: str, space_id: str) -> bool:
    """Delete a space and all its cascaded children. Returns True if deleted."""
    engine = _get_engine(connection_id)
    with engine.begin() as pg:
        result = pg.execute(
            _sql("DELETE FROM knowledge_spaces WHERE id = :id"),
            {"id": space_id},
        )
    return (result.rowcount or 0) > 0
