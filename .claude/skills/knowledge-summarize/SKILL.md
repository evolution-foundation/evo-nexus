---
name: knowledge-summarize
description: "Generate a TL;DR summary of a specific document or learning unit in the Knowledge base. Pulls chunks from pgvector and synthesizes via Claude Haiku. Use when the user wants a quick overview ('summary of lesson 5', 'TL;DR of this PDF', 'explain document X in one paragraph')."
---

# knowledge-summarize

Group: **Consumption**. Generate TL;DR of a document or unit using indexed chunks.

## When to trigger

- "Summary of lesson 5"
- "TL;DR of this PDF"
- "Explain document X"
- "Summary of module Y"

## Arguments

| Name | Type | Required | Description |
|---|---|---|---|
| `document_id` | str | one of two | Document UUID |
| `unit_id` | str | one of two | Unit UUID (aggregates all docs) |
| `connection` | str | no | Defaults to first ready |
| `max_tokens` | int | no | Limit (default 500) |

## Workflow

### Step 1 — Fetch chunks

```python
from dashboard.backend.sdk_client import evo

if document_id:
    doc = evo.get(f"/api/knowledge/v1/documents/{document_id}",
                  headers={"X-Knowledge-Connection": connection})
    chunks = doc["chunks"]
    title = doc["title"]
elif unit_id:
    docs = evo.get(f"/api/knowledge/v1/documents?unit_id={unit_id}",
                   headers={"X-Knowledge-Connection": connection})
    chunks = []
    for d in docs:
        full = evo.get(f"/api/knowledge/v1/documents/{d['id']}",
                       headers={"X-Knowledge-Connection": connection})
        chunks.extend(full["chunks"])
    title = f"Unit {unit_id} ({len(docs)} documents)"
```

### Step 2 — Concatenate + truncate

Concatenate `chunk.content` separated by `\n\n`. If total > 40k chars: sample first/middle/last third.

### Step 3 — LLM call

Model: `claude-haiku-4-5-20251001`.

Prompt:

```
Summarize the document in structured markdown. Max {max_tokens} tokens.

## {title}

**TL;DR (1 paragraph):** ...

**Key points:**
- ...
- ...

**Target audience / when to use:** (optional)

### Document
{concatenated_chunks}
```

### Step 4 — Render

Return summary + footer `Based on {N} chunks from {M} documents`.

## Actionable failures

- Neither `document_id` nor `unit_id` passed → "Pass one of the two (mutually exclusive)."
- Not found → "Not found. Use `knowledge-browse` to list."
- `ANTHROPIC_API_KEY` missing → "Set `ANTHROPIC_API_KEY` in `.env`."
- Doc status != ready → "Not indexed (status={status}). Run `knowledge-reindex` or wait."
