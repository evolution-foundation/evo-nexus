"""Auth endpoints — login, logout, setup, users, audit."""

from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, request, jsonify, abort
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, AuditLog, Role, BrainRepoConfig, has_permission, audit, needs_setup, needs_onboarding, get_role_permissions, get_role_agent_access, get_role_workspace_folders, ALL_RESOURCES, AGENT_LAYERS
from auth_security import (
    clear_login_throttles,
    get_active_login_lockout,
    normalize_login_key,
    password_policy_violations,
    record_login_failure,
)

bp = Blueprint("auth", __name__)


# ── Permission decorator ─────────────────────────────

def require_permission(resource, action):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not has_permission(current_user.role, resource, action):
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def _require_password_strength(password: str, *, username: str = "", email: str = ""):
    violations = password_policy_violations(password, username=username, email=email)
    if violations:
        abort(400, description="; ".join(violations))


def _lockout_description(lock_until: datetime) -> str:
    remaining = max(0, int((lock_until - datetime.now(timezone.utc)).total_seconds()))
    minutes = max(1, (remaining + 59) // 60)
    return f"Too many login attempts. Try again in {minutes} minute(s)."


def _as_text(value) -> str:
    return "" if value is None else str(value)


# ── Setup (first run only) ───────────────────────────

@bp.route("/api/auth/needs-setup")
def check_setup():
    return jsonify({"needs_setup": needs_setup()})


@bp.route("/api/auth/setup", methods=["POST"])
def setup():
    if not needs_setup():
        abort(404, description="Setup already completed")

    data = request.get_json()
    if not data:
        abort(400, description="Missing JSON body")

    username = _as_text(data.get("username")).strip()
    email = _as_text(data.get("email")).strip()
    display_name = _as_text(data.get("display_name")).strip()
    password = _as_text(data.get("password"))

    if not username or not password:
        abort(400, description="Username and password are required")
    if not email:
        abort(400, description="Email is required for license registration")
    _require_password_strength(password, username=username, email=email)

    # Save workspace config if provided
    workspace_data = data.get("workspace")
    if workspace_data:
        _save_workspace_config(workspace_data)

    user = User(
        username=username,
        email=email or None,
        display_name=display_name or username,
        role="admin",
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    login_user(user, remember=True)
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()

    audit(user, "setup_completed")
    audit(user, "login")

    # License registration (direct — no OAuth, no redirect)
    try:
        from licensing import setup_perform
        setup_perform(
            email=email or "",
            name=display_name or username,
            client_ip=request.remote_addr,
        )
    except Exception:
        pass  # Never block setup if licensing server is offline

    return jsonify({"user": user.to_dict(), "message": "Setup complete"})


def _save_workspace_config(ws: dict):
    """Generate workspace.yaml and CLAUDE.md from setup data."""
    import yaml
    from pathlib import Path
    from routes._helpers import WORKSPACE

    config_dir = WORKSPACE / "config"
    config_dir.mkdir(exist_ok=True)

    # Build workspace.yaml
    config = {
        "workspace": {
            "name": f"{ws.get('company_name', '')} Workspace".strip(),
            "owner": ws.get("owner_name", ""),
            "company": ws.get("company_name", ""),
            "timezone": ws.get("timezone", "UTC"),
            "language": ws.get("language", "pt-BR"),
        },
        "agents": {a: True for a in ws.get("agents", [])},
        "integrations": {i: True for i in ws.get("integrations", [])},
        "dashboard": {"port": 8080},
    }

    yaml_path = config_dir / "workspace.yaml"
    # encoding="utf-8" is required — otherwise on Windows Python defaults to
    # cp1252 and mangles accented characters in owner/company names
    # (e.g. "João" becomes "Jo?o" on read).
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    # Generate CLAUDE.md inline (no template needed)
    claude_md_path = WORKSPACE / "CLAUDE.md"
    if not claude_md_path.exists():
        ws = config["workspace"]
        claude_md_path.write_text(
            f"# {ws['name']} — Claude Context File\n\n"
            f"Claude reads this file at the start of every session.\n\n"
            f"## Who I Am\n\n"
            f"**Name:** {ws['owner']}\n"
            f"**Company:** {ws['company']}\n"
            f"**Timezone:** {ws['timezone']}\n\n"
            f"## Language\n\nAlways respond in **{ws['language']}**.\n",
            encoding="utf-8",
        )

    # Create workspace folders
    folders = ["daily-logs", "projects", "community", "social", "finance", "meetings", "courses", "strategy"]
    for folder in folders:
        (WORKSPACE / folder).mkdir(exist_ok=True)


# ── Login / Logout ───────────────────────────────────

@bp.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data:
        abort(400)

    username = _as_text(data.get("username")).strip()
    password = _as_text(data.get("password"))
    normalized_username = normalize_login_key(username)

    if not username or not password:
        abort(400, description="Username and password are required")

    lock_until = get_active_login_lockout(normalized_username, request.remote_addr)
    if lock_until and lock_until > datetime.now(timezone.utc):
        audit(
            None,
            "login_locked",
            detail=f"username={normalized_username or '<empty>'}; ip={request.remote_addr or '<unknown>'}",
        )
        abort(429, description=_lockout_description(lock_until))

    user = User.query.filter_by(username=username, is_active=True).first()
    if not user or not user.check_password(password):
        lock_until = record_login_failure(normalized_username, request.remote_addr)
        entry = AuditLog(
            username=username,
            action="login_failed",
            ip_address=request.remote_addr,
        )
        db.session.add(entry)
        if lock_until and lock_until > datetime.now(timezone.utc):
            db.session.add(
                AuditLog(
                    username=username,
                    action="login_locked",
                    ip_address=request.remote_addr,
                    detail=_lockout_description(lock_until),
                )
            )
            db.session.commit()
            abort(429, description=_lockout_description(lock_until))
        db.session.commit()
        abort(401, description="Invalid username or password")

    login_user(user, remember=True)
    user.last_login = datetime.now(timezone.utc)
    clear_login_throttles(normalized_username, request.remote_addr)
    db.session.commit()

    audit(user, "login")

    # Attempt licensing setup on login (if not yet active)
    try:
        from licensing import attempt_setup_on_login
        attempt_setup_on_login(
            email=user.email or "",
            name=user.display_name or user.username,
            client_ip=request.remote_addr,
        )
    except Exception:
        pass

    return jsonify({"user": user.to_dict()})


@bp.route("/api/auth/logout", methods=["POST"])
@login_required
def logout():
    audit(current_user, "logout")
    logout_user()
    return jsonify({"message": "Logged out"})


@bp.route("/api/auth/me")
@login_required
def me():
    perms = get_role_permissions(current_user.role)
    agent_access = get_role_agent_access(current_user.role)
    workspace_folders = get_role_workspace_folders(current_user.role)
    return jsonify({
        "user": current_user.to_dict(),
        "permissions": perms,
        "agent_access": agent_access,
        "workspace_folders": workspace_folders,
    })


@bp.route("/api/auth/change-password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json() or {}
    old_pw = _as_text(data.get("old_password"))
    new_pw = _as_text(data.get("new_password"))

    if not current_user.check_password(old_pw):
        abort(400, description="Current password is incorrect")
    _require_password_strength(new_pw, username=current_user.username, email=current_user.email or "")

    current_user.set_password(new_pw)
    db.session.commit()
    audit(current_user, "password_changed", f"user:{current_user.id}")
    return jsonify({"message": "Password changed"})


# ── Users (admin only) ───────────────────────────────

@bp.route("/api/users")
@login_required
@require_permission("users", "view")
def list_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify([u.to_dict() for u in users])


@bp.route("/api/users", methods=["POST"])
@login_required
@require_permission("users", "manage")
def create_user():
    data = request.get_json() or {}
    username = _as_text(data.get("username")).strip()
    password = _as_text(data.get("password"))
    role = data.get("role", "viewer")

    if not username or not password:
        abort(400, description="Username and password required")
    if User.query.filter_by(username=username).first():
        abort(400, description="Username already exists")
    valid_roles = [r.name for r in Role.query.all()]
    if role not in valid_roles:
        abort(400, description=f"Invalid role. Valid: {', '.join(valid_roles)}")
    _require_password_strength(password, username=username, email=_as_text(data.get("email")).strip())

    user = User(
        username=username,
        email=_as_text(data.get("email")).strip() or None,
        display_name=_as_text(data.get("display_name")).strip() or username,
        role=role,
        created_by=current_user.id,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    audit(current_user, "user_created", f"user:{user.id}", f"role={role}")
    return jsonify(user.to_dict()), 201


@bp.route("/api/users/<int:user_id>", methods=["PUT"])
@login_required
@require_permission("users", "manage")
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json() or {}

    if "display_name" in data:
        user.display_name = _as_text(data["display_name"]).strip()
    if "email" in data:
        user.email = _as_text(data["email"]).strip() or None
    valid_roles = [r.name for r in Role.query.all()]
    if "role" in data and data["role"] in valid_roles:
        if user.id == current_user.id and data["role"] != user.role:
            abort(400, description="Cannot change your own role")
        old_role = user.role
        user.role = data["role"]
        audit(current_user, "user_role_changed", f"user:{user.id}", f"{old_role} -> {user.role}")
    if "is_active" in data:
        user.is_active = data["is_active"]
    if "password" in data and data["password"]:
        new_password = _as_text(data["password"])
        _require_password_strength(new_password, username=user.username, email=user.email or "")
        user.set_password(new_password)

    db.session.commit()
    return jsonify(user.to_dict())


@bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
@require_permission("users", "manage")
def deactivate_user(user_id):
    if user_id == current_user.id:
        abort(400, description="Cannot deactivate yourself")
    user = User.query.get_or_404(user_id)
    user.is_active = False
    db.session.commit()
    audit(current_user, "user_deactivated", f"user:{user.id}")
    return jsonify({"message": "User deactivated"})


# ── Audit (admin only) ───────────────────────────────

@bp.route("/api/audit")
@login_required
@require_permission("audit", "view")
def list_audit():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    user_filter = request.args.get("user")
    action_filter = request.args.get("action")

    query = AuditLog.query.order_by(AuditLog.created_at.desc())
    if user_filter:
        query = query.filter(AuditLog.username == user_filter)
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    total = query.count()
    entries = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        "entries": [e.to_dict() for e in entries],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


# ── Roles (admin only) ─────────────────────────────

@bp.route("/api/roles")
@login_required
@require_permission("users", "view")
def list_roles():
    roles = Role.query.order_by(Role.name).all()
    return jsonify([r.to_dict() for r in roles])


@bp.route("/api/roles/resources")
@login_required
@require_permission("users", "view")
def list_resources():
    """List all available resources and their possible actions."""
    return jsonify(ALL_RESOURCES)


@bp.route("/api/roles/agent-layers")
@login_required
@require_permission("users", "view")
def list_agent_layers():
    """Return AGENT_LAYERS mapping for frontend grouping."""
    return jsonify(AGENT_LAYERS)


@bp.route("/api/roles/workspace-folders")
@login_required
@require_permission("users", "view")
def list_workspace_folders():
    """Return sorted list of top-level workspace folder names from disk."""
    import os
    from pathlib import Path
    workspace_path = Path(__file__).resolve().parents[3] / "workspace"
    folders = []
    if workspace_path.is_dir():
        blocklist = {
            ".git", "node_modules", "dist", ".venv", "__pycache__",
            "backups", ".mypy_cache", ".pytest_cache", "target", "build",
            ".next", ".turbo", "coverage", ".trash",
        }
        for name in sorted(os.listdir(workspace_path)):
            full = workspace_path / name
            if full.is_dir() and not name.startswith('.') and name not in blocklist:
                folders.append(name)
    return jsonify({"folders": folders})


@bp.route("/api/roles", methods=["POST"])
@login_required
@require_permission("users", "manage")
def create_role():
    data = request.get_json()
    name = data.get("name", "").strip().lower()
    description = data.get("description", "").strip()
    permissions = data.get("permissions", {})

    if not name:
        abort(400, description="Role name is required")
    if not name.isalnum() and "_" not in name:
        abort(400, description="Role name must be alphanumeric (underscores allowed)")
    if Role.query.filter_by(name=name).first():
        abort(400, description="Role already exists")

    role = Role(name=name, description=description)
    role.permissions = permissions
    role.agent_access = data.get("agent_access", {"mode": "all"})
    role.workspace_folders = data.get("workspace_folders", {"mode": "all"})
    db.session.add(role)
    db.session.commit()

    audit(current_user, "role_created", f"role:{name}")
    return jsonify(role.to_dict()), 201


@bp.route("/api/roles/<int:role_id>", methods=["PUT"])
@login_required
@require_permission("users", "manage")
def update_role(role_id):
    role = Role.query.get_or_404(role_id)
    data = request.get_json()

    if "description" in data:
        role.description = data["description"]
    if "permissions" in data:
        role.permissions = data["permissions"]
    if "agent_access" in data:
        role.agent_access = data["agent_access"]
    if "workspace_folders" in data:
        role.workspace_folders = data["workspace_folders"]
    if "name" in data and not role.is_builtin:
        new_name = data["name"].strip().lower()
        if new_name != role.name and Role.query.filter_by(name=new_name).first():
            abort(400, description="Role name already exists")
        # Update users with old role name
        old_name = role.name
        role.name = new_name
        User.query.filter_by(role=old_name).update({"role": new_name})

    db.session.commit()
    audit(current_user, "role_updated", f"role:{role.name}")
    return jsonify(role.to_dict())


@bp.route("/api/roles/<int:role_id>", methods=["DELETE"])
@login_required
@require_permission("users", "manage")
def delete_role(role_id):
    role = Role.query.get_or_404(role_id)
    if role.is_builtin:
        abort(400, description="Cannot delete built-in roles")
    if User.query.filter_by(role=role.name).count() > 0:
        abort(400, description="Cannot delete role with assigned users")

    name = role.name
    db.session.delete(role)
    db.session.commit()
    audit(current_user, "role_deleted", f"role:{name}")
    return jsonify({"message": f"Role '{name}' deleted"})


# ── Onboarding state ────────────────────────────────

@bp.route("/api/auth/needs-onboarding")
def check_onboarding():
    if needs_setup():
        return jsonify({"needs_onboarding": False, "onboarding_state": None})
    # For unauthenticated requests (checking before login redirect)
    from flask_login import current_user
    if not current_user.is_authenticated:
        return jsonify({"needs_onboarding": False, "onboarding_state": None})
    return jsonify({
        "needs_onboarding": needs_onboarding(current_user),
        "onboarding_state": current_user.onboarding_state,
    })

@bp.route("/api/auth/mark-agents-visited", methods=["POST"])
@login_required
def mark_agents_visited():
    current_user.onboarding_completed_agents_visit = True
    db.session.commit()
    return jsonify({"ok": True})


# ── License Status (admin only) ─────────────

@bp.route("/api/license/status")
@login_required
@require_permission("config", "view")
def license_status():
    from licensing import get_license_status
    return jsonify(get_license_status())
