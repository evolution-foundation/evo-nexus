# Guia curto — MemPalace para agentes

Este guia diz, em uma página, **quando** um agente deve usar MemPalace e **como** ler/escrever memória no workspace. Para o brief técnico completo, veja `workspace/development/research/[C]research-mempalace-agent-guide-2026-06-16.md`.

## 30-seconds cheatsheet

- **Buscar:** `evo.get("/api/mempalace/search", params={"q": "...", "n": 5})`
- **Indexar:** `evo.post("/api/mempalace/mine")`
- **Status:** `evo.get("/api/mempalace/status")`
- **Privado (1 agente):** `.claude/agent-memory/{agent}/`
- **Compartilhado (todos):** `memory/*.md`
- **Semântico (grande volume):** MemPalace

## TL;DR

| Onde | Use para | Como acessar |
|---|---|---|
| `.claude/agent-memory/{agent}/` | Notas privadas de um agente (decisões, gotchas, padrões de uma sessão) | Leitura/escrita de arquivo `.md` |
| `memory/*.md` | Conhecimento compartilhado entre **todos** os agentes (pessoas, projetos, glossário, contexto) | Leitura/escrita de arquivo `.md`; o conteúdo vira hot cache via `CLAUDE.md` |
| **MemPalace** (`dashboard/data/mempalace/`) | Busca semântica + BM25 sobre fontes grandes (código, docs, transcrições) | API REST `/api/mempalace/*`, MCP, CLI ou SDK Python |

`memory/*.md` e MemPalace **não competem**: o markdown é a fonte curada, o MemPalace é o índice de busca.

## Quando usar qual

```
Preciso guardar uma informação?
│
├── Contexto técnico de UMA sessão (gotcha, decisão local)
│   → .claude/agent-memory/{agente}/   (markdown)
│
├── Conhecimento que TODOS os agentes precisam (pessoa, projeto, glossário)
│   → memory/*.md                       (markdown)
│
└── Preciso BUSCAR semanticamente em muitos arquivos
    → MemPalace                         (índice)
```

Regra do `dev-remember`: nota compartilhada → `memory/`. Quando o volume e a busca semântica justificarem (centenas de arquivos), use MemPalace.

## Como ler

### API REST (rotinas, heartbeats, scripts)

```python
from dashboard.backend.sdk_client import evo

hits = evo.get("/api/mempalace/search", params={
    "q": "como funciona o provider fallback",
    "wing": "evo-nexus",
    "n": 5,
})
for r in hits.get("results", []):
    print(f"[{r['similarity']:.2f}] {r['source_file']}")
```

Outros endpoints úteis:

| Método | Rota | Função |
|---|---|---|
| GET | `/api/mempalace/status` | Versão, drawers, wings, rooms, status do mining |
| GET | `/api/mempalace/sources` | Fontes configuradas |
| POST | `/api/mempalace/mine` | Dispara reindexação (opcional `source_index`) |

Tudo requer `DASHBOARD_API_TOKEN` (injetado automaticamente pelo `evo` SDK).

### MCP (dentro do Claude Code)

```bash
claude mcp add mempalace -- python -m mempalace.mcp_server \
  --palace /home/sistemabritto/Documentos/evo-nexus/dashboard/data/mempalace
```

Depois, a tool `search_memories` aparece direto na sessão.

### CLI (debug rápido)

```bash
.venv/bin/mempalace search "autenticação jwt" \
  --palace dashboard/data/mempalace -n 5
```

## Como escrever

**Ninguém escreve drawers à mão.** O ciclo é:

1. Escreva o conteúdo em arquivos (`.md`, `.py`, `.ts`, …) dentro de uma fonte registrada.
2. Dispare a indexação: `POST /api/mempalace/mine`.
3. Acompanhe `GET /api/mempalace/status` (`mining.phase`, `files_done/total`).
4. Pronto — buscas já enxergam o conteúdo.

Para fixar `wing` e `rooms` de uma fonte, crie um `mempalace.yaml` na raiz dela. Caso contrário, o worker assume `wing = nome do diretório`, `room = general`.

## Estrutura Wing → Room → Drawer

| Nível | O que é | Exemplo |
|---|---|---|
| **Wing** | Projeto ou categoria top-level | `evo-nexus`, `evo-ai`, `docs` |
| **Room** | Tópico dentro da wing | `architecture`, `decisions`, `technical` |
| **Drawer** | Chunk de ~800 caracteres + metadata | Indexado automaticamente |

Filtre por `wing`/`room` na busca quando souber o domínio — corta ruído.

## MemPalace vs Knowledge Base (pgvector)

São produtos **diferentes** no mesmo dashboard:

| | MemPalace | Knowledge Base |
|---|---|---|
| Storage | ChromaDB local em `dashboard/data/mempalace/` | Postgres + pgvector (BYO) |
| Escopo | Memória pessoal do workspace, offline | Multi-tenant, API-first para times e produtos (ex.: Evo Academy) |
| Rota | `/api/mempalace/*` | `/api/knowledge/*` e `/api/knowledge/v1/*` |
| Skills | — | `knowledge-{query,summarize,ingest,browse,organize,admin}` |

Se a busca é para o agente raciocinar localmente, é MemPalace. Se é para servir clientes externos via API, é Knowledge Base.

## Versão e observações

- **MemPalace 3.4.0** instalado em `.venv/` (verificado em 2026-06-16).
- Embedding padrão: `all-MiniLM-L6-v2` (384-dim, inglês). Para pt-BR, considere trocar para `embeddinggemma-300m-ONNX` — exige `mempalace repair rebuild-index`.
- Worker de mining é subprocesso detached: se o dashboard reiniciar no meio, o worker antigo termina sozinho; o `/status` faz PID check no próximo poll.
- ChromaDB não tem auth própria — a segurança vem do `require_permission` no Flask (`mempalace:view` e `mempalace:manage`).

## Próximos passos sugeridos

- Indexar `memory/` e `workspace/development/` como fontes do MemPalace para deixar todo o conhecimento curado searchable.
- Atualizar `.claude/skills/dev-remember/SKILL.md` com link direto para este guia.

---

**Brief técnico completo:** `workspace/development/research/[C]research-mempalace-agent-guide-2026-06-16.md`
