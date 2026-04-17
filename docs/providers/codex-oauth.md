# OpenAI Codex (OAuth) — Provider Guide

> 🇧🇷 Português abaixo — [pular para versão PT-BR](#pt-br-guia-do-provider-openai-codex-oauth)

---

## 🇬🇧 English

### What is the Codex OAuth provider?

The `codex_auth` provider lets EvoNexus run agents on **GPT-5.x via the ChatGPT Codex backend** — using your regular ChatGPT login, no API key required. It routes through [OpenClaude](https://github.com/Gitlawb/openclaude) (v0.3.0+), which reads `~/.codex/auth.json` and exchanges the OAuth token automatically.

This is distinct from the plain `openai` provider, which uses an `OPENAI_API_KEY` and hits the standard chat completions endpoint.

### Prerequisites

| Tool | Install |
|------|---------|
| OpenClaude 0.3.0+ | `npm install -g @gitlawb/openclaude` |
| An active ChatGPT account | [chatgpt.com](https://chatgpt.com) |

The dashboard's Providers page also shows the current OpenClaude version and a Test button to confirm it's reachable.

### Setup

1. Open the dashboard at `http://localhost:8080` and go to **Providers**.
2. Find the **OpenAI Codex (OAuth)** card.
3. Click **Login** — a modal opens with two options:
   - **Browser OAuth** — opens `auth.openai.com` in your browser; after signing in, paste the callback URL back into the modal.
   - **Device Auth** — shows a code you type at `auth.openai.com/codex/device`. Some organizations disable this flow — if so, use Browser OAuth.
4. On success, the card shows an **OAuth** badge and the provider becomes active automatically.

Behind the scenes, the dashboard backend writes `~/.codex/auth.json` in the format OpenClaude expects:

```json
{
  "auth_mode": "Chatgpt",
  "tokens": {
    "access_token": "…",
    "refresh_token": "…",
    "id_token": "…",
    "account_id": "…"
  },
  "last_refresh": "2026-04-17T…Z"
}
```

### How sessions are spawned

When you start a terminal session or run an ADW routine with `codex_auth` active, both `dashboard/terminal-server/src/claude-bridge.js` and `ADWs/runner.py`:

1. Read `config/providers.json` and pick the `codex_auth` entry.
2. Inject **only** `CLAUDE_CODE_USE_OPENAI=1` and `OPENAI_MODEL=codexplan` (the default) into the child process.
3. Strip `OPENAI_API_KEY` from the environment — even if it's set globally — so OpenClaude does not fall back to API-key auth.
4. Inherit `CODEX_HOME` from the parent shell if set, letting advanced users point `~/.codex/` elsewhere.

OpenClaude sees `~/.codex/auth.json` and authenticates automatically. No token ever lands in `providers.json` or `.env`.

### Model aliases

OpenClaude's Codex mode uses **aliases**, not raw model names:

| Alias | Model | Use case |
|-------|-------|----------|
| `codexplan` | GPT-5.4 on Codex backend (high reasoning) | Default — complex agent work, long context |
| `codexspark` | GPT-5.3 Codex Spark (faster, cheaper) | Loops, quick iteration |

> ⚠️ **Setting `OPENAI_MODEL=gpt-5.4` explicitly breaks Codex OAuth** — it routes to the plain chat completions endpoint, which requires an API key, and falls back to failure. Always use an alias.

### Advanced overrides

These environment variables are on the allowlist (see `dashboard/backend/routes/providers.py::ALLOWED_ENV_VARS`). You can set them in the provider's `env_vars` via the Configure button:

| Variable | Purpose |
|----------|---------|
| `CODEX_AUTH_JSON_PATH` | Override the auth file location (default: `~/.codex/auth.json`) |
| `CODEX_API_KEY` | Raw token override — skips the auth.json read entirely |

### Troubleshooting

**`openclaude` not found when running as `evonexus` service user**
Setup v0.23.3+ installs OpenClaude for the service user under `~/.local`. If you upgraded from an older version, run:

```bash
su - evonexus -c 'npm install -g @gitlawb/openclaude --prefix ~/.local'
```

**Session dies immediately, logs show "authentication failed"**
Your token probably expired. OpenClaude refreshes automatically, but if that fails:

1. Dashboard → Providers → OpenAI Codex → **Logout**, then **Login** again.
2. Or manually: `rm ~/.codex/auth.json` and re-auth via dashboard.

**"Model not found" errors**
You likely have `OPENAI_MODEL=gpt-5.4` (or similar raw name) set globally. Remove it from `.env` and let the provider default (`codexplan`) kick in.

**Codex Auth works in dashboard but not in ADW routines**
Check that `config/providers.json` lists `codex_auth` as `active_provider` and that `ADWs/runner.py` is the current version — it reads `providers.json` fresh on every call.

### How this differs from `openai` (API key)

| | `openai` | `codex_auth` |
|--|--|--|
| Auth method | `OPENAI_API_KEY` env var | OAuth token in `~/.codex/auth.json` |
| Endpoint | `api.openai.com/v1/chat/completions` | Codex backend via `/responses` |
| Default model | `gpt-4.1` | `codexplan` |
| Cost | Pay per token | Covered by ChatGPT subscription |
| Token rotation | Manual | Automatic (OpenClaude handles refresh) |

---

## 🇧🇷 Guia do provider OpenAI Codex (OAuth) {#pt-br-guia-do-provider-openai-codex-oauth}

### O que é o provider Codex OAuth?

O provider `codex_auth` faz o EvoNexus rodar agentes no **GPT-5.x via backend Codex do ChatGPT** — usando seu login normal do ChatGPT, sem API key. A chamada passa pelo [OpenClaude](https://github.com/Gitlawb/openclaude) (v0.3.0+), que lê o `~/.codex/auth.json` e troca o token OAuth automaticamente.

É diferente do provider `openai` normal, que usa `OPENAI_API_KEY` e bate no endpoint padrão de chat completions.

### Pré-requisitos

| Ferramenta | Instalação |
|------------|------------|
| OpenClaude 0.3.0+ | `npm install -g @gitlawb/openclaude` |
| Conta ativa no ChatGPT | [chatgpt.com](https://chatgpt.com) |

A página Providers do dashboard mostra a versão atual do OpenClaude e tem um botão Test pra confirmar que está acessível.

### Setup

1. Abra o dashboard em `http://localhost:8080` e vá em **Providers**.
2. Encontre o card **OpenAI Codex (OAuth)**.
3. Clique em **Login** — um modal abre com duas opções:
   - **Browser OAuth** — abre `auth.openai.com` no navegador; depois de logar, cole a URL de callback de volta no modal.
   - **Device Auth** — mostra um código pra você digitar em `auth.openai.com/codex/device`. Algumas organizações desabilitam esse fluxo — se for o caso, use o Browser OAuth.
4. Dando certo, o card mostra a badge **OAuth** e o provider vira ativo automaticamente.

Por baixo, o backend do dashboard grava `~/.codex/auth.json` no formato que o OpenClaude espera:

```json
{
  "auth_mode": "Chatgpt",
  "tokens": {
    "access_token": "…",
    "refresh_token": "…",
    "id_token": "…",
    "account_id": "…"
  },
  "last_refresh": "2026-04-17T…Z"
}
```

### Como as sessões são abertas

Quando você inicia uma sessão de terminal ou roda uma rotina ADW com `codex_auth` ativo, tanto `dashboard/terminal-server/src/claude-bridge.js` quanto `ADWs/runner.py`:

1. Leem o `config/providers.json` e pegam a entrada `codex_auth`.
2. Injetam **só** `CLAUDE_CODE_USE_OPENAI=1` e `OPENAI_MODEL=codexplan` (o default) no processo filho.
3. Removem `OPENAI_API_KEY` do environment — mesmo que esteja setado globalmente — pra que o OpenClaude não caia pro fluxo de API key.
4. Herdam `CODEX_HOME` do shell pai se setado, deixando usuários avançados apontarem `~/.codex/` pra outro lugar.

O OpenClaude enxerga o `~/.codex/auth.json` e autentica sozinho. Nenhum token encosta no `providers.json` ou no `.env`.

### Aliases de modelo

O modo Codex do OpenClaude usa **aliases**, não nomes crus de modelo:

| Alias | Modelo | Quando usar |
|-------|--------|-------------|
| `codexplan` | GPT-5.4 no backend Codex (reasoning alto) | Default — trabalho complexo de agente, contexto longo |
| `codexspark` | GPT-5.3 Codex Spark (mais rápido, mais barato) | Loops, iteração rápida |

> ⚠️ **Setar `OPENAI_MODEL=gpt-5.4` explicitamente quebra o Codex OAuth** — o OpenClaude manda a chamada pro endpoint de chat completions puro, que exige API key, e dá erro. Sempre use um alias.

### Overrides avançados

Essas variáveis de ambiente estão no allowlist (ver `dashboard/backend/routes/providers.py::ALLOWED_ENV_VARS`). Dá pra setar via botão Configure do provider:

| Variável | Função |
|----------|--------|
| `CODEX_AUTH_JSON_PATH` | Override do arquivo de auth (default: `~/.codex/auth.json`) |
| `CODEX_API_KEY` | Token cru — pula a leitura do auth.json |

### Troubleshooting

**`openclaude` não encontrado ao rodar como service user `evonexus`**
Setup v0.23.3+ instala o OpenClaude pro service user em `~/.local`. Se você fez upgrade de uma versão antiga, rode:

```bash
su - evonexus -c 'npm install -g @gitlawb/openclaude --prefix ~/.local'
```

**Sessão morre na hora, logs mostram "authentication failed"**
Provavelmente seu token expirou. O OpenClaude faz refresh automático, mas se falhar:

1. Dashboard → Providers → OpenAI Codex → **Logout**, depois **Login** de novo.
2. Ou manualmente: `rm ~/.codex/auth.json` e re-autentique via dashboard.

**Erros de "Model not found"**
Você provavelmente tem `OPENAI_MODEL=gpt-5.4` (ou outro nome cru) setado globalmente. Tira do `.env` e deixa o default do provider (`codexplan`) entrar.

**Codex Auth funciona no dashboard mas não nas rotinas ADW**
Confirme que `config/providers.json` lista `codex_auth` como `active_provider` e que `ADWs/runner.py` está na versão atual — ele lê o `providers.json` fresco em cada chamada.

### Como isso difere do `openai` (API key)

| | `openai` | `codex_auth` |
|--|--|--|
| Método de auth | env var `OPENAI_API_KEY` | Token OAuth em `~/.codex/auth.json` |
| Endpoint | `api.openai.com/v1/chat/completions` | Backend Codex via `/responses` |
| Modelo default | `gpt-4.1` | `codexplan` |
| Custo | Por token | Incluso na assinatura ChatGPT |
| Rotação de token | Manual | Automática (OpenClaude cuida do refresh) |
