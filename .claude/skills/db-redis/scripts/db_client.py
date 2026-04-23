#!/usr/bin/env python3
"""db-redis — inspect Redis instances declared in .env.

Connections are numbered blocks in .env, same shape as SOCIAL_YOUTUBE_N_*:

    DB_REDIS_1_LABEL=cache-dev
    DB_REDIS_1_HOST=redis.dev.internal
    DB_REDIS_1_PORT=6379
    DB_REDIS_1_DB=0                    # numeric DB index, default 0
    DB_REDIS_1_USERNAME=               # optional, Redis 6+ ACL
    DB_REDIS_1_PASSWORD=...
    DB_REDIS_1_TLS=false               # default false
    DB_REDIS_1_ALLOW_WRITE=false       # default false
    DB_REDIS_1_QUERY_TIMEOUT=30
    DB_REDIS_1_MAX_ROWS=1000

Or a full URL alternative (URL wins when both are set):

    DB_REDIS_2_LABEL=queue-prod
    DB_REDIS_2_URL=rediss://user:pw@host:6380/0

Commands:
    db_client.py accounts
    db_client.py test <label>
    db_client.py keys <label> [pattern] [limit]
    db_client.py get <label> <key>
    db_client.py info <label> [section]
    db_client.py dbsize <label>

Read-only verbs only in v1 (GET, KEYS/SCAN, INFO, DBSIZE, TYPE, TTL). Write
verbs (SET, DEL, FLUSH, etc.) not exposed — add when ALLOW_WRITE is needed.

Exit 0 on success. Exit non-zero with single-line JSON on failure.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

PREFIX = "REDIS"
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

        url = os.environ.get(f"{p}URL", "").strip()
        conn = {
            "index": n,
            "label": label,
            "url": url or None,
            "host": os.environ.get(f"{p}HOST", "").strip() or None,
            "port": _parse_int(os.environ.get(f"{p}PORT"), 6379),
            "db": _parse_int(os.environ.get(f"{p}DB"), 0),
            "username": os.environ.get(f"{p}USERNAME", "").strip() or None,
            "password": os.environ.get(f"{p}PASSWORD", "") or None,
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


def _connect(conn: dict):
    try:
        import redis
    except ImportError:
        _die(
            "driver_missing",
            "redis is not installed. Run `uv pip install redis` "
            "or add it to your workspace's pyproject.toml.",
        )
        return

    if conn["url"]:
        return redis.Redis.from_url(
            conn["url"],
            socket_timeout=conn["query_timeout"],
            socket_connect_timeout=min(10, conn["query_timeout"]),
            decode_responses=True,
        )

    kwargs = {
        "host": conn["host"] or "localhost",
        "port": conn["port"],
        "db": conn["db"],
        "socket_timeout": conn["query_timeout"],
        "socket_connect_timeout": min(10, conn["query_timeout"]),
        "decode_responses": True,
    }
    if conn["username"]:
        kwargs["username"] = conn["username"]
    if conn["password"]:
        kwargs["password"] = conn["password"]
    if conn["tls"]:
        kwargs["ssl"] = True

    return redis.Redis(**kwargs)


def cmd_accounts() -> None:
    conns = load_connections()
    _emit(
        {
            "ok": True,
            "flavor": "redis",
            "count": len(conns),
            "connections": [
                {
                    "index": c["index"],
                    "label": c["label"],
                    "host": c["host"] if not c["url"] else "<url>",
                    "port": c["port"] if not c["url"] else None,
                    "database": str(c["db"]) if not c["url"] else None,
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
            client.ping()
            rtt_ms = int((time.monotonic() - started) * 1000)
            _emit({"ok": True, "label": conn_cfg["label"], "rtt_ms": rtt_ms})
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        _die("connection_failed", str(exc), label=conn_cfg["label"])


def cmd_keys(label: str, pattern: str | None, limit_str: str | None) -> None:
    """SCAN-based key listing (production-safe; avoids blocking KEYS *)."""
    conn_cfg = find_connection(label)
    pat = pattern or "*"
    try:
        user_limit = int(limit_str) if limit_str else conn_cfg["max_rows"]
    except ValueError:
        _die("invalid_args", f"Limit must be an integer, got: {limit_str}", label=conn_cfg["label"])

    limit = min(user_limit, conn_cfg["max_rows"])
    query_id = str(uuid.uuid4())
    started = time.monotonic()

    client = _connect(conn_cfg)
    try:
        out: list[str] = []
        truncated = False
        for key in client.scan_iter(match=pat, count=200):
            if len(out) >= limit:
                truncated = True
                break
            out.append(key)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _emit(
            {
                "ok": True,
                "query_id": query_id,
                "label": conn_cfg["label"],
                "pattern": pat,
                "keys": out,
                "row_count": len(out),
                "truncated": truncated,
                "execution_time_ms": elapsed_ms,
            }
        )
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def cmd_get(label: str, key: str) -> None:
    """Read a single key — auto-detects type and uses the right reader."""
    conn_cfg = find_connection(label)
    client = _connect(conn_cfg)
    try:
        t = client.type(key)
        if t == "none":
            _emit({"ok": True, "label": conn_cfg["label"], "key": key, "type": "none", "value": None, "ttl_seconds": None})
            return

        ttl = client.ttl(key)
        ttl_val = None if ttl < 0 else ttl

        value = None
        if t == "string":
            value = client.get(key)
        elif t == "list":
            value = client.lrange(key, 0, conn_cfg["max_rows"] - 1)
        elif t == "set":
            value = list(client.sscan_iter(key, count=200))
            if len(value) > conn_cfg["max_rows"]:
                value = value[: conn_cfg["max_rows"]]
        elif t == "hash":
            value = client.hgetall(key)
        elif t == "zset":
            value = client.zrange(key, 0, conn_cfg["max_rows"] - 1, withscores=True)
        elif t == "stream":
            value = client.xrange(key, count=conn_cfg["max_rows"])
        else:
            value = f"<unsupported type: {t}>"

        _emit({"ok": True, "label": conn_cfg["label"], "key": key, "type": t, "value": value, "ttl_seconds": ttl_val})
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def cmd_info(label: str, section: str | None) -> None:
    conn_cfg = find_connection(label)
    client = _connect(conn_cfg)
    try:
        info = client.info(section=section) if section else client.info()
        _emit({"ok": True, "label": conn_cfg["label"], "section": section or "default", "info": info})
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def cmd_dbsize(label: str) -> None:
    conn_cfg = find_connection(label)
    client = _connect(conn_cfg)
    try:
        size = client.dbsize()
        _emit({"ok": True, "label": conn_cfg["label"], "dbsize": size, "db": conn_cfg["db"]})
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str]) -> None:
    _load_dotenv()

    if len(argv) < 2:
        _die("usage", "Usage: db_client.py <accounts|test|keys|get|info|dbsize> [args]")

    cmd = argv[1]
    args = argv[2:]

    try:
        if cmd == "accounts":
            cmd_accounts()
        elif cmd == "test":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py test <label>")
            cmd_test(args[0])
        elif cmd == "keys":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py keys <label> [pattern] [limit]")
            cmd_keys(args[0], args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
        elif cmd == "get":
            if len(args) < 2:
                _die("usage", "Usage: db_client.py get <label> <key>")
            cmd_get(args[0], args[1])
        elif cmd == "info":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py info <label> [section]")
            cmd_info(args[0], args[1] if len(args) > 1 else None)
        elif cmd == "dbsize":
            if len(args) < 1:
                _die("usage", "Usage: db_client.py dbsize <label>")
            cmd_dbsize(args[0])
        else:
            _die("usage", f"Unknown command '{cmd}'. Try: accounts, test, keys, get, info, dbsize")
    except SystemExit:
        raise
    except ValueError as exc:
        _die("config_error", str(exc))
    except Exception as exc:  # noqa: BLE001
        _die("unexpected", str(exc), type=type(exc).__name__)


if __name__ == "__main__":
    main(sys.argv)
