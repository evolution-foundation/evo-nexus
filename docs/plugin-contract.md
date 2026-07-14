# EvoNexus Plugin Contract

This document describes the plugin.yaml schema for EvoNexus plugins, including capabilities, validated fields, and host-enforced contracts.

---

## plugin.yaml — Top-Level Fields

```yaml
schema_version: "1.0"       # required; must be "1.0"
name: string                 # human-readable name
slug: string                 # kebab-case identifier; unique across plugins
version: string              # semver
description: string
author: string
capabilities:                # list of declared capabilities (see below)
  - capability_name
```

---

## Capabilities

A capability must be declared in `capabilities:` before the corresponding block is used. Unknown capabilities are rejected at install time.

| Capability | Enum value | Purpose |
|---|---|---|
| `readonly_data` | `readonly_data` | Expose plugin data to agent queries |
| `custom_tools` | `custom_tools` | Register callable tools on agents |
| `public_pages` | `public_pages` | Token-gated public web pages served by host |
| `safe_uninstall` | `safe_uninstall` | 3-step uninstall wizard with data preservation |

---

## `public_pages` — Token-Gated Public Pages

Requires `capabilities: [public_pages]`.

```yaml
public_pages:
  - id: string                       # unique within this plugin
    description: string
    route_prefix: string             # e.g. "orders"; becomes /p/<slug>/orders/<token>
    token_source:
      table: string                  # must start with <slug>_ (snake_case)
      column: string                 # column holding the access token (snake_case)
    bundle: string                   # must start with ui/public/
    custom_element_name: string      # e.g. "my-plugin-orders"
    auth_mode: token                 # only "token" supported in v1
    rate_limit_per_ip: string        # e.g. "60/minute"
    audit_action: string             # logged per request
```

### Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/p/<slug>/<prefix>/<token>` | Serve the HTML bundle (portal entry) |
| `GET` | `/p/<slug>/<prefix>/<token>/data` | Run a `public_via`-tagged readonly query |
| `GET` | `/p/<slug>/<prefix>/<token>/public-assets/<path>` | Serve static assets from `ui/public/` |

All three endpoints:
1. Validate the token parametrically against `token_source.table/column` (SQL: `SELECT 1 FROM <table> WHERE <column> = ?`)
2. Apply rate limiting (60 req/min on portal, 120 req/min on data)
3. Emit security headers (CSP, X-Content-Type-Options, Referrer-Policy, HSTS)
4. Write an audit log row

### Linking a `readonly_data` query to a public page

```yaml
readonly_data:
  queries:
    - name: order_summary
      sql: "SELECT id, status, total FROM nutri_orders WHERE id = :order_id"
      public_via: orders          # id of the public_page above
      bind_token_param: order_id  # parameter name that receives the token value
```

`public_via` must reference a declared `public_pages[].id`. When set, `bind_token_param` is required; the validated token value is injected at query time.

---

## `safe_uninstall` — 3-Step Uninstall Wizard

Requires `capabilities: [safe_uninstall]`.

```yaml
safe_uninstall:
  enabled: bool                     # true = enforce wizard; false = legacy confirm()
  block_uninstall: bool             # if true, uninstall is unconditionally blocked (409)
  reason: string                    # displayed in wizard Step 1 (regulatory context)

  user_confirmation:
    checkbox_label: string          # Step 1 checkbox text
    typed_phrase: string            # Step 3 required phrase (exact match)

  pre_uninstall_hook:
    script: string                  # relative path inside plugin dir (e.g. scripts/export.py)
    output_dir: string              # where the export lands (relative to plugin dir)
    timeout_seconds: int            # 1–600
    must_produce_file: bool         # if true, fail if output_dir is empty after hook

  preserved_tables:                 # tables to rename rather than drop
    - <slug>_tablename              # must be prefixed with <slug>_

  preserved_host_entities:          # host-managed tables with partial row preservation
    host_table_name:
      "SQL condition for rows to KEEP"
                                    # rows matching NOT (condition) are deleted

  block_uninstall: false
```

### Host enforcement

When `enabled: true`:

1. **Admin role required** — non-admin users receive 403.
2. **Confirmation phrase** — `DELETE /api/plugins/<slug>` body must include `confirmation_phrase` matching `user_confirmation.typed_phrase`.
3. **Export verification** — `exported_at` path must be provided and the file must exist.
4. **ZIP password** — `zip_password` must be present (forwarded to pre-uninstall hook if configured).
5. **Pre-uninstall hook** — if configured, runs in a sandboxed subprocess with no secret env vars (only `PLUGIN_SLUG`, `PLUGIN_VERSION`, `OUTPUT_DIR`, `DB_READONLY_PATH`). Hook failure aborts uninstall.
6. **Preserved tables** — tables listed in `preserved_tables` are renamed to `_orphan_<slug>_<tablename>` and recorded in `plugin_orphans`. They are **not dropped**.
7. **Cascade-DELETE filtering** — for tables listed in `preserved_host_entities`, only rows NOT matching the preservation condition are deleted.

### Force-uninstall escape hatch

Setting `EVONEXUS_ALLOW_FORCE_UNINSTALL=1` in the host environment bypasses all safe_uninstall checks. Every force-uninstall is logged as `plugin_uninstall_force` in the audit table with the acting user's identity. This flag is intended for emergency recovery only.

### Reinstall after safe_uninstall

On reinstall of a plugin with orphaned tables:

1. Host checks `plugin_orphans` for unrecovered rows.
2. If present, compares `tarball_sha256` of the incoming tarball against `original_sha256` recorded at uninstall time.
3. SHA256 mismatch → install blocked unless request includes `confirmed_sha256_change: true` (explicit operator acknowledgment).
4. On SHA256 match (or explicit override): orphan tables are renamed back (`_orphan_<slug>_<table>` → `<table>`) before install.sql runs.

### `plugin_orphans` table (host-managed)

```sql
CREATE TABLE plugin_orphans (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    tablename TEXT NOT NULL,        -- original name (before _orphan_ prefix)
    orphaned_at TEXT NOT NULL,
    orphaned_by_user_id INTEGER,
    original_plugin_version TEXT,
    original_sha256 TEXT,
    original_publisher_url TEXT,
    recovered_at TEXT,              -- NULL until reinstall recovery
    UNIQUE(slug, tablename)
);
```

---

## Security Notes

- Plugin SQL identifiers (`table`, `column`) are validated at install time against `^[a-z][a-z0-9_]*$`. The host never interpolates untrusted input into SQL identifiers.
- Token values in public-page routes are always bound as SQL parameters (`?`), never interpolated.
- Pre-uninstall hooks run with a read-only DB copy; no write access and no secret env vars.
- SQL in `readonly_data.queries` must not reference `_orphan_*` tables (rejected at install via schema validator).
- Rate limiting is applied at the IP level on all public endpoints (flask-limiter, in-memory storage, single-process).

---

## Changelog

| Version | Change |
|---|---|
| v1.0.0 | Initial contract: `readonly_data`, `custom_tools` |
| v1.1.0 | Added `public_pages` (B2) and `safe_uninstall` (B3) capabilities |
