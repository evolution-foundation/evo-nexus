from __future__ import annotations

import importlib
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask

BACKEND_DIR = Path(__file__).resolve().parents[2] / "dashboard" / "backend"
ADW_DIR = Path(__file__).resolve().parents[2] / "ADWs"
for path in (str(BACKEND_DIR), str(ADW_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture
def app():
    import models

    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    models.db.init_app(app)
    with app.app_context():
        models.db.create_all()
    return app


def make_task(app, **overrides):
    from models import ScheduledTask, db

    values = {
        "name": "task",
        "description": "",
        "type": "prompt",
        "payload": "say hi",
        "scheduled_at": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    values.update(overrides)
    with app.app_context():
        task = ScheduledTask(**values)
        db.session.add(task)
        db.session.commit()
        return task.id


def test_atomic_claim_executes_due_task_once(app):
    import routes.tasks as tasks
    importlib.reload(tasks)

    task_id = make_task(app)
    with app.app_context():
        assert tasks.claim_task(task_id) is True
        assert tasks.claim_task(task_id) is False
        from models import ScheduledTask
        assert ScheduledTask.query.get(task_id).status == "running"


def test_failed_task_manual_claim_has_one_exact_fenced_winner(app):
    from models import ScheduledTask, db
    from routes.tasks import claim_task

    with app.app_context():
        task = ScheduledTask(
            name="retry-fence",
            type="prompt",
            payload="ok",
            status="failed",
            attempt=4,
            runtime_run_id="historical-run",
            error="old failure",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id

        assert claim_task(
            task_id,
            expected_status="failed",
            expected_attempt=4,
            expected_runtime_run_id="historical-run",
            clear_error=True,
        )
        assert not claim_task(
            task_id,
            expected_status="failed",
            expected_attempt=4,
            expected_runtime_run_id="historical-run",
            clear_error=True,
        )
        claimed = db.session.get(ScheduledTask, task_id)
        assert claimed.status == "running"
        assert claimed.attempt == 4
        assert claimed.runtime_run_id == "historical-run"
        assert claimed.error is None


def test_failed_task_claim_rejects_stale_attempt_or_runtime_run(app):
    from models import ScheduledTask, db
    from routes.tasks import claim_task

    with app.app_context():
        task = ScheduledTask(
            name="retry-stale-fence",
            type="prompt",
            payload="ok",
            status="failed",
            attempt=3,
            runtime_run_id="current-run",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()

        assert not claim_task(
            task.id,
            expected_status="failed",
            expected_attempt=2,
            expected_runtime_run_id="old-run",
            clear_error=True,
        )
        assert db.session.get(ScheduledTask, task.id).status == "failed"


def test_recover_running_task_requeues_or_fails_by_policy(app):
    import routes.tasks as tasks
    importlib.reload(tasks)

    task_id = make_task(app, status="running", started_at=datetime.now(timezone.utc) - timedelta(minutes=20))
    with app.app_context():
        assert tasks.recover_stale_tasks(max_age_seconds=60) == 1
        from models import ScheduledTask
        task = ScheduledTask.query.get(task_id)
        assert task.status == "pending"
        assert task.attempt == 1


def test_recover_interrupted_work_recovers_tasks_and_runtime_runs(app):
    import routes.tasks as tasks
    importlib.reload(tasks)

    task_id = make_task(
        app,
        status="running",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    with app.app_context():
        from models import RuntimeRun, ScheduledTask, db
        from runtime_runs import create_run, transition

        run = create_run(task_id)
        transition(run, "running")
        run.started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.session.commit()

        recovered = tasks.recover_interrupted_work()

        assert recovered == {"tasks": 1, "runtime_runs": 1}
        assert ScheduledTask.query.get(task_id).status == "pending"
        assert RuntimeRun.query.get(run.id).status == "failed"
        assert tasks.recover_interrupted_work() == {"tasks": 0, "runtime_runs": 0}


def test_recovery_atomically_fails_fresh_queued_run_before_requeueing_task(app):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run

    task_id = make_task(
        app,
        status="running",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        run = create_run(task_id)
        run_id = run.id
        task.runtime_run_id = run_id
        task.attempt = run.attempt
        db.session.commit()

        assert tasks.recover_interrupted_work(
            task_timeout_seconds=60,
            run_lease_seconds=900,
        ) == {"tasks": 1, "runtime_runs": 1}

        db.session.expire_all()
        task = db.session.get(ScheduledTask, task_id)
        assert task.status == "pending"
        assert task.attempt == 2
        assert db.session.get(RuntimeRun, run_id).status == "failed"


def test_recover_interrupted_work_preserves_fresh_active_work(app):
    import routes.tasks as tasks
    importlib.reload(tasks)

    task_id = make_task(
        app,
        status="running",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with app.app_context():
        from models import RuntimeRun, ScheduledTask, db
        from runtime_runs import create_run, transition

        run = create_run(task_id)
        transition(run, "running")
        run.started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()

        assert tasks.recover_interrupted_work() == {"tasks": 0, "runtime_runs": 0}
        assert ScheduledTask.query.get(task_id).status == "running"
        assert RuntimeRun.query.get(run.id).status == "running"


def test_recovery_preserves_task_whose_run_awaits_approval(app):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, request_approval, transition

    task_id = make_task(
        app,
        status="running",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        run = create_run(task_id)
        run_id = run.id
        task.runtime_run_id = run_id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        request_approval(run, "publish")

        assert tasks.recover_interrupted_work(
            task_timeout_seconds=0,
            run_lease_seconds=0,
        ) == {"tasks": 0, "runtime_runs": 0}

        db.session.expire_all()
        assert db.session.get(ScheduledTask, task_id).status == "running"
        assert db.session.get(RuntimeRun, run_id).status == "awaiting_approval"


def test_approval_renews_linked_task_and_run_leases_before_resuming(app):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, decide_approval, request_approval, transition

    stale_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    task_id = make_task(app, status="running", started_at=stale_at)
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        run = create_run(task_id)
        run_id = run.id
        task.runtime_run_id = run_id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        approval = request_approval(run, "publish")

        run.started_at = stale_at
        task.started_at = stale_at
        db.session.commit()
        decide_approval(run, approval, approved=True, actor="reviewer")

        assert tasks.recover_interrupted_work(
            task_timeout_seconds=60,
            run_lease_seconds=60,
        ) == {"tasks": 0, "runtime_runs": 0}

        db.session.expire_all()
        resumed_task = db.session.get(ScheduledTask, task_id)
        resumed_run = db.session.get(RuntimeRun, run_id)
        assert resumed_task.status == "running"
        assert resumed_run.status == "running"
        assert resumed_task.started_at.replace(tzinfo=timezone.utc) > stale_at
        assert resumed_run.started_at.replace(tzinfo=timezone.utc) > stale_at


def test_worker_cannot_finalize_after_recovery_fences_its_attempt(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)

    sys.path.insert(0, str(ADW_DIR))
    try:
        import runner
    finally:
        sys.path.pop(0)

    task_id = make_task(app, type="prompt", payload="fenced")
    monkeypatch.setattr(tasks, "_resolve_task_runtime", lambda task: ("hermes", "default", None))

    def finish_after_recovery(*args, **kwargs):
        assert tasks.recover_interrupted_work(
            task_timeout_seconds=0,
            run_lease_seconds=0,
        ) == {"tasks": 1, "runtime_runs": 1}
        return {"success": True, "stdout": "late success", "returncode": 0}

    monkeypatch.setattr(runner, "run_claude", finish_after_recovery)
    with app.app_context():
        assert tasks._execute_task(task_id) is False

        from models import EventOutbox, RuntimeRun, ScheduledTask

        task = ScheduledTask.query.get(task_id)
        run = RuntimeRun.query.get(task.runtime_run_id)
        assert task.status == "failed"
        assert task.attempt == run.attempt
        assert run.status == "failed"
        assert EventOutbox.query.filter_by(
            event_type="run.failed",
            subject=f"run:{run.id}",
        ).count() == 1


def test_recover_interrupted_work_terminalizes_orphaned_queued_run(app):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import EventOutbox, RuntimeRun, db
    from runtime_runs import create_run

    task_id = make_task(app, status="running")
    with app.app_context():
        run = create_run(task_id)
        run.queued_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.session.commit()

        recovered = tasks.recover_interrupted_work(
            task_timeout_seconds=900,
            run_lease_seconds=900,
        )

        run = db.session.get(RuntimeRun, run.id)
        assert recovered["runtime_runs"] == 1
        assert run.status == "failed"
        assert run.exit_code == -1
        assert EventOutbox.query.filter_by(
            event_type="run.failed",
            subject=f"run:{run.id}",
        ).count() == 1


def test_worker_cannot_attach_run_after_lease_is_recovered(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)

    sys.path.insert(0, str(ADW_DIR))
    try:
        import runner
    finally:
        sys.path.pop(0)

    task_id = make_task(app, type="prompt", payload="fenced-before-run")

    def resolve_after_recovery(task):
        assert tasks.recover_interrupted_work(
            task_timeout_seconds=0,
            run_lease_seconds=0,
        ) == {"tasks": 1, "runtime_runs": 0}
        return "hermes", "default", None

    monkeypatch.setattr(tasks, "_resolve_task_runtime", resolve_after_recovery)
    monkeypatch.setattr(
        runner,
        "run_claude",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("superseded worker must not execute")
        ),
    )
    with app.app_context():
        assert tasks._execute_task(task_id) is False

        from models import RuntimeRun, ScheduledTask

        task = ScheduledTask.query.get(task_id)
        run = RuntimeRun.query.filter_by(task_id=task_id).one()
        assert task.status == "pending"
        assert task.runtime_run_id is None
        assert run.status == "cancelled"


def test_recovered_running_effect_is_not_retried_during_provider_resolution(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, transition

    task_id = make_task(app, type="prompt", payload="provider-failure-after-recovery", status="running")
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        task.started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        run = create_run(task_id)
        run_id = run.id
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        run.started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.session.commit()

        assert tasks.recover_interrupted_work(
            task_timeout_seconds=0,
            run_lease_seconds=0,
        ) == {"tasks": 1, "runtime_runs": 1}

    monkeypatch.setattr(
        tasks,
        "_resolve_task_runtime",
        lambda task: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )

    with app.app_context():
        assert tasks._execute_task(task_id) is False
        task = db.session.get(ScheduledTask, task_id)
        previous_run = db.session.get(RuntimeRun, run_id)
        assert task.status == "failed"
        assert task.error == "Worker lease expired; external outcome indeterminate"
        assert task.runtime_run_id == previous_run.id
        assert previous_run.status == "failed"


def test_worker_cannot_start_external_execution_after_attached_lease_is_recovered(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)

    sys.path.insert(0, str(ADW_DIR))
    try:
        import runner
    finally:
        sys.path.pop(0)

    task_id = make_task(app, type="prompt", payload="fenced-at-start")
    original_start = tasks._start_owned_execution
    external_calls = []

    def recover_before_start(task_id, run, attempt):
        assert tasks.recover_stale_tasks(max_age_seconds=0) == 1
        return original_start(task_id, run, attempt)

    monkeypatch.setattr(tasks, "_start_owned_execution", recover_before_start)
    monkeypatch.setattr(
        runner,
        "run_claude",
        lambda *args, **kwargs: external_calls.append((args, kwargs)),
    )

    with app.app_context():
        assert tasks._execute_task(task_id) is False

        from models import RuntimeRun, ScheduledTask, db

        task = db.session.get(ScheduledTask, task_id)
        run = RuntimeRun.query.filter_by(task_id=task_id).one()
        assert task.status == "pending"
        assert task.attempt == 2
        assert run.status == "failed"
        assert external_calls == []


def test_worker_cannot_start_external_execution_after_started_lease_is_recovered(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)

    sys.path.insert(0, str(ADW_DIR))
    try:
        import runner
    finally:
        sys.path.pop(0)

    task_id = make_task(app, type="prompt", payload="fenced-after-start")
    original_start = tasks._start_owned_execution
    external_calls = []

    def recover_after_start(task_id, run, attempt):
        assert original_start(task_id, run, attempt) is True
        assert tasks.recover_interrupted_work(
            task_timeout_seconds=0,
            run_lease_seconds=0,
        ) == {"tasks": 1, "runtime_runs": 1}
        return True

    monkeypatch.setattr(tasks, "_start_owned_execution", recover_after_start)
    monkeypatch.setattr(
        runner,
        "run_claude",
        lambda *args, **kwargs: external_calls.append((args, kwargs)),
    )

    with app.app_context():
        assert tasks._execute_task(task_id) is False

        from models import RuntimeRun, ScheduledTask, db

        task = db.session.get(ScheduledTask, task_id)
        run = RuntimeRun.query.filter_by(task_id=task_id).one()
        assert task.status == "failed"
        assert task.attempt == run.attempt
        assert run.status == "failed"
        assert external_calls == []


def test_prompt_spawn_handoff_registers_process_before_cancellation_is_accepted(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)

    sys.path.insert(0, str(ADW_DIR))
    try:
        import runner
    finally:
        sys.path.pop(0)

    task_id = make_task(app, type="prompt", payload="handoff")
    entered_spawn = threading.Event()
    allow_registration = threading.Event()
    process_killed = threading.Event()
    cancellation_done = threading.Event()
    external_effects = []

    class Process:
        pid = 12345

    process = Process()
    monkeypatch.setattr(tasks, "_resolve_task_runtime", lambda task: ("hermes", "default", None))
    monkeypatch.setattr(tasks, "_kill_process_group", lambda current: process_killed.set())

    def fake_run_claude(*args, on_process, **kwargs):
        entered_spawn.set()
        assert allow_registration.wait(5)
        on_process(process)
        assert process_killed.wait(5)
        if not process_killed.is_set():
            external_effects.append("effect")
        return {"success": False, "stdout": "", "stderr": "cancelled", "returncode": -1}

    monkeypatch.setattr(runner, "run_claude", fake_run_claude)

    def execute():
        with app.app_context():
            tasks._execute_task(task_id)

    def cancel_after_spawn_started():
        assert entered_spawn.wait(5)
        with app.app_context():
            from models import ScheduledTask, db

            task = db.session.get(ScheduledTask, task_id)
            assert tasks._cancel_task_atomically(task)
            tasks.cancel_task_process(task_id)
        cancellation_done.set()

    worker = threading.Thread(target=execute)
    canceller = threading.Thread(target=cancel_after_spawn_started)
    worker.start()
    canceller.start()
    assert entered_spawn.wait(5)
    assert not cancellation_done.wait(0.1)
    allow_registration.set()
    worker.join(5)
    canceller.join(5)

    assert not worker.is_alive()
    assert not canceller.is_alive()
    assert cancellation_done.is_set()
    assert process_killed.is_set()
    assert external_effects == []


def test_script_spawn_handoff_registers_process_before_cancellation_is_accepted(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)

    task_id = make_task(app, type="script", payload="memory_lint.py")
    entered_spawn = threading.Event()
    allow_spawn_return = threading.Event()
    process_killed = threading.Event()
    cancellation_done = threading.Event()
    external_effects = []

    class Process:
        pid = 12346
        returncode = -1

        def communicate(self, timeout):
            assert process_killed.wait(5)
            if not process_killed.is_set():
                external_effects.append("effect")
            return "", "cancelled"

    process = Process()
    monkeypatch.setattr(tasks, "_resolve_task_runtime", lambda task: (None, None, None))
    monkeypatch.setattr(tasks, "_kill_process_group", lambda current: process_killed.set())

    def fake_popen(*args, **kwargs):
        entered_spawn.set()
        assert allow_spawn_return.wait(5)
        return process

    monkeypatch.setattr(tasks.subprocess, "Popen", fake_popen)

    def execute():
        with app.app_context():
            tasks._execute_task(task_id)

    def cancel_during_spawn():
        assert entered_spawn.wait(5)
        with app.app_context():
            from models import ScheduledTask, db

            task = db.session.get(ScheduledTask, task_id)
            assert tasks._cancel_task_atomically(task)
            tasks.cancel_task_process(task_id)
        cancellation_done.set()

    worker = threading.Thread(target=execute)
    canceller = threading.Thread(target=cancel_during_spawn)
    worker.start()
    canceller.start()
    assert entered_spawn.wait(5)
    assert not cancellation_done.wait(0.1)
    allow_spawn_return.set()
    worker.join(5)
    canceller.join(5)

    assert not worker.is_alive()
    assert not canceller.is_alive()
    assert cancellation_done.is_set()
    assert process_killed.is_set()
    assert external_effects == []


def test_spawn_handoff_registry_removes_inactive_task_locks():
    import routes.tasks as tasks
    importlib.reload(tasks)

    task_id = 1000
    first_lock = tasks._acquire_spawn_handoff(task_id)
    contender_started = threading.Event()
    contender_observations = []

    def contend_for_same_task():
        contender_started.set()
        second_lock = tasks._acquire_spawn_handoff(task_id)
        contender_observations.append(second_lock is first_lock)
        tasks._release_spawn_handoff(task_id, second_lock)

    contender = threading.Thread(target=contend_for_same_task)
    contender.start()
    assert contender_started.wait(5)
    for _ in range(100):
        with tasks._SPAWN_HANDOFF_LOCKS_GUARD:
            if tasks._SPAWN_HANDOFF_LOCKS[task_id].users == 2:
                break
        threading.Event().wait(0.01)
    with tasks._SPAWN_HANDOFF_LOCKS_GUARD:
        assert tasks._SPAWN_HANDOFF_LOCKS[task_id].users == 2

    tasks._release_spawn_handoff(task_id, first_lock)
    contender.join(5)
    assert not contender.is_alive()
    assert contender_observations == [True]
    assert task_id not in tasks._SPAWN_HANDOFF_LOCKS

    for task_id in range(100):
        with tasks._spawn_handoff_lock(task_id):
            assert task_id in tasks._SPAWN_HANDOFF_LOCKS
        assert task_id not in tasks._SPAWN_HANDOFF_LOCKS

    assert tasks._SPAWN_HANDOFF_LOCKS == {}


def test_sqlite_recovery_and_terminalization_have_one_winner(tmp_path):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import EventOutbox, RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, transition

    app = Flask("sqlite-recovery-race")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'recovery-race.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        task = ScheduledTask(
            name="race",
            type="prompt",
            payload="x",
            status="running",
            scheduled_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
        db.session.add(task)
        db.session.commit()
        run = create_run(task.id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        run.started_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.session.commit()
        task_id, run_id, attempt = task.id, run.id, run.attempt

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def finalize():
        try:
            with app.app_context():
                current_run = db.session.get(RuntimeRun, run_id)
                barrier.wait()
                results["finalize"] = tasks._finish_owned_execution(
                    task_id,
                    current_run,
                    attempt,
                    task_status="completed",
                    run_status="succeeded",
                    summary="done",
                    exit_code=0,
                )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def recover():
        try:
            with app.app_context():
                barrier.wait()
                results["recover"] = tasks.recover_interrupted_work(
                    task_timeout_seconds=0,
                    run_lease_seconds=0,
                )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=finalize), threading.Thread(target=recover)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        run = db.session.get(RuntimeRun, run_id)
        if results["finalize"]:
            assert results["recover"] == {"tasks": 0, "runtime_runs": 0}
            assert (task.status, run.status) == ("completed", "succeeded")
        else:
            assert results["recover"] == {"tasks": 1, "runtime_runs": 1}
            assert (task.status, run.status) == ("failed", "failed")
        assert EventOutbox.query.filter(
            EventOutbox.subject == f"run:{run_id}",
            EventOutbox.event_type.in_(("run.succeeded", "run.failed")),
        ).count() == 1


def test_sqlite_cancellation_and_terminalization_do_not_overwrite_each_other(tmp_path):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import EventOutbox, RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, transition

    app = Flask("sqlite-cancel-race")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'cancel-race.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        task = ScheduledTask(
            name="race",
            type="prompt",
            payload="x",
            status="running",
            scheduled_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        run = create_run(task.id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        task_id, run_id, attempt = task.id, run.id, run.attempt

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def finalize():
        try:
            with app.app_context():
                current_run = db.session.get(RuntimeRun, run_id)
                barrier.wait()
                results["finalize"] = tasks._finish_owned_execution(
                    task_id,
                    current_run,
                    attempt,
                    task_status="completed",
                    run_status="succeeded",
                    summary="done",
                    exit_code=0,
                )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def cancel():
        try:
            with app.app_context():
                current_task = db.session.get(ScheduledTask, task_id)
                barrier.wait()
                results["cancel"] = tasks._cancel_task_atomically(current_task)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=finalize), threading.Thread(target=cancel)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        run = db.session.get(RuntimeRun, run_id)
        if (task.status, run.status) == ("completed", "succeeded"):
            assert results == {"finalize": True, "cancel": False}
        else:
            assert (task.status, run.status) == ("cancelled", "cancelled")
            assert results == {"finalize": True, "cancel": True}
        terminal_events = EventOutbox.query.filter(
            EventOutbox.subject == f"run:{run_id}",
            EventOutbox.event_type.in_(("run.succeeded", "run.failed", "run.cancelled")),
        ).all()
        assert len(terminal_events) == 1
        assert terminal_events[0].event_type == f"run.{run.status}"


def test_cancelling_pending_retry_preserves_historical_failed_run(app):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, transition

    with app.app_context():
        task = ScheduledTask(
            name="retry",
            type="prompt",
            payload="x",
            status="running",
            scheduled_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        run = create_run(task.id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        transition(run, "failed", error="old failure", exit_code=-1)
        task.status = "pending"
        db.session.commit()

        assert tasks._cancel_task_atomically(task) is True
        assert db.session.get(ScheduledTask, task.id).status == "cancelled"
        assert db.session.get(RuntimeRun, run.id).status == "failed"


def test_cancel_recovered_attempt_before_new_run_attach_preserves_historical_run(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, transition

    with app.app_context():
        task = ScheduledTask(
            name="cancel-before-new-run-attach",
            type="prompt",
            payload="x",
            status="running",
            scheduled_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            attempt=1,
        )
        db.session.add(task)
        db.session.commit()
        old_run = create_run(task.id)
        task.runtime_run_id = old_run.id
        db.session.commit()
        transition(old_run, "running")
        transition(old_run, "failed", error="old failure", exit_code=-1)

        task.status = "running"
        task.attempt = 2
        task.started_at = datetime.now(timezone.utc)
        db.session.commit()
        task_id = task.id
        old_run_id = old_run.id

        assert tasks._cancel_task_atomically(task) is True
        assert db.session.get(ScheduledTask, task_id).status == "cancelled"
        assert db.session.get(RuntimeRun, old_run_id).status == "failed"

        monkeypatch.setattr(
            tasks, "_resolve_task_runtime", lambda _task: ("hermes", "default", None)
        )
        assert tasks._execute_task(task_id, already_claimed=True) is False
        new_run = RuntimeRun.query.filter_by(task_id=task_id, attempt=2).one()
        assert new_run.status == "cancelled"


def test_task_poller_recovers_periodically_without_zero_timeout():
    source = (BACKEND_DIR / "app.py").read_text()
    poller = source.split("def _poll_scheduled_tasks():", 1)[1].split("task_thread =", 1)[0]

    assert "task_timeout_seconds=0" not in poller
    assert "run_lease_seconds=0" not in poller
    assert poller.index("while True:") < poller.index("recover_interrupted_work()")
    assert 'os.environ.get("WERKZEUG_RUN_MAIN") == "true"' in source

    import routes.tasks as tasks

    assert tasks.TASK_RECOVERY_TIMEOUT_SECONDS > tasks.TASK_TIMEOUT_SECONDS


def test_hermes_adapter_never_treats_agent_as_skill(monkeypatch):
    import hermes_adapter

    command = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kwargs):
        command["args"] = args
        return Result()

    monkeypatch.setattr(hermes_adapter.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["hermes_adapter.py", "--print", "--output-format", "json", "--agent", "clawdia-assistant", "--profile", "operator", "hello"])
    with pytest.raises(SystemExit) as exit_info:
        hermes_adapter.main()
    assert exit_info.value.code == 0
    assert "--skills" not in command["args"]
    assert command["args"][:3] == ["hermes", "-p", "operator"]


def test_update_validation_rejects_invalid_mutable_type(app):
    import routes.tasks as tasks
    importlib.reload(tasks)

    with app.test_request_context(json={"type": "invalid"}):
        _, error = tasks._validate_task_data({"type": "invalid"}, partial=True)
    assert error[1] == 400


def test_skill_prompt_and_script_types_are_valid(app):
    import routes.tasks as tasks
    importlib.reload(tasks)

    for task_type in ("skill", "prompt", "script"):
        with app.test_request_context(json={}):
            _, error = tasks._validate_task_data({"name": "t", "type": task_type, "payload": "x", "scheduled_at": "2026-07-25T10:00:00Z"})
        assert error is None


def test_cancel_signals_running_process_group(monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)

    class Process:
        def poll(self):
            return None

        pid = 123

        def wait(self, timeout):
            return None

    process = Process()
    tasks._RUNNING_PROCESSES[7] = process
    calls = []
    monkeypatch.setattr(tasks.os, "getpgid", lambda pid: 42)
    monkeypatch.setattr(tasks.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    assert tasks.cancel_task_process(7) is True
    assert calls == [(42, tasks.signal.SIGTERM)]


def test_approval_gate_rejects_pending_or_rejected(app):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRunApproval, db

    with app.app_context():
        db.session.add(RuntimeRunApproval(id="approval", run_id="run", action_hash="x", status="pending"))
        db.session.commit()
        assert tasks._requirements_met("run") is False
        RuntimeRunApproval.query.get("approval").status = "rejected"
        db.session.commit()
        assert tasks._requirements_met("run") is False


def test_cancel_requested_wins_over_successful_worker_completion(app):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, transition

    task_id = make_task(app, status="running", started_at=datetime.now(timezone.utc))
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        run = create_run(task_id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        transition(run, "cancel_requested")

        assert tasks._finish_owned_execution(
            task_id,
            run,
            run.attempt,
            task_status="completed",
            run_status="succeeded",
            summary="late success",
            exit_code=0,
        ) is True

        db.session.expire_all()
        assert db.session.get(ScheduledTask, task_id).status == "cancelled"
        assert db.session.get(RuntimeRun, run.id).status == "cancelled"


def test_pending_approval_atomically_blocks_successful_finalization(app):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, ScheduledTask, db
    from runtime_runs import create_run, request_approval, transition

    task_id = make_task(app, status="running", started_at=datetime.now(timezone.utc))
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        run = create_run(task_id)
        task.runtime_run_id = run.id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        request_approval(run, "publish")

        assert tasks._finish_owned_execution(
            task_id,
            run,
            run.attempt,
            task_status="completed",
            run_status="succeeded",
            summary="must wait",
            exit_code=0,
        ) is False

        db.session.expire_all()
        assert db.session.get(ScheduledTask, task_id).status == "running"
        assert db.session.get(RuntimeRun, run.id).status == "awaiting_approval"


def test_rejected_approval_cancels_linked_task_run_and_process(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)
    from models import RuntimeRun, RuntimeRunApproval, ScheduledTask, db
    from runtime_runs import create_run, decide_approval, request_approval, transition

    task_id = make_task(app, status="running", started_at=datetime.now(timezone.utc))
    process_killed = []

    class Process:
        pid = 12347

    monkeypatch.setattr(tasks, "_kill_process_group", lambda process: process_killed.append(process.pid))
    with app.app_context():
        task = db.session.get(ScheduledTask, task_id)
        run = create_run(task_id)
        run_id = run.id
        task.runtime_run_id = run_id
        task.attempt = run.attempt
        db.session.commit()
        transition(run, "running")
        approval = request_approval(run, "publish")
        approval_id = approval.id
        tasks._RUNNING_PROCESSES[task_id] = Process()
        decide_approval(run, approval, approved=False, actor="reviewer")

        db.session.expire_all()
        assert db.session.get(ScheduledTask, task_id).status == "cancelled"
        assert db.session.get(RuntimeRun, run_id).status == "cancelled"
        assert db.session.get(RuntimeRunApproval, approval_id).status == "rejected"
        assert process_killed == [12347]


def test_script_task_does_not_resolve_provider(app, monkeypatch):
    import routes.tasks as tasks
    importlib.reload(tasks)

    task_id = make_task(app, type="script", payload="missing.py")
    monkeypatch.setattr(tasks, "_resolve_task_runtime", lambda task: (_ for _ in ()).throw(AssertionError("provider should not resolve")))
    with app.app_context():
        tasks._execute_task(task_id)
        from models import ScheduledTask
        assert ScheduledTask.query.get(task_id).status == "failed"
