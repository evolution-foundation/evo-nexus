---
name: db-mongo
description: "Query MongoDB databases configured in .env (DB_MONGO_N_*). Use when the user asks to query, explore, or audit data in a MongoDB database. Picks connection by label (e.g. 'orders-dev', 'analytics-prod') or numeric index. Read-only by default — writes refused unless DB_MONGO_N_ALLOW_WRITE=true on that block."
metadata:
  openclaw:
    requires:
      env:
        - DB_MONGO_1_LABEL
      bins:
        - python3
    primaryEnv: DB_MONGO_1_LABEL
    files:
      - "scripts/*"
      - "references/*"
---

# db-mongo

Query MongoDB databases declared in `.env`. Same numbered block pattern as every other `db-*` and `SOCIAL_*_N_*` integration.

## Setup — one-time, per database

Add a block to `.env` (gitignored). Two ways to define a connection:

### Option A — URI (recommended for Atlas / managed)

```env
DB_MONGO_1_LABEL=orders-dev
DB_MONGO_1_URI=mongodb+srv://user:pw@cluster0.abc.mongodb.net/orders?retryWrites=true
# DB_MONGO_1_DATABASE=orders          # optional if in URI
# DB_MONGO_1_ALLOW_WRITE=false
# DB_MONGO_1_QUERY_TIMEOUT=30
# DB_MONGO_1_MAX_ROWS=1000
```

### Option B — components

```env
DB_MONGO_2_LABEL=analytics-prod
DB_MONGO_2_HOST=mongo.prod.internal
DB_MONGO_2_PORT=27017
DB_MONGO_2_DATABASE=analytics
DB_MONGO_2_USER=agent_readonly
DB_MONGO_2_PASSWORD=...
# DB_MONGO_2_AUTH_SOURCE=admin        # optional
# DB_MONGO_2_TLS=true                 # optional
```

URI wins when both are set. `LABEL` is always required.

## Usage

All commands output a single JSON line on stdout (safe to pipe).

### List configured connections
```bash
python3 .claude/skills/db-mongo/scripts/db_client.py accounts
```

### Health-check a connection (MongoDB ping)
```bash
python3 .claude/skills/db-mongo/scripts/db_client.py test orders-dev
```

### Find documents
```bash
# All (up to MAX_ROWS)
python3 .claude/skills/db-mongo/scripts/db_client.py find orders-dev customers

# With filter (JSON)
python3 .claude/skills/db-mongo/scripts/db_client.py find orders-dev customers \
  '{"status": "active"}' 20
```

### Aggregation pipeline
```bash
python3 .claude/skills/db-mongo/scripts/db_client.py aggregate orders-dev orders \
  '[{"$match": {"status": "paid"}}, {"$group": {"_id": "$product", "total": {"$sum": "$amount"}}}]'
```

### Explore database
```bash
# List collections
python3 .claude/skills/db-mongo/scripts/db_client.py collections orders-dev

# DB stats (size, object count, index size)
python3 .claude/skills/db-mongo/scripts/db_client.py stats orders-dev
```

## Output shape

Successful find/aggregate:
```json
{
  "ok": true,
  "query_id": "uuid-v4",
  "label": "orders-dev",
  "collection": "customers",
  "documents": [{"_id": "...", "email": "a@b.com"}],
  "row_count": 1,
  "truncated": false,
  "execution_time_ms": 12
}
```

## Guardrails

- **Read-only by default.** Aggregation pipelines containing `$out` or `$merge` (write stages) are refused unless `ALLOW_WRITE=true`. `find` is always safe; no separate write commands are exposed in v1.
- **Query timeout** applied at the server selection, socket, and connect levels.
- **Result size** capped at `MAX_ROWS` (default 1000). For aggregations we stop iterating the cursor at that point.
- **Credentials** never leave `.env`; `accounts` strips password before returning.

## Error codes

- `no_connections`, `not_found`, `ambiguous`, `config_error` — same as other `db-*` skills
- `driver_missing` — `pymongo` not installed
- `connection_failed` — network, auth, TLS, or timeout
- `invalid_filter` / `invalid_pipeline` — JSON parse failed
- `write_blocked` — aggregation with `$out`/`$merge` on `ALLOW_WRITE=false`
- `usage` — wrong CLI args

## Workflow

1. `accounts` to confirm the connection label.
2. `collections` to see what's in there.
3. `find` or `aggregate` with the smallest query that answers the question.
4. If a pipeline is expensive, ask the user whether to `$limit` earlier or add indexes before running.

## Dependencies

- Python 3.10+
- `pymongo` — not pre-installed; `uv pip install pymongo` on first use.

## Deep-dive references

Mongo isn't covered by PlanetScale's `database-skills` upstream. When you need authoritative reference, go to the Mongo docs directly:

- [MongoDB Query Operators](https://www.mongodb.com/docs/manual/reference/operator/query/)
- [Aggregation Pipeline Stages](https://www.mongodb.com/docs/manual/reference/operator/aggregation-pipeline/)
- [Indexing Strategies](https://www.mongodb.com/docs/manual/applications/indexes/)
- [Explain Plan](https://www.mongodb.com/docs/manual/reference/method/cursor.explain/)
