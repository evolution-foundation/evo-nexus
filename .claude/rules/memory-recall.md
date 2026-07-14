# Memory Recall & Self-Learning — Protocolo dos Agentes

Sessões são efêmeras (`/clear`, redeploys, novos terminais); memória não pode
ser. Este protocolo garante que todo agente **recupere contexto no início** e
**persista aprendizados no final** — o ciclo de self-learning do workspace.

## 1. Recall — no início de toda sessão/tarefa

Antes de trabalhar, recupere contexto nesta ordem (barato → caro):

1. **Hot cache** — `CLAUDE.md` já está no contexto (automático).
2. **Memória do agente** — leia `.claude/agent-memory/{seu-agente}/` (em
   especial `MEMORY.md` / `learnings.md` se existirem). É a SUA memória entre
   sessões; se o diretório está vazio, você é novo — comece a populá-lo.
3. **Busca semântica (MemPalace)** — quando o assunto tem histórico provável,
   consulte a base de conhecimento:

   ```python
   from dashboard.backend.sdk_client import evo
   hits = evo.get("/api/mempalace/search", params={"q": "deploy VPS omniroute", "n": 5})
   ```

   Indexa `memory/`, `.claude/agent-memory/` e `workspace/development/` —
   decisões, incidentes, retros e aprendizados de TODOS os agentes.
4. **Inbox de tickets (kanban)** — trabalho persistente atribuído a você:

   ```python
   abertos = evo.get("/api/tickets", params={"assignee_agent": "{seu-slug}", "status": "open"})
   ```

   Ticket relevante ao assunto atual → faça checkout atômico antes de agir
   (`POST /api/tickets/{id}/checkout`) e comente o progresso.

**Regra prática:** se o usuário mencionar algo que soa como já discutido
("aquele bug", "como fizemos antes", "o problema do X"), consulte o MemPalace
ANTES de dizer que não sabe.

## 2. Self-learning — ao final de toda tarefa não-trivial

Persista o que a próxima sessão vai precisar:

1. **Aprendizado do agente** — acrescente em
   `.claude/agent-memory/{seu-agente}/learnings.md` (crie se não existir):

   ```markdown
   ## 2026-07-07 — Bot Telegram 401 com omnirouter
   **O que aconteceu:** chave NVIDIA do env sequestrava chamadas ao gateway.
   **Lição:** chave do provider em providers.json SEMPRE vence o env global.
   **Aplicar quando:** qualquer 401 em provider OpenAI-compatível.
   ```

   Formato livre, mas sempre com **lição** e **quando aplicar**. Datas
   absolutas, nunca "hoje/ontem".
2. **Fato do workspace** (afeta outros agentes ou o negócio) → registre em
   `memory/` seguindo o padrão LLM Wiki (ver seção Memory System do
   CLAUDE.md); a rotina memory-sync propaga.
3. **Trabalho inacabado** → vira ticket (`POST /api/tickets`), nunca só uma
   nota na conversa. Conversa morre; ticket fica no kanban.
4. **Reindexar** — os arquivos novos entram na busca semântica no próximo
   mine. Depois de gravar aprendizados importantes, dispare:

   ```python
   evo.post("/api/mempalace/mine")  # indexa todas as fontes (idempotente)
   ```

## 3. O que NUNCA fazer

- Não confie que "a sessão anterior sabe" — ela não existe mais após /clear.
- Não duplique: antes de criar memória nova, busque se já existe (atualize o
  arquivo existente em vez de criar outro).
- Não guarde segredos (keys, tokens, senhas) em memórias ou learnings.
- Não salve o que o repositório já registra (código, git history, CLAUDE.md).

## Infraestrutura (referência)

| Peça | Onde | Persistência |
|---|---|---|
| MemPalace (índice chroma + fontes) | `dashboard/data/mempalace/` | volume `evonexus_dashboard_data` |
| Memória dos agentes | `.claude/agent-memory/` | volume `evonexus_agent_memory` |
| Memória do workspace | `memory/` | volume `evonexus_memory` |
| Tickets/kanban | SQLite `dashboard.db` | volume `evonexus_dashboard_data` |

API MemPalace: `GET /api/mempalace/status` · `GET /api/mempalace/search?q=&n=` ·
`POST /api/mempalace/mine` · fontes em `GET/POST /api/mempalace/sources`.
O pacote vem pré-instalado na imagem do dashboard; fontes padrão são seedadas
no primeiro uso.

## Regras relacionadas

- `tickets.md` — inbox, checkout atômico, menções
- `agents.md` — agent-memory por agente, EvoClient
- `heartbeats.md` — heartbeats consomem o mesmo inbox
