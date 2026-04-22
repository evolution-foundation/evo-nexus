---
name: knowledge-admin
description: "Administrative operations on the Knowledge base: connect new pgvector servers, check health, view stats, export data, install parser models. Use when the user wants to configure/monitor the system ('connect a new pgvector', 'status of connections', 'how many docs do we have', 'export backup of space X', 'install Marker models')."
---

# knowledge-admin

Group: **Administration**. Consolidates connect/health/stats/export/install-parser via subcommand.

## When to trigger

- "Connect a new pgvector"
- "Status of connections"
- "How many docs are in Academy?"
- "Export space X as backup"
- "Install Marker models"

## Arguments

| Name | Type | Required | Description |
|---|---|---|---|
| `action` | str | yes | `connect` \| `health` \| `stats` \| `export` \| `install-parser` |
| (action-specific args) | | | see below |

## Actions

### `connect` — New connection wizard

Optional args: `slug`, `name`, `host`, `port`, `database`, `username`, `password`, `ssl_mode`, `connection_string`.

Flow:

1. If args missing, ask interactively (chat):
   - Name ("What do you want to call this connection?")
   - Host, port (default 5432), user, password, database, SSL mode
   - OR paste a full connection string
2. `POST /api/knowledge/connections` — register (encryption via workspace key)
3. `POST /api/knowledge/connections/:id/configure` — runs:
   - `SELECT version()` (Postgres >= 14)
   - Validate `pgvector` >= 0.5
   - PgBouncer detect (port 6543, pooler, ?pgbouncer=true) → HTTP 422 with message
   - Alembic upgrade head
   - Seed `knowledge_config`
4. Show phase-by-phase progress
5. Output: final status + next steps ("create your first space via UI or `knowledge-organize action=create`")

### `health`

```python
from dashboard.backend.sdk_client import evo

conns = evo.get("/api/knowledge/connections")
for c in conns:
    health = evo.get(f"/api/knowledge/connections/{c['id']}/health")
    # aggregate: status, schema_version, pgvector_version, chunks, spaces, last_error
```

Output:

```
| Connection | Status | Schema | pgvector | Spaces | Chunks | Last health |
|---|---|---|---|---|---|---|
| academy | ✅ ready | v3 | 0.5.1 | 5 | 12,400 | 2026-04-20 14:05 |
| acme | ⚠️ needs_mig | v2 | 0.5.0 | 2 | 3,100 | 2026-04-20 14:05 |
| staging | ❌ error | — | — | — | — | `connection refused` |
```

### `stats`

Aggregates per-connection + global stats:

```python
stats = evo.get("/api/knowledge/stats")
# { connections: [...], total_documents: N, total_chunks: M, by_content_type: {...}, growth_7d: X }
```

Output:

```
## Knowledge stats

Total documents: {N}
Total chunks: {M}
Total spaces: {S}
Growth (last 7d): +{X} docs, +{Y} chunks

### By content_type
- lesson: {N}
- tutorial: {N}
- faq: {N}
...

### Per connection
| Connection | Docs | Chunks | Spaces |
...
```

### `export`

Args: `space_id` (yes), `format` (default "jsonl"), `connection`.

```python
docs = evo.get(
    "/api/knowledge/v1/documents",
    params={"space_id": space_id, "format": "jsonl", "include_chunks": True},
    headers={"X-Knowledge-Connection": connection},
)

# Save to workspace/data/knowledge-exports/{connection}_{space_slug}_{timestamp}.jsonl
from pathlib import Path
import json
from datetime import datetime

out = Path("workspace/data/knowledge-exports") / \
      f"{connection}_{space_id}_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w") as f:
    for doc in docs:
        f.write(json.dumps(doc, ensure_ascii=False) + "\n")

print(f"Exported {len(docs)} docs to {out}")
```

### `install-parser`

Downloads Marker models (Surya OCR ~500MB). Idempotent — uses sentinel file.

```python
resp = evo.post("/api/knowledge/parsers/install", {})
# poll /api/knowledge/parsers/status until installed=true
```

Show progress. If already installed, no-op.

## Actionable failures

- Invalid `action` → list actions
- Invalid credentials on connect → "Check host/port/user"
- PgBouncer detected → exact message from ADR-009
- Export without write permission → "Create `workspace/data/knowledge-exports/` manually"
- Install-parser without disk → "No space. Marker needs ~500MB."
