"""Pydantic schema for heartbeat configuration validation.

Public entry point:
    load_heartbeats(include_plugins=True) -> HeartbeatsFile
        PG mode:  reads from the `heartbeats` DB table.
        SQLite:   reads from config/heartbeats.yaml (legacy path, unchanged).

The legacy YAML functions remain available for SQLite path and internal use:
    load_heartbeats_yaml(path, include_plugins) -> HeartbeatsFile
    save_heartbeats_yaml(data, path)            -> None
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

WORKSPACE = Path(__file__).resolve().parent.parent.parent

VALID_WAKE_TRIGGERS = frozenset(
    {"interval", "new_task", "mention", "manual", "approval_decision"}
)

WakeTrigger = Literal["interval", "new_task", "mention", "manual", "approval_decision"]


class HeartbeatConfig(BaseModel):
    """Single heartbeat definition from config/heartbeats.yaml."""

    id: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")]
    agent: Annotated[str, Field(min_length=1, max_length=100)]
    interval_seconds: Annotated[int, Field(ge=60)]
    # max_turns may be 0 ONLY for in-process handler heartbeats (see `handler`
    # and handler_contract). Claude heartbeats must be >= 1; the per-mode rule
    # is enforced in the model_validator so the error message is meaningful.
    max_turns: Annotated[int, Field(ge=0, le=100)] = 10
    timeout_seconds: Annotated[int, Field(ge=30, le=3600)] = 600
    lock_timeout_seconds: Annotated[int, Field(ge=60)] = 1800
    wake_triggers: Annotated[List[WakeTrigger], Field(min_length=1)]
    enabled: bool = False
    goal_id: Optional[str] = None
    required_secrets: List[str] = Field(default_factory=list)
    # required (>=20 chars) for Claude heartbeats, may be empty for handler
    # heartbeats; the length rule moved into handler_contract().
    decision_prompt: str = ""
    source_plugin: Optional[str] = None  # AC4: set to plugin slug for plugin-contributed heartbeats
    # (Wave 2.2r) optional Python "module.function" run in-process by
    # heartbeat_runner instead of spawning a Claude CLI subprocess. When set,
    # max_turns MUST be 0 and decision_prompt may be empty.
    handler: Optional[str] = None

    @model_validator(mode="after")
    def validate_handler_or_prompt(self) -> "HeartbeatConfig":
        """Either handler XOR a non-empty decision_prompt must be provided."""
        has_handler = bool(self.handler)
        has_prompt = len(self.decision_prompt.strip()) >= 20
        if not has_handler and not has_prompt:
            raise ValueError(
                "decision_prompt must be at least 20 characters when no handler is set"
            )
        if has_handler and self.max_turns != 0:
            raise ValueError(
                "max_turns must be 0 for handler-based heartbeats (no Claude CLI invocation)"
            )
        return self

    @field_validator("agent")
    @classmethod
    def agent_must_exist(cls, v: str) -> str:
        # Sentinel values for heartbeats that run infrastructure scripts
        # directly (not a Claude session). These don't have a .md file in
        # .claude/agents/ — they dispatch to a Python worker instead.
        # Keep this list explicit so typos still raise.
        SYSTEM_SENTINELS = {"system"}
        if v in SYSTEM_SENTINELS:
            return v
        agents_dir = WORKSPACE / ".claude" / "agents"
        agent_file = agents_dir / f"{v}.md"
        if agent_file.exists():
            return v
        # Plugin-provided agents are named `plugin-{slug}-{name}.md` and may
        # not be present on disk at boot (installed async). Skip strict file
        # check for these — runtime will resolve them when the plugin loads.
        if v.startswith("plugin-"):
            return v
        available = [p.stem for p in agents_dir.glob("*.md")]
        raise ValueError(
            f"Agent '{v}' not found in .claude/agents/. "
            f"Available: {sorted(available)}"
        )

    @field_validator("wake_triggers")
    @classmethod
    def triggers_must_be_valid(cls, v: list) -> list:
        invalid = set(v) - VALID_WAKE_TRIGGERS
        if invalid:
            raise ValueError(
                f"Invalid wake_triggers: {invalid}. "
                f"Must be subset of: {sorted(VALID_WAKE_TRIGGERS)}"
            )
        return list(dict.fromkeys(v))  # deduplicate preserving order

    @model_validator(mode="after")
    def handler_contract(self) -> "HeartbeatConfig":
        """Enforce the handler vs. Claude-heartbeat contract.

        - handler SET   -> in-process Python; max_turns MUST be 0;
                           decision_prompt may be empty.
        - handler UNSET -> Claude CLI heartbeat; max_turns MUST be 1..100 and
                           decision_prompt MUST be >= 20 chars.
        """
        handler = (self.handler or "").strip()
        if handler:
            if self.max_turns != 0:
                raise ValueError(
                    f"Heartbeat '{self.id}' sets handler='{handler}' but "
                    f"max_turns={self.max_turns}; handler heartbeats must have "
                    f"max_turns: 0 (no Claude turns are spent)."
                )
            # decision_prompt is unconstrained for handler heartbeats.
        else:
            if not (1 <= self.max_turns <= 100):
                raise ValueError(
                    f"Heartbeat '{self.id}': max_turns must be between 1 and "
                    f"100 for Claude heartbeats (got {self.max_turns}). "
                    f"max_turns: 0 is only valid when a 'handler' is set."
                )
            if len(self.decision_prompt) < 20:
                raise ValueError(
                    f"Heartbeat '{self.id}': decision_prompt must be at least "
                    f"20 characters for Claude heartbeats (got "
                    f"{len(self.decision_prompt)})."
                )
        return self


class HeartbeatsFile(BaseModel):
    """Root structure of config/heartbeats.yaml."""

    heartbeats: List[HeartbeatConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def ids_must_be_unique(self) -> "HeartbeatsFile":
        ids = [h.id for h in self.heartbeats]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"Duplicate heartbeat ids: {duplicates}")
        return self


def load_heartbeats_yaml(
    path: Path | None = None,
    include_plugins: bool = True,
) -> HeartbeatsFile:
    """Load and validate config/heartbeats.yaml, optionally merging plugin heartbeats.

    When include_plugins=True (default), globs plugins/*/heartbeats.yaml in
    alphabetical order and merges their heartbeats into the result. Each plugin
    file is parsed independently — a broken plugin YAML does NOT prevent core
    heartbeats from loading (fail-isolated, logged as ERROR).

    Duplicate heartbeat ids across files raise ValueError (second file with the
    same id is rejected; the first-seen wins).

    Args:
        path: Path to the core heartbeats.yaml. Defaults to config/heartbeats.yaml.
        include_plugins: Whether to merge plugins/*/heartbeats.yaml files.

    Returns:
        Merged HeartbeatsFile with all valid heartbeats.

    Raises:
        ValidationError: If core heartbeats.yaml is invalid.
    """
    import logging
    import yaml

    logger = logging.getLogger(__name__)

    if path is None:
        path = WORKSPACE / "config" / "heartbeats.yaml"

    # Bootstrap from example if user config is missing
    if not path.exists():
        example = path.parent / "heartbeats.example.yaml"
        if example.is_file():
            import shutil
            shutil.copy2(example, path)
        else:
            # No config and no example — return empty core
            core = HeartbeatsFile(heartbeats=[])
            if not include_plugins:
                return core
            # Fall through to plugin union below with empty core
            return _merge_plugin_heartbeats(core, logger)

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    core = HeartbeatsFile.model_validate(raw)

    if not include_plugins:
        return core

    return _merge_plugin_heartbeats(core, logger)


def _merge_plugin_heartbeats(core: "HeartbeatsFile", logger: "logging.Logger") -> "HeartbeatsFile":
    """Merge plugins/*/heartbeats.yaml files into core heartbeats.

    Each plugin file is parsed independently (fail-isolated).
    Duplicate ids log an ERROR and are skipped (first-seen wins).

    Args:
        core: The core HeartbeatsFile to extend.
        logger: Logger instance.

    Returns:
        New HeartbeatsFile with core + plugin heartbeats merged.
    """
    import yaml

    plugins_dir = WORKSPACE / "plugins"
    if not plugins_dir.exists():
        return core

    merged = list(core.heartbeats)
    seen_ids: dict[str, str] = {h.id: "config/heartbeats.yaml" for h in merged}

    plugin_yaml_files = sorted(plugins_dir.glob("*/heartbeats.yaml"))
    for plugin_yaml in plugin_yaml_files:
        plugin_slug = plugin_yaml.parent.name
        try:
            with open(plugin_yaml, encoding="utf-8") as f:
                raw_plugin = yaml.safe_load(f) or {}

            # Rewrite `agent: bare-name` -> `agent: plugin-{slug}-{bare-name}`
            # to match the file_ops prefix applied on install. Plugin authors
            # write the bare agent name in their yaml; the installer renames
            # the file and the validator must look up the prefixed name.
            for hb in raw_plugin.get("heartbeats", []) or []:
                agent = hb.get("agent")
                if isinstance(agent, str) and agent and not agent.startswith(f"plugin-{plugin_slug}-") and agent != "system":
                    hb["agent"] = f"plugin-{plugin_slug}-{agent}"

            plugin_hb_file = HeartbeatsFile.model_validate(raw_plugin)
        except Exception as exc:
            logger.error(
                "Plugin '%s' heartbeats.yaml is invalid — skipping (plugin marked broken): %s",
                plugin_slug,
                exc,
            )
            continue

        for hb in plugin_hb_file.heartbeats:
            if hb.id in seen_ids:
                logger.error(
                    "Duplicate heartbeat id '%s' in plugin '%s' "
                    "(already defined in '%s') — skipping plugin heartbeat",
                    hb.id,
                    plugin_slug,
                    seen_ids[hb.id],
                )
                continue
            seen_ids[hb.id] = str(plugin_yaml)
            # AC4: tag heartbeat with its originating plugin slug
            hb = hb.model_copy(update={"source_plugin": plugin_slug})
            merged.append(hb)

    return HeartbeatsFile(heartbeats=merged)


def save_heartbeats_yaml(data: HeartbeatsFile, path: Path | None = None) -> None:
    """Atomically write heartbeats to config/heartbeats.yaml (temp + rename).

    SQLite path only — in PG mode, use DB writes directly (CRUD endpoints).
    """
    import os
    import yaml

    if path is None:
        path = WORKSPACE / "config" / "heartbeats.yaml"

    raw = {
        "heartbeats": [
            {k: v for k, v in h.model_dump().items() if v is not None or k in ("goal_id",)}
            for h in data.heartbeats
        ]
    }

    tmp_path = path.with_suffix(".yaml.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    os.rename(tmp_path, path)


# ---------------------------------------------------------------------------
# Public dialect-aware entry point
# ---------------------------------------------------------------------------

def load_heartbeats(include_plugins: bool = True) -> HeartbeatsFile:
    """Load heartbeat definitions — dialect-bifurcated public entry point.

    PostgreSQL: reads from the ``heartbeats`` DB table.  The ``include_plugins``
    flag filters by ``source_plugin IS NULL`` when False (user-created only).

    SQLite: delegates to :func:`load_heartbeats_yaml` (unchanged legacy path).

    Args:
        include_plugins: When False, exclude plugin-contributed heartbeats.

    Returns:
        :class:`HeartbeatsFile` with all valid heartbeats.
    """
    try:
        from db.engine import get_engine
        dialect = get_engine().dialect.name
    except Exception:
        dialect = "sqlite"  # safe fallback if engine not yet initialised

    if dialect == "postgresql":
        return _load_heartbeats_from_db(include_plugins)
    return load_heartbeats_yaml(include_plugins=include_plugins)


def _load_heartbeats_from_db(include_plugins: bool) -> HeartbeatsFile:
    """Read heartbeat definitions directly from the ``heartbeats`` DB table.

    Each row is mapped to a :class:`HeartbeatConfig` using the column names
    that match the Pydantic model.  Unknown/extra DB columns are ignored.

    Rows that fail Pydantic validation are logged as ERROR and skipped so
    that a single corrupt row does not prevent other heartbeats from loading
    (same fail-isolation policy as YAML plugin loading).

    Args:
        include_plugins: When False, only rows where source_plugin IS NULL
                         are returned (user-created heartbeats only).

    Returns:
        :class:`HeartbeatsFile` containing all valid DB heartbeats.
    """
    from db.engine import get_engine
    from sqlalchemy import text as sa_text

    logger = logging.getLogger(__name__)

    query = (
        "SELECT * FROM heartbeats WHERE source_plugin IS NULL"
        if not include_plugins
        else "SELECT * FROM heartbeats"
    )

    heartbeats: list[HeartbeatConfig] = []
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(sa_text(query)).fetchall()
    except Exception as exc:
        logger.error("load_heartbeats_from_db: DB read failed: %s", exc)
        return HeartbeatsFile(heartbeats=[])

    for row in rows:
        row_dict = dict(row._mapping)
        # wake_triggers and required_secrets are stored as JSON TEXT in the DB.
        for json_col in ("wake_triggers", "required_secrets"):
            raw_val = row_dict.get(json_col)
            if isinstance(raw_val, str):
                try:
                    row_dict[json_col] = json.loads(raw_val)
                except (ValueError, TypeError):
                    row_dict[json_col] = []
            elif raw_val is None:
                row_dict[json_col] = []

        # decision_prompt may be NULL in DB (handler-based heartbeats).
        if row_dict.get("decision_prompt") is None:
            row_dict["decision_prompt"] = ""

        # handler column may not exist in older schema (0002) — tolerate absence.
        row_dict.setdefault("handler", None)

        try:
            hb = HeartbeatConfig.model_validate(row_dict)
            heartbeats.append(hb)
        except Exception as exc:
            logger.error(
                "load_heartbeats_from_db: row id=%r failed validation — skipping: %s",
                row_dict.get("id"),
                exc,
            )

    return HeartbeatsFile(heartbeats=heartbeats)
