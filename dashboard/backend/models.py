"""SQLAlchemy models for EvoNexus dashboard."""

import json
import secrets
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import bcrypt

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(128), nullable=False)
    display_name = db.Column(db.String(120))
    avatar_url = db.Column(db.String(500))
    role = db.Column(db.String(20), nullable=False, default="viewer")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    def set_password(self, password: str):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    onboarding_state = db.Column(db.String(20), nullable=True)
    onboarding_completed_agents_visit = db.Column(db.Boolean, default=False, nullable=False, server_default='0')

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
            "last_login": self.last_login.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.last_login else None,
            "onboarding_state": self.onboarding_state,
            "onboarding_completed_agents_visit": self.onboarding_completed_agents_visit,
        }


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    username = db.Column(db.String(80))
    action = db.Column(db.String(50), nullable=False)
    resource = db.Column(db.String(100))
    detail = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "action": self.action,
            "resource": self.resource,
            "detail": self.detail,
            "ip_address": self.ip_address,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
        }


# All available resources and their possible actions
ALL_RESOURCES = {
    "chat": ["view", "execute", "manage"],
    "services": ["view", "execute", "manage"],
    "systems": ["view", "execute", "manage"],
    "integrations": ["view", "execute", "manage"],
    "workspace": ["view", "manage"],
    "agents": ["view", "manage"],
    "memory": ["view", "manage"],
    "skills": ["view", "manage"],
    "costs": ["view", "manage"],
    "config": ["view", "manage"],
    "users": ["view", "manage"],
    "audit": ["view"],
    "templates": ["view"],
    "routines": ["view", "execute"],
    "scheduler": ["view", "execute"],
    "tasks": ["view", "execute", "manage"],
    "triggers": ["view", "execute", "manage"],
    "mempalace": ["view", "manage"],
    "knowledge": ["view", "manage"],
    "heartbeats": ["view", "execute", "manage"],
    "goals": ["view", "execute", "manage"],
    "tickets": ["view", "execute", "manage"],
}

# Agent layer mapping (file-stem names)
AGENT_LAYERS: dict[str, str] = {
    # Business layer
    "clawdia-assistant": "business",
    "flux-finance": "business",
    "atlas-project": "business",
    "kai-personal-assistant": "business",
    "pulse-community": "business",
    "sage-strategy": "business",
    "pixel-social-media": "business",
    "nex-sales": "business",
    "mentor-courses": "business",
    "lumen-learning": "business",
    "oracle": "business",
    "mako-marketing": "business",
    "aria-hr": "business",
    "zara-cs": "business",
    "lex-legal": "business",
    "nova-product": "business",
    "dex-data": "business",
    # Engineering layer
    "apex-architect": "engineering",
    "echo-analyst": "engineering",
    "compass-planner": "engineering",
    "raven-critic": "engineering",
    "lens-reviewer": "engineering",
    "zen-simplifier": "engineering",
    "vault-security": "engineering",
    "bolt-executor": "engineering",
    "hawk-debugger": "engineering",
    "grid-tester": "engineering",
    "probe-qa": "engineering",
    "oath-verifier": "engineering",
    "trail-tracer": "engineering",
    "flow-git": "engineering",
    "scroll-docs": "engineering",
    "canvas-designer": "engineering",
    "prism-scientist": "engineering",
    "helm-conductor": "engineering",
    "mirror-retro": "engineering",
    "scout-explorer": "engineering",
    "quill-writer": "engineering",
}

# Default permissions for built-in roles (used when seeding)
BUILTIN_ROLES = {
    "admin": {
        "description": "Full access to all resources",
        "permissions": {r: actions[:] for r, actions in ALL_RESOURCES.items()},
        "agent_access": {"mode": "all"},
        "workspace_folders": {"mode": "all"},
    },
    "operator": {
        "description": "Can view and execute, but not manage users or audit",
        "agent_access": {"mode": "all"},
        "workspace_folders": {"mode": "all"},
        "permissions": {
            "chat": ["view", "execute"],
            "services": ["view", "execute"],
            "systems": ["view", "execute"],
            "integrations": ["view", "execute"],
            "workspace": ["view"],
            "agents": ["view"],
            "memory": ["view"],
            "skills": ["view"],
            "costs": ["view"],
            "config": ["view"],
            "templates": ["view"],
            "routines": ["view", "execute"],
            "scheduler": ["view", "execute"],
            "tasks": ["view", "execute"],
            "triggers": ["view", "execute"],
            "mempalace": ["view"],
            "knowledge": ["view", "manage"],
            "heartbeats": ["view", "execute"],
            "tickets": ["view", "execute"],
        },
    },
    "viewer": {
        "description": "Read-only access to dashboards",
        "agent_access": {"mode": "none"},
        "workspace_folders": {"mode": "all"},
        "permissions": {
            "workspace": ["view"],
            "agents": ["view"],
            "memory": ["view"],
            "skills": ["view"],
            "costs": ["view"],
            "config": ["view"],
            "templates": ["view"],
            "services": ["view"],
            "systems": ["view"],
            "integrations": ["view"],
            "routines": ["view"],
            "scheduler": ["view"],
            "tasks": ["view"],
            "triggers": ["view"],
            "mempalace": ["view"],
            "knowledge": ["view"],
            "heartbeats": ["view"],
            "tickets": ["view"],
        },
    },
}


class ScheduledTask(db.Model):
    __tablename__ = "scheduled_tasks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    type = db.Column(db.String(20), nullable=False)  # skill, prompt, script
    payload = db.Column(db.Text, nullable=False)
    agent = db.Column(db.String(50), nullable=True)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending, running, completed, failed, cancelled
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    result_summary = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "payload": self.payload,
            "agent": self.agent,
            "scheduled_at": self.scheduled_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.scheduled_at else None,
            "status": self.status,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
            "started_at": self.started_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.started_at else None,
            "completed_at": self.completed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.completed_at else None,
            "result_summary": self.result_summary,
            "error": self.error,
            "created_by": self.created_by,
        }


class Trigger(db.Model):
    __tablename__ = "triggers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # webhook, event
    source = db.Column(db.String(50), nullable=False)  # github, linear, telegram, discord, stripe, custom
    event_filter = db.Column(db.Text, nullable=True, default="{}")  # JSON filter config
    action_type = db.Column(db.String(20), nullable=False)  # skill, prompt, script
    action_payload = db.Column(db.Text, nullable=False)
    agent = db.Column(db.String(50), nullable=True)
    secret = db.Column(db.String(128), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    from_yaml = db.Column(db.Boolean, default=False)
    remote_trigger_id = db.Column(db.String(100), nullable=True)
    source_plugin = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    executions = db.relationship("TriggerExecution", backref="trigger", lazy="dynamic", cascade="all, delete-orphan")

    @staticmethod
    def generate_secret():
        return secrets.token_hex(32)

    @property
    def event_filter_dict(self) -> dict:
        try:
            return json.loads(self.event_filter) if self.event_filter else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @event_filter_dict.setter
    def event_filter_dict(self, value: dict):
        self.event_filter = json.dumps(value)

    def webhook_url(self, base_url: str = "") -> str:
        return f"{base_url}/api/triggers/webhook/{self.id}"

    def to_dict(self, include_secret=False):
        d = {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "type": self.type,
            "source": self.source,
            "event_filter": self.event_filter_dict,
            "action_type": self.action_type,
            "action_payload": self.action_payload,
            "agent": self.agent,
            "enabled": self.enabled,
            "from_yaml": self.from_yaml,
            "remote_trigger_id": self.remote_trigger_id,
            "source_plugin": self.source_plugin,
            "created_by": self.created_by,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.updated_at else None,
            "execution_count": self.executions.count() if self.executions else 0,
        }
        if include_secret:
            d["secret"] = self.secret
        return d


class TriggerExecution(db.Model):
    __tablename__ = "trigger_executions"

    id = db.Column(db.Integer, primary_key=True)
    trigger_id = db.Column(db.Integer, db.ForeignKey("triggers.id", ondelete="CASCADE"), nullable=False)
    event_data = db.Column(db.Text, nullable=True, default="{}")  # JSON payload received
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending, running, completed, failed
    result_summary = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)

    @property
    def event_data_dict(self) -> dict:
        try:
            return json.loads(self.event_data) if self.event_data else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self):
        return {
            "id": self.id,
            "trigger_id": self.trigger_id,
            "event_data": self.event_data_dict,
            "status": self.status,
            "result_summary": self.result_summary,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.started_at else None,
            "completed_at": self.completed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.completed_at else None,
        }


class RuntimeConfig(db.Model):
    __tablename__ = "runtime_configs"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, onupdate=lambda: datetime.now(timezone.utc))


class System(db.Model):
    __tablename__ = "systems"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500))
    url = db.Column(db.String(500))
    container = db.Column(db.String(120))
    icon = db.Column(db.String(10), default="📦")
    type = db.Column(db.String(20), default="docker")  # docker, external, iframe
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "container": self.container,
            "icon": self.icon,
            "type": self.type,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
        }


class FileShare(db.Model):
    __tablename__ = "file_shares"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    path = db.Column(db.String(500), nullable=False)       # repo-relative path
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=True)      # null = no expiration
    view_count = db.Column(db.Integer, default=0)
    enabled = db.Column(db.Boolean, default=True)

    created_by_user = db.relationship("User", foreign_keys=[created_by_id])

    def to_dict(self):
        return {
            "id": self.id,
            "token": self.token,
            "path": self.path,
            "created_by_id": self.created_by_id,
            "created_by": self.created_by_user.username if self.created_by_user else None,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
            "expires_at": self.expires_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.expires_at else None,
            "view_count": self.view_count,
            "enabled": self.enabled,
        }


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    permissions_json = db.Column(db.Text, nullable=False, default="{}")
    agent_access_json = db.Column(db.Text, nullable=True, default='{"mode": "all"}')
    workspace_folders_json = db.Column(db.Text, nullable=True, default='{"mode": "all"}')
    is_builtin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def permissions(self) -> dict:
        try:
            return json.loads(self.permissions_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @permissions.setter
    def permissions(self, value: dict):
        self.permissions_json = json.dumps(value)

    @property
    def agent_access(self) -> dict:
        try:
            result = json.loads(self.agent_access_json) if self.agent_access_json else None
            return result if result else {"mode": "all"}
        except (json.JSONDecodeError, TypeError):
            return {"mode": "all"}

    @agent_access.setter
    def agent_access(self, value: dict):
        self.agent_access_json = json.dumps(value)

    @property
    def workspace_folders(self) -> dict:
        try:
            result = json.loads(self.workspace_folders_json) if self.workspace_folders_json else None
            return result if result else {"mode": "all"}
        except (json.JSONDecodeError, TypeError):
            return {"mode": "all"}

    @workspace_folders.setter
    def workspace_folders(self, value: dict):
        self.workspace_folders_json = json.dumps(value)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "permissions": self.permissions,
            "agent_access": self.agent_access,
            "workspace_folders": self.workspace_folders,
            "is_builtin": self.is_builtin,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
        }


class Heartbeat(db.Model):
    __tablename__ = "heartbeats"

    id = db.Column(db.String(100), primary_key=True)  # slug: "atlas-4h"
    agent = db.Column(db.String(100), nullable=False)
    interval_seconds = db.Column(db.Integer, nullable=False)
    max_turns = db.Column(db.Integer, nullable=False, default=10)
    timeout_seconds = db.Column(db.Integer, nullable=False, default=600)
    lock_timeout_seconds = db.Column(db.Integer, nullable=False, default=1800)
    wake_triggers = db.Column(db.Text, nullable=False, default="[]")  # JSON array
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    goal_id = db.Column(db.String(100), nullable=True)  # FK stub for Feature 1.2
    required_secrets = db.Column(db.Text, nullable=True, default="[]")  # JSON array
    decision_prompt = db.Column(db.Text, nullable=False)
    source_plugin = db.Column(db.Text, nullable=True)  # Wave 1.1: plugin slug if contributed by a plugin
    created_at = db.Column(db.String(30), default=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    updated_at = db.Column(db.String(30), default=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), onupdate=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

    runs = db.relationship("HeartbeatRun", backref="heartbeat", lazy="dynamic", cascade="all, delete-orphan")
    triggers = db.relationship("HeartbeatTriggerEvent", backref="heartbeat", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def wake_triggers_list(self) -> list:
        try:
            return json.loads(self.wake_triggers) if self.wake_triggers else []
        except (json.JSONDecodeError, TypeError):
            return []

    @wake_triggers_list.setter
    def wake_triggers_list(self, value: list):
        self.wake_triggers = json.dumps(value)

    @property
    def required_secrets_list(self) -> list:
        try:
            return json.loads(self.required_secrets) if self.required_secrets else []
        except (json.JSONDecodeError, TypeError):
            return []

    @required_secrets_list.setter
    def required_secrets_list(self, value: list):
        self.required_secrets = json.dumps(value)

    def to_dict(self):
        last_run = self.runs.order_by(HeartbeatRun.started_at.desc()).first()
        return {
            "id": self.id,
            "agent": self.agent,
            "interval_seconds": self.interval_seconds,
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
            "lock_timeout_seconds": self.lock_timeout_seconds,
            "wake_triggers": self.wake_triggers_list,
            "enabled": self.enabled,
            "goal_id": self.goal_id,
            "required_secrets": self.required_secrets_list,
            "decision_prompt": self.decision_prompt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run": last_run.to_dict() if last_run else None,
            "run_count": self.runs.count(),
            "source_plugin": self.source_plugin,
        }


class HeartbeatRun(db.Model):
    __tablename__ = "heartbeat_runs"

    run_id = db.Column(db.String(36), primary_key=True)  # uuid4
    heartbeat_id = db.Column(db.String(100), db.ForeignKey("heartbeats.id", ondelete="CASCADE"), nullable=False)
    trigger_id = db.Column(db.String(36), nullable=True)
    started_at = db.Column(db.String(30), default=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    ended_at = db.Column(db.String(30), nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    tokens_in = db.Column(db.Integer, nullable=True)
    tokens_out = db.Column(db.Integer, nullable=True)
    cost_usd = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="running")  # running, success, fail, timeout, killed
    prompt_preview = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)
    triggered_by = db.Column(db.String(50), nullable=True)  # interval, manual, new_task, mention, approval_decision

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "heartbeat_id": self.heartbeat_id,
            "trigger_id": self.trigger_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "status": self.status,
            "prompt_preview": self.prompt_preview,
            "error": self.error,
            "triggered_by": self.triggered_by,
        }


class HeartbeatTriggerEvent(db.Model):
    __tablename__ = "heartbeat_triggers"

    id = db.Column(db.String(36), primary_key=True)  # uuid4
    heartbeat_id = db.Column(db.String(100), db.ForeignKey("heartbeats.id", ondelete="CASCADE"), nullable=False)
    trigger_type = db.Column(db.String(50), nullable=False)  # interval, new_task, mention, manual, approval_decision
    payload = db.Column(db.Text, nullable=True, default="{}")  # JSON
    created_at = db.Column(db.String(30), default=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    consumed_at = db.Column(db.String(30), nullable=True)
    coalesced_into = db.Column(db.String(36), nullable=True)  # trigger id that absorbed this (debounce)

    @property
    def payload_dict(self) -> dict:
        try:
            return json.loads(self.payload) if self.payload else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self):
        return {
            "id": self.id,
            "heartbeat_id": self.heartbeat_id,
            "trigger_type": self.trigger_type,
            "payload": self.payload_dict,
            "created_at": self.created_at,
            "consumed_at": self.consumed_at,
            "coalesced_into": self.coalesced_into,
        }


# --------------- Goal Cascade models (Feature 1.2) ---------------

class Mission(db.Model):
    __tablename__ = "missions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    target_metric = db.Column(db.String(100))
    target_value = db.Column(db.Float)
    current_value = db.Column(db.Float, nullable=False, default=0)
    due_date = db.Column(db.String(20))
    status = db.Column(db.String(20), nullable=False, default="active")
    created_at = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30), nullable=False)

    projects = db.relationship("GoalProject", backref="mission", lazy="dynamic", cascade="all, delete-orphan")

    def to_dict(self, include_projects=False):
        d = {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "target_metric": self.target_metric,
            "target_value": self.target_value,
            "current_value": self.current_value,
            "due_date": self.due_date,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_projects:
            d["projects"] = [p.to_dict(include_goals=True) for p in self.projects]
        return d


class GoalProject(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    mission_id = db.Column(db.Integer, db.ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    workspace_folder_path = db.Column(db.String(500))
    status = db.Column(db.String(20), nullable=False, default="active")
    created_at = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30), nullable=False)

    goals = db.relationship("Goal", backref="project", lazy="dynamic", cascade="all, delete-orphan")

    def to_dict(self, include_goals=False):
        d = {
            "id": self.id,
            "slug": self.slug,
            "mission_id": self.mission_id,
            "title": self.title,
            "description": self.description,
            "workspace_folder_path": self.workspace_folder_path,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_goals:
            d["goals"] = [g.to_dict(include_tasks=True) for g in self.goals]
        return d


class Goal(db.Model):
    __tablename__ = "goals"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    target_metric = db.Column(db.String(100))
    metric_type = db.Column(db.String(20), nullable=False, default="count")
    target_value = db.Column(db.Float, nullable=False, default=1.0)
    current_value = db.Column(db.Float, nullable=False, default=0.0)
    due_date = db.Column(db.String(20))
    status = db.Column(db.String(20), nullable=False, default="active")
    created_at = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30), nullable=False)

    goal_tasks = db.relationship("GoalTask", backref="goal", lazy="dynamic", cascade="all, delete-orphan")

    def to_dict(self, include_tasks=False):
        d = {
            "id": self.id,
            "slug": self.slug,
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "target_metric": self.target_metric,
            "metric_type": self.metric_type,
            "target_value": self.target_value,
            "current_value": self.current_value,
            "due_date": self.due_date,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_tasks:
            d["tasks"] = [t.to_dict() for t in self.goal_tasks]
        return d


class GoalTask(db.Model):
    __tablename__ = "goal_tasks"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.Integer, nullable=False, default=3)
    assignee_agent = db.Column(db.String(100))
    status = db.Column(db.String(20), nullable=False, default="open")
    locked_at = db.Column(db.String(30))
    locked_by = db.Column(db.String(100))
    due_date = db.Column(db.String(20))
    created_at = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "assignee_agent": self.assignee_agent,
            "status": self.status,
            "locked_at": self.locked_at,
            "locked_by": self.locked_by,
            "due_date": self.due_date,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# --------------- End Goal Cascade models ---------------


# --------------- Tickets models (Feature 1.3) ---------------

TICKET_STATUSES = ("open", "in_progress", "blocked", "review", "resolved", "closed", "archived")
TICKET_PRIORITIES = ("urgent", "high", "medium", "low")
PRIORITY_RANK = {"urgent": 4, "high": 3, "medium": 2, "low": 1}


class Ticket(db.Model):
    __tablename__ = "tickets"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('open','in_progress','blocked','review','resolved','closed')",
            name="ck_ticket_status",
        ),
        db.CheckConstraint(
            "priority IN ('urgent','high','medium','low')",
            name="ck_ticket_priority",
        ),
        db.CheckConstraint(
            "(locked_at IS NULL AND locked_by IS NULL) OR (locked_at IS NOT NULL AND locked_by IS NOT NULL)",
            name="ck_ticket_lock_consistency",
        ),
    )

    id = db.Column(db.String(36), primary_key=True)  # uuid4
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="open")
    priority = db.Column(db.String(10), nullable=False, default="medium")
    priority_rank = db.Column(db.Integer, nullable=False, default=2)  # derived
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    assignee_agent = db.Column(db.String(100), nullable=True)
    locked_at = db.Column(db.String(30), nullable=True)
    locked_by = db.Column(db.String(100), nullable=True)
    lock_timeout_seconds = db.Column(db.Integer, nullable=True)
    created_by = db.Column(db.String(100), nullable=False, default="davidson")
    source_agent = db.Column(db.String(100), nullable=True)
    source_session_id = db.Column(db.String(36), nullable=True)
    # thread-areas columns
    workspace_path = db.Column(db.Text, nullable=True)
    memory_md_path = db.Column(db.Text, nullable=True)
    thread_session_id = db.Column(db.Text, nullable=True)
    message_count = db.Column(db.Integer, nullable=False, default=0)
    last_summary_at_message = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.String(30), nullable=False)
    updated_at = db.Column(db.String(30), nullable=False)
    resolved_at = db.Column(db.String(30), nullable=True)

    comments = db.relationship("TicketComment", backref="ticket", lazy="dynamic", cascade="all, delete-orphan")
    activity = db.relationship("TicketActivity", backref="ticket", lazy="dynamic", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "priority_rank": self.priority_rank,
            "project_id": self.project_id,
            "goal_id": self.goal_id,
            "assignee_agent": self.assignee_agent,
            "locked_at": self.locked_at,
            "locked_by": self.locked_by,
            "lock_timeout_seconds": self.lock_timeout_seconds,
            "created_by": self.created_by,
            "source_agent": self.source_agent,
            "source_session_id": self.source_session_id,
            # thread-areas fields
            "workspace_path": self.workspace_path,
            "memory_md_path": self.memory_md_path,
            "thread_session_id": self.thread_session_id,
            "message_count": self.message_count,
            "last_summary_at_message": self.last_summary_at_message,
            "is_thread": self.memory_md_path is not None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
        }


class TicketComment(db.Model):
    __tablename__ = "ticket_comments"

    id = db.Column(db.String(36), primary_key=True)  # uuid4
    ticket_id = db.Column(db.String(36), db.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    author = db.Column(db.String(100), nullable=False)  # 'davidson' | 'zara-cs' | ...
    body = db.Column(db.Text, nullable=False)
    mentions = db.Column(db.Text, nullable=True)  # JSON array of agent slugs
    created_at = db.Column(db.String(30), nullable=False)

    @property
    def mentions_list(self) -> list:
        try:
            return json.loads(self.mentions) if self.mentions else []
        except (json.JSONDecodeError, TypeError):
            return []

    @mentions_list.setter
    def mentions_list(self, value: list):
        self.mentions = json.dumps(value)

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "author": self.author,
            "body": self.body,
            "mentions": self.mentions_list,
            "created_at": self.created_at,
        }


class TicketActivity(db.Model):
    __tablename__ = "ticket_activity"

    id = db.Column(db.String(36), primary_key=True)  # uuid4
    ticket_id = db.Column(db.String(36), db.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    actor = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(50), nullable=False)  # created, status_changed, checkout, release, auto_release, assigned, commented, linked_session
    payload = db.Column(db.Text, nullable=True)  # JSON

    @property
    def payload_dict(self) -> dict:
        try:
            return json.loads(self.payload) if self.payload else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    created_at = db.Column(db.String(30), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "actor": self.actor,
            "action": self.action,
            "payload": self.payload_dict,
            "created_at": self.created_at,
        }


# --------------- End Tickets models ---------------


def seed_roles():
    """Create or update built-in roles to match current defaults."""
    for name, config in BUILTIN_ROLES.items():
        existing = Role.query.filter_by(name=name).first()
        if existing:
            # Merge new resources into existing permissions without removing custom ones
            current = existing.permissions or {}

            # --- Migração workspace-file-manager: files+reports → workspace ---
            legacy_actions = set(current.get("files", [])) | set(current.get("reports", []))
            if legacy_actions:
                merged = set(current.get("workspace", [])) | legacy_actions
                current["workspace"] = sorted(merged)
            current.pop("files", None)
            current.pop("reports", None)
            # --- fim migração ---

            default = config["permissions"]
            for resource, actions in default.items():
                if resource not in current:
                    current[resource] = actions
            existing.permissions = current

            # --- Migração agent_access: set default for existing roles ---
            if existing.agent_access_json is None:
                existing.agent_access = config.get("agent_access", {"mode": "all"})
            # --- fim migração ---

            # --- Migração workspace_folders: set default for existing roles ---
            if existing.workspace_folders_json is None:
                existing.workspace_folders = config.get("workspace_folders", {"mode": "all"})
            # --- fim migração ---
        else:
            role = Role(
                name=name,
                description=config["description"],
                is_builtin=True,
            )
            role.permissions = config["permissions"]
            role.agent_access = config.get("agent_access", {"mode": "all"})
            role.workspace_folders = config.get("workspace_folders", {"mode": "all"})
            db.session.add(role)
    db.session.commit()


def seed_systems():
    """Create default systems if they don't exist."""
    defaults = [
        {
            "name": "Claude Status",
            "description": "Anthropic Claude platform status and incident history",
            "url": "https://status.anthropic.com",
            "icon": "📊",
            "type": "external",
        },
    ]
    for item in defaults:
        existing = System.query.filter_by(name=item["name"]).first()
        if not existing:
            db.session.add(System(**item))
    db.session.commit()


def get_role_permissions(role_name: str) -> dict:
    """Get permissions for a role from DB, fallback to builtin defaults."""
    role = Role.query.filter_by(name=role_name).first()
    if role:
        return role.permissions
    # Fallback to builtin
    builtin = BUILTIN_ROLES.get(role_name)
    if builtin:
        return builtin["permissions"]
    return {}


def get_role_agent_access(role_name: str) -> dict:
    """Get agent_access config for a role from DB, fallback to builtin defaults."""
    role = Role.query.filter_by(name=role_name).first()
    if role:
        return role.agent_access
    builtin = BUILTIN_ROLES.get(role_name)
    if builtin:
        return builtin.get("agent_access", {"mode": "all"})
    return {"mode": "all"}


def has_permission(role: str, resource: str, action: str) -> bool:
    perms = get_role_permissions(role)
    return action in perms.get(resource, [])


def has_agent_access(role_name: str, agent_name: str) -> bool:
    """Check if a role has access to a specific agent."""
    if role_name == "admin":
        return True
    config = get_role_agent_access(role_name)
    mode = config.get("mode", "all")
    if mode == "all":
        return True
    if mode == "none":
        return False
    if mode == "selected":
        agents = config.get("agents", [])
        return agent_name in agents
    if mode == "layer":
        layers = config.get("layers", [])
        agent_layer = AGENT_LAYERS.get(agent_name)
        return agent_layer is not None and agent_layer in layers
    # Unknown mode — default to all
    return True


def get_role_workspace_folders(role_name: str) -> dict:
    """Get workspace_folders config for a role from DB, fallback to builtin defaults."""
    role = Role.query.filter_by(name=role_name).first()
    if role:
        return role.workspace_folders
    builtin = BUILTIN_ROLES.get(role_name)
    if builtin:
        return builtin.get("workspace_folders", {"mode": "all"})
    return {"mode": "all"}


def has_workspace_folder_access(role_name: str, path: str) -> bool:
    """Check if a role has access to a specific workspace folder.

    Only enforces top-level folder access (e.g. workspace/finance/).
    Subfolders inherit parent access.

    NOTE: This check applies only to the Flask API workspace endpoints.
    Terminal-server / agent sessions (Claude Code CLI) do NOT enforce folder
    restrictions — they bypass the Flask API entirely. This is a known
    limitation to be addressed in a future iteration.

    Args:
        role_name: The role name string.
        path: A repo-relative path string (e.g. "workspace/finance/report.md").

    Returns:
        True if access is allowed, False otherwise.
    """
    if role_name == "admin":
        return True

    parts = path.strip("/").split("/")

    # Root "workspace" listing — always allowed (filtering happens on children)
    if len(parts) == 0 or parts[0] != "workspace":
        return True  # Non-workspace paths are unaffected by folder permissions
    if len(parts) < 2 or parts[1] == "":
        return True  # Browsing workspace root is allowed; children are filtered

    folder = parts[1]

    config = get_role_workspace_folders(role_name)
    mode = config.get("mode", "all")

    if mode == "all":
        return True
    if mode == "none":
        return False
    if mode == "selected":
        folders = config.get("folders", [])
        # Empty selected list behaves as none
        if not folders:
            return False
        return folder in [f.strip("/") for f in folders]
    # Unknown mode — default to all
    return True


def audit(user, action: str, resource: str = None, detail: str = None):
    """Log an action to the audit trail."""
    from flask import request
    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "system",
        action=action,
        resource=resource,
        detail=detail,
        ip_address=request.remote_addr if request else None,
    )
    db.session.add(entry)
    db.session.commit()


class BrainRepoConfig(db.Model):
    __tablename__ = "brain_repo_configs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    github_token_encrypted = db.Column(db.LargeBinary, nullable=True)
    repo_url = db.Column(db.String(500), nullable=True)
    repo_owner = db.Column(db.String(200), nullable=True)
    repo_name = db.Column(db.String(200), nullable=True)
    local_path = db.Column(db.String(500), nullable=True)
    last_sync = db.Column(db.DateTime, nullable=True)
    sync_enabled = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    last_error = db.Column(db.Text, nullable=True)
    pending_count = db.Column(db.Integer, default=0, nullable=False, server_default='0')
    # Async sync job state — set by routes/brain_repo when an operation is enqueued,
    # cleared by brain_repo.job_runner when it finishes. sync_job_kind holds a short
    # verb ("sync", "milestone", "bootstrap") so the UI can show which operation is
    # running. cancel_requested is a cooperative flag the pipeline checks between
    # steps (no signal-based kill — git operations aren't safe to interrupt arbitrarily).
    sync_in_progress = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    sync_started_at = db.Column(db.DateTime, nullable=True)
    sync_job_kind = db.Column(db.String(32), nullable=True)
    cancel_requested = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", backref=db.backref("brain_repo_config", uselist=False))

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "repo_url": self.repo_url,
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "local_path": self.local_path,
            "last_sync": self.last_sync.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.last_sync else None,
            "sync_enabled": self.sync_enabled,
            "last_error": self.last_error,
            "pending_count": self.pending_count,
            "sync_in_progress": bool(self.sync_in_progress),
            "sync_job_kind": self.sync_job_kind,
            "sync_started_at": self.sync_started_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.sync_started_at else None,
            "cancel_requested": bool(self.cancel_requested),
            "connected": self.github_token_encrypted is not None,
        }


def needs_setup() -> bool:
    """Check if the system needs initial setup (no users exist)."""
    try:
        return User.query.count() == 0
    except Exception:
        return True


def needs_onboarding(user) -> bool:
    """Check if the user needs to complete the onboarding wizard."""
    return user is not None and user.onboarding_state in (None, "pending")


# ---------------------------------------------------------------------------
# Wave 2.5 — Plugin security scan tables
# ---------------------------------------------------------------------------


class PluginScanCache(db.Model):
    """Cache table for plugin security scan results.

    Cache key: tarball_sha256 + scanner_version (7-day TTL).
    Hit means we skip re-scanning identical plugin archives.
    """

    __tablename__ = "plugin_scan_cache"

    id = db.Column(db.Integer, primary_key=True)
    tarball_sha256 = db.Column(db.String(64), nullable=False)
    scanner_version = db.Column(db.String(20), nullable=False)
    verdict = db.Column(db.String(10), nullable=False)  # APPROVE | WARN | BLOCK
    findings_json = db.Column(db.Text, nullable=False, default="[]")
    scanned_files = db.Column(db.Integer, nullable=False, default=0)
    llm_augmented = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Composite unique key enforced at DB level (see inline migration)
    __table_args__ = (
        db.UniqueConstraint("tarball_sha256", "scanner_version", name="uq_scan_cache_sha_ver"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tarball_sha256": self.tarball_sha256,
            "scanner_version": self.scanner_version,
            "verdict": self.verdict,
            "findings": json.loads(self.findings_json or "[]"),
            "scanned_files": self.scanned_files,
            "llm_augmented": self.llm_augmented,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
        }


class PluginAuditLog(db.Model):
    """Audit log for plugin security decisions.

    Events: scan_approved, scan_warn_accepted, scan_skipped, scan_blocked, scan_override
    """

    __tablename__ = "plugin_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(200), nullable=False)
    event = db.Column(db.String(50), nullable=False)
    verdict = db.Column(db.String(10), nullable=True)   # APPROVE | WARN | BLOCK | None
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    actor_username = db.Column(db.String(80), nullable=True)
    detail_json = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "event": self.event,
            "verdict": self.verdict,
            "actor_user_id": self.actor_user_id,
            "actor_username": self.actor_username,
            "detail": json.loads(self.detail_json or "{}"),
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if self.created_at else None,
        }
