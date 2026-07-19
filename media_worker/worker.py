#!/usr/bin/env python3
"""media-worker — polling loop that claims queued MediaJobs via the dashboard
API and runs the CPU-heavy pipeline (OpenCode composition -> HyperFrames
render -> ffprobe validation) locally, in this isolated container.

Never touches the SQLite DB directly. Like the scheduler/telegram services,
it talks to the dashboard exclusively through DASHBOARD_API_TOKEN +
/api/media/jobs/* (see dashboard/backend/routes/media_jobs.py) — a single
source of truth for state transitions (the state machine is enforced
server-side), matching the existing service-to-service pattern in this
project (no service other than the dashboard itself mounts the SQLite
volume).

Concurrency: exactly one job in flight per process (briefing Etapa 3), and
`replicas: 1` at the Swarm level makes that also the cluster-wide ceiling.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sdk_client import evo  # noqa: E402
from media_executor import run_opencode_media_job, MediaExecutionError  # noqa: E402
from media_render import render_composition, run_doctor, RenderError  # noqa: E402
from media_validation import validate_mp4, ValidationError  # noqa: E402
from media_manifest import load_and_validate_manifest, ManifestValidationError  # noqa: E402
from media_workspace import job_dir, media_workspace_root, sha256_file  # noqa: E402

POLL_SECONDS = float(os.environ.get("MEDIA_WORKER_POLL_SECONDS", "10"))
JOB_TIMEOUT_SECONDS = int(os.environ.get("MEDIA_JOB_TIMEOUT_SECONDS", "3600"))
MAX_FILE_SIZE_BYTES = int(os.environ.get("MEDIA_MAX_FILE_SIZE_BYTES", str(1024 * 1024 * 1024)))
HEARTBEAT_FILE = Path(os.environ.get("MEDIA_WORKER_HEARTBEAT_FILE", "/tmp/media-worker.alive"))
AGENT_NAME = "media-worker"


def _touch_heartbeat() -> None:
    try:
        HEARTBEAT_FILE.write_text(str(time.time()))
    except OSError:
        pass


def _patch(job_id: str, **fields) -> dict:
    return evo.patch(f"/api/media/jobs/{job_id}", fields)


def _fail_job(job_id: str, error: str, terminal: bool = False) -> None:
    status = "failed" if terminal else "retryable_failure"
    try:
        _patch(job_id, status=status, last_error=error[:4000])
    except Exception:
        print(f"[media-worker] could not report failure for {job_id}: {traceback.format_exc()}", flush=True)


def _claim_next_job() -> dict | None:
    """Try queued jobs first, then jobs eligible for automatic retry.
    `/run` is the atomic claim (mirrors tickets.checkout_ticket) — if another
    process (or a human clicking "Iniciar") wins the race, `/run` 409s and we
    just move on to the next candidate.
    """
    for status in ("queued", "retryable_failure"):
        try:
            jobs = evo.get("/api/media/jobs", params={"status": status, "limit": 5})
        except Exception:
            print(f"[media-worker] failed to list {status} jobs: {traceback.format_exc()}", flush=True)
            continue
        for job in jobs or []:
            try:
                return evo.post(f"/api/media/jobs/{job['id']}/run", {"agent": AGENT_NAME})
            except Exception:
                continue
    return None


def process_job(job: dict) -> None:
    job_id = job["id"]
    base = job_dir(job_id)
    print(f"[media-worker] processing job {job_id} ({job.get('platform')}, {job.get('width')}x{job.get('height')})", flush=True)

    # ── 1. Composition via OpenCode/OmniRoute (never a direct model call) ──
    _patch(job_id, status="generating")
    try:
        run_opencode_media_job(base, timeout_seconds=min(JOB_TIMEOUT_SECONDS, 1800))
    except MediaExecutionError as exc:
        try:
            (base / "logs" / "opencode.ndjson").write_text(
                json.dumps(exc.result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
        _fail_job(job_id, f"Composição falhou: {exc}")
        return

    # ── 2. Schema-validate the manifest the agent produced ─────────────
    try:
        load_and_validate_manifest(base)
    except ManifestValidationError as exc:
        _fail_job(job_id, f"Manifesto inválido: {exc}")
        return

    # ── 3. Deterministic render — worker-driven, never the agent ───────
    _patch(job_id, status="rendering")
    output_path = base / "output" / "final.mp4"
    try:
        render_composition(
            base / "project", output_path,
            fps=int(job["fps"]), quality="high", timeout_seconds=min(JOB_TIMEOUT_SECONDS, 1800),
        )
    except RenderError as exc:
        try:
            (base / "logs" / "render.log").write_text(exc.log, encoding="utf-8")
        except OSError:
            pass
        _fail_job(job_id, f"Render falhou: {exc}")
        return

    # ── 4. ffprobe validation — never trust exit 0 alone ────────────────
    _patch(job_id, status="validating")
    try:
        meta = validate_mp4(
            output_path,
            expected_width=int(job["width"]), expected_height=int(job["height"]),
            expected_duration_seconds=float(job["duration_seconds"]), expected_fps=int(job["fps"]),
            max_size_bytes=MAX_FILE_SIZE_BYTES, workspace_root=media_workspace_root(),
        )
    except ValidationError as exc:
        _fail_job(job_id, f"Validação falhou: {exc}")
        return

    checksum = sha256_file(output_path)
    _patch(job_id, status="ready_for_review", render_path=str(output_path), render_sha256=checksum, **meta.to_dict())
    print(f"[media-worker] job {job_id} ready_for_review (sha256={checksum[:12]}...)", flush=True)


def main() -> None:
    print(f"[media-worker] started, polling every {POLL_SECONDS}s (job timeout {JOB_TIMEOUT_SECONDS}s)", flush=True)
    doctor = run_doctor(Path.cwd())
    print(f"[media-worker] hyperframes doctor ok={doctor['ok']}", flush=True)
    while True:
        _touch_heartbeat()
        try:
            job = _claim_next_job()
        except Exception:
            print(f"[media-worker] poll error: {traceback.format_exc()}", flush=True)
            job = None
        if job is None:
            time.sleep(POLL_SECONDS)
            continue
        try:
            process_job(job)
        except Exception:
            print(f"[media-worker] unhandled error processing {job.get('id')}: {traceback.format_exc()}", flush=True)
            try:
                _fail_job(job["id"], f"Erro inesperado: {traceback.format_exc()[-2000:]}")
            except Exception:
                pass


if __name__ == "__main__":
    main()
