#!/usr/bin/env python3
"""db-mongo — query MongoDB databases declared in .env.

Connections are numbered blocks in .env, same shape as SOCIAL_YOUTUBE_N_*:

    DB_MONGO_1_LABEL=orders-dev
    DB_MONGO_1_URI=mongodb://user:pw@host:27017/mydb?authSource=admin
    DB_MONGO_1_DATABASE=mydb           # optional if included in URI
    DB_MONGO_1_ALLOW_WRITE=false       # default false
    DB_MONGO_1_QUERY_TIMEOUT=30        # seconds, default 30
    DB_MONGO_1_MAX_ROWS=1000           # default 1000

Or components instead of URI (URI wins when both are set):

    DB_MONGO_2_LABEL=analytics-prod
    DB_MONGO_2_HOST=mongo.prod.internal
    DB_MONGO_2_PORT=27017
    DB_MONGO_2_DATABASE=analytics
    DB_MONGO_2_USER=agent_readonly
    DB_MONGO_2_PASSWORD=...
    DB_MONGO_2_AUTH_SOURCE=admin       # optional
    DB_MONGO_2_TLS=true                # optional (for Atlas / managed)

Commands:
    db_client.py accounts
    db_client.py test <label>
    db_client.py find <label> <collection> [filter_json] [limit]
    db_client.py aggregate <label> <collection> <pipeline_json>
    db_client.py collections <label>
    db_client.py stats <label>

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
from urllib.parse import quote_plus

PREFIX = "MONGO"
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

        uri = os.environ.get(f"{p}URI", "").strip()
        conn = {
            "index": n,
            "label": label,
            "uri": uri or None,
            "host": os.environ.get(f"{p}HOST", "").strip() or None,
            "port": _parse_int(os.environ.get(f"{p}PORT"), 27017),
            "database": os.environ.get(f"{p}DATABASE", "").strip() or None,
            "user": os.environ.get(f"{p}USER", "").strip() or None,
            "password": os.environ.get(f"{p}PASSWORD", "") or None,
            "auth_source": os.environ.get(f"{p}AUTH_SOURCE", "").strip() or None,
            "tls": _parse_bool(os.environ.get(f"{p}TLS"), False),
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


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _die(code: str, message: str, **extra) -> None:
    payload = {"ok": False, "error_code": code, "error": message}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr)
    sys.exit(1)


def _build_uri(conn: dict) -> str:
    if conn["uri"]:
        return conn["uri"]
    user = quote_plus(conn["user"]) if conn["user"] else ""
    pwd = quote_plus(conn["password"]) if conn["password"] else ""
    creds = f"{user}:{pwd}@" if user else ""
    host = conn["host"] or "localhost"
    port = conn["port"] or 27017
    db = conn["database"] or ""
    params = []
    if conn["auth_source"]:
        params.append(f"authSource={conn['auth_source']}")
    if conn["tls"]:
        params.append("tls=true")
    qs = "?" + "&".join(params) if params else ""
    return f"mongodb://{creds}{host}:{port}/{db}{qs}"


def _connect(conn: dict):
    try:
        import pymongo
        from pymongo import MongoClient  # noqa: F401
    except ImportError:
        _die(
            "driver_missing",
            "pymongo is not installed. Run `uv pip install pymongo` "
            "or add it to your workspace's pyproject.toml.",
        )
        return

    uri = _build_uri(conn)
    timeout_ms = conn["query_timeout"] * 1000
    return pymongo.MongoClient(
        uri,
        serverSelectionTimeoutMS=min(10_000, timeout_ms),
        socketTimeoutMS=timeout_ms,
        connectTimeoutMS=min(10_000, timeout_ms),
    )


def _get_db(client, conn: dict):
    # Prefer explicit DATABASE; fall back to default from URI
    if conn["database"]:
        return client[conn["database"]]
    default = client.get_default_database()
    if default is None:
        _die(
            "config_error",
            "No database specified. Set DB_MONGO_<N>_DATABASE or include it in the URI.",
            label=conn["label"],
        )
    return default


_WRITE_COMMANDS = {
    "insert",
    "insert_one",
    "insert_many",
    "update",
    "update_one",
    "update_many",
    "replace_one",
    "delete",
    "delete_one",
    "delete_many",
    "drop",
    "rename",
    "create",
    "createCollection",
    "bulkWrite",
    "findOneAndUpdate",
    "findOneAndReplace",
    "findOneAndDelete",
    "$out",
    "$merge",
}


def _pipeline_is_write(pipeline: list) -> bool:
    """Check if an aggregation pipeline contains write stages ($out, $merge)."""
    for stage in pipeline or []:
        if not isinstance(stage, dict):
            continue
        for key in stage:
            if key in {"$out", "$merge"}:
                return True
    return False


def cmd_accounts() -> None:
    conns = load_connections()
    _emit(
        {
            "ok": True,
            "flavor": "mongo",
            "count": len(conns),
            "connections": [
                {
                    "index": c["index"],
                    "label": c["label"],
                    "host": c["host"] if not c["uri"] else "<uri>",
                    "port": c["port"] if not c["uri"] else None,
                    "database": c["database"],
                    "tls": c["tls"],
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
        client = _connect(conn_cfg)
        try:
            client.admin.command("ping")
            rtt_ms = int((time.monotonic() - started) * 1000)
            _emit({"ok": True, "label": conn_cfg["label"], "rtt_ms": rtt_ms})
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        _die("connection_failed", str(exc), label=conn_cfg["label"])


def cmd_find(label: str, collection: str, filter_json: str | None, limit_str: str | None) -> None:
    conn_cfg = find_connection(label)
    try:
        filt = json.loads(filter_json) if filter_json else {}
    except json.JSONDecodeError as exc:
        _die("invalid_filter", f"Filter must be valid JSON: {exc}", label=conn_cfg["label"])

    try:
        user_limit = int(limit_str) if limit_str else conn_cfg["max_rows"]
    except ValueError:
        _die("invalid_args", f"Limit must be an integer, got: {limit_str}", label=conn_cfg["label"])

    limit = min(user_limit, conn_cfg["max_rows"])
    query_id = str(uuid.uuid4())
    started = time.monotonic()

    client = _connect(conn_cfg)
    try:
        db = _get_db(client, conn_cfg)
        cur = db[collection].find(filt).limit(limit + 1)
        docs = list(cur)
        truncated = len(docs) > limit
        if truncated:
            docs = docs[:limit]
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _emit(
            {
                "ok": True,
                "query_id": query_id,
                "label": conn_cfg["label"],
                "collection": collection,
                "documents": docs,
                "row_count": len(docs),
                "truncated": truncated,
                "execution_time_ms": elapsed_ms,
            }
        )
    finally:
        client.close()


def cmd_aggregate(label: str, collection: str, pipeline_json: str) -> None:
    conn_cfg = find_connection(label)
    try:
        pipeline = json.loads(pipeline_json)
    except json.JSONDecodeError as exc:
        _die("invalid_pipeline", f"Pipeline must be valid JSON: {exc}", label=conn_cfg["label"])

    if not isinstance(pipeline, list):
        _die("invalid_pipeline", "Pipeline must be a JSON array of stages", label=conn_cfg["label"])

    if _pipeline_is_write(pipeline) and not conn_cfg["allow_write"]:
        _die(
            "write_blocked",
            f"Aggregation contains write stages ($out/$merge) — connection '{conn_cfg['label']}' "
            f"has ALLOW_WRITE=false. Set DB_MONGO_{conn_cfg['index']}_ALLOW_WRITE=true to permit.",
            label=conn_cfg["label"],
        )

    query_id = str(uuid.uuid4())
    started = time.monotonic()
    client = _connect(conn_cfg)
    try:
        db = _get_db(client, conn_cfg)
        cur = db[collection].aggregate(pipeline)
        limit = conn_cfg["max_rows"]
        docs = []
        truncated = False
        for i, d in enumerate(cur):
            if i >= limit:
                truncated = True
                break
            docs.append(d)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _emit(
            {
                "ok": True,
                "query_id": query_id,
                "label": conn_cfg["label"],
                "collection": collection,
                "documents": docs,
                "row_count": len(docs),
                "truncated": truncated,
                "execution_time_ms": elapsed_ms,
            }
        )
    finally:
        client.close()


def cmd_collections(label: str) -> None:
    conn_cfg = find_connection(label)
    client = _connect(conn_cfg)
    try:
        db = _get_db(client, conn_cfg)
        names = sorted(db.list_collection_names())
        _emit(
            {
                "ok": True,
                "label": conn_cfg["label"],
                "database": db.name,
                "collections": names,
                "row_count": len(names),
            }
        )
    finally:
        client.close()


def cmd_stats(label: str) -> None:
    conn_cfg = find_connection(label)
    client = _connect(conn_cfg)
    try:
        db = _get_db(client, conn_cfg)
        stats = db.command("dbstats")
        _emit(
            {
                "ok": True,
                "label": conn_cfg["label"],
                "database": db.name,
                "stats": {
                    "collections": stats.get("collections"),
                    "objects": stats.get("objects"),
                    "data_size_bytes": stats.get("dataSize"),
                    "storage_size_bytes": stats.get("storageSize"),
                    "indexes": stats.get("indexes"),
                    "index_size_bytes": stats.get("indexSize"),
                },
            }
        )
    finally:
        client.close()


def main(argv: list[str]) -> None:
    _load_dotenv()

    if len(argv) < 2:
        _die("usage", "Usage: db_client.py <accounts|test|find|aggregate|collections|stats> [args]")

    cmd = argv[1]
    args = argv[2:]

    try:
        if cmd == "accounts":
            cmd_accounts()
        elif cmd == "test":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py test <label>")
            cmd_test(args[0])
        elif cmd == "find":
            if len(args) < 2:
                _die("usage", "Usage: db_client.py find <label> <collection> [filter_json] [limit]")
            cmd_find(args[0], args[1], args[2] if len(args) > 2 else None, args[3] if len(args) > 3 else None)
        elif cmd == "aggregate":
            if len(args) < 3:
                _die("usage", "Usage: db_client.py aggregate <label> <collection> <pipeline_json>")
            cmd_aggregate(args[0], args[1], args[2])
        elif cmd == "collections":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py collections <label>")
            cmd_collections(args[0])
        elif cmd == "stats":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py stats <label>")
            cmd_stats(args[0])
        else:
            _die("usage", f"Unknown command '{cmd}'. Try: accounts, test, find, aggregate, collections, stats")
    except SystemExit:
        raise
    except ValueError as exc:
        _die("config_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        _die("unexpected", str(exc), type=type(exc).__name__)


if __name__ == "__main__":
    main(sys.argv)
