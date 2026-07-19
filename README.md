<p align="center">
  <a href="https://evolutionfoundation.com.br">
    <img src="public/cover.webp" alt="Evolution Foundation" height="60"/>
  </a>
</p>

<p align="center">
  <img src="public/cover.svg" alt="EvoNexus" width="100%"/>
</p>

<h1 align="center">Omni-Nexus</h1>

<p align="center">
  Distribuição turbinada do <a href="https://github.com/evolution-foundation/evo-nexus">EvoNexus</a> pronta para VPS —
  com gateway de IA <a href="https://github.com/diegosouzapw/OmniRoute">OmniRoute</a> embutido na stack,
  seletor de providers e bot do Telegram multi-provider.
</p>

<p align="center">
  Uma camada de upgrade mantida por <a href="https://sistemabritto.com.br">Sistema Britto</a> sobre o EvoNexus da Evolution Foundation.
</p>

<p align="center">
  <a href="https://github.com/evolution-foundation/evo-nexus"><img src="https://img.shields.io/badge/upstream-evolution--foundation%2Fevo--nexus-00ffa7" alt="Upstream" /></a>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0" /></a>
  <a href="https://sistemabritto.com.br"><img src="https://img.shields.io/badge/by-sistemabritto.com.br-white" alt="Sistema Britto" /></a>
</p>

<p align="center">
  <a href="https://sistemabritto.com.br">Sistema Britto</a> &middot;
  <a href="#como-funciona-passo-a-passo-guia-sem-jargão">Como funciona (guia sem jargão)</a> &middot;
  <a href="https://github.com/evolution-foundation/evo-nexus">Projeto original</a> &middot;
  <a href="#deploy-completo-na-vps-passo-a-passo">Deploy na VPS</a> &middot;
  <a href="#omniroute--o-gateway-de-ia-da-stack">OmniRoute</a> &middot;
  <a href="#bot-do-telegram-multi-provider">Telegram</a> &middot;
  <a href="#créditos--agradecimentos">Créditos</a>
</p>

---

> **Disclaimer:** assim como o EvoNexus original, este é um projeto open source **não oficial**, **não afiliado, endossado ou patrocinado pela Anthropic**. "Claude" e "Claude Code" são marcas da Anthropic, PBC. O projeto integra o Claude Code como ferramenta de terceiros e exige que você forneça sua própria instalação e credenciais.

---

## O que é este fork

O [EvoNexus](https://github.com/evolution-foundation/evo-nexus) é uma camada operacional multi-agente construída sobre o CLI do Claude Code: **38 agentes especializados** (17 de negócio + 21 de engenharia), 190+ skills, rotinas agendadas, heartbeats, tickets, goals e um dashboard web completo. Toda essa base vem do projeto original da [Evolution Foundation](https://evolutionfoundation.com.br) — leia o [README upstream](https://github.com/evolution-foundation/evo-nexus#readme) para conhecer a plataforma em profundidade.

O **Omni-Nexus** é a camada de upgrade do [Sistema Britto](https://sistemabritto.com.br) em cima disso, com um objetivo claro: **rodar o EvoNexus inteiro numa VPS com Docker Swarm, sem depender de nenhum gateway de IA externo e sem exigir login claude.ai** — usando o provider que você quiser, inclusive vários ao mesmo tempo com fallback automático.

> Se você não é técnico e só quer entender **o que esse sistema faz pela sua empresa e como usá-lo no dia a dia**, vá direto para [Como funciona, passo a passo (guia sem jargão)](#como-funciona-passo-a-passo-guia-sem-jargão). O restante deste README é a documentação técnica do deploy e da infraestrutura.

### O upgrade em resumo

| Camada | O que foi adicionado |
|---|---|
| **OmniRoute na stack** | Gateway de IA self-hosted ([OmniRoute](https://github.com/diegosouzapw/OmniRoute), 237+ providers) como serviço do Swarm, com DNS interno, auth nativa e volume persistente |
| **Seletor de providers** | Provider `omnirouter` no dashboard + roteamento OpenClaude no terminal/chat, Codex OAuth via device auth, NVIDIA NIM, resolução de chaves por provider |
| **Telegram multi-provider** | Bot em modo `provider`: responde pelo provider ativo, troca de provider no chat com `/provider`, áudio (Whisper/Groq), imagens, leitura de URLs e memória por conversa — sem login claude.ai |
| **Pipeline de deploy VPS** | GitHub Actions → imagens no seu Docker Hub → stack de exemplo para Portainer/Traefik com volumes persistentes e backups SQLite consistentes |
| **Hardening** | Dezenas de correções de produção: precedência de chaves por provider, auto-updater do CLI travado, trust/permissions como root em container, recuperação de EIO no terminal, allowlist do Telegram re-seedada a cada boot |

---

## Como funciona, passo a passo (guia sem jargão)

Esqueça "gateway", "Swarm" e "provider" por um momento. Esta seção explica **o que o sistema faz** e **como você usa isso no seu trabalho**, sem pressupor conhecimento técnico.

### A ideia central

Você não fica configurando ferramenta nem escrevendo prompt o dia inteiro. Você diz **o que quer alcançar**, o sistema quebra isso em passos concretos, um time de agentes de IA especializados executa, e você só aparece pra **aprovar ou corrigir o rumo** — pelo dashboard ou pelo Telegram, do celular.

O ciclo é sempre o mesmo, em 4 níveis, cada um mais concreto que o anterior:

```
Missão            "o que eu quero pra minha empresa" (ex.: faturar R$1M até dez/2026)
  └─ Projeto       uma frente de trabalho (ex.: "Evo AI", "Lançamento do curso X")
       └─ Meta      um número que prova progresso (ex.: "100 clientes pagantes", "vender 200 vagas")
            └─ Ticket   uma tarefa real, do tamanho de uma tarde (ex.: "escrever 5 posts pro Instagram")
```

Você só precisa criar o topo (a Missão). **A partir daí a IA sugere o resto** — os Projetos, as Metas de cada projeto, e a quebra de cada Meta em Tickets — e cada sugestão só se torna real depois que você aprova ou rejeita, geralmente com um toque no Telegram. Nada é criado por trás das suas costas.

### O passo a passo na prática

1. **Você cria uma Missão** — a página `/goals` no dashboard, ou simplesmente descreve o objetivo pra IA. Ex.: "Quero R$1M de faturamento até dezembro."
2. **A IA sugere Projetos** — em minutos, você recebe no Telegram algo como: *"Sugiro 3 projetos pra essa missão: Evo AI, Evento X e Academia Y. Aprovar?"* Um toque e os projetos existem.
3. **A IA sugere Metas por Projeto** — pra cada projeto aprovado, chega outra sugestão: *"Pro projeto Evo AI, sugiro as metas: 100 clientes pagantes até 30/06, lançar a v2 da cobrança."* Aprovar de novo.
4. **A IA quebra cada Meta em Tickets** — tarefas do tamanho certo pra alguém (humano ou agente) executar essa semana, já com prioridade e responsável sugeridos.
5. **Os agentes trabalham os Tickets** — cada Ticket tem um agente-dono (ex.: Pixel pra conteúdo de redes sociais, Nex pra propostas comerciais, Flux pra financeiro). Eles avançam o trabalho sozinhos entre as reuniões — é o mecanismo de **heartbeat**: o agente "acorda" de tempos em tempos, olha a fila de tickets dele, decide se há algo a fazer, e age.
6. **O progresso sobe sozinho** — quando um Ticket é resolvido, a Meta correspondente avança automaticamente; quando todas as Metas de um Projeto terminam, o Projeto se marca como concluído. Você acompanha em `/goals` sem precisar atualizar planilha nenhuma.
7. **Você só entra pra decidir, não pra operar** — cada rung acima (Missão→Projeto, Projeto→Meta, Meta→Ticket) para numa aprovação. Publicar conteúdo real nas redes também para numa aprovação antes de ir ao ar. A IA propõe; você decide.

### Onde você vê tudo isso

| Página | Pra que serve |
|---|---|
| **Projetos** | Suas frentes de trabalho ativas, criar novo projeto |
| **Metas** | A árvore Missão → Projeto → Meta, com % de progresso |
| **Kanban** | Os Tickets — o trabalho real, dia a dia |
| **Agentes** | Seu "time": 38 especialistas (financeiro, comercial, social media, jurídico, dados, etc.) |
| **Habilidades** | O que cada agente sabe fazer (190+ skills prontas) |
| **Heartbeats** | Quais agentes estão em modo proativo e com que frequência acordam |
| **Rotinas** | Relatórios e ações automáticas em horário fixo (briefing de manhã, fechamento do dia, etc.) |
| **Gatilhos** | Eventos que acordam um agente na hora (ex.: alguém te mencionou num ticket) |
| **Atividades** | Histórico de tudo que aconteceu — auditoria |
| **Modelos** | Templates reutilizáveis (relatórios, documentos) |

### Um exemplo concreto — gerar clientes pelas redes sociais

Isso conecta direto com o objetivo de negócio de atrair clientes, seguidores, receita e autoridade pelo conteúdo:

1. Você cria a Meta "5.000 seguidores no Instagram até setembro" dentro de um Projeto de marketing.
2. A IA quebra em Tickets: calendário de conteúdo, 10 posts, 3 reels, 1 sequência de e-mail de nutrição.
3. O agente **Pixel** (social media) pega os tickets de conteúdo e produz os posts.
4. Antes de qualquer coisa ir ao ar, cai uma aprovação no seu Telegram com o texto e a mídia exatos — você aprova, rejeita ou pede ajuste.
5. Aprovado, a publicação sai de verdade nas redes (via integração com o Postiz) — sem fabricar sucesso: o sistema só marca como publicado depois de confirmar de fato na plataforma.
6. Seguidores/leads que isso gera alimentam de volta a Meta e, no fim da cadeia, a Missão de faturamento.

**O ganho pra sua rotina:** você troca "lembrar de postar, escrever o texto, editar a imagem, publicar, acompanhar" por "aprovar um Telegram por dia". O sistema é a memória e a mão de obra; você é a decisão.

---

## OmniRoute — o gateway de IA da stack

O upgrade mais importante deste fork: o [OmniRoute](https://github.com/diegosouzapw/OmniRoute) (MIT, criado por [diegosouzapw](https://github.com/diegosouzapw)) roda **dentro da sua stack Swarm** como o serviço opcional `omniroute`, e o EvoNexus fala com ele pela rede interna.

**Por que isso importa:**

- **Fim da dependência externa** — se um gateway público cai (503), seu bot e seus heartbeats caem junto. Self-hosted, o único ponto de falha é a sua VPS.
- **237+ providers com fallback automático** — configure OpenAI, Anthropic, Gemini, DeepSeek, Groq, NVIDIA e o que mais quiser no dashboard do OmniRoute; com `OPENAI_MODEL=auto` ele roteia pro melhor disponível e cai pro próximo se um falhar.
- **Codex OAuth embutido** — conecte sua conta ChatGPT Plus/Pro no OmniRoute e use a cota do Codex como um provider comum.
- **Compressão de tokens** (RTK/Caveman) — reduz o custo de contexto em 15–95% dependendo do conteúdo.
- **Latência mínima** — o EvoNexus acessa `http://omniroute:20128/v1` via alias DNS do Swarm, sem sair pra internet e sem passar pelo Traefik.

**Como fica a arquitetura:**

```
Telegram / Dashboard / Heartbeats / Rotinas
        |
        v
EvoNexus (provider ativo: omnirouter)
        |
        v  http://omniroute:20128/v1  (rede interna do Swarm)
OmniRoute (self-hosted)
        |
        +-- Codex OAuth (ChatGPT Plus)      [priority 1]
        +-- NVIDIA NIM                       [fallback]
        +-- OpenRouter / Gemini / DeepSeek…  [fallback]
```

**Segurança:** o dashboard do OmniRoute usa a **auth nativa** (login + sessão JWT). Não coloque basic-auth do Traefik na frente — as chamadas internas do dashboard (SSE/WS/API) usam header `Authorization` próprio e entram em loop de 401. O `REQUIRE_API_KEY=true` garante que a API `/v1` só responde com chave válida.

**Configuração assistida por agente:** [PROMPT-OMNIROUTE-CONFIG.md](PROMPT-OMNIROUTE-CONFIG.md) — prompt pronto pra colar num Claude Code (ou agente similar) que audita e otimiza o seu OmniRoute pela management API: keys nomeadas com telemetria de custo, compressão com auto-trigger, refresh de quota e reativação de modelos `:free`. Extraído de uma configuração real em produção.

---

## Seletor de providers

A página **Providers** do dashboard ganhou o provider **OMNIROUTER** (qualquer endpoint OpenAI-compatível com URL, chave e modelo customizados), somando-se aos existentes (Anthropic nativo, OpenRouter, OpenAI, Gemini, NVIDIA NIM, Codex Auth, Bedrock, Vertex).

Regras de resolução de chave que este fork corrigiu e agora documenta (leia antes de debugar um 401):

1. **A chave do próprio provider em `config/providers.json` sempre vence** — é ela que a página Providers grava.
2. As chaves do `.env` são **fallback**, usadas só quando o provider não tem chave própria.
3. `NVIDIA_API_KEY` do ambiente só é enviada para endpoints `*.nvidia.com` — nunca vaza para outros gateways.

O terminal e o chat do dashboard usam o CLI [OpenClaude](https://www.npmjs.com/package/@gitlawb/openclaude) para providers não-Anthropic, com ambiente limpo por sessão, `--fallback-model` automático e auto-update do CLI desativado em produção (um self-update no meio da sessão matava o processo).

---

## Bot do Telegram multi-provider

No EvoNexus original, o canal do Telegram usa o modo nativo do Claude Code (channels) — que **exige login claude.ai dentro do container** e não funciona com providers OpenAI-compatíveis. Este fork adiciona o **modo `provider`** (`TELEGRAM_MODE=provider`, padrão na stack): um runtime próprio que responde pelo provider ativo do dashboard.

O que o bot faz:

| Recurso | Como |
|---|---|
| Responder pelo provider ativo | Chat Completions no provider configurado (OmniRoute, NVIDIA, OpenRouter, Codex…) |
| **Trocar de provider no chat** | `/provider omnirouter` · `/provider status` · `/provider default` (volta ao global) |
| Sessão nova | `/new` (limpa a memória local da conversa) |
| Áudio → texto | Transcrição via Whisper na API da Groq (`/groq set <key>` para configurar) |
| Imagens | Descreve e responde sobre fotos enviadas |
| URLs | Baixa e resume links colados na conversa |
| Memória por chat | Histórico local por conversa, com identificação de quem falou |
| Fallback | Se o provider primário falha, percorre a cadeia `fallback_providers` do providers.json |

Benefício direto: o bot **sobrevive a redeploys sem re-login** (nada de sessão claude.ai pra expirar) e você escolhe o custo por conversa — manda o dia a dia pro modelo barato e troca pro modelo forte com um comando.

---

## Deploy completo na VPS (passo a passo)

### Pré-requisitos

- VPS com **Docker Swarm** inicializado (`docker swarm init`)
- **Traefik** rodando e conectado à rede externa `network_public` (entrypoint TLS `websecure`, cert resolver `letsencryptresolver`)
- **Portainer** (recomendado) ou acesso SSH para `docker stack deploy`
- Dois subdomínios apontando pra VPS (A record): um pro EvoNexus (ex.: `nexus.seudominio.com.br`) e um pro dashboard do OmniRoute (ex.: `omni.seudominio.com.br`)

### 1. Publique as imagens no seu Docker Hub

O workflow [`.github/workflows/docker-publish-britto.yml`](.github/workflows/docker-publish-britto.yml) builda as duas imagens Swarm (`evo-nexus-runtime` e `evo-nexus-dashboard`) e publica **no seu namespace** do Docker Hub. Faça fork deste repositório e configure os secrets em *Settings → Secrets and variables → Actions*:

| Secret | Valor |
|---|---|
| `DOCKERHUB_USERNAME` | seu usuário do Docker Hub (vira o namespace das imagens) |
| `DOCKERHUB_TOKEN` | Access Token (Docker Hub → Account Settings → Security) |

Qualquer push na branch de deploy (ou tag `vX.Y.Z`, ou disparo manual) publica `:latest` e `:sha-xxxx`. Build típico: ~2 min com cache.

### 2. Suba a stack no Portainer

Use a [`evonexus-vps.stack.example.yml`](evonexus-vps.stack.example.yml) como base (Portainer → Stacks → Add stack → Web editor). Ela sobe 4 serviços:

| Serviço | O que é |
|---|---|
| `evonexus_dashboard` | Flask + React + terminal web + heartbeats (exposto via Traefik) |
| `evonexus_scheduler` | Rotinas agendadas (ADWs) |
| `evonexus_telegram` | Bot do Telegram em modo provider |
| `omniroute` | Gateway de IA (opcional, mas recomendado) |

Preencha as variáveis da stack (aba *Environment variables* do Portainer):

| Variável | Como gerar |
|---|---|
| `EVONEXUS_DOMAIN` | seu domínio (ex.: `nexus.seudominio.com.br`) |
| `DASHBOARD_API_TOKEN` | `openssl rand -base64 32` |
| `OMNIROUTE_DOMAIN` | domínio do dashboard do OmniRoute (ex.: `omni.seudominio.com.br`) |
| `OMNIROUTE_INITIAL_PASSWORD` | senha de login do dashboard do OmniRoute |
| `OMNIROUTE_JWT_SECRET` | `openssl rand -base64 48` |
| `OMNIROUTE_API_KEY_SECRET` | `openssl rand -hex 32` |
| `OMNIROUTE_STORAGE_KEY` | `openssl rand -hex 32` — **guarde bem: cifra o SQLite do OmniRoute; perder = perder as configs** |
| `SMTP_*` | opcionais (notificações por email) |

A stack **não contém nenhuma credencial de propósito** — todos os tokens de integrações (Google, Stripe, Linear…) são configurados depois, pela UI.

### 3. Configure o OmniRoute

1. Acesse `https://omni.seudominio.com.br` e faça login com a `OMNIROUTE_INITIAL_PASSWORD`.
2. Na aba de **providers**, conecte o que você usa: Codex OAuth (ChatGPT Plus), NVIDIA, Gemini, DeepSeek, etc. A **ordem de prioridade** define o roteamento do `auto`.
3. Na aba **Endpoints**, gere uma **API key** (`sk-...`) para o EvoNexus.

> ⚠️ As keys vivem no SQLite do volume `omniroute_data`. Se você zerar o volume, **todas as keys morrem** — gere uma nova e atualize no EvoNexus. E pra zerar o volume no Swarm: `docker volume rm` falha com "volume in use" enquanto containers parados de tasks antigas existirem; remova-os antes com `docker ps -a --filter volume=omniroute_data -q | xargs docker rm -f`.

### 4. Plugue o OmniRoute como provider do EvoNexus

Acesse `https://nexus.seudominio.com.br` → **Providers** → **OMNIROUTER**:

| Campo | Valor |
|---|---|
| Base URL | `http://omniroute:20128/v1` (DNS interno do Swarm — não use a URL pública) |
| API Key | a key gerada no passo 3 |
| Model | `auto` (deixa o OmniRoute rotear e fazer fallback) |

Marque como provider ativo. Pronto: dashboard, terminal, heartbeats, rotinas e Telegram passam a responder pelo OmniRoute.

### 5. Telegram (opcional)

1. Crie um bot no [@BotFather](https://t.me/BotFather) e pegue o token.
2. No dashboard → **Integrations**, salve `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` (seu chat id).
3. O serviço `evonexus_telegram` já sobe em `TELEGRAM_MODE=provider`. Mande um `ping` — deve responder pelo provider ativo.

> ⚠️ Cada deploy precisa do **seu próprio bot/token** — dois pollers no mesmo token brigam (HTTP 409) e um rouba as mensagens do outro.

### 6. Atualizações

Push na branch de deploy → GitHub Actions publica as imagens novas → na VPS:

```bash
docker service update --force --image SEU_USUARIO/evo-nexus-dashboard:latest evonexus_evonexus_dashboard
docker service update --force --image SEU_USUARIO/evo-nexus-runtime:latest  evonexus_evonexus_telegram
docker service update --force --image SEU_USUARIO/evo-nexus-runtime:latest  evonexus_evonexus_scheduler
```

### Troubleshooting rápido

| Sintoma | Causa provável |
|---|---|
| `401 Unauthorized: chave API inválida/expirada` | Key do provider errada **no providers.json** (a página Providers grava lá; o `.env` é só fallback) — ou key do OmniRoute morta por reset de volume |
| Bot responde `All providers failed` | Provider ativo sem chave válida; teste `/provider status` no chat |
| Terminal morre com exit 1 no meio da sessão | Auto-update do CLI (já travado com `DISABLE_AUTOUPDATER=1` nesta versão) |
| `workspace has not been trusted` como root | Entrypoints desta versão re-seedam o trust e exportam `IS_SANDBOX=1` a cada boot — confira se está na imagem atualizada |
| Dashboard do OmniRoute em loop de 401 | Basic-auth do Traefik na frente — remova; a auth é nativa |

---

## O que vem do upstream (e continua aqui)

Tudo do EvoNexus original está preservado: os 38 agentes, as 190+ skills, rotinas/scheduler, heartbeats (protocolo de 9 passos), goals (cascata Mission → Project → Goal → Ticket, com sugestão automática por IA em cada degrau, aprovada pelo humano), tickets com checkout atômico, memória persistente em duas camadas, knowledge base semântica, dashboard completo com auditoria e gestão de usuários, e as 19+ integrações (Google, Linear, GitHub, Discord, Stripe, Omie, Bling, Asaas, Fathom, Todoist…).

Documentação da plataforma: [README original](https://github.com/evolution-foundation/evo-nexus#readme) · [docs.evolutionfoundation.com.br](https://docs.evolutionfoundation.com.br) · [docs/getting-started.md](docs/getting-started.md) · [docs/architecture.md](docs/architecture.md) · [ROUTINES.md](ROUTINES.md) · [CHANGELOG.md](CHANGELOG.md)

---

## Créditos & Agradecimentos

Este fork existe porque outros construíram coisas excelentes antes:

- **[EvoNexus](https://github.com/evolution-foundation/evo-nexus)** pela **[Evolution Foundation](https://evolutionfoundation.com.br)** — a plataforma inteira: agentes, skills, rotinas, heartbeats, goals, tickets, dashboard e integrações. Este repositório é um fork derivado; todo o mérito da base é deles. Site: [evolutionfoundation.com.br](https://evolutionfoundation.com.br) · Suporte: suporte@evofoundation.com.br
- **[OmniRoute](https://github.com/diegosouzapw/OmniRoute)** por **[Diego Souza](https://github.com/diegosouzapw)** (MIT) — o gateway de IA self-hosted que esta distribuição embute na stack.
- **[oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode)** por **Yeachan Heo** (MIT) — 19 dos 21 agentes de engenharia e as skills `dev-*` derivam do OMC (herdado do upstream). Detalhes em [NOTICE.md](NOTICE.md).
- **[OpenClaude](https://www.npmjs.com/package/@gitlawb/openclaude)** — o CLI que permite rodar o protocolo do Claude Code em providers alternativos.

A camada de upgrade (OmniRoute na stack, seletor de providers, Telegram multi-provider, pipeline VPS e hardening) é mantida por **[Sistema Britto](https://sistemabritto.com.br)**.

---

## Licença

Este fork mantém integralmente a licença do EvoNexus original: **Apache License 2.0 com condições adicionais de proteção de marca** — preservação de LOGO/copyright nos componentes de frontend e requisito de notificação de uso. Veja [LICENSE](LICENSE) para o texto completo.

Em conformidade com essas condições, esta distribuição **não remove nem modifica** o LOGO e as informações de copyright do EvoNexus no console e nas aplicações. Para questões de licenciamento do EvoNexus, contate **suporte@evofoundation.com.br**.

## Marcas

"Evolution Foundation", "Evolution" e "EvoNexus" são marcas da Evolution Foundation — veja [TRADEMARKS.md](TRADEMARKS.md). "Omni-Nexus" nomeia apenas esta distribuição derivada e não é afiliado à Evolution Foundation além da relação de fork. Atribuições de terceiros: [NOTICE](NOTICE) e [NOTICE.md](NOTICE.md).

---

<p align="center">
  Um toolkit comunitário não oficial para o <a href="https://docs.anthropic.com/en/docs/claude-code">Claude Code</a>
  <br/>
  Base por <a href="https://evolutionfoundation.com.br">Evolution Foundation</a> · Upgrade por <a href="https://sistemabritto.com.br">Sistema Britto</a> · © 2026
  <br/>
  <sub>Não afiliado à Anthropic</sub>
</p>
