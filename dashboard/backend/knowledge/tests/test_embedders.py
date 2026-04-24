"""Tests for knowledge/embedders.

LocalEmbedder round-trip test requires sentence-transformers installed.
OpenAIEmbedder tests use mocks (no API calls).
"""

import os
import sys
import pytest


def _backend():
    return os.path.join(os.path.dirname(__file__), "..", "..")


def _add_backend():
    b = _backend()
    if b not in sys.path:
        sys.path.insert(0, b)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_get_embedder_default_is_local(self):
        _add_backend()
        from knowledge.embedders.base import get_embedder
        os.environ.pop("KNOWLEDGE_EMBEDDER_PROVIDER", None)
        embedder = get_embedder()
        assert embedder.__class__.__name__ == "LocalEmbedder"

    def test_get_embedder_explicit_local(self):
        _add_backend()
        from knowledge.embedders.base import get_embedder
        embedder = get_embedder("local")
        assert embedder.__class__.__name__ == "LocalEmbedder"

    def test_get_embedder_explicit_openai(self):
        _add_backend()
        from knowledge.embedders.base import get_embedder
        embedder = get_embedder("openai")
        assert embedder.__class__.__name__ == "OpenAIEmbedder"

    def test_get_embedder_explicit_gemini(self):
        _add_backend()
        from knowledge.embedders.base import get_embedder
        embedder = get_embedder("gemini")
        assert embedder.__class__.__name__ == "GeminiEmbedder"

    def test_get_embedder_unknown_raises_value_error(self):
        _add_backend()
        from knowledge.embedders.base import get_embedder
        with pytest.raises(ValueError, match="Unknown embedder provider"):
            get_embedder("voyage")  # type: ignore[arg-type]

    def test_local_embedder_dim(self):
        _add_backend()
        from knowledge.embedders.local_embedder import LocalEmbedder
        e = LocalEmbedder()
        assert e.dim == 768

    def test_openai_embedder_dim(self):
        _add_backend()
        from knowledge.embedders.openai_embedder import OpenAIEmbedder
        e = OpenAIEmbedder()
        assert e.dim == 1536

    def test_gemini_embedder_default_dim(self, monkeypatch):
        _add_backend()
        monkeypatch.delenv("KNOWLEDGE_GEMINI_DIM", raising=False)
        monkeypatch.delenv("KNOWLEDGE_GEMINI_MODEL", raising=False)
        from knowledge.embedders.gemini_embedder import GeminiEmbedder
        e = GeminiEmbedder()
        assert e.dim == 768

    def test_gemini_embedder_custom_dim(self, monkeypatch):
        _add_backend()
        monkeypatch.setenv("KNOWLEDGE_GEMINI_DIM", "1536")
        from knowledge.embedders.gemini_embedder import GeminiEmbedder
        e = GeminiEmbedder()
        assert e.dim == 1536


# ---------------------------------------------------------------------------
# LocalEmbedder round-trip
# ---------------------------------------------------------------------------

class TestLocalEmbedder:
    @pytest.fixture(autouse=True)
    def skip_if_no_st(self):
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            pytest.skip("sentence-transformers not installed")

    def test_embed_single_text_returns_correct_dim(self):
        _add_backend()
        from knowledge.embedders.local_embedder import LocalEmbedder
        e = LocalEmbedder()
        result = e.embed(["Hello world"])
        assert len(result) == 1
        assert len(result[0]) == 768

    def test_embed_multiple_texts_returns_all_vectors(self):
        _add_backend()
        from knowledge.embedders.local_embedder import LocalEmbedder
        e = LocalEmbedder()
        texts = ["First sentence.", "Second sentence.", "Third sentence."]
        result = e.embed(texts)
        assert len(result) == 3
        assert all(len(v) == 768 for v in result)

    def test_embed_portuguese_text(self):
        _add_backend()
        from knowledge.embedders.local_embedder import LocalEmbedder
        e = LocalEmbedder()
        result = e.embed(["Olá mundo, como vai você?"])
        assert len(result) == 1
        assert len(result[0]) == 768

    def test_embed_empty_list_raises(self):
        _add_backend()
        from knowledge.embedders.local_embedder import LocalEmbedder
        e = LocalEmbedder()
        with pytest.raises(ValueError, match="empty texts list"):
            e.embed([])

    def test_embed_returns_list_of_floats(self):
        _add_backend()
        from knowledge.embedders.local_embedder import LocalEmbedder
        e = LocalEmbedder()
        result = e.embed(["Test"])
        assert all(isinstance(v, float) for v in result[0])

    def test_model_is_cached(self):
        _add_backend()
        from knowledge.embedders import local_embedder as mod
        # Reset global model
        mod._model = None
        mod._model_name = None

        from knowledge.embedders.local_embedder import LocalEmbedder
        e1 = LocalEmbedder()
        e2 = LocalEmbedder()
        # Embed once to load model
        e1.embed(["warm up"])
        model_ref = mod._model
        # Second embed should reuse cached model
        e2.embed(["check cache"])
        assert mod._model is model_ref


# ---------------------------------------------------------------------------
# OpenAIEmbedder (mocked)
# ---------------------------------------------------------------------------

class TestOpenAIEmbedder:
    def test_embed_requires_api_key(self):
        _add_backend()
        from knowledge.embedders.openai_embedder import OpenAIEmbedder
        os.environ.pop("OPENAI_API_KEY", None)
        e = OpenAIEmbedder()
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            e.embed(["test"])

    def test_embed_empty_list_raises(self):
        _add_backend()
        from knowledge.embedders.openai_embedder import OpenAIEmbedder
        e = OpenAIEmbedder()
        with pytest.raises(ValueError, match="empty texts list"):
            e.embed([])

    def test_dim_by_model_matrix(self):
        """Each known model returns the correct dimension — no env override needed."""
        _add_backend()
        from knowledge.embedders.openai_embedder import OpenAIEmbedder, _MODEL_DIMS

        expected = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        for model, expected_dim in expected.items():
            os.environ["KNOWLEDGE_OPENAI_MODEL"] = model
            e = OpenAIEmbedder()
            assert e.dim == expected_dim, f"{model}: expected {expected_dim}, got {e.dim}"

        # Unknown model falls back to 1536 default
        os.environ["KNOWLEDGE_OPENAI_MODEL"] = "text-embedding-unknown-zzz"
        e_unknown = OpenAIEmbedder()
        assert e_unknown.dim == 1536, "Unknown model should default to 1536"

    def test_embed_with_mock(self, monkeypatch):
        _add_backend()
        from knowledge.embedders.openai_embedder import OpenAIEmbedder

        os.environ["OPENAI_API_KEY"] = "sk-test-key"
        os.environ.pop("KNOWLEDGE_OPENAI_MODEL", None)  # default → 3-small → 1536

        _MOCK_DIM = 1536  # default model dim

        # Build mock embedding response
        mock_vector = [0.1] * _MOCK_DIM

        class MockEmbeddingItem:
            def __init__(self, idx):
                self.index = idx
                self.embedding = mock_vector

        class MockEmbeddings:
            def create(self, **kwargs):
                n = len(kwargs["input"])
                mock_response = type(
                    "R", (), {"data": [MockEmbeddingItem(i) for i in range(n)]}
                )()
                return mock_response

        class MockClient:
            embeddings = MockEmbeddings()

        monkeypatch.setattr(
            "knowledge.embedders.openai_embedder.OpenAI",
            lambda **kw: MockClient(),
        )

        e = OpenAIEmbedder()
        result = e.embed(["hello", "world"])
        assert len(result) == 2
        assert all(len(v) == _MOCK_DIM for v in result)
