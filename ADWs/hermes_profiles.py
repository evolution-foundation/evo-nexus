"""Hermes profile registry and deterministic routing.

Fase 2 da integração Hermes↔EvoNexus. Resolve, para cada tarefa, qual perfil
Hermes (``~/.hermes/profiles/<slug>``) deve executá-la, aplicando roteamento
determinístico por ``task_type`` com override manual e fail-safe de baixo
privilégio.

Módulo puro: sem I/O de rede, sem dependências do Flask. Testável isolado.

Mecanismo de seleção por invocação: ``hermes -p <slug> chat ...`` (flag global
antes do subcommand). Não altera ``~/.hermes/active_profile``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PROFILES_CONFIG = WORKSPACE / "config" / "hermes_profiles.json"
PROFILES_EXAMPLE = WORKSPACE / "config" / "hermes_profiles.example.json"

# Slug syntax allowlist — mesma disciplina anti-injection das allowlists do runner.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Fallback embutido caso a config esteja ausente/corrompida.
_DEFAULT_FALLBACK = "default-mac"
_DEFAULT_PROFILES_DIR = "~/.hermes/profiles"


def load_routing() -> dict:
    """Read config/hermes_profiles.json. If missing, copy from the example.

    Falls back to a minimal safe structure if both are unreadable.
    """
    try:
        if not PROFILES_CONFIG.is_file() and PROFILES_EXAMPLE.is_file():
            shutil.copy2(PROFILES_EXAMPLE, PROFILES_CONFIG)
        if PROFILES_CONFIG.is_file():
            config = json.loads(PROFILES_CONFIG.read_text(encoding="utf-8"))
            config.setdefault("routing", {})
            config.setdefault("fallback_profile", _DEFAULT_FALLBACK)
            config.setdefault("profiles_dir", _DEFAULT_PROFILES_DIR)
            return config
    except (json.JSONDecodeError, OSError):
        pass
    return {
        "routing": {},
        "fallback_profile": _DEFAULT_FALLBACK,
        "profiles_dir": _DEFAULT_PROFILES_DIR,
    }


def _profiles_dir(config: dict | None = None) -> Path:
    config = config or load_routing()
    raw = config.get("profiles_dir") or _DEFAULT_PROFILES_DIR
    return Path(raw).expanduser()


def list_installed_profiles(config: dict | None = None) -> list[str]:
    """Return the slugs of profiles actually present on disk (sorted)."""
    base = _profiles_dir(config)
    try:
        return sorted(
            p.name
            for p in base.iterdir()
            if p.is_dir() and _SLUG_RE.match(p.name)
        )
    except OSError:
        return []


def is_valid_slug(slug: str | None) -> bool:
    """True if slug is syntactically valid (does not check installation)."""
    return bool(slug) and bool(_SLUG_RE.match(slug))


def resolve_profile(
    task_type: str | None,
    override: str | None = None,
    config: dict | None = None,
) -> tuple[str, str]:
    """Resolve the Hermes profile slug for a task.

    Precedence:
      1. ``override`` — if a valid, installed profile → (override, "manual_override").
      2. routing[task_type] — if mapped and installed → (slug, "routed:<task_type>").
      3. fallback_profile — if installed → (fallback, "fallback").

    Fail-safe: an unknown/invalid override or task_type never escalates
    privilege; it degrades to the fallback profile. If not even the fallback
    is installed, raises RuntimeError rather than executing a phantom profile.

    Returns (slug, reason).
    """
    config = config or load_routing()
    installed = set(list_installed_profiles(config))
    fallback = config.get("fallback_profile") or _DEFAULT_FALLBACK

    # 1. Manual override (only if valid AND installed — else fall through).
    if override:
        if is_valid_slug(override) and override in installed:
            return override, "manual_override"
        # invalid/uninstalled override is ignored (fail-safe, no escalation)

    # 2. Deterministic routing by task_type.
    if task_type:
        routed = config.get("routing", {}).get(task_type)
        if routed and is_valid_slug(routed) and routed in installed:
            return routed, f"routed:{task_type}"

    # 3. Fallback (low-privilege baseline).
    if fallback in installed:
        return fallback, "fallback"

    raise RuntimeError(
        f"No installable Hermes profile: fallback '{fallback}' not found in "
        f"{_profiles_dir(config)}. Installed: {sorted(installed) or 'none'}."
    )
