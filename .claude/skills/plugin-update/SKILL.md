---
name: plugin-update
description: Update an installed EvoNexus plugin to a newer version from its source. Use when the user asks to update, upgrade, or bump a plugin to the latest version. Triggers on "atualiza plugin X", "update plugin X", "upgrade plugin Y".
metadata:
  category: plugins
  version: 1.0.0
---

# Plugin Update

Pull the latest version of an installed plugin and re-run the install flow with migrations applied safely.

## How to use

### Step 1 — Confirm installed + check for update

```python
from dashboard.backend.sdk_client import evo

detail = evo.get(f"/api/plugins/{slug}")
current_version = detail["version"]
```

If 404, redirect to `plugin-install`.

Check marketplace for latest:

```python
marketplace = evo.get("/api/plugins/marketplace")
latest = next((p for p in marketplace if p["id"] == slug), None)
```

If `latest.version == current_version`, tell user it's already up to date and stop.

### Step 2 — Show changelog and impact

Show user:

- Current version → Target version
- If `latest` has `changelog` or `release_notes`, display it
- If the new version adds new `env_vars_required` the user hasn't set, flag it
- If the new version has new migrations, preview the SQL

### Step 3 — Require explicit confirmation

Accept only clear affirmatives. Cancel is always safe — current version keeps working.

### Step 4 — Run update

```python
result = evo.post(f"/api/plugins/{slug}/update", {})
```

### Step 5 — Report

On success, summarise version change + anything that needs user action (new env var, scheduler restart if routines changed).

## Error handling

- `HTTP 404 not_found` — plugin not installed. Suggest `plugin-install`.
- `HTTP 409 migration_chain_pending` — previous install/update left pending migration. Tell user to resolve manually (usually means checking plugin state file) before retrying.
- `HTTP 409 already_latest` — nothing to do.
- `HTTP 500 update_failed` — install flow failed mid-way. Backend's crash recovery will revert; previous version should still work. Surface the specific step that failed.

## Notes for the agent

- Update runs full install flow on the new version — pre-install hook, migrations, copy, post-install.
- Rollback: backend keeps previous manifest until new install finalizes. If new install fails, previous remains intact.
- If plugin is `tier: full-stack` with subprocess backend, the subprocess is restarted by the updater.
