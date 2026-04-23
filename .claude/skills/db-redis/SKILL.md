---
name: db-redis
description: "Inspect Redis instances configured in .env (DB_REDIS_N_*). Use when the user asks to read keys, check cache state, list keys by pattern, or query info/dbsize on a Redis server. Picks connection by label (e.g. 'cache-dev', 'queue-prod') or numeric index. Read-only in v1 — no SET/DEL/FLUSH exposed."
metadata:
  openclaw:
    requires:
      env:
        - DB_REDIS_1_LABEL
      bins:
        - python3
    primaryEnv: DB_REDIS_1_LABEL
    files:
      - "scripts/*"
      - "references/*"
---

# db-redis

Inspect Redis instances declared in `.env`. Same numbered block pattern as every other `db-*` and `SOCIAL_*_N_*` integration.

## Setup — one-time, per instance

Add a block to `.env` (gitignored):

```env
DB_REDIS_1_LABEL=cache-dev
DB_REDIS_1_HOST=redis.dev.internal
DB_REDIS_1_PORT=6379
DB_REDIS_1_DB=0                        # numeric DB index (default 0)
# DB_REDIS_1_USERNAME=                 # optional (Redis 6+ ACL)
DB_REDIS_1_PASSWORD=...
# DB_REDIS_1_TLS=false                 # default false
# DB_REDIS_1_ALLOW_WRITE=false         # default false (no write verbs in v1 anyway)
# DB_REDIS_1_QUERY_TIMEOUT=30
# DB_REDIS_1_MAX_ROWS=1000
```

Full URL alternative (wins when both are set):

```env
DB_REDIS_2_LABEL=queue-prod
DB_REDIS_2_URL=rediss://user:pw@host:6380/0
```

`LABEL` is always required.

## Usage

All commands output JSON on stdout (safe to pipe). Errors go to stderr.

### List configured connections
```bash
python3 .claude/skills/db-redis/scripts/db_client.py accounts
```

### Health-check (PING, reports RTT)
```bash
python3 .claude/skills/db-redis/scripts/db_client.py test cache-dev
```

### List keys (production-safe — uses SCAN, never KEYS *)
```bash
# All keys (up to MAX_ROWS)
python3 .claude/skills/db-redis/scripts/db_client.py keys cache-dev

# Pattern + limit
python3 .claude/skills/db-redis/scripts/db_client.py keys cache-dev 'user:*' 50
```

### Read a single key (auto-detects type)
```bash
python3 .claude/skills/db-redis/scripts/db_client.py get cache-dev user:123
```

Handles `string`, `list`, `set`, `hash`, `zset`, `stream` automatically. Includes TTL in seconds (null if persistent).

### Server info
```bash
# All sections
python3 .claude/skills/db-redis/scripts/db_client.py info cache-dev

# Specific section (memory, clients, replication, stats, persistence, ...)
python3 .claude/skills/db-redis/scripts/db_client.py info cache-dev memory
```

### Database size (number of keys)
```bash
python3 .claude/skills/db-redis/scripts/db_client.py dbsize cache-dev
```

## Output shape

`get` on a string key:
```json
{
  "ok": true,
  "label": "cache-dev",
  "key": "user:123",
  "type": "string",
  "value": "alice",
  "ttl_seconds": 3600
}
```

`keys`:
```json
{
  "ok": true,
  "query_id": "uuid-v4",
  "label": "cache-dev",
  "pattern": "user:*",
  "keys": ["user:1", "user:2", "user:3"],
  "row_count": 3,
  "truncated": false,
  "execution_time_ms": 8
}
```

## Guardrails

- **Read-only verbs only in v1** — GET, SCAN (via `keys`), TYPE, TTL, LRANGE/SMEMBERS/HGETALL/ZRANGE/XRANGE (via `get`), INFO, DBSIZE, PING. No SET/DEL/FLUSHDB/FLUSHALL exposed. `ALLOW_WRITE` reserved for future write commands.
- **SCAN instead of KEYS** — `keys` uses `scan_iter` with `count=200` to avoid blocking the server on production instances.
- **Result cap** — `keys` stops at `MAX_ROWS`. Collection readers (LRANGE/SMEMBERS/ZRANGE/XRANGE inside `get`) also cap at `MAX_ROWS`.
- **Decoding** — responses decoded as UTF-8 (`decode_responses=True`). Raw bytes not supported in v1; if you need binary values, store them base64-encoded or ask for a follow-up.

## Error codes

- `no_connections`, `not_found`, `ambiguous`, `config_error`, `usage` — same as other `db-*` skills
- `driver_missing` — `redis` (python) not installed
- `connection_failed` — network, auth, TLS, or timeout
- `invalid_args` — bad limit argument

## Workflow

1. `accounts` to confirm the label.
2. `dbsize` or `info memory` for a quick overview before listing.
3. `keys <label> '<pattern>'` to find what you're looking for — always use a narrow pattern on prod instances.
4. `get <label> <key>` to read individual values. TTL is included so you know if the value is ephemeral.

## Dependencies

- Python 3.10+
- `redis` (python client) — not pre-installed; `uv pip install redis` on first use. Works against Redis 5+, Redis Stack, Valkey, and managed services (Upstash, Redis Cloud, AWS ElastiCache, Google Memorystore).

## Deep-dive references

Redis isn't covered by PlanetScale's `database-skills`. Authoritative sources:

- [Redis Commands](https://redis.io/commands/)
- [SCAN vs KEYS](https://redis.io/docs/latest/commands/scan/) — why `keys` uses SCAN
- [ACL (Access Control List)](https://redis.io/docs/latest/operate/oss_and_stack/management/security/acl/) — Redis 6+ username/password
- [Memory Optimization](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/memory-optimization/)
- [Persistence (RDB vs AOF)](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
