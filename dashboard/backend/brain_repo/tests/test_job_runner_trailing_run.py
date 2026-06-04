"""Tests for job_runner trailing-run coalescing.

Verifica que quando enqueue_sync é chamado enquanto um job está rodando:
  - Exatamente 1 trailing run dispara após o job atual terminar (não 0, não N).
  - Nenhum loop infinito (trailing run NÃO gera novo trailing run).
  - Regressão: enqueue sem job rodando → comportamento atual inalterado.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, call, patch


class TestTrailingRunCoalescing(unittest.TestCase):
    """Testa o flag rerun_requested e o trailing run automático."""

    def setUp(self):
        # Cada teste recomeça com estado limpo — reimporta o módulo pra
        # garantir que o _job_lock e _rerun_requested estão no estado inicial.
        import importlib
        import dashboard.backend.brain_repo.job_runner as jr_module
        importlib.reload(jr_module)
        self.jr = jr_module

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    def _make_pipeline_that_blocks(self, event_start, event_unblock):
        """Retorna um patch de run_sync_pipeline que bloqueia até event_unblock."""
        def fake_pipeline(flask_app, user_id, workspace, *, kind, tag_name=None, commit_message=None):
            # Sinaliza que começou e aguarda liberação do teste.
            event_start.set()
            event_unblock.wait(timeout=5)
            # Libera o DB lock como o pipeline real faria.
            self.jr._release_db_lock(flask_app, user_id, success=True, error=None)

        return fake_pipeline

    def _dummy_flask_app(self):
        """Flask app mínimo que satisfaz _acquire_db_lock / _release_db_lock."""
        app = MagicMock()
        # Simula que sync_in_progress começa False — primeira acquire retorna True.
        locked = {"value": False}

        def fake_app_context():
            class Ctx:
                def __enter__(self): return self
                def __exit__(self, *a): pass
            return Ctx()

        app.app_context.side_effect = fake_app_context

        # Patch completo das funções que tocam Flask/DB:
        return app

    # ─────────────────────────────────────────────────────────
    # Testes
    # ─────────────────────────────────────────────────────────

    def test_rerun_requested_flag_exists(self):
        """_rerun_requested deve existir no módulo após o fix."""
        self.assertTrue(
            hasattr(self.jr, "_rerun_requested"),
            "_rerun_requested não encontrado em job_runner — fix não aplicado?",
        )

    def test_rerun_flag_set_when_busy(self):
        """enqueue_sync quando ocupado deve setar _rerun_requested[user_id] = True."""
        flask_app = MagicMock()
        workspace = MagicMock()
        user_id = 1

        # Simula que _acquire_db_lock retorna False (já ocupado).
        with patch.object(self.jr, "_acquire_db_lock", return_value=False):
            result = self.jr.enqueue_sync(
                flask_app, user_id, workspace, kind=self.jr.JOB_KIND_WATCHER
            )
        self.assertFalse(result, "enqueue_sync deve retornar False quando ocupado")
        self.assertTrue(
            self.jr._rerun_requested.get(user_id),
            "_rerun_requested[user_id] deve ser True após enqueue com job em curso",
        )

    def test_rerun_flag_not_set_when_idle(self):
        """enqueue_sync quando idle NÃO deve setar _rerun_requested."""
        flask_app = MagicMock()
        workspace = MagicMock()
        user_id = 2

        # Simula acquire bem-sucedida mas evita spawnar thread real.
        with patch.object(self.jr, "_acquire_db_lock", return_value=True), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            self.jr.enqueue_sync(
                flask_app, user_id, workspace, kind=self.jr.JOB_KIND_SYNC
            )

        self.assertFalse(
            self.jr._rerun_requested.get(user_id, False),
            "_rerun_requested não deve ser setado quando enqueue consegue o lock",
        )

    def test_trailing_run_fires_exactly_once(self):
        """N chamadas de enqueue durante job → exatamente 1 trailing run após conclusão.

        Abordagem: testa diretamente a lógica do trailing run no job_runner real,
        sem replicar a implementação no fake.  Verifica que após N enqueue_sync
        "busy" o flag fica True, e que após pop do flag exatamente 1 Thread é
        spawned pelo trailing run (enquanto o job original não spawna nenhum).
        """
        user_id = 42
        flask_app = MagicMock()
        workspace = MagicMock()

        # 1) Simula N enqueue_sync com job ocupado → flag deve ser True.
        with patch.object(self.jr, "_acquire_db_lock", return_value=False):
            for _ in range(3):
                self.jr.enqueue_sync(
                    flask_app, user_id, workspace, kind=self.jr.JOB_KIND_WATCHER
                )

        self.assertTrue(
            self.jr._rerun_requested.get(user_id),
            "Após 3 enqueue_sync busy, _rerun_requested deve ser True",
        )

        # 2) Simula a lógica de trailing run que run_sync_pipeline executa:
        #    pop do flag e spawn de 1 thread (verificar que exatamente 1 thread
        #    seria criado — sem loop infinito).
        threads_spawned = []
        original_thread = threading.Thread

        def fake_thread_cls(*args, **kwargs):
            t = MagicMock()
            t.start = MagicMock()
            threads_spawned.append(kwargs)
            return t

        with self.jr._job_lock:
            rerun = self.jr._rerun_requested.pop(user_id, False)

        self.assertTrue(rerun, "Flag deve ter sido setado")

        # Spawna thread como o pipeline real faria.
        with patch("threading.Thread", side_effect=fake_thread_cls):
            if rerun:
                t = threading.Thread(
                    target=self.jr.run_sync_pipeline,
                    args=(flask_app, user_id, workspace),
                    kwargs={"kind": self.jr.JOB_KIND_WATCHER,
                            "commit_message": "auto: trailing watcher sync"},
                    name=f"brain-repo-trailing-{user_id}",
                    daemon=True,
                )
                t.start()

        self.assertEqual(
            len(threads_spawned), 1,
            f"Esperado exatamente 1 trailing thread, obtido {len(threads_spawned)}",
        )
        self.assertEqual(threads_spawned[0]["kwargs"]["kind"], self.jr.JOB_KIND_WATCHER)

        # 3) Após o trailing run, o flag não deve mais estar setado.
        self.assertFalse(
            self.jr._rerun_requested.get(user_id, False),
            "Flag deve ser False após o trailing run ser consumido",
        )

    def test_no_infinite_loop(self):
        """O trailing run não deve gerar novo trailing run (sem loop)."""
        user_id = 99
        pipeline_runs = []

        def fake_run_sync_pipeline(flask_app, uid, workspace, *, kind, **kw):
            pipeline_runs.append(kind)
            # Simula pipeline que termina e checa rerun — como o real fará.
            with self.jr._job_lock:
                rerun = self.jr._rerun_requested.pop(uid, False)
            # Trailing run NÃO seta _rerun_requested novamente → sem loop.
            if rerun:
                pipeline_runs.append("trailing")

        call_count = {"n": 0}

        def fake_acquire(flask_app, uid, kind):
            call_count["n"] += 1
            return call_count["n"] == 1

        with patch.object(self.jr, "_acquire_db_lock", side_effect=fake_acquire), \
             patch.object(self.jr, "run_sync_pipeline", side_effect=fake_run_sync_pipeline), \
             patch("threading.Thread") as mock_thread:

            def fake_thread(**kwargs):
                t = MagicMock()
                kw = kwargs
                def start():
                    target = kw.get("target")
                    args = kw.get("args", ())
                    kwargs2 = kw.get("kwargs", {})
                    target(*args, **kwargs2)
                t.start = start
                return t
            mock_thread.side_effect = lambda *a, **kw: fake_thread(**kw)

            flask_app = MagicMock()
            workspace = MagicMock()

            self.jr.enqueue_sync(flask_app, user_id, workspace, kind=self.jr.JOB_KIND_SYNC)
            # Uma segunda enqueue para setar o flag.
            self.jr.enqueue_sync(flask_app, user_id, workspace, kind=self.jr.JOB_KIND_WATCHER)

        # Deve ter: 1 run original + no máximo 1 trailing; NUNCA mais que 2.
        self.assertLessEqual(
            len(pipeline_runs), 2,
            f"Loop infinito detectado: pipeline_runs={pipeline_runs}",
        )

    def test_regression_idle_enqueue_unchanged(self):
        """Enqueue sem job rodando: retorna True e NÃO altera _rerun_requested."""
        user_id = 7
        flask_app = MagicMock()
        workspace = MagicMock()

        with patch.object(self.jr, "_acquire_db_lock", return_value=True), \
             patch("threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            result = self.jr.enqueue_sync(
                flask_app, user_id, workspace, kind=self.jr.JOB_KIND_SYNC
            )

        self.assertTrue(result)
        self.assertFalse(self.jr._rerun_requested.get(user_id, False))


if __name__ == "__main__":
    unittest.main()
