# Prompt para o Codex — Migração do EvoNexus para VPS

## Contexto geral

O EvoNexus é um sistema de orquestração de agentes (Claude Code, cronjobs, rotinas, dashboard Flask+React com terminal embutido) que roda localmente no meu PC. Preciso migrá-lo para minha VPS onde já rodam outros serviços (Hermes, EvoCRM, Traefik, pgvector). O PC não fica ligado a maior parte do dia, então a migração para VPS é obrigatória.

**Objetivo final:** EvoNexus rodando na VPS como serviço Docker Swarm, na mesma rede `network_public` dos outros serviços, acessível via subdomínio `nexus.workflowapi.com.br` através do Traefik.

## Ambiente da VPS (informações conhecidas)

### Rede Docker
- **`network_public`** — rede Docker external já criada na VPS, usada por Hermes, EvoCRM, Traefik. Todos os serviços do Swarm ficam nela.
- **`hermes_internal`** — rede secundária usada pelo Hermes (não precisa mexer aqui).

### Traefik (reverse proxy)
- Já rodando na VPS como serviço Docker Swarm
- Entry points: `websecure` (443/TLS) e `web` (80)
- Cert resolver: `letsencryptresolver` (também existe `letsencrypt` em algumas stacks)
- Padrão de labels: `traefik.enable=true`, `traefik.docker.network=network_public`, `traefik.http.routers.X.rule=Host(...)`, etc.
- Domínio base: `*.workflowapi.com.br`

### Banco de dados
- **`pgvector`** — container PostgreSQL com extensão pgvector já rodando na `network_public`, usado pelo EvoCRM.
- Password do postgres: definida via env `POSTGRES_PASSWORD` na VPS.
- **IMPORTANTE:** O EvoNexus usa **SQLite** (`dashboard.db`) como seu banco — NÃO usa Postgres nativamente. O `pyproject.toml` inclui `psycopg2-binary` e `alembic` como deps, mas o app roda em SQLite por padrão.

**Decisão sobre banco:** Preciso que o Codex verifique se o EvoNexus pode continuar usando SQLite (mais simples, menos um container pra subir) ou se há benefício real em migrar para o `pgvector` que já existe na VPS. Avaliar: o `dashboard.db` é um arquivo SQLite que vive dentro de um volume Docker — se reiniciar o container ou redeploiar a imagem, os dados persistem desde que o volume esteja nomeado. Para um sistema de orquestração que provavelmente tem pouco volume de escrita no DB (a maior parte é logs e agent memory em arquivos), SQLite com volume nomeado é suficiente. Confirmar isso analisando o código.

### Serviços já presentes na VPS
- Hermes Agent (profiles: mistica, excarplex) — porta 9119 (dashboard), 8642 (API server)
- EvoCRM — auth (3001), crm (3000), core (5555), processor (8000), bot_runtime (8080), gateway (3030), redis (6379)
- Traefik — reverse proxy com TLS automático via Let's Encrypt
- pgvector — PostgreSQL 15+ com extensão vector

## Estado atual do EvoNexus (local)

### Dockerfiles existentes
1. **`Dockerfile.swarm`** — runtime image (Python + Node 22 + claude-code CLI + openclaude CLI + gh CLI + todoist CLI + uv). Usa entrypoint.sh com bootstrap de config volume, geração de secret key, wait-for-key, Docker Secrets support. ENTRYPOINT = `entrypoint.sh`, CMD = `bash`.
2. **`Dockerfile.swarm.dashboard`** — dashboard image (3-stage: frontend build com Vite → terminal-server node-pty compile → runtime Python 3.12 + Node 22 + uv + ambos CLIs). Inclui `start-dashboard.sh` que sobe Flask (:8080) + terminal-server (:32352) simultaneamente. HEALTHCHECK em `/api/version`.
3. **`Dockerfile`** — runtime para local (sem Swarm bootstrap).
4. **`Dockerfile.dashboard`** — dashboard Python-only (sem terminal-server, sem Node no runtime).
5. **`Dockerfile.dev`** — dev local com volumes montados.

### Compose files existentes
- **`docker-compose.yml`** — local com build (3 services: dashboard, telegram, runner).
- **`docker-compose.dev.yml`** — local dev com hot reload.
- **`docker-compose.hub.yml`** — para usuários finais, puxa imagens do Docker Hub `evoapicloud/evo-nexus-*`. Sem rede externa, sem Traefik.
- **`docker-compose.proxy.yml`** — igual o hub mas com `expose` em vez de `ports` (para reverse proxy).
- **`evonexus.stack.yml`** — stack para Portainer/Swarm, usa rede `traefik-public` (external) e Traefik labels. **Este é o mais próximo do que preciso, mas usa `traefik-public` em vez de `network_public`.**

### CI/CD
- **`.github/workflows/docker-publish-britto.yml`** — workflow GitHub Actions que builda `Dockerfile.swarm` e `Dockerfile.swarm.dashboard` e publica no Docker Hub do meu usuário (`sistemabritto`). Triggers: push na branch `feat/chat-openclaude-provider-routing`, tags `v*`, e manual. Já configurado com secrets `DOCKERHUB_USERNAME` e `DOCKERHUB_TOKEN`.

### Git
- Fork: `https://github.com/sistemabritto/evo-nexus.git`
- Branch ativa: `feat/chat-openclaude-provider-routing`
- Origin: mesmo repo (push)

### Estrutura de serviços do EvoNexus
1. **dashboard** — Flask backend (:8080) + React frontend (servido como static) + Node terminal-server (:32352). Tudo num único container.
2. **telegram** — bot que escuta mensagens Telegram via Claude Code com plugin `plugin:telegram@claude-plugins-official`.
3. **scheduler** — executa `scheduler.py` (rotinas automáticas com a lib `schedule`).

Todos os 3 serviços compartilham volumes: `config`, `workspace`, `memory`, `adw_logs`, `agent_memory`, `claude_auth`, `codex_auth`.

### entrypoint.sh
- Cria `/workspace/config` a partir de defaults na primeira boot
- Gera `EVONEXUS_SECRET_KEY` e `KNOWLEDGE_MASTER_KEY` automaticamente
- Sela `.env` do config volume como env vars
- Se `REQUIRE_ANTHROPIC_KEY=1`, espera em loop 30s até a key aparecer (configurada via dashboard UI)

### Configuração pós-deploy
- Tudo (Anthropic API key, OpenRouter, NVIDIA NIM, Stripe, Omie, Bling, Asaas, Todoist, Telegram bot token, etc.) é configurado via dashboard UI após o primeiro boot
- Não há secrets no stack file — zero secrets committed

## O que eu preciso que o Codex faça

### 1. Auditoria do estado atual

Verificar se os Dockerfiles Swarm (`Dockerfile.swarm` e `Dockerfile.swarm.dashboard`) estão completos e corretos para buildar. Pontos de atenção:

- **`Dockerfile.swarm.dashboard`** Stage 2 (terminal-build): Node 22-slim + python3 + make + g++ para compilar `node-pty`. Verificar se o `dashboard/terminal-server/package.json` e `package-lock.json` existem e estão commitados.
- **`Dockerfile.swarm.dashboard`** Stage 1 (frontend-build): `npm install --legacy-peer-deps` resolve conflito de peer deps do React. Confirmar que `dashboard/frontend/package.json` está commitado.
- **`start-dashboard.sh`**: Verificar se tem permissão de execução (chmod +x) e se o `sed -i 's/\r//'` (remoção de CRLF) está presente — sem isso, scripts bash com line endings Windows quebram no Linux.
- **`entrypoint.sh`**: Mesma coisa — verificar line endings e permissão.
- **`.dockerignore`**: Confirmar que exclui `.env`, `.git`, `node_modules`, `__pycache__`, `.venv`, `workspace/`, `dashboard/data/`, `ADWs/logs/`, `.claude/agent-memory/`, `.claude/.env` para não vazar secrets nem dados locais na imagem.

Reportar qualquer arquivo faltante, erro de sintaxe, ou inconsistência.

### 2. Criar o stack file para a VPS

Baseado no `evonexus.stack.yml` existente, criar um **novo arquivo `evonexus-vps.stack.yml`** com as seguintes alterações:

**a) Rede:** Trocar `traefik-public` por `network_public` (external: true, name: network_public) — é a rede que já existe na minha VPS.

**b) Traefik labels:** Atualizar para o domínio `nexus.workflowapi.com.br`:
- `traefik.http.routers.evonexus_dashboard.rule=Host(\`nexus.workflowapi.com.br\`)`
- `traefik.http.routers.evonexus_terminal.rule=Host(\`nexus.workflowapi.com.br\`) && PathPrefix(\`/terminal\`)`
- Usar `traefik.docker.network=network_public`
- Usar `traefik.http.routers.X.entrypoints=websecure`
- Usar `traefik.http.routers.X.tls.certresolver=letsencryptresolver` (padrão da VPS)

**c) Imagens:** Usar `sistemabritto/evo-nexus-dashboard:latest` e `sistemabritto/evo-nexus-runtime:latest` (meu namespace no Docker Hub, não o `evoapicloud`).

**d) Volumes:** Manter os mesmos volumes nomeados do stack original:
```
evonexus_config, evonexus_workspace, evonexus_dashboard_data, evonexus_memory,
evonexus_adw_logs, evonexus_agent_memory, evonexus_claude_auth, evonexus_codex_auth
```

**e) Banco de dados:** Por enquanto manter SQLite (dashboard.db dentro do volume `evonexus_dashboard_data`). Não adicionar um serviço de Postgres ao stack — o EvoNexus não precisa. Confirmar que o `dashboard.db` está dentro de um volume nomeado e não no filesystem efêmero do container.

**f) Environment:** Manter variáveis do stack original:
```
TZ=America/Sao_Paulo
EVONEXUS_PORT=8080
TERMINAL_SERVER_PORT=32352
FORWARDED_ALLOW_IPS=*
REQUIRE_ANTHROPIC_KEY=1  (telegram + scheduler)
```

**g) Healthcheck:** O `Dockerfile.swarm.dashboard` já tem HEALTHCHECK em `/api/version`. Não precisa duplicar no compose.

**h) Resource limits:** Manter os limites do stack original (1 CPU, 1024M por serviço) — ajustar se necessário para a VPS.

### 3. Verificar o fluxo de build e publish

Confirmar que o `.github/workflows/docker-publish-britto.yml` está correto e vai funcionar:

- **Triggers:** Push na branch `feat/chat-openclaude-provider-routing` → build + push `:latest` e `:sha-XXXX`
- **Namespace:** `${{ secrets.DOCKERHUB_USERNAME }}` → deve resolver para `sistemabritto`
- **Imagens:** `evo-nexus-runtime` (Dockerfile.swarm) e `evo-nexus-dashboard` (Dockerfile.swarm.dashboard)
- **Platforms:** `linux/amd64` (VPS é x86_64)
- **Secrets necessários:** `DOCKERHUB_USERNAME` e `DOCKERHUB_TOKEN` — confirmar que estão configurados no repo GitHub (Settings → Secrets and variables → Actions)

Após fazer push da branch, as imagens devem aparecer em `https://hub.docker.com/r/sistemabritto/evo-nexus-dashboard/tags` e `https://hub.docker.com/r/sistemabritto/evo-nexus-runtime/tags`.

### 4. Checklist de deploy na VPS

Criar um checklist passo a passo do que precisa ser feito na VPS para subir o EvoNexus. Deve incluir:

1. **Pré-requisitos na VPS:**
   - Confirmar que `network_public` existe: `docker network ls | grep network_public`
   - Confirmar que Traefik está rodando e acessível
   - Confirmar que o domínio `nexus.workflowapi.com.br` aponta para o IP da VPS (DNS A record)
   - Confirmar que Docker Swarm está ativo: `docker node ls`

2. **Deploy do stack:**
   ```bash
   # Copiar evonexus-vps.stack.yml para a VPS
   docker stack deploy -c evonexus-vps.stack.yml evonexus
   ```

3. **Verificação pós-deploy:**
   - `docker service ls` — ver se os 3 serviços (dashboard, telegram, scheduler) estão `Replicas 1/1`
   - `docker service logs evonexus_evonexus_dashboard --tail 50` — ver se Flask e terminal-server subiram
   - `curl -k https://nexus.workflowapi.com.br/api/version` — deve retornar JSON com a versão
   - Abrir `https://nexus.workflowapi.com.br` no navegador — wizard de setup deve aparecer

4. **Configuração via dashboard UI:**
   - Criar conta admin
   - Configurar provider (Anthropic API key, ou NVIDIA NIM, ou OpenRouter)
   - Configurar Telegram bot token (se for usar o canal Telegram)
   - Configurar integrações (Omie, Bling, Asaas, etc. — conforme necessário)

5. **DNS:**
   - Adicionar registro A `nexus` → IP da VPS (ou CNAME se já tiver wildcard `*.workflowapi.com.br`)

### 5. Validação final

Rodar uma verificação local antes do deploy na VPS:

```bash
# Buildar localmente as imagens Swarm para testar
docker build -f Dockerfile.swarm -t evo-nexus-runtime:test .
docker build -f Dockerfile.swarm.dashboard -t evo-nexus-dashboard:test .

# Subir com o stack file novo (adaptando para não precisar da rede externa)
docker compose -f evonexus-vps.stack.yml up -d  # pode precisar ajustes pra local
```

Confirmar que as imagens buildam sem erro e o container dashboard inicia corretamente com `curl http://localhost:8080/api/version` respondendo.

## Pontos de atenção específicos

1. **Line endings:** O repo foi desenvolvido em Windows em algum momento. Verificar se `entrypoint.sh` e `start-dashboard.sh` têm CRLF (quebrariam no Linux da VPS). Se tiverem, converter para LF com `sed -i 's/\r$//'` ou `dos2unix`.

2. **Permissões de execução:** `entrypoint.sh` e `start-dashboard.sh` precisam de `chmod +x`. Os Dockerfiles Swarm já fazem `chmod +x` no `entrypoint.sh`, mas confirmar que `start-dashboard.sh` também recebe.

3. **Volume `dashboard.db`:** O arquivo `dashboard.db` (SQLite) está em `dashboard/data/` e é montado como volume `evonexus_dashboard_data` no container (`/workspace/dashboard/data`). Confirmar que o volume nomeado cobre esse path e que o DB persiste entre deploys.

4. **`.claude/` no build:** O `Dockerfile.swarm.dashboard` copia `.claude/` inteiro (`COPY .claude/ .claude/`), exceto o que está no `.dockerignore`. Confirmar que `.claude/agent-memory/` e `.claude/.env` estão excluídos (já estão no `.dockerignore`).

5. **`config/` no build:** The `Dockerfile.swarm.dashboard` copia `config/` inteiro. Confirmar que `providers.json` e `heartbeats.yaml` (que podem ter dados sensíveis locais) não vão parar na imagem — idealmente só os `.example` deveriam ir, ou então sobrescrever via volume em runtime.

6. **`_defaults/` bootstrap:** O `Dockerfile.swarm.dashboard` cria `/workspace/_defaults/` a partir de `config/` e `.env.example` no build. O `entrypoint.sh` usa isso para popular o volume `config` na primeira boot. Verificar que isso funciona corretamente — o container dashboard precisa ter `entrypoint.sh` antes de `start-dashboard.sh`.

7. **Replicas:** Como Hermes, EvoCRM e agora EvoNexus vão competir por recursos na mesma VPS, confirmar que os limites de CPU/memória do stack file estão adequados. Hermes aloca 1 CPU / 1GB, EvoCRM serviços menores. EvoNexus com 3 serviços a 1 CPU / 1GB cada é 3GB total — pode ser pesado. Avaliar se o scheduler e o telegram precisam de tantos recursos, ou se podem ser reduzidos (0.5 CPU / 512M talvez).

8. **Dependência entre serviços:** No `docker-compose.hub.yml`, `telegram` e `scheduler` têm `depends_on: dashboard (condition: service_healthy)`. No `evonexus.stack.yml` não tem `depends_on` (Swarm não suporta condition). Como o `entrypoint.sh` já faz wait-for-key, isso é OK — telegram e scheduler esperam a key aparecer, que só é configurada depois do dashboard estar acessível. Confirmar que isso funciona.

## Resumo do que entregar

1. ✅ Auditoria dos Dockerfiles e scripts → report do que está OK e do que precisa correção
2. ✅ Novo arquivo `evonexus-vps.stack.yml` → stack file pronto pra VPS
3. ✅ Verificação do workflow de CI/CD → confirma que build/push vai funcionar
4. ✅ Checklist de deploy na VPS → passo a passo
5. ✅ Validação local → build test das imagens antes do push

## Observação

O projeto está em `/home/sistemabritto/Documentos/evo-nexus/`. A branch ativa é `feat/chat-openclaude-provider-routing`. O fork no GitHub é `sistemabritto/evo-nexus`.

Responda em português. Seja direto e técnico. Execute os comandos necessários para validar o que estão pedindo — não apenas descreva o que faria.
