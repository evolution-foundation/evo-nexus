---
name: int-uptime-kuma
description: "Monitor and manage Uptime Kuma monitors. List status of all services, create HTTP/keyword monitors with custom headers, pause, resume and delete monitors. Reading uses Prometheus metrics (fast, no session needed). Writing uses Socket.io with username/password auth. Configure your instance via the UPTIME_KUMA_* env vars."
metadata:
  openclaw:
    requires:
      env:
        - UPTIME_KUMA_URL
        - UPTIME_KUMA_API_KEY
        - UPTIME_KUMA_USERNAME
        - UPTIME_KUMA_PASSWORD
      bins:
        - python3
    primaryEnv: UPTIME_KUMA_API_KEY
    files:
      - "scripts/*"
---

# Uptime Kuma — Monitoramento

Configure a instância via env (`UPTIME_KUMA_URL`). Compatível com Uptime Kuma **2.x** (`louislam/uptime-kuma`, validado na 2.2.1).

**Arquitetura de acesso:**
- Leitura (status, listagem): `/metrics` Prometheus com Basic Auth via API key (rápido, stateless, stdlib só)
- Escrita (criar, editar, pausar, retomar, deletar): Socket.io sobre WebSocket via `python-socketio` (sessão única persistente, login real verificado)

> **Interpretador:** os comandos de **escrita** dependem de `python-socketio` (já em `pyproject.toml`). Rode-os com o Python do projeto: `.venv/bin/python` (ou `uv run python`). A **leitura** (`status`/`get`) usa só a stdlib e roda com qualquer `python3`. O `smoke` roda com `python3`, mas seu step `write_auth` (login do caminho de escrita) só executa de fato com `.venv/bin/python` — com `python3` ele reporta `SKIP`. Para um smoke completo, use `.venv/bin/python`.

> **Compat Uptime Kuma 2.x:** no schema v2 o campo `conditions` é NOT NULL e `accepted_statuscodes` precisa ser lista — o cliente já injeta ambos (validado ao vivo na 2.2.1). A lib oficial `uptime-kuma-api` (lucasheld) só vai até 1.23.2, por isso a implementação é própria.

## Mutation-safety (escrita)

Todo comando de escrita (`add`/`edit`/`pause`/`resume`/`delete`) é **dry-run por padrão**:
sem `--execute` o comando **não muta** — imprime o que faria (ação + payload/alvo) em JSON
e sai 0, **sem abrir socket de escrita**. Mesma semântica do `int-blue`.

- **`--execute`** efetiva a mutação. Mantém o ack-check (erro do servidor → exit 1).
- **`--source`** (obrigatório em todo write): `human` | `agent:<slug>` | `routine:<name>`.
  Registrado no evento estruturado (stderr) para audit trail.
- **`snapshot_before`** em `edit` e `delete`: o estado atual do monitor (via Prometheus) é
  capturado e emitido no output para rollback manual. Em `delete`, o snapshot também é o
  2º fator (ver abaixo).
- **`--max-writes`** (global, default 5): teto de writes por execução.

### Gates por blast-radius

| Comando | Blast radius | Gate |
|---|---|---|
| `add` / `edit` / `resume` | baixo | dry-run default + `--execute` |
| `pause` | cega o monitoramento de um alvo de produção | dry-run default + `--execute` (warning no output) |
| `delete` | destrutivo, **irreversível** | dry-run default + `--execute` **+** `--confirm "<nome exato do monitor>"` (2º fator: o nome tem que bater com o snapshot real; sem snapshot disponível, o delete é bloqueado) |

## Setup (uma vez)

Variáveis no `.env` (a escrita autentica com `USERNAME`/`PASSWORD`; login é verificado e aborta em falha):
```
UPTIME_KUMA_URL=https://uptime.example.com
UPTIME_KUMA_API_KEY=uk1_...
UPTIME_KUMA_USERNAME=admin
UPTIME_KUMA_PASSWORD=...
```

> **Login falha alto e acionável.** O caminho de escrita **nunca** segue com sessão
> não-autenticada (era o bug que devolvia `{}` em silêncio). Se o servidor rejeita o
> login, o erro distingue as duas causas e some o palpite:
> - `authIncorrectCreds` → usuário/senha **stale ou incorretos** (o usuário não é
>   necessariamente `admin` — confira o `.env` contra a instância).
> - mensagem de `token`/`2FA` → a conta tem **2FA ativo**; o login Socket.io exige TOTP.
>   Use uma conta de serviço sem 2FA para a automação.
> A resposta crua do servidor sempre vem anexada à mensagem.

## Comandos

### Ver status de todos os monitores
```bash
python3 .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py status
```

### Filtrar por nome
```bash
python3 .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py status --name "Evolution"
```

### Ver apenas monitores com DOWN
```bash
python3 .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py status --down
```

### Ver detalhes de um monitor
```bash
python3 .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py get <id>
```

> **Lembrete:** sem `--execute` qualquer comando abaixo é dry-run (não muta). Veja o que
> faria primeiro; só então repita com `--execute`. `--source` é sempre obrigatório.

### Criar monitor HTTP simples (escrita → use `.venv/bin/python`)
```bash
# dry-run (default — só imprime o que faria):
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py add \
  "Nome do monitor" "https://example.com/health" \
  --interval 60 --maxretries 3 --source agent:atlas-project
# efetivar:
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py add \
  "Nome do monitor" "https://example.com/health" \
  --interval 60 --maxretries 3 --source agent:atlas-project --execute
```

### Criar monitor para Evolution Go (com header apikey) — escrita → use `.venv/bin/python`
```bash
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py add \
  "Evolution Go — WhatsApp" "https://your-evolution-go.example.com/instance/status" \
  --type keyword \
  --headers '{"apikey":"<apikey>"}' \
  --keyword '"Connected":true' \
  --interval 60 --source agent:atlas-project --execute
```

### Criar monitor para Evolution API (com header apikey) — escrita → use `.venv/bin/python`
```bash
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py add \
  "WhatsApp — minha-instancia" \
  "https://your-evolution-api.example.com/instance/connectionState/minha-instancia" \
  --type keyword \
  --headers '{"apikey":"<global_apikey>"}' \
  --keyword '"state":"open"' \
  --interval 60 --source agent:atlas-project --execute
```

### Editar monitor (escrita → use `.venv/bin/python`)
```bash
# dry-run mostra snapshot_before + o payload que enviaria:
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py edit <id> \
  "Novo nome" "https://example.com/health" --interval 120 --source agent:atlas-project
# efetivar:
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py edit <id> \
  "Novo nome" "https://example.com/health" --interval 120 --source agent:atlas-project --execute
```

### Pausar monitor (escrita → use `.venv/bin/python`)
```bash
# pause CEGA o monitoramento do alvo — confira o dry-run antes:
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py pause <id> \
  --source agent:atlas-project --execute
```

### Retomar monitor (escrita → use `.venv/bin/python`)
```bash
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py resume <id> \
  --source agent:atlas-project --execute
```

### Deletar monitor (escrita → use `.venv/bin/python`) — IRREVERSÍVEL, exige 2º fator
```bash
# 1) dry-run mostra o snapshot_before e o nome exato a confirmar:
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py delete <id> \
  --source agent:atlas-project
# 2) efetivar — --confirm tem que bater com o nome real do monitor:
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py delete <id> \
  --source agent:atlas-project --execute --confirm "Nome exato do monitor"
```

### Smoke test (saúde da skill — exit 0 sempre, JSON)
```bash
# leitura + escrita: use .venv/bin/python para exercitar o step write_auth
.venv/bin/python .claude/skills/int-uptime-kuma/scripts/uptime_kuma_client.py smoke
```
Reporta `{overall, steps[], duration_ms}`. Steps: `auth` (env de leitura), `fetch_metrics`
(Prometheus) e `write_auth` (login real do caminho de escrita, **sem mutar nada**). Se rodado
com `python3` (sem `python-socketio`) o `write_auth` reporta `SKIP` — daí a recomendação de
`.venv/bin/python` para um smoke completo.

## Output

JSON para stdout. Campos de status:
- `status`: `UP` | `DOWN` | `PENDING` | `MAINTENANCE`
- `uptime_24h`: porcentagem de uptime nas últimas 24h
- `response_ms`: tempo de resposta em ms
- `cert_days`: dias restantes no certificado SSL
- `cert_valid`: certificado válido?

## Notas

- O campo `monitor_url` nos labels Prometheus é a URL configurada no monitor.
- Monitores do tipo `group` aparecem no status mas não têm URL própria.
- Para Evolution Go, usar `--type keyword` com `--headers` e `--keyword '"Connected":true'` — a URL não tem o nome da instância (é identificada pelo header `apikey`).
- Para Evolution API, a URL inclui o nome da instância: `/instance/connectionState/{name}`.

## Fronteiras de responsabilidade

- **Esta skill** cobre **monitoramento de disponibilidade** (uptime/health checks): ler status,
  criar/editar/pausar/retomar/deletar monitores no Uptime Kuma.
- **Containers, stacks e Docker** (saber se um serviço *está rodando* / reiniciar) são do
  **`int-portainer`** — esta skill só observa o endpoint externo, não toca a infra.
- **Orquestração de agentes** (decisão de *agir* sobre um serviço caído, alertas, runbooks)
  é dos **heartbeats** (`config/heartbeats.yaml`) — esta skill é a sonda; quem decide e age é o heartbeat.
