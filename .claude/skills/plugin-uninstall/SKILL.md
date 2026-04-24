---
name: plugin-uninstall
description: Uninstall an EvoNexus plugin, reverting the files it copied and running its uninstall.sql. Use when the user asks to remove, uninstall, or delete a plugin. Triggers on "desinstala plugin X", "remove plugin X", "uninstall plugin X", "tira o plugin Y".
metadata:
  category: plugins
  version: 1.0.0
---

# Plugin Uninstall

Remove a plugin from the workspace using `DELETE /api/plugins/<slug>`. Uninstall is reverse-order via `.install-manifest.json`.

## How to use

### Step 1 — Resolve the slug

If not given, call list and ask which one:

```python
from dashboard.backend.sdk_client import evo

plugins = evo.get("/api/plugins")
```

Validate exists:

```python
detail = evo.get(f"/api/plugins/{slug}")
```

If HTTP 404, say so and stop.

### Step 2 — Show what will be removed

Read `manifest` from detail. Summarise:

- Files in `.claude/agents/plugin-<slug>-*.md`, `.claude/skills/plugin-<slug>-*/`, `.claude/commands/plugin-<slug>-*.md`, `.claude/rules/plugin-<slug>-*.md`
- Heartbeats registered by the plugin (disappear from `/heartbeats`)
- Routines (scheduler SIGHUP reload on removal)
- Claude hook handlers under `plugins/<slug>/claude-hook-handlers/`
- Contents of `migrations/uninstall.sql` — full text
- Contents of `hooks/pre-uninstall.sh` and `hooks/post-uninstall.sh` — full text

**Important**: user data created **inside** plugin-managed tables is usually DROPped by uninstall SQL. Tell the user explicitly and ask if they want to export first.

### Step 3 — Require explicit confirmation

Accept only clear affirmatives. If user hesitates, offer:

- Export first via `GET /api/plugins/<slug>/readonly-data/<query>` if declared
- Disable instead of remove: `evo.patch(f"/api/plugins/{slug}", {"enabled": False})` — reversible, keeps data

### Step 4 — Run uninstall

```python
evo.delete(f"/api/plugins/{slug}")
```

### Step 5 — Report

On success, summarise: files removed, tables dropped, hooks cleared. Point to `/plugins` for confirmation.

## Error handling

- `HTTP 404 not_found` — slug doesn't exist. Reprompt.
- `HTTP 409 uninstall_failed` — reverse-order chain failed. Surface specific step. Offer manual recovery (delete `plugins/<slug>/` after fixing, then retry).
- `HTTP 500 sql_rollback_failed` — rare. DB may be inconsistent. Check `ADWs/logs/plugins/`.

## Notes for the agent

- Disable ≠ uninstall. Disable keeps data and is reversible.
- If plugin has routines, scheduler reloads automatically. If offline, user must restart.
