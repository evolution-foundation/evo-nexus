# SPDX-License-Identifier: MIT
#!/usr/bin/env python3
"""
Portainer CLI — monitoramento e controle de stacks/containers.

Instâncias são descobertas automaticamente a partir das variáveis de ambiente:
  PORTAINER_<NOME>_URL    → URL base da instância (ex: https://portainer.example.com)
  PORTAINER_<NOME>_TOKEN  → API token da instância

Exemplo com duas instâncias (prod e staging):
  PORTAINER_PROD_URL=https://portainer.prod.example.com
  PORTAINER_PROD_TOKEN=ptr_xxxxx
  PORTAINER_STAGING_URL=https://portainer.staging.example.com
  PORTAINER_STAGING_TOKEN=ptr_yyyyy

Uso:
  python3 portainer.py <instance> <command> [args]

  instance: <nome-da-instância> | all
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def _load_dotenv():
    """Carrega .env do root do repo sem sobreescrever variáveis já no ambiente."""
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


# ---------------------------------------------------------------------------
# Config — descoberta dinâmica de instâncias via env
# ---------------------------------------------------------------------------

def _discover_instances() -> dict:
    """Constrói o dicionário de instâncias a partir das variáveis de ambiente.

    Varre o ambiente em busca de pares PORTAINER_<NOME>_URL / PORTAINER_<NOME>_TOKEN.
    O nome da instância é derivado de <NOME> em lowercase.

    Exemplo:
      PORTAINER_PROD_URL=https://portainer.example.com → instância "prod"
      PORTAINER_PROD_TOKEN=ptr_xxxxx
    """
    instances = {}
    for key, value in os.environ.items():
        if key.startswith("PORTAINER_") and key.endswith("_URL") and value:
            name = key[len("PORTAINER_"):-len("_URL")].lower()
            token_key = f"PORTAINER_{name.upper()}_TOKEN"
            token = os.environ.get(token_key, "")
            instances[name] = {
                "url": value.rstrip("/"),
                "token": token,
            }
    return instances


INSTANCES = _discover_instances()

CONTAINER_STATUS_EMOJI = {
    "running": "🟢",
    "exited": "🔴",
    "paused": "🟡",
    "restarting": "🔄",
    "dead": "💀",
    "created": "⚪",
    "removing": "🗑️",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(url: str, token: str, method: str = "GET", body=None, raw: bool = False):
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("X-API-Key", token)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    req.add_header("Accept", "application/json, text/plain, */*")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if raw:
                # Bytes crus, sem parse — ex: logs com mux do Docker (binário, não-UTF-8).
                return data
            if not data:
                return {}
            try:
                return json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Não-JSON / binário: devolve dict para callers que fazem .get().
                # Nunca retornar bytes — causaria AttributeError nos callers.
                # UnicodeDecodeError é irmão de JSONDecodeError (não filho): pegar ambos.
                return {"raw": data.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"[ERRO HTTP {e.code}] {url}\n{body_text}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[ERRO conexão] {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def api(instance_cfg: dict, path: str, method: str = "GET", body=None, raw: bool = False):
    url = f"{instance_cfg['url']}/api{path}"
    data = json.dumps(body).encode() if body else None
    return _request(url, instance_cfg["token"], method=method, body=data, raw=raw)


# ---------------------------------------------------------------------------
# Docker stream helpers
# ---------------------------------------------------------------------------

def _strip_docker_mux(data: bytes) -> str:
    """Remove headers de multiplexação do Docker (8 bytes por frame).

    Formato: [stream_type(1B), padding(3B), frame_size(4B big-endian)] + payload
    Referência: https://docs.docker.com/engine/api/v1.43/#tag/Container/operation/ContainerAttach
    """
    result = []
    i = 0
    while i + 8 <= len(data):
        frame_size = int.from_bytes(data[i + 4:i + 8], "big")
        frame_data = data[i + 8:i + 8 + frame_size]
        result.append(frame_data.decode("utf-8", errors="replace"))
        i += 8 + frame_size
    if i < len(data):
        # bytes restantes sem header válido — decodifica direto
        result.append(data[i:].decode("utf-8", errors="replace"))
    return "".join(result)


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------

def _fmt_ts(unix_ts) -> str:
    if not unix_ts:
        return "—"
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc).astimezone()
    return dt.strftime("%d/%m %H:%M")


def _print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_ping(cfg: dict, instance_name: str, **_):
    """Testa conexão e retorna versão do Portainer."""
    info = api(cfg, "/system/status")
    version = info.get("Version", "?")
    print(f"✅ {instance_name}: Portainer {version} — OK")


def cmd_endpoints(cfg: dict, instance_name: str, fmt: str = "table", **_):
    """Lista ambientes (endpoints) disponíveis."""
    endpoints = api(cfg, "/endpoints")
    if fmt == "json":
        _print_json(endpoints)
        return
    print(f"\n{'ID':<5} {'Nome':<30} {'Tipo':<15} {'Status':<10} {'Containers'}")
    print("-" * 75)
    for ep in endpoints:
        snap = ep.get("Snapshots", [{}])
        snap = snap[0] if snap else {}
        running = snap.get("RunningContainerCount", "?")
        total = snap.get("TotalContainerCount", "?")
        status = "🟢 online" if ep.get("Status") == 1 else "🔴 offline"
        tipo = {1: "Docker", 2: "Agent", 3: "Azure", 4: "Edge", 6: "Kubernetes"}.get(ep.get("Type"), "?")
        print(f"{ep['Id']:<5} {ep['Name']:<30} {tipo:<15} {status:<18} {running}/{total} containers")


def cmd_containers(cfg: dict, instance_name: str, endpoint_id=None, fmt: str = "table", show_all: bool = False, **_):
    """Lista containers de um endpoint (padrão: mostra todos, filtra problemáticos com --problems)."""
    endpoints = api(cfg, "/endpoints")
    targets = [ep for ep in endpoints if endpoint_id is None or ep["Id"] == endpoint_id]

    if not targets:
        print(f"Endpoint {endpoint_id} não encontrado em {instance_name}.")
        return

    all_data = []
    for ep in targets:
        eid = ep["Id"]
        containers = api(cfg, f"/endpoints/{eid}/docker/containers/json?all=true")
        for c in containers:
            all_data.append({
                "endpoint": ep["Name"],
                "id": c["Id"][:12],
                "name": (c.get("Names") or ["?"])[0].lstrip("/"),
                "image": c.get("Image", "?").split(":")[0].split("/")[-1],
                "status": c.get("State", "?"),
                "status_text": c.get("Status", "?"),
                "created": c.get("Created"),
            })

    if fmt == "json":
        _print_json(all_data)
        return

    if not show_all:
        # Filtra só containers com problema
        problems = [c for c in all_data if c["status"] not in ("running",)]
        if not problems:
            print(f"✅ {instance_name}: todos os containers estão rodando normalmente.")
            return
        display = problems
        print(f"⚠️  {instance_name}: {len(problems)} container(s) com problema:\n")
    else:
        display = all_data

    print(f"  {'Endpoint':<20} {'Container':<30} {'Imagem':<25} {'Status':<12} {'Criado'}")
    print("  " + "-" * 100)
    for c in display:
        emoji = CONTAINER_STATUS_EMOJI.get(c["status"], "❓")
        print(f"  {c['endpoint']:<20} {c['name']:<30} {c['image']:<25} {emoji} {c['status']:<10} {_fmt_ts(c['created'])}")


def cmd_stacks(cfg: dict, instance_name: str, fmt: str = "table", **_):
    """Lista todas as stacks."""
    stacks = api(cfg, "/stacks")
    if fmt == "json":
        _print_json(stacks)
        return
    print(f"\n  {'ID':<6} {'Nome':<35} {'Status':<12} {'Endpoint':<20} {'Atualizado'}")
    print("  " + "-" * 90)
    for s in stacks:
        status = s.get("Status", 0)
        status_str = "🟢 ativo" if status == 1 else "🔴 parado"
        updated = _fmt_ts(s.get("UpdateDate") or s.get("CreationDate"))
        ep_name = s.get("EndpointId", "?")
        print(f"  {s['Id']:<6} {s['Name']:<35} {status_str:<20} {str(ep_name):<20} {updated}")


def cmd_stack_start(cfg: dict, instance_name: str, stack_id: int, **_):
    """Liga uma stack parada."""
    result = api(cfg, f"/stacks/{stack_id}/start", method="POST")
    name = result.get("Name", f"stack #{stack_id}")
    print(f"🟢 {instance_name}: stack '{name}' iniciada com sucesso.")


def cmd_stack_stop(cfg: dict, instance_name: str, stack_id: int, **_):
    """Para uma stack ativa."""
    result = api(cfg, f"/stacks/{stack_id}/stop", method="POST")
    name = result.get("Name", f"stack #{stack_id}")
    print(f"🔴 {instance_name}: stack '{name}' parada com sucesso.")


def cmd_logs(cfg: dict, instance_name: str, endpoint_id, container: str, lines: int = 50, **_):
    """Últimas N linhas de log de um container (por nome ou ID)."""
    if endpoint_id is None:
        print(
            "Erro: --endpoint-id/-e é obrigatório para o comando 'logs'.\n"
            "Use 'endpoints' para listar os IDs disponíveis.",
            file=sys.stderr,
        )
        sys.exit(1)
    containers = api(cfg, f"/endpoints/{endpoint_id}/docker/containers/json?all=true")
    match = None
    for c in containers:
        names = [(n.lstrip("/")) for n in (c.get("Names") or [])]
        if container in names or c["Id"].startswith(container):
            match = c
            break

    if not match:
        print(f"Container '{container}' não encontrado no endpoint {endpoint_id} de {instance_name}.")
        sys.exit(1)

    cid = match["Id"]
    logs = api(cfg, f"/endpoints/{endpoint_id}/docker/containers/{cid}/logs?stdout=true&stderr=true&tail={lines}", raw=True)
    # logs retorna bytes com headers multiplexados do Docker (8 bytes por frame)
    # ou string se já decodificado, ou dict em caso inesperado
    if isinstance(logs, bytes):
        text = _strip_docker_mux(logs)
    elif isinstance(logs, str):
        text = logs
    else:
        text = json.dumps(logs, indent=2)
    print(f"=== Logs: {match['Names'][0].lstrip('/')} ({instance_name}) — últimas {lines} linhas ===")
    print(text)


def cmd_health(cfg: dict, instance_name: str, **_):
    """Resumo rápido de saúde: endpoints + containers com problema."""
    endpoints = api(cfg, "/endpoints")
    stacks = api(cfg, "/stacks")
    total_stacks = len(stacks)
    stopped_stacks = [s for s in stacks if s.get("Status") != 1]

    problem_containers = []
    for ep in endpoints:
        eid = ep["Id"]
        try:
            containers = api(cfg, f"/endpoints/{eid}/docker/containers/json?all=true")
            for c in containers:
                if c.get("State") != "running":
                    problem_containers.append({
                        "endpoint": ep["Name"],
                        "name": (c.get("Names") or ["?"])[0].lstrip("/"),
                        "state": c.get("State", "?"),
                    })
        except SystemExit:
            pass

    print(f"\n{'='*50}")
    print(f"  🏥 Health: {instance_name}")
    print(f"{'='*50}")
    print(f"  Endpoints:  {len(endpoints)} registrado(s)")
    print(f"  Stacks:     {total_stacks} total | {len(stopped_stacks)} parada(s)")
    print(f"  Containers: {len(problem_containers)} com problema")

    if stopped_stacks:
        print(f"\n  ⛔ Stacks paradas:")
        for s in stopped_stacks:
            print(f"     - [{s['Id']}] {s['Name']}")

    if problem_containers:
        print(f"\n  ⚠️  Containers com problema:")
        for c in problem_containers:
            emoji = CONTAINER_STATUS_EMOJI.get(c["state"], "❓")
            print(f"     {emoji} [{c['endpoint']}] {c['name']} — {c['state']}")
    else:
        print(f"\n  ✅ Todos os containers rodando normalmente.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

COMMANDS = {
    "ping": cmd_ping,
    "endpoints": cmd_endpoints,
    "containers": cmd_containers,
    "stacks": cmd_stacks,
    "stack-start": cmd_stack_start,
    "stack-stop": cmd_stack_stop,
    "logs": cmd_logs,
    "health": cmd_health,
}


def resolve_instances(name: str) -> list:
    """Resolve o nome da instância para [(name, cfg), ...].

    Aceita o nome de qualquer instância configurada via PORTAINER_<NOME>_URL/TOKEN,
    ou "all" para iterar sobre todas as instâncias configuradas.
    """
    if not INSTANCES:
        print(
            "Nenhuma instância Portainer configurada. "
            "Defina PORTAINER_<NOME>_URL e PORTAINER_<NOME>_TOKEN no .env.",
            file=sys.stderr,
        )
        sys.exit(1)
    if name == "all":
        return list(INSTANCES.items())
    if name not in INSTANCES:
        available = " | ".join(sorted(INSTANCES.keys()))
        print(
            f"Instância '{name}' desconhecida. "
            f"Instâncias configuradas: {available} | all",
            file=sys.stderr,
        )
        sys.exit(1)
    cfg = INSTANCES[name]
    if not cfg["url"] or not cfg["token"]:
        print(f"Credenciais não configuradas para '{name}'. Verifique o .env.", file=sys.stderr)
        sys.exit(1)
    return [(name, cfg)]


def cmd_smoke_portainer():
    """Smoke test: ping + status info per configured instance. Always exits 0 with JSON."""
    import time as _time
    overall = "PASS"
    steps = []
    t0 = _time.monotonic()

    if not INSTANCES:
        steps.append({"step": "config", "status": "FAIL",
                       "error": "no instances configured (set PORTAINER_<NOME>_URL + PORTAINER_<NOME>_TOKEN)"})
        overall = "FAIL"
    else:
        for inst_name, cfg in INSTANCES.items():
            # step: ping (reuses /system/status — same as cmd_ping)
            ts = _time.monotonic()
            step_name = f"ping_{inst_name}"
            if not cfg["url"] or not cfg["token"]:
                steps.append({"step": step_name, "status": "FAIL",
                               "error": f"credentials not set for {inst_name}",
                               "duration_ms": round((_time.monotonic() - ts) * 1000)})
                overall = "FAIL"
                continue
            try:
                info = api(cfg, "/system/status")
                version = info.get("Version", "?")
                steps.append({"step": step_name, "status": "PASS",
                               "version": version,
                               "duration_ms": round((_time.monotonic() - ts) * 1000)})
            except SystemExit as e:
                steps.append({"step": step_name, "status": "FAIL",
                               "error": f"api call failed (exit {e.code})",
                               "duration_ms": round((_time.monotonic() - ts) * 1000)})
                overall = "FAIL"
            except Exception as e:
                steps.append({"step": step_name, "status": "FAIL",
                               "error": str(e)[:300],
                               "duration_ms": round((_time.monotonic() - ts) * 1000)})
                overall = "FAIL"

    print(json.dumps({"overall": overall, "steps": steps,
                       "duration_ms": round((_time.monotonic() - t0) * 1000)}))
    sys.exit(0)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        cmd_smoke_portainer()

    # Constrói a lista de instâncias disponíveis para o help do argparse
    available = sorted(INSTANCES.keys()) or ["<nenhuma configurada>"]
    instance_choices = available + ["all"]

    parser = argparse.ArgumentParser(description="Portainer CLI para EvoNexus")
    parser.add_argument(
        "instance",
        choices=instance_choices,
        metavar=f"instance ({' | '.join(instance_choices)})",
        help="Nome da instância Portainer (conforme PORTAINER_<NOME>_URL no .env) ou 'all'",
    )
    parser.add_argument("command", choices=list(COMMANDS.keys()), help="Comando a executar")
    parser.add_argument("--endpoint-id", "-e", type=int, default=None, help="ID do endpoint Docker")
    parser.add_argument("--stack-id", "-s", type=int, default=None, help="ID da stack")
    parser.add_argument("--container", "-c", default=None, help="Nome ou ID do container")
    parser.add_argument("--lines", "-n", type=int, default=50, help="Linhas de log (padrão: 50)")
    parser.add_argument("--all", dest="show_all", action="store_true", help="Mostrar todos os containers (não só problemáticos)")
    parser.add_argument("--format", "-f", choices=["table", "json"], default="table", help="Formato de saída")
    args = parser.parse_args()

    fn = COMMANDS[args.command]
    instances = resolve_instances(args.instance)

    for inst_name, cfg in instances:
        fn(
            cfg=cfg,
            instance_name=inst_name,
            endpoint_id=args.endpoint_id,
            stack_id=args.stack_id,
            container=args.container,
            lines=args.lines,
            show_all=args.show_all,
            fmt=args.format,
        )


if __name__ == "__main__":
    main()
