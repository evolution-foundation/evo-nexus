---
name: social-ai-trends-blog
description: >
  Pesquisa semanal de trending topics de IA no X (Twitter) e gera 15 tópicos
  virais para o blog Sistema Britto, cada um com gancho para os produtos
  Evolution. Pipeline: coleta (X API) → ranqueamento por viralidade → síntese
  editorial (Mako) → revisão de alinhamento (Sage) → aprovação humana (Felipe).
  Use quando: "trending de IA", "pauta do blog", "tópicos virais da semana",
  "o que está bombando em IA", ou no cronjob semanal (segunda 08:00).
---

# social-ai-trends-blog — Pauta semanal de IA para o blog

Transforma o hype real do X em pauta editorial acionável para o blog
(`blog.sistemabritto.com.br`, via `custom-int-ghost`), sempre puxando o gancho
para os produtos Evolution.

## Produtos para ancorar o gancho

| Produto | Ângulo de gancho |
|---|---|
| **Evolution API** | API open source de WhatsApp — dono da própria infra, sem lock-in |
| **Evo AI** | CRM + agentes de IA |
| **Evo CRM** | Gestão de relacionamento |
| **EvoGo** | Evolution Go |
| **Evo Academy** | Cursos — gancho para temas "aprenda IA" |
| **Evolution Summit** | Evento — gancho para tendências/futuro |

## Pipeline (5 fases + cron)

```
1. COLETA      → fetch_ai_trends.py (X API v2 recent search, 7 dias, sort=relevancy)
2. RANQUEAMENTO→ score = likes + 2·RT + 1.5·quote + 0.5·reply + 1.5·bookmark + 0.001·impressions
                 + clusterização por tema (regex) + filtro de ruído
3. SÍNTESE     → @mako-marketing monta 15 tópicos: título + ângulo + gancho de produto
4. REVISÃO     → @sage-strategy revisa alinhamento estratégico, originalidade e gancho
5. APROVAÇÃO   → Felipe avalia a lista; só publica/agenda após feedback explícito
```

### Agentes

- **Executor:** `@mako-marketing` (Mako) — síntese editorial e ganchos
- **Supervisor:** `@sage-strategy` (Sage) — revisão de alinhamento e qualidade
- **Aprovador:** Felipe (humano) — nada vai pro ar sem o OK

## Como rodar (manual)

```bash
# 1. Coleta + ranqueamento (gera trends_raw.json)
python3 .claude/skills/social-ai-trends-blog/fetch_ai_trends.py --days 7 --per-query 100

# 2. Mako lê trends_raw.json e escreve [C]pauta-AAAA-WW.md (15 tópicos)
# 3. Sage revisa e anota; ajustes aplicados
# 4. Entrega a lista ao Felipe no Telegram/dashboard para aprovação
```

## Requisitos

- `SOCIAL_TWITTER_1_BEARER_TOKEN` no `.env` — tier **Basic** ou superior
  (recent search com janela de 7 dias + `sort_order=relevancy`).
- Limite: janela máxima de 7 dias no recent search. Para histórico maior,
  precisaria do endpoint full-archive (tier Pro/Enterprise).

## Saídas

| Arquivo | Conteúdo |
|---|---|
| `trends_raw.json` | Dados brutos: temas ranqueados + top tweets com métricas |
| `[C]pauta-AAAA-WW.md` | Os 15 tópicos finais (gerado pelo Mako, revisado pelo Sage) |

## Cronjob semanal

Segunda-feira 08:00 (America/Sao_Paulo). Roda a coleta + síntese + revisão e
notifica o Felipe no Telegram com os 15 tópicos para aprovação. **Não publica
automaticamente** — aprovação humana é obrigatória.

Registro do cron: ver `config/routines.yaml` (entrada `ai-trends-blog`) ou
agendar via `/schedule`.

## Anti-padrões

- ❌ Publicar sem aprovação do Felipe.
- ❌ Confiar só na contagem bruta de virais (tier Basic é amostra limitada) —
  usar os **temas** como sinal e sintetizar pautas, não copiar tweets.
- ❌ Gancho forçado de produto onde não cabe — se o tema não conecta com
  Evolution, marcar como "sem gancho" em vez de inventar.
- ❌ Repetir pautas de semanas anteriores sem checar histórico.
