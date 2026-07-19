"""MemPalace integration — optional semantic knowledge base."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

from routes._helpers import WORKSPACE
from routes.auth_routes import require_permission

bp = Blueprint("mempalace", __name__)

PALACE_DIR = WORKSPACE / "dashboard" / "data" / "mempalace"
SOURCES_FILE = PALACE_DIR / "sources.json"
MINING_STATUS_FILE = PALACE_DIR / "mining_status.json"


# ── Helpers ──────────────────────────────────────────────

def _mempalace_available():
    """Check if mempalace is installed. Returns (installed, version)."""
    try:
        import mempalace  # noqa: F401
        return True, getattr(mempalace, "__version__", "unknown")
    except ImportError:
        return False, None


# Fontes seedadas no primeiro uso — as memórias do workspace e dos agentes
# são o motivo de existir da base (recall semântico após /clear). O usuário
# pode remover/adicionar pela UI normalmente; o seed só acontece quando
# sources.json ainda não existe.
DEFAULT_SOURCES = [
    ("memory", "Memória do workspace", "memoria"),
    (".claude/agent-memory", "Memória dos agentes", "agentes"),
    ("workspace/development", "Artefatos de desenvolvimento", "desenvolvimento"),
]


def _seed_default_sources():
    now = datetime.now(timezone.utc).isoformat()
    sources = []
    for rel, label, wing in DEFAULT_SOURCES:
        path = (WORKSPACE / rel).resolve()
        if path.is_dir():
            sources.append({
                "path": str(path),
                "label": label,
                "wing": wing,
                "added_at": now,
                "last_indexed": None,
            })
    if sources:
        _save_sources(sources)
    return sources


def _load_sources():
    if not SOURCES_FILE.exists():
        return _seed_default_sources()
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_sources(sources):
    PALACE_DIR.mkdir(parents=True, exist_ok=True)
    SOURCES_FILE.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_mining_status():
    try:
        status = json.loads(MINING_STATUS_FILE.read_text(encoding="utf-8"))
        pid = status.get("pid")
        # O worker publica phase="done" antes de sair — o check de PID sozinho
        # não basta: o child exitado vira zumbi (ninguém dá wait()) e
        # os.kill(pid, 0) segue passando, prendendo a UI em "in progress".
        if status.get("phase") in ("done", "error"):
            if pid:
                try:
                    os.waitpid(pid, os.WNOHANG)  # reap do zumbi (best-effort)
                except (OSError, ChildProcessError):
                    pass
            MINING_STATUS_FILE.unlink(missing_ok=True)
            return None
        if pid:
            try:
                os.kill(pid, 0)
                return status
            except OSError:
                # Process finished
                MINING_STATUS_FILE.unlink(missing_ok=True)
                return None
        return None
    except Exception:
        return None


def _set_mining_status(status):
    PALACE_DIR.mkdir(parents=True, exist_ok=True)
    MINING_STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")


def _get_palace_stats():
    """Get stats from mempalace if available."""
    try:
        import chromadb
        db_path = PALACE_DIR / "chroma"
        if not db_path.exists():
            # Try palace dir directly
            db_path = PALACE_DIR
        client = chromadb.PersistentClient(path=str(db_path))
        try:
            collection = client.get_collection("mempalace_drawers")
        except Exception:
            return {"total_drawers": 0, "wings": [], "rooms": []}

        count = collection.count()
        if count == 0:
            return {"total_drawers": 0, "wings": [], "rooms": []}

        results = collection.get(limit=min(count, 10000), include=["metadatas"])
        wings = set()
        rooms = set()
        for meta in (results.get("metadatas") or []):
            if meta:
                if meta.get("wing"):
                    wings.add(meta["wing"])
                if meta.get("room"):
                    rooms.add(meta["room"])
        return {
            "total_drawers": count,
            "wings": sorted(wings),
            "rooms": sorted(rooms),
        }
    except Exception:
        return None


# ── Endpoints ────────────────────────────────────────────

@bp.route("/api/mempalace/status")
@require_permission("mempalace", "view")
def status():
    installed, version = _mempalace_available()
    sources = _load_sources()
    mining = _get_mining_status()

    result = {
        "installed": installed,
        "version": version,
        "palace_path": str(PALACE_DIR),
        "stats": None,
        "sources_count": len(sources),
        "mining": mining,
    }

    if installed:
        result["stats"] = _get_palace_stats()

    return jsonify(result)


@bp.route("/api/mempalace/install", methods=["POST"])
@require_permission("mempalace", "manage")
def install():
    installed, _ = _mempalace_available()
    if installed:
        return jsonify({"status": "already_installed"})

    try:
        import shutil
        # Prefer uv if available, fallback to pip
        uv_bin = shutil.which("uv")
        if uv_bin:
            cmd = [uv_bin, "pip", "install", "mempalace"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "mempalace"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            PALACE_DIR.mkdir(parents=True, exist_ok=True)
            # Auto-init the palace so MCP and CLI work immediately
            subprocess.run(
                [sys.executable, "-m", "mempalace", "init", str(PALACE_DIR)],
                capture_output=True, timeout=30,
            )
            return jsonify({"status": "installed"})
        return jsonify({"status": "error", "detail": result.stderr}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "detail": "Installation timed out"}), 500
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@bp.route("/api/mempalace/sources")
@require_permission("mempalace", "view")
def list_sources():
    return jsonify({"sources": _load_sources()})


@bp.route("/api/mempalace/sources", methods=["POST"])
@require_permission("mempalace", "manage")
def add_source():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "").strip()
    label = data.get("label", "").strip()
    wing = data.get("wing", "").strip() or None
    room = data.get("room", "").strip() or None

    if not path:
        return jsonify({"error": "path is required"}), 400

    resolved = Path(path).resolve()
    if not resolved.is_dir():
        return jsonify({"error": f"Directory not found: {path}"}), 400

    # Block paths outside home directory or workspace
    home = Path.home().resolve()
    if not (str(resolved).startswith(str(home)) or str(resolved).startswith(str(WORKSPACE.resolve()))):
        return jsonify({"error": "Source path must be within home directory or workspace"}), 400

    sources = _load_sources()

    # Check for duplicate
    if any(s["path"] == str(resolved) for s in sources):
        return jsonify({"error": "Source already exists"}), 409

    sources.append({
        "path": str(resolved),
        "label": label or resolved.name,
        "wing": wing,
        "room": room,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "last_indexed": None,
    })
    _save_sources(sources)
    return jsonify({"status": "added", "sources": sources}), 201


def sync_project_source(*, workspace_folder_path: str, mission_title: str | None, project_slug: str) -> None:
    """Upsert a MemPalace source for a Project's workspace folder.

    Hierarchy mapping (confirmed with Felipe): Mission -> Wing, Project ->
    Room. Called from routes/goals.py whenever a Project is created/updated
    with a non-null workspace_folder_path — mirrors how goal_created wakes
    goal-planner, but here it's a plain upsert, not an agent trigger.

    Best-effort: MemPalace being uninstalled, the path not existing yet, or
    any other failure must never block project creation — this is purely
    additive bookkeeping for semantic search, not part of the goals data
    model's correctness.
    """
    try:
        resolved = Path(workspace_folder_path).expanduser().resolve()
        if not resolved.is_dir():
            return
        wing = (mission_title or "sem-missao").strip() or "sem-missao"
        room = project_slug.strip()
        if not room:
            return

        sources = _load_sources()
        found = False
        for s in sources:
            if s["path"] == str(resolved):
                s["wing"] = wing
                s["room"] = room
                found = True
        if not found:
            sources.append({
                "path": str(resolved),
                "label": room,
                "wing": wing,
                "room": room,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "last_indexed": None,
            })
        _save_sources(sources)

        # ensure_mempalace_yaml (the worker) only writes rooms/wing when the
        # folder has NO mempalace.yaml yet — a no-op for a folder mined
        # before this sync existed. Patch the still-untouched auto-generated
        # default ("general", the only room) so re-mining routes content to
        # the Project's own room without clobbering a hand-customized config
        # (multiple rooms, or a room already renamed by the user).
        yaml_path = resolved / "mempalace.yaml"
        if yaml_path.exists():
            try:
                import yaml as _yaml
                cfg = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                rooms = cfg.get("rooms") or []
                if len(rooms) == 1 and rooms[0].get("name") == "general":
                    cfg["wing"] = wing
                    cfg["rooms"] = [{"name": room, "description": "All project files"}]
                    yaml_path.write_text(_yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


@bp.route("/api/mempalace/sources/<int:idx>", methods=["DELETE"])
@require_permission("mempalace", "manage")
def delete_source(idx):
    sources = _load_sources()
    if idx < 0 or idx >= len(sources):
        return jsonify({"error": "Invalid source index"}), 404
    removed = sources.pop(idx)
    _save_sources(sources)
    return jsonify({"status": "removed", "removed": removed, "sources": sources})


@bp.route("/api/mempalace/mine", methods=["POST"])
@require_permission("mempalace", "manage")
def mine():
    installed, _ = _mempalace_available()
    if not installed:
        return jsonify({"error": "MemPalace is not installed"}), 400

    # Check if mining is already running
    if _get_mining_status():
        return jsonify({"error": "Mining already in progress"}), 409

    data = request.get_json(silent=True) or {}
    source_index = data.get("source_index")  # None = all sources

    sources = _load_sources()
    if not sources:
        return jsonify({"error": "No sources configured"}), 400

    if source_index is not None:
        if source_index < 0 or source_index >= len(sources):
            return jsonify({"error": "Invalid source index"}), 404
        targets = [sources[source_index]]
    else:
        targets = sources

    # Spawn the mining worker as a detached subprocess. The worker publishes
    # per-file progress to MINING_STATUS_FILE so the dashboard can render a
    # real progress bar + ETA instead of a spinner.
    PALACE_DIR.mkdir(parents=True, exist_ok=True)

    worker_path = Path(__file__).parent / "_mempalace_worker.py"
    worker_payload = {
        "palace_path": str(PALACE_DIR),
        "status_file": str(MINING_STATUS_FILE),
        "targets": [
            {"path": t["path"], "wing": t.get("wing") or None, "room": t.get("room") or None}
            for t in targets
        ],
    }

    # Seed the status file BEFORE spawning so the UI shows "scanning..."
    # instantly instead of a gap while the worker boots.
    _set_mining_status({
        "pid": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "phase": "scanning",
        "sources": [t["path"] for t in targets],
        "current_file": None,
        "current_source": None,
        "files_done": 0,
        "files_total": 0,
        "files_skipped": 0,
        "drawers_added": 0,
        "elapsed_seconds": 0,
        "eta_seconds": None,
        "rate_files_per_sec": 0,
    })

    cmd = [sys.executable, str(worker_path)]
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(worker_payload).encode("utf-8"))
    process.stdin.close()

    # Patch the seed status with the real pid so /status PID-check works
    # immediately. The worker will overwrite this on its first publish().
    seed = json.loads(MINING_STATUS_FILE.read_text(encoding="utf-8"))
    seed["pid"] = process.pid
    _set_mining_status(seed)

    # Update last_indexed for the targeted sources
    now = datetime.now(timezone.utc).isoformat()
    target_paths = {t["path"] for t in targets}
    for s in sources:
        if s["path"] in target_paths:
            s["last_indexed"] = now
    _save_sources(sources)

    return jsonify({"status": "started", "pid": process.pid})


@bp.route("/api/mempalace/search")
@require_permission("mempalace", "view")
def search():
    installed, _ = _mempalace_available()
    if not installed:
        return jsonify({"error": "MemPalace is not installed"}), 400

    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    wing = request.args.get("wing", "").strip() or None
    room = request.args.get("room", "").strip() or None
    n = min(int(request.args.get("n", 10)), 50)

    try:
        from mempalace.searcher import search_memories
        results = search_memories(
            query=q,
            palace_path=str(PALACE_DIR),
            wing=wing,
            room=room,
            n_results=n,
        )
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
