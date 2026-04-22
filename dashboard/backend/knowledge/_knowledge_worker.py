"""Knowledge Base ingestion worker — subprocess entry point.

Spawned by the ingestion API endpoint. Reads a JSON payload from stdin:
    {
        "connection_id": "...",
        "space_id": "...",
        "unit_id": "...",      (optional)
        "document_id": "...",  (optional — for idempotent re-runs)
        "file_path": "...",
        "metadata": {...}      (optional)
    }

Writes progress to dashboard/data/knowledge/ingestion/<document_id>.json
so the frontend can render a progress bar.

Phases emitted in order:
    scanning → parsing → chunking → embedding → storing → classifying → done

On crash: writes {phase: "error", error: <traceback>} to the status file.

Not meant to be imported — this is a standalone worker script.
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Strip the script's directory from sys.path so `knowledge.*` imports resolve
# to the installed package rather than sibling files.
_script_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_script_dir)
if _script_dir in sys.path:
    sys.path.remove(_script_dir)
# Ensure the backend dir is on sys.path so `from knowledge.xxx import` works
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


_PHASE_ORDER = [
    "scanning",
    "parsing",
    "chunking",
    "embedding",
    "storing",
    "classifying",
    "done",
]

_PHASE_TIMEOUTS = {
    "parsing": int(os.environ.get("KNOWLEDGE_PARSE_TIMEOUT", "600")),
    "embedding": int(os.environ.get("KNOWLEDGE_EMBED_TIMEOUT", "120")),
    "storing": int(os.environ.get("KNOWLEDGE_STORE_TIMEOUT", "60")),
}


def _status_file(base_dir: Path, document_id: str) -> Path:
    return base_dir / f"{document_id}.json"


def main() -> None:
    raw = sys.stdin.read()
    payload = json.loads(raw)

    connection_id: str = payload["connection_id"]
    space_id: str = payload["space_id"]
    unit_id: str | None = payload.get("unit_id")
    document_id: str | None = payload.get("document_id")
    file_path: str = payload["file_path"]
    metadata: dict = payload.get("metadata") or {}

    # Determine status file location
    base_dir = (
        Path(__file__).parent.parent.parent  # dashboard/
        / "data"
        / "knowledge"
        / "ingestion"
    )
    base_dir.mkdir(parents=True, exist_ok=True)

    # We need a document_id to name the status file; generate early if not given
    if not document_id:
        import uuid
        document_id = str(uuid.uuid4())

    status_file = _status_file(base_dir, document_id)
    started_at = datetime.now(timezone.utc).isoformat()
    started_monotonic = time.monotonic()
    pid = os.getpid()

    def write_status(phase: str, error: str | None = None) -> None:
        elapsed = round(time.monotonic() - started_monotonic, 1)
        step_idx = _PHASE_ORDER.index(phase) if phase in _PHASE_ORDER else 0
        data: dict = {
            "pid": pid,
            "document_id": document_id,
            "started_at": started_at,
            "phase": phase,
            "current_step": step_idx + 1,
            "total_steps": len(_PHASE_ORDER),
            "elapsed_seconds": elapsed,
            "bytes_processed": 0,
            "eta_seconds": None,
        }
        if error is not None:
            data["error"] = error
        try:
            status_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    write_status("scanning")

    # ------------------------------------------------------------------
    # KNOWLEDGE_MASTER_KEY must be available
    # connection_pool._resolve_sqlite_db_path() handles DB location via
    # a __file__-relative fallback — don't set a relative env override here.
    # ------------------------------------------------------------------
    from knowledge.ingestion import ingest_document

    try:
        doc_id = ingest_document(
            connection_id=connection_id,
            space_id=space_id,
            file_path=file_path,
            metadata=metadata,
            unit_id=unit_id,
            document_id=document_id,
            progress_callback=write_status,
        )
        write_status("done")
        print(json.dumps({"ok": True, "document_id": doc_id}), flush=True)
    except Exception:
        tb = traceback.format_exc()
        write_status("error", error=tb)
        print(json.dumps({"ok": False, "error": tb}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Last-resort crash log
        try:
            err_path = Path.home() / ".evonexus_knowledge_worker_error.log"
            err_path.write_text(
                f"[{datetime.now(timezone.utc).isoformat()}]\n" + traceback.format_exc(),
                encoding="utf-8",
            )
        except Exception:
            pass
        raise
