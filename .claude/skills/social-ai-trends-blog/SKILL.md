---
name: social-ai-trends-blog
description: >
  Pesquisa semanal de trending topics de IA no X (Twitter), gera uma fila de
  pautas virais para o blog Sistema Britto e alimenta a rotina diaria AI News:
  seleção por Sage, texto por Quill, revisão por Raven, humanização/SEO/link
  building por Mako, imagem, Ghost draft, aprovação humana, publicação e
  compartilhamento no LinkedIn. Use quando: "trending de IA", "pauta do blog",
  "AI News", "tópicos virais da semana", "o que está bombando em IA", ou nos
  cronjobs semanais/diários.
---

# social-ai-trends-blog — AI News semanal + fila diaria

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

## Pipeline semanal (segunda)

```
1. COLETA      → fetch_ai_trends.py (X API v2 recent search, 7 dias, sort=relevancy)
2. RANQUEAMENTO→ score = likes + 2·RT + 1.5·quote + 0.5·reply + 1.5·bookmark + 0.001·impressions
                 + clusterização por tema (regex) + filtro de ruído
3. SÍNTESE     → @mako-marketing monta 15 tópicos: título + ângulo + gancho de produto
4. REVISÃO     → @sage-strategy revisa viralidade, relevância e fit Sistema Britto
5. FILA        → salva a pauta semanal e a fila editorial em workspace/marketing/ai-news/
```

### Agentes

- **Coleta:** script `fetch_ai_trends.py` usando X API.
- **Curadoria:** `@sage-strategy` escolhe os viral topics mais relevantes para Sistema Britto.
- **Síntese editorial:** `@mako-marketing` transforma tendencias em fila de pautas.

## Pipeline diario (19:00)

```
1. Sage seleciona o proximo item da fila.
2. Quill escreve o blogpost AI News com fontes e estrutura.
3. Raven revisa factualidade, risco, promessa e lacunas.
4. Mako humaniza, aplica SEO/link building, gera imagem/thumbnail e cria Ghost draft.
5. Telegram envia pedido de aprovacao com link do draft.
6. Publicacao e compartilhamento no LinkedIn so acontecem depois do OK humano.
7. Ao gerar o draft, o item da fila passa para `drafted` com `approval_status: pending`.
```

### Regras de aprovacao

- Nunca publicar no Ghost sem aprovacao explicita do Felipe.
- Nunca compartilhar no LinkedIn sem o post do Ghost estar aprovado/publicado.
- Se `LINKEDIN_ACCESS_TOKEN` nao estiver configurado, registrar o bloqueio e nao tentar compartilhar.
- O draft diario deve ficar como `status: draft`.
- A mensagem de aprovacao precisa conter: titulo, promessa, link de preview/admin quando houver, caminho da imagem e pergunta objetiva de OK.

## Como rodar (manual)

```bash
# Semanal: coleta + curadoria + fila
python3 ADWs/routines/custom/ai_news_weekly_x_research.py

# Diario: consome proximo item, cria draft e pede aprovacao
python3 ADWs/routines/custom/ai_news_daily_draft.py
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
| `workspace/marketing/ai-news/[C]pauta-AAAA-WW.md` | Os 15 tópicos finais |
| `workspace/marketing/ai-news/queue.json` | Fila editorial diaria |
| `workspace/marketing/ai-news/drafts/` | Drafts, revisoes, imagens e aprovacoes |

## Cronjobs

- Segunda-feira 08:00: `AI News Weekly X Research`.
- Todos os dias 19:00: `AI News Daily Draft`.

Registro do cron: ver `config/routines.yaml`.

## Estrategia "post recompensa"

Manter como trilha paralela de crescimento:

1. Sage define tese, publico, oferta e criterio de lead qualificado.
2. Mako transforma em campanha comentavel com CTA de palavra-chave.
3. Pixel/Canvas geram criativo.
4. Raven revisa risco de promessa, clareza e friccao.
5. Felipe aprova antes de postar.

## Anti-padrões

- ❌ Publicar sem aprovação do Felipe.
- ❌ Confiar só na contagem bruta de virais (tier Basic é amostra limitada) —
  usar os **temas** como sinal e sintetizar pautas, não copiar tweets.
- ❌ Gancho forçado de produto onde não cabe — se o tema não conecta com
  Evolution, marcar como "sem gancho" em vez de inventar.
- ❌ Repetir pautas de semanas anteriores sem checar histórico.
