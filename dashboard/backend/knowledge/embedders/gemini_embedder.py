"""Google Gemini embedder.

Supports two models:
  * ``gemini-embedding-001`` — text-only, 2048-token input, accepts ``task_type``.
  * ``gemini-embedding-2-preview`` — multimodal preview, 8192-token input,
    does NOT accept ``task_type`` (per Google docs, tasks are instructed
    directly in the prompt for this model).

Uses Matryoshka Representation Learning (MRL): output dim can be 768, 1536,
or 3072. Default is 768 to align storage/index cost with the local MPNet
embedder. For dims < 3072, vectors are L2-normalized on the client side
(required per Google's embedding docs to preserve relative distances).

Requires: ``pip install google-genai>=0.3``
Requires: ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY`` environment variable.

Lazy import: ``google.genai`` is NOT imported at module load time so that
deployments that never activate the Gemini provider do not pay the import
cost and do not fail when the package is absent.

Dimension mismatch note: if a Knowledge connection was configured with the
``local`` embedder (768-dim), switching to Gemini with ``dim=1536`` or
``dim=3072`` will break vector search. The configure wizard warns about
this before persisting. This embedder does not enforce the check —
the constraint lives in the API layer (``routes/knowledge.py``).
"""

from __future__ import annotations

import math
import os
from typing import Any, List, Optional

from knowledge.embedders.base import BaseEmbedder

try:
    from google import genai  # type: ignore[import]
    from google.genai import types as genai_types  # type: ignore[import]
except ImportError:  # pragma: no cover - exercised only when SDK is missing
    genai = None  # type: ignore[assignment,misc]
    genai_types = None  # type: ignore[assignment,misc]


_DEFAULT_MODEL = "gemini-embedding-001"
_DEFAULT_DIM = 768

# Allowed dimensions per Gemini MRL guidance.
# Full range supported by the API is 128-3072; we expose only the three
# values Google explicitly recommends ("for best quality and efficiency").
_ALLOWED_DIMS = {768, 1536, 3072}

# Model-specific native default dim (what the API returns when
# output_dimensionality is omitted). Kept separate from _DEFAULT_DIM so the
# evo-nexus default stays at 768 across both models for storage alignment.
_MODEL_NATIVE_DIMS = {
    "gemini-embedding-001": 768,
    "gemini-embedding-2-preview": 768,
}

# Models that accept the ``task_type`` parameter. The 2-preview model does
# not — task optimisation is expressed inline in the prompt instead.
_MODELS_WITH_TASK_TYPE = {"gemini-embedding-001"}

# Conservative client-side batch size. gemini-embedding-001 has a 2048-token
# input limit; at ~50 tokens/chunk that's ~40 chunks/call. We stay at 20 for
# safety across chunking strategies and to keep individual error blasts small.
_BATCH_SIZE = 20


def _clean(raw: Optional[str]) -> str:
    """Strip surrounding whitespace/quotes that naive .env parsers may leak."""
    if not raw:
        return ""
    return raw.strip().strip('"').strip("'")


def _resolve_api_key() -> Optional[str]:
    """Return the first available Gemini key from the env.

    Accepts either ``GEMINI_API_KEY`` (preferred — explicit) or
    ``GOOGLE_API_KEY`` (the google-genai SDK default). Returning the
    raw value here lets the SDK handle the rest.
    """
    return (
        _clean(os.environ.get("GEMINI_API_KEY"))
        or _clean(os.environ.get("GOOGLE_API_KEY"))
        or None
    )


class GeminiEmbedder(BaseEmbedder):
    """Google Gemini embedder.

    Configuration (all optional, with sensible defaults):
        KNOWLEDGE_GEMINI_MODEL   — model id (default: gemini-embedding-001)
        KNOWLEDGE_GEMINI_DIM     — output dim: 768, 1536, or 3072 (default: 768)
        GEMINI_API_KEY           — Google AI Studio key
        GOOGLE_API_KEY           — fallback key name (SDK default)
    """

    def __init__(self) -> None:
        self._model = _clean(os.environ.get("KNOWLEDGE_GEMINI_MODEL")) or _DEFAULT_MODEL

        raw_dim = _clean(os.environ.get("KNOWLEDGE_GEMINI_DIM"))
        if raw_dim:
            try:
                dim = int(raw_dim)
            except ValueError:
                dim = _MODEL_NATIVE_DIMS.get(self._model, _DEFAULT_DIM)
        else:
            dim = _MODEL_NATIVE_DIMS.get(self._model, _DEFAULT_DIM)

        # Silently coerce invalid dims to the default — we must not raise
        # in __init__ because the settings UI instantiates embedders just to
        # read .dim for display purposes.
        if dim not in _ALLOWED_DIMS:
            dim = _DEFAULT_DIM
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(
        self,
        texts: List[str],
        task_type: Optional[str] = None,
    ) -> List[List[float]]:
        """Embed *texts* via the Gemini embeddings API.

        Args:
            texts: list of non-empty strings.
            task_type: one of Gemini's task hints
                (RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY, SEMANTIC_SIMILARITY,
                CLASSIFICATION, CLUSTERING, QUESTION_ANSWERING,
                FACT_VERIFICATION, CODE_RETRIEVAL_QUERY). Only honoured for
                models in ``_MODELS_WITH_TASK_TYPE``; passed to the API
                otherwise ignored. ``None`` means "do not pass".

        Returns:
            List of float vectors matching ``self.dim``.

        Raises:
            ValueError: if ``texts`` is empty.
            RuntimeError: if ``google-genai`` is not installed, or no API
                key is configured.
        """
        if not texts:
            raise ValueError("embed() called with empty texts list.")

        if genai is None:
            raise RuntimeError(
                "google-genai package is not installed. "
                "Run: pip install google-genai>=0.3  (or: uv add google-genai)"
            )

        api_key = _resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. "
                "Add it to .env: GEMINI_API_KEY=AIzaSy..."
            )

        client = genai.Client(api_key=api_key)
        config = self._build_config(task_type)

        all_vectors: List[List[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            response = client.models.embed_content(
                model=self._model,
                contents=batch,
                config=config,
            )
            for emb in response.embeddings:
                vec = list(emb.values)
                if self._dim < 3072:
                    vec = _l2_normalize(vec)
                all_vectors.append(vec)

        return all_vectors

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_config(self, task_type: Optional[str]) -> Optional[Any]:
        """Assemble ``EmbedContentConfig`` only when we need to override defaults."""
        kwargs: dict = {}

        # Override native dim only when user picked something different.
        native = _MODEL_NATIVE_DIMS.get(self._model, _DEFAULT_DIM)
        if self._dim != native:
            kwargs["output_dimensionality"] = self._dim

        # task_type is only valid on the 001 model.
        if task_type and self._model in _MODELS_WITH_TASK_TYPE:
            kwargs["task_type"] = task_type

        if not kwargs:
            return None
        return genai_types.EmbedContentConfig(**kwargs)


def _l2_normalize(vec: List[float]) -> List[float]:
    """Unit-length-normalize *vec* in L2 norm.

    Gemini's embedding docs require this for dims < 3072 because the
    Matryoshka-truncated segment is not guaranteed to be unit-norm, and
    cosine-similarity retrieval against an HNSW index assumes normalized
    vectors.
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]
