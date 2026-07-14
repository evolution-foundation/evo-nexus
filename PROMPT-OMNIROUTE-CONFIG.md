# Prompt — Configurar o OmniRoute com um agente (Claude Code)

Este arquivo é um **prompt pronto pra copiar e colar** num agente de código
(Claude Code, ou qualquer agente com acesso a shell/HTTP) para **auditar e
otimizar um OmniRoute remoto** — o gateway de IA da stack
[Omni-Nexus](https://github.com/sistemabritto/omni-nexus#readme) — usando a
management API e o CLI oficial em modo remoto.

Foi extraído de uma configuração real feita em produção (Sistema Britto, VPS
Swarm). O agente estuda o estado atual antes de mexer, aplica só mudanças
reversíveis e valida tudo com chamadas reais.

> Mantido por [Sistema Britto](https://sistemabritto.com.br) ·
> OmniRoute é um projeto de [Diego Souza](https://github.com/diegosouzapw/OmniRoute) (MIT)

---

## Pré-requisitos

1. **OmniRoute no ar** — ex.: serviço `omniroute` da
   [`evonexus-vps.stack.example.yml`](evonexus-vps.stack.example.yml)
   (dashboard em `https://omni.SEU-DOMINIO.com.br`).
2. **Access Token com scope `admin`** — gere no dashboard do OmniRoute, na
   seção **Access Tokens** (formato `oma_live_...`). É o token de
   **gerenciamento** — não confunda com as API keys de inferência (`sk-...`,
   geradas em **Dashboard → Endpoints**).
3. Um agente com acesso a shell (Claude Code em qualquer diretório serve).

## Como usar

Substitua os dois placeholders e cole o prompt abaixo no agente:

- `{{OMNIROUTE_URL}}` → ex.: `https://omni.seudominio.com.br`
- `{{ADMIN_TOKEN}}` → seu `oma_live_...` de scope admin

---

## O prompt

````text
Você vai auditar e otimizar um gateway OmniRoute remoto usando a management
API dele. Trabalhe em etapas, SEMPRE leia o estado atual antes de alterar
qualquer coisa, e valide cada mudança com uma chamada real.

Servidor: {{OMNIROUTE_URL}}
Access Token (scope admin): {{ADMIN_TOKEN}}

## Fatos importantes sobre a API (aprenda antes de começar)

- Access Tokens `oma_live_...` autenticam a MAIORIA das rotas de gerenciamento
  (`Authorization: Bearer <token>`), mas NÃO servem para inferência (`/v1/*`)
  nem para as rotas `/api/settings/<sub>` (compression etc.), que exigem
  sessão do dashboard OU uma API key `sk-...` com scope "manage".
- API keys de inferência (`sk-...`) são criadas via `POST /api/keys`
  (body: {"name":"...","scopes":[...]}). Para obter uma key administrativa,
  crie com scopes ["manage"] — a rota aceita o access token admin. A resposta
  traz o plaintext UMA vez; guarde.
- `/v1/chat/completions` do OmniRoute STREAMA SSE por padrão — sempre mande
  "stream": false em testes que fazem parse do JSON.
- Rotas que spawnam processos (/api/services/*, /api/mcp/*, /api/plugins/*)
  são loopback-only — não tente por token remoto.
- O roteamento `auto` é zero-config (combo virtual + LKGP): variantes
  `auto/<categoria>[:<tier>]` (coding, reasoning, fast, cheap, free, pro...)
  são anunciadas em GET /v1/models.

## Etapa 1 — Validar acesso e inventariar

1. GET /api/cli/whoami com o token → confirme scope admin.
2. GET /api/settings → salve o JSON (estado antes).
3. GET /api/providers → liste conexões: provider, isActive, testStatus,
   providerSpecificData.
4. (Opcional) Instale o CLI oficial num diretório temporário
   (`npm install omniroute`) e conecte:
   `omniroute connect {{OMNIROUTE_URL}} --key <token> --name vps`
   — aí `omniroute providers metrics`, `omniroute cost` etc. funcionam.

## Etapa 2 — Keys nomeadas (telemetria de custo por consumidor)

1. POST /api/keys {"name":"<consumidor>-provider"} → key de inferência
   dedicada (ex.: para o EvoNexus/Omni-Nexus, bots, IDEs). Uma key por
   consumidor = custo rastreável por key no dashboard.
2. POST /api/keys {"name":"agent-manage","scopes":["manage"]} → key
   administrativa para as rotas /api/settings/*.

## Etapa 3 — Otimizações globais (aplique e confirme uma a uma)

1. PATCH /api/settings (com o access token):
   {"autoRefreshProviderQuota": true, "debugMode": false}
   → o roteador `auto` passa a decidir com dados frescos de quota
   (maximiza assinaturas antes de queimar API paga) e corta overhead de
   log em produção.
2. PUT /api/settings/compression (com a key "manage" da etapa 2):
   {"enabled": true, "defaultMode": "off",
    "autoTriggerMode": "standard", "autoTriggerTokens": 32000}
   → requests pequenos ficam intocados; contextos grandes (agentes de
   código, heartbeats) ganham ~30% de economia com proteção de código.
3. Conexões marcadas como falhas (ex.: openrouter "credits_exhausted"):
   se providerSpecificData.importFreeModelsOnly=true, os modelos :free
   custam $0 e não dependem de crédito — force um re-teste:
   POST /api/providers/<connection-id>/test
   → testStatus deve voltar a "active".

## Etapa 4 — Validar fim-a-fim (com a key de inferência da etapa 2)

1. POST /v1/chat/completions {"model":"auto","messages":[...],
   "max_tokens":10,"stream":false} → espere HTTP 200 e inspecione os
   headers X-OmniRoute-Provider / X-OmniRoute-Model /
   X-OmniRoute-Compression (deve vir "off" em request pequeno).
2. GET /v1/models → liste as variantes `auto/` anunciadas.
3. Teste um modelo :free explicitamente se reativou alguma conexão.

## Etapa 5 — Relatório

Entregue: tabela antes/depois das settings alteradas, keys criadas (nome +
4 últimos chars, NUNCA a key inteira em logs), status das conexões, e
recomendação de mapeamento de model tiers para o cliente (ex.:
opus→auto/claude-opus, sonnet→auto/coding, haiku→auto/best-fast).

## Regras de segurança

- NÃO altere: requireLogin, senhas, JWT/API secrets, portas, proxy global.
- NÃO reinicie serviços nem chame rotas de lifecycle.
- NÃO imprima keys/tokens completos no relatório final.
- Toda mudança deve ser reversível com um único PATCH/PUT (documente o
  valor anterior).
````

---

## Alternativa via MCP

O OmniRoute também expõe um servidor MCP (`mcpEnabled` nas settings, rotas
`/api/mcp/*` — **loopback-only** por segurança). Para gerenciar por MCP o
agente precisa rodar na mesma máquina do OmniRoute. Para gestão remota, o
caminho acima (Access Token + management API/CLI) é o suportado.

## Referências

- [Docs do OmniRoute](https://github.com/diegosouzapw/OmniRoute/tree/main/docs) —
  `guides/REMOTE-MODE.md`, `routing/AUTO-COMBO.md`, `compression/COMPRESSION_GUIDE.md`
- [README do Omni-Nexus](README.md) — deploy completo da stack na VPS
- [Stack de exemplo](evonexus-vps.stack.example.yml) — serviço `omniroute`
