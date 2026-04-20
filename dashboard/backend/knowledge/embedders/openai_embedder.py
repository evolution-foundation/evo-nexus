"""OpenAI embedder using text-embedding-3-small (1536-dim).

Requires: pip install openai>=1.0
Requires: OPENAI_API_KEY environment variable.

Lazy import: openai is NOT imported at module load time.

Note on dimension mismatch: if a Knowledge connection was configured with
local embedder (768-dim), switching to OpenAI (1536-dim) will break vector
search. The configure wizard warns about this before persisting to
knowledge_config (AC-02). This embedder does not enforce the check — the
constraint lives in the API layer.
"""

from __future__ import annotations

import os
from typing import List

from knowledge.embedders.base import BaseEmbedder

try:
    from openai import OpenAI  # type: ignore[import]
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

_DEFAULT_MODEL = "text-embedding-3-small"
_OPENAI_DIM = 1536


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI text-embedding-3-small embedder (1536-dim).

    Requires: OPENAI_API_KEY env var.
    Override model via KNOWLEDGE_OPENAI_MODEL env var.
    """

    def __init__(self) -> None:
        self._model = os.environ.get("KNOWLEDGE_OPENAI_MODEL", _DEFAULT_MODEL)

    @property
    def dim(self) -> int:
        return _OPENAI_DIM

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed *texts* via OpenAI Embeddings API.

        Args:
            texts: list of non-empty strings

        Returns:
            List of 1536-dim float vectors.

        Raises:
            RuntimeError: if openai package is not installed.
            RuntimeError: if OPENAI_API_KEY is not set.
            ValueError: if texts is empty.
        """
        if not texts:
            raise ValueError("embed() called with empty texts list.")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. "
                "Add it to .env: OPENAI_API_KEY=sk-..."
            )

        if OpenAI is None:
            raise RuntimeError(
                "openai package is not installed. "
                "Run: pip install openai>=1.0  (or: uv add openai)"
            )

        client = OpenAI(api_key=api_key)
        response = client.embeddings.create(model=self._model, input=texts)
        # OpenAI returns embeddings sorted by index
        sorted_data = sorted(response.data, key=lambda d: d.index)
        return [item.embedding for item in sorted_data]
