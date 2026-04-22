"""Tests for knowledge/migrations/versions/001_initial_schema._resolve_embedder_config.

Pure unit tests — no Postgres, no Alembic, no file I/O.

The migration file starts with a digit (001_…) so it cannot be imported via the
normal dotted-module path; we use importlib to load it by path.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_migration_module():
    """Load 001_initial_schema.py via importlib (digit-prefixed filename)."""
    migration_path = (
        Path(__file__).resolve().parent.parent
        / "migrations" / "versions" / "001_initial_schema.py"
    )
    spec = importlib.util.spec_from_file_location("knowledge_001_initial_schema", migration_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestResolveEmbedderConfig:
    """_resolve_embedder_config() returns (provider, model, dim) from env."""

    def _fn(self):
        return _load_migration_module()._resolve_embedder_config

    def test_default_is_local_768(self, monkeypatch):
        monkeypatch.delenv("KNOWLEDGE_EMBEDDER_PROVIDER", raising=False)
        monkeypatch.delenv("KNOWLEDGE_OPENAI_MODEL", raising=False)
        fn = self._fn()
        provider, model, dim = fn()
        assert provider == "local"
        assert dim == 768

    def test_openai_small_is_1536(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "openai")
        monkeypatch.setenv("KNOWLEDGE_OPENAI_MODEL", "text-embedding-3-small")
        fn = self._fn()
        provider, model, dim = fn()
        assert provider == "openai"
        assert model == "text-embedding-3-small"
        assert dim == 1536

    def test_openai_large_is_3072(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "openai")
        monkeypatch.setenv("KNOWLEDGE_OPENAI_MODEL", "text-embedding-3-large")
        fn = self._fn()
        provider, model, dim = fn()
        assert provider == "openai"
        assert dim == 3072

    def test_openai_ada_is_1536(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "openai")
        monkeypatch.setenv("KNOWLEDGE_OPENAI_MODEL", "text-embedding-ada-002")
        fn = self._fn()
        _, _, dim = fn()
        assert dim == 1536

    def test_openai_default_model_when_no_model_env(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "openai")
        monkeypatch.delenv("KNOWLEDGE_OPENAI_MODEL", raising=False)
        fn = self._fn()
        provider, model, dim = fn()
        assert provider == "openai"
        # text-embedding-3-small is the default for openai
        assert model == "text-embedding-3-small"
        assert dim == 1536

    def test_unknown_provider_falls_back_to_local(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", "invalid_provider")
        fn = self._fn()
        provider, _, dim = fn()
        assert provider == "local"
        assert dim == 768

    def test_quoted_env_values_are_stripped(self, monkeypatch):
        monkeypatch.setenv("KNOWLEDGE_EMBEDDER_PROVIDER", '"openai"')
        monkeypatch.setenv("KNOWLEDGE_OPENAI_MODEL", '"text-embedding-3-large"')
        fn = self._fn()
        provider, model, dim = fn()
        assert provider == "openai"
        assert model == "text-embedding-3-large"
        assert dim == 3072
