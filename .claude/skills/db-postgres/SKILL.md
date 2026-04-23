---
name: db-postgres
description: "Query PostgreSQL databases configured in .env (DB_POSTGRES_N_*). Use when the user asks to query, explore, or audit data in a Postgres database. Picks connection by label (e.g. 'msgops-dev', 'bms-prod') or numeric index. Read-only by default — writes refused unless DB_POSTGRES_N_ALLOW_WRITE=true on that block."
metadata:
  openclaw:
    requires:
      env:
        - DB_POSTGRES_1_LABEL
      bins:
        - python3
    primaryEnv: DB_POSTGRES_1_LABEL
    files:
      - "scripts/*"
      - "references/*"
---

# db-postgres

Query Postgres databases declared in `.env`. Connections follow the same numbered pattern as `SOCIAL_YOUTUBE_N_*` / `SOCIAL_INSTAGRAM_N_*` — one block per database, labelled for humans, picked by label or index at call time.

## Setup — one-time, per database

Add a block to `.env` (gitignored). Increment the index per connection:

```env
# ── Postgres: msgops-dev ─────────────────────────────
DB_POSTGRES_1_LABEL=msgops-dev
DB_POSTGRES_1_HOST=db.dev.internal
DB_POSTGRES_1_PORT=5432
DB_POSTGRES_1_DATABASE=msgops
DB_POSTGRES_1_USER=agent_readonly
DB_POSTGRES_1_PASSWORD=...                # raw; .env is gitignored
DB_POSTGRES_1_SSL_MODE=require            # disable | require | verify-ca | verify-full
# DB_POSTGRES_1_SSL_CA_PATH=/path/ca.pem  # optional, if verify-*
# DB_POSTGRES_1_ALLOW_WRITE=false         # default false
# DB_POSTGRES_1_QUERY_TIMEOUT=30          # seconds, default 30
# DB_POSTGRES_1_MAX_ROWS=1000             # default 1000

# ── Postgres: bms-prod (read-only replica) ───────────
DB_POSTGRES_2_LABEL=bms-prod
DB_POSTGRES_2_HOST=bms-ro.prod.internal
DB_POSTGRES_2_PORT=5432
DB_POSTGRES_2_DATABASE=bms
DB_POSTGRES_2_USER=agent_readonly
DB_POSTGRES_2_PASSWORD=...
DB_POSTGRES_2_SSL_MODE=require
```

Alternative — full DSN instead of components (DSN wins when both are set):

```env
DB_POSTGRES_3_LABEL=evo-ai-dev
DB_POSTGRES_3_DSN=postgresql://agent_ro:***@evoai.dev.internal:5432/evoai?sslmode=require
```

`LABEL` is always required — it's how agents pick the connection.

## Usage

All commands output a single JSON line on stdout (safe to pipe). Errors go to stderr as JSON and exit non-zero.

### List configured connections
```bash
python3 .claude/skills/db-postgres/scripts/db_client.py accounts
```

### Health-check a connection
```bash
python3 .claude/skills/db-postgres/scripts/db_client.py test msgops-dev
```

### Run a read-only query
```bash
python3 .claude/skills/db-postgres/scripts/db_client.py query msgops-dev \
  "SELECT count(*) FROM users WHERE created_at > now() - interval '7 days'"
```

### Explore schema
```bash
# List all tables (public + user schemas)
python3 .claude/skills/db-postgres/scripts/db_client.py tables msgops-dev

# Describe a table (columns, types, nullability, defaults)
python3 .claude/skills/db-postgres/scripts/db_client.py describe msgops-dev users
# Schema-qualified:
python3 .claude/skills/db-postgres/scripts/db_client.py describe msgops-dev analytics.events
```

## Output shape

Successful query:
```json
{
  "ok": true,
  "query_id": "uuid-v4",
  "label": "msgops-dev",
  "columns": ["id", "email", "created_at"],
  "rows": [[1, "a@b.com", "2026-04-22T12:00:00"]],
  "row_count": 1,
  "truncated": false,
  "full_result_path": null,
  "execution_time_ms": 12
}
```

When rows exceed `MAX_ROWS`, `truncated` is `true` and `full_result_path` points to a CSV in `ADWs/logs/db-queries/<query_id>.csv` — the agent can read that file directly instead of re-running the query.

Error:
```json
{"ok": false, "error_code": "write_blocked", "error": "Write query blocked — connection 'msgops-dev' has ALLOW_WRITE=false. ...", "label": "msgops-dev"}
```

Error codes:
- `no_connections` — no `DB_POSTGRES_N_*` blocks in `.env`
- `not_found` — label/index doesn't match any block
- `ambiguous` — multiple blocks share the same label (use index instead)
- `config_error` — block present but required field missing (typically `LABEL`)
- `driver_missing` — `psycopg2` not installed
- `connection_failed` — network, auth, TLS, or `statement_timeout` tripped
- `write_blocked` — write verb detected without `ALLOW_WRITE=true`
- `multi_statement` — more than one statement in a single call
- `usage` — wrong CLI args

## Guardrails

- Write verbs (`DELETE | UPDATE | INSERT | TRUNCATE | DROP | ALTER | CREATE | GRANT | REVOKE | COMMENT | VACUUM | REINDEX`) — refused unless `DB_POSTGRES_N_ALLOW_WRITE=true`.
- Multi-statement queries (anything with `;` that has content after it) — refused in v1.
- Query timeout — sets `statement_timeout = <QUERY_TIMEOUT>s` on the session before executing.
- Result size — `fetchmany(MAX_ROWS + 1)` to detect truncation; full result streamed to CSV when truncated so the agent context never holds >1000 rows.

## Workflow

1. If the user doesn't specify a label, run `accounts` to see what's configured and pick the one that matches their intent. If ambiguous, ask.
2. Write the smallest SQL that answers the question — prefer aggregates, `LIMIT`, and `EXPLAIN` before dumping rows.
3. Run via `query`. Inspect the result.
4. For performance-sensitive queries, load the deep-dive references below.

## Dependencies

- Python 3.10+
- `psycopg2-binary` (or `psycopg2`) — not pre-installed; `uv pip install psycopg2-binary` on first use.

## Deep-dive references

These load the PlanetScale `database-skills` repo verbatim — same content the upstream authors ship for their own tooling.

- [EXPLAIN analysis](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/explain-analysis.md)
- [Index optimization](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/index-optimization.md)
- [Indexing fundamentals](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/indexing.md)
- [MVCC + VACUUM](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/mvcc-vacuum.md)
- [MVCC transactions](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/mvcc-transactions.md)
- [Partitioning](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/partitioning.md)
- [PGBouncer configuration](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/pgbouncer-configuration.md)
- [Memory management / ops](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/memory-management-ops.md)
- [Monitoring](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/monitoring.md)
- [Backup & recovery](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/backup-recovery.md)
- [Optimization checklist](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/postgres/references/optimization-checklist.md)

Upstream: [planetscale/database-skills](https://github.com/planetscale/database-skills) (MIT). Credit to PlanetScale for the reference content.
