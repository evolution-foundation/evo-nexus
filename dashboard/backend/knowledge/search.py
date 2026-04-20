"""Hybrid search: HNSW vector + BM25 FTS + RRF + metadata boost.

Algorithm (ADR-009):
  1. Embed query (cached 60 s per query text + connection).
  2. CTE `vec`  — top-100 by cosine similarity via pgvector HNSW.
  3. CTE `fts`  — top-100 by BM25 tsvector full-text search.
  4. CTE `fused` — RRF( vec_rank, fts_rank ) scores.
  5. JOIN to knowledge_documents to get content_type.
  6. Apply boost:
       score * COALESCE(space.content_type_boosts->>'<type>', '1.0')::float
     If COALESCE returned 1.0 (i.e., no space override), multiply by DEFAULT_BOOSTS.
  7. Re-sort and slice top_k.

Public API:
    hybrid_search(connection_id, space_id, query, top_k, filters) -> list[dict]
"""

from __future__ import annotations

import json
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from knowledge.connection_pool import get_dsn, get_engine
from knowledge.embedders import get_embedder


# ---------------------------------------------------------------------------
# Default content-type boosts (used when space.content_type_boosts has no override)
# ---------------------------------------------------------------------------

DEFAULT_BOOSTS: Dict[str, float] = {
    "faq": 1.20,
    "reference": 1.15,
    "lesson": 1.10,
    "tutorial": 1.05,
    "article": 1.00,
    "decision": 1.00,
    "note": 0.90,
    "transcript": 0.80,
}

# RRF constant (standard value)
_RRF_K = 60


# ---------------------------------------------------------------------------
# Embedding cache (TTL 60 s per query+connection_id key)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_embed_cache: Dict[Tuple[str, str], Tuple[List[float], float]] = {}
_EMBED_CACHE_TTL = 60.0


def _get_query_vector(connection_id: str, query: str) -> List[float]:
    """Return cached embedding or compute fresh."""
    cache_key = (connection_id, query)
    now = time.monotonic()

    with _cache_lock:
        entry = _embed_cache.get(cache_key)
        if entry is not None:
            vector, ts = entry
            if now - ts < _EMBED_CACHE_TTL:
                return vector

    embedder = get_embedder()
    vectors = embedder.embed([query])
    vector = vectors[0]

    with _cache_lock:
        _embed_cache[cache_key] = (vector, now)
        # Evict stale entries (keep cache bounded)
        stale = [k for k, (_, ts) in _embed_cache.items() if now - ts >= _EMBED_CACHE_TTL]
        for k in stale:
            del _embed_cache[k]

    return vector


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sql(stmt: str):
    return text(stmt)


def _get_engine(connection_id: str):
    dsn = get_dsn(connection_id)
    return get_engine(connection_id, dsn)


def _vector_str(vector: List[float]) -> str:
    return "[" + ",".join(str(v) for v in vector) + "]"


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------

_HYBRID_SQL = """
WITH vec AS (
    SELECT
        c.id             AS chunk_id,
        c.document_id,
        c.space_id,
        c.unit_id,
        c.chunk_idx,
        c.chunk_type,
        c.content,
        c.metadata,
        row_number() OVER (ORDER BY c.embedding <=> CAST(:query_vec AS vector)) AS vec_rank
    FROM knowledge_chunks c
    WHERE c.space_id = :space_id
      AND c.embedding IS NOT NULL
    ORDER BY c.embedding <=> CAST(:query_vec AS vector)
    LIMIT 100
),
fts AS (
    SELECT
        c.id             AS chunk_id,
        row_number() OVER (ORDER BY ts_rank_cd(c.content_tsv, plainto_tsquery('portuguese', :query_text)) DESC) AS fts_rank
    FROM knowledge_chunks c
    WHERE c.space_id = :space_id
      AND c.content_tsv @@ plainto_tsquery('portuguese', :query_text)
    ORDER BY ts_rank_cd(c.content_tsv, plainto_tsquery('portuguese', :query_text)) DESC
    LIMIT 100
),
fused AS (
    SELECT
        COALESCE(v.chunk_id, f.chunk_id) AS chunk_id,
        (
            COALESCE(1.0 / (:rrf_k + v.vec_rank), 0.0)
            + COALESCE(1.0 / (:rrf_k + f.fts_rank), 0.0)
        ) AS rrf_score
    FROM vec v
    FULL OUTER JOIN fts f ON v.chunk_id = f.chunk_id
)
SELECT
    fu.chunk_id,
    fu.rrf_score,
    v.document_id,
    v.space_id,
    v.unit_id,
    v.chunk_idx,
    v.chunk_type,
    v.content,
    v.metadata       AS chunk_metadata,
    d.title          AS doc_title,
    d.content_type,
    d.status         AS doc_status,
    COALESCE(
        (s.content_type_boosts ->> d.content_type)::float,
        1.0
    )                AS space_boost
FROM fused fu
JOIN vec v ON fu.chunk_id = v.chunk_id
JOIN knowledge_documents d ON v.document_id = d.id
JOIN knowledge_spaces s ON v.space_id = s.id
WHERE d.status = 'ready'
ORDER BY fu.rrf_score DESC
LIMIT :fetch_limit
"""


def hybrid_search(
    connection_id: str,
    space_id: str,
    query: str,
    top_k: int = 10,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Run hybrid search and return ranked results.

    Args:
        connection_id: Knowledge connection slug/id.
        space_id: Target space UUID.
        query: Natural language query.
        top_k: Number of results to return.
        filters: Optional dict with keys:
            unit_id (str): restrict to this unit
            chunk_type (str): restrict to chunk type
            content_type (str): restrict to document content_type

    Returns:
        List of dicts with keys:
            chunk_id, document_id, doc_title, content_type,
            chunk_idx, chunk_type, content, chunk_metadata,
            rrf_score, final_score
    """
    if filters is None:
        filters = {}

    query_vector = _get_query_vector(connection_id, query)
    vector_str = _vector_str(query_vector)

    # Fetch 3x top_k to have buffer after re-scoring/filtering
    fetch_limit = max(top_k * 3, 30)

    params: Dict[str, Any] = {
        "space_id": space_id,
        "query_vec": vector_str,
        "query_text": query,
        "rrf_k": _RRF_K,
        "fetch_limit": fetch_limit,
    }

    engine = _get_engine(connection_id)
    with engine.connect() as pg:
        rows = pg.execute(_sql(_HYBRID_SQL), params).fetchall()

    results: List[Dict[str, Any]] = []
    for row in rows:
        r = dict(row._mapping)
        content_type = r.get("content_type") or "article"
        space_boost = float(r.get("space_boost") or 1.0)
        rrf_score = float(r.get("rrf_score") or 0.0)

        # Apply DEFAULT_BOOSTS only when space had no override (space_boost == 1.0)
        # We detect "no override" by checking if the space_boost came from COALESCE default.
        # Since SQL returns 1.0 for both "no JSONB key" and explicit 1.0, we always
        # apply DEFAULT_BOOSTS on top of the space_boost.
        default_boost = DEFAULT_BOOSTS.get(content_type, 1.0)
        final_score = rrf_score * space_boost * default_boost

        # Apply optional filters
        if filters.get("unit_id") and r.get("unit_id") != filters["unit_id"]:
            continue
        if filters.get("chunk_type") and r.get("chunk_type") != filters["chunk_type"]:
            continue
        if filters.get("content_type") and content_type != filters["content_type"]:
            continue

        chunk_metadata = r.get("chunk_metadata")
        if isinstance(chunk_metadata, str):
            try:
                chunk_metadata = json.loads(chunk_metadata)
            except (ValueError, TypeError):
                chunk_metadata = {}

        results.append(
            {
                "chunk_id": str(r["chunk_id"]),
                "document_id": str(r["document_id"]),
                "doc_title": r.get("doc_title"),
                "content_type": content_type,
                "unit_id": str(r["unit_id"]) if r.get("unit_id") else None,
                "chunk_idx": r.get("chunk_idx"),
                "chunk_type": r.get("chunk_type"),
                "content": r.get("content"),
                "chunk_metadata": chunk_metadata or {},
                "rrf_score": round(rrf_score, 6),
                "final_score": round(final_score, 6),
            }
        )

    # Re-sort after Python boost application, slice to top_k
    results.sort(key=lambda x: x["final_score"], reverse=True)
    return results[:top_k]
