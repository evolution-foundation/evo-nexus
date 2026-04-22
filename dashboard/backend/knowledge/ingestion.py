"""Document ingestion pipeline: parse → chunk → embed → store → enqueue classify.

ADR-007: all chunk INSERTs happen in a single Postgres transaction.
If any step fails, the transaction rolls back and the document status
is updated to 'error' with the error_message populated.

Public API:
    ingest_document(connection_id, space_id, file_path, metadata) -> str
        Returns the document_id on success.
        Raises on unrecoverable error (document marked 'error' in Postgres).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Module-level references — allow patch.object(ingestion, "get_parser", ...) in tests.
# These are reassigned lazily on first import to avoid heavy dependencies at Flask startup.
from knowledge.connection_pool import get_dsn, get_engine  # noqa: F401
from knowledge.parsers import get_parser  # noqa: F401
from knowledge.embedders import get_embedder  # noqa: F401
from knowledge.chunking import chunk_markdown  # noqa: F401


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ingest_document(
    connection_id: str,
    space_id: str,
    file_path: str | Path,
    metadata: Optional[Dict[str, Any]] = None,
    unit_id: Optional[str] = None,
    document_id: Optional[str] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> str:
    """Ingest a document into the Knowledge Base.

    Steps:
        1. Create/update row in knowledge_documents (status='processing')
        2. Parse via get_parser() → markdown
        3. Chunk via chunk_markdown()
        4. Embed via get_embedder() (batch)
        5. INSERT chunks in a single Postgres transaction (ADR-007)
        6. UPDATE document status='ready', indexed_at=now()
        7. Enqueue in knowledge_classify_queue (ADR-008)

    Args:
        connection_id: ID of the Knowledge connection (from knowledge_connections SQLite).
        space_id: UUID of the target knowledge_space.
        file_path: path to the document file on disk.
        metadata: optional dict with title, description, tags, owner_id, etc.
        unit_id: optional UUID of the knowledge_unit to link.
        document_id: if provided, re-use this ID (idempotent re-run).
        progress_callback: optional callable(phase: str) for progress reporting.

    Returns:
        The document_id (UUID string).

    Raises:
        Exception: if ingestion fails. The document row is updated to status='error'.
    """
    file_path = Path(file_path)
    if metadata is None:
        metadata = {}

    def _emit(phase: str) -> None:
        if progress_callback:
            progress_callback(phase)

    # Resolve DSN and engine
    dsn = get_dsn(connection_id)
    engine = get_engine(connection_id, dsn)

    # ------------------------------------------------------------------
    # Step 1: Create or update document row
    # ------------------------------------------------------------------
    _emit("scanning")
    doc_id = document_id or str(uuid.uuid4())
    doc_title = metadata.get("title") or file_path.stem
    doc_tags = metadata.get("tags") or []
    doc_owner = metadata.get("owner_id")
    doc_description = metadata.get("description")
    doc_size = file_path.stat().st_size if file_path.exists() else None
    mime_type = metadata.get("mime_type")
    now_iso = datetime.now(timezone.utc).isoformat()

    with engine.begin() as pg:
        _upsert_document(
            pg=pg,
            doc_id=doc_id,
            space_id=space_id,
            unit_id=unit_id,
            title=doc_title,
            description=doc_description,
            tags=doc_tags,
            owner_id=doc_owner,
            source_uri=str(file_path),
            mime_type=mime_type,
            size_bytes=doc_size,
            metadata=metadata,
            status="processing",
            error_message=None,
            created_at=now_iso,
        )

    # ------------------------------------------------------------------
    # Step 2: Parse
    # ------------------------------------------------------------------
    _emit("parsing")
    try:
        parser = get_parser(file_path=file_path)
        parse_result = parser.parse(file_path)
        markdown = parse_result["markdown"]
        parse_metadata = parse_result["metadata"]
    except Exception as exc:
        _mark_error(engine, doc_id, f"Parse error: {exc}")
        raise

    # ------------------------------------------------------------------
    # Step 3: Chunk
    # ------------------------------------------------------------------
    _emit("chunking")
    try:
        merged_metadata = {**parse_metadata, **metadata}
        chunks = chunk_markdown(markdown, merged_metadata)
    except Exception as exc:
        _mark_error(engine, doc_id, f"Chunk error: {exc}")
        raise

    if not chunks:
        _mark_error(engine, doc_id, "No chunks produced — document may be empty or unsupported.")
        raise ValueError(f"No chunks produced for {file_path.name}")

    # ------------------------------------------------------------------
    # Step 4: Embed
    # ------------------------------------------------------------------
    _emit("embedding")
    try:
        embedder = get_embedder()
        texts = [c["content"] for c in chunks]
        # task_type is honoured by providers that support it (e.g. Gemini's
        # gemini-embedding-001). Others (local MPNet, OpenAI) ignore it.
        vectors = embedder.embed(texts, task_type="RETRIEVAL_DOCUMENT")
    except Exception as exc:
        _mark_error(engine, doc_id, f"Embed error: {exc}")
        raise

    # ------------------------------------------------------------------
    # Step 5: INSERT chunks in a single transaction (ADR-007)
    # ------------------------------------------------------------------
    _emit("storing")
    try:
        with engine.begin() as pg:
            _insert_chunks_batch(
                pg=pg,
                chunks=chunks,
                vectors=vectors,
                doc_id=doc_id,
                space_id=space_id,
                unit_id=unit_id,
            )
            # Step 6 inline: update document status
            indexed_at = datetime.now(timezone.utc).isoformat()
            pg.execute(
                _sql("UPDATE knowledge_documents SET status = 'ready', indexed_at = :ts WHERE id = :id"),
                {"ts": indexed_at, "id": doc_id},
            )
    except Exception as exc:
        _mark_error(engine, doc_id, f"Store error: {exc}")
        raise

    # ------------------------------------------------------------------
    # Step 7: Enqueue classify (ADR-008 — best-effort, not in main txn)
    # ------------------------------------------------------------------
    _emit("classifying")
    try:
        with engine.begin() as pg:
            pg.execute(
                _sql(
                    """
                    INSERT INTO knowledge_classify_queue (document_id, attempts, enqueued_at)
                    VALUES (:doc_id, 0, now())
                    ON CONFLICT (document_id) DO NOTHING
                    """
                ),
                {"doc_id": doc_id},
            )
    except Exception:
        # Non-fatal — document is 'ready', classify will be retried by janitor
        pass

    _emit("done")
    return doc_id


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sql(statement: str):  # type: ignore[return]
    """Wrap raw SQL string in SQLAlchemy text()."""
    from sqlalchemy import text
    return text(statement)


def _upsert_document(
    pg,
    doc_id: str,
    space_id: str,
    unit_id: Optional[str],
    title: str,
    description: Optional[str],
    tags: List[str],
    owner_id: Optional[str],
    source_uri: str,
    mime_type: Optional[str],
    size_bytes: Optional[int],
    metadata: Dict[str, Any],
    status: str,
    error_message: Optional[str],
    created_at: str,
) -> None:
    """Insert or update a knowledge_documents row."""
    pg.execute(
        _sql(
            """
            INSERT INTO knowledge_documents
                (id, space_id, unit_id, title, description, tags, owner_id,
                 source_uri, mime_type, size_bytes, metadata, status,
                 error_message, created_at)
            VALUES
                (:id, :space_id, :unit_id, :title, :description, :tags, :owner_id,
                 :source_uri, :mime_type, :size_bytes, :metadata, :status,
                 :error_message, :created_at)
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                error_message = EXCLUDED.error_message
            """
        ),
        {
            "id": doc_id,
            "space_id": space_id,
            "unit_id": unit_id,
            "title": title,
            "description": description,
            "tags": tags if tags else None,
            "owner_id": owner_id,
            "source_uri": source_uri,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "metadata": json.dumps(metadata) if metadata else None,
            "status": status,
            "error_message": error_message,
            "created_at": created_at,
        },
    )


def _insert_chunks_batch(
    pg,
    chunks: List[Dict[str, Any]],
    vectors: List[List[float]],
    doc_id: str,
    space_id: str,
    unit_id: Optional[str],
) -> None:
    """Bulk insert chunks with embeddings into knowledge_chunks.

    pgvector expects the embedding as a string '[0.1,0.2,...]::vector'.
    """
    for chunk, vector in zip(chunks, vectors):
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"
        pg.execute(
            _sql(
                """
                INSERT INTO knowledge_chunks
                    (document_id, space_id, unit_id, chunk_idx, chunk_type,
                     content, metadata, embedding)
                VALUES
                    (:document_id, :space_id, :unit_id, :chunk_idx, :chunk_type,
                     :content, CAST(:metadata AS jsonb), CAST(:embedding AS vector))
                """
            ),
            {
                "document_id": doc_id,
                "space_id": space_id,
                "unit_id": unit_id,
                "chunk_idx": chunk["chunk_idx"],
                "chunk_type": chunk["chunk_type"],
                "content": chunk["content"],
                "metadata": json.dumps(chunk["metadata"]) if chunk.get("metadata") else None,
                "embedding": vector_str,
            },
        )


def _mark_error(engine, doc_id: str, error_message: str) -> None:
    """Update document status to 'error' (best-effort — swallows exceptions)."""
    try:
        with engine.begin() as pg:
            pg.execute(
                _sql(
                    "UPDATE knowledge_documents SET status = 'error', error_message = :msg WHERE id = :id"
                ),
                {"msg": error_message, "id": doc_id},
            )
    except Exception:
        pass
