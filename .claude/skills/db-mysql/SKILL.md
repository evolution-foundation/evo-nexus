---
name: db-mysql
description: "Query MySQL / MariaDB databases configured in .env (DB_MYSQL_N_*). Use when the user asks to query, explore, or audit data in a MySQL database. Picks connection by label (e.g. 'orders-dev', 'analytics-prod') or numeric index. Read-only by default — writes refused unless DB_MYSQL_N_ALLOW_WRITE=true on that block."
metadata:
  openclaw:
    requires:
      env:
        - DB_MYSQL_1_LABEL
      bins:
        - python3
    primaryEnv: DB_MYSQL_1_LABEL
    files:
      - "scripts/*"
      - "references/*"
---

# db-mysql

Query MySQL / MariaDB databases declared in `.env`. Connections follow the same numbered pattern as `SOCIAL_YOUTUBE_N_*` / `SOCIAL_INSTAGRAM_N_*` — one block per database, labelled for humans, picked by label or index at call time.

## Setup — one-time, per database

Add a block to `.env` (gitignored). Increment the index per connection:

```env
# ── MySQL: orders-dev ────────────────────────────────
DB_MYSQL_1_LABEL=orders-dev
DB_MYSQL_1_HOST=mysql.dev.internal
DB_MYSQL_1_PORT=3306
DB_MYSQL_1_DATABASE=orders
DB_MYSQL_1_USER=agent_readonly
DB_MYSQL_1_PASSWORD=...                # raw; .env is gitignored
# DB_MYSQL_1_SSL_CA_PATH=/path/ca.pem  # optional, enables TLS verification
# DB_MYSQL_1_ALLOW_WRITE=false         # default false
# DB_MYSQL_1_QUERY_TIMEOUT=30          # seconds, default 30
# DB_MYSQL_1_MAX_ROWS=1000             # default 1000
```

Alternative — full DSN instead of components (DSN wins when both are set):

```env
DB_MYSQL_2_LABEL=analytics-prod
DB_MYSQL_2_DSN=mysql://agent_ro:***@analytics.prod.internal:3306/analytics
```

`LABEL` is always required — it's how agents pick the connection.

## Usage

All commands output a single JSON line on stdout (safe to pipe). Errors go to stderr as JSON and exit non-zero.

### List configured connections
```bash
python3 .claude/skills/db-mysql/scripts/db_client.py accounts
```

### Health-check a connection
```bash
python3 .claude/skills/db-mysql/scripts/db_client.py test orders-dev
```

### Run a read-only query
```bash
python3 .claude/skills/db-mysql/scripts/db_client.py query orders-dev \
  "SELECT count(*) FROM customers WHERE created_at > now() - interval 7 day"
```

### Explore schema
```bash
# List all tables (excludes mysql/information_schema/performance_schema/sys)
python3 .claude/skills/db-mysql/scripts/db_client.py tables orders-dev

# Describe a table
python3 .claude/skills/db-mysql/scripts/db_client.py describe orders-dev customers
# Schema-qualified:
python3 .claude/skills/db-mysql/scripts/db_client.py describe orders-dev analytics.events
```

## Output shape

Successful query:
```json
{
  "ok": true,
  "query_id": "uuid-v4",
  "label": "orders-dev",
  "columns": ["id", "email", "created_at"],
  "rows": [[1, "a@b.com", "2026-04-22 12:00:00"]],
  "row_count": 1,
  "truncated": false,
  "full_result_path": null,
  "execution_time_ms": 12
}
```

When rows exceed `MAX_ROWS`, `truncated` is `true` and `full_result_path` points to a CSV in `ADWs/logs/db-queries/<query_id>.csv` — the agent can read that file directly instead of re-running.

Error:
```json
{"ok": false, "error_code": "write_blocked", "error": "Write query blocked — connection 'orders-dev' has ALLOW_WRITE=false. ...", "label": "orders-dev"}
```

Error codes:
- `no_connections` — no `DB_MYSQL_N_*` blocks in `.env`
- `not_found` — label/index doesn't match any block
- `ambiguous` — multiple blocks share the same label (use index instead)
- `config_error` — block present but required field missing (typically `LABEL`)
- `driver_missing` — `pymysql` not installed
- `connection_failed` — network, auth, TLS, or `MAX_EXECUTION_TIME` tripped
- `write_blocked` — write verb detected without `ALLOW_WRITE=true`
- `multi_statement` — more than one statement in a single call
- `usage` — wrong CLI args

## Guardrails

- Write verbs (`DELETE | UPDATE | INSERT | REPLACE | TRUNCATE | DROP | ALTER | CREATE | GRANT | REVOKE | RENAME | LOAD | CALL | HANDLER`) — refused unless `DB_MYSQL_N_ALLOW_WRITE=true`.
- Multi-statement queries — refused in v1. Supports `--`, `#`, and `/* */` comment styles.
- Query timeout — sets `SESSION MAX_EXECUTION_TIME = <QUERY_TIMEOUT>s` on the session (MySQL 5.7.4+; MariaDB honors `max_statement_time` but the SET statement is ignored gracefully on older versions).
- Result size — `fetchmany(MAX_ROWS + 1)` to detect truncation; full result streamed to CSV when truncated so the agent context never holds >1000 rows.
- `utf8mb4` charset enforced on the connection to avoid mojibake on modern collations.

## Workflow

1. If the user doesn't specify a label, run `accounts` to see what's configured and pick the one that matches their intent. If ambiguous, ask.
2. Write the smallest SQL that answers the question — prefer aggregates, `LIMIT`, and `EXPLAIN` before dumping rows.
3. Run via `query`. Inspect the result.
4. For performance-sensitive queries, load the deep-dive references below.

## Dependencies

- Python 3.10+
- `pymysql` — not pre-installed; `uv pip install pymysql` on first use. Pure-Python driver, no system libs required.

## Deep-dive references

These load the PlanetScale `database-skills` repo verbatim — same content the upstream authors ship for their own tooling.

- [EXPLAIN analysis](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/explain-analysis.md)
- [Composite indexes](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/composite-indexes.md)
- [Covering indexes](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/covering-indexes.md)
- [Fulltext indexes](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/fulltext-indexes.md)
- [Index maintenance](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/index-maintenance.md)
- [Primary keys](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/primary-keys.md)
- [Data types](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/data-types.md)
- [Character sets](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/character-sets.md)
- [Isolation levels](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/isolation-levels.md)
- [Deadlocks](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/deadlocks.md)
- [Row locking gotchas](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/row-locking-gotchas.md)
- [Online DDL](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/online-ddl.md)
- [Partitioning](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/partitioning.md)
- [Replication lag](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/replication-lag.md)
- [Connection management](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/connection-management.md)
- [N+1 queries](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/n-plus-one.md)
- [Query optimization pitfalls](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/query-optimization-pitfalls.md)
- [JSON column patterns](https://raw.githubusercontent.com/planetscale/database-skills/main/skills/mysql/references/json-column-patterns.md)

Upstream: [planetscale/database-skills](https://github.com/planetscale/database-skills) (MIT). Credit to PlanetScale for the reference content.
