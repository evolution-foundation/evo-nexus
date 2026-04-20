"""Asynchronous document classification worker (ADR-008).

Runs as a background daemon thread. Polls knowledge_classify_queue on each
Knowledge connection and classifies documents using a cheap LLM (Claude Haiku
via ANTHROPIC_API_KEY, or Gemini Flash via GEMINI_API_KEY).

Classification populates:
    knowledge_documents.content_type, difficulty_level, topics[]

Checkout protocol (mirrors ticket_janitor pattern, Postgres edition):
    SELECT ... FOR UPDATE SKIP LOCKED → lock → classify → DELETE from queue
    On error: release lock, increment attempts, mark dead_letter after 3 tries

Janitor: every 5 min, release stale locks
    (locked_at + lock_timeout_seconds < now())

If no LLM API key is set: worker logs a one-time warning and exits gracefully.
Documents remain without classification — acceptable for v1.

Integration:
    app.py calls start_classify_worker() inside an app context after other
    janitors start. Pass the app object so the thread can read config.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import anthropic  # type: ignore[import]
except ImportError:
    anthropic = None  # type: ignore[assignment]

log = logging.getLogger("classify_worker")

_POLL_INTERVAL = int(os.environ.get("KNOWLEDGE_CLASSIFY_POLL_INTERVAL", "10"))
_JANITOR_INTERVAL = int(os.environ.get("KNOWLEDGE_CLASSIFY_JANITOR_INTERVAL", "300"))
_LOCK_TIMEOUT_SECONDS = 600
_MAX_ATTEMPTS = 3

_worker_started = False
_worker_lock = threading.Lock()

# Suppress duplicate "no LLM key" log
_warned_no_llm = False


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------

def _classify_document(content_sample: str) -> Optional[Dict[str, Any]]:
    """Call a cheap LLM to classify a document chunk sample.

    Returns dict with content_type, difficulty_level, topics[],
    or None if no LLM is configured.
    """
    global _warned_no_llm

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")

    if not anthropic_key and not gemini_key:
        if not _warned_no_llm:
            log.warning(
                "No LLM API key found (ANTHROPIC_API_KEY or GEMINI_API_KEY). "
                "Document classification is disabled. "
                "Set one of these keys to enable automatic content classification."
            )
            _warned_no_llm = True
        return None

    prompt = (
        "You are a document classifier. Given a sample of document content, "
        "return a JSON object with exactly these fields:\n"
        '  "content_type": one of ["tutorial", "reference", "case_study", "whitepaper", '
        '"transcript", "article", "book_chapter", "other"]\n'
        '  "difficulty_level": one of ["beginner", "intermediate", "advanced"]\n'
        '  "topics": list of 1-5 short topic strings (e.g. ["machine learning", "python"])\n\n'
        "Respond with ONLY the JSON object, no markdown fences.\n\n"
        f"Document sample:\n{content_sample[:3000]}"
    )

    if anthropic_key:
        try:
            if anthropic is None:
                raise ImportError("anthropic not installed")
            client = anthropic.Anthropic(api_key=anthropic_key)
            message = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            return json.loads(raw)
        except Exception as exc:
            log.warning(f"Anthropic classify error: {exc}")
            return None

    if gemini_key:
        try:
            import google.generativeai as genai  # type: ignore[import]
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.strip("`").lstrip("json").strip()
            return json.loads(raw)
        except Exception as exc:
            log.warning(f"Gemini classify error: {exc}")
            return None

    return None


# ---------------------------------------------------------------------------
# Queue operations (Postgres)
# ---------------------------------------------------------------------------

def _get_connections(sqlite_db_path: str) -> List[Dict[str, Any]]:
    """Return all 'ready' Knowledge connections from SQLite."""
    import sqlite3
    conn = sqlite3.connect(sqlite_db_path)
    try:
        cur = conn.execute(
            "SELECT id, connection_string_encrypted FROM knowledge_connections WHERE status = 'ready'"
        )
        rows = cur.fetchall()
        return [{"id": row[0], "cs_enc": row[1]} for row in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _process_queue_for_connection(connection_id: str, dsn: str, worker_id: str) -> int:
    """Process one pending classify_queue item for this connection.

    Returns the number of items processed (0 or 1).
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(
        dsn,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "application_name": "evonexus-classify-worker"},
    )

    processed = 0
    try:
        with engine.begin() as pg:
            # Atomic checkout: FOR UPDATE SKIP LOCKED
            row = pg.execute(
                text(
                    """
                    SELECT q.document_id, d.title, LEFT(c.content, 3000) AS content_sample
                    FROM knowledge_classify_queue q
                    JOIN knowledge_documents d ON d.id = q.document_id
                    LEFT JOIN LATERAL (
                        SELECT content FROM knowledge_chunks
                        WHERE document_id = q.document_id
                        ORDER BY chunk_idx ASC
                        LIMIT 1
                    ) c ON TRUE
                    WHERE q.locked_at IS NULL
                      AND q.attempts < :max_attempts
                    ORDER BY q.enqueued_at ASC
                    LIMIT 1
                    FOR UPDATE OF q SKIP LOCKED
                    """
                ),
                {"max_attempts": _MAX_ATTEMPTS},
            ).fetchone()

            if row is None:
                return 0

            doc_id = str(row[0])
            content_sample = row[2] or ""

            # Lock the row
            pg.execute(
                text(
                    """
                    UPDATE knowledge_classify_queue
                    SET locked_at = now(),
                        locked_by = :worker_id,
                        lock_timeout_seconds = :timeout
                    WHERE document_id = :doc_id
                    """
                ),
                {"worker_id": worker_id, "timeout": _LOCK_TIMEOUT_SECONDS, "doc_id": doc_id},
            )

        # Classify outside the lock transaction (can take a few seconds)
        classification = _classify_document(content_sample)

        with engine.begin() as pg:
            if classification:
                topics = classification.get("topics") or []
                pg.execute(
                    text(
                        """
                        UPDATE knowledge_documents
                        SET content_type = :ct,
                            difficulty_level = :dl,
                            topics = :topics
                        WHERE id = :doc_id
                        """
                    ),
                    {
                        "ct": classification.get("content_type"),
                        "dl": classification.get("difficulty_level"),
                        "topics": topics,
                        "doc_id": doc_id,
                    },
                )
                # Remove from queue on success
                pg.execute(
                    text("DELETE FROM knowledge_classify_queue WHERE document_id = :doc_id"),
                    {"doc_id": doc_id},
                )
                processed = 1
            else:
                # No classification available — release lock, increment attempts
                pg.execute(
                    text(
                        """
                        UPDATE knowledge_classify_queue
                        SET locked_at = NULL,
                            locked_by = NULL,
                            attempts = attempts + 1
                        WHERE document_id = :doc_id
                        """
                    ),
                    {"doc_id": doc_id},
                )

    except Exception as exc:
        log.warning(f"classify_worker: error processing connection {connection_id}: {exc}")
    finally:
        try:
            engine.dispose()
        except Exception:
            pass

    return processed


def _release_stale_locks(connection_id: str, dsn: str) -> None:
    """Release classify_queue locks that have exceeded lock_timeout_seconds."""
    from sqlalchemy import create_engine, text

    engine = create_engine(
        dsn,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "application_name": "evonexus-classify-janitor"},
    )
    try:
        with engine.begin() as pg:
            result = pg.execute(
                text(
                    """
                    UPDATE knowledge_classify_queue
                    SET locked_at = NULL, locked_by = NULL
                    WHERE locked_at IS NOT NULL
                      AND locked_at + (lock_timeout_seconds * interval '1 second') < now()
                    RETURNING document_id
                    """
                )
            )
            n = result.rowcount
            if n > 0:
                log.info(f"classify_worker janitor: released {n} stale lock(s) for connection {connection_id}")
    except Exception as exc:
        log.debug(f"classify_worker janitor error for connection {connection_id}: {exc}")
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _worker_loop(sqlite_db_path: str) -> None:
    """Background classify worker loop."""
    import os as _os
    worker_id = f"worker-pid-{os.getpid()}"
    last_janitor_run = 0.0

    log.info(f"classify_worker started (poll_interval={_POLL_INTERVAL}s)")

    while True:
        time.sleep(_POLL_INTERVAL)

        now = time.monotonic()
        run_janitor = (now - last_janitor_run) >= _JANITOR_INTERVAL

        try:
            from knowledge.crypto import decrypt_secret
            connections = _get_connections(sqlite_db_path)

            for conn_info in connections:
                connection_id = conn_info["id"]
                cs_enc = conn_info["cs_enc"]
                if not cs_enc:
                    continue
                try:
                    dsn = decrypt_secret(bytes(cs_enc))
                except Exception:
                    continue

                if run_janitor:
                    _release_stale_locks(connection_id, dsn)

                _process_queue_for_connection(connection_id, dsn, worker_id)

        except Exception as exc:
            log.warning(f"classify_worker loop error: {exc}")

        if run_janitor:
            last_janitor_run = now


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_classify_worker(sqlite_db_path: str) -> None:
    """Start the classify worker background thread (idempotent).

    Args:
        sqlite_db_path: absolute path to the EvoNexus SQLite database.
    """
    global _worker_started

    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    t = threading.Thread(
        target=_worker_loop,
        args=(sqlite_db_path,),
        daemon=True,
        name="knowledge-classify-worker",
    )
    t.start()
    log.info(f"classify_worker thread started (poll={_POLL_INTERVAL}s, janitor={_JANITOR_INTERVAL}s)")
