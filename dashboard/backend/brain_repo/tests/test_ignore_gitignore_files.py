"""Tests for build_ignore_callback — .gitignore suppression behaviour.

Verifica que:
  1. O callback de ignore retorna ".gitignore" como ignorado quando presente
     em ``names``, qualquer que seja o diretório.
  2. Outros arquivos e diretórios legítimos NÃO são excluídos por esta regra.
  3. Um copytree simulado sobre uma árvore contendo workspace/marketing/_state/
     NÃO copia o .gitignore interno mas SIM copia os arquivos de estado sob ele.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path


class TestIgnoreGitignoreFiles(unittest.TestCase):
    """Testa a regra de não-cópia de .gitignore aninhados."""

    def setUp(self):
        import importlib
        import dashboard.backend.brain_repo.job_runner as jr_module
        importlib.reload(jr_module)
        self.jr = jr_module

    # ────────────────────────────────────────────────────────────
    # Testes unitários do callback
    # ────────────────────────────────────────────────────────────

    def _make_workspace(self):
        """Cria um workspace temporário em disco com estrutura mínima."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        return Path(tmp)

    def test_gitignore_is_ignored_at_root(self):
        """build_ignore_callback deve retornar '.gitignore' como ignorado."""
        ws = self._make_workspace()
        callback = self.jr.build_ignore_callback(ws)

        # Simula names na raiz do workspace
        result = callback(str(ws), [".gitignore", "README.md", "data.yaml"])
        self.assertIn(".gitignore", result)

    def test_gitignore_is_ignored_in_nested_dir(self):
        """A regra se aplica em qualquer subdiretório, não só na raiz."""
        ws = self._make_workspace()
        nested = ws / "workspace" / "marketing" / "_state"
        nested.mkdir(parents=True, exist_ok=True)

        callback = self.jr.build_ignore_callback(ws)
        result = callback(str(nested), [".gitignore", "snapshot-2026-05.json", "README.md"])

        self.assertIn(".gitignore", result)

    def test_regular_files_not_ignored_by_gitignore_rule(self):
        """Arquivos comuns NÃO devem ser excluídos pela regra do .gitignore."""
        ws = self._make_workspace()
        nested = ws / "workspace" / "marketing" / "_state"
        nested.mkdir(parents=True, exist_ok=True)

        # Cria arquivo real pra que full.is_file() retorne True
        (nested / "snapshot.json").write_text('{"ok": true}')

        callback = self.jr.build_ignore_callback(ws)
        result = callback(str(nested), [".gitignore", "snapshot.json"])

        self.assertIn(".gitignore", result)
        self.assertNotIn("snapshot.json", result)

    def test_only_gitignore_name_excluded_not_gitkeep(self):
        """'.gitkeep' e outros .git* que não são '.gitignore' NÃO devem ser excluídos pela regra."""
        ws = self._make_workspace()
        callback = self.jr.build_ignore_callback(ws)

        result = callback(str(ws), [".gitignore", ".gitkeep", ".gitattributes"])

        self.assertIn(".gitignore", result)
        # .gitkeep e .gitattributes são arquivos legítimos de versionamento
        self.assertNotIn(".gitkeep", result)
        self.assertNotIn(".gitattributes", result)

    # ────────────────────────────────────────────────────────────
    # Teste de integração — copytree simulado
    # ────────────────────────────────────────────────────────────

    def test_copytree_does_not_copy_gitignore_but_copies_state_files(self):
        """copytree com o callback NÃO deve copiar .gitignore e DEVE copiar arquivos de _state."""
        ws = self._make_workspace()
        dst_root = self._make_workspace()

        # Monta estrutura: workspace/marketing/_state/
        state_dir = ws / "workspace" / "marketing" / "_state"
        state_dir.mkdir(parents=True)

        # .gitignore com wildcard que excluiria tudo
        (state_dir / ".gitignore").write_text("*\n!.gitignore\n!README.md\n")
        (state_dir / "README.md").write_text("# State\n")
        (state_dir / "snapshot-2026-05.json").write_text('{"ads": []}')
        (state_dir / "checkpoint.yaml").write_text("step: 3\n")

        callback = self.jr.build_ignore_callback(ws)
        dst = dst_root / "workspace" / "marketing" / "_state"

        shutil.copytree(str(state_dir), str(dst), ignore=callback)

        # .gitignore NÃO deve ter sido copiado
        self.assertFalse(
            (dst / ".gitignore").exists(),
            ".gitignore não deveria ter sido copiado pro brain repo",
        )

        # Arquivos de estado DEVEM ter sido copiados
        self.assertTrue(
            (dst / "snapshot-2026-05.json").exists(),
            "snapshot-2026-05.json deveria ter sido copiado",
        )
        self.assertTrue(
            (dst / "checkpoint.yaml").exists(),
            "checkpoint.yaml deveria ter sido copiado",
        )
        self.assertTrue(
            (dst / "README.md").exists(),
            "README.md deveria ter sido copiado",
        )


if __name__ == "__main__":
    unittest.main()
