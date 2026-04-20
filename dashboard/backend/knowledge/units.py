"""CRUD for knowledge_units (Postgres via SQLAlchemy).

Schema (see migrations/versions/001_initial_schema.py):
    id, space_id, slug, title, description, sequence_idx,
    prerequisites (uuid[]), metadata (jsonb), created_at

Public API:
    create_unit(connection_id, data) -> dict
    list_units(connection_id, space_id) -> list[dict]
    get_unit(connection_id, unit_id) -> dict | None
    update_unit(connection_id, unit_id, data) -> dict | None
    delete_unit(connection_id, unit_id) -> bool
    reorder_units(connection_id, space_id, ordered_ids) -> list[dict]
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
    d = dict(row._mapping)
    if "metadata" in d and isinstance(d["metadata"], str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (ValueError, TypeError):
            pass
    return d


def _get_engine(connection_id: str):
    dsn = get_dsn(connection_id)
    return get_engine(connection_id, dsn)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_unit(connection_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a new knowledge_unit row.

    Required: space_id (str), title (str), slug (str)
    Optional: description, sequence_idx, prerequisites (list[uuid]), metadata
    """
    engine = _get_engine(connection_id)
    unit_id = str(uuid.uuid4())
    metadata = data.get("metadata") or {}
    prerequisites = data.get("prerequisites") or []

    # Accept legacy `name` from older callers; `title` is canonical.
    title = data.get("title") or data.get("name")
    if not title:
        raise ValueError("title is required")

    # Accept legacy `sort_order` as fallback.
    seq = data.get("sequence_idx")
    if seq is None:
        seq = data.get("sort_order", 0)

    with engine.begin() as pg:
        pg.execute(
            _sql(
                """
                INSERT INTO knowledge_units
                    (id, space_id, slug, title, description, sequence_idx,
                     prerequisites, metadata)
                VALUES
                    (:id, :space_id, :slug, :title, :description, :sequence_idx,
                     :prerequisites, CAST(:metadata AS jsonb))
                """
            ),
            {
                "id": unit_id,
                "space_id": data["space_id"],
                "slug": data["slug"],
                "title": title,
                "description": data.get("description"),
                "sequence_idx": seq,
                "prerequisites": prerequisites,
                "metadata": json.dumps(metadata),
            },
        )
        row = pg.execute(
            _sql("SELECT * FROM knowledge_units WHERE id = :id"),
            {"id": unit_id},
        ).fetchone()

    return _row_to_dict(row)


def list_units(
    connection_id: str,
    space_id: str,
) -> List[Dict[str, Any]]:
    """Return units in a space, ordered by sequence_idx ASC."""
    engine = _get_engine(connection_id)
    with engine.connect() as pg:
        rows = pg.execute(
            _sql(
                """
                SELECT * FROM knowledge_units
                WHERE space_id = :space_id
                ORDER BY sequence_idx ASC, created_at ASC
                """
            ),
            {"space_id": space_id},
        ).fetchall()

    return [_row_to_dict(r) for r in rows]


def get_unit(connection_id: str, unit_id: str) -> Optional[Dict[str, Any]]:
    engine = _get_engine(connection_id)
    with engine.connect() as pg:
        row = pg.execute(
            _sql("SELECT * FROM knowledge_units WHERE id = :id"),
            {"id": unit_id},
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_unit(
    connection_id: str, unit_id: str, data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Update mutable unit fields. Returns updated row or None if not found."""
    engine = _get_engine(connection_id)

    # Normalize legacy field names.
    if "name" in data and "title" not in data:
        data = {**data, "title": data["name"]}
    if "sort_order" in data and "sequence_idx" not in data:
        data = {**data, "sequence_idx": data["sort_order"]}

    allowed = {"title", "description", "sequence_idx", "prerequisites",
               "metadata", "slug"}
    updates = []
    params: Dict[str, Any] = {"id": unit_id}

    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key == "metadata":
            updates.append("metadata = CAST(:metadata AS jsonb)")
            params["metadata"] = json.dumps(value if value is not None else {})
        else:
            updates.append(f"{key} = :{key}")
            params[key] = value

    if not updates:
        return get_unit(connection_id, unit_id)

    set_clause = ", ".join(updates)
    with engine.begin() as pg:
        pg.execute(
            _sql(f"UPDATE knowledge_units SET {set_clause} WHERE id = :id"),
            params,
        )
        row = pg.execute(
            _sql("SELECT * FROM knowledge_units WHERE id = :id"),
            {"id": unit_id},
        ).fetchone()

    return _row_to_dict(row) if row else None


def delete_unit(connection_id: str, unit_id: str) -> bool:
    """Delete a unit. Returns True if deleted."""
    engine = _get_engine(connection_id)
    with engine.begin() as pg:
        result = pg.execute(
            _sql("DELETE FROM knowledge_units WHERE id = :id"),
            {"id": unit_id},
        )
    return (result.rowcount or 0) > 0


def reorder_units(
    connection_id: str, space_id: str, ordered_ids: List[str]
) -> List[Dict[str, Any]]:
    """Assign sequence_idx = index position for each unit_id in ordered_ids."""
    engine = _get_engine(connection_id)
    with engine.begin() as pg:
        for idx, uid in enumerate(ordered_ids):
            pg.execute(
                _sql(
                    """
                    UPDATE knowledge_units
                    SET sequence_idx = :sequence_idx
                    WHERE id = :id AND space_id = :space_id
                    """
                ),
                {"sequence_idx": idx, "id": uid, "space_id": space_id},
            )
    return list_units(connection_id, space_id)
