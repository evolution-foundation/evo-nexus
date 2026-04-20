"""Base embedder ABC + registry for the Knowledge Base.

Registry logic:
    get_embedder("local")   → LocalEmbedder (sentence-transformers, 768 dim)
    get_embedder("openai")  → OpenAIEmbedder (text-embedding-3-small, 1536 dim)
    get_embedder()          → reads KNOWLEDGE_EMBEDDER_PROVIDER env var (default "local")
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import List, Literal


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

class BaseEmbedder(ABC):
    """Abstract base for all embedding providers."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Embedding dimension for this provider."""

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts.

        Args:
            texts: list of strings to embed (non-empty, each non-empty)

        Returns:
            List of float vectors, one per input text. Each vector has ``dim`` elements.
        """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def get_embedder(
    provider: Literal["local", "openai", "auto"] = "auto",
) -> BaseEmbedder:
    """Return an instantiated embedder for *provider*.

    provider:
        "auto"   — reads KNOWLEDGE_EMBEDDER_PROVIDER env var (default "local")
        "local"  — LocalEmbedder (sentence-transformers)
        "openai" — OpenAIEmbedder (requires OPENAI_API_KEY)

    Raises:
        ValueError: unknown provider name
    """
    if provider == "auto":
        provider = os.environ.get("KNOWLEDGE_EMBEDDER_PROVIDER", "local")  # type: ignore[assignment]

    if provider == "local":
        from knowledge.embedders.local_embedder import LocalEmbedder
        return LocalEmbedder()
    if provider == "openai":
        from knowledge.embedders.openai_embedder import OpenAIEmbedder
        return OpenAIEmbedder()

    raise ValueError(
        f"Unknown embedder provider '{provider}'. "
        "Valid options: 'local', 'openai'. "
        "Set KNOWLEDGE_EMBEDDER_PROVIDER in .env to change the default."
    )
