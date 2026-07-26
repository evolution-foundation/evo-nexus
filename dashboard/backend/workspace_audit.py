"""Workspace mutations audit — dialect-bifurcated helper.

PostgreSQL mode: INSERT into workspace_mutations table.
SQLite mode:     append to ADWs/logs/workspace-mutations.jsonl (legacy behaviour).

Fail-safe: errors never propagate to callers.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from config_store import get_dialect
from db.engine import get_engine

log = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
AUDIT_LOG_FILE = WORKSPACE_ROOT / "ADWs" / "logs" / "workspace-mutations.jsonl"


def audit_mutation(
    *,
    user_id: Optional[int],
    role: Optional[str],
    op: str,
    path: str,
    result: str,
    extra: Optional[dict] = None,
) -> None:
    """Record a workspace mutation event.

    Parameters
    ----------
    user_id:  authenticated user PK (None when anonymous)
    role:     user role string (None when anonymous)
    op:       operation name — upload | delete | rename | move | chmod | …
    path:     workspace-relative path affected
    result:   outcome — ok | error | denied
    extra:    arbitrary additional context (serialised as JSON)
    """
    try:
        if get_dialect() == "postgresql":
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO workspace_mutations
                            (ts, user_id, role, op, path, result, extra)
                        VALUES (NOW(), :user_id, :role, :op, :path, :result, :extra)
                        """
                    ),
                    {
                        "user_id": user_id,
                        "role": role,
                        "op": op,
                        "path": path,
                        "result": result,
                        "extra": json.dumps(extra) if extra else None,
                    },
                )
        else:
            AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "user_id": user_id,
                "role": role,
                "op": op,
                "path": path,
                "result": result,
                "extra": extra,
            }
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("[workspace_audit] failed to record mutation: %s", exc)
