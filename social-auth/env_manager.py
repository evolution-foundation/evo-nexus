"""Manage .env — multi-account support for social platforms."""

import os
import re
from pathlib import Path
from datetime import datetime, timezone

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# ── Low-level .env read/write ────────────────────────

def read_env() -> dict:
    """Read all vars from .env."""
    env = {}
    if not ENV_PATH.exists():
        return env
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def set_env(key: str, value: str):
    """Set or update a key in .env."""
    lines = []
    found = False
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k == key:
                        lines.append(f"{key}={value}\n")
                        found = True
                        continue
                lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        lines.append(f"{key}={value}\n")
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)


def delete_env(key: str):
    """Remove a key from .env."""
    if not ENV_PATH.exists():
        return
    lines = []
    with open(ENV_PATH) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k == key:
                    continue
            lines.append(line if line.endswith("\n") else line + "\n")
    with open(ENV_PATH, "w") as f:
        f.writelines(lines)


# ── Multi-account management ─────────────────────────
# Pattern: SOCIAL_{PLATFORM}_{N}_{FIELD}
# Example: SOCIAL_YOUTUBE_1_API_KEY, SOCIAL_YOUTUBE_1_LABEL, SOCIAL_INSTAGRAM_2_ACCESS_TOKEN

PLATFORM_FIELDS = {
    "youtube": {
        "name": "YouTube",
        "icon": "▶️",
        "auth_type": "api_key_or_oauth",
        "fields": ["API_KEY", "ACCESS_TOKEN", "REFRESH_TOKEN", "CHANNEL_ID"],
        "required_any": ["API_KEY", "ACCESS_TOKEN"],
        "expires": False,
    },
    "instagram": {
        "name": "Instagram",
        "icon": "📸",
        "auth_type": "oauth",
        "fields": ["ACCESS_TOKEN", "ACCOUNT_ID", "PAGE_TOKEN", "TOKEN_CREATED_AT"],
        "required": ["ACCESS_TOKEN"],
        "expires": True,
        "ttl_days": 60,
        "timestamp_field": "TOKEN_CREATED_AT",
    },
    "linkedin": {
        "name": "LinkedIn",
        "icon": "💼",
        "auth_type": "oauth",
        "fields": ["ACCESS_TOKEN", "PERSON_URN", "ORG_URN", "TOKEN_CREATED_AT"],
        "required": ["ACCESS_TOKEN"],
        "expires": True,
        "ttl_days": 60,
        "timestamp_field": "TOKEN_CREATED_AT",
    },
    "twitter": {
        "name": "X / Twitter",
        "icon": "𝕏",
        "auth_type": "bearer_or_oauth",
        "fields": ["BEARER_TOKEN", "ACCESS_TOKEN", "REFRESH_TOKEN", "USER_ID", "TOKEN_CREATED_AT"],
        "required_any": ["ACCESS_TOKEN", "BEARER_TOKEN"],
        "expires": True,
        "ttl_days": 180,
        "timestamp_field": "TOKEN_CREATED_AT",
    },
    "tiktok": {
        "name": "TikTok",
        "icon": "🎵",
        "auth_type": "oauth",
        "fields": ["ACCESS_TOKEN", "REFRESH_TOKEN", "OPEN_ID", "TOKEN_CREATED_AT"],
        "required": ["ACCESS_TOKEN"],
        "expires": True,
        "ttl_days": 86400,
        "timestamp_field": "TOKEN_CREATED_AT",
    },
    "twitch": {
        "name": "Twitch",
        "icon": "🎮",
        "auth_type": "client_credentials",
        "fields": ["CLIENT_ID", "CLIENT_SECRET", "ACCESS_TOKEN", "BROADCASTER_ID"],
        "required": ["CLIENT_ID", "CLIENT_SECRET"],
        "expires": False,
    },
}


def _prefix(platform: str, index: int) -> str:
    return f"SOCIAL_{platform.upper()}_{index}"


def get_accounts(platform: str) -> list[dict]:
    """Get all accounts for a platform.

    Returns list of: {"index": N, "label": "...", "status": "connected|expired|expiring|disconnected", "detail": "...", "days_left": N|None, "fields": {...}}
    """
    env = read_env()
    cfg = PLATFORM_FIELDS.get(platform, {})
    accounts = []

    # Find all indices for this platform
    pattern = re.compile(rf"^SOCIAL_{platform.upper()}_(\d+)_")
    indices = set()
    for key in env:
        m = pattern.match(key)
        if m:
            indices.add(int(m.group(1)))

    for idx in sorted(indices):
        prefix = _prefix(platform, idx)
        label = env.get(f"{prefix}_LABEL", f"Conta {idx}")
        fields = {}
        for f in cfg.get("fields", []):
            val = env.get(f"{prefix}_{f}", "")
            if val:
                fields[f] = val

        # Check required fields
        # "required" = ALL must be present; "required_any" = at least ONE must be present
        required_all = cfg.get("required", [])
        required_any = cfg.get("required_any", [])

        is_connected = True
        missing_detail = ""

        if required_any:
            if not any(f in fields for f in required_any):
                is_connected = False
                missing_detail = f"Missing: {' ou '.join(required_any)}"
        if required_all:
            missing = [f for f in required_all if f not in fields]
            if missing:
                is_connected = False
                missing_detail = f"Missing: {', '.join(missing)}"

        if not is_connected:
            status = "disconnected"
            detail = missing_detail
            days_left = None
        else:
            status = "connected"
            detail = label
            days_left = None

            if platform == "twitter" and "BEARER_TOKEN" in fields and "ACCESS_TOKEN" not in fields:
                detail = f"{label} (read-only app bearer; posting requires OAuth tweet.write)"

            # Check expiration
            if cfg.get("expires") and cfg.get("timestamp_field") and cfg["timestamp_field"] in fields:
                ts = fields.get(cfg["timestamp_field"])
                if ts:
                    try:
                        created = datetime.fromisoformat(ts)
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        elapsed = (datetime.now(timezone.utc) - created).days
                        ttl = cfg.get("ttl_days", 60)
                        days_left = ttl - elapsed
                        if days_left <= 0:
                            status = "expired"
                            detail = f"Expirou há {abs(days_left)} dias"
                        elif days_left <= 7:
                            status = "expiring"
                            detail = f"Expira em {days_left} dias"
                    except (ValueError, TypeError):
                        pass

        accounts.append({
            "index": idx,
            "label": label,
            "status": status,
            "detail": detail,
            "days_left": days_left,
            "fields": fields,
        })

    return accounts


def next_index(platform: str) -> int:
    """Get next available index for a new account."""
    accounts = get_accounts(platform)
    if not accounts:
        return 1
    return max(a["index"] for a in accounts) + 1


def save_account(platform: str, index: int, label: str, fields: dict):
    """Save an account's fields to .env."""
    prefix = _prefix(platform, index)
    set_env(f"{prefix}_LABEL", label)
    for field, value in fields.items():
        if value:
            set_env(f"{prefix}_{field}", value)


def delete_account(platform: str, index: int):
    """Remove all fields for an account."""
    prefix = _prefix(platform, index)
    env = read_env()
    for key in list(env.keys()):
        if key.startswith(prefix):
            delete_env(key)


def rename_account(platform: str, index: int, new_label: str):
    """Rename an account."""
    prefix = _prefix(platform, index)
    set_env(f"{prefix}_LABEL", new_label)


def all_platforms_with_accounts() -> list[dict]:
    """Get all platforms with their accounts for the UI."""
    result = []
    for pid, cfg in PLATFORM_FIELDS.items():
        accounts = get_accounts(pid)
        result.append({
            "id": pid,
            "name": cfg["name"],
            "icon": cfg["icon"],
            "auth_type": cfg["auth_type"],
            "accounts": accounts,
            "has_connected": any(a["status"] in ("connected", "expiring") for a in accounts),
        })
    return result
