# Knowledge — database setup

The Knowledge base stores chunked documents with vector embeddings in
PostgreSQL + pgvector. This page collects the gotchas you need to know
before pointing it at a managed database.

## Requirements

- **PostgreSQL 14+** with the `pgvector` extension available.
- **Session mode** connections. Transaction pooling (PgBouncer in
  transaction-pooling mode) is **not supported**, see below.
- Privileges sufficient to run `CREATE EXTENSION IF NOT EXISTS vector`,
  `CREATE INDEX ... USING hnsw`, and Alembic migrations on first connect.

## Provider cheat-sheet

### Supabase

Supabase exposes **two** Postgres endpoints per project:

| Endpoint                                         | Port | Mode              | Use here? |
|--------------------------------------------------|------|-------------------|-----------|
| Direct connection (`db.<ref>.supabase.co`)       | 5432 | Session           | **Yes**   |
| Pooler (`aws-*-*.pooler.supabase.com`)           | 6543 | Transaction pool  | **No**    |
| Session pooler (`aws-*-*.pooler.supabase.com`)   | 5432 | Session           | Yes       |

Use the direct connection string Supabase shows in
*Project Settings → Database → Connection string → URI*. It looks like:

```
postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
```

If you pasted the pooler URL (port `6543` or hostname containing `pooler`)
the wizard fails fast with a `Knowledge is not compatible with PgBouncer
in transaction pooling mode` error. That's by design — the Alembic
migrations and `CREATE INDEX ... USING hnsw` statements rely on prepared
statements and session-scoped state that PgBouncer's transaction pooling
silently drops.

If you genuinely need pooling (for example, your Supabase project is on
a plan with a connection limit), use the **session pooler** endpoint
Supabase exposes on port `5432`. It keeps prepared statements working
while still bounding the number of backend connections.

#### IPv6-only direct connection

New Supabase projects default to IPv6-only on the direct endpoint. If
your host does not have IPv6 connectivity the connection will fail with

```
connection to server at "db.<ref>.supabase.co" failed: Network is unreachable
```

Workarounds:

1. Enable the IPv4 add-on in Supabase (paid feature).
2. Use the session pooler URL on port `5432` — it is dual-stack.
3. Run Knowledge on a host with native IPv6 (most cloud providers do).

### Neon, Railway, Render, Fly, Heroku

Same rule: give Knowledge the **session-mode** connection string. Neon
and Railway both expose a pooler; pick the non-pooler URL from the
dashboard. The fast-fail check triggers on port `6543` and on hostnames
containing `pooler`.

### Self-hosted Postgres

Nothing special — just make sure `pgvector` is installed
(`CREATE EXTENSION vector` runs as superuser or owner of the target
database) and that the user in your connection string can create
extensions and indexes.

## Error reference

| Message snippet                                  | Cause                                                 | Fix |
|--------------------------------------------------|-------------------------------------------------------|-----|
| `hostname contains 'pooler'`                     | You pasted the Supabase/Neon transaction-pool URL     | Swap for the direct (`5432`) connection string |
| `port 6543 (Supabase transaction pooler)`        | Same as above, detected by port                        | Same |
| `Network is unreachable` + IPv6 address          | Host has no IPv6, direct endpoint is IPv6-only        | Session pooler URL or IPv4 add-on |
| `could not load extension "vector"`              | pgvector not installed on the cluster                  | `CREATE EXTENSION vector;` as a role with privileges |
| `permission denied to create extension "vector"` | Role lacks `CREATE` on the database                   | Grant it, or run the `CREATE EXTENSION` yourself |

## Related

- Wizard: **Knowledge → Connections → New** in the dashboard.
- Source for the validation: `dashboard/backend/knowledge/auto_migrator.py`.
