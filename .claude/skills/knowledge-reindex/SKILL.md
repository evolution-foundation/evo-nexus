---
name: knowledge-reindex
description: "Re-run parsing, chunking, and embedding for one document or all documents in a space. Use after changing embedder provider (e.g., local 768 → OpenAI 1536), re-classifying content, or recovering from a parser bug. Shows cost/time preview before bulk operations."
---

# knowledge-reindex

Group: **Ingestion**. Re-process existing documents (re-parse + re-embed + re-classify).

## When to trigger

- "Re-embed the Academy docs"
- "Refresh the auto tags"
- "Reindex document X"
- After switching embedder or fixing a parser bug

## Arguments

| Name | Type | Required | Description |
|---|---|---|---|
| `document_id` | str | one of two | Single doc |
| `space_id` | str | one of two | Bulk — all docs in space |
| `connection` | str | no | Defaults to first ready |
| `confirm` | bool | bulk only | Without `confirm=true`, shows preview only |

## Workflow

### Single mode

```python
from dashboard.backend.sdk_client import evo

evo.post(
    f"/api/knowledge/v1/documents/{document_id}/reindex",
    {},
    headers={"X-Knowledge-Connection": connection},
)
```

Follow status poll (same pattern as `knowledge-ingest` step 4).

### Bulk mode — 2 steps

**Step 1 (preview, always):**

```python
docs = evo.get(f"/api/knowledge/v1/documents?space_id={space_id}",
               headers={"X-Knowledge-Connection": connection})

chunks_total = sum(d.get("chunk_count", 0) for d in docs)
embedder = evo.get("/api/knowledge/settings")["embedder_provider"]

if embedder == "openai":
    # text-embedding-3-small: $0.02 / 1M tokens, avg ~300 tokens/chunk
    tokens_est = chunks_total * 300
    cost_est = tokens_est * 0.02 / 1_000_000
else:
    cost_est = 0.0

rate = 50 if embedder == "local" else 200  # chunks/s
time_est_min = chunks_total / rate / 60
```

Preview:

```
Reindex preview:
  Space: {slug}
  Documents: {N}
  Chunks: {chunks_total}
  Embedder: {embedder}
  Estimated cost: ${cost_est:.2f}
  Estimated time: ~{time_est_min:.0f} min

Run with `confirm=true` to execute.
```

**Step 2 (execute if `confirm=true`):**

```python
for doc in docs:
    evo.post(f"/api/knowledge/v1/documents/{doc['id']}/reindex", {},
             headers={"X-Knowledge-Connection": connection})
```

Progress: `Enqueued 27/120 docs...`

Closing: "All {N} reindex jobs enqueued. Monitor with `knowledge-browse` or `knowledge-admin action=stats`."

## Output

- **Preview:** table + ask for confirmation
- **Execute:** progress + monitoring instructions

## Actionable failures

- Neither `document_id` nor `space_id` → "Pass one of the two"
- Space with 0 docs → "Empty space"
- Doc in `processing` → "Already processing. Wait or kill via UI."
