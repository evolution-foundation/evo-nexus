"""
provider_store — dialect-bifurcated provider config helper.

PostgreSQL: providers live in the ``llm_providers`` table;
            active provider key lives in ``runtime_configs('active_provider')``.
SQLite:     providers stay in ``config/providers.json`` (unchanged from v0.33.x).

Public API
----------
    list_providers()                             -> dict   (same shape as providers.json)
    get_active_provider()                        -> str
    set_active_provider(slug, actor_id=None)     -> None
    update_provider_config(slug, env_vars, actor_id=None) -> None

The ``list_providers()`` return value mirrors the canonical ``providers.json``
shape so all existing call sites work unchanged:

    {
        "active_provider": "anthropic",
        "providers": {
            "anthropic": {
                "name": "Anthropic",
                "description": "...",
                "cli_command": "claude",
                "env_vars": {...},
                "requires_logout": False,
                ...
            },
            ...
        },
    }
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

from config_store import get_dialect, get_config, set_config
from db.engine import get_engine

# ---------------------------------------------------------------------------
# Paths (SQLite / fallback mode)
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _WORKSPACE_ROOT / "config"
_PROVIDERS_FILE = _CONFIG_DIR / "providers.json"
_PROVIDERS_EXAMPLE = _CONFIG_DIR / "providers.example.json"


# ---------------------------------------------------------------------------
# Internal helpers — JSON file (SQLite mode)
# ---------------------------------------------------------------------------

def _read_providers_json() -> dict:
    """Read providers.json; seed from example if missing."""
    try:
        if not _PROVIDERS_FILE.is_file():
            if _PROVIDERS_EXAMPLE.is_file():
                shutil.copy2(_PROVIDERS_EXAMPLE, _PROVIDERS_FILE)
        if _PROVIDERS_FILE.is_file():
            return json.loads(_PROVIDERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {"active_provider": "anthropic", "providers": {}}


def _write_providers_json(config: dict) -> None:
    """Write providers.json atomically via tmp-rename."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _PROVIDERS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(_PROVIDERS_FILE)


# ---------------------------------------------------------------------------
# Internal helpers — PostgreSQL mode
# ---------------------------------------------------------------------------

def _list_providers_from_db() -> list[dict]:
    """Return all rows from llm_providers as a list of dicts."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT slug, name, description, cli_command, env_vars, requires_logout, enabled FROM llm_providers ORDER BY slug")
        ).fetchall()
    result = []
    for row in rows:
        env_vars = {}
        if row.env_vars:
            try:
                env_vars = json.loads(row.env_vars)
            except (json.JSONDecodeError, TypeError):
                env_vars = {}
        result.append({
            "slug": row.slug,
            "name": row.name,
            "description": row.description or "",
            "cli_command": row.cli_command or "claude",
            "env_vars": env_vars,
            "requires_logout": bool(row.requires_logout),
            "enabled": bool(row.enabled),
        })
    return result


def _update_provider_env_vars_in_db(slug: str, env_vars: dict) -> None:
    """UPDATE llm_providers SET env_vars = :v WHERE slug = :s."""
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE llm_providers SET env_vars = :v, updated_at = NOW() WHERE slug = :s"),
            {"v": json.dumps(env_vars), "s": slug},
        )


def update_provider_cli(
    slug: str,
    cli_command: str,
    env_vars: dict,
    requires_logout: bool,
) -> None:
    """Persist a validated CLI switch and its complete environment preset."""
    if get_dialect() != "postgresql":
        raise RuntimeError("update_provider_cli is only used in PostgreSQL mode")
    if _get_provider_row(slug) is None:
        raise ValueError(f"Unknown provider slug: {slug!r}")
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE llm_providers SET cli_command = :cli, env_vars = :env, "
                "requires_logout = :logout, updated_at = NOW() WHERE slug = :slug"
            ),
            {
                "cli": cli_command,
                "env": json.dumps(env_vars),
                "logout": requires_logout,
                "slug": slug,
            },
        )


def _get_provider_row(slug: str) -> Optional[dict]:
    """Fetch a single provider row by slug. Returns None if not found."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT slug, name, description, cli_command, env_vars, requires_logout, enabled"
                " FROM llm_providers WHERE slug = :s"
            ),
            {"s": slug},
        ).fetchone()
    if row is None:
        return None
    env_vars = {}
    if row.env_vars:
        try:
            env_vars = json.loads(row.env_vars)
        except (json.JSONDecodeError, TypeError):
            env_vars = {}
    return {
        "slug": row.slug,
        "name": row.name,
        "description": row.description or "",
        "cli_command": row.cli_command or "claude",
        "env_vars": env_vars,
        "requires_logout": bool(row.requires_logout),
        "enabled": bool(row.enabled),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_providers() -> dict:
    """Return the full provider config in canonical shape.

    Shape is identical to providers.json so existing call sites need no changes:
        {
            "active_provider": "<slug>",
            "providers": {"<slug>": { name, cli_command, env_vars, ... }, ...},
        }
    """
    if get_dialect() == "postgresql":
        rows = _list_providers_from_db()
        active = get_config("active_provider", "anthropic")
        providers: dict[str, Any] = {}
        for row in rows:
            slug = row["slug"]
            providers[slug] = {
                "name": row["name"],
                "description": row["description"],
                "cli_command": row["cli_command"],
                "env_vars": row["env_vars"],
                "requires_logout": row["requires_logout"],
                "enabled": row["enabled"],
            }
        return {"active_provider": active, "providers": providers}

    # SQLite mode — delegate to JSON file.
    return _read_providers_json()


def get_active_provider() -> str:
    """Return the active provider slug."""
    if get_dialect() == "postgresql":
        return get_config("active_provider", "anthropic")
    config = _read_providers_json()
    return config.get("active_provider", "anthropic")


def set_active_provider(slug: str, actor_id: Optional[int] = None) -> None:
    """Set the active provider.

    In PG mode: validates slug exists in llm_providers, then writes
    runtime_configs(key='active_provider', value=slug).
    In SQLite mode: rewrites providers.json.
    """
    if get_dialect() == "postgresql":
        # Validate slug exists (allow "none" to disable all).
        if slug != "none":
            row = _get_provider_row(slug)
            if row is None:
                raise ValueError(f"Unknown provider slug: {slug!r}")
        set_config("active_provider", slug, actor_id=actor_id)
        return

    config = _read_providers_json()
    if slug != "none" and slug not in config.get("providers", {}):
        raise ValueError(f"Unknown provider slug: {slug!r}")
    config["active_provider"] = slug
    _write_providers_json(config)


def update_provider_config(
    slug: str,
    env_vars: dict,
    actor_id: Optional[int] = None,
) -> None:
    """Merge env_vars into a provider's existing config.

    In PG mode: UPDATE llm_providers SET env_vars = merged WHERE slug = :s.
    In SQLite mode: merge into providers.json.

    Only the provided keys are updated; other keys are preserved.
    Caller is responsible for allowlisting / sanitising env_vars before calling.
    """
    if get_dialect() == "postgresql":
        row = _get_provider_row(slug)
        if row is None:
            raise ValueError(f"Unknown provider slug: {slug!r}")
        existing = row["env_vars"]
        existing.update(env_vars)
        _update_provider_env_vars_in_db(slug, existing)
        return

    config = _read_providers_json()
    provider = config.get("providers", {}).get(slug)
    if provider is None:
        raise ValueError(f"Unknown provider slug: {slug!r}")
    existing = provider.get("env_vars", {})
    existing.update(env_vars)
    provider["env_vars"] = existing
    _write_providers_json(config)


def seed_providers_from_json(providers_json_path: Optional[Path] = None) -> int:
    """One-shot bootstrap: import providers.json into llm_providers if table is empty.

    Used by app.py to migrate existing installs when first running in PG mode.
    Idempotent — no-op if the table already has rows.

    Returns the number of rows inserted (0 if already seeded).
    """
    if get_dialect() != "postgresql":
        return 0

    with get_engine().connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM llm_providers")).scalar()
    if count and count > 0:
        return 0  # Already seeded.

    src = providers_json_path or _PROVIDERS_FILE
    if not src.is_file():
        src = _PROVIDERS_EXAMPLE
    if not src.is_file():
        return 0

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    providers = data.get("providers", {})
    if not providers:
        return 0

    inserted = 0
    with get_engine().begin() as conn:
        for slug, prov in providers.items():
            env_json = json.dumps(prov.get("env_vars", {}))
            try:
                conn.execute(
                    text("""
                        INSERT INTO llm_providers (slug, name, description, cli_command, env_vars, requires_logout)
                        VALUES (:slug, :name, :desc, :cli, :env, :req)
                        ON CONFLICT (slug) DO NOTHING
                    """),
                    {
                        "slug": slug,
                        "name": prov.get("name", slug),
                        "desc": prov.get("description", ""),
                        "cli": prov.get("cli_command", "claude"),
                        "env": env_json,
                        "req": bool(prov.get("requires_logout", False)),
                    },
                )
                inserted += 1
            except Exception:
                pass  # Non-fatal — continue with remaining providers.

    # Seed active_provider into runtime_configs if set.
    active = data.get("active_provider", "anthropic")
    if active:
        set_config("active_provider", active)

    return inserted
