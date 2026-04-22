"""Knowledge Base package — pgvector-backed document storage and retrieval.

Fail-fast: `assert_master_key()` must be called before any Knowledge API endpoint
is used. It verifies KNOWLEDGE_MASTER_KEY is set and is a valid Fernet key.
It is NOT called at module import time to avoid breaking unrelated Flask startup
(e.g. MemPalace) when the user has not yet run `evonexus init-key`.
"""

import os
import sys


def assert_master_key() -> None:
    """Verify that KNOWLEDGE_MASTER_KEY is set and is a valid Fernet key.

    Exits the process with a clear error message if the key is missing or
    invalid. Call this once at the start of every Knowledge blueprint handler
    (via a before_request hook registered on the blueprint).
    """
    key = os.environ.get("KNOWLEDGE_MASTER_KEY", "").strip()
    if not key:
        sys.stderr.write(
            "ERROR: KNOWLEDGE_MASTER_KEY is not set.\n"
            "Run `evonexus init-key` (or `make init-key`) before using Knowledge.\n"
            "Docs: workspace/development/features/pgvector-knowledge/\n"
        )
        sys.exit(1)
    try:
        from cryptography.fernet import Fernet
        Fernet(key.encode())
    except Exception as exc:
        sys.stderr.write(f"ERROR: KNOWLEDGE_MASTER_KEY is invalid: {exc}\n")
        sys.exit(1)
