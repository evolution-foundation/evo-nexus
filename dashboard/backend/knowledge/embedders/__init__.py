"""Embedder registry for the Knowledge Base.

Usage:
    from knowledge.embedders import get_embedder
    embedder = get_embedder()            # uses KNOWLEDGE_EMBEDDER_PROVIDER env var
    vectors = embedder.embed(["text1", "text2"])  # list[list[float]]
"""

from knowledge.embedders.base import get_embedder, BaseEmbedder

__all__ = ["get_embedder", "BaseEmbedder"]
