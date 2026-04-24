---
name: plugin-install
description: Install an EvoNexus plugin from a git URL or uploaded archive. Use when the user asks to install a plugin, add a plugin, or mentions a plugin source URL (github:user/repo, https://..., or a ZIP/tar.gz upload). Triggers on phrases like "instala plugin", "install plugin X", "adicionar plugin", "quero instalar Y".
metadata:
  category: plugins
  version: 1.0.0
---

# Plugin Install

Install an EvoNexus plugin through the `/api/plugins/*` endpoints. Plugins execute arbitrary code (agents, skills, shell hooks, SQL migrations) — always surface the preview and require explicit confirmation before running the install.

## How to use

### Step 1 — Obtain the source URL

Ask for the plugin source if not provided. Accepted forms:

- `github:user/repo` — default branch
- `github:user/repo@v1.2.3` — specific tag or branch
- `https://…/archive.tar.gz` or `https://…/archive.zip` — HTTPS tarball / zip URL
- Uploaded archive (`.zip` / `.tar.gz`) — via the `/plugins` upload flow

Local filesystem paths (`/abs/path`, `./rel`, `~/...`) and non-HTTPS schemes (`file://`, `ssh://`, etc.) are **rejected by the backend** — do not try to work around that. If the user has a local checkout, tell them to push to a branch, create a release tag, or zip the plugin directory and upload it.

### Step 2 — Preview the manifest

Use the EvoClient SDK (auto-resolves URL + Bearer auth):

```python
from dashboard.backend.sdk_client import evo

preview = evo.post("/api/plugins/preview", {"source_url": "<URL>"})
```

Show the user, in this order:

- **Identidade:** id, name, version, author, license
- **Capabilities:** agents, skills, commands, rules, heartbeats, routines, widgets, migrations, claude_hooks (only keys present)
- **Env vars exigidas:** `env_vars_required`. Must exist in `.env`.
- **`install.sql` completo** (expanded): full SQL. Runs in transaction against dashboard DB.
- **Hooks shell completos:** full text of `hooks/pre-install.sh` and `hooks/post-install.sh`.
- **Claude hook handlers** (if any): full text of `claude_hook_handlers/*.{py,sh}`.

Do not abbreviate. The user needs to read everything that will execute.

### Step 3 — Require explicit confirmation

Accept only clear affirmatives ("sim", "confirmar", "pode instalar", "ok"). If the user hesitates or asks for changes, stop and answer — do not install.

### Step 4 — Run the install

```python
result = evo.post("/api/plugins/install", {
    "source_url": "<URL>",
    "confirmed": True,
})
```

### Step 5 — Report the outcome

On success (`{"status":"active","id":"<slug>"}`), summarise what the user now has: agents under `.claude/agents/plugin-<slug>-*`, heartbeats visible in `/heartbeats`, routines (scheduler reload on SIGHUP), widgets on Overview next page load.

If scheduler was offline and plugin has routines, backend returns `routine_activation_pending=true` — explain the user needs to start the scheduler.

## Error handling

- `HTTP 400 invalid_source_url` — URL failed source whitelist. Reprompt.
- `HTTP 400 schema_invalid` — manifest rejected by Pydantic. Show validation error.
- `HTTP 409 slug_conflict` — plugin id already installed. Offer uninstall first or a different one.
- `HTTP 409 install_in_progress` — another install running. Retry or check `/plugins` UI.
- `HTTP 500 migration_failed` — SQL transaction rolled back. DB unchanged. Surface SQLite error.
- Other 5xx — capture body, suggest checking `ADWs/logs/plugins/`.

## Notes for the agent

- Never skip preview, even with high-trust URLs.
- Never pass `confirmed=True` without explicit user green light.
- Install one plugin at a time — if user asks for several, preview+confirm each in sequence.
