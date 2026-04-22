"""Base embedder ABC + registry for the Knowledge Base.

Registry logic:
    get_embedder("local")   → LocalEmbedder (sentence-transformers, 768 dim)
    get_embedder("openai")  → OpenAIEmbedder (text-embedding-3-small, 1536 dim)
    get_embedder("gemini")  → GeminiEmbedder (gemini-embedding-001, MRL 768/1536/3072)
    get_embedder()          → reads KNOWLEDGE_EMBEDDER_PROVIDER env var (default "local")
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import List, Literal, Optional


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
    def embed(
        self,
        texts: List[str],
        task_type: Optional[str] = None,
    ) -> List[List[float]]:
        """Embed a list of texts.

        Args:
            texts: list of strings to embed (non-empty, each non-empty)
            task_type: optional task hint used by providers that support it
                (e.g. Gemini's RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY,
                SEMANTIC_SIMILARITY). Providers that do not support task
                hints (local, OpenAI) ignore this parameter silently.

        Returns:
            List of float vectors, one per input text. Each vector has ``dim`` elements.
        """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def get_embedder(
    provider: Literal["local", "openai", "gemini", "auto"] = "auto",
) -> BaseEmbedder:
    """Return an instantiated embedder for *provider*.

    provider:
        "auto"   — reads KNOWLEDGE_EMBEDDER_PROVIDER env var (default "local")
        "local"  — LocalEmbedder (sentence-transformers)
        "openai" — OpenAIEmbedder (requires OPENAI_API_KEY)
        "gemini" — GeminiEmbedder (requires GEMINI_API_KEY or GOOGLE_API_KEY)

    Raises:
        ValueError: unknown provider name
    """
    if provider == "auto":
        raw = os.environ.get("KNOWLEDGE_EMBEDDER_PROVIDER", "local")
        # Strip surrounding quotes that may leak from naive .env parsers
        raw = raw.strip().strip('"').strip("'").lower()
        provider = raw  # type: ignore[assignment]

    if provider == "local":
        from knowledge.embedders.local_embedder import LocalEmbedder
        return LocalEmbedder()
    if provider == "openai":
        from knowledge.embedders.openai_embedder import OpenAIEmbedder
        return OpenAIEmbedder()
    if provider == "gemini":
        from knowledge.embedders.gemini_embedder import GeminiEmbedder
        return GeminiEmbedder()

    raise ValueError(
        f"Unknown embedder provider '{provider}'. "
        "Valid options: 'local', 'openai', 'gemini'. "
        "Set KNOWLEDGE_EMBEDDER_PROVIDER in .env to change the default."
    )
