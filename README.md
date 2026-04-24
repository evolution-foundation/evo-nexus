<p align="center">
  <a href="https://evolutionfoundation.com.br">
    <img src="public/cover.webp" alt="Evolution Foundation" height="60"/>
  </a>
</p>

<p align="center">
  <img src="public/cover.svg" alt="EvoNexus" width="100%"/>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#web-dashboard">Dashboard</a> •
  <a href="docs/getting-started.md">Docs</a> •
  <a href="CHANGELOG.md">Changelog</a> •
  <a href="CONTRIBUTING.md">Contributing</a> •
  <a href="LICENSE">MIT License</a>
</p>

---

> **Disclaimer:** EvoNexus is an independent, **unofficial open-source project**. It is **not affiliated with, endorsed by, or sponsored by Anthropic**. "Claude" and "Claude Code" are trademarks of Anthropic, PBC. This project integrates with Claude Code as a third-party tool and requires users to provide their own installation and credentials.

---

## What It Is

EvoNexus is an open source, **unofficial** multi-agent operating layer built around the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI protocol — but **not locked to any single LLM provider**. It runs natively on Anthropic's `claude` CLI by default, and can transparently switch to OpenAI, Google Gemini, OpenRouter (200+ models), AWS Bedrock, Google Vertex AI, or Codex Auth via [OpenClaude](https://www.npmjs.com/package/@gitlawb/openclaude). Same agents, same skills, same workflows — your choice of backend.

It turns a single CLI installation into a team of **38 specialized agents** organized in two ortogonal layers — **17 business agents** (operations, finance, community, marketing, HR, legal, product, data, learning retention) and **21 engineering agents** (architecture, planning, code review, testing, debugging, security, design, cycle orchestration, retrospective — 19 derived from [oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode), MIT, by Yeachan Heo + 2 native: Helm and Mirror). The engineering layer follows a canonical 6-phase workflow documented in `.claude/rules/dev-phases.md`. Each agent has its own domain, skills, persistent memory, and automated routines. The result is a production system that runs daily operations for a founder/CEO **and** supports software development workflows: from morning briefings to financial reports, community monitoring, social analytics, end-of-day consolidation, plus architectural reviews, code audits, and verified implementation pipelines.

**This is not a chatbot.** It is a real operating layer that runs routines, generates HTML reports, syncs meetings, triages emails, monitors community health, tracks financial metrics, and consolidates everything into a unified dashboard — all automated.

### Why EvoNexus?

- **Markdown-first agents** — agents are `.md` files with system prompts, not code. No SDK, no plugin runtime, no compile step. Add an agent by dropping a file in `.claude/agents/`
- **Skills as instructions** — reusable capabilities are markdown too. 190+ skills covering finance, community, social, engineering, data, legal, HR, ops, product, CS
- **Multi-provider by design** — default runs on Anthropic's native `claude` CLI, but can switch to OpenRouter, OpenAI, Gemini, AWS Bedrock, Google Vertex, or Codex Auth via [OpenClaude](https://www.npmjs.com/package/@gitlawb/openclaude) without touching a line of code. Your keys, your model choice, no vendor lock-in
- **MCP integrations** — first-class support for Google Calendar, Gmail, GitHub, Linear, Telegram, Canva, Notion, and more via the Model Context Protocol
- **Slash commands** — `/clawdia`, `/flux`, `/pulse`, `/apex` invoke agents directly from the terminal
- **Persistent memory** — `CLAUDE.md` + per-agent memory survives across sessions
- **CLI-first, local-only** — runs anywhere the Claude CLI (or OpenClaude) runs. Your data never leaves your infrastructure

---

## Key Features

- **Multi-Provider** — runs on Anthropic (native `claude`) or any of 6 alternate backends via [OpenClaude](https://www.npmjs.com/package/@gitlawb/openclaude): OpenRouter (200+ models), OpenAI, Google Gemini, Codex Auth, AWS Bedrock, Google Vertex AI. Switch providers from the dashboard, no code changes
- **17 Core Agents + Custom** — Ops, Finance, Projects, Community, Social, Strategy, Sales, Courses, Learning Retention, Personal, Knowledge, Marketing, HR, Customer Success, Legal, Product, Data — plus user-created `custom-*` agents (gitignored)
- **150+ Skills + Custom** — organized by domain prefix (`social-`, `fin-`, `int-`, `prod-`, `mkt-`, `gog-`, `obs-`, `discord-`, `pulse-`, `sage-`, `hr-`, `legal-`, `ops-`, `cs-`, `data-`, `pm-`). Includes `prod-activation-plan` — the canonical skill for producing phased activation plans (index + folder-per-phase + file-per-item) used by Oracle
- **7 Core + 20 Custom Routines** — daily, weekly, and monthly ADWs managed by a scheduler (core routines ship with the repo; custom routines are user-created and gitignored)
- **Web Dashboard** — React + Flask app with auth, roles, web terminal, service management
- **19 Integrations** — Google Calendar, Gmail, Linear, GitHub, Discord, Telegram, Stripe, Omie, Bling, Asaas, Fathom, Todoist, YouTube, Instagram, LinkedIn, Evolution API, Evolution Go, Evo CRM, and more
- **2 core + custom HTML report templates** — dark-themed dashboards for every domain
- **Persistent Memory** — two-tier system (CLAUDE.md + memory/) with LLM Wiki pattern: ingest propagation, weekly lint, centralized index, and operation log
- **Knowledge Base** — optional semantic search via [MemPalace](https://github.com/milla-jovovich/mempalace) (local ChromaDB vectors, one-click install)
- **Full Observability** — JSONL logs, execution metrics, cost tracking per routine
- **Heartbeats (Phase 1)** — proactive agents that wake on a schedule, run a 9-step protocol, and decide whether to act
- **Goal Cascade (Phase 1)** — Mission → Project → Goal → Task hierarchy. Link any routine/heartbeat/ticket to a goal; agents receive the chain as prompt context
- **Tickets (Phase 1)** — persistent conversation/work threads with atomic checkout (`UPDATE WHERE locked_at IS NULL`), 6-state workflow, mention-based wake triggers, global `/issues` page

---

## Screenshots

<p align="center">
  <img src="public/print-overview.webp" alt="Overview" width="49%" />
  <img src="public/print-chat.webp" alt="Agent Chat" width="49%" />
</p>
<p align="center">
  <img src="public/print-agents.webp" alt="Agents" width="49%" />
  <img src="public/print-integrations.webp" alt="Integrations" width="49%" />
</p>
<p align="center">
  <img src="public/print-costs.webp" alt="Costs" width="49%" />
</p>

---

## Integrations

Connect your existing tools via MCP servers, API clients, or OAuth:

| Integration | Type | What it does |
|---|---|---|
| **Google Calendar** | MCP | Read/create/update events, find free time |
| **Gmail** | MCP | Read, draft, send emails, triage inbox |
| **GitHub** | MCP + CLI | PRs, issues, releases, code search |
| **Linear** | MCP | Issues, sprints, project tracking |
| **Discord** | API | Community messages, channels, moderation |
| **Telegram** | MCP + Bot | Notifications, messages, commands |
| **Stripe** | API | Charges, subscriptions, MRR, customers |
| **Omie** | API | ERP — clients, invoices, financials, stock |
| **Bling** | API (OAuth2) | Brazilian ERP — products, orders, NF-e, contacts, stock (auto-refresh via `make bling-auth`) |
| **Asaas** | API | Brazilian payments — Pix, boleto, credit card, subscriptions, marketplace split |
| **Fathom** | API | Meeting recordings, transcripts, summaries |
| **Todoist** | CLI | Task management, priorities, projects |
| **YouTube** | OAuth | Channel stats, videos, engagement |
| **Instagram** | OAuth | Profile, posts, engagement, insights |
| **LinkedIn** | OAuth | Profile, org stats |
| **Canva** | MCP | Design and presentations |
| **Notion** | MCP | Knowledge base, pages, databases |
| **Obsidian** | CLI | Vault management, notes, search |
| **Evolution API** | API | WhatsApp messaging — instances, messages, chats, groups |
| **Evolution Go** | API | WhatsApp messaging (Go implementation) |
| **Evo CRM** | API | AI-powered CRM — contacts, conversations, pipelines |

Social media accounts (YouTube, Instagram, LinkedIn) are connected via OAuth through the dashboard.

---

## Prerequisites

**For the Docker install** (easiest — the container ships everything):

| Tool | Required | Install |
|------|----------|---------|
| **Docker Engine 24+** | Yes | [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) |

That's it. The image includes Claude Code, Python, Node, uv, gh, and every other runtime dependency.

**For the CLI / from-source install** (the dev flow):

| Tool | Required | Install |
|------|----------|---------|
| **Claude Code** | Yes | `npm install -g @anthropic-ai/claude-code` ([docs](https://claude.ai/download)) |
| **Python 3.11+** | Yes | via [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node.js 18+** | Yes | [nodejs.org](https://nodejs.org) |
| **uv** | Yes | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

The setup wizard (`make setup`) checks for all prerequisites before proceeding.

---

## AI Providers

EvoNexus runs on **Anthropic's Claude** by default — no extra config needed. For everything else (OpenAI, Gemini, Bedrock, OpenRouter, Vertex AI, Codex Auth), it switches to [OpenClaude](https://www.npmjs.com/package/@gitlawb/openclaude), a drop-in binary that speaks the Claude CLI protocol but dispatches to the provider of your choice via environment variables.

| Provider | Binary | Key env vars |
|---|---|---|
| **Anthropic** (default) | `claude` | native auth |
| **OpenRouter** (200+ models) | `openclaude` | `CLAUDE_CODE_USE_OPENAI`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` |
| **OpenAI** | `openclaude` | `CLAUDE_CODE_USE_OPENAI`, `OPENAI_API_KEY`, `OPENAI_MODEL` |
| **Google Gemini** | `openclaude` | `CLAUDE_CODE_USE_GEMINI`, `GEMINI_API_KEY`, `GEMINI_MODEL` |
| **Codex Auth** (OpenAI via OAuth) | `openclaude` | `CLAUDE_CODE_USE_OPENAI`, `OPENAI_MODEL=codexplan` (auth via `~/.codex/auth.json`, no API key) — see [docs/providers/codex-oauth.md](docs/providers/codex-oauth.md) |
| **AWS Bedrock** | `openclaude` | `CLAUDE_CODE_USE_BEDROCK`, `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK` |
| **Google Vertex AI** | `openclaude` | `CLAUDE_CODE_USE_VERTEX`, `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION` |

Install OpenClaude once globally if you plan to use any non-Anthropic provider:

```bash
npm install -g @gitlawb/openclaude
```

The setup wizard asks which provider you want during `make setup`, and you can switch at any time from the **Providers** page in the dashboard (no restart needed — the terminal-server and ADW runner re-read `config/providers.json` on every session spawn). See [docs/dashboard/providers.md](docs/dashboard/providers.md) for the full reference.

---

## Quick Start

> **Starting out?** After installing, open Claude Code and call **`/oracle`**. It's the official entry point of EvoNexus: runs the initial setup, interviews you about your business, shows what the toolkit can automate for you, and delivers a **phased activation plan** — an index file at the top + one folder per phase + one file per item, each with suggested agent team, dependencies, and pending decisions. The plan is materialized by the `prod-activation-plan` skill (workspace canonical pattern) so you never have to guess the next step.

### Method 1 — Docker (no setup, runs anywhere)

The fastest way. Pulls official multi-arch images (`linux/amd64` + `linux/arm64`) from Docker Hub — works natively on Apple Silicon, AWS Graviton, Oracle ARM, Raspberry Pi, and x86_64 servers.

```bash
curl -O https://raw.githubusercontent.com/EvolutionAPI/evo-nexus/main/docker-compose.hub.yml
docker compose -f docker-compose.hub.yml up -d
open http://localhost:8080
```

The setup wizard loads on first boot. Paste your Anthropic / OpenAI / Codex key and you're done — no extra tooling on your host. Full guide: [docs/guides/docker-install.md](docs/guides/docker-install.md).

### Method 2 — One command (for CLI / terminal workflow)

```bash
npx @evoapi/evo-nexus
```

This downloads and runs the interactive setup wizard automatically. Requires the CLI prerequisites above (Claude Code, Python, Node, uv).

### Method 3 — Manual clone (for developers / contributors)

```bash
git clone --depth 1 https://github.com/EvolutionAPI/evo-nexus.git
cd evo-nexus

# Interactive setup wizard — checks prerequisites, creates config files
make setup
```

### What the wizard does

The wizard:
- Checks that Claude Code, uv, Node.js are installed
- Asks for your name, company, timezone, language
- **Asks which AI provider to use** (Anthropic by default; offers OpenRouter, OpenAI, Gemini, Bedrock, Vertex, Codex Auth as alternatives via OpenClaude)
- Lets you pick which agents and integrations to enable
- Generates `config/workspace.yaml`, `config/providers.json`, `.env`, `CLAUDE.md`, and workspace folders
- Builds the dashboard frontend

### 2. Configure API keys

```bash
nano .env
```

Add keys for the integrations you enabled. Common ones:

```env
# Discord (for community monitoring)
DISCORD_BOT_TOKEN=your_token
DISCORD_GUILD_ID=your_guild_id

# Stripe (for financial routines)
STRIPE_SECRET_KEY=sk_live_...

# Telegram (for notifications)
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

# Social media (connect via dashboard Integrations page)
SOCIAL_YOUTUBE_1_API_KEY=...
SOCIAL_INSTAGRAM_1_ACCESS_TOKEN=...
```

See `.env.example` for all available variables.

### 3. Start the dashboard

```bash
make dashboard-app
```

Open **http://localhost:8080** — the first run shows a setup wizard where you:
- Configure your workspace (name, company, agents, integrations)
- Create your admin account
- License is activated automatically in the background

### 4. Start automated routines

```bash
make scheduler
```

Runs all enabled routines on schedule (morning briefing, email triage, community pulse, financial reports, etc). Configure schedules in `config/routines.yaml`.

### 5. Use Claude Code — start with `/oracle`

Open Claude Code in the project directory — it reads `CLAUDE.md` automatically. The **first thing you should do** is call `/oracle`:

```bash
/oracle
```

Oracle will:
1. Detect workspace state (fresh install vs. configured)
2. Interview you about your business, pain points and priorities
3. Map workspace capabilities to your pains (via @scout-explorer)
4. Present the "wow" report — what can be automated for you
5. Delegate the plan structure to @compass-planner
6. **Materialize the plan using the `prod-activation-plan` skill** — creates an index file + phase folders + item files, each with suggested agent team, dependencies and pending decisions
7. Hand you the plan with **3 autonomy paths**: Guided / Autonomous / Delegated

The plan lives at `workspace/development/plans/[C]{plan-name}-{date}.md` (index) + `fase-*/` folders. You can discuss or implement any item in isolation by pointing to its file.

### Available slash commands

```bash
# Use slash commands to invoke agents
/clawdia       # Ops — agenda, emails, tasks, decisions
/flux          # Finance — Stripe, ERP, cash flow, reports
/atlas         # Projects — Linear, GitHub, sprints, milestones
/pulse         # Community — Discord, WhatsApp, sentiment, FAQ
/pixel         # Social media — content, calendar, analytics
/sage          # Strategy — OKRs, roadmap, competitive analysis
/nex           # Sales — pipeline, proposals, qualification
/mentor        # Courses — learning paths, modules
/lumen-learning # Learning retention — spaced repetition, fact capture, quizzes
/kai           # Personal — health, habits, routine
/oracle        # Entry point — onboarding, business discovery, implementation plan
/mako          # Marketing — campaigns, content, SEO, brand
/aria          # HR — recruiting, onboarding, performance
/zara          # Customer Success — triage, escalation, health
/lex           # Legal — contracts, compliance, NDA, risk
/nova          # Product — specs, roadmaps, metrics, research
/dex           # Data / BI — analysis, SQL, dashboards

# Or just describe what you need — Claude routes to the right agent
```

---

## Web Dashboard

A full web UI at `http://localhost:8080`:

| Page | What it does |
|------|-------------|
| **Overview** | Unified dashboard with metrics from all agents |
| **Systems** | Register and manage apps/services (Docker, external URLs) |
| **Reports** | Browse HTML reports generated by routines |
| **Agents** | View agent definitions and system prompts |
| **Routines** | Metrics per routine (runs, success rate, cost) + manual run |
| **Tasks** | Schedule one-off actions (skill, prompt, script) at a specific date/time |
| **Skills** | Browse all 190+ skills by category (~125 business + 25 dev-*) |
| **Templates** | Preview HTML report templates |
| **Services** | Start/stop scheduler, channels (Telegram, Discord, iMessage) with live logs |
| **Memory** | Browse agent and global memory files |
| **Knowledge** | Semantic search via [MemPalace](https://github.com/milla-jovovich/mempalace) — index code, docs, and knowledge |
| **Integrations** | Status of all connected services + OAuth setup |
| **Chat** | Embedded Claude Code terminal (xterm.js + WebSocket) |
| **Users** | User management with roles (admin, operator, viewer) |
| **Roles** | Custom roles with granular permission matrix |
| **Audit Log** | Full audit trail of all actions |
| **Config** | View CLAUDE.md, routines config, and workspace settings |

```bash
make dashboard-app   # Start Flask + React on :8080
```

---

## Architecture

```
User (human)
    |
    v
Claude Code (orchestrator)
    |
    +-- Clawdia   — ops: agenda, emails, tasks, decisions, dashboard
    +-- Flux      — finance: Stripe, ERP, MRR, cash flow, monthly close
    +-- Atlas     — projects: Linear, GitHub, milestones, sprints
    +-- Pulse     — community: Discord, WhatsApp, sentiment, FAQ
    +-- Pixel     — social: content, calendar, cross-platform analytics
    +-- Sage      — strategy: OKRs, roadmap, prioritization, scenarios
    +-- Nex       — sales: pipeline, proposals, qualification
    +-- Mentor    — courses: learning paths, modules
    +-- Kai       — personal: health, habits, routine (isolated domain)
    +-- Oracle    — entry point: onboarding, business discovery, implementation plan
    +-- Mako      — marketing: campaigns, content, SEO, brand
    +-- Aria      — HR: recruiting, onboarding, performance
    +-- Zara      — customer success: triage, escalation, health
    +-- Lex       — legal: contracts, compliance, NDA, risk
    +-- Nova      — product: specs, roadmaps, metrics, research
    +-- Dex       — data/BI: analysis, SQL, dashboards
```

Each agent has:
- System prompt in `.claude/agents/`
- Slash command in `.claude/commands/`
- Persistent memory in `.claude/agent-memory/`
- Related skills in `.claude/skills/`

---

## Workspace Structure

```
evo-nexus/
├── .claude/
│   ├── agents/          — 38 agent system prompts
│   ├── commands/        — 38 slash commands
│   ├── skills/          — 190+ skills by prefix (~125 business + 25 dev-*) (+ custom)
│   └── templates/html/  — 2 core + custom HTML templates
├── ADWs/
│   ├── runner.py        — execution engine (logs + metrics + notifications)
│   ├── routines/         — 7 core routine scripts (shipped with repo)
│   └── routines/custom/  — 20 custom routines (user-created, gitignored)
├── dashboard/
│   ├── backend/         — Flask + SQLAlchemy + WebSocket
│   └── frontend/        — React + TypeScript + Tailwind
├── social-auth/         — OAuth multi-account app
├── config/              — workspace.yaml, routines.yaml
├── workspace/           — user data folders (gitignored content)
├── setup.py             — CLI setup wizard
├── scheduler.py         — automated routine scheduler
├── Makefile             — 44+ make targets
└── CLAUDE.template.md   — template for generated CLAUDE.md
```

Workspace folders (`workspace/daily-logs/`, `workspace/projects/`, etc.) are created by setup — content is gitignored, only structure is tracked.

---

## Commands

```bash
# Setup & Dashboard
make setup           # Interactive setup wizard
make dashboard-app   # Start web dashboard on :8080


# Routines
make scheduler       # Start automated routine scheduler
make morning         # Run morning briefing
make triage          # Run email triage
make community       # Run community pulse
make fin-pulse       # Run financial pulse
make eod             # Run end-of-day consolidation
make memory-lint     # Run memory health check (contradictions, stale data, gaps)
make weekly          # Run weekly review

# Backup & Restore
make backup          # Export workspace data to local ZIP
make backup-s3       # Export + upload to S3
make restore FILE=<path>  # Restore from backup ZIP

# Observability
make logs            # Show latest JSONL log entries
make metrics         # Show per-routine metrics (runs, cost, tokens)
make help            # List all available commands
```

---

## Documentation

| Doc | Description |
|-----|-------------|
| [Getting Started](docs/getting-started.md) | Full setup guide |
| [Architecture](docs/architecture.md) | How agents, skills, and routines work |
| [ROUTINES.md](ROUTINES.md) | Complete routine documentation |
| [ROADMAP.md](ROADMAP.md) | Improvement plan and backlog |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| `.claude/skills/CLAUDE.md` | Full skill index |

---

## Credits & Acknowledgments

EvoNexus stands on the shoulders of great open source projects:

- **[oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode)** by **Yeachan Heo** (MIT) — 19 of the 21 engineering agents (including `apex-architect`, `bolt-executor`, `lens-reviewer`) and all `dev-*` skills are derived from OMC v4.11.4. The 2 native agents (`helm-conductor`, `mirror-retro`) and the 6-phase workflow (`.claude/rules/dev-phases.md`) are EvoNexus-native additions. See [NOTICE.md](NOTICE.md) for the full list of derived components and modifications.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
Third-party attributions are documented in [NOTICE.md](NOTICE.md).

---

<p align="center">
  An unofficial community toolkit for <a href="https://docs.anthropic.com/en/docs/claude-code">Claude Code</a>
  <br/>
  <sub>Built by <a href="https://evolutionfoundation.com.br">Evolution Foundation</a> — not affiliated with Anthropic</sub>
</p>
