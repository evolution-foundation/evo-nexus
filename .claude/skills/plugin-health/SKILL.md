---
name: plugin-health
description: Check an installed plugin's health — verify manifest SHA256 matches files on disk, detect tampering or corruption. Use when the user asks if a plugin is working, reports a bug, or wants to validate integrity. Triggers on "plugin X está funcionando?", "health plugin X", "status plugin X", "está ok o plugin Y?".
metadata:
  category: plugins
  version: 1.0.0
---

# Plugin Health

Run on-demand integrity check for a plugin. Compares `.install-manifest.json` SHA256 entries against actual files on disk. Detects tampering, partial corruption, or missing files.

## How to use

### Step 1 — Resolve the slug

If not given, list and ask:

```python
from dashboard.backend.sdk_client import evo

plugins = evo.get("/api/plugins")
```

### Step 2 — Run health check

```python
health = evo.get(f"/api/plugins/{slug}/health")
```

Response shape:

```json
{
  "status": "active" | "broken" | "routine_activation_pending",
  "reason": "sha_mismatch" | "missing_files" | null,
  "files": [".claude/agents/plugin-foo-agent.md", ...],  // if broken
  "checked_at": "..."
}
```

### Step 3 — Report

**If `active`:** plugin is healthy. Summarise: total files checked, all SHA256 matching, registered hooks/heartbeats/routines still wired.

**If `broken`:**
- List corrupted/missing files
- Suggest options:
  - Reinstall: `plugin-install` with same source URL (overwrites files, preserves data)
  - Uninstall + reinstall: clean slate
  - Manual fix: edit files back to original state (if user knows what was changed)

**If `routine_activation_pending`:** scheduler was offline during install. Tell user to start/restart scheduler.

## Error handling

- `HTTP 404 not_found` — plugin not installed.
- `HTTP 500 health_check_failed` — filesystem permission issue or manifest unreadable. Check `ADWs/logs/plugins/`.

## Notes for the agent

- Broken state doesn't auto-disable. Plugin files may still work partially. User should decide whether to act.
- If multiple plugins broken at once, likely the user did a git operation or `.claude/` migration. Health check is diagnostic, not autofix.
