"""Brain Repo — File watcher with debounce for auto-sync."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)

DEBOUNCE_SECONDS_DEFAULT = 30

WATCH_PATHS = [
    "memory",
    "workspace",
    "customizations",
    "config-safe",
]

EXCLUDE_PATHS = [
    "memory/raw-transcripts",
]


class _ChangeHandler(FileSystemEventHandler):
    """Watchdog event handler that debounces filesystem events."""

    def __init__(
        self,
        install_dir: Path,
        sync_fn: Callable[[], None],
        debounce_seconds: int,
    ) -> None:
        super().__init__()
        self._install_dir = install_dir
        self._sync_fn = sync_fn
        self._debounce = debounce_seconds
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_any_event(self, event) -> None:
        self._on_change(event)

    def _on_change(self, event) -> None:
        # Ignore directory events (we only care about file changes)
        if event.is_directory:
            return

        # Check exclude paths
        src = str(getattr(event, "src_path", ""))
        for excl in EXCLUDE_PATHS:
            excl_abs = str(self._install_dir / excl)
            if src.startswith(excl_abs):
                log.debug("watcher: ignoring excluded path %s", src)
                return

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        log.info("watcher: debounce elapsed, triggering sync")
        try:
            self._sync_fn()
        except Exception as exc:
            log.error("watcher: sync_fn raised exception: %s", exc)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class BrainRepoWatcher:
    """Watches brain repo directories and triggers sync_fn after debounce."""

    def __init__(
        self,
        install_dir: Path,
        brain_repo_dir: Path,
        sync_fn: Callable[[], None],
        debounce_seconds: int = DEBOUNCE_SECONDS_DEFAULT,
    ) -> None:
        self._install_dir = install_dir
        self._brain_repo_dir = brain_repo_dir
        self._sync_fn = sync_fn
        self._debounce = debounce_seconds
        self._observer: Observer | None = None
        self._handler: _ChangeHandler | None = None

    def start(self) -> None:
        """Start watching. Only observes paths that exist."""
        self._handler = _ChangeHandler(
            self._install_dir,
            self._sync_fn,
            self._debounce,
        )
        self._observer = Observer()

        watched_any = False
        for rel_path in WATCH_PATHS:
            watch_target = self._brain_repo_dir / rel_path
            if watch_target.exists():
                self._observer.schedule(
                    self._handler,
                    str(watch_target),
                    recursive=True,
                )
                log.debug("watcher: watching %s", watch_target)
                watched_any = True
            else:
                log.debug("watcher: skipping non-existent path %s", watch_target)

        if watched_any:
            self._observer.start()
            log.info("BrainRepoWatcher started (debounce=%ds)", self._debounce)
        else:
            log.warning("BrainRepoWatcher: no valid watch paths found, not started")

    def stop(self) -> None:
        """Stop the observer gracefully."""
        if self._handler is not None:
            self._handler.cancel()

        if self._observer is not None and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
            log.info("BrainRepoWatcher stopped")


def start_brain_watcher(install_dir: Path, flask_app=None) -> "BrainRepoWatcher | None":
    """Create and start a BrainRepoWatcher if sync is enabled.

    Reads BrainRepoConfig from the database (requires Flask app context).
    Returns None if no enabled config is found.

    Args:
        install_dir: workspace root — what the watcher observes.
        flask_app: Flask app instance for DB queries. MUST be passed from
            app.py after ``db.init_app(app)``. The old code used
            ``from app import app`` which hit a circular import with a
            half-initialised SQLAlchemy, producing the startup warning
            "current Flask app is not registered with this 'SQLAlchemy'
            instance" — and the watcher silently gave up. Passing ``app``
            explicitly avoids the circular dependency.
    """
    try:
        if flask_app is None:
            # Legacy fallback for callers that haven't been updated yet.
            # Emits a clear warning so the issue is visible in logs.
            try:
                from app import app as flask_app  # type: ignore[import]
            except Exception as exc:
                log.error(
                    "start_brain_watcher: flask_app not provided and fallback import failed: %s",
                    exc,
                )
                return None

        from models import BrainRepoConfig  # type: ignore[import]
        from brain_repo.github_oauth import get_master_key  # type: ignore[import]

        with flask_app.app_context():
            config = BrainRepoConfig.query.filter_by(sync_enabled=True).first()
            if config is None:
                log.info("start_brain_watcher: no enabled brain repo config found")
                return None

            if not config.local_path:
                log.warning("start_brain_watcher: config has no local_path")
                return None

            brain_repo_dir = Path(config.local_path)

            if not config.github_token_encrypted:
                log.warning("start_brain_watcher: no encrypted token in config")
                return None

            # Crypto-readiness guard. Without a master key every enqueue_sync
            # call below would fail on token decryption and the watcher would
            # log the same error every 30 s forever. Better to refuse to start
            # and surface the configuration problem at boot.
            if not get_master_key():
                log.critical(
                    "start_brain_watcher: BRAIN_REPO_MASTER_KEY missing — refusing "
                    "to start watcher (stored tokens cannot be decrypted)",
                )
                return None

            # Capture user_id for the closure; the job runner re-loads the
            # config inside each job to pick up fresh state (e.g. a disconnect
            # that happened after start but before the first debounce fired).
            _user_id = config.user_id

            def _sync_fn() -> None:
                """Enqueue a sync through the job runner.

                Earlier versions of this callback ran ``git commit`` + ``git
                push`` inline, which caused two problems:
                  1. it raced with manual sync_force / tag_milestone (no lock),
                     so tags created by the manual path sometimes never got
                     pushed because the watcher commit overwrote the working
                     tree in between.
                  2. it bypassed the mirror-exclusion rules (``workspace/projects``
                     etc.), re-copying multi-GB git repos on every file change.

                Now the watcher funnels everything through ``job_runner.enqueue_sync``
                which acquires the global lock, applies exclusions, and handles
                cancel requests uniformly. If a job is already running,
                ``enqueue_sync`` returns False and we just skip this tick —
                the next debounce (or the watcher on the next change) retries.
                """
                try:
                    from brain_repo import job_runner
                except ImportError as exc:
                    log.warning("watcher _sync_fn: job_runner unavailable: %s", exc)
                    return

                enqueued = job_runner.enqueue_sync(
                    flask_app,
                    _user_id,
                    install_dir,
                    kind=job_runner.JOB_KIND_WATCHER,
                    commit_message="auto: file watcher sync",
                )
                if not enqueued:
                    log.debug(
                        "watcher _sync_fn: another job is running, skipping this tick",
                    )

            watcher = BrainRepoWatcher(
                install_dir=install_dir,
                brain_repo_dir=brain_repo_dir,
                sync_fn=_sync_fn,
            )
            watcher.start()
            return watcher

    except Exception as exc:
        log.error("start_brain_watcher failed: %s", exc)
        return None
