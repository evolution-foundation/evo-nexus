# Agents Overview

Agents are the core of EvoNexus. Each agent is a specialized AI persona with its own domain, system prompt, skills, and persistent memory.

EvoNexus organizes agents in **two ortogonal layers**:

- **Business Layer (17 agents)** — operations, finance, community, marketing, HR, legal, product, data, sales, learning retention. Built and maintained by EvoNexus.
- **Engineering Layer (21 agents)** — software development, code review, testing, debugging, security, design, cycle orchestration, retrospective. 19 derived from [oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode) (MIT, by Yeachan Heo) + 2 native (Helm, Mirror). Follows a canonical 6-phase workflow. See [Engineering Layer](engineering-layer.md) for details.

The two layers are independent — business tasks route to business agents, engineering tasks to engineering agents — but cross-layer handoffs are common (e.g., `@nova` writes a PRD → `@apex-architect` does the architecture review → `@bolt-executor` implements → `@mirror-retro` captures lessons).

**Total: 38 agents** + custom agents you create.

## What Is an Agent?

An agent is a markdown file in `.claude/agents/` that contains:

- **Frontmatter** with metadata (name, description, model, color, memory scope)
- **System prompt** that defines the agent's identity, responsibilities, tone, and behavior

When invoked, Claude Code loads the agent's system prompt and operates within that persona for the duration of the task.

## How to Invoke an Agent

### Slash commands

Each agent has a corresponding command in `.claude/commands/`:

**Business Layer (17):**

```
/clawdia-assistant       — Ops hub: agenda, emails, tasks, decisions
/flux-finance            — Finance: Stripe, ERP, cash flow, reports
/atlas-project           — Projects: Linear, GitHub, sprints, milestones
/pulse-community         — Community: Discord, WhatsApp, sentiment, FAQ
/pixel-social-media      — Social media: content, calendar, analytics
/sage-strategy           — Strategy: OKRs, roadmap, competitive analysis
/nex-sales               — Sales: pipeline, proposals, qualification
/mentor-courses          — Courses: learning paths, modules
/lumen-learning          — Learning retention: spaced repetition, fact capture, quizzes
/kai-personal-assistant  — Personal: health, habits, routine
/oracle                  — Workspace knowledge: docs, how-to, configuration
/mako-marketing          — Marketing: campaigns, SEO, email, brand
/aria-hr                 — HR / People: recruiting, onboarding, performance
/zara-cs                 — Customer Success: triage, escalation, KB
/lex-legal               — Legal / Compliance: contracts, NDA, LGPD
/nova-product            — Product: specs, roadmaps, metrics, research
/dex-data                — Data / BI: analysis, SQL, dashboards
```

**Engineering Layer (19):**

```
/apex-architect      — Architect: design, debugging, tradeoffs (READ-ONLY)
/echo-analyst        — Analyst: discovery, gap analysis (READ-ONLY)
/compass-planner     — Planner: 3-6 step interview-driven plans
/raven-critic        — Critic: multi-perspective adversarial review (READ-ONLY)
/bolt-executor       — Executor: smallest viable diff implementation
/hawk-debugger       — Debugger: root cause, build errors
/lens-reviewer       — Code reviewer: 2-stage, OWASP, SOLID (READ-ONLY)
/zen-simplifier      — Code simplifier: refactor without behavior change
/vault-security      — Security: OWASP Top 10, secrets, deps (READ-ONLY)
/grid-tester         — Test engineer: TDD, pyramid, flaky diagnosis
/probe-qa            — QA tester: interactive tmux sessions
/oath-verifier       — Verifier: evidence-based completion (READ-ONLY)
/trail-tracer        — Tracer: causal investigation with hypotheses
/scout-explorer      — Explorer: parallel codebase search (READ-ONLY)
/flow-git            — Git master: atomic commits, safe rebase
/scroll-docs         — Document specialist: external SDK/API docs (READ-ONLY)
/quill-writer        — Writer: technical docs with verified examples
/canvas-designer     — Designer: production-grade UI/UX
/prism-scientist     — Scientist: formal statistical analysis
```

Usage in Claude Code:

```
/clawdia-assistant check my emails
/flux-finance what is the company's financial status?
/apex-architect why is the bot runtime hanging on reconnect?
/dev-autopilot build me a CRUD API for a bookstore inventory
```

### Auto-routing

You do not need to pick the right agent manually. Just describe what you need and Claude routes to the correct agent based on the `description` field in each agent's frontmatter.

Examples:

| You say | Agent activated |
|---------|----------------|
| "good morning" | Clawdia |
| "monthly closing" | Flux |
| "check github PRs" | Atlas |
| "community sentiment" | Pulse |
| "write a LinkedIn post" | Pixel |
| "should we prioritize X or Y?" | Sage |
| "prepare a proposal for client Z" | Nex |
| "create a course module" | Mentor |
| "how is my health progress?" | Kai |

The routing logic uses example interactions in each agent's `description` field to match user intent.

## Agent Memory

Every agent has persistent, file-based memory at:

```
.claude/agent-memory/<agent-name>/
```

Memory is organized by type:

| Type | Purpose |
|------|---------|
| `user` | Who the user is, their role, preferences |
| `feedback` | Corrections and confirmed approaches |
| `project` | Ongoing work, deadlines, decisions |
| `reference` | Pointers to external systems |

Each memory file uses frontmatter (`name`, `description`, `type`) and a `MEMORY.md` index file tracks all entries. Agents read memory at the start of each session and update it as they learn.

For semantic search over large volumes of content (code, docs, transcripts), use **MemPalace** — the local vector memory system. See [MemPalace Guide](mempalace-guide.md) for the complete reference on when and how to use it.

## Custom Agents

You can create your own agents with the `custom-` prefix. Custom agents are gitignored (personal to your workspace) and appear in the dashboard with a gray "custom" badge.

To create a custom agent, use the `create-agent` skill or see [Creating Agents](creating-agents.md).

```
.claude/agents/custom-devops.md      # Agent prompt
.claude/commands/custom-devops.md    # Slash command
.claude/agent-memory/custom-devops/  # Persistent memory
```

## Business Layer — 17 Core Agents

| Agent | Command | Domain | Color |
|-------|---------|--------|-------|
| [**Clawdia**](clawdia.md) | `/clawdia-assistant` | Ops: agenda, emails, tasks, meetings, decisions | cyan |
| [**Flux**](flux.md) | `/flux-finance` | Finance: Stripe, Omie, cash flow, monthly close | orange |
| [**Atlas**](atlas.md) | `/atlas-project` | Projects: Linear, GitHub, sprints, licensing | green |
| [**Pulse**](pulse.md) | `/pulse-community` | Community: Discord, WhatsApp, sentiment, FAQ | blue |
| [**Pixel**](pixel.md) | `/pixel-social-media` | Social: content, calendar, cross-platform analytics | yellow |
| [**Sage**](sage.md) | `/sage-strategy` | Strategy: OKRs, roadmap, competitive analysis | orange |
| [**Nex**](nex.md) | `/nex-sales` | Sales: pipeline, proposals, qualification | red |
| [**Mentor**](mentor.md) | `/mentor-courses` | Courses: learning paths, modules, academy | purple |
| [**Lumen**](lumen-learning.md) | `/lumen-learning` | Learning retention: spaced repetition (SM-2), fact capture, quizzes | yellow |
| [**Kai**](kai.md) | `/kai-personal-assistant` | Personal: health, habits, routine (isolated) | blue |
| [**Oracle**](oracle.md) | `/oracle` | Workspace knowledge: docs, how-to, configuration | amber |
| [**Mako**](mako.md) | `/mako-marketing` | Marketing: campaigns, SEO, email sequences, brand | orange |
| [**Aria**](aria.md) | `/aria-hr` | HR / People: recruiting, onboarding, performance | pink |
| [**Zara**](zara.md) | `/zara-cs` | Customer Success: triage, escalation, health scores | cyan |
| [**Lex**](lex.md) | `/lex-legal` | Legal / Compliance: contracts, NDA, LGPD, risk | purple |
| [**Nova**](nova.md) | `/nova-product` | Product: specs, roadmaps, metrics, research | blue |
| [**Dex**](dex.md) | `/dex-data` | Data / BI: analysis, SQL, dashboards, visualizations | yellow |

## Engineering Layer — 19 Agents

Software development specialists. Each agent has a specific role in the dev workflow: design, planning, implementation, review, testing, debugging, verification.

### Reasoning (opus)

| Agent | Command | Role |
|---|---|---|
| **Apex** | `/apex-architect` | Architect — architectural design, read-only debugging, tradeoffs |
| **Echo** | `/echo-analyst` | Analyst — discovery, requirements gaps, hidden assumptions |
| **Compass** | `/compass-planner` | Planner — tactical 3-6 step planning with interview |
| **Raven** | `/raven-critic` | Critic — challenges plans before execution, multi-perspective |
| **Lens** | `/lens-reviewer` | Code Reviewer — 2-stage review (spec + quality), OWASP, SOLID |
| **Zen** | `/zen-simplifier` | Code Simplifier — deslop, refactoring, clarity |
| **Vault** | `/vault-security` | Security Reviewer — OWASP Top 10, secrets, dependency audit |

### Execution (sonnet)

| Agent | Command | Role |
|---|---|---|
| **Bolt** | `/bolt-executor` | Executor — precise multi-file implementation |
| **Hawk** | `/hawk-debugger` | Debugger — root cause, regressions, stack traces |
| **Grid** | `/grid-tester` | Test Engineer — TDD, strategy pyramid, coverage |
| **Probe** | `/probe-qa` | QA Tester — interactive testing, flaky diagnosis |
| **Oath** | `/oath-verifier` | Verifier — evidence-based completion verification |
| **Trail** | `/trail-tracer` | Tracer — causal tracing, competing hypotheses |
| **Flow** | `/flow-git` | Git Master — atomic commits, rebase, history cleanup |
| **Scroll** | `/scroll-docs` | Document Specialist — external docs (SDKs, APIs) via web |
| **Canvas** | `/canvas-designer` | Designer — UI/UX for product (Evo AI CRM, dashboards) |
| **Prism** | `/prism-scientist` | Scientist — formal statistical analysis, hypothesis testing |

### Speed (haiku)

| Agent | Command | Role |
|---|---|---|
| **Scout** | `/scout-explorer` | Explorer — parallel codebase search (Glob/Grep) |
| **Quill** | `/quill-writer` | Writer — quick technical docs, README, comments |

See the dedicated [Engineering Layer](engineering-layer.md) page for pipelines, attribution, and detailed workflows.

### Agent Roles in Detail

**Clawdia** is the default agent and operational hub. Morning briefings, email triage, task management, meeting summaries, and end-of-day consolidation all run through Clawdia.

**Flux** acts as a virtual CFO. It queries Stripe for MRR/churn and Omie for payables/receivables, generates financial reports, and manages the monthly close process.

**Atlas** tracks development work across Linear and GitHub. Sprint status, PR reviews, issue tracking, and open source licensing telemetry are its domain.

**Pulse** monitors community health across Discord and WhatsApp. It generates daily/weekly/monthly pulse reports, tracks sentiment, identifies recurring questions, and maintains the FAQ.

**Pixel** manages social media across YouTube, Instagram, and LinkedIn. Content creation, calendar planning, cross-platform analytics, and engagement tracking.

**Sage** handles high-level strategy. OKR reviews, competitive analysis, strategy digests, and decision frameworks.

**Nex** manages the sales pipeline. Lead qualification, proposals, and commercial workflows.

**Mentor** handles the course platform (Evo Academy). Learning paths, module creation, and educational content.

**Kai** is the personal assistant with an isolated domain. Health tracking, habits, personal appointments, and routines. It does not handle professional matters.

**Oracle** is the workspace knowledge agent. It answers questions about how EvoNexus works — agents, skills, routines, integrations, dashboard, configuration, and architecture — by reading the actual documentation before responding. No RAG or vector DB needed.

**Mako** handles marketing. Campaign planning, content creation, brand reviews, SEO audits, email sequences, and performance reports.

**Aria** manages HR and People Operations. Recruiting pipeline, performance reviews, onboarding checklists, compensation analysis, and org planning.

**Zara** handles customer success. Ticket triage, escalation packaging, customer research, draft responses, and knowledge base articles.

**Lex** manages legal and compliance. Contract review, NDA triage, LGPD compliance checks, risk assessment, and legal briefs.

**Nova** handles product management. Feature specs/PRDs, metrics reviews, roadmap updates, product brainstorming, and stakeholder updates.

**Dex** is the data and BI agent. Data analysis, SQL queries, interactive dashboards, statistical analysis, and data validation.

**Lumen** is the knowledge retention agent. It captures atomic facts from pasted content, runs SM-2 spaced repetition review sessions, generates retrieval-practice quizzes, and reports retention metrics per deck. Use Lumen to lock in what you read — Mentor creates learning content, Lumen makes it stick.

## Preloaded Skills

Each agent has a `skills` field in its frontmatter that pre-loads domain-specific skills into the agent's context at startup. This means the agent doesn't need to discover or load skills during execution — they're immediately available.

```yaml
---
name: "flux-finance"
skills:
  - fin-daily-pulse
  - fin-weekly-report
  - int-stripe
  - int-omie
---
```

## Agent Teams (Experimental)

For consolidation tasks that span multiple domains (strategy digest, dashboard, weekly review), Agent Teams can run domain-specific agents in parallel. Each teammate collects data from their domain simultaneously, then the lead agent synthesizes results.

Agent Teams are opt-in and cost ~3-5x more tokens. Run manually:

```bash
make team-strategy    # @sage leads, @atlas + @flux + @pulse + @pixel as teammates
make team-dashboard   # @clawdia leads, @atlas + @flux + @pulse + @dex as teammates
make team-weekly      # @clawdia leads, @atlas + @flux + @pulse as teammates
```

See [Routines Overview](../routines/overview.md) for details on Agent Teams.
