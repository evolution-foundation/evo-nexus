#!/usr/bin/env python3
"""
Microsoft Clarity Data Export API client.
Calls clarity.ms/export-data directly — no third-party SDK or proxy.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load .env from workspace root (4 levels above this file)."""
    env_path = Path(__file__).resolve().parents[4] / ".env"
    if not env_path.exists():
        return
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

API_BASE = os.environ.get("CLARITY_API_BASE", "https://www.clarity.ms/export-data/api/v1")

# Valid values for dimension parameters
VALID_DIMS = {
    "Browser", "Device", "Country", "OS",
    "Source", "Medium", "Campaign", "Channel", "URL",
}

# HTTP status codes that warrant a single retry
RETRYABLE_CODES = {500, 502, 503, 504}

# 429 means daily quota exhausted — retry won't help; handled separately
QUOTA_EXCEEDED_CODE = 429


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------

def _discover_projects() -> dict[str, str]:
    """
    Return {project_name: token} for every CLARITY_TOKEN_<NAME> env var found.
    Names are returned upper-cased as stored; callers normalise for matching.
    """
    projects: dict[str, str] = {}
    for key, val in os.environ.items():
        if key.startswith("CLARITY_TOKEN_") and val:
            name = key[len("CLARITY_TOKEN_"):]
            projects[name] = val
    return projects


def _resolve_token(project: Optional[str]) -> tuple:
    """
    Return (project_label, token).

    Resolution order:
    1. --project <name>  → look for CLARITY_TOKEN_<NAME> (case-insensitive).
    2. No --project      → fall back to CLARITY_API_TOKEN if set.
    3. Otherwise         → list available projects and exit(1).
    """
    if project:
        upper = project.upper()
        token = os.environ.get(f"CLARITY_TOKEN_{upper}")
        if token:
            return upper, token
        # Not found
        available = list(_discover_projects().keys())
        print(
            f"Erro: nenhum token encontrado para --project {project!r}.\n"
            f"Projetos disponíveis: {available or ['(nenhum)']}\n"
            "Configure CLARITY_TOKEN_<NOME> no .env.",
            file=sys.stderr,
        )
        sys.exit(1)

    # No --project: fallback to CLARITY_API_TOKEN
    fallback = os.environ.get("CLARITY_API_TOKEN")
    if fallback:
        return "default", fallback

    available = list(_discover_projects().keys())
    if available:
        print(
            "Erro: especifique --project <nome>. Projetos disponíveis:\n  "
            + "\n  ".join(available),
            file=sys.stderr,
        )
    else:
        print(
            "Erro: nenhum token Clarity configurado.\n"
            "Configure CLARITY_API_TOKEN ou CLARITY_TOKEN_<NOME> no .env.\n"
            "Obtenha o token em https://clarity.microsoft.com → Settings → Export Data.",
            file=sys.stderr,
        )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

def _redact(msg: str, token: str) -> str:
    """Replace token value with *** in any string — last-resort defence-in-depth."""
    return msg.replace(token, "***") if token else msg


# ---------------------------------------------------------------------------
# HTTP helper — ALWAYS returns dict
# ---------------------------------------------------------------------------

def _clarity_request(token: str, path: str, params: Optional[dict] = None) -> dict:
    """
    Execute a GET request against the Clarity Export API.

    Always returns a dict:
      - On success: parsed JSON body.
      - On HTTP error: {"_error": True, "status": <code>, "message": <str>}.
    Never returns bytes or str — lint-http-types safe.
    """
    url = f"{API_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )

    # Build request with auth header — token NEVER appears in error messages below
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    def _do_request() -> dict:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                return json.loads(raw)  # type: ignore[return-value]
        except urllib.error.HTTPError as exc:
            code = exc.code
            # 429: quota exhausted — body may not be JSON
            if code == QUOTA_EXCEEDED_CODE:
                try:
                    body_text = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body_text = "(unreadable body)"
                # Trim body; run through _redact as defence-in-depth
                return {"_error": True, "status": code, "message": _redact(f"Limite diário de requisições esgotado (429). Body: {body_text[:200]}", token)}
            # Other HTTP errors — extract message from JSON body when available
            try:
                body = json.loads(exc.read())
                msg = body.get("message") or body.get("error") or f"HTTP {code}"
            except Exception:
                # Use only the HTTP status code; str(exc) may include request repr
                msg = f"HTTP {code}"
            return {"_error": True, "status": code, "message": _redact(msg, token)}
        except urllib.error.URLError as exc:
            # URLError.reason is a string or OSError — safe to stringify
            reason = str(exc.reason) if hasattr(exc, "reason") else "network error"
            return {"_error": True, "status": 0, "message": _redact(f"Network error: {reason}", token)}
        except Exception as exc:
            # Generic fallback — exc.args[0] could theoretically contain the token
            raw = exc.args[0] if exc.args else "unknown error"
            return {"_error": True, "status": 0, "message": _redact(f"{type(exc).__name__}: {raw}", token)}

    result = _do_request()

    # Single retry for transient server errors (5xx) — not for 429 (quota)
    # Only applies when result is a dict with _error (never a successful list response)
    if isinstance(result, dict) and result.get("_error") and result.get("status") in RETRYABLE_CODES:
        time.sleep(2)
        result = _do_request()

    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_days(days: int) -> None:
    if days not in (1, 2, 3):
        print(
            f"Erro: --days deve ser 1, 2 ou 3 (recebido: {days}). "
            "A API Clarity suporta apenas janelas de 1-3 dias.",
            file=sys.stderr,
        )
        sys.exit(1)


def _validate_dims(dims: list[str]) -> None:
    for d in dims:
        if d not in VALID_DIMS:
            print(
                f"Erro: dimensão inválida: {d!r}.\n"
                f"Valores aceitos: {sorted(VALID_DIMS)}\n"
                "Nota: 'Popular Pages' NÃO é uma dimensão válida neste endpoint "
                "(retorna body vazio — bug conhecido da Microsoft).",
                file=sys.stderr,
            )
            sys.exit(1)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _print_table(rows: list[dict], title: str = "") -> None:
    if not rows:
        return
    if title:
        print(f"\n=== {title} ===")
    keys = list(rows[0].keys())
    col_w = {k: max(len(k), max(len(str(r.get(k, ""))) for r in rows)) for k in keys}
    sep = "+-" + "-+-".join("-" * col_w[k] for k in keys) + "-+"
    header = "| " + " | ".join(k.ljust(col_w[k]) for k in keys) + " |"
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        print("| " + " | ".join(str(row.get(k, "")).ljust(col_w[k]) for k in keys) + " |")
    print(sep)


def _print_insights(data: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if not data:
        print("Sem dados retornados. Verifique se o projeto tem tráfego no período solicitado.")
        return

    for metric in data:
        metric_name = metric.get("metricName", "?")
        information = metric.get("information") or []
        if not information:
            print(f"\n[{metric_name}] — sem dados")
            continue
        _print_table(information, title=metric_name)
        print(f"  {len(information)} linha(s)")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_live_insights(args: argparse.Namespace) -> None:
    """Busca métricas do projeto no endpoint project-live-insights."""
    _validate_days(args.days)

    dims = [d for d in [args.dim1, args.dim2, args.dim3] if d]
    if dims:
        _validate_dims(dims)

    # Build params
    params: dict = {"numOfDays": args.days}
    if args.dim1:
        params["dimension1"] = args.dim1
    if args.dim2:
        params["dimension2"] = args.dim2
    if args.dim3:
        params["dimension3"] = args.dim3

    if args.all:
        # Varrer todos os projetos (cuidado: 10 req/dia/projeto)
        projects = _discover_projects()
        single_token = os.environ.get("CLARITY_API_TOKEN")
        if single_token:
            projects["default"] = single_token
        if not projects:
            print("Nenhum projeto configurado.", file=sys.stderr)
            sys.exit(1)
        ok_count = 0
        for name, token in projects.items():
            print(f"\n{'='*60}\nProjeto: {name}\n{'='*60}")
            result = _clarity_request(token, "project-live-insights", params)
            if isinstance(result, dict) and result.get("_error"):
                print(f"Erro {result['status']}: {result['message']}", file=sys.stderr)
                continue
            ok_count += 1
            _print_insights(result if isinstance(result, list) else [result], args.json)
        # Exit 1 se TODOS falharam; 0 se ao menos um sucesso
        if ok_count == 0:
            sys.exit(1)
        return

    label, token = _resolve_token(args.project)
    result = _clarity_request(token, "project-live-insights", params)

    if isinstance(result, dict) and result.get("_error"):
        print(f"Erro {result['status']}: {result['message']}", file=sys.stderr)
        sys.exit(1)

    data = result if isinstance(result, list) else []
    if not data:
        print(
            "Body vazio. Possíveis causas:\n"
            "  1. Dimensão inválida (ex: 'Popular Pages' não é dimensão válida).\n"
            "  2. Projeto sem tráfego no período.\n"
            "  3. Token sem permissão de Export Data.",
        )
        return

    if not args.json:
        print(f"Projeto: {label} | Últimos {args.days} dia(s)")
    _print_insights(data, args.json)


def cmd_list_projects(args: argparse.Namespace) -> None:
    """Lista projetos configurados (NÃO imprime tokens)."""
    projects = _discover_projects()
    single = os.environ.get("CLARITY_API_TOKEN")
    if single:
        projects["[CLARITY_API_TOKEN]"] = "(via CLARITY_API_TOKEN)"

    if not projects:
        print("Nenhum projeto Clarity configurado.")
        print("Configure CLARITY_API_TOKEN ou CLARITY_TOKEN_<NOME> no .env.")
        return

    print(f"Projetos Clarity configurados ({len(projects)}):")
    for name in sorted(projects):
        # Nunca imprimir o token
        print(f"  • {name}")


def cmd_smoke(args: argparse.Namespace) -> None:
    """
    E2E health check — SEMPRE exit 0, SEMPRE emite JSON.
    Contrato: {overall, steps[], duration_ms}.
    """
    t0 = time.monotonic()

    def _ms(since: float) -> int:
        return round((time.monotonic() - since) * 1000)

    out: dict = {"overall": "PASS", "steps": []}

    try:
        # Step 1: env_present — verifica se há algum token configurado
        ts = time.monotonic()
        projects = _discover_projects()
        single = os.environ.get("CLARITY_API_TOKEN")
        if not projects and not single:
            out["steps"].append({
                "step": "env_present",
                "status": "FAIL",
                "error": "Nenhum token Clarity encontrado (CLARITY_API_TOKEN ou CLARITY_TOKEN_<NOME>)",
                "duration_ms": _ms(ts),
            })
            out["overall"] = "FAIL"
            out["duration_ms"] = _ms(t0)
            print(json.dumps(out))
            return

        token_count = len(projects) + (1 if single else 0)
        out["steps"].append({
            "step": "env_present",
            "status": "PASS",
            "tokens_found": token_count,
            "duration_ms": _ms(ts),
        })

        # Step 2: auth / live call — usa --project se fornecido, senão o primeiro disponível
        ts = time.monotonic()
        if args.project:
            label, token = _resolve_token(args.project)
        elif projects:
            label = next(iter(projects))
            token = projects[label]
        else:
            label = "default"
            token = single  # type: ignore[assignment]

        # Chamada real com days=1 (mínimo possível — menor custo de quota)
        result = _clarity_request(token, "project-live-insights", {"numOfDays": 1})

        if isinstance(result, dict) and result.get("_error"):
            status_code = result.get("status", 0)
            # 429 = quota esgotada mas auth está OK (token válido)
            if status_code == QUOTA_EXCEEDED_CODE:
                out["steps"].append({
                    "step": "live_call",
                    "status": "WARN",
                    "note": "Token válido mas quota diária esgotada (429). Auth OK.",
                    "duration_ms": _ms(ts),
                })
                # WARN não falha o overall
            else:
                out["steps"].append({
                    "step": "live_call",
                    "status": "FAIL",
                    "error": f"HTTP {status_code}: {result.get('message', '')}",
                    "duration_ms": _ms(ts),
                })
                out["overall"] = "FAIL"
        else:
            row_count = sum(
                len(m.get("information") or [])
                for m in (result if isinstance(result, list) else [])
            )
            out["steps"].append({
                "step": "live_call",
                "status": "PASS",
                "project": label,
                "metrics_returned": len(result) if isinstance(result, list) else 0,
                "total_rows": row_count,
                "duration_ms": _ms(ts),
            })

    except SystemExit:
        # _resolve_token pode chamar sys.exit; captura para não vazar
        out["steps"].append({
            "step": "live_call",
            "status": "FAIL",
            "error": "Token inválido ou projeto não encontrado (sys.exit capturado)",
            "duration_ms": 0,
        })
        out["overall"] = "FAIL"
    except Exception as exc:
        # Redact any token value that might appear in the exception message
        _known_tokens = list(_discover_projects().values())
        if os.environ.get("CLARITY_API_TOKEN"):
            _known_tokens.append(os.environ["CLARITY_API_TOKEN"])
        _raw_err = str(exc)[:300]
        for _tok in _known_tokens:
            if _tok:
                _raw_err = _raw_err.replace(_tok, "***")
        out["steps"].append({
            "step": "unknown",
            "status": "FAIL",
            "error": _raw_err,
            "duration_ms": 0,
        })
        out["overall"] = "FAIL"

    out["duration_ms"] = _ms(t0)
    print(json.dumps(out))
    sys.exit(0)  # SEMPRE exit 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Microsoft Clarity Data Export API client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # ── smoke ─────────────────────────────────────────────────────────────
    smoke_p = sub.add_parser("smoke", help="Health check — sempre exit 0, emite JSON")
    smoke_p.add_argument("--project", help="Nome do projeto (CLARITY_TOKEN_<NOME>)")

    # ── list-projects ─────────────────────────────────────────────────────
    sub.add_parser("list-projects", help="Lista projetos configurados (sem imprimir tokens)")

    # ── live-insights ─────────────────────────────────────────────────────
    li_p = sub.add_parser("live-insights", help="Busca métricas do projeto (project-live-insights)")
    li_p.add_argument("--project", help="Nome do projeto (CLARITY_TOKEN_<NOME>); omitir usa CLARITY_API_TOKEN")
    li_p.add_argument("--days", type=int, default=1, choices=[1, 2, 3],
                      help="Janela em dias: 1, 2 ou 3 (default: 1)")
    li_p.add_argument("--dim1", metavar="DIM",
                      help=f"Dimensão 1. Aceitos: {', '.join(sorted(VALID_DIMS))}")
    li_p.add_argument("--dim2", metavar="DIM", help="Dimensão 2")
    li_p.add_argument("--dim3", metavar="DIM", help="Dimensão 3")
    li_p.add_argument("--all", action="store_true",
                      help="Buscar todos os projetos configurados (cuidado: consome quota de cada um)")
    li_p.add_argument("--json", action="store_true", help="Saída em JSON bruto")

    args = parser.parse_args()

    dispatch = {
        "smoke": cmd_smoke,
        "list-projects": cmd_list_projects,
        "live-insights": cmd_live_insights,
    }

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
