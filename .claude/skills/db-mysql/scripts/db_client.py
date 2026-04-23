#!/usr/bin/env python3
"""db-mysql — query MySQL databases declared in .env.

Connections are numbered blocks in .env, same shape as SOCIAL_YOUTUBE_N_*:

    DB_MYSQL_1_LABEL=orders-dev
    DB_MYSQL_1_HOST=mysql.dev.internal
    DB_MYSQL_1_PORT=3306
    DB_MYSQL_1_DATABASE=orders
    DB_MYSQL_1_USER=agent_readonly
    DB_MYSQL_1_PASSWORD=...
    DB_MYSQL_1_SSL_CA_PATH=/path/ca.pem      # optional
    DB_MYSQL_1_ALLOW_WRITE=false             # default false
    DB_MYSQL_1_QUERY_TIMEOUT=30              # seconds, default 30
    DB_MYSQL_1_MAX_ROWS=1000                 # default 1000

Or a full DSN alternative (DSN wins over components if both set):

    DB_MYSQL_3_LABEL=analytics-prod
    DB_MYSQL_3_DSN=mysql://user:pw@host:3306/db

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
from urllib.parse import urlparse

PREFIX = "MYSQL"
ENV_PREFIX = f"DB_{PREFIX}_"


def _load_dotenv() -> None:
    here = Path.cwd().resolve()
    for d in [here, *here.parents]:
        candidate = d / ".env"
        if candidate.is_file():
            try:
                for raw in candidate.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
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
            "port": _parse_int(os.environ.get(f"{p}PORT"), 3306),
            "database": os.environ.get(f"{p}DATABASE", "").strip() or None,
            "user": os.environ.get(f"{p}USER", "").strip() or None,
            "password": os.environ.get(f"{p}PASSWORD", "") or None,
            "ssl_ca_path": os.environ.get(f"{p}SSL_CA_PATH", "").strip() or None,
            "allow_write": _parse_bool(os.environ.get(f"{p}ALLOW_WRITE"), False),
            "query_timeout": _parse_int(os.environ.get(f"{p}QUERY_TIMEOUT"), 30),
            "max_rows": _parse_int(os.environ.get(f"{p}MAX_ROWS"), 1000),
        }
        out.append(conn)
    return out


def find_connection(label_or_index: str) -> dict:
    conns = load_connections()
    if not conns:
        _die(
            "no_connections",
            f"No {ENV_PREFIX}N_* blocks found in .env. "
            f"See docs/integrations/databases.md for the template.",
        )

    if label_or_index.isdigit():
        n = int(label_or_index)
        for c in conns:
            if c["index"] == n:
                return c
        _die("not_found", f"No {ENV_PREFIX}{n}_* block in .env.")

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


_WRITE_VERBS = re.compile(
    r"^\s*(DELETE|UPDATE|INSERT|REPLACE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|RENAME|LOAD|CALL|HANDLER)\b",
    re.IGNORECASE,
)


def _is_write(sql: str) -> bool:
    cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned)
    cleaned = re.sub(r"#[^\n]*", " ", cleaned)  # MySQL also accepts # as a comment
    return bool(_WRITE_VERBS.match(cleaned))


def _count_statements(sql: str) -> int:
    cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned)
    cleaned = re.sub(r"#[^\n]*", " ", cleaned)
    cleaned = cleaned.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1]
    return cleaned.count(";") + 1


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _die(code: str, message: str, **extra) -> None:
    payload = {"ok": False, "error_code": code, "error": message}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr)
    sys.exit(1)


def _connect(conn: dict):
    try:
        import pymysql
    except ImportError:
        _die(
            "driver_missing",
            "pymysql is not installed. Run `uv pip install pymysql` "
            "or add it to your workspace's pyproject.toml.",
        )
        return

    connect_timeout = min(10, conn["query_timeout"])
    read_timeout = conn["query_timeout"]

    if conn["dsn"]:
        u = urlparse(conn["dsn"])
        kwargs = {
            "host": u.hostname or "localhost",
            "port": u.port or 3306,
            "user": u.username or "",
            "password": u.password or "",
            "database": (u.path or "/").lstrip("/") or None,
            "connect_timeout": connect_timeout,
            "read_timeout": read_timeout,
            "write_timeout": read_timeout,
            "charset": "utf8mb4",
        }
    else:
        kwargs = {
            "host": conn["host"],
            "port": conn["port"],
            "user": conn["user"],
            "password": conn["password"],
            "database": conn["database"],
            "connect_timeout": connect_timeout,
            "read_timeout": read_timeout,
            "write_timeout": read_timeout,
            "charset": "utf8mb4",
        }
        if conn["ssl_ca_path"]:
            kwargs["ssl"] = {"ca": conn["ssl_ca_path"]}

    return pymysql.connect(**kwargs)


def cmd_accounts() -> None:
    conns = load_connections()
    _emit(
        {
            "ok": True,
            "flavor": "mysql",
            "count": len(conns),
            "connections": [
                {
                    "index": c["index"],
                    "label": c["label"],
                    "host": c["host"] if not c["dsn"] else "<dsn>",
                    "port": c["port"] if not c["dsn"] else None,
                    "database": c["database"] if not c["dsn"] else None,
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
            _emit({"ok": True, "label": conn_cfg["label"], "rtt_ms": rtt_ms})
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
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
            f"Set DB_MYSQL_{conn_cfg['index']}_ALLOW_WRITE=true to permit writes.",
            label=conn_cfg["label"],
        )

    query_id = str(uuid.uuid4())
    started = time.monotonic()
    conn = _connect(conn_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {conn_cfg['query_timeout'] * 1000}")
            cur.execute(sql)
            if cur.description is None:
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

            csv_path = None
            if truncated:
                try:
                    import csv

                    logs_dir = Path.cwd() / "ADWs" / "logs" / "db-queries"
                    logs_dir.mkdir(parents=True, exist_ok=True)
                    csv_path = logs_dir / f"{query_id}.csv"
                    with csv_path.open("w", encoding="utf-8", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(columns)
                        for r in rows:
                            writer.writerow(r)
                        while True:
                            batch = cur.fetchmany(1000)
                            if not batch:
                                break
                            for r in batch:
                                writer.writerow(r)
                except Exception:  # noqa: BLE001
                    csv_path = None

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
        "WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys') "
        "ORDER BY table_schema, table_name",
    )


def cmd_describe(label: str, table: str) -> None:
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = None, table

    name_esc = name.replace("'", "''")
    if schema:
        schema_esc = schema.replace("'", "''")
        where = f"table_schema = '{schema_esc}' AND table_name = '{name_esc}'"
    else:
        where = f"table_schema = DATABASE() AND table_name = '{name_esc}'"

    cmd_query(
        label,
        f"SELECT column_name, column_type, is_nullable, column_default, column_key, extra "
        f"FROM information_schema.columns "
        f"WHERE {where} "
        f"ORDER BY ordinal_position",
    )


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
