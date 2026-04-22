---
name: knowledge-browse
description: "List and filter documents and units in the Knowledge base. Use when the user wants to see what is indexed ('what's in space X', 'list the last 10 indexed docs', 'which documents have error status', 'show Academy units')."
---

# knowledge-browse

Group: **Curation**. List/filter documents + units.

## When to trigger

- "What's in space X"
- "List the last 10 indexed docs"
- "Which documents have error status"
- "Show Academy units"

## Arguments

| Name | Type | Default | Description |
|---|---|---|---|
| `scope` | str | "documents" | "documents" OR "units" |
| `connection` | str | first ready | Target connection |
| `space` | str | null | Filter by space slug |
| `unit_id` | str | null | Filter by unit |
| `content_type` | str | null | lesson/tutorial/faq/reference/... |
| `difficulty` | str | null | beginner/intermediate/advanced |
| `topics` | list[str] | null | Filter by tags |
| `status` | str | null | pending/processing/ready/error |
| `q` | str | null | Text search on title |
| `limit` | int | 20 | How many to return |

## Workflow

### Step 1 — Call the right endpoint

```python
from dashboard.backend.sdk_client import evo

if scope == "documents":
    params = {k: v for k, v in {
        "space": space, "unit_id": unit_id, "content_type": content_type,
        "difficulty": difficulty, "topics": ",".join(topics or []),
        "status": status, "q": q, "limit": limit,
    }.items() if v is not None}
    items = evo.get("/api/knowledge/v1/documents", params=params,
                    headers={"X-Knowledge-Connection": connection})
elif scope == "units":
    params = {"space": space, "limit": limit}
    items = evo.get("/api/knowledge/v1/units", params=params,
                    headers={"X-Knowledge-Connection": connection})
```

### Step 2 — Render markdown table

Documents:

```
| Title | Type | Difficulty | Topics | Chunks | Status | Added |
|---|---|---|---|---|---|---|
| ... | lesson | beginner | [whatsapp, auth] | 14 | ready | 2026-04-20 |
```

Units:

```
| Title | Slug | Space | Seq | Docs | Created |
|---|---|---|---|---|---|
| ... | ... | academy | 1 | 5 | 2026-04-15 |
```

### Step 3 — Footer

```
Showing {len(items)} of {total} {scope} in {connection}{/space if filtered}.
Filters applied: {filters_summary}.
```

## Actionable failures

- 0 results → suggest relaxing filters
- Space not found → list available spaces
- Connection not found → "Run `knowledge-admin action=health`"
