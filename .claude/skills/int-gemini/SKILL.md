---
name: int-gemini
description: Integração com a Google Gemini API (AI Studio) via GEMINI_API_KEY. Use para OFFLOAD de tarefas baratas/volumosas dos modelos Claude/Opus — classificação de transcripts, sumarização, 1º rascunho de conteúdo, e pesquisa web com Google Search grounding. Comandos ask/models/smoke, saída JSON estruturada. Aciona quando o usuário menciona Gemini, "rodar no Gemini", offload de tarefa barata, grounding/pesquisa web via Google, ou distribuir consumo entre modelos.
---

# int-gemini — Google Gemini API (AI Studio)

Camada B (MTA-only). Wrapper CLI stdlib-only para a Generative Language API, pensado
para **distribuir consumo** dos agentes: tirar do Claude/Opus as tarefas baratas e
volumosas e mandar pro Gemini free/low-cost.

## Setup

```bash
# .env (raiz do repo)
GEMINI_API_KEY=...        # AI Studio (https://aistudio.google.com/apikey). Conta MTA.
GEMINI_MODEL=gemini-2.5-flash   # opcional — default do `ask`
GEMINI_API_BASE=...             # opcional — override do endpoint
```

A chave vem do **AI Studio** (free tier). O **Google One / AI Premium** (app de chat)
**NÃO dá quota de API** — é assinatura do app gemini.google.com, canal separado.

## Comandos

```bash
P=.claude/skills/int-gemini/scripts/gemini_client.py

# Gerar resposta (default gemini-2.5-flash)
python3 $P ask "Classifique o sentimento: 'adorei o atendimento'"

# Escolher modelo + system instruction + forçar JSON do modelo
python3 $P ask "Extraia {nome, intencao} do texto: ..." \
  --model gemini-2.5-flash-lite --system "Você é um classificador. Responda só JSON." --json

# Pesquisa web com Google Search grounding (retorna sources[])
python3 $P ask "Quais as novidades do RD Station em 2026?" --grounding

# Override do guard de confidencialidade (uso CONSCIENTE — ver seção abaixo)
python3 $P ask "..." --allow-sensitive

# Fallback p/ flash se o modelo falhar (ex.: 503 do flash-lite)
python3 $P ask "Classifique ..." --model gemini-2.5-flash-lite --fallback

# System instruction de um arquivo (instruções longas reutilizáveis)
python3 $P ask "..." --system-file path/to/system.txt

python3 $P --version

# Qual modelo Gemini usar p/ um tipo de tarefa (helper de roteamento)
python3 $P route classify     # → gemini-2.5-flash-lite
python3 $P route summarize    # → gemini-2.5-flash

# Varre um texto pelo guard de confidencialidade (sem chamar a API)
python3 $P scan "honorários de contrato R$ 1.000, CPF 123.456.789-00"

# Listar modelos acessíveis pela chave
python3 $P models

# Health-check (sempre exit 0, JSON {overall, steps[], duration_ms})
python3 $P smoke
```

| Comando | O que faz | Exit codes |
|---|---|---|
| `ask <prompt>` | Gera resposta. Passa pelo guard de confidencialidade primeiro. | 0=ok, 1=erro API, **2=bloqueado pelo guard** |
| `route <tipo>` | Modelo recomendado p/ `classify/triage/extract/summarize/draft/translate/research/reason` | 0 |
| `scan <texto>` | Só o guard (sem rede) — `{safe, confidential_findings[], detected_types[]}` | 0 |
| `models` | Lista modelos `generateContent` da chave | 0 / 1 |
| `smoke` | Health-check 4 steps (env, generate, guard, models) | **sempre 0** |

**Todos os comandos** emitem JSON em stdout. Saída do `ask`: `{ok, model, text, grounded,
sources[], usage{prompt/candidates/total_tokens}, finish_reason}` — `model` é o modelo que
de fato respondeu (com `--fallback`, pode diferir do `--model` pedido).
Logging estruturado vai pra **stderr**; o JSON de resultado vai pra **stdout** — pipeável.

**Precedência de modelo:** `--model` (flag) > `GEMINI_MODEL` (env) > default `gemini-2.5-flash`.
O `route` é só um guia — não sobrepõe; o caller passa o resultado pro `--model`.

## Modelos (verificado ao vivo 2026-06-23 — 37 modelos `generateContent`)

| Modelo | Janela in | out | Uso recomendado |
|---|---|---|---|
| `gemini-2.5-flash` (default) | 1.048.576 | 65.536 | Sumarização, 1º rascunho, grounding |
| `gemini-2.5-flash-lite` | 1.048.576 | 65.536 | Classificação em volume (mais barato/rápido) |
| `gemini-2.5-pro` | 1.048.576 | 65.536 | Raciocínio (free tier removido abr/2026 → pago) |
| `gemini-3-flash-preview` / `gemini-3.5-flash` | 1.048.576 | 65.536 | Geração mais nova (preview) |

A chave MTA acessa também a família `gemini-3.x` (preview) e modelos de imagem/TTS.
Rode `models` para a lista viva.

## Quando usar Gemini vs Claude

| Tarefa | Modelo | Por quê |
|---|---|---|
| Classificação/triagem em volume (transcripts, leads, e-mails) | **Gemini Flash-Lite** | Barato, rápido, free tier cobre o volume |
| Sumarização de reunião (transcrição de reunião) / texto longo | **Gemini Flash** | Janela 1M tokens, custo baixo |
| 1º rascunho de conteúdo (depois Claude refina) | **Gemini Flash** | Tira o trabalho bruto do Opus |
| Pesquisa web factual | **Gemini Flash --grounding** | Google Search nativo + sources |
| Arquitetura, estratégia, código de produção, decisão de risco | **Claude/Opus** | Qualidade de raciocínio + contexto MTA |
| Qualquer dado **confidencial de cliente** (sem redação) | **Claude** (ou tier no-train) | Free tier do Gemini **treina** com os prompts; o guard é PARCIAL |

## Confidencialidade (CRÍTICO) — guard-rail PARCIAL, não garantia

O **free tier do Gemini usa prompts+respostas para treino do modelo**. Por isso o `ask`
roda um guard ANTES de chamar a API e **bloqueia por padrão** (exit 2) ao detectar dado
sensível. **Mas o guard é uma rede de segurança PARCIAL — não confie como garantia.**

**Pega de forma CONFIÁVEL (dado estruturado):** CPF, CNPJ, cartão, CEP, e-mail, valor em
R$, telefone (incl. nono dígito separado e fixo de 8), endereço com logradouro, PIX,
termos de contrato (cláusula/honorários/NDA), segredo (api_key/token/senha).

**Pega só em BEST-EFFORT (gera falso-negativo — NÃO confie):** nome de pessoa (só perto
de "lead/cliente/diretor/sócio…"), endereço sem logradouro nomeado, telefone falado por
extenso, primeiro nome isolado. Ex.: `"João Silva ligou ontem"` (nome solto) **passa**.
O corpus de falso-negativo em `tests/` documenta esses limites explicitamente.

**Por isso:** tarefas que carregam dado de cliente **por natureza** (resumo de transcrição de reunião,
ficha de lead, e-mail de cliente) → **redija/anonimize antes**, use tier no-train, OU
mantenha no Claude. **Não jogue o texto cru contra o guard esperando que ele proteja.**

- **Bloqueado → o que fazer:** redigir (substituir nome/telefone/endereço por placeholder),
  OU `--allow-sensitive` se você conscientemente avaliou que é seguro (ex.: o "R$" é
  genérico de marketing, não financeiro de cliente), OU manter no Claude.
- **Falso-positivo é de propósito:** o guard prefere bloquear (um ID de 10 dígitos vira
  "telefone") — o `--allow-sensitive` é o override consciente. Use `scan <texto>` para
  inspecionar sem chamar a API.

## Helper de roteamento (qual modelo)

`route <tipo>` centraliza a escolha do modelo Gemini quando o offload já foi decidido:

| Tipo de tarefa | Modelo | Por quê |
|---|---|---|
| `classify` / `triage` / `extract` | `gemini-2.5-flash-lite` | Volume — mais barato/rápido |
| `summarize` / `draft` / `translate` / `research` | `gemini-2.5-flash` | Janela 1M, custo baixo |
| `reason` | `gemini-2.5-pro` | Raciocínio (pago no free desde abr/2026) |
| (desconhecido) | `gemini-2.5-flash` | Fallback seguro |

> **Achado do piloto (2026-06-23):** o `gemini-2.5-flash-lite` pode devolver **HTTP 503
> "high demand"** transitório — tenha `gemini-2.5-flash` como fallback. Triagem em lote
> cabe num **único request** (24 tarefas → 1 chamada, ~6,6k tokens) — economiza RPD.

## Limites & confidencialidade — ver análise

Estudo de limites reais + matemática de volume + piloto recomendado em
`workspace/strategy/[C]gemini-offload-analise-2026-06-23.md`.

## Notas técnicas

- Stdlib only (urllib) — sem dependências. `.env` loader igual às outras skills int-*.
- Retry: 1x após backoff 20s em 429/5xx/timeout.
- Type-safe: `_request` sempre retorna `dict` (nunca bytes/str crua).
- `smoke` é gracioso sem a chave (FAIL no step `env`, exit 0) e valida o guard offline.
- **Testes:** `python3 -m pytest .claude/skills/int-gemini/tests/` — **105 testes**
  100% mockados (conftest bloqueia rede real em autouse; nenhum teste gasta quota).
  Inclui corpus de **falso-negativo documentado** do guard (limites explícitos).
- Piloto de referência (offload real, dry-run): `workspace/_scripts/_misc/gemini-pilot/`.
- **Auto-melhoria (2026-06-23):** `--fallback`, `--system-file`, `--version`, WARN no
  `route` desconhecido e doc de precedência vieram de uma auto-crítica rodada pela própria
  `int-gemini ask` (Gemini Flash). Rejeitadas conscientemente: invocação nativa (convenção
  MTA usa `python3 $P`) e `smoke` exit≠0 (contrato MTA: smoke é SEMPRE exit 0).
