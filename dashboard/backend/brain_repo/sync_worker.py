"""Brain Repo — Background sync worker with retry queue."""

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

RETRY_INTERVAL_SECONDS = 60
PENDING_DIR_NAME = "brain-pending"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncWorker:
    """Background worker that retries pending commit+push jobs."""

    def __init__(
        self,
        install_dir: Path,
        brain_repo_dir: Path,
        token_fn: Callable[[], str],
    ) -> None:
        self._install_dir = install_dir
        self._brain_repo_dir = brain_repo_dir
        self._token_fn = token_fn
        self._pending_dir = install_dir.parent / PENDING_DIR_NAME
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, job_type: str = "commit_push") -> None:
        """Write a new pending job JSON file to the pending directory."""
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "type": job_type,
            "created_at": _now_iso(),
            "attempts": 0,
            "last_error": None,
        }
        job_path = self._pending_dir / f"{job_id}.json"
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        log.debug("sync_worker: enqueued job %s (%s)", job_id, job_type)

    def process_pending(self) -> int:
        """Process all pending job files. Returns count of attempted jobs."""
        job_files = sorted(self._pending_dir.glob("*.json"))
        if not job_files:
            return 0

        processed = 0
        for job_path in job_files:
            try:
                success = self._execute_job(job_path)
                if success:
                    processed += 1
            except Exception as exc:
                log.error("sync_worker: error processing %s: %s", job_path.name, exc)

        return processed

    def start(self) -> None:
        """Start background daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            log.warning("sync_worker: already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="BrainSyncWorker",
            daemon=True,
        )
        self._thread.start()
        log.info("SyncWorker started (retry interval=%ds)", RETRY_INTERVAL_SECONDS)

    def stop(self) -> None:
        """Signal the worker thread to stop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        log.info("SyncWorker stopped")

    def update_badge(self, user_id: int, count: int) -> None:
        """Update BrainRepoConfig.pending_count via Flask app context."""
        try:
            from app import app  # type: ignore[import]
            from models import BrainRepoConfig, db  # type: ignore[import]

            with app.app_context():
                config = BrainRepoConfig.query.filter_by(user_id=user_id).first()
                if config is not None:
                    config.pending_count = count
                    db.session.commit()
        except Exception as exc:
            log.warning("sync_worker update_badge failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _execute_job(self, job_path: Path) -> bool:
        """Try to commit+push. Remove job file on success. Return True on success."""
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("sync_worker: could not read job file %s: %s", job_path, exc)
            return False

        job["attempts"] = job.get("attempts", 0) + 1

        try:
            import brain_repo.git_ops as git_ops  # type: ignore[import]

            token = self._token_fn()
            git_ops.commit_all(self._brain_repo_dir, "auto: sync worker retry")
            success, push_err = git_ops.push(self._brain_repo_dir, token, with_tags=True)
            if success:
                job_path.unlink(missing_ok=True)
                log.info("sync_worker: job %s succeeded", job.get("id"))
                return True
            else:
                job["last_error"] = push_err or "push returned False"
                job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
                return False
        except Exception as exc:
            job["last_error"] = str(exc)[:300]
            job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
            log.warning("sync_worker: job %s failed: %s", job.get("id"), exc)
            return False

    def _loop(self) -> None:
        """Main retry loop. Runs until stop() is called."""
        while not self._stop_event.is_set():
            try:
                count = self.process_pending()
                if count:
                    log.debug("sync_worker: processed %d pending jobs", count)
            except Exception as exc:
                log.error("sync_worker: loop error: %s", exc)

            self._stop_event.wait(timeout=RETRY_INTERVAL_SECONDS)
