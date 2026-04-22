# Knowledge Base (pgvector)

The Knowledge Base page provides **multi-server hybrid search** (vector + BM25) over your documents with OCR, chunking, and async classification. It is powered by **pgvector** on Postgres — Bring Your Own Database (BYO).

> Different from [MemPalace](knowledge-base.md): MemPalace is offline/local for personal notes; Knowledge is multi-tenant, API-first, for teams and external integrations (Evo Academy, client knowledge bases).

## Architecture at a glance

```
EvoNexus (client-only)
  ├── SQLite local: knowledge_connections (credentials encrypted at rest)
  ├── Flask API: /api/knowledge/*  (internal) + /api/knowledge/v1/*  (external, API keys)
  ├── Worker subprocess: parse → chunk → embed → enqueue classify
  ├── Classify worker: async LLM classification (content_type, difficulty, topics)
  └── Skills: knowledge-{query,summarize,ingest,reindex,browse,organize,admin}

Your Postgres (not managed by EvoNexus)
  ├── pgvector extension
  ├── Tables: knowledge_{config,spaces,units,documents,chunks,api_keys,api_usage,classify_queue}
  └── Alembic-managed schema
```

**EvoNexus does NOT install or run Postgres.** You bring your own (Supabase, Neon, RDS, self-hosted). EvoNexus connects, auto-migrates the schema, and uses it.

## First-time setup

### 1. Encryption key (auto-generated)

Since v0.26.0, `KNOWLEDGE_MASTER_KEY` is generated automatically on first setup (`make setup` wizard) and on Docker first boot (`entrypoint.sh`). You do NOT need to run `make init-key` manually on fresh installs.

**Back up your `.env`** — losing `KNOWLEDGE_MASTER_KEY` loses access to all encrypted credentials.

> Legacy: if you need to regenerate (lost key, rotated secrets), the CLI is still available: `make init-key` / `evonexus init-key`. Idempotent — preserves an existing key.

### 2. Prepare your Postgres

You need:

- **Postgres ≥ 14**
- **pgvector ≥ 0.5** extension installed (`CREATE EXTENSION vector;`)
- A **database** already created
- A user with `CONNECT`, `CREATE`, and ideally `CREATE EXTENSION` permissions

EvoNexus does NOT create databases — you must create the DB first.

### 3. Connect via UI or skill

**UI:** navigate to `/knowledge` → Connections → **+ New Connection** → fill credentials → **Connect & Configure**.

**Skill:**

```
/knowledge-admin action=connect
```

The skill walks you through the wizard interactively.

### 4. Create a space

Spaces are logical groupings inside a connection (e.g., "academy", "support-kb", "sales-playbook"). Create via UI or:

```
/knowledge-organize action=create space_id=<space_id> slug=<slug> title="..."
```

## Concepts

| Concept | Description |
|---|---|
| **Connection** | One Postgres + pgvector server. You can have many. |
| **Space** | Logical grouping inside a connection (ex: "academy", "community"). |
| **Unit** | Learning unit / module grouping documents (ex: "Module 3 — WhatsApp"). |
| **Document** | Uploaded file (PDF, DOCX, etc.) — gets parsed, chunked, embedded. |
| **Chunk** | Content piece (~500 tokens) with embedding, used for search. |

## Parser

**Marker** (MIT, ~500MB model bundle) is the default parser. Supports PDF, DOCX, PPTX, XLSX, HTML, EPUB, images (with OCR).

First use: click **Install Parser Models** in UI or run:

```
/knowledge-admin action=install-parser
```

Download takes a few minutes. Idempotent — re-running is a no-op.

**LlamaParse** (optional, cloud API) available if you set `LLAMAPARSE_API_KEY`.

## Embedder

- **local** (default, 768 dim): `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`. Free, offline, pt-BR native.
- **openai** (opt-in, 1536 dim): `text-embedding-3-small`. Costs ~$0.02 / 1M tokens. Requires `OPENAI_API_KEY`.
- **gemini** (opt-in, 768 / 1536 / 3072 dim via MRL): `gemini-embedding-001` (stable, text-only, task-type aware) or `gemini-embedding-2-preview` (multimodal, 8192-token input). Requires `GEMINI_API_KEY` (free tier on [aistudio.google.com/apikey](https://aistudio.google.com/apikey)). Default dim is 768 to align storage cost with `local`.

**Embedder is GLOBAL per EvoNexus instance.** Changing it after you have data requires removing all connections and recreating them (reindex skill planned for v0.25.1).

## Search: hybrid + metadata boost

Query combines **3 signals** via Reciprocal Rank Fusion (RRF):

1. **Vector similarity** (HNSW cosine)
2. **BM25** (Postgres `tsvector` em português)
3. **Content-type boost** (configurable per space):

| content_type | default boost |
|---|---|
| faq | 1.20 |
| reference | 1.15 |
| lesson | 1.10 |
| tutorial | 1.05 |
| article | 1.00 |
| decision | 1.00 |
| note | 0.90 |
| transcript | 0.80 |

Override boosts per space via `content_type_boosts` JSONB column.

## Skills

| Skill | Group | Purpose |
|---|---|---|
| [knowledge-query](../../.claude/skills/knowledge-query/SKILL.md) | Consumo | Hybrid search + optional RAG answer |
| [knowledge-summarize](../../.claude/skills/knowledge-summarize/SKILL.md) | Consumo | TL;DR of document or unit |
| [knowledge-ingest](../../.claude/skills/knowledge-ingest/SKILL.md) | Ingestão | Upload file or URL |
| knowledge-reindex | Ingestão | Re-parse/re-embed (single or bulk) — deferred to v0.25.1 |
| [knowledge-browse](../../.claude/skills/knowledge-browse/SKILL.md) | Curadoria | List/filter docs + units |
| [knowledge-organize](../../.claude/skills/knowledge-organize/SKILL.md) | Curadoria | Units: create/move/reorder/link |
| [knowledge-admin](../../.claude/skills/knowledge-admin/SKILL.md) | Admin | Connections, health, stats, export, parser install |

All skills auto-use the first `ready` connection. Pass `connection="slug"` to target a specific one.

## Authentication

Two modes — clear separation:

- **`DASHBOARD_API_TOKEN`** (existing, in `.env`) → internal use by skills, heartbeats, routines. **Bypasses rate limit.**
- **`knowledge_api_keys`** → external use (Academy app, webhooks, third-party). Scoped by `connection_id` + `space_ids`. Rate-limited (fixed window).

Create API keys via UI (`/knowledge/api-keys`) or via the REST API.

### Example (external — Evo Academy calling Knowledge)

```bash
curl -H "Authorization: Bearer evo_k_abc.xyz..." \
     -H "Content-Type: application/json" \
     -H "X-Knowledge-Connection: academy" \
     -d '{"query": "how does onboarding work?", "space": "aulas-2026"}' \
     https://evonexus.local/api/knowledge/v1/search
```

## Permissions

| Permission | Who has it | What it allows |
|---|---|---|
| `knowledge:view` | admin, operator, viewer | View connections, spaces, search |
| `knowledge:manage` | admin | Create connections, upload, delete, generate API keys |

## API endpoints

### Internal (UI-only, session auth)

| Method | Endpoint | Description |
|---|---|---|
| GET/POST | `/api/knowledge/connections` | List/create connections |
| POST | `/api/knowledge/connections/:id/configure` | Run auto-migration |
| GET | `/api/knowledge/connections/:id/health` | Health check |
| CRUD | `/api/knowledge/api-keys` | Manage tokens |
| GET/PUT | `/api/knowledge/settings` | Embedder + parser globals |

### Public v1 (Bearer token)

| Method | Endpoint | Description |
|---|---|---|
| CRUD | `/api/knowledge/v1/spaces` | Spaces |
| CRUD | `/api/knowledge/v1/units` | Learning units |
| CRUD | `/api/knowledge/v1/documents` | Documents (upload multipart) |
| POST | `/api/knowledge/v1/search` | Hybrid search |
| GET | `/api/knowledge/v1/documents/:id/status` | Ingestion progress |

## Troubleshooting

**"PgBouncer detected"** — Supabase pooler (port 6543) is not supported. Use direct connection (port 5432). Same for AWS RDS Proxy.

**Connection status `needs_migration`** — EvoNexus was upgraded and remote schema is old. Click **Run migrations** in UI or `POST /connections/:id/migrate`.

**Marker timeout on large PDFs** — default 10min. Adjust `MARKER_TIMEOUT_SECONDS` in `.env`.

**Classify queue stuck** — check if `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` is set. Without either, classification is skipped (not fatal).

**Embedder provider locked** — you have data using current dim. To change the provider, remove all connections and recreate them. Reindex skill is planned for v0.25.1.
