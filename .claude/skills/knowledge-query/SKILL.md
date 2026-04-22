---
name: knowledge-query
description: "Hybrid search (vector + BM25 via RRF + metadata boost) against the pgvector Knowledge base, with optional RAG synthesis. Use when the user asks factual questions that should be grounded in indexed documents (e.g., 'what do we know about X', 'search the knowledge base for Y', '@knowledge <query>'). Pass answer=true to synthesize a narrative response with citations instead of raw snippets."
---

# knowledge-query

Group: **Consumption**. Hybrid search on pgvector + optional RAG (LLM synthesis with citations).

## When to trigger

- "What do we know about X?"
- "Search the knowledge base for Y"
- "@knowledge <query>"
- Any factual question that should be grounded in indexed documents

## Arguments

| Name | Type | Default | Description |
|---|---|---|---|
| `query` | str | required | Natural language question |
| `connection` | str | first `ready` | Connection slug (e.g., "academy", "acme") |
| `space` | str | null = all | Space slug within the connection |
| `top_k` | int | 5 | How many snippets to return |
| `filters` | dict | {} | `{unit_id, content_type, topics, date_range}` |
| `answer` | bool | false | If true, synthesize narrative answer with citations |

## Workflow

### Step 1 — Identify active connection

If `connection` is not provided, call `GET /api/knowledge/connections?status=ready` and use the first one. If none ready: return actionable error: "No Knowledge connection configured. Run `knowledge-admin action=connect` first."

### Step 2 — Hybrid search

```python
from dashboard.backend.sdk_client import evo

hits = evo.post(
    "/api/knowledge/v1/search",
    {"query": query, "space": space, "top_k": top_k, "filters": filters},
    headers={"X-Knowledge-Connection": connection},
)
```

Response: list of `{chunk_id, content, document_id, title, content_type, similarity_score, metadata: {page, section, heading_path}}`.

### Step 3a — Format snippets (if `answer=false`)

For each hit:

```
**[{content_type}]** {title} — p.{metadata.page or "?"}
> {content[:300]}...
Score: {similarity_score:.3f}
```

Separate with `---`.

### Step 3b — RAG synthesis (if `answer=true`)

1. Take top-5 snippets
2. Build prompt:

```
You are a factual assistant. Answer ONLY using the sources below.
Cite each fact with [source:page] right after the claim.
If sources don't cover the question: "The knowledge base contains no information on this."

### Question
{query}

### Sources
[1] {title_1} (p.{page_1}): {content_1}
[2] {title_2} (p.{page_2}): {content_2}
...

### Answer
```

3. Call Claude Haiku 4.5 via `anthropic` SDK (`ANTHROPIC_API_KEY` from `.env`). Model: `claude-haiku-4-5-20251001`. Max tokens: 800.
4. Render response + sources block at the end.

## Output

- `answer=false`: markdown list of snippets with scores
- `answer=true`: narrative answer + sources
- Always: footer `Searched {N} chunks in {connection}/{space or "all"} in {elapsed_ms}ms`

## Actionable failures

- Connection not found → "Connection `X` does not exist. Run `knowledge-admin action=health`."
- Space not found → list available spaces
- 0 hits → suggest relaxing filters
- `ANTHROPIC_API_KEY` missing with `answer=true` → fallback to raw snippets + warning
