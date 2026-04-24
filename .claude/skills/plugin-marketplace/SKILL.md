---
name: plugin-marketplace
description: Browse the EvoNexus plugin marketplace, searching for plugins by keyword, tag, or capability. Use when the user wants to find a plugin, asks what plugins are available, or describes a need that might have a plugin. Triggers on "procura plugin de X", "marketplace de plugins", "plugins disponíveis para Y", "tem algum plugin que faz Z".
metadata:
  category: plugins
  version: 1.0.0
---

# Plugin Marketplace

Browse the curated `registry.json` from `github.com/EvolutionAPI/evonexus-plugins` plus community plugins. Help the user find what they need and hand off to `plugin-install` when they pick one.

## How to use

### Step 1 — Fetch registry

```python
from dashboard.backend.sdk_client import evo

marketplace = evo.get("/api/plugins/marketplace")
```

Cached 10min backend-side. If backend returns 503 `marketplace_unreachable`, tell the user the registry is offline and suggest direct install via git URL (`plugin-install`).

### Step 2 — Filter by user intent

Each entry: `{id, name, version, repo_url, tier, tags, description, verified, author, homepage}`.

- If user provided a keyword, match against `name`, `description`, `tags`
- If user wants "verified only", filter `verified=true`
- If user wants a specific `tier` (essential vs full-stack), filter

Default: don't filter, show everything ordered by `verified=true` first, then alphabetical.

### Step 3 — Render results

```
### 🟢 PM Essentials (verified)
Agents, skills, routines para gestão de projetos com sincronização Linear.
v1.0.0 · tags: pm, linear, showcase · MIT · @EvolutionAPI/evonexus-plugin-pm-essentials

### 🟢 Pipedrive Suite (verified)
CRM integrado: pipeline, deals, health scores.
v0.4.1 · tags: crm, pipedrive · MIT · @user/evonexus-plugin-pipedrive

### Community: Custom Linear Sync
Fork extendido com webhooks do Linear.
v2.0.0 · tags: linear, webhooks · @community/evonexus-plugin-linear-webhooks
```

Use `🟢 verified` green badge, no badge for community.

### Step 4 — Offer to install

If the user expresses interest in a specific plugin ("instala o Pipedrive", "quero esse aí"), hand off to `plugin-install` skill passing the `repo_url` directly.

## Error handling

- Empty registry (nothing returned) — possible fresh registry or fetch error. Suggest direct install via git URL.
- `HTTP 503 marketplace_unreachable` — fetcher failed (offline or GitHub down). Offer to retry or install directly by URL.

## Notes for the agent

- Don't invent plugins. Only list what backend returns.
- Verified badge is meaningful — `verified=true` means the Evolution team reviewed. Default recommendations to verified.
- If user asks "what's new", sort by installed_at descending (latest first).
- Respect tier: `tier=full-stack` plugins are bigger commitment (UI, backend), `tier=essential` are lighter.
