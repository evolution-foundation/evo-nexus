"""Flask backend for the workspace dashboard — EvoNexus."""

import os
import sys
import secrets
import re
from pathlib import Path
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, send_from_directory, request, jsonify
from flask_cors import CORS
from flask_login import LoginManager, current_user, login_user

# Workspace root: two levels up from backend/
WORKSPACE = Path(__file__).resolve().parent.parent.parent

# Load .env from workspace root
load_dotenv(WORKSPACE / ".env")

# Add social-auth to path
sys.path.insert(0, str(WORKSPACE / "social-auth"))


def _is_production() -> bool:
    env = (
        os.environ.get("EVONEXUS_ENV")
        or os.environ.get("FLASK_ENV")
        or os.environ.get("ENV")
        or ""
    ).strip().lower()
    return env in {"production", "prod"}


def _cors_allowed_origins():
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
    if raw:
        if raw == "*":
            return "*"
        origins = [origin.strip() for origin in re.split(r"[,\s]+", raw) if origin.strip()]
        return origins or "*"
    return "*" if not _is_production() else []

app = Flask(__name__, static_folder=None)
# Persist secret key so sessions survive restarts
_secret_key = os.environ.get("EVONEXUS_SECRET_KEY")
if not _secret_key:
    if _is_production():
        raise RuntimeError("EVONEXUS_SECRET_KEY must be set in production")
    _key_file = WORKSPACE / "dashboard" / "data" / ".secret_key"
    _key_file.parent.mkdir(parents=True, exist_ok=True)
    if _key_file.exists():
        _secret_key = _key_file.read_text(encoding="utf-8").strip()
    else:
        _secret_key = secrets.token_hex(32)
        _key_file.write_text(_secret_key, encoding="utf-8")
        _key_file.chmod(0o600)

app.secret_key = _secret_key

# Generate BRAIN_REPO_MASTER_KEY if not set (Fernet key for encrypting GitHub tokens)
_brain_key = os.environ.get("BRAIN_REPO_MASTER_KEY")
if not _brain_key:
    _env_file = WORKSPACE / ".env"
    try:
        from cryptography.fernet import Fernet as _Fernet
        _new_brain_key = _Fernet.generate_key().decode()
        _env_lines = _env_file.read_text(encoding="utf-8").splitlines() if _env_file.exists() else []
        _env_lines.append(f"BRAIN_REPO_MASTER_KEY={_new_brain_key}")
        _env_file.write_text("\n".join(_env_lines) + "\n", encoding="utf-8")
        os.environ["BRAIN_REPO_MASTER_KEY"] = _new_brain_key
    except Exception as _bk_exc:
        print(f"WARNING: Could not generate BRAIN_REPO_MASTER_KEY: {_bk_exc}")

# DATABASE_URL is the single source of truth for the backend DB.
# Falls back to the legacy SQLite path so existing deployments see zero behaviour change (AC1).
# Supported: sqlite:///... and postgresql[+psycopg2]://...
_default_db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
_database_url: str = os.environ.get("DATABASE_URL", "") or f"sqlite:///{_default_db_path}"
# Normalise postgres:// shorthand to psycopg2 dialect for SQLAlchemy
if _database_url.startswith("postgres://"):
    _database_url = "postgresql+psycopg2://" + _database_url[len("postgres://"):]
elif _database_url.startswith("postgresql://") and "+psycopg2" not in _database_url:
    _database_url = _database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Pool config — applied only when backend is Postgres (Flask-SQLAlchemy forwards to SQLAlchemy).
# SQLite ignores these.  Values override-able via env vars (ADR PG-Q8).
_is_pg_backend = _database_url.startswith("postgresql")
if _is_pg_backend:
    _pool_size = int(os.environ.get("EVONEXUS_DB_POOL_SIZE", "5"))
    _max_overflow = int(os.environ.get("EVONEXUS_DB_MAX_OVERFLOW", "10"))
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_size": _pool_size,
        "max_overflow": _max_overflow,
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_timeout": 30,
        "connect_args": {"client_encoding": "UTF8"},
    }
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
# SameSite=Strict prevents cross-origin cookie riding (CSRF defense layer 1).
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

# --- JSON encoding for API responses ---
# With ensure_ascii=True (Flask default) jsonify escapes every non-ASCII
# character as \uXXXX. JSON parsers in the browser decode this correctly,
# but it clutters network logs and occasionally breaks naive consumers that
# look at raw bytes (e.g. grep over nginx access logs). Emit real UTF-8 so
# accented content ("João", "Mirandas Leilões") stays readable end-to-end.
try:
    app.json.ensure_ascii = False          # type: ignore[attr-defined]
    app.json.mimetype = "application/json; charset=utf-8"  # type: ignore[attr-defined]
except AttributeError:
    # Flask <2.2 exposed this through app.config; keep compatibility.
    app.config["JSON_AS_ASCII"] = False

CORS(app, origins=_cors_allowed_origins(), supports_credentials=True)

# --------------- Rate limiting (in-memory, single-process Flask) ---------------
# Vault audit §2.S1 CRITICAL: all public endpoints require rate limiting.
# The limiter singleton lives in rate_limit.py to avoid circular imports with blueprints.
from rate_limit import limiter
limiter.init_app(app)

# --------------- Database ---------------
from models import db, User, BrainRepoConfig, needs_setup, seed_roles, seed_systems
db.init_app(app)

# Create tables on first run + enable WAL mode for concurrent reads (SQLite only)
with app.app_context():
    db.create_all()
    if not _is_pg_backend:
        db.session.execute(db.text("PRAGMA journal_mode=WAL"))
        db.session.commit()

    # Schema is managed by Alembic migrations (dashboard/alembic/versions/).
    # On fresh installs: run `alembic upgrade head` from dashboard/alembic/.
    # On legacy SQLite installs: the _stamp_if_legacy() bootstrap in alembic/env.py
    # detects the existing schema and stamps it as revision 0001 automatically,
    # then subsequent migrations (0002+) run incrementally.
    # The executescript() blocks that used to live here have been removed (PG-Q6).

    # --- Migration: providers.json schema normalization ---
    # In SQLite mode: if providers.json exists but is missing the canonical keys
    # ({active_provider, providers: {...}}), copy providers.example.json over it.
    # In PG mode: if llm_providers table is empty and providers.json exists, seed
    # the table from the JSON file (one-shot, idempotent).
    try:
        from config_store import get_dialect as _get_dialect
        if _get_dialect() == "postgresql":
            from provider_store import seed_providers_from_json as _seed_providers
            _seeded = _seed_providers()
            if _seeded:
                print(f"[migration] Seeded {_seeded} provider(s) from providers.json into llm_providers table")
        else:
            _providers_file = WORKSPACE / "config" / "providers.json"
            _providers_example = WORKSPACE / "config" / "providers.example.json"
            if _providers_file.is_file():
                try:
                    import json as _json
                    _data = _json.loads(_providers_file.read_text(encoding="utf-8"))
                    _ok = (
                        isinstance(_data, dict)
                        and "active_provider" in _data
                        and isinstance(_data.get("providers"), dict)
                    )
                except Exception:
                    _ok = False
                if not _ok and _providers_example.is_file():
                    import shutil as _shutil
                    _shutil.copy2(_providers_example, _providers_file)
                    print("[migration] providers.json had invalid schema, restored from providers.example.json")
    except Exception as _mig_exc:
        print(f"[migration] providers.json normalization skipped: {_mig_exc}")
    # --- End providers.json migration ---

    seed_roles()
    seed_systems()
    # Sync trigger definitions from YAML config
    from routes.triggers import sync_triggers_from_yaml
    sync_triggers_from_yaml()

    # Sync heartbeats from YAML + start dispatcher thread
    try:
        from heartbeat_dispatcher import _sync_heartbeats_to_db, start_dispatcher_thread
        _sync_heartbeats_to_db()
        start_dispatcher_thread()
    except Exception as _hb_exc:
        print(f"WARNING: heartbeat dispatcher init failed: {_hb_exc}")

    # Register SQLAlchemy event listeners (observability layer — PG-Q3)
    try:
        from db.listeners import register_all as _register_listeners
        _register_listeners()
    except Exception as _ls_exc:
        print(f"WARNING: db listeners registration failed: {_ls_exc}")

    # Start ticket janitor (auto-release timed-out locks)
    try:
        from ticket_janitor import start_janitor_thread
        start_janitor_thread()
    except Exception as _tj_exc:
        print(f"WARNING: ticket janitor init failed: {_tj_exc}")

    # Start Knowledge pool GC + health check threads
    try:
        from knowledge.connection_pool import start_gc_thread
        from knowledge.health_check import start_health_check_thread
        start_gc_thread()
        start_health_check_thread(lambda: app)
    except Exception as _kn_exc:
        print(f"WARNING: knowledge background threads init failed: {_kn_exc}")

    # Start knowledge usage janitor (delete usage rows > 7 days)
    try:
        from knowledge.usage_janitor import start_janitor_thread as start_usage_janitor
        start_usage_janitor()
    except Exception as _uj_exc:
        print(f"WARNING: knowledge usage janitor init failed: {_uj_exc}")

    # Start knowledge classify worker (async document classification — ADR-008)
    # The worker now uses the shared SQLAlchemy engine; the path arg is legacy
    # (kept positional for backwards-compat).
    try:
        from knowledge.classify_worker import start_classify_worker
        _db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
        _legacy_path = _db_uri.replace("sqlite:///", "") if _db_uri.startswith("sqlite") else ""
        start_classify_worker(_legacy_path)
    except Exception as _cw_exc:
        print(f"WARNING: knowledge classify worker init failed: {_cw_exc}")

    # --- Claude Code hooks bootstrap (plugins-v1a step 8) ---
    # Idempotent: registers dispatcher for 4 v1a events in .claude/settings.json.
    # Plugins are a core feature — no feature flag, runs unconditionally.
    try:
        from claude_hook_bootstrap import run as _bootstrap_hooks
        _bootstrap_hooks()
    except Exception as _hb_exc:
        print(f"WARNING: claude_hook_bootstrap failed: {_hb_exc}")
    # --- End Claude Code hooks bootstrap ---

    # --- Plugin crash recovery (ADR-5) ---
    # Detects orphaned .install-state.json files and rolls back incomplete installs.
    try:
        from plugin_install_state import crash_recovery_on_boot as _crash_recovery
        _plugin_db_path = WORKSPACE / "dashboard" / "data" / "evonexus.db"
        _recovery_log = _crash_recovery(_plugin_db_path)
        if _recovery_log:
            print(f"Plugin crash recovery: {len(_recovery_log)} actions taken")
    except Exception as _cr_exc:
        print(f"WARNING: plugin crash recovery failed: {_cr_exc}")
    # --- End plugin crash recovery ---

    # Cleanup: remove old disabled share records (expired + disabled + older than 30 days)
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from models import FileShare as _FileShare
    _cutoff = _dt.now(_tz.utc) - _td(days=30)
    _FileShare.query.filter(
        _FileShare.enabled == False,  # noqa: E712
        _FileShare.created_at < _cutoff,
    ).delete()
    db.session.commit()

# --------------- Licensing (register-only, no heartbeat) ───
from licensing import auto_register_if_needed

with app.app_context():
    auto_register_if_needed()

# --------------- Login Manager ---------------
login_manager = LoginManager()
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"error": "Authentication required"}), 401

# --------------- Auth Middleware ---------------
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/needs-setup",
    "/api/auth/setup",
    "/api/health",
    "/api/auth/needs-onboarding",
    "/api/config/workspace-status",
    "/api/version",
    "/api/version/check",
    "/api/agents/active",
}

def _try_api_token_auth():
    """Resolve an Authorization: Bearer <token> header against DASHBOARD_API_TOKEN.
    On match, log in the configured service user for the duration of this request.
    Returns True if a valid token was found and applied, False otherwise.
    """
    expected = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    provided = header[len("Bearer "):].strip()
    if not provided or not secrets.compare_digest(provided, expected):
        return False
    # Load service user: DASHBOARD_API_USER env var, defaults to first admin
    service_username = os.environ.get("DASHBOARD_API_USER", "").strip()
    user = None
    if service_username:
        user = User.query.filter_by(username=service_username, is_active=True).first()
    if user is None:
        user = User.query.filter_by(role="admin", is_active=True).order_by(User.id.asc()).first()
    if user is None:
        return False
    # Log in for this request only (no remember cookie)
    login_user(user, remember=False, fresh=False)
    return True


@app.before_request
def auth_middleware():
    path = request.path

    # Static assets and frontend
    if not path.startswith("/api/") and not path.startswith("/ws/"):
        return None

    # WebSocket — auth checked inside the handler
    if path.startswith("/ws/"):
        return None

    # Control API has its own service-token authentication and scopes.
    if path.startswith("/api/control/v1/"):
        return None

    # Public API paths (exact match or prefix match for docs/webhooks/shares)
    if (
        path in PUBLIC_PATHS
        or path.startswith("/api/docs")
        or path.startswith("/api/triggers/webhook/")
        or (path.startswith("/api/shares/") and "/view" in path)
        or path.startswith("/api/knowledge/v1/")
    ):
        return None

    # Setup redirect — if no users, only allow setup endpoints
    if needs_setup():
        if path not in PUBLIC_PATHS:
            return jsonify({"error": "Setup required", "needs_setup": True}), 403

    # Try API token auth first (Bearer header) for headless agents / CLI tools
    if not current_user.is_authenticated:
        if _try_api_token_auth():
            return None

    # Require auth for all other API paths
    if not current_user.is_authenticated:
        return jsonify({"error": "Authentication required"}), 401

# --------------- Register blueprints ---------------
from routes.overview import bp as overview_bp
from routes.workspace import bp as workspace_bp
from routes.agents import bp as agents_bp
from routes.routines import bp as routines_bp
from routes.skills import bp as skills_bp
from routes.templates_routes import bp as templates_bp
from routes.memory import bp as memory_bp
from routes.costs import bp as costs_bp
from routes.config import bp as config_bp
from routes.integrations import bp as integrations_bp
from routes.scheduler import bp as scheduler_bp
from routes.services import bp as services_bp
from routes.auth_routes import bp as auth_bp
from routes.systems import bp as systems_bp
from routes.tailscale import bp as tailscale_bp
from routes.docs import bp as docs_bp
from routes.mempalace import bp as mempalace_bp
from routes.tasks import bp as tasks_bp
from routes.triggers import bp as triggers_bp
from routes.terminal_proxy import bp as terminal_proxy_bp, register_websocket_proxy as _register_terminal_ws
from routes.backups import bp as backups_bp
from routes.providers import bp as providers_bp
from routes.hermes_profiles_routes import bp as hermes_profiles_bp
from routes.control_api import bp as control_api_bp
from routes.runtime_runs import bp as runtime_runs_bp
from routes.hermes_proxy import bp as hermes_proxy_bp, register_websocket_proxy as _register_hermes_ws
from routes.settings import bp as settings_bp
from routes.shares import bp as shares_bp
from routes.heartbeats import bp as heartbeats_bp
from routes.goals import bp as goals_bp
from routes.tickets import bp as tickets_bp
from routes.chat_messages import bp as chat_messages_bp
from routes.health import bp as health_bp
from routes.knowledge import bp as knowledge_bp
from routes.knowledge_public import bp as knowledge_public_bp
from routes.knowledge_proxy import bp as knowledge_proxy_bp
from routes.knowledge_v1 import bp as knowledge_v1_bp
from routes.databases import bp as databases_bp
from routes.plugins import bp as plugins_bp
from routes.mcp_servers import bp as mcp_servers_bp
from routes.plugin_public_pages import bp as plugin_public_pages_bp

# Brain Repo + Onboarding blueprints (loaded after routes are created)
try:
    from routes.onboarding import bp as onboarding_bp
    from routes.brain_repo import bp as brain_repo_bp
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(brain_repo_bp)
except ImportError:
    pass  # Routes not yet created

# Brain Repo watcher startup
# Pass the app instance explicitly to avoid the circular `from app import app`
# that triggered "Flask app is not registered with this 'SQLAlchemy' instance"
# on every boot, leaving auto-sync permanently off.
try:
    from brain_repo.watcher import start_brain_watcher
    start_brain_watcher(WORKSPACE, flask_app=app)
except Exception as _bw_exc:
    pass  # Brain watcher starts only when a brain repo is configured

app.register_blueprint(overview_bp)
app.register_blueprint(workspace_bp)
app.register_blueprint(agents_bp)
app.register_blueprint(routines_bp)
app.register_blueprint(skills_bp)
app.register_blueprint(templates_bp)
app.register_blueprint(memory_bp)
app.register_blueprint(costs_bp)
app.register_blueprint(config_bp)
app.register_blueprint(integrations_bp)
app.register_blueprint(scheduler_bp)
app.register_blueprint(services_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(systems_bp)
app.register_blueprint(tailscale_bp)
app.register_blueprint(docs_bp)
app.register_blueprint(mempalace_bp)
app.register_blueprint(tasks_bp)
app.register_blueprint(triggers_bp)
app.register_blueprint(terminal_proxy_bp)
app.register_blueprint(hermes_proxy_bp)

# Mount the terminal-server WebSocket proxy on the same Sock instance the
# rest of the app uses. Done after the blueprint is registered so route
# names are unique. Without this, browsers connecting from a host other
# than the one running the Node terminal-server (LAN, Tailscale Funnel,
# SSH tunnel without the dynamic port forwarded) cannot reach it directly
# due to CORS preflight + private-network-access policies.
try:
    from flask_sock import Sock as _Sock
    # Serialize all writes on each simple_websocket connection so the bridge
    # pump thread, the bridge main thread (close), and simple-websocket's
    # internal _thread (Pong/Close) cannot interleave frame bytes — the cause
    # of the browser-side "WebSocket: Invalid frame header" on the
    # Hermes/terminal proxies. Must run before the Sock instance is created.
    from ws_send_lock import install as _install_ws_send_lock
    _install_ws_send_lock()
    _terminal_sock = _Sock(app)
    _register_terminal_ws(_terminal_sock)
    # Reuse the same Sock instance for the Hermes chat WebSocket bridge.
    # Without this the Hermes UI chat terminal's pty/ws/events sockets have
    # nothing to upgrade against and close with code 1006.
    _register_hermes_ws(_terminal_sock)
except Exception as _exc:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "terminal_proxy: failed to mount WebSocket proxy: %s — terminal "
        "interactions will require direct access to the terminal-server port.",
        _exc,
    )
app.register_blueprint(backups_bp)
app.register_blueprint(providers_bp)
app.register_blueprint(hermes_profiles_bp)
app.register_blueprint(control_api_bp)
app.register_blueprint(runtime_runs_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(shares_bp)
app.register_blueprint(heartbeats_bp)
app.register_blueprint(goals_bp)
app.register_blueprint(tickets_bp)
app.register_blueprint(chat_messages_bp)
app.register_blueprint(health_bp)
app.register_blueprint(knowledge_bp)
app.register_blueprint(knowledge_public_bp)
app.register_blueprint(knowledge_proxy_bp)
app.register_blueprint(knowledge_v1_bp)
app.register_blueprint(databases_bp)
app.register_blueprint(plugins_bp)
app.register_blueprint(mcp_servers_bp)
# B2.0: plugin public pages (unauthenticated, token-bound portals)
app.register_blueprint(plugin_public_pages_bp)

# --------------- Social Auth blueprints ---------------
from auth.youtube import bp as youtube_auth_bp
from auth.instagram import bp as instagram_auth_bp
from auth.linkedin import bp as linkedin_auth_bp
from auth.twitter import bp as twitter_auth_bp
from auth.tiktok import bp as tiktok_auth_bp
from auth.twitch import bp as twitch_auth_bp

app.register_blueprint(youtube_auth_bp)
app.register_blueprint(instagram_auth_bp)
app.register_blueprint(linkedin_auth_bp)
app.register_blueprint(twitter_auth_bp)
app.register_blueprint(tiktok_auth_bp)
app.register_blueprint(twitch_auth_bp)

def _get_local_version():
    """Read current version from pyproject.toml."""
    try:
        pyproject = WORKSPACE / "pyproject.toml"
        for line in pyproject.read_text().splitlines():
            if line.startswith("version"):
                return line.split('"')[1]
    except Exception:
        pass
    return "unknown"


@app.route("/api/version")
def api_version():
    """Return current version from pyproject.toml."""
    return {"version": _get_local_version()}


@app.route("/api/agents/active")
def api_agents_active():
    """Return currently active agents from hook-generated status file."""
    import json
    status_file = WORKSPACE / ".claude" / "agent-status.json"
    try:
        if status_file.is_file():
            data = json.loads(status_file.read_text())
            # Filter entries older than 10 minutes (stale)
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
            active = []
            for entry in data.get("active_agents", []):
                try:
                    started = datetime.fromisoformat(entry["started_at"].replace("Z", "+00:00"))
                    if started > cutoff:
                        active.append(entry)
                except (KeyError, ValueError):
                    pass
            return {"active_agents": active, "last_updated": data.get("last_updated")}
    except Exception:
        pass
    return {"active_agents": [], "last_updated": None}


# --- Version check with 1h cache ---
_version_cache = {"data": None, "expires": 0}

@app.route("/api/version/check")
def api_version_check():
    """Compare local version against latest GitHub release (cached 1h)."""
    import time
    import requests as http_requests

    now = time.time()
    if _version_cache["data"] and now < _version_cache["expires"]:
        return _version_cache["data"]

    current = _get_local_version()
    result = {
        "current": current,
        "latest": None,
        "update_available": False,
        "release_url": None,
        "release_notes": None,
    }

    try:
        resp = http_requests.get(
            "https://api.github.com/repos/EvolutionAPI/evo-nexus/releases/latest",
            timeout=10,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            latest = data.get("tag_name", "").lstrip("v")
            result["latest"] = latest
            result["release_url"] = data.get("html_url")
            result["release_notes"] = data.get("body", "")[:500]

            # Compare versions (semver-like: major.minor.patch)
            def parse_ver(v):
                try:
                    return tuple(int(x) for x in v.split("."))
                except (ValueError, AttributeError):
                    return (0, 0, 0)

            if parse_ver(latest) > parse_ver(current):
                result["update_available"] = True
    except Exception:
        pass

    _version_cache["data"] = result
    _version_cache["expires"] = now + 3600  # 1 hour
    return result

@app.route("/api/social-accounts")
def social_accounts():
    from env_manager import all_platforms_with_accounts
    return {"platforms": all_platforms_with_accounts()}

@app.route("/api/social-accounts/<platform>/<int:index>", methods=["DELETE"])
def delete_social_account(platform, index):
    from env_manager import delete_account, all_platforms_with_accounts
    delete_account(platform, index)
    return {"ok": True, "platforms": all_platforms_with_accounts()}

# --------------- Serve React build ---------------
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    full = FRONTEND_DIST / path
    if full.is_file():
        return send_from_directory(str(FRONTEND_DIST), path)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return send_from_directory(str(FRONTEND_DIST), "index.html")
    return {"error": "Frontend not built. Run npm build in frontend/"}, 404


if __name__ == "__main__":
    # Port: EVONEXUS_PORT env var takes precedence; fallback to 8080.
    # YAML-based port override (dashboard.port) was broken in SQLite mode
    # (read cfg["port"] but YAML stored cfg["dashboard"]["port"]) and is
    # a no-op in PG mode.  Env var is the canonical way to set the port.
    port = int(os.environ.get("EVONEXUS_PORT", 8080))
    # Scheduler runs as a standalone process (scheduler.py) started by start-services.sh.
    # A thread here would create a duplicate instance — all routines would fire 2-3x.
    # One-off scheduled tasks (ScheduledTask model) are checked by the standalone scheduler
    # via _run_pending_tasks, which is called from its own loop.
    import threading

    def _run_pending_tasks():
        """Check for pending scheduled tasks and execute them."""
        from datetime import datetime as _dt, timezone as _tz
        from models import ScheduledTask

        try:
            now = _dt.now(_tz.utc)
            from routes.tasks import claim_due_tasks
            for task_id in claim_due_tasks(now):
                task = ScheduledTask.query.get(task_id)
                log_path = WORKSPACE / "ADWs" / "logs" / "scheduler.log"
                with open(log_path, "a") as log:  # noqa: pg-native-logs — scheduler runtime log; per-routine outputs go through routine_run_store
                    log.write(f"  [{_dt.now().strftime('%H:%M')}] Running scheduled task #{task.id}: {task.name}\n")

                t = threading.Thread(target=_execute_task_with_context, args=(task_id,), daemon=True)
                t.start()
        except Exception:
            pass

    def _execute_task_with_context(task_id):
        with app.app_context():
            from routes.tasks import _execute_task
            _execute_task(task_id, already_claimed=True)

    def _poll_scheduled_tasks():
        """Lightweight thread that only polls ScheduledTask — no routine scheduling."""
        import time as _time
        while True:
            with app.app_context():
                try:
                    from routes.tasks import recover_interrupted_work
                    recovered = recover_interrupted_work()
                    if any(recovered.values()):
                        app.logger.warning("Recovered interrupted work: %s", recovered)
                except Exception:
                    app.logger.exception("Failed to recover interrupted work")
                _run_pending_tasks()
            _time.sleep(30)

    # Dev mode: EVONEXUS_DEV=1 enables Flask's auto-reloader so edits to
    # dashboard/backend/*.py take effect without a manual restart. Disabled by
    # default — production runs with a fixed process managed by systemd/docker.
    dev_mode = os.getenv("EVONEXUS_DEV") == "1"
    if not dev_mode or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        task_thread = threading.Thread(target=_poll_scheduled_tasks, daemon=True, name="task-poller")
        task_thread.start()

    app.run(host="0.0.0.0", port=port, debug=dev_mode, use_reloader=dev_mode)
