"""Pydantic schema for heartbeat configuration validation."""

from __future__ import annotations

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
    max_turns: Annotated[int, Field(ge=1, le=100)] = 10
    timeout_seconds: Annotated[int, Field(ge=30, le=3600)] = 600
    lock_timeout_seconds: Annotated[int, Field(ge=60)] = 1800
    wake_triggers: Annotated[List[WakeTrigger], Field(min_length=1)]
    enabled: bool = False
    goal_id: Optional[str] = None
    required_secrets: List[str] = Field(default_factory=list)
    decision_prompt: Annotated[str, Field(min_length=20)]

    @field_validator("agent")
    @classmethod
    def agent_must_exist(cls, v: str) -> str:
        agents_dir = WORKSPACE / ".claude" / "agents"
        agent_file = agents_dir / f"{v}.md"
        if not agent_file.exists():
            available = [p.stem for p in agents_dir.glob("*.md")]
            raise ValueError(
                f"Agent '{v}' not found in .claude/agents/. "
                f"Available: {sorted(available)}"
            )
        return v

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
    def interval_trigger_requires_interval_field(self) -> "HeartbeatConfig":
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


def load_heartbeats_yaml(path: Path | None = None) -> HeartbeatsFile:
    """Load and validate config/heartbeats.yaml. Raises ValidationError on failure."""
    import yaml

    if path is None:
        path = WORKSPACE / "config" / "heartbeats.yaml"

    # Bootstrap from example if user config is missing
    if not path.exists():
        example = path.parent / "heartbeats.example.yaml"
        if example.is_file():
            import shutil
            shutil.copy2(example, path)
        else:
            return HeartbeatsFile(heartbeats=[])

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return HeartbeatsFile.model_validate(raw)


def save_heartbeats_yaml(data: HeartbeatsFile, path: Path | None = None) -> None:
    """Atomically write heartbeats to config/heartbeats.yaml (temp + rename)."""
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
