"""Integrations endpoint — check configured integrations via env vars."""

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

import requests as http
from flask import Blueprint, jsonify, request
from flask_login import current_user

from models import audit
from routes.knowledge import _require_xhr

bp = Blueprint("integrations", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
SKILLS_DIR = WORKSPACE / ".claude" / "skills"

INTEGRATIONS = [
    {"name": "Omie", "key": "OMIE_APP_KEY", "category": "erp"},
    {"name": "Bling", "key": "BLING_ACCESS_TOKEN", "category": "erp"},
    {"name": "Stripe", "key": "STRIPE_SECRET_KEY", "category": "payments"},
    {"name": "Asaas", "key": "ASAAS_API_KEY", "category": "payments"},
    {"name": "Todoist", "key": "TODOIST_API_TOKEN", "category": "productivity"},
    {"name": "Fathom", "key": "FATHOM_API_KEY", "category": "meetings"},
    {"name": "Discord", "key": "DISCORD_BOT_TOKEN", "category": "community"},
    {"name": "Telegram", "key": "TELEGRAM_BOT_TOKEN", "category": "messaging"},
    {"name": "YouTube", "key": "SOCIAL_YOUTUBE_", "category": "social", "prefix": True},
    {"name": "Instagram", "key": "SOCIAL_INSTAGRAM_", "category": "social", "prefix": True},
    {"name": "LinkedIn", "key": "SOCIAL_LINKEDIN_", "category": "social", "prefix": True},
    {"name": "Evolution API", "key": "EVOLUTION_API_KEY", "category": "messaging"},
    {"name": "Evolution Go", "key": "EVOLUTION_GO_KEY", "category": "messaging"},
    {"name": "Evo CRM", "key": "EVO_CRM_TOKEN", "category": "crm"},
    {"name": "AI Image Creator", "key": "AI_IMG_CREATOR_", "category": "creative", "prefix": True},
    # Note: LLM providers (OpenAI, Anthropic, Gemini) are NOT listed here.
    # Agents/classifiers use Claude Code as the runner (subprocess); Knowledge
    # embedder accepts OpenAI as an opt-in via Knowledge Settings.
]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter between --- markers."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fm_text = "\n".join(lines[1:end])
    try:
        return yaml.safe_load(fm_text) or {}
    except Exception:
        return {}


def _quote_env_value(value: str) -> str:
    """Double-quote an env value, escaping internal quotes and backslashes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _upsert_env_vars(
    env_path: Path,
    kvs: dict,
    section_comment: str | None = None,
) -> None:
    """Upsert key=value pairs into a .env file atomically.

    - Updates existing KEY= lines in-place.
    - Appends new keys with an optional section comment before the first new one.
    - Writes atomically via tmp-file + rename.
    - Never logs values — only key names.
    """
    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)

    updated: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        matched = False
        for key in kvs:
            if re.match(rf"^{re.escape(key)}\s*=", line):
                new_lines.append(f"{key}={_quote_env_value(kvs[key])}\n")
                updated.add(key)
                log.info("upsert_env_vars: updated key %s", key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    # Append new keys not yet present
    first_new = True
    for key, value in kvs.items():
        if key not in updated:
            if first_new and section_comment:
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines.append("\n")
                new_lines.append(f"# {section_comment}\n")
                first_new = False
            new_lines.append(f"{key}={_quote_env_value(value)}\n")
            log.info("upsert_env_vars: appended key %s", key)

    content = "".join(new_lines)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=env_path.parent, prefix=".env.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, env_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _scan_custom_integrations() -> list:
    """Scan SKILLS_DIR for custom-int-* subdirs and return integration entries."""
    results = []
    if not SKILLS_DIR.is_dir():
        return results
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir() or not d.name.startswith("custom-int-"):
            continue
        slug = d.name[len("custom-int-"):]
        skill_md = d / "SKILL.md"
        fm: dict = {}
        if skill_md.exists():
            fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))

        display_name = fm.get("displayName") or slug.replace("-", " ").title()
        description = fm.get("description") or ""
        category = fm.get("category") or "other"
        env_keys = fm.get("envKeys") or []

        results.append({
            "slug": slug,
            "name": display_name,
            "category": category,
            "description": description,
            "envKeys": env_keys,
            "configured": any(bool(os.environ.get(k)) for k in env_keys) if env_keys else False,
            "status": "ok" if (env_keys and any(bool(os.environ.get(k)) for k in env_keys)) else "pending",
            "type": category,
            "kind": "custom",
        })
    return results


@bp.route("/api/integrations")
def list_integrations():
    results = []
    for integ in INTEGRATIONS:
        if integ.get("prefix"):
            configured = any(k.startswith(integ["key"]) for k in os.environ)
        else:
            configured = bool(os.environ.get(integ["key"]))

        results.append({
            "name": integ["name"],
            "category": integ["category"],
            "configured": configured,
            "status": "ok" if configured else "pending",
            "type": integ["category"],
            "kind": "core",
        })

    custom = _scan_custom_integrations()
    all_integrations = results + custom

    configured_count = sum(1 for r in all_integrations if r.get("configured"))
    return jsonify({
        "integrations": all_integrations,
        "configured_count": configured_count,
        "total_count": len(all_integrations),
    })


@bp.route("/api/integrations/custom", methods=["POST"])
def create_custom_integration():
    _require_xhr()
    data = request.get_json(silent=True) or {}
    slug = (data.get("slug") or "").strip().lower()
    display_name = (data.get("displayName") or "").strip()
    description = (data.get("description") or "").strip()
    category = (data.get("category") or "other").strip()
    env_keys = data.get("envKeys") or []
    env_values: dict = data.get("envValues") or {}

    if not slug:
        return jsonify({"error": "slug is required"}), 400
    if not SLUG_RE.match(slug):
        return jsonify({"error": "slug must be lowercase alphanumeric and hyphens only"}), 400
    if not display_name:
        return jsonify({"error": "displayName is required"}), 400

    target_dir = SKILLS_DIR / f"custom-int-{slug}"
    if target_dir.exists():
        return jsonify({"error": f"Integration '{slug}' already exists"}), 409

    # Build env block for the SKILL.md template (names only — no values)
    env_block_lines = [f"{k}=" for k in env_keys] if env_keys else ["# Add your env vars here"]
    env_block = "\n".join(env_block_lines)

    skill_content = f"""---
name: custom-int-{slug}
displayName: "{display_name}"
description: "{description}"
category: "{category}"
envKeys: {env_keys!r}
---
# {display_name}

Custom integration for {display_name}.

## Setup

Add these to your `.env`:

```
{env_block}
```

## Usage

Use `from dashboard.backend.sdk_client import evo` for any internal API calls.
Document the public endpoints, auth method, and example calls here.
"""

    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")

    # Write env values to .env (values never go into SKILL.md or the response)
    if env_values:
        env_path = WORKSPACE / ".env"
        _upsert_env_vars(env_path, env_values, section_comment=f"custom-int-{slug}")

    try:
        audit(
            current_user,
            "create_custom_integration",
            "integrations",
            f"slug={slug} keys={sorted(env_values.keys()) if env_values else []}",
        )
    except Exception:
        log.warning("integrations.create_custom_integration: audit() failed (non-fatal)", exc_info=True)

    entry = {
        "slug": slug,
        "name": display_name,
        "category": category,
        "description": description,
        "envKeys": env_keys,
        "configured": False,
        "status": "pending",
        "type": category,
        "kind": "custom",
    }
    return jsonify(entry), 201


@bp.route("/api/integrations/custom/<slug>", methods=["PATCH"])
def update_custom_integration(slug: str):
    _require_xhr()
    target_dir = SKILLS_DIR / f"custom-int-{slug}"
    if not target_dir.exists():
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(silent=True) or {}
    skill_md = target_dir / "SKILL.md"
    fm: dict = {}
    if skill_md.exists():
        fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))

    display_name = (data.get("displayName") or fm.get("displayName") or slug.replace("-", " ").title()).strip()
    description = (data.get("description") or fm.get("description") or "").strip()
    category = (data.get("category") or fm.get("category") or "other").strip()
    env_keys = data.get("envKeys") if "envKeys" in data else (fm.get("envKeys") or [])
    env_values: dict = data.get("envValues") or {}

    # Preserve existing body (content after frontmatter) if present
    existing_body = ""
    if skill_md.exists():
        raw = skill_md.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        if len(parts) == 3:
            existing_body = parts[2].strip()

    if not existing_body:
        env_block_lines = [f"{k}=" for k in env_keys] if env_keys else ["# Add your env vars here"]
        env_block = "\n".join(env_block_lines)
        existing_body = f"""# {display_name}

Custom integration for {display_name}.

## Setup

Add these to your `.env`:

```
{env_block}
```

## Usage

Use `from dashboard.backend.sdk_client import evo` for any internal API calls.
Document the public endpoints, auth method, and example calls here."""

    skill_content = f"""---
name: custom-int-{slug}
displayName: "{display_name}"
description: "{description}"
category: "{category}"
envKeys: {env_keys!r}
---
{existing_body}
"""
    skill_md.write_text(skill_content, encoding="utf-8")

    # Write env values to .env (values never go into SKILL.md or the response)
    if env_values:
        env_path = WORKSPACE / ".env"
        _upsert_env_vars(env_path, env_values, section_comment=f"custom-int-{slug}")

    try:
        audit(
            current_user,
            "update_custom_integration",
            "integrations",
            f"slug={slug} keys={sorted(env_values.keys()) if env_values else []}",
        )
    except Exception:
        log.warning("integrations.update_custom_integration: audit() failed (non-fatal)", exc_info=True)

    configured = any(bool(os.environ.get(k)) for k in env_keys) if env_keys else False
    entry = {
        "slug": slug,
        "name": display_name,
        "category": category,
        "description": description,
        "envKeys": env_keys,
        "configured": configured,
        "status": "ok" if configured else "pending",
        "type": category,
        "kind": "custom",
    }
    return jsonify(entry), 200


@bp.route("/api/integrations/custom/<slug>", methods=["DELETE"])
def delete_custom_integration(slug: str):
    _require_xhr()
    target_dir = SKILLS_DIR / f"custom-int-{slug}"
    if not target_dir.exists():
        return jsonify({"error": "Not found"}), 404
    shutil.rmtree(target_dir)
    try:
        audit(current_user, "delete_custom_integration", "integrations", f"slug={slug}")
    except Exception:
        log.warning("integrations.delete_custom_integration: audit() failed (non-fatal)", exc_info=True)
    return jsonify({"ok": True}), 200


@bp.route("/api/integrations/<name>/test", methods=["POST"])
def test_integration(name: str):
    """Basic connectivity test for an integration."""
    t0 = time.time()

    def ok(message: str = "Conexão OK") -> "tuple[object, int]":
        latency = round((time.time() - t0) * 1000)
        return jsonify({"ok": True, "message": message, "latency_ms": latency}), 200

    def fail(error: str) -> "tuple[object, int]":
        return jsonify({"ok": False, "error": error}), 200

    slug = name.lower().replace(" ", "-").replace("_", "-")

    # --- Stripe ---
    if slug == "stripe":
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not key:
            return fail("STRIPE_SECRET_KEY não configurado")
        try:
            r = http.get(
                "https://api.stripe.com/v1/charges",
                params={"limit": 1},
                auth=(key, ""),
                timeout=8,
            )
            if r.status_code == 200:
                return ok("Stripe conectado com sucesso")
            return fail(f"Stripe retornou {r.status_code}")
        except Exception as e:
            return fail(str(e))

    # --- Omie ---
    if slug == "omie":
        app_key = os.environ.get("OMIE_APP_KEY", "")
        app_secret = os.environ.get("OMIE_APP_SECRET", "")
        if not app_key or not app_secret:
            return fail("OMIE_APP_KEY e OMIE_APP_SECRET não configurados")
        try:
            r = http.post(
                "https://app.omie.com.br/api/v1/geral/clientes/",
                json={
                    "call": "ListarClientes",
                    "app_key": app_key,
                    "app_secret": app_secret,
                    "param": [{"pagina": 1, "registros_por_pagina": 1}],
                },
                timeout=10,
            )
            data = r.json()
            if "faultstring" in data:
                return fail(data["faultstring"])
            return ok("Omie conectado com sucesso")
        except Exception as e:
            return fail(str(e))

    # --- Evolution API ---
    if slug == "evolution-api":
        api_key = os.environ.get("EVOLUTION_API_KEY", "")
        api_url = os.environ.get("EVOLUTION_API_URL", "").rstrip("/")
        if not api_key or not api_url:
            return fail("EVOLUTION_API_KEY e EVOLUTION_API_URL não configurados")
        try:
            r = http.get(
                f"{api_url}/instance/fetchInstances",
                headers={"apikey": api_key},
                timeout=8,
            )
            if r.status_code == 200:
                return ok("Evolution API conectada com sucesso")
            return fail(f"Evolution API retornou {r.status_code}")
        except Exception as e:
            return fail(str(e))

    # --- Todoist ---
    if slug == "todoist":
        token = os.environ.get("TODOIST_API_TOKEN", "")
        if not token:
            return fail("TODOIST_API_TOKEN não configurado")
        try:
            r = http.get(
                "https://api.todoist.com/rest/v2/projects",
                headers={"Authorization": f"Bearer {token}"},
                timeout=8,
            )
            if r.status_code == 200:
                return ok("Todoist conectado com sucesso")
            return fail(f"Todoist retornou {r.status_code}")
        except Exception as e:
            return fail(str(e))

    # Passthrough for integrations without a dedicated test
    return jsonify({"ok": True, "message": "Nenhum teste disponível para esta integração"}), 200
