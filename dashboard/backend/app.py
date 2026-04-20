"""Flask backend for the workspace dashboard — EvoNexus."""

import os
import sys
import secrets
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

app = Flask(__name__, static_folder=None)
# Persist secret key so sessions survive restarts
_secret_key = os.environ.get("EVONEXUS_SECRET_KEY")
if not _secret_key:
    _key_file = WORKSPACE / "dashboard" / "data" / ".secret_key"
    _key_file.parent.mkdir(parents=True, exist_ok=True)
    if _key_file.exists():
        _secret_key = _key_file.read_text().strip()
    else:
        _secret_key = secrets.token_hex(32)
        _key_file.write_text(_secret_key)
        _key_file.chmod(0o600)

app.secret_key = _secret_key
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{WORKSPACE / 'dashboard' / 'data' / 'evonexus.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
CORS(app, origins=["http://localhost:5173"], supports_credentials=True)

# --------------- Database ---------------
from models import db, User, needs_setup, seed_roles, seed_systems
db.init_app(app)

# Create tables on first run + enable WAL mode for concurrent reads
with app.app_context():
    db.create_all()
    db.session.execute(db.text("PRAGMA journal_mode=WAL"))
    db.session.commit()

    # --- Auto-migrate: add new columns to existing tables ---
    import sqlite3 as _sqlite3
    _db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    _conn = _sqlite3.connect(_db_path)
    _cur = _conn.cursor()
    _existing_cols = {row[1] for row in _cur.execute("PRAGMA table_info(roles)").fetchall()}
    if "agent_access_json" not in _existing_cols:
        _cur.execute("ALTER TABLE roles ADD COLUMN agent_access_json TEXT DEFAULT '{\"mode\": \"all\"}'")
        _conn.commit()
    if "workspace_folders_json" not in _existing_cols:
        _cur.execute("ALTER TABLE roles ADD COLUMN workspace_folders_json TEXT DEFAULT '{\"mode\": \"all\"}'")
        _conn.commit()

    # --- Heartbeats migration (Feature 1.1) ---
    _existing_tables = {row[0] for row in _cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "heartbeats" not in _existing_tables:
        _cur.executescript("""
            CREATE TABLE IF NOT EXISTS heartbeats (
                id TEXT PRIMARY KEY,
                agent TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL CHECK(interval_seconds >= 60),
                max_turns INTEGER NOT NULL DEFAULT 10,
                timeout_seconds INTEGER NOT NULL DEFAULT 600,
                lock_timeout_seconds INTEGER NOT NULL DEFAULT 1800,
                wake_triggers TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 0,
                goal_id TEXT,
                required_secrets TEXT DEFAULT '[]',
                decision_prompt TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS heartbeat_runs (
                run_id TEXT PRIMARY KEY,
                heartbeat_id TEXT NOT NULL REFERENCES heartbeats(id) ON DELETE CASCADE,
                trigger_id TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_ms INTEGER,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                status TEXT NOT NULL CHECK(status IN ('running','success','fail','timeout','killed')),
                prompt_preview TEXT,
                error TEXT,
                triggered_by TEXT
            );
            CREATE TABLE IF NOT EXISTS heartbeat_triggers (
                id TEXT PRIMARY KEY,
                heartbeat_id TEXT NOT NULL REFERENCES heartbeats(id) ON DELETE CASCADE,
                trigger_type TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                consumed_at TEXT,
                coalesced_into TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_hb_runs_hb_status ON heartbeat_runs(heartbeat_id, status);
            CREATE INDEX IF NOT EXISTS idx_hb_runs_started ON heartbeat_runs(started_at);
            CREATE INDEX IF NOT EXISTS idx_hb_trig_hb_created ON heartbeat_triggers(heartbeat_id, created_at);
        """)
        _conn.commit()
    # --- End heartbeats migration ---

    # --- Goal Cascade migration (Feature 1.2) ---
    if "missions" not in _existing_tables:
        _cur.executescript("""
            CREATE TABLE IF NOT EXISTS missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                target_metric TEXT,
                target_value REAL,
                current_value REAL NOT NULL DEFAULT 0,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','achieved','on-hold','cancelled')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                mission_id INTEGER REFERENCES missions(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT,
                workspace_folder_path TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT,
                target_metric TEXT,
                metric_type TEXT NOT NULL DEFAULT 'count' CHECK(metric_type IN ('count','currency','percentage','boolean')),
                target_value REAL NOT NULL DEFAULT 1.0,
                current_value REAL NOT NULL DEFAULT 0.0,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','achieved','on-hold','cancelled')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goal_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                description TEXT,
                priority INTEGER NOT NULL DEFAULT 3,
                assignee_agent TEXT,
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','done','cancelled')),
                locked_at TEXT,
                locked_by TEXT,
                due_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_mission ON projects(mission_id);
            CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
            CREATE INDEX IF NOT EXISTS idx_goals_project_status ON goals(project_id, status);
            CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal_status ON goal_tasks(goal_id, status);
        """)
        _conn.commit()
    # Always ensure view and trigger exist (idempotent — safe to run on every startup)
    _cur.executescript("""
        CREATE VIEW IF NOT EXISTS goal_progress_v AS
        SELECT g.id as goal_id, g.slug, g.target_value,
               COUNT(t.id) as total_tasks,
               COUNT(CASE WHEN t.status='done' THEN 1 END) as done_tasks,
               CASE WHEN COUNT(t.id) > 0
                    THEN CAST(COUNT(CASE WHEN t.status='done' THEN 1 END) AS REAL) / COUNT(t.id) * 100.0
                    ELSE 0 END as pct_complete
        FROM goals g LEFT JOIN goal_tasks t ON t.goal_id = g.id
        GROUP BY g.id;
        CREATE TRIGGER IF NOT EXISTS trg_task_done_updates_goal
        AFTER UPDATE OF status ON goal_tasks
        WHEN NEW.goal_id IS NOT NULL AND NEW.status = 'done' AND OLD.status != 'done'
        BEGIN
          UPDATE goals SET current_value = current_value + 1, updated_at = datetime('now') WHERE id = NEW.goal_id;
          UPDATE goals SET status = 'achieved' WHERE id = NEW.goal_id AND current_value >= target_value AND status = 'active';
        END;
    """)
    _conn.commit()
    # Seed data if missions table is empty (guard on row count, not table existence)
    _cur.execute("SELECT COUNT(*) FROM missions")
    if _cur.fetchone()[0] == 0:
        _now_seed = "2026-04-14T00:00:00.000000Z"
        _cur.execute("""
            INSERT INTO missions (slug, title, description, target_metric, target_value, current_value, due_date, status, created_at, updated_at)
            VALUES ('evo-revenue-1m-q4-2026', 'Evolution Revenue $1M Q4 2026',
                    'Atingir $1M de receita anual até o Q4 2026',
                    'revenue_usd', 1000000, 0, '2026-12-31', 'active', ?, ?)
        """, (_now_seed, _now_seed))
        _mission_id = _cur.lastrowid
        # Projects
        for _slug, _title, _desc in [
            ('evo-ai', 'Evo AI', 'CRM + AI agents — produto principal'),
            ('evo-summit', 'Evolution Summit', 'Evento de lançamento (14-16 Abr 2026)'),
            ('evo-academy', 'Evo Academy', 'Plataforma de cursos'),
        ]:
            _cur.execute("""
                INSERT INTO projects (slug, mission_id, title, description, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
            """, (_slug, _mission_id, _title, _desc, _now_seed, _now_seed))
        _conn.commit()
        # Goals per project
        _evo_ai_id = _cur.execute("SELECT id FROM projects WHERE slug='evo-ai'").fetchone()[0]
        _summit_id = _cur.execute("SELECT id FROM projects WHERE slug='evo-summit'").fetchone()[0]
        _academy_id = _cur.execute("SELECT id FROM projects WHERE slug='evo-academy'").fetchone()[0]
        _goals_seed = [
            ('evo-ai-100-customers', _evo_ai_id, '100 paying customers by Jun 30', 'customers', 'count', 100, '2026-06-30'),
            ('evo-ai-billing-v2', _evo_ai_id, 'Ship billing v2', 'shipped', 'boolean', 1, '2026-05-31'),
            ('evo-summit-200-tickets', _summit_id, 'Sell 200 tickets', 'tickets_sold', 'count', 200, '2026-04-13'),
            ('evo-summit-3-sponsors', _summit_id, 'Close 3 sponsors', 'sponsors', 'count', 3, '2026-04-10'),
            ('evo-academy-50-students', _academy_id, '50 beta students', 'students', 'count', 50, '2026-06-30'),
        ]
        for _gs in _goals_seed:
            _cur.execute("""
                INSERT INTO goals (slug, project_id, title, target_metric, metric_type, target_value, current_value, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 'active', ?, ?)
            """, (_gs[0], _gs[1], _gs[2], _gs[3], _gs[4], _gs[5], _now_seed, _now_seed))
        _conn.commit()
    # --- End Goal Cascade migration ---

    # --- Tickets migration (Feature 1.3) ---
    _existing_tables2 = {row[0] for row in _cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "tickets" not in _existing_tables2:
        _cur.executescript("""
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','in_progress','blocked','review','resolved','closed')),
                priority TEXT NOT NULL DEFAULT 'medium'
                    CHECK(priority IN ('urgent','high','medium','low')),
                priority_rank INTEGER NOT NULL DEFAULT 2,
                project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
                assignee_agent TEXT,
                locked_at TEXT,
                locked_by TEXT,
                lock_timeout_seconds INTEGER,
                created_by TEXT NOT NULL DEFAULT 'davidson',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT,
                CHECK (
                    (locked_at IS NULL AND locked_by IS NULL) OR
                    (locked_at IS NOT NULL AND locked_by IS NOT NULL)
                )
            );
            CREATE TABLE IF NOT EXISTS ticket_comments (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                author TEXT NOT NULL,
                body TEXT NOT NULL,
                mentions TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ticket_activity (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tickets_assignee_status ON tickets(assignee_agent, status);
            CREATE INDEX IF NOT EXISTS idx_tickets_status_priority ON tickets(status, priority_rank);
            CREATE INDEX IF NOT EXISTS idx_tickets_locked ON tickets(locked_at);
            CREATE INDEX IF NOT EXISTS idx_tickets_project ON tickets(project_id);
            CREATE INDEX IF NOT EXISTS idx_tickets_goal ON tickets(goal_id);
            CREATE INDEX IF NOT EXISTS idx_comments_ticket_created ON ticket_comments(ticket_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_activity_ticket_created ON ticket_activity(ticket_id, created_at);
        """)
        _conn.commit()
    # --- Source attribution columns on tickets ---
    _ticket_cols = {row[1] for row in _cur.execute("PRAGMA table_info(tickets)").fetchall()}
    if "source_agent" not in _ticket_cols:
        _cur.execute("ALTER TABLE tickets ADD COLUMN source_agent TEXT")
        _conn.commit()
    if "source_session_id" not in _ticket_cols:
        _cur.execute("ALTER TABLE tickets ADD COLUMN source_session_id TEXT")
        _conn.commit()
    # --- End source attribution migration ---

    # --- End tickets migration ---

    # --- Knowledge connections migration (pgvector-knowledge feature) ---
    _existing_tables3 = {row[0] for row in _cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "knowledge_connections" not in _existing_tables3:
        _cur.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_connections (
                id TEXT PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                connection_string_encrypted BLOB,
                host TEXT,
                port INTEGER,
                database_name TEXT,
                username TEXT,
                ssl_mode TEXT,
                status TEXT DEFAULT 'disconnected',
                schema_version TEXT,
                pgvector_version TEXT,
                postgres_version TEXT,
                last_health_check TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS knowledge_connection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id TEXT REFERENCES knowledge_connections(id) ON DELETE CASCADE,
                event_type TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_kconn_status ON knowledge_connections(status);
            CREATE INDEX IF NOT EXISTS idx_kconn_events_conn ON knowledge_connection_events(connection_id, created_at);
        """)
        _conn.commit()
    # --- End knowledge connections migration ---

    # --- Knowledge API keys migration (pgvector-knowledge Step 4) ---
    _existing_tables4 = {row[0] for row in _cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "knowledge_api_keys" not in _existing_tables4:
        _cur.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_api_keys (
                id TEXT PRIMARY KEY,
                name TEXT,
                prefix TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                space_ids TEXT NOT NULL DEFAULT '[]',
                scopes TEXT NOT NULL DEFAULT '["read"]',
                rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
                rate_limit_per_day INTEGER NOT NULL DEFAULT 10000,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                expires_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_kak_prefix ON knowledge_api_keys(prefix);
        """)
        _conn.commit()
    # --- End knowledge API keys migration ---

    # Fix corrupted datetime columns (NULL or non-string values crash SQLAlchemy)
    for _tbl, _col in [("roles", "created_at"), ("users", "created_at"), ("users", "last_login")]:
        try:
            _tbl_cols = {row[1] for row in _cur.execute(f"PRAGMA table_info({_tbl})").fetchall()}
            if _col in _tbl_cols:
                _cur.execute(f"UPDATE {_tbl} SET {_col} = datetime('now') WHERE {_col} IS NOT NULL AND typeof({_col}) != 'text'")
                _cur.execute(f"UPDATE {_tbl} SET {_col} = datetime('now') WHERE {_col} IS NOT NULL AND {_col} != '' AND {_col} NOT LIKE '____-__-__%'")
        except Exception:
            pass
    _conn.commit()
    _conn.close()
    # --- End auto-migrate ---

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
    try:
        from knowledge.classify_worker import start_classify_worker
        _sqlite_db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
        start_classify_worker(_sqlite_db_path)
    except Exception as _cw_exc:
        print(f"WARNING: knowledge classify worker init failed: {_cw_exc}")

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
from routes.docs import bp as docs_bp
from routes.mempalace import bp as mempalace_bp
from routes.tasks import bp as tasks_bp
from routes.triggers import bp as triggers_bp
from routes.backups import bp as backups_bp
from routes.providers import bp as providers_bp
from routes.settings import bp as settings_bp
from routes.shares import bp as shares_bp
from routes.heartbeats import bp as heartbeats_bp
from routes.goals import bp as goals_bp
from routes.tickets import bp as tickets_bp
from routes.knowledge import bp as knowledge_bp
from routes.knowledge_public import bp as knowledge_public_bp
from routes.knowledge_proxy import bp as knowledge_proxy_bp
from routes.knowledge_v1 import bp as knowledge_v1_bp

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
app.register_blueprint(docs_bp)
app.register_blueprint(mempalace_bp)
app.register_blueprint(tasks_bp)
app.register_blueprint(triggers_bp)
app.register_blueprint(backups_bp)
app.register_blueprint(providers_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(shares_bp)
app.register_blueprint(heartbeats_bp)
app.register_blueprint(goals_bp)
app.register_blueprint(tickets_bp)
app.register_blueprint(knowledge_bp)
app.register_blueprint(knowledge_public_bp)
app.register_blueprint(knowledge_proxy_bp)
app.register_blueprint(knowledge_v1_bp)

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
    # Read port from workspace.yaml or env, fallback to 8080
    port = int(os.environ.get("EVONEXUS_PORT", 8080))
    try:
        import yaml
        config_path = WORKSPACE / "config" / "workspace.yaml"
        if config_path.is_file():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            if cfg and cfg.get("port"):
                port = int(cfg["port"])
    except Exception:
        pass
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
            pending = ScheduledTask.query.filter(
                ScheduledTask.status == "pending",
                ScheduledTask.scheduled_at <= now,
            ).all()

            for task in pending:
                log_path = WORKSPACE / "ADWs" / "logs" / "scheduler.log"
                with open(log_path, "a") as log:
                    log.write(f"  [{_dt.now().strftime('%H:%M')}] Running scheduled task #{task.id}: {task.name}\n")

                t = threading.Thread(target=_execute_task_with_context, args=(task.id,), daemon=True)
                t.start()
        except Exception:
            pass

    def _execute_task_with_context(task_id):
        with app.app_context():
            from routes.tasks import _execute_task
            _execute_task(task_id)

    def _poll_scheduled_tasks():
        """Lightweight thread that only polls ScheduledTask — no routine scheduling."""
        import time as _time
        while True:
            with app.app_context():
                _run_pending_tasks()
            _time.sleep(30)

    task_thread = threading.Thread(target=_poll_scheduled_tasks, daemon=True, name="task-poller")
    task_thread.start()

    app.run(host="0.0.0.0", port=port, debug=False)
