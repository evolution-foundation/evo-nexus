---
name: plugin-list
description: List all installed EvoNexus plugins with status, version, and update availability. Use when the user asks about installed plugins, wants an inventory, or mentions plugins generally. Triggers on "quais plugins tenho", "list plugins", "plugins instalados", "o que tem instalado".
metadata:
  category: plugins
  version: 1.0.0
---

# Plugin List

Return the current inventory of installed plugins.

## How to use

### Step 1 — Fetch installed plugins

```python
from dashboard.backend.sdk_client import evo

plugins = evo.get("/api/plugins")
```

Response is a JSON array of `{id, name, version, status, tier, installed_at, source_url}`.

### Step 2 — (Optional) Cross-check marketplace for updates

If user hints at "desatualizados" / "outdated" / "updates":

```python
marketplace = evo.get("/api/plugins/marketplace")
```

Match installed vs marketplace by `id`; flag when `installed.version < marketplace.version` (semver).

### Step 3 — Render compact markdown table

```
| slug | name | version | status | update? |
|------|------|---------|--------|---------|
| pm-essentials | PM Essentials | 1.0.0 | active | — |
| int-pipedrive | Pipedrive Suite | 0.4.1 | active | 0.5.0 |
```

Order by `name` ascending. Hide columns user didn't ask for.

Close with a one-line summary: total installed, N with updates, N broken (`status=broken` = manifest SHA mismatch, suggest `plugin-health`).

## Error handling

- Empty `[]` — say plainly no plugins installed, offer `plugin-marketplace`.
- HTTP 5xx — verify the dashboard backend is running; don't invent data.

## Notes for the agent

- Omit `source_url` from output unless asked — long and noisy.
- If `status` is `routine_activation_pending`, surface explicitly — scheduler offline during install, restart needed.
