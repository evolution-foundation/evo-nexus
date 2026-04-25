"""Pydantic v2 schema for plugin.yaml manifest validation."""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

WORKSPACE = Path(__file__).resolve().parent.parent.parent

# Slug: starts and ends with alphanum, interior may have hyphens, 3-64 chars
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

# MCP server name: lowercase alphanum + hyphens, 1-50 chars, starts with alphanum
_MCP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$|^[a-z0-9]$")

# Shell metacharacters that must never appear in MCP args or env values
_SHELL_METACHAR_RE = re.compile(r"[;&|<>`\\]")

# Allowed asset extensions for plugin icon / avatar (Wave 2.0).
# SVG is intentionally excluded — XSS surface, no sanitizer in v2.0.
_ALLOWED_ASSET_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})

# Hex SHA256 pattern (64 chars)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Semver: MAJOR.MINOR.PATCH with optional pre-release/build metadata
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))"
    r"?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# Allowed source URL schemes (Vault condition C6)
_ALLOWED_SOURCE_SCHEMES = frozenset({"https"})

# Allowed claude hook events (PRD AC-15)
_VALID_HOOK_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "Stop", "SubagentStop"}
)


class Capability(str, Enum):
    """Declared plugin capability enum."""

    agents = "agents"
    skills = "skills"
    rules = "rules"
    heartbeats = "heartbeats"
    sql_migrations = "sql_migrations"
    widgets = "widgets"
    claude_hooks = "claude_hooks"
    readonly_data = "readonly_data"
    # v1a extension — seed host-managed rows on install, tagged with
    # source_plugin so uninstall can clean them without touching user rows.
    goals = "goals"
    tasks = "tasks"
    triggers = "triggers"
    # Wave 2.1 — full-screen plugin UI pages + writable data
    ui_pages = "ui_pages"
    writable_data = "writable_data"
    # B2.0 — unauthenticated public pages served by the host (token-bound)
    public_pages = "public_pages"
    # B3 — safe uninstall with data preservation and 3-step wizard
    safe_uninstall = "safe_uninstall"


class PluginMcpServer(BaseModel):
    """Single MCP server declaration in plugin.yaml (Wave 2.3).

    The effective name injected into ~/.claude.json is ``plugin-{slug}-{name}``
    to avoid collisions with user-owned MCP entries.

    Supported interpolations in ``args`` and ``env`` values (string-replace, no shell):
      ${WORKSPACE}   — absolute path to the EvoNexus workspace
      ${PLUGIN_DIR}  — absolute path to plugins/<slug>/ directory
      ${ENV:NAME}    — value of NAME from .env file (install fails if absent)
    """

    name: Annotated[str, Field(min_length=1, max_length=50)]
    command: Literal["npx", "node", "python", "python3", "uv", "uvx", "deno"]
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_pattern(cls, v: str) -> str:
        if not _MCP_NAME_RE.match(v):
            raise ValueError(
                f"MCP server name '{v}' must match ^[a-z0-9][a-z0-9-]*[a-z0-9]$ "
                "(1-50 chars, lowercase alphanum and hyphens, start/end with alphanum)"
            )
        return v

    @field_validator("args", mode="before")
    @classmethod
    def args_no_shell_metachars(cls, v: object) -> object:
        if not isinstance(v, list):
            return v
        for item in v:
            if isinstance(item, str) and _SHELL_METACHAR_RE.search(item):
                raise ValueError(
                    f"MCP server arg '{item}' contains shell metacharacter. "
                    "Characters [;&|<>`\\] are not allowed."
                )
        return v

    @field_validator("env")
    @classmethod
    def env_keys_uppercase_and_values_safe(cls, v: Dict[str, str]) -> Dict[str, str]:
        for key, value in v.items():
            # Env keys must be uppercase identifiers
            if not re.match(r"^[A-Z][A-Z0-9_]*$", key):
                raise ValueError(
                    f"MCP env key '{key}' must be an uppercase identifier "
                    "(e.g. API_KEY, WORKSPACE_DIR)."
                )
            # Env values must not contain shell metacharacters
            if _SHELL_METACHAR_RE.search(value):
                raise ValueError(
                    f"MCP env value for '{key}' contains shell metacharacter. "
                    "Characters [;&|<>`\\] are not allowed."
                )
        return v


# ---------------------------------------------------------------------------
# Wave 2.2r — Integration specs
# ---------------------------------------------------------------------------

# Closed enum of integration categories, mirroring INTEGRATIONS in routes/integrations.py
_INTEGRATION_CATEGORIES = frozenset({
    "erp", "payments", "crm", "messaging", "community",
    "social", "productivity", "meetings", "creative", "other",
})

# Env var name: uppercase letter then uppercase letters/digits/underscores
_ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Integration slug: lowercase alphanum + hyphens, 1-50 chars
_INTEGRATION_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$|^[a-z0-9]$")


class EnvVarSpec(BaseModel):
    """A single environment variable declared in a plugin integration."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Optional[str] = None
    required: bool = False
    secret: bool = False
    default: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_uppercase(cls, v: str) -> str:
        if not _ENV_VAR_NAME_RE.match(v):
            raise ValueError(
                f"EnvVarSpec name '{v}' must match ^[A-Z][A-Z0-9_]*$ "
                "(uppercase identifier, e.g. TODOIST_PLUGIN_API_KEY)"
            )
        return v


class HealthCheckSpec(BaseModel):
    """HTTP health check declared in a plugin integration (v1 supports http only)."""

    type: Literal["http"]
    url: Annotated[str, Field(min_length=1, max_length=2000)]
    expect_status: int = 200
    timeout_seconds: int = 5

    @field_validator("timeout_seconds")
    @classmethod
    def clamp_timeout(cls, v: int) -> int:
        """Hard clamp: [1, 10] seconds (ADR decision 4)."""
        return max(1, min(10, v))

    # url validation happens in PluginIntegration model_validator (needs env_vars)


class PluginIntegration(BaseModel):
    """A single integration declared by a plugin (Wave 2.2r).

    Each integration may declare one or more env vars that skills/hooks consume,
    plus an optional HTTP health check. Values are stored in .env (never DB).
    """

    slug: Annotated[str, Field(min_length=1, max_length=50)]
    label: Annotated[str, Field(min_length=1, max_length=80)]
    category: Annotated[str, Field(min_length=1, max_length=50)]
    env_vars: List[EnvVarSpec] = Field(default_factory=list)
    health_check: Optional[HealthCheckSpec] = None

    @field_validator("slug")
    @classmethod
    def slug_pattern(cls, v: str) -> str:
        if not _INTEGRATION_SLUG_RE.match(v):
            raise ValueError(
                f"Integration slug '{v}' must match ^[a-z0-9][a-z0-9-]*[a-z0-9]$ "
                "(1-50 chars, lowercase alphanum and hyphens)"
            )
        return v

    @field_validator("category")
    @classmethod
    def category_in_enum(cls, v: str) -> str:
        if v not in _INTEGRATION_CATEGORIES:
            raise ValueError(
                f"Integration category '{v}' is not in the allowed set: "
                f"{sorted(_INTEGRATION_CATEGORIES)}"
            )
        return v

    @model_validator(mode="after")
    def env_var_names_unique(self) -> "PluginIntegration":
        """Each env var name must be unique within this integration."""
        seen: set[str] = set()
        for spec in self.env_vars:
            if spec.name in seen:
                raise ValueError(
                    f"Duplicate env var name '{spec.name}' in integration '{self.slug}'. "
                    "Each env var name must be unique within an integration."
                )
            seen.add(spec.name)
        return self

    @model_validator(mode="after")
    def health_check_url_vars_declared(self) -> "PluginIntegration":
        """${VAR} references in health_check.url must be declared in env_vars (ADR decision 4)."""
        if not self.health_check:
            return self
        declared_names = {spec.name for spec in self.env_vars}
        # Find all ${VAR} tokens in the URL
        var_refs = re.findall(r"\$\{([^}]+)\}", self.health_check.url)
        for ref in var_refs:
            if ref not in declared_names:
                raise ValueError(
                    f"health_check.url references '${{{ref}}}' which is not declared "
                    f"in env_vars of integration '{self.slug}'. Only env vars from the "
                    "same integration may be referenced to prevent credential exfiltration."
                )
        return self


class WidgetSpec(BaseModel):
    """Single UI widget declared in plugin.yaml."""

    id: Annotated[str, Field(min_length=1, max_length=100)]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    route: Annotated[str, Field(min_length=1, max_length=500)]
    icon: Optional[str] = None

    @field_validator("id")
    @classmethod
    def id_pattern(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError(f"Widget id '{v}' must match ^[a-z0-9-]+$")
        return v


class ClaudeHookSpec(BaseModel):
    """Single claude hook declaration in plugin.yaml."""

    event: str
    handler_path: str

    @field_validator("event")
    @classmethod
    def event_must_be_valid(cls, v: str) -> str:
        if v not in _VALID_HOOK_EVENTS:
            raise ValueError(
                f"claude_hook event '{v}' not in allowed set: {sorted(_VALID_HOOK_EVENTS)}"
            )
        return v

    @field_validator("handler_path")
    @classmethod
    def handler_path_must_be_safe(cls, v: str) -> str:
        """Vault condition C2: handler_path must not escape plugin directory."""
        # Reject absolute paths and obvious traversal patterns.
        # Full realpath check happens at install time in plugin_loader.py.
        if Path(v).is_absolute():
            raise ValueError(
                f"handler_path '{v}' must be relative (no absolute paths allowed)"
            )
        # Reject sequences that could traverse upward
        parts = Path(v).parts
        depth = 0
        for part in parts:
            if part == "..":
                depth -= 1
                if depth < 0:
                    raise ValueError(
                        f"handler_path '{v}' contains path traversal sequence"
                    )
            else:
                depth += 1
        return v


class ReadonlyQuery(BaseModel):
    """A named read-only SQL query exposed by the plugin."""

    id: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(min_length=1, max_length=500)]
    sql: Annotated[str, Field(min_length=1)]
    # B2.0: expose this query on the public portal without host auth.
    # Value is the PluginPublicPage.id that gates access.
    public_via: Optional[str] = None
    # B2.0: named SQL parameter in ``sql`` that receives the URL token value.
    # Required when public_via is set.  The parameter must appear in ``sql``
    # as :token_param (e.g. ``WHERE magic_link_token = :token``).
    bind_token_param: Optional[str] = None

    @field_validator("id")
    @classmethod
    def id_pattern(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError(
                f"ReadonlyQuery id '{v}' must match ^[a-z0-9_]+$"
            )
        return v

    @field_validator("sql")
    @classmethod
    def sql_must_be_readonly(cls, v: str) -> str:
        """Reject obvious write statements (full enforcement in plugin_migrator.py)."""
        stripped = v.strip().upper()
        write_keywords = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "ATTACH")
        for kw in write_keywords:
            if stripped.startswith(kw):
                raise ValueError(
                    f"ReadonlyQuery sql must be SELECT-only; found '{kw}' statement"
                )
        return v


def _validate_asset_path(v: str) -> str:
    """Shared validator for icon / avatar path fields (Wave 2.0).

    Rules (ADR decisions 2, 3, 6):
    - Must be relative (no leading slash)
    - Must not contain path traversal sequences (..)
    - Must start with 'ui/' (ensures serving by existing endpoint)
    - Must use forward slashes only
    - Extension must be in _ALLOWED_ASSET_EXTENSIONS (rejects .svg)
    - External URLs (http/https) are rejected
    """
    if v.startswith(("http://", "https://")):
        raise ValueError(
            f"Asset path '{v}' must be a relative path inside the plugin tarball. "
            "External URLs are not supported in v2.0."
        )
    if v.startswith("/"):
        raise ValueError(f"Asset path '{v}' must be relative (no leading slash).")
    # Reject backslash (Windows-style paths)
    if "\\" in v:
        raise ValueError(f"Asset path '{v}' must use forward slashes only.")
    # Reject traversal sequences
    parts = Path(v).parts
    depth = 0
    for part in parts:
        if part == "..":
            depth -= 1
            if depth < 0:
                raise ValueError(
                    f"Asset path '{v}' contains path traversal sequence '..'."
                )
        else:
            depth += 1
    # Must start with ui/
    if not v.startswith("ui/"):
        raise ValueError(
            f"Asset path '{v}' must start with 'ui/' so it is served by the "
            "existing /plugins/<slug>/ui/<path> endpoint."
        )
    # Extension whitelist
    ext = Path(v).suffix.lower()
    if ext not in _ALLOWED_ASSET_EXTENSIONS:
        raise ValueError(
            f"Asset '{v}' has extension '{ext}' which is not allowed. "
            f"Allowed extensions: {sorted(_ALLOWED_ASSET_EXTENSIONS)}. "
            "SVG is rejected due to XSS risk (see ADR decision 6)."
        )
    return v


class PluginMetadata(BaseModel):
    """Optional visual identity metadata for a plugin (Wave 2.0).

    Declared under ``metadata:`` in plugin.yaml.  All fields are optional
    at the struct level; icon is required when the metadata block is present.
    """

    icon: str
    icon_sha256: Optional[str] = None

    @field_validator("icon")
    @classmethod
    def icon_path_valid(cls, v: str) -> str:
        return _validate_asset_path(v)

    @field_validator("icon_sha256")
    @classmethod
    def icon_sha256_pattern(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _SHA256_RE.match(v):
            raise ValueError(
                f"icon_sha256 '{v}' must be a 64-character lowercase hex SHA256."
            )
        return v


class PluginAgentEntry(BaseModel):
    """Optional per-agent metadata that enriches the agent scan (Wave 2.0).

    Declared under ``agents:`` in plugin.yaml.  ``file`` is the key used to
    match against the scanned agent .md files in ``agents/`` directory.
    Existence of ``file`` in the tarball is validated at install time (not
    here) to avoid coupling schema to filesystem state.
    """

    file: str
    avatar: Optional[str] = None
    avatar_sha256: Optional[str] = None

    @field_validator("file")
    @classmethod
    def file_path_relative(cls, v: str) -> str:
        """file must be relative and not traverse up."""
        if v.startswith("/"):
            raise ValueError(f"agents[].file '{v}' must be relative.")
        if "\\" in v:
            raise ValueError(f"agents[].file '{v}' must use forward slashes.")
        parts = Path(v).parts
        depth = 0
        for part in parts:
            if part == "..":
                depth -= 1
                if depth < 0:
                    raise ValueError(
                        f"agents[].file '{v}' contains path traversal sequence '..'."
                    )
            else:
                depth += 1
        return v

    @field_validator("avatar")
    @classmethod
    def avatar_path_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_asset_path(v)
        return v

    @field_validator("avatar_sha256")
    @classmethod
    def avatar_sha256_pattern(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _SHA256_RE.match(v):
            raise ValueError(
                f"avatar_sha256 '{v}' must be a 64-character lowercase hex SHA256."
            )
        return v


class PluginPage(BaseModel):
    """A full-screen page declared in plugin.yaml under ui_entry_points.pages (Wave 2.1).

    The page bundle is a vanilla JS Web Component (no bundler, no framework).
    It is served by the existing /plugins/<slug>/ui/<path> endpoint.
    """

    id: Annotated[str, Field(min_length=1, max_length=100)]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    # React Router sub-path under /plugins-ui/<slug>/ (e.g. "projects")
    path: Annotated[str, Field(min_length=1, max_length=200)]
    # Relative path inside the tarball, e.g. "ui/pages/projects.js"
    bundle: Annotated[str, Field(min_length=1, max_length=500)]
    # Web component tag name registered by the bundle via customElements.define()
    custom_element_name: Annotated[str, Field(min_length=1, max_length=200)]
    # sidebar_group id to inject into (matches PluginSidebarGroup.id or native group)
    sidebar_group: Optional[str] = None
    # Lucide icon name or null
    icon: Optional[str] = None
    # Order within the sidebar group (lower = higher up; default 999)
    order: int = 999

    @field_validator("id")
    @classmethod
    def id_pattern(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError(f"PluginPage id '{v}' must match ^[a-z0-9-]+$")
        return v

    @field_validator("path")
    @classmethod
    def path_no_leading_slash(cls, v: str) -> str:
        if v.startswith("/"):
            raise ValueError(f"PluginPage path '{v}' must not start with '/'")
        return v

    @field_validator("bundle")
    @classmethod
    def bundle_path_valid(cls, v: str) -> str:
        """Bundle must be relative, under ui/, with a .js or .mjs extension."""
        if v.startswith(("/", "http://", "https://")):
            raise ValueError(
                f"PluginPage bundle '{v}' must be a relative path starting with 'ui/'"
            )
        if not v.startswith("ui/"):
            raise ValueError(
                f"PluginPage bundle '{v}' must start with 'ui/' to be served "
                "by the existing /plugins/<slug>/ui/<path> endpoint."
            )
        ext = Path(v).suffix.lower()
        if ext not in {".js", ".mjs"}:
            raise ValueError(
                f"PluginPage bundle '{v}' must have a .js or .mjs extension."
            )
        return v

    @field_validator("custom_element_name")
    @classmethod
    def custom_element_name_has_hyphen(cls, v: str) -> str:
        """Web Components spec: custom element names must contain at least one hyphen."""
        if "-" not in v:
            raise ValueError(
                f"custom_element_name '{v}' must contain at least one hyphen "
                "(Web Components specification requirement)."
            )
        return v


class PluginSidebarGroup(BaseModel):
    """A sidebar navigation group declared by a plugin (Wave 2.1).

    Injected into the dashboard Sidebar alongside native groups.
    """

    id: Annotated[str, Field(min_length=1, max_length=100)]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    # Rendering order among all sidebar groups; native groups occupy 1-5
    order: int = 999
    collapsible: bool = True

    @field_validator("id")
    @classmethod
    def id_pattern(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError(f"PluginSidebarGroup id '{v}' must match ^[a-z0-9-]+$")
        return v


class WritableResourceJsonSchema(BaseModel):
    """Inline JSON Schema stored in plugin.yaml for payload validation."""

    type: str = "object"
    properties: Optional[Dict[str, Any]] = None
    required: Optional[List[str]] = None
    additionalProperties: bool = True


class PluginWritableResource(BaseModel):
    """A writable data resource exposed via POST/PUT/DELETE (Wave 2.1).

    Mirrors readonly_data structure but supports mutations.
    All SQL must target {slug}_* tables only (enforced at schema and runtime).
    """

    id: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(min_length=1, max_length=500)]
    table: Annotated[str, Field(min_length=1, max_length=200)]
    # Explicit column allowlist — only these columns may appear in POST/PUT payloads
    allowed_columns: List[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list
    )
    # Optional JSON Schema for payload validation (jsonschema library)
    json_schema: Optional[WritableResourceJsonSchema] = None

    @field_validator("id")
    @classmethod
    def id_pattern(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError(
                f"WritableResource id '{v}' must match ^[a-z0-9_]+$"
            )
        return v

    @field_validator("table")
    @classmethod
    def table_pattern(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"WritableResource table '{v}' must match ^[a-z][a-z0-9_]*$"
            )
        return v


class PluginPublicPageTokenSource(BaseModel):
    """Token source declaration for a public page (B2.0).

    The host validates the incoming token against ``column`` in ``table``
    using a parametric query.  Table must be slug-prefixed (enforced by the
    PluginManifest validator ``public_pages_tables_slug_prefixed``).

    B2.0 v1 deliberately does NOT support a ``revoked_when`` SQL fragment to
    prevent SQL injection.  Revocation is the plugin's responsibility: nulling
    or rotating the token column value causes the next request to 404.
    """

    # Plugin-owned table containing the token column (validated slug-prefixed)
    table: Annotated[str, Field(min_length=1, max_length=200)]
    # Column in ``table`` that holds the token value
    column: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("table")
    @classmethod
    def table_identifier(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"token_source.table '{v}' must match ^[a-z][a-z0-9_]*$"
            )
        return v

    @field_validator("column")
    @classmethod
    def column_identifier(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"token_source.column '{v}' must match ^[a-z][a-z0-9_]*$"
            )
        return v


class PluginPublicPage(BaseModel):
    """A public (unauthenticated) page declared in plugin.yaml under public_pages (B2.0).

    The host registers ``/p/{slug}/{route_prefix}/{token}`` as a public route
    and validates the token against ``token_source.column`` in ``token_source.table``
    on every request.  Only B2.0 (read-only, no PIN) is supported in v1.
    B2.1 (PIN + writable + auto_set_columns) is deferred.
    """

    # Unique identifier within this plugin's public_pages list
    id: Annotated[str, Field(min_length=1, max_length=100)]
    # Human-readable label for audit logs and admin UI
    description: Annotated[str, Field(min_length=1, max_length=500)]
    # URL prefix segment, without leading/trailing slashes (e.g. "portal")
    route_prefix: Annotated[str, Field(min_length=1, max_length=100)]
    # Token source — which plugin table/column the URL token is validated against
    token_source: PluginPublicPageTokenSource
    # Plugin JS bundle path (must be under ui/public/)
    bundle: Annotated[str, Field(min_length=1, max_length=500)]
    # Web component tag name registered by the bundle
    custom_element_name: Annotated[str, Field(min_length=1, max_length=200)]
    # auth_mode: only "token" is supported in B2.0 (B2.1 will add "pin")
    auth_mode: Literal["token"] = "token"
    # Rate limit override per page (requests/minute/IP); defaults to global limiter
    rate_limit_per_ip: Optional[int] = None
    # Optional action name to write to the audit log on each page view
    audit_action: Optional[str] = None

    @field_validator("id")
    @classmethod
    def id_pattern(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError(f"PluginPublicPage id '{v}' must match ^[a-z0-9_]+$")
        return v

    @field_validator("route_prefix")
    @classmethod
    def route_prefix_clean(cls, v: str) -> str:
        """No leading/trailing slashes; only lowercase alphanum + hyphens."""
        v = v.strip("/")
        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", v):
            raise ValueError(
                f"route_prefix '{v}' must be lowercase alphanum+hyphens, no slashes"
            )
        return v

    @field_validator("bundle")
    @classmethod
    def bundle_in_public_subtree(cls, v: str) -> str:
        """Bundle must live under ui/public/ to prevent leaking authenticated bundles."""
        if not v.startswith("ui/public/"):
            raise ValueError(
                f"PluginPublicPage bundle '{v}' must start with 'ui/public/' "
                "(authenticated ui_pages bundles are not accessible from public routes)."
            )
        ext = Path(v).suffix.lower()
        if ext not in {".js", ".mjs"}:
            raise ValueError(
                f"PluginPublicPage bundle '{v}' must have a .js or .mjs extension."
            )
        return v

    @field_validator("custom_element_name")
    @classmethod
    def custom_element_name_has_hyphen(cls, v: str) -> str:
        if "-" not in v:
            raise ValueError(
                f"custom_element_name '{v}' must contain at least one hyphen "
                "(Web Components specification requirement)."
            )
        return v

    @field_validator("rate_limit_per_ip")
    @classmethod
    def rate_limit_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("rate_limit_per_ip must be a positive integer")
        return v


class PluginUIEntryPoints(BaseModel):
    """Typed container for ui_entry_points in plugin.yaml (Wave 2.1).

    Replaces the previous Optional[Dict[str, Any]] with typed sub-models.
    Widgets remain as raw dicts for backward compatibility; pages is new.
    """

    widgets: Optional[List[Dict[str, Any]]] = None
    pages: Optional[List[PluginPage]] = None
    sidebar_groups: Optional[List[PluginSidebarGroup]] = None


class PluginManifest(BaseModel):
    """Full plugin.yaml manifest schema for v1a."""

    # --- Identity ---
    id: Annotated[str, Field(min_length=3, max_length=64)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    version: Annotated[str, Field(min_length=5, max_length=50)]
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    author: Annotated[str, Field(min_length=1, max_length=200)]
    license: Annotated[str, Field(min_length=1, max_length=100)]
    homepage: Optional[str] = None

    # --- Compatibility ---
    min_evonexus_version: Annotated[str, Field(min_length=5, max_length=50)]
    tier: Annotated[str, Field(pattern=r"^essential$")] = "essential"

    # --- Capabilities ---
    capabilities: List[Capability] = Field(default_factory=list)

    # --- Environment variables (Vault condition R2) ---
    env_vars_needed: List[str] = Field(default_factory=list)

    # --- Conflict declarations ---
    conflicts: Dict[str, Any] = Field(default_factory=dict)

    # --- UI extensions (v1a: widgets; Wave 2.1: pages + sidebar_groups) ---
    ui_entry_points: Optional[PluginUIEntryPoints] = None

    # --- Wave 2.1: Writable data resources (POST/PUT/DELETE mutations) ---
    writable_data: Optional[List[PluginWritableResource]] = None

    # --- Dependencies (empty in v1a) ---
    dependencies: Dict[str, str] = Field(default_factory=dict)

    # --- Claude hooks (step 8, validated here for schema completeness) ---
    claude_hooks: List[ClaudeHookSpec] = Field(default_factory=list)

    # --- Readonly data queries (step 8+, validated here for completeness) ---
    readonly_data: List[ReadonlyQuery] = Field(default_factory=list)

    # --- Source URL (for remote installs, Vault condition C6) ---
    source_url: Optional[str] = None

    # --- Wave 2.0: Plugin & Agent identity (icon + avatar) ---
    # Both optional — existing plugins without these fields are unaffected.
    metadata: Optional[PluginMetadata] = None
    agents: Optional[List[PluginAgentEntry]] = None

    # --- Wave 2.3: MCP servers injected into ~/.claude.json on install ---
    # Effective name in ~/.claude.json: plugin-{slug}-{server.name}
    mcp_servers: Optional[List[PluginMcpServer]] = None

    # --- Wave 2.2r: Integrations (env vars via plugin) ---
    # Each entry declares a named integration with env vars + optional health check.
    # env_vars_needed is kept as deprecated warning-only for backwards compatibility.
    integrations: Optional[List["PluginIntegration"]] = None

    # --- B2.0: Public pages (unauthenticated, token-bound) ---
    # Declared under public_pages: in plugin.yaml.
    # Requires Capability.public_pages in capabilities list.
    public_pages: Optional[List[PluginPublicPage]] = None

    @field_validator("id")
    @classmethod
    def slug_pattern(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(
                f"Plugin id '{v}' must match ^[a-z0-9][a-z0-9-]{{1,62}}[a-z0-9]$"
            )
        return v

    @field_validator("version", "min_evonexus_version")
    @classmethod
    def semver_pattern(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(
                f"Version '{v}' must be valid semver (e.g. 1.0.0)"
            )
        return v

    @field_validator("conflicts")
    @classmethod
    def conflicts_values_not_empty(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        for key, val in v.items():
            if val == "" or val is None:
                raise ValueError(
                    f"conflicts entry '{key}' must have a non-empty value"
                )
        return v

    @field_validator("source_url")
    @classmethod
    def source_url_scheme(cls, v: Optional[str]) -> Optional[str]:
        """Vault condition C6: only https:// source URLs allowed."""
        if v is None:
            return v
        from urllib.parse import urlparse
        parsed = urlparse(v)
        if parsed.scheme not in _ALLOWED_SOURCE_SCHEMES:
            raise ValueError(
                f"source_url scheme '{parsed.scheme}' not allowed. "
                f"Only {sorted(_ALLOWED_SOURCE_SCHEMES)} permitted."
            )
        return v

    @model_validator(mode="after")
    def tier_must_be_essential_in_v1a(self) -> "PluginManifest":
        if self.tier != "essential":
            raise ValueError("v1a only supports tier='essential'")
        return self

    @model_validator(mode="after")
    def readonly_queries_use_slug_prefix(self) -> "PluginManifest":
        """ADR-4 / Vault F8: SQL queries must only reference {slug_under}_* tables.

        Extracts table names after FROM/JOIN keywords and rejects any that do not
        start with the plugin's slug prefix (hyphens replaced with underscores).
        """
        if not self.readonly_data:
            return self
        slug_under = self.id.replace("-", "_") + "_"
        # Regex: capture identifier after FROM or JOIN (ignoring subqueries / CTEs)
        _TABLE_RE = re.compile(
            r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            re.IGNORECASE,
        )
        for query in self.readonly_data:
            tables = _TABLE_RE.findall(query.sql)
            for table in tables:
                if not table.lower().startswith(slug_under):
                    raise ValueError(
                        f"ReadonlyQuery '{query.id}' references table '{table}' "
                        f"which does not start with required prefix '{slug_under}'. "
                        "All plugin queries must only access the plugin's own tables."
                    )
        return self

    @model_validator(mode="after")
    def writable_resources_use_slug_prefix(self) -> "PluginManifest":
        """Wave 2.1: writable_data table names must start with {slug_under}.

        Same guard as readonly_queries_use_slug_prefix but for the `table` field
        on each PluginWritableResource entry.
        """
        if not self.writable_data:
            return self
        slug_under = self.id.replace("-", "_") + "_"
        for resource in self.writable_data:
            if not resource.table.lower().startswith(slug_under):
                raise ValueError(
                    f"WritableResource '{resource.id}' references table "
                    f"'{resource.table}' which does not start with required prefix "
                    f"'{slug_under}'. Plugin writable resources must only target "
                    "the plugin's own tables."
                )
        return self

    @model_validator(mode="after")
    def mcp_server_names_unique(self) -> "PluginManifest":
        """Wave 2.3: MCP server names must be unique within this plugin manifest."""
        if not self.mcp_servers:
            return self
        seen: set[str] = set()
        for server in self.mcp_servers:
            if server.name in seen:
                raise ValueError(
                    f"Duplicate MCP server name '{server.name}' in mcp_servers. "
                    "Each name must be unique within the plugin manifest."
                )
            seen.add(server.name)
        return self

    @model_validator(mode="after")
    def integration_slugs_unique(self) -> "PluginManifest":
        """Wave 2.2r: integration slugs must be unique within this plugin manifest."""
        if not self.integrations:
            return self
        seen: set[str] = set()
        for integ in self.integrations:
            if integ.slug in seen:
                raise ValueError(
                    f"Duplicate integration slug '{integ.slug}' in integrations. "
                    "Each integration slug must be unique within the plugin manifest."
                )
            seen.add(integ.slug)
        return self

    @model_validator(mode="after")
    def pages_bundle_paths_unique(self) -> "PluginManifest":
        """Wave 2.1: page ids and paths within a plugin must be unique."""
        if not self.ui_entry_points or not self.ui_entry_points.pages:
            return self
        pages = self.ui_entry_points.pages
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for page in pages:
            if page.id in seen_ids:
                raise ValueError(
                    f"Duplicate PluginPage id '{page.id}' in ui_entry_points.pages."
                )
            if page.path in seen_paths:
                raise ValueError(
                    f"Duplicate PluginPage path '{page.path}' in ui_entry_points.pages."
                )
            seen_ids.add(page.id)
            seen_paths.add(page.path)
        return self


    @model_validator(mode="after")
    def public_pages_require_capability(self) -> "PluginManifest":
        """B2.0: public_pages block requires Capability.public_pages in capabilities."""
        if self.public_pages and Capability.public_pages not in self.capabilities:
            raise ValueError(
                "public_pages is declared but Capability.public_pages is missing "
                "from capabilities list."
            )
        return self

    @model_validator(mode="after")
    def public_pages_tables_slug_prefixed(self) -> "PluginManifest":
        """B2.0: token_source.table must start with {slug_under} (same guard as readonly/writable)."""
        if not self.public_pages:
            return self
        slug_under = self.id.replace("-", "_") + "_"
        for page in self.public_pages:
            table = page.token_source.table
            if not table.lower().startswith(slug_under):
                raise ValueError(
                    f"PluginPublicPage '{page.id}' token_source.table '{table}' "
                    f"does not start with required prefix '{slug_under}'. "
                    "Public page token sources must only reference the plugin's own tables."
                )
        return self

    @model_validator(mode="after")
    def public_pages_ids_unique(self) -> "PluginManifest":
        """B2.0: public page ids and route_prefixes must be unique within a plugin."""
        if not self.public_pages:
            return self
        seen_ids: set[str] = set()
        seen_prefixes: set[str] = set()
        for page in self.public_pages:
            if page.id in seen_ids:
                raise ValueError(
                    f"Duplicate PluginPublicPage id '{page.id}' in public_pages."
                )
            if page.route_prefix in seen_prefixes:
                raise ValueError(
                    f"Duplicate PluginPublicPage route_prefix '{page.route_prefix}' in public_pages."
                )
            seen_ids.add(page.id)
            seen_prefixes.add(page.route_prefix)
        return self

    @model_validator(mode="after")
    def readonly_public_via_references_valid_page(self) -> "PluginManifest":
        """B2.0: readonly_data[].public_via must reference a declared public_pages[].id."""
        has_public_via = [q for q in self.readonly_data if q.public_via]
        if not has_public_via:
            return self
        page_ids = {p.id for p in (self.public_pages or [])}
        for query in has_public_via:
            if query.public_via not in page_ids:
                raise ValueError(
                    f"ReadonlyQuery '{query.id}' references public_via='{query.public_via}' "
                    "which is not declared in public_pages."
                )
            if not query.bind_token_param:
                raise ValueError(
                    f"ReadonlyQuery '{query.id}' has public_via set but bind_token_param "
                    "is missing. The query must declare which SQL parameter receives the token."
                )
        return self


def load_plugin_manifest(plugin_dir: Path) -> PluginManifest:
    """Load and validate plugin.yaml from a plugin directory.

    Args:
        plugin_dir: Path to the installed plugin directory (must contain plugin.yaml).

    Returns:
        Validated PluginManifest instance.

    Raises:
        FileNotFoundError: If plugin.yaml does not exist.
        pydantic.ValidationError: If the manifest is invalid.
    """
    import yaml

    manifest_path = plugin_dir / "plugin.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"plugin.yaml not found in {plugin_dir}")

    with open(manifest_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return PluginManifest.model_validate(raw)
