"""Local sentence-transformers embedder (768-dim, pt-BR capable).

Model: sentence-transformers/paraphrase-multilingual-mpnet-base-v2
  - 768 dimensions
  - Supports Portuguese and 50+ other languages
  - ~420 MB download on first use (cached in ~/.cache/huggingface/)

Lazy import: SentenceTransformer is NOT imported at module load time.
"""

from __future__ import annotations

import threading
from typing import List, Optional, Any

from knowledge.embedders.base import BaseEmbedder

_BATCH_SIZE = 32
_DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"

# Module-level singleton (lazy init, thread-safe)
_model_lock = threading.Lock()
_model: Optional[Any] = None
_model_name: Optional[str] = None


def _get_model(model_name: str) -> Any:
    """Return cached SentenceTransformer model, loading it if necessary."""
    global _model, _model_name
    with _model_lock:
        if _model is not None and _model_name == model_name:
            return _model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers  (or: uv add sentence-transformers)"
            ) from exc
        _model = SentenceTransformer(model_name)
        _model_name = model_name
        return _model


class LocalEmbedder(BaseEmbedder):
    """Sentence-transformers embedder for local, offline use.

    Requires: pip install sentence-transformers
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name

    @property
    def dim(self) -> int:
        return 768

    def embed(
        self,
        texts: List[str],
        task_type: Optional[str] = None,
    ) -> List[List[float]]:
        """Embed *texts* in batches of 32.

        Args:
            texts: list of non-empty strings
            task_type: ignored — MPNet has no task-conditioning. Accepted
                for API parity with providers that do (e.g. Gemini).

        Returns:
            List of 768-dim float vectors.

        Raises:
            RuntimeError: if sentence-transformers is not installed.
            ValueError: if texts is empty.
        """
        del task_type  # unused
        if not texts:
            raise ValueError("embed() called with empty texts list.")

        model = _get_model(self._model_name)

        all_vectors: List[List[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            embeddings = model.encode(batch, convert_to_numpy=True)
            all_vectors.extend(emb.tolist() for emb in embeddings)

        return all_vectors
