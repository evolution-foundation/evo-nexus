#!/usr/bin/env python3
"""db-postgres — query PostgreSQL databases declared in .env.

Connections are numbered blocks in .env, same shape as SOCIAL_YOUTUBE_N_*:

    DB_POSTGRES_1_LABEL=msgops-dev
    DB_POSTGRES_1_HOST=db.dev.internal
    DB_POSTGRES_1_PORT=5432
    DB_POSTGRES_1_DATABASE=msgops
    DB_POSTGRES_1_USER=agent_readonly
    DB_POSTGRES_1_PASSWORD=...
    DB_POSTGRES_1_SSL_MODE=require           # optional (default: prefer)
    DB_POSTGRES_1_SSL_CA_PATH=/path/ca.pem   # optional (verify-* modes)
    DB_POSTGRES_1_ALLOW_WRITE=false          # default false
    DB_POSTGRES_1_QUERY_TIMEOUT=30           # seconds, default 30
    DB_POSTGRES_1_MAX_ROWS=1000              # default 1000

Or a full DSN alternative (DSN wins over components if both set):

    DB_POSTGRES_3_LABEL=evo-ai-dev
    DB_POSTGRES_3_DSN=postgresql://user:pw@host:5432/db?sslmode=require

Commands:
    db_client.py accounts
    db_client.py test <label>
    db_client.py query <label> <sql>
    db_client.py tables <label>
    db_client.py describe <label> <table>

Exit 0 on success. Exit non-zero with a single-line JSON error on failure.
Output on stdout is always JSON — safe to pipe.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

PREFIX = "POSTGRES"
ENV_PREFIX = f"DB_{PREFIX}_"

# ---------- .env loader (zero-dep) ----------

def _load_dotenv() -> None:
    """Load .env from workspace root into os.environ if not already set.

    Walks up from cwd until it finds .env or hits filesystem root. Existing
    env vars always win (never clobbered) — same contract as python-dotenv.
    """
    here = Path.cwd().resolve()
    for d in [here, *here.parents]:
        candidate = d / ".env"
        if candidate.is_file():
            try:
                for raw in candidate.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip()
                    if v and v[0] == v[-1] and v[0] in ('"', "'"):
                        v = v[1:-1]
                    os.environ.setdefault(k, v)
            except OSError:
                pass
            return


# ---------- Env parser (vendored) ----------

def _parse_bool(val: str, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _parse_int(val: str | None, default: int) -> int:
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def load_connections() -> list[dict]:
    """Parse DB_POSTGRES_N_* blocks out of os.environ.

    Returns a list sorted by N ascending. Gaps are OK (1, 3, 7 is fine).
    A block is considered present if any DB_POSTGRES_N_* key exists.
    LABEL is required — raises ValueError with a pointer to the offending N.
    """
    indices: set[int] = set()
    pattern = re.compile(rf"^{ENV_PREFIX}(\d+)_.+$")
    for k in os.environ:
        m = pattern.match(k)
        if m:
            indices.add(int(m.group(1)))

    out: list[dict] = []
    for n in sorted(indices):
        p = f"{ENV_PREFIX}{n}_"
        label = os.environ.get(f"{p}LABEL", "").strip()
        if not label:
            raise ValueError(
                f"{p}LABEL is required but missing. Add '{p}LABEL=<human-name>' to .env "
                f"or remove all other {p}* keys."
            )

        dsn = os.environ.get(f"{p}DSN", "").strip()
        conn = {
            "index": n,
            "label": label,
            "dsn": dsn or None,
            "host": os.environ.get(f"{p}HOST", "").strip() or None,
            "port": _parse_int(os.environ.get(f"{p}PORT"), 5432),
            "database": os.environ.get(f"{p}DATABASE", "").strip() or None,
            "user": os.environ.get(f"{p}USER", "").strip() or None,
            "password": os.environ.get(f"{p}PASSWORD", "") or None,
            "ssl_mode": os.environ.get(f"{p}SSL_MODE", "").strip() or None,
            "ssl_ca_path": os.environ.get(f"{p}SSL_CA_PATH", "").strip() or None,
            "allow_write": _parse_bool(os.environ.get(f"{p}ALLOW_WRITE"), False),
            "query_timeout": _parse_int(os.environ.get(f"{p}QUERY_TIMEOUT"), 30),
            "max_rows": _parse_int(os.environ.get(f"{p}MAX_ROWS"), 1000),
        }
        out.append(conn)
    return out


def find_connection(label_or_index: str) -> dict:
    """Resolve a connection by label (case-insensitive) or by numeric index."""
    conns = load_connections()
    if not conns:
        _die(
            "no_connections",
            f"No {ENV_PREFIX}N_* blocks found in .env. "
            f"See docs/integrations/databases.md for the template.",
        )

    # Numeric index?
    if label_or_index.isdigit():
        n = int(label_or_index)
        for c in conns:
            if c["index"] == n:
                return c
        _die("not_found", f"No {ENV_PREFIX}{n}_* block in .env.")

    # Label match (case-insensitive)
    target = label_or_index.strip().lower()
    matches = [c for c in conns if c["label"].lower() == target]
    if not matches:
        labels = ", ".join(c["label"] for c in conns)
        _die("not_found", f"No connection labelled '{label_or_index}'. Available: {labels}")
    if len(matches) > 1:
        _die(
            "ambiguous",
            f"Multiple connections labelled '{label_or_index}' "
            f"(indices {[m['index'] for m in matches]}). Use the index instead.",
        )
    return matches[0]


# ---------- Write-guard ----------

_WRITE_VERBS = re.compile(
    r"^\s*(DELETE|UPDATE|INSERT|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|COMMENT|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)
_MULTI_STATEMENT = re.compile(r";\s*\S")  # a ; followed by more content (outside strings — approx)


def _is_write(sql: str) -> bool:
    # Strip leading comments (-- ... \n or /* ... */).
    cleaned = sql
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned)
    return bool(_WRITE_VERBS.match(cleaned))


def _has_multi_statement(sql: str) -> bool:
    # Naive but cheap — detects ';' followed by non-whitespace before EOF.
    # Does NOT try to parse string literals out. For v1 we keep this strict:
    # one statement per call, trailing ';' allowed but nothing after it.
    stripped = sql.rstrip().rstrip(";").rstrip()
    return bool(_MULTI_STATEMENT.search(stripped + ";x"[:1]))  # trick: add sentinel


# The regex above is fragile — simpler and strict: count semicolons that
# aren't at the very end after stripping whitespace.
def _count_statements(sql: str) -> int:
    # Remove comments first
    cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned)
    # Strip trailing whitespace + one optional ';'
    cleaned = cleaned.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1]
    # Any remaining ';' suggests multi-statement. This is approximate (doesn't
    # account for ';' inside string literals) — acceptable for agent usage
    # where we want to err on the side of refusing.
    return cleaned.count(";") + 1


# ---------- Output helpers ----------

def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _die(code: str, message: str, **extra) -> None:
    payload = {"ok": False, "error_code": code, "error": message}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr)
    sys.exit(1)


# ---------- psycopg2 connection ----------

def _connect(conn: dict):
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor  # noqa: F401
    except ImportError:
        _die(
            "driver_missing",
            "psycopg2 is not installed. Run `uv pip install psycopg2-binary` "
            "or add it to your workspace's pyproject.toml.",
        )
        return  # unreachable; satisfies type checker

    if conn["dsn"]:
        return psycopg2.connect(conn["dsn"], connect_timeout=min(10, conn["query_timeout"]))

    kwargs = {
        "host": conn["host"],
        "port": conn["port"],
        "dbname": conn["database"],
        "user": conn["user"],
        "password": conn["password"],
        "connect_timeout": min(10, conn["query_timeout"]),
    }
    if conn["ssl_mode"]:
        kwargs["sslmode"] = conn["ssl_mode"]
    if conn["ssl_ca_path"]:
        kwargs["sslrootcert"] = conn["ssl_ca_path"]
    return psycopg2.connect(**kwargs)


# ---------- Commands ----------

def cmd_accounts() -> None:
    conns = load_connections()
    _emit(
        {
            "ok": True,
            "flavor": "postgres",
            "count": len(conns),
            "connections": [
                {
                    "index": c["index"],
                    "label": c["label"],
                    "host": c["host"] if not c["dsn"] else "<dsn>",
                    "port": c["port"] if not c["dsn"] else None,
                    "database": c["database"] if not c["dsn"] else None,
                    "ssl_mode": c["ssl_mode"],
                    "allow_write": c["allow_write"],
                    "query_timeout": c["query_timeout"],
                    "max_rows": c["max_rows"],
                }
                for c in conns
            ],
        }
    )


def cmd_test(label: str) -> None:
    conn_cfg = find_connection(label)
    started = time.monotonic()
    try:
        conn = _connect(conn_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            rtt_ms = int((time.monotonic() - started) * 1000)
            _emit(
                {
                    "ok": True,
                    "label": conn_cfg["label"],
                    "rtt_ms": rtt_ms,
                }
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — surface driver errors verbatim
        _die("connection_failed", str(exc), label=conn_cfg["label"])


def cmd_query(label: str, sql: str) -> None:
    conn_cfg = find_connection(label)

    if _count_statements(sql) > 1:
        _die(
            "multi_statement",
            "Multi-statement queries are not supported. Run one statement per call.",
            label=conn_cfg["label"],
        )

    if _is_write(sql) and not conn_cfg["allow_write"]:
        _die(
            "write_blocked",
            f"Write query blocked — connection '{conn_cfg['label']}' has ALLOW_WRITE=false. "
            f"Set DB_POSTGRES_{conn_cfg['index']}_ALLOW_WRITE=true to permit writes.",
            label=conn_cfg["label"],
        )

    query_id = str(uuid.uuid4())
    started = time.monotonic()
    conn = _connect(conn_cfg)
    try:
        # statement_timeout applies in ms
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {conn_cfg['query_timeout'] * 1000}")
            cur.execute(sql)
            if cur.description is None:
                # Non-SELECT (shouldn't happen if ALLOW_WRITE=false); treat as ok with 0 rows
                conn.commit()
                elapsed_ms = int((time.monotonic() - started) * 1000)
                _emit(
                    {
                        "ok": True,
                        "query_id": query_id,
                        "label": conn_cfg["label"],
                        "columns": [],
                        "rows": [],
                        "row_count": cur.rowcount,
                        "truncated": False,
                        "execution_time_ms": elapsed_ms,
                    }
                )
                return

            columns = [d[0] for d in cur.description]
            limit = conn_cfg["max_rows"]
            rows = cur.fetchmany(limit + 1)
            truncated = len(rows) > limit
            if truncated:
                rows = rows[:limit]

            elapsed_ms = int((time.monotonic() - started) * 1000)

            # Persist full result to ADWs/logs/db-queries/<uuid>.csv when truncated
            csv_path = None
            if truncated:
                try:
                    import csv

                    logs_dir = Path.cwd() / "ADWs" / "logs" / "db-queries"
                    logs_dir.mkdir(parents=True, exist_ok=True)
                    csv_path = logs_dir / f"{query_id}.csv"
                    # Re-execute to stream full result into CSV; cheaper than
                    # caching all rows in memory for large results
                    with csv_path.open("w", encoding="utf-8", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(columns)
                        # Write already-fetched truncated rows first
                        for r in rows:
                            writer.writerow(r)
                        # Drain cursor remainder
                        while True:
                            batch = cur.fetchmany(1000)
                            if not batch:
                                break
                            for r in batch:
                                writer.writerow(r)
                except Exception:  # noqa: BLE001
                    csv_path = None  # best-effort; don't fail the query over logs

            _emit(
                {
                    "ok": True,
                    "query_id": query_id,
                    "label": conn_cfg["label"],
                    "columns": columns,
                    "rows": [list(r) for r in rows],
                    "row_count": len(rows),
                    "truncated": truncated,
                    "full_result_path": str(csv_path) if csv_path else None,
                    "execution_time_ms": elapsed_ms,
                }
            )
    finally:
        conn.close()


def cmd_tables(label: str) -> None:
    cmd_query(
        label,
        "SELECT table_schema, table_name "
        "FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY table_schema, table_name",
    )


def cmd_describe(label: str, table: str) -> None:
    # Split optional schema.table
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = "public", table

    # Escape single quotes to avoid breaking the string
    schema_esc = schema.replace("'", "''")
    name_esc = name.replace("'", "''")

    cmd_query(
        label,
        f"SELECT column_name, data_type, is_nullable, column_default "
        f"FROM information_schema.columns "
        f"WHERE table_schema = '{schema_esc}' AND table_name = '{name_esc}' "
        f"ORDER BY ordinal_position",
    )


# ---------- Entrypoint ----------

def main(argv: list[str]) -> None:
    _load_dotenv()

    if len(argv) < 2:
        _die("usage", "Usage: db_client.py <accounts|test|query|tables|describe> [args]")

    cmd = argv[1]
    args = argv[2:]

    try:
        if cmd == "accounts":
            cmd_accounts()
        elif cmd == "test":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py test <label>")
            cmd_test(args[0])
        elif cmd == "query":
            if len(args) < 2:
                _die("usage", "Usage: db_client.py query <label> <sql>")
            cmd_query(args[0], args[1])
        elif cmd == "tables":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py tables <label>")
            cmd_tables(args[0])
        elif cmd == "describe":
            if len(args) < 2:
                _die("usage", "Usage: db_client.py describe <label> <table>")
            cmd_describe(args[0], args[1])
        else:
            _die("usage", f"Unknown command '{cmd}'. Try: accounts, test, query, tables, describe")
    except SystemExit:
        raise
    except ValueError as exc:
        _die("config_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        _die("unexpected", str(exc), type=type(exc).__name__)


if __name__ == "__main__":
    main(sys.argv)
