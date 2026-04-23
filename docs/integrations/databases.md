# Databases — Postgres & MySQL (v0.28+)

Configure one or more databases via `.env` and query them from any agent via the `db-postgres` / `db-mysql` skills. Same pattern as the social integrations (`SOCIAL_YOUTUBE_N_*`, etc.) — numbered blocks, `.env` is gitignored, no extra infra.

## TL;DR

```env
DB_POSTGRES_1_LABEL=msgops-dev
DB_POSTGRES_1_HOST=db.dev.internal
DB_POSTGRES_1_PORT=5432
DB_POSTGRES_1_DATABASE=msgops
DB_POSTGRES_1_USER=agent_readonly
DB_POSTGRES_1_PASSWORD=***
DB_POSTGRES_1_SSL_MODE=require
```

```bash
# one-time
uv pip install psycopg2-binary

# query
python3 .claude/skills/db-postgres/scripts/db_client.py \
  query msgops-dev "SELECT count(*) FROM users"
```

Writes are refused unless the connection opts in:

```env
DB_POSTGRES_1_ALLOW_WRITE=true
```

## Supported flavors (v1)

| Flavor | Skill | Driver | Default port |
|---|---|---|---|
| PostgreSQL | `db-postgres` | `psycopg2-binary` | 5432 |
| MySQL / MariaDB | `db-mysql` | `pymysql` | 3306 |

Mongo and Redis are planned for v2 — same env-block pattern, different verbs.

## Env block reference

### Postgres

| Key | Required | Default | Notes |
|---|---|---|---|
| `DB_POSTGRES_<N>_LABEL` | yes | — | Human name, how agents pick the connection. Case-insensitive match. |
| `DB_POSTGRES_<N>_HOST` | yes* | — | *unless `DSN` is set |
| `DB_POSTGRES_<N>_PORT` | no | 5432 | |
| `DB_POSTGRES_<N>_DATABASE` | yes* | — | *unless `DSN` is set |
| `DB_POSTGRES_<N>_USER` | yes* | — | *unless `DSN` is set |
| `DB_POSTGRES_<N>_PASSWORD` | no | — | Omit for password-less local dev |
| `DB_POSTGRES_<N>_SSL_MODE` | no | — | `disable`, `require`, `verify-ca`, `verify-full` |
| `DB_POSTGRES_<N>_SSL_CA_PATH` | no | — | Path to CA cert (for `verify-*` modes) |
| `DB_POSTGRES_<N>_DSN` | no | — | Full `postgresql://...` URI. Wins over components. |
| `DB_POSTGRES_<N>_ALLOW_WRITE` | no | `false` | Set `true` to permit write verbs. |
| `DB_POSTGRES_<N>_QUERY_TIMEOUT` | no | `30` | Seconds. Applied via `SET statement_timeout`. |
| `DB_POSTGRES_<N>_MAX_ROWS` | no | `1000` | Rows returned inline; full result spills to CSV when truncated. |

### MySQL / MariaDB

| Key | Required | Default | Notes |
|---|---|---|---|
| `DB_MYSQL_<N>_LABEL` | yes | — | Human name, how agents pick the connection. |
| `DB_MYSQL_<N>_HOST` | yes* | — | *unless `DSN` is set |
| `DB_MYSQL_<N>_PORT` | no | 3306 | |
| `DB_MYSQL_<N>_DATABASE` | yes* | — | *unless `DSN` is set |
| `DB_MYSQL_<N>_USER` | yes* | — | *unless `DSN` is set |
| `DB_MYSQL_<N>_PASSWORD` | no | — | |
| `DB_MYSQL_<N>_SSL_CA_PATH` | no | — | Enables TLS verification when set |
| `DB_MYSQL_<N>_DSN` | no | — | Full `mysql://...` URI. Wins over components. |
| `DB_MYSQL_<N>_ALLOW_WRITE` | no | `false` | Set `true` to permit write verbs. |
| `DB_MYSQL_<N>_QUERY_TIMEOUT` | no | `30` | Seconds. Applied via `SET SESSION MAX_EXECUTION_TIME`. |
| `DB_MYSQL_<N>_MAX_ROWS` | no | `1000` | Rows returned inline; full result spills to CSV when truncated. |

## How agents pick a connection

- **By label** (case-insensitive exact match): `query msgops-dev "SELECT ..."`.
- **By index** (the `N` in the env key): `query 1 "SELECT ..."`.
- If the label matches two blocks (user error — labels should be unique), the agent gets an `ambiguous` error and has to use the index.

## Commands (same surface across flavors)

```bash
# List configured connections — returns label, host, port, database, guardrails
db_client.py accounts

# RTT + 'SELECT 1' roundtrip, ~10s timeout
db_client.py test <label|index>

# Run a query, streamed result (max MAX_ROWS inline, rest spilled to CSV)
db_client.py query <label|index> "<sql>"

# List tables (excluding system schemas)
db_client.py tables <label|index>

# Describe a single table (columns, types, nullability, defaults)
db_client.py describe <label|index> <table>
db_client.py describe <label|index> <schema>.<table>
```

All commands output one JSON line on stdout, safe to pipe. Errors go to stderr as JSON and exit non-zero.

## Guardrails

- **Read-only by default.** Write verbs — `DELETE | UPDATE | INSERT | REPLACE | TRUNCATE | DROP | ALTER | CREATE | GRANT | REVOKE | ...` — are refused unless `ALLOW_WRITE=true` on that specific connection.
- **One statement per call.** Multi-statement SQL (extra `;` with content after) is refused in v1.
- **Timeouts are enforced server-side** (`statement_timeout` for Postgres, `MAX_EXECUTION_TIME` for MySQL 5.7.4+) — a runaway query hits the DB's own cancellation, not just a client-side timer.
- **Row cap** — `MAX_ROWS` is a soft cap on what's returned inline. When exceeded, the script persists the full result to `ADWs/logs/db-queries/<query_id>.csv` and signals `truncated: true` + `full_result_path` so the agent can read the file directly instead of re-running with a larger limit.
- **Credentials stay in `.env`.** They never transit through the dashboard's process memory (unless you use the `/integrations/databases` Test button, which only reads them transiently for the test RTT), never get logged, and never enter the agent's context beyond the subprocess that executes the query with those env vars set.

## Error codes

| Code | Meaning |
|---|---|
| `no_connections` | No `DB_<FLAVOR>_N_*` blocks in `.env` |
| `not_found` | Label/index doesn't match any block |
| `ambiguous` | Multiple blocks share the same label — use the index |
| `config_error` | A block exists but `LABEL` is missing |
| `driver_missing` | `psycopg2`/`pymysql` not installed |
| `connection_failed` | Network, auth, TLS, or timeout tripped |
| `write_blocked` | Write verb detected on a `ALLOW_WRITE=false` connection |
| `multi_statement` | More than one statement in a single call |
| `usage` | Wrong CLI args |

## Rotating a password

1. Edit `.env` — replace the old value in the relevant block.
2. Restart the dashboard (or just source `.env` again in the shell you run agents from).
3. The next `db_client.py` call reads the new value — no restart of individual agents needed.

## FAQ

**Why not a SQLite registry + encrypted credentials?**
We considered it, chose `.env` for consistency with every other integration (Stripe, Omie, Social) and because it already solves the problem: `.env` is gitignored, editable in plain text, and there's an in-product env editor at `/integrations/env-editor` for dashboard-only flows. Zero new infra, no new master key, no new table.

**What about DBs behind Auth0 / OIDC proxies?**
v1 assumes a static password or DSN is enough. If a DB requires token-exchange, the current workaround is to set a service token as `PASSWORD` and let the proxy handle exchange. Native OIDC support is deferred.

**Can I use this for the workspace's own SQLite DB (`dashboard.db`)?**
Not in v1 — the `db-*` skills target network-attached SQL services. For `dashboard.db` introspection, keep using `data-analyze` / `data-write-query` which already know that shape.

**Is the `/integrations/databases` page the source of truth?**
No. `.env` is. The page is a read-only convenience view — it lists what's parsed from `.env`, offers a Test button, and can copy a block template to the clipboard. It never persists passwords.

## See also

- [`db-postgres` SKILL.md](../../.claude/skills/db-postgres/SKILL.md) — full command reference + deep-dive references
- [`db-mysql` SKILL.md](../../.claude/skills/db-mysql/SKILL.md) — full command reference + deep-dive references
- [`data-write-query`](../skills/data-write-query.md) — SQL authoring skill (can compose with `db-*` via `--connection <label>` flag — coming in PR 2)
- Upstream reference content: [planetscale/database-skills](https://github.com/planetscale/database-skills) (MIT)
