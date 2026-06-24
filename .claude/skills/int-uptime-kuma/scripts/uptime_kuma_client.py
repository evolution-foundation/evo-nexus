#!/usr/bin/env python3
"""
Uptime Kuma client — leitura via Prometheus /metrics, gestão via Socket.io.

Leitura (status, listagem, get): /metrics Prometheus com Basic Auth (API key,
stateless — rápido e robusto).
Escrita (add, edit, pause, resume, delete): Socket.io sobre WebSocket via
python-socketio, com sessão única persistente e login real (verifica `ok`).

Mutation-safety: writes são dry-run por padrão (não abrem socket). Use --execute
para efetivar. --source (human | agent:<slug> | routine:<name>) é obrigatório em
todo write. edit/delete capturam snapshot_before (estado atual via Prometheus)
para rollback manual. delete exige um 2º fator (--confirm <nome do monitor>).

Compatível com Uptime Kuma 2.x (campo `conditions` é NOT NULL no schema v2;
`accepted_statuscodes` precisa ser lista). Instância via env (sem hardcode).
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

_write_count = 0
_max_writes = 5


# ── env ──────────────────────────────────────────────────────────────────────

def _load_dotenv():
    env_path = Path(__file__).resolve().parents[4] / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

_load_dotenv()

BASE_URL   = os.environ.get("UPTIME_KUMA_URL", "").rstrip("/")
API_KEY    = os.environ.get("UPTIME_KUMA_API_KEY", "")
USERNAME   = os.environ.get("UPTIME_KUMA_USERNAME", "")
PASSWORD   = os.environ.get("UPTIME_KUMA_PASSWORD", "")
UA         = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ── Prometheus metrics reader ────────────────────────────────────────────────

def _fetch_metrics() -> str:
    cred = base64.b64encode(f"apikey:{API_KEY}".encode()).decode()
    req = urllib.request.Request(
        f"{BASE_URL}/metrics",
        headers={"Authorization": f"Basic {cred}", "User-Agent": UA}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode()


def _parse_metrics(raw: str) -> list[dict]:
    """Parse Prometheus metrics into a list of monitor dicts."""
    monitors: dict[str, dict] = {}
    for line in raw.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        # e.g.: monitor_status{monitor_id="3",monitor_name="...",monitor_type="http",...} 1
        metric_name = line.split("{")[0]
        if metric_name not in (
            "monitor_status", "monitor_uptime_ratio",
            "monitor_response_time", "monitor_cert_days_remaining",
            "monitor_cert_is_valid",
        ):
            continue
        try:
            labels_str = line[line.index("{") + 1:line.index("}")]
            value_str  = line[line.index("}") + 1:].strip()
            # only use the base value (no 'window' label for uptime)
            labels: dict[str, str] = {}
            for part in labels_str.split(","):
                k, _, v = part.partition("=")
                labels[k.strip()] = v.strip().strip('"')
            if "window" in labels:
                continue  # skip windowed uptime duplicates; keep only non-windowed
            mid = labels.get("monitor_id", "")
            if not mid:
                continue
            if mid not in monitors:
                monitors[mid] = {
                    "id": mid,
                    "name":  labels.get("monitor_name", ""),
                    "type":  labels.get("monitor_type", ""),
                    "url":   labels.get("monitor_url", ""),
                }
            try:
                val = float(value_str)
            except ValueError:
                continue
            if metric_name == "monitor_status":
                STATUS_MAP = {1: "UP", 0: "DOWN", 2: "PENDING", 3: "MAINTENANCE"}
                monitors[mid]["status"] = STATUS_MAP.get(int(val), str(int(val)))
            elif metric_name == "monitor_uptime_ratio":
                monitors[mid]["uptime_24h"] = f"{val * 100:.1f}%"
            elif metric_name == "monitor_response_time":
                monitors[mid]["response_ms"] = int(val)
            elif metric_name == "monitor_cert_days_remaining":
                monitors[mid]["cert_days"] = int(val)
            elif metric_name == "monitor_cert_is_valid":
                monitors[mid]["cert_valid"] = bool(int(val))
        except (ValueError, IndexError):
            continue
    return sorted(monitors.values(), key=lambda m: int(m["id"]))


# ── Socket.io client (WebSocket via python-socketio) ──────────────────────────

def _login_hint(resp: Any) -> str:
    """Translate a rejected login ack into an actionable diagnosis.

    The two failure modes need different fixes, so the message must tell them
    apart instead of just echoing the raw payload (the gap that made the auth
    failure opaque): wrong/stale credentials vs. 2FA enabled on the account.
    The raw ack is always appended so nothing is hidden.
    """
    raw = json.dumps(resp, ensure_ascii=False)
    msg = (resp.get("msg") if isinstance(resp, dict) else "") or ""
    low = msg.lower()
    if "token" in low or "2fa" in low or "twofa" in low:
        hint = ("2FA está ativo nesta conta — o login Socket.io exige o código TOTP "
                "(o cliente envia token vazio). Desative o 2FA do usuário de serviço "
                "ou use uma conta sem 2FA para a automação.")
    elif "incorrectcreds" in low or "credential" in low or "password" in low:
        hint = ("credenciais rejeitadas — UPTIME_KUMA_USERNAME/PASSWORD stale ou "
                "incorretos. Confira o usuário (não é necessariamente 'admin') e a "
                "senha no .env contra a instância.")
    else:
        hint = "login rejeitado pelo servidor."
    return f"{hint} (resposta do servidor: {raw})"


class UKSocket:
    """Socket.io client over WebSocket using python-socketio.

    A single persistent connection avoids the session-loss problem of a
    curl-per-request long-polling model. Login is verified (`ok` must be True);
    a failed login raises so callers never operate on an unauthenticated socket.
    """

    def __init__(self):
        self._sio = None

    def connect(self, attempts: int = 3):
        # Wall-clock ceiling matters: this client feeds autonomous monitoring
        # (heartbeats) — it must fail fast, not hang. Worst case ~3 attempts ×
        # 8s wait_timeout + linear backoff (2+4s) ≈ 30s, then it gives up.
        import socketio
        last_err: Optional[Exception] = None
        for i in range(attempts):
            sio = socketio.Client(reconnection=False, request_timeout=10)
            try:
                sio.connect(BASE_URL, transports=["websocket"], wait_timeout=8)
                self._sio = sio
                # let the server emit info / loginRequired before we proceed
                time.sleep(0.4)
                return
            except Exception as e:  # noqa: BLE001 — retry any connect failure
                last_err = e
                try:
                    sio.disconnect()
                except Exception:
                    pass
                time.sleep(2.0 * (i + 1))
        raise RuntimeError(f"socket connect failed after {attempts} attempts: {last_err}")

    def login(self) -> dict:
        """Authenticate. Raises if the server rejects the credentials.

        The returned token is not stored: python-socketio keeps the underlying
        connection authenticated for the lifetime of this socket, so every
        subsequent `call()` runs on the logged-in session without re-sending it.
        """
        resp = self._sio.call(
            "login", {"username": USERNAME, "password": PASSWORD, "token": ""},
            timeout=15,
        )
        if not (isinstance(resp, dict) and resp.get("ok")):
            raise RuntimeError(f"login failed: {_login_hint(resp)}")
        return resp

    def call(self, event: str, *args) -> Any:
        return self._sio.call(event, *args, timeout=20)

    def close(self):
        if self._sio is not None:
            try:
                self._sio.disconnect()
            except Exception:
                pass


def _socket_session() -> UKSocket:
    if not USERNAME or not PASSWORD:
        print(json.dumps({"error": "UPTIME_KUMA_USERNAME / UPTIME_KUMA_PASSWORD not set"}))
        sys.exit(1)
    sio = UKSocket()
    try:
        sio.connect()
        sio.login()
    except Exception as e:  # noqa: BLE001 — surface the real failure, never swallow
        sio.close()
        print(json.dumps({"error": str(e)[:500]}))
        sys.exit(1)
    return sio


def _check_ack(resp: Any) -> Any:
    """A write ack must be {ok: true}. Otherwise emit the error + exit non-zero.

    Fixes the prior silent-failure bug where a server error (e.g. auth or schema)
    returned `{}` / `{ok:false}` but the CLI still exited 0.
    """
    if isinstance(resp, dict) and resp.get("ok"):
        return resp
    print(json.dumps({"error": "operation failed", "detail": resp}, ensure_ascii=False))
    sys.exit(1)


# ── mutation-safety (dry-run default, --source, --max-writes, logging) ─────────

_SOURCE_PATTERN = re.compile(r"^(human|agent:[a-z0-9_-]+|routine:[a-z0-9_-]+)$")


def validate_source(source: Optional[str]) -> str:
    """A escrita exige atribuição de origem (mesma semântica do int-blue)."""
    if not source or not _SOURCE_PATTERN.match(source):
        print(json.dumps({
            "error": ("--source obrigatório. Valores aceitos: "
                      "human | agent:<slug> | routine:<name>. "
                      f"Recebido: {source!r}")
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    return source


def check_write_limit() -> None:
    """Teto de writes por execução — defesa contra loop de mutação acidental."""
    global _write_count, _max_writes
    if _write_count >= _max_writes:
        print(json.dumps({
            "error": f"Limite de {_max_writes} writes por execução atingido. "
                     "Use --max-writes para aumentar."
        }), file=sys.stderr)
        sys.exit(1)
    _write_count += 1


def log_event(op: str, status: str, duration_ms: float, extra: Optional[dict] = None) -> None:
    """Evento estruturado em stderr (audit trail; stdout fica para o resultado)."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "op": op,
        "status": status,
        "duration_ms": round(duration_ms),
    }
    if extra:
        entry.update(extra)
    print(json.dumps(entry, ensure_ascii=False), file=sys.stderr)


def _snapshot_monitor(monitor_id: Any) -> Optional[dict]:
    """Captura o estado atual de um monitor via Prometheus (read-only).

    Usado antes de edit/delete para registrar `snapshot_before` (rollback manual).
    Graceful: se a leitura falhar ou o monitor não aparecer nos metrics (ex.:
    pausado não emite `monitor_status`), retorna None — nunca aborta a operação.
    """
    try:
        monitors = _parse_metrics(_fetch_metrics())
    except Exception:  # noqa: BLE001 — snapshot é best-effort, não bloqueia o write
        return None
    for m in monitors:
        if str(m.get("id")) == str(monitor_id):
            return m
    return None


def _build_monitor(args, with_id: bool = False) -> dict:
    """Build a monitor payload with the fields Uptime Kuma 2.x requires.

    `conditions` is NOT NULL in the v2 schema; `accepted_statuscodes` must be a
    list (the server iterates it). Defaults mirror the Uptime Kuma UI.
    """
    monitor = {
        "type":               args.type,
        "name":               args.name,
        "url":                args.url,
        "interval":           args.interval,
        "retryInterval":      args.retry_interval,
        "resendInterval":     0,
        "maxretries":         args.maxretries,
        "active":             True,
        "notificationIDList": {},
        "accepted_statuscodes": [str(args.expected_status)]
                                if args.expected_status is not None
                                else ["200-299"],
        "method":             "GET",
        "maxredirects":       10,
        "timeout":            48,
        "ignoreTls":          False,
        "upsideDown":         False,
        "expiryNotification": False,
        "conditions":         [],
    }
    if with_id:
        monitor["id"] = int(args.id)
    if args.headers:
        try:
            monitor["headers"] = json.loads(args.headers)
        except json.JSONDecodeError:
            print(json.dumps({"error": "invalid --headers JSON"}))
            sys.exit(1)
    if args.keyword:
        monitor["keyword"] = args.keyword
    return monitor


# ── commands ─────────────────────────────────────────────────────────────────

def _smoke_write_auth() -> dict:
    """Exercise the WRITE path login (connect + login, NO mutation).

    Returns a step dict. Graceful: never raises — reports SKIP if creds /
    python-socketio are unavailable, FAIL if the auth itself is rejected. This
    catches auth regressions the read-only Prometheus step is blind to (the bug
    that motivated Fase 1).
    """
    ts = time.monotonic()
    if not USERNAME or not PASSWORD:
        return {"step": "write_auth", "status": "SKIP",
                "reason": "UPTIME_KUMA_USERNAME / UPTIME_KUMA_PASSWORD not set",
                "duration_ms": round((time.monotonic() - ts) * 1000)}
    try:
        import socketio  # noqa: F401 — presence check; UKSocket.connect imports it
    except Exception:
        return {"step": "write_auth", "status": "SKIP",
                "reason": "python-socketio not installed (run write path with .venv/bin/python)",
                "duration_ms": round((time.monotonic() - ts) * 1000)}
    sio = UKSocket()
    try:
        sio.connect()
        sio.login()  # raises on rejected credentials
        return {"step": "write_auth", "status": "PASS",
                "duration_ms": round((time.monotonic() - ts) * 1000)}
    except Exception as e:  # noqa: BLE001 — never crash the smoke
        return {"step": "write_auth", "status": "FAIL",
                "error": str(e)[:300],
                "duration_ms": round((time.monotonic() - ts) * 1000)}
    finally:
        sio.close()


def cmd_smoke(_args=None):
    """Smoke test: read auth + fetch metrics + write-path login. Always exits 0 with JSON."""
    overall = "PASS"
    steps = []
    t0 = time.monotonic()

    # step 1: auth — check required env vars
    ts = time.monotonic()
    if not BASE_URL or not API_KEY:
        missing = []
        if not BASE_URL:
            missing.append("UPTIME_KUMA_URL")
        if not API_KEY:
            missing.append("UPTIME_KUMA_API_KEY")
        steps.append({"step": "auth", "status": "FAIL",
                       "error": f"Missing: {', '.join(missing)}",
                       "duration_ms": round((time.monotonic() - ts) * 1000)})
        overall = "FAIL"
        print(json.dumps({"overall": overall, "steps": steps,
                           "duration_ms": round((time.monotonic() - t0) * 1000)}))
        sys.exit(0)
    steps.append({"step": "auth", "status": "PASS",
                   "duration_ms": round((time.monotonic() - ts) * 1000)})

    # step 2: fetch metrics (reuses _fetch_metrics — same as cmd_status)
    ts = time.monotonic()
    try:
        raw = _fetch_metrics()
        monitors = _parse_metrics(raw)
        down = sum(1 for m in monitors if m.get("status") == "DOWN")
        steps.append({"step": "fetch_metrics", "status": "PASS",
                       "monitor_count": len(monitors), "down": down,
                       "duration_ms": round((time.monotonic() - ts) * 1000)})
    except Exception as e:
        steps.append({"step": "fetch_metrics", "status": "FAIL",
                       "error": str(e)[:300],
                       "duration_ms": round((time.monotonic() - ts) * 1000)})
        overall = "FAIL"

    # step 3: write-path login (no mutation) — SKIP doesn't fail overall, FAIL does
    wa = _smoke_write_auth()
    steps.append(wa)
    if wa["status"] == "FAIL":
        overall = "FAIL"

    print(json.dumps({"overall": overall, "steps": steps,
                       "duration_ms": round((time.monotonic() - t0) * 1000)}))
    sys.exit(0)


def cmd_status(args):
    raw = _fetch_metrics()
    monitors = _parse_metrics(raw)
    if args.name:
        monitors = [m for m in monitors if args.name.lower() in m["name"].lower()]
    if args.down:
        monitors = [m for m in monitors if m.get("status") == "DOWN"]
    print(json.dumps(monitors, indent=2, ensure_ascii=False))


def cmd_get(args):
    raw = _fetch_metrics()
    monitors = _parse_metrics(raw)
    match = [m for m in monitors if str(m["id"]) == str(args.id)]
    if not match:
        print(json.dumps({"error": f"monitor {args.id} not found"}))
        sys.exit(1)
    print(json.dumps(match[0], indent=2, ensure_ascii=False))


def cmd_add(args):
    validate_source(args.source)
    monitor = _build_monitor(args)
    if not args.execute:
        log_event("add", "dry_run", 0, {"source": args.source})
        print(json.dumps({"dry_run": True, "would": "add", "monitor": monitor},
                         indent=2, ensure_ascii=False))
        return
    check_write_limit()
    t0 = time.monotonic()
    sio = _socket_session()
    try:
        resp = _check_ack(sio.call("add", monitor))
        log_event("add", "ok", (time.monotonic() - t0) * 1000,
                  {"source": args.source, "monitorID": resp.get("monitorID")})
        print(json.dumps({**resp, "dry_run": False}, indent=2, ensure_ascii=False))
    finally:
        sio.close()


def cmd_edit(args):
    validate_source(args.source)
    monitor = _build_monitor(args, with_id=True)
    snapshot = _snapshot_monitor(args.id)
    if not args.execute:
        log_event("edit", "dry_run", 0, {"source": args.source, "id": int(args.id)})
        print(json.dumps({"dry_run": True, "would": "edit",
                          "snapshot_before": snapshot, "monitor": monitor},
                         indent=2, ensure_ascii=False))
        return
    check_write_limit()
    t0 = time.monotonic()
    sio = _socket_session()
    try:
        resp = _check_ack(sio.call("editMonitor", monitor))
        log_event("edit", "ok", (time.monotonic() - t0) * 1000,
                  {"source": args.source, "id": int(args.id)})
        print(json.dumps({**resp, "snapshot_before": snapshot, "dry_run": False},
                         indent=2, ensure_ascii=False))
    finally:
        sio.close()


def cmd_pause(args):
    validate_source(args.source)
    # pause cega o monitoramento de um alvo de produção — gate: exige --execute.
    if not args.execute:
        log_event("pause", "dry_run", 0, {"source": args.source, "id": int(args.id)})
        print(json.dumps({"dry_run": True, "would": "pause", "id": int(args.id),
                          "warning": "pausar cega o monitoramento deste alvo"},
                         indent=2, ensure_ascii=False))
        return
    check_write_limit()
    t0 = time.monotonic()
    sio = _socket_session()
    try:
        resp = _check_ack(sio.call("pauseMonitor", int(args.id)))
        log_event("pause", "ok", (time.monotonic() - t0) * 1000,
                  {"source": args.source, "id": int(args.id)})
        print(json.dumps({**resp, "dry_run": False}, indent=2, ensure_ascii=False))
    finally:
        sio.close()


def cmd_resume(args):
    validate_source(args.source)
    if not args.execute:
        log_event("resume", "dry_run", 0, {"source": args.source, "id": int(args.id)})
        print(json.dumps({"dry_run": True, "would": "resume", "id": int(args.id)},
                         indent=2, ensure_ascii=False))
        return
    check_write_limit()
    t0 = time.monotonic()
    sio = _socket_session()
    try:
        resp = _check_ack(sio.call("resumeMonitor", int(args.id)))
        log_event("resume", "ok", (time.monotonic() - t0) * 1000,
                  {"source": args.source, "id": int(args.id)})
        print(json.dumps({**resp, "dry_run": False}, indent=2, ensure_ascii=False))
    finally:
        sio.close()


def cmd_delete(args):
    validate_source(args.source)
    snapshot = _snapshot_monitor(args.id)
    if not args.execute:
        log_event("delete", "dry_run", 0, {"source": args.source, "id": int(args.id)})
        print(json.dumps({"dry_run": True, "would": "delete", "id": int(args.id),
                          "snapshot_before": snapshot,
                          "warning": "delete é IRREVERSÍVEL — confirme com "
                                     "--confirm \"<nome do monitor>\""},
                         indent=2, ensure_ascii=False))
        return
    # delete é destrutivo e irreversível — 2º fator: o nome informado em --confirm
    # tem que bater com o nome real do monitor (snapshot). Bloqueia delete acidental
    # por id errado.
    if not args.confirm:
        print(json.dumps({"error": "delete exige --confirm \"<nome do monitor>\" "
                                   "(2º fator) além de --execute"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    actual_name = snapshot.get("name") if snapshot else None
    if actual_name is None:
        print(json.dumps({"error": f"não foi possível confirmar o monitor {args.id}: "
                                   "snapshot indisponível (monitor pausado/ausente nos "
                                   "metrics). Delete bloqueado por segurança."},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    if args.confirm != actual_name:
        print(json.dumps({"error": "nome em --confirm não bate com o monitor",
                          "id": int(args.id), "expected": actual_name,
                          "got": args.confirm}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    check_write_limit()
    t0 = time.monotonic()
    sio = _socket_session()
    try:
        resp = _check_ack(sio.call("deleteMonitor", int(args.id)))
        log_event("delete", "ok", (time.monotonic() - t0) * 1000,
                  {"source": args.source, "id": int(args.id), "name": actual_name})
        print(json.dumps({**resp, "snapshot_before": snapshot, "dry_run": False},
                         indent=2, ensure_ascii=False))
    finally:
        sio.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

_MONITOR_TYPES = ["http", "port", "ping", "keyword", "json-query", "dns", "steam",
                  "mqtt", "sqlserver", "postgres", "mysql", "mongodb", "radius",
                  "redis", "group"]


def _add_monitor_args(parser):
    parser.add_argument("--type",            default="http", choices=_MONITOR_TYPES)
    parser.add_argument("--interval",        type=int, default=60)
    parser.add_argument("--retry-interval",  type=int, default=60)
    parser.add_argument("--maxretries",      type=int, default=3)
    parser.add_argument("--headers",         help='JSON string: \'{"apikey":"..."}\'')
    parser.add_argument("--keyword",         help="Keyword to search in response body")
    parser.add_argument("--expected-status", type=int, help="Expected HTTP status code")


def _add_write_args(parser):
    """Args de mutation-safety comuns a todo write: dry-run default + atribuição."""
    parser.add_argument("--source", required=True,
                        help="Origem da escrita: human | agent:<slug> | routine:<name>")
    parser.add_argument("--execute", action="store_true",
                        help="Efetivar a mutação (sem isso é dry-run — só imprime o que faria)")


def main():
    if not BASE_URL:
        print(json.dumps({"error": "UPTIME_KUMA_URL not set"}))
        sys.exit(1)

    p = argparse.ArgumentParser(description="Uptime Kuma CLI")
    p.add_argument("--max-writes", type=int, default=5,
                   help="Limite de writes por execução (default: 5)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("smoke", help="Smoke test: auth + fetch metrics")

    ps = sub.add_parser("status", help="List all monitors with current status")
    ps.add_argument("--name", help="Filter by name (substring)")
    ps.add_argument("--down", action="store_true", help="Show only DOWN monitors")

    pg = sub.add_parser("get", help="Get one monitor by ID")
    pg.add_argument("id")

    pa = sub.add_parser("add", help="Create a new monitor")
    pa.add_argument("name")
    pa.add_argument("url")
    _add_monitor_args(pa)
    _add_write_args(pa)

    pe = sub.add_parser("edit", help="Edit an existing monitor")
    pe.add_argument("id")
    pe.add_argument("name")
    pe.add_argument("url")
    _add_monitor_args(pe)
    _add_write_args(pe)

    for name, help_text in [("pause", "Pause monitor (cega o monitoramento do alvo)"),
                            ("resume", "Resume monitor"),
                            ("delete", "Delete monitor (IRREVERSÍVEL)")]:
        px = sub.add_parser(name, help=help_text)
        px.add_argument("id", help="Monitor ID")
        _add_write_args(px)
        if name == "delete":
            px.add_argument("--confirm", metavar="NOME",
                            help="2º fator obrigatório: nome exato do monitor a deletar")

    args = p.parse_args()
    global _max_writes
    _max_writes = args.max_writes
    {
        "smoke":  cmd_smoke,
        "status": cmd_status,
        "get":    cmd_get,
        "add":    cmd_add,
        "edit":   cmd_edit,
        "pause":  cmd_pause,
        "resume": cmd_resume,
        "delete": cmd_delete,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
