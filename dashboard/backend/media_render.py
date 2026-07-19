"""Deterministic HyperFrames render step.

Runs directly on the worker (never delegated back to the agent) — briefing
Etapa 6: "A renderização final e a validação podem ser executadas pelo
worker depois que o OpenCode terminar, para que etapas críticas sejam
determinísticas." CLI flags below are taken verbatim from the HyperFrames
CLI reference (`npx hyperframes render --help`; see
`.claude/skills/hyperframes-cli/SKILL.md`), not guessed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

VALID_FPS = (24, 30, 60)
VALID_QUALITY = ("draft", "standard", "high")


class RenderError(Exception):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def _resolve_hyperframes_bin(project_dir: Path) -> str:
    """Prefer the project-local install (deterministic, pinned via
    package.json/lockfile — briefing Etapa 4) over a bare `hyperframes` on
    PATH.
    """
    local_bin = project_dir / "node_modules" / ".bin" / "hyperframes"
    if local_bin.is_file():
        return str(local_bin)
    global_bin = shutil.which("hyperframes")
    if global_bin:
        return global_bin
    raise RenderError("Binário hyperframes não encontrado (nem local em node_modules/.bin, nem no PATH).")


def render_composition(
    project_dir: Path,
    output_path: Path,
    *,
    fps: int,
    quality: str = "high",
    format_: str = "mp4",
    timeout_seconds: int = 900,
    strict: bool = True,
) -> dict:
    project_dir = Path(project_dir).resolve()
    output_path = Path(output_path).resolve()
    if fps not in VALID_FPS:
        raise ValueError(f"fps inválido para HyperFrames: {fps!r} (aceita {VALID_FPS})")
    if quality not in VALID_QUALITY:
        raise ValueError(f"quality inválida: {quality!r} (aceita {VALID_QUALITY})")

    binary = _resolve_hyperframes_bin(project_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        binary, "render",
        "--output", str(output_path),
        "--fps", str(fps),
        "--quality", quality,
        "--format", format_,
    ]
    if strict:
        cmd.append("--strict")

    try:
        proc = subprocess.run(
            cmd, cwd=str(project_dir), capture_output=True, text=True, timeout=timeout_seconds
        )
    except subprocess.TimeoutExpired as exc:
        log = (exc.stdout or "") + (exc.stderr or "")
        raise RenderError(f"hyperframes render excedeu {timeout_seconds}s", log=log) from None

    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        raise RenderError(f"hyperframes render saiu com código {proc.returncode}", log=log)
    if not output_path.is_file():
        raise RenderError("hyperframes render retornou código 0 mas não produziu o arquivo de saída.", log=log)
    return {"log": log, "output_path": str(output_path)}


def run_doctor(cwd: Path, timeout_seconds: int = 60) -> dict:
    """`hyperframes doctor` — environment diagnostic (Chrome/FFmpeg/Node/memory).
    Used both by the Docker image build-time test (Etapa 4) and by the
    worker's own startup health check.
    """
    binary = _resolve_hyperframes_bin(Path(cwd))
    try:
        proc = subprocess.run(
            [binary, "doctor"], cwd=str(cwd), capture_output=True, text=True, timeout=timeout_seconds
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "log": (exc.stdout or "") + (exc.stderr or "")}
    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "log": log}
