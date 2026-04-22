"""Tests for GeminiEmbedder — all mocked, no live API calls.

Covers:
  * Env-driven configuration (model, dim, API key resolution)
  * Input validation (empty list, missing key, missing SDK)
  * L2 normalization for dim < 3072
  * Model-specific task_type handling (001 honours it, 2-preview skips)
  * Client-side batching
"""

import importlib
import math
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


def _backend():
    return os.path.join(os.path.dirname(__file__), "..", "..")


def _add_backend():
    b = _backend()
    if b not in sys.path:
        sys.path.insert(0, b)


def _fresh_module():
    """Reload the embedder module so each test starts with clean state."""
    _add_backend()
    if "knowledge.embedders.gemini_embedder" in sys.modules:
        del sys.modules["knowledge.embedders.gemini_embedder"]
    return importlib.import_module("knowledge.embedders.gemini_embedder")


def _mock_embeddings_response(n: int, dim: int = 768):
    """Return a MagicMock shaped like google.genai's EmbedContentResponse."""
    resp = MagicMock()
    resp.embeddings = []
    for i in range(n):
        emb = MagicMock()
        # Distinguish each vector by its first element so we can assert order.
        emb.values = [float(i + 1)] + [0.1] * (dim - 1)
        resp.embeddings.append(emb)
    return resp


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestGeminiEmbedderConfig:
    def test_default_dim_is_768(self, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_GEMINI_DIM", raising=False)
        monkeypatch.delenv("KNOWLEDGE_GEMINI_MODEL", raising=False)
        mod = _fresh_module()
        e = mod.GeminiEmbedder()
        assert e.dim == 768
        assert e._model == "gemini-embedding-001"

    def test_custom_dim_1536(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "1536")
        mod = _fresh_module()
        assert mod.GeminiEmbedder().dim == 1536

    def test_custom_dim_3072(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "3072")
        mod = _fresh_module()
        assert mod.GeminiEmbedder().dim == 3072

    def test_invalid_dim_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "512")
        mod = _fresh_module()
        # 512 is valid per API but not in our allowed set → coerce to 768.
        assert mod.GeminiEmbedder().dim == 768

    def test_non_numeric_dim_falls_back(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "not-a-number")
        mod = _fresh_module()
        assert mod.GeminiEmbedder().dim == 768

    def test_dim_with_quotes_is_cleaned(self, monkeypatch):
        # Naive .env parsers sometimes leave quotes.
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", '"1536"')
        mod = _fresh_module()
        assert mod.GeminiEmbedder().dim == 1536

    def test_custom_model(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_GEMINI_MODEL", "gemini-embedding-2-preview")
        mod = _fresh_module()
        e = mod.GeminiEmbedder()
        assert e._model == "gemini-embedding-2-preview"
        assert e.dim == 768


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

class TestApiKeyResolution:
    def test_prefers_gemini_api_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "a" * 33)
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSy" + "b" * 33)
        mod = _fresh_module()
        assert mod._resolve_api_key() == "AIzaSy" + "a" * 33

    def test_falls_back_to_google_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSy" + "c" * 33)
        mod = _fresh_module()
        assert mod._resolve_api_key() == "AIzaSy" + "c" * 33

    def test_returns_none_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        mod = _fresh_module()
        assert mod._resolve_api_key() is None


# ---------------------------------------------------------------------------
# embed() error paths
# ---------------------------------------------------------------------------

class TestEmbedErrors:
    def test_empty_texts_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "x" * 33)
        mod = _fresh_module()
        with pytest.raises(ValueError, match="empty texts"):
            mod.GeminiEmbedder().embed([])

    def test_no_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        mod = _fresh_module()
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            mod.GeminiEmbedder().embed(["hello"])

    def test_no_sdk_installed_raises(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "x" * 33)
        mod = _fresh_module()
        with patch.object(mod, "genai", None):
            with pytest.raises(RuntimeError, match="google-genai"):
                mod.GeminiEmbedder().embed(["hello"])


# ---------------------------------------------------------------------------
# embed() success paths (mocked SDK)
# ---------------------------------------------------------------------------

class TestEmbedSuccess:
    @pytest.fixture
    def setup_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "x" * 33)
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "768")
        monkeypatch.setenv("KNOWLEDGE_GEMINI_MODEL", "gemini-embedding-001")

    def test_returns_correct_shape(self, setup_env):
        mod = _fresh_module()
        with patch.object(mod, "genai") as mock_genai:
            client = MagicMock()
            client.models.embed_content.return_value = _mock_embeddings_response(3)
            mock_genai.Client.return_value = client
            result = mod.GeminiEmbedder().embed(["a", "b", "c"])
        assert len(result) == 3
        assert all(len(v) == 768 for v in result)

    def test_l2_normalizes_when_dim_lt_3072(self, setup_env, monkeypatch):
        """Per Gemini docs: normalize dimensions < 3072."""
        mod = _fresh_module()
        with patch.object(mod, "genai") as mock_genai:
            client = MagicMock()
            # Return unnormalized vec: [3, 4, 0, 0, ...] → norm = 5
            resp = MagicMock()
            emb = MagicMock()
            emb.values = [3.0, 4.0] + [0.0] * 766
            resp.embeddings = [emb]
            client.models.embed_content.return_value = resp
            mock_genai.Client.return_value = client
            result = mod.GeminiEmbedder().embed(["x"])
        assert abs(result[0][0] - 0.6) < 1e-6  # 3/5
        assert abs(result[0][1] - 0.8) < 1e-6  # 4/5
        # Every other component is 0/5 = 0
        assert all(v == 0.0 for v in result[0][2:])
        # Unit norm
        norm = math.sqrt(sum(x * x for x in result[0]))
        assert abs(norm - 1.0) < 1e-6

    def test_skips_normalization_at_3072(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "x" * 33)
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "3072")
        mod = _fresh_module()
        with patch.object(mod, "genai") as mock_genai:
            client = MagicMock()
            resp = MagicMock()
            emb = MagicMock()
            # Unnormalized vec — but since dim=3072 we pass it through as-is
            emb.values = [5.0] + [0.0] * 3071
            resp.embeddings = [emb]
            client.models.embed_content.return_value = resp
            mock_genai.Client.return_value = client
            result = mod.GeminiEmbedder().embed(["x"])
        assert result[0][0] == 5.0  # untouched

    def test_zero_vector_does_not_crash_normalization(self, setup_env):
        mod = _fresh_module()
        with patch.object(mod, "genai") as mock_genai:
            client = MagicMock()
            resp = MagicMock()
            emb = MagicMock()
            emb.values = [0.0] * 768
            resp.embeddings = [emb]
            client.models.embed_content.return_value = resp
            mock_genai.Client.return_value = client
            result = mod.GeminiEmbedder().embed(["x"])
        # Zero-vec passes through without division-by-zero.
        assert result[0] == [0.0] * 768


# ---------------------------------------------------------------------------
# task_type routing
# ---------------------------------------------------------------------------

class TestTaskType:
    def _captured_config(self, call_args):
        """Return the EmbedContentConfig passed to embed_content (or None)."""
        # kwargs first (preferred — our impl uses kwargs)
        if "config" in call_args.kwargs:
            return call_args.kwargs["config"]
        # positional fallback
        if len(call_args.args) >= 3:
            return call_args.args[2]
        return None

    def test_001_passes_task_type_when_set(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "x" * 33)
        monkeypatch.setenv("KNOWLEDGE_GEMINI_MODEL", "gemini-embedding-001")
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "768")
        mod = _fresh_module()
        with patch.object(mod, "genai") as mock_genai, \
             patch.object(mod, "genai_types") as mock_types:
            client = MagicMock()
            client.models.embed_content.return_value = _mock_embeddings_response(1)
            mock_genai.Client.return_value = client
            mod.GeminiEmbedder().embed(["q"], task_type="RETRIEVAL_QUERY")
            # EmbedContentConfig was built with task_type
            mock_types.EmbedContentConfig.assert_called_once()
            kwargs = mock_types.EmbedContentConfig.call_args.kwargs
            assert kwargs.get("task_type") == "RETRIEVAL_QUERY"

    def test_2_preview_skips_task_type(self, monkeypatch):
        """gemini-embedding-2-preview does not support task_type per the
        Google docs; our code must not pass it."""
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "x" * 33)
        monkeypatch.setenv("KNOWLEDGE_GEMINI_MODEL", "gemini-embedding-2-preview")
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "768")
        mod = _fresh_module()
        with patch.object(mod, "genai") as mock_genai, \
             patch.object(mod, "genai_types") as mock_types:
            client = MagicMock()
            client.models.embed_content.return_value = _mock_embeddings_response(1)
            mock_genai.Client.return_value = client
            mod.GeminiEmbedder().embed(["q"], task_type="RETRIEVAL_QUERY")
            # Since dim=768 is the native dim AND task_type is skipped,
            # there is no config to build at all → config=None.
            config = self._captured_config(
                client.models.embed_content.call_args
            )
            assert config is None
            # And EmbedContentConfig was never constructed
            mock_types.EmbedContentConfig.assert_not_called()

    def test_001_without_task_type_omits_kwarg(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "x" * 33)
        monkeypatch.setenv("KNOWLEDGE_GEMINI_MODEL", "gemini-embedding-001")
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "1536")
        mod = _fresh_module()
        with patch.object(mod, "genai") as mock_genai, \
             patch.object(mod, "genai_types") as mock_types:
            client = MagicMock()
            client.models.embed_content.return_value = _mock_embeddings_response(1, dim=1536)
            mock_genai.Client.return_value = client
            mod.GeminiEmbedder().embed(["q"])  # no task_type
            kwargs = mock_types.EmbedContentConfig.call_args.kwargs
            assert "task_type" not in kwargs
            # But output_dimensionality IS passed (1536 != native 768)
            assert kwargs.get("output_dimensionality") == 1536


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------

class TestBatching:
    def test_large_input_is_batched(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy" + "x" * 33)
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "768")
        mod = _fresh_module()
        # 50 texts with _BATCH_SIZE=20 → 3 calls (20, 20, 10)
        with patch.object(mod, "genai") as mock_genai:
            client = MagicMock()

            def fake_embed(**kwargs):
                batch_size = len(kwargs["contents"])
                return _mock_embeddings_response(batch_size)

            client.models.embed_content.side_effect = fake_embed
            mock_genai.Client.return_value = client
            result = mod.GeminiEmbedder().embed([f"t{i}" for i in range(50)])
        assert len(result) == 50
        assert client.models.embed_content.call_count == 3
        # Validate sizes per call
        sizes = [
            len(call.kwargs["contents"])
            for call in client.models.embed_content.call_args_list
        ]
        assert sizes == [20, 20, 10]
