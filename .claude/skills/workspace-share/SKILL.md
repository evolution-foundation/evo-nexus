---
name: workspace-share
description: Create, list, or revoke public share links for workspace files. Use when the user says "share this file", "create a public link", "share the dashboard", "compartilha esse arquivo", "list my share links", "revoke share link", or wants to share a report/HTML with someone external.
---

# workspace-share

## Trigger

Use this skill when the user wants to:
- Share a workspace file with an external person
- Generate a public URL for a file (report, dashboard, HTML, markdown)
- List or manage active share links
- Revoke a share link

## Actions

Use `from dashboard.backend.sdk_client import evo` — auto-handles URL + auth.

### 1. Create share link

Accepts a file path (explicit or resolved from context, e.g., "today's dashboard").

**Smart resolution:** If the user says "share the financial pulse" or "share today's report", search `workspace/` for the most recent matching file (e.g., `workspace/finance/*pulse*.html`) and confirm with the user before sharing.

```python
from dashboard.backend.sdk_client import evo
share = evo.post("/api/shares", {
    "path": "<repo-relative-path>",
    "expires_in": "7d",
})
```

`expires_in` options: `"1h"`, `"24h"`, `"7d"`, `"30d"`, or `null` (no expiration).

Returns `{ token, url, expires_at }`. Present the `url` to the user — this is the public link.

### 2. List active shares

```python
from dashboard.backend.sdk_client import evo
result = evo.get("/api/shares")
```

Returns `{ shares: [...] }`. Format as a readable table showing: file path, created by, created at, expires at, views, status.

### 3. Revoke share

Accepts a token or identifies the share from context (e.g., file path).

```python
from dashboard.backend.sdk_client import evo
evo.delete(f"/api/shares/{token}")
```

Confirm with the user before revoking (the link becomes immediately inaccessible).

## Notes

- Only files inside `workspace/` can be shared (no admin paths)
- HTML files render natively in the browser (no login required)
- Markdown and code files are rendered by the EvoNexus share viewer
- Each view is counted and logged in the audit trail
