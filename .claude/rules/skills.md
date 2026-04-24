# Skills (175+ skills, business + engineering layers)

Organized by prefix — see `.claude/skills/CLAUDE.md` for the full index.

The workspace has skills in two layers:
- **Business layer** — `social-`, `fin-`, `legal-`, `mkt-`, `hr-`, etc. (operations, finance, community, marketing)
- **Engineering layer** — `dev-*` (software development, derived from oh-my-claudecode — see [NOTICE.md](../../NOTICE.md))

| Prefix | Category | Count |
|---------|-----------|-----|
| `social-` | Social media (posts, threads, carousels, analytics, strategy) | 17 |
| `dev-` | Engineering Layer (autopilot, plan, ralplan, deep-interview, deep-dive, external-context, trace, verify, ultraqa, visual-verdict, ai-slop-cleaner, sciomc, team, ccg, ralph, mcp-setup, deepinit, project-session-manager, configure-notifications, release, cancel, remember, ask, learner, skillify) | 25 |
| `int-` | Integrations (Fathom, Todoist, Stripe, Omie, Bling, Asaas, Discord, Telegram, Linear, GitHub, YouTube, Instagram, LinkedIn, WhatsApp, Licensing) | 15 |
| `fin-` | Finance (statements, journal, reconciliation, SOX, pulse, close) | 11 |
| `prod-` | Productivity (morning, eod, review, memory, dashboard, trends, licensing, activation-plan) | 10 |
| `ops-` | Operations (capacity plan, status report, change request, process doc, runbook, optimization, vendor review, risk assessment, compliance) | 9 |
| `legal-` | Legal / Compliance (contract review, compliance check, NDA triage, brief, response, risk assessment, meeting briefing, signature, vendor check) | 9 |
| `hr-` | HR / People (recruiting, performance review, onboarding, comp analysis, draft offer, interview prep, org planning, people report, policy lookup) | 9 |
| `mkt-` | Marketing (content, campaigns, SEO, email sequences, competitive) | 8 |
| `data-` | Data / BI (analyze, dashboard, write query, explore, create viz, statistical analysis, validate) | 7 |
| `gog-` | Google (Gmail, Calendar, Tasks, followups) | 6 |
| `pm-` | Product Management (write spec, metrics review, roadmap update, brainstorming, stakeholder update, synthesize research) | 6 |
| `cs-` | Customer Success (ticket triage, escalation, customer research, draft response, KB article) | 5 |
| `obs-` | Obsidian (CLI, markdown, bases, canvas, defuddle) | 5 |
| `discord-` | Discord (messages, channels, manage, create) | 5 |
| `pulse-` | Community (daily, weekly, monthly, FAQ sync) | 4 |
| `sage-` | Strategy (OKR review, strategy digest, competitive analysis) | 3 |
| `create-` | Workspace management (create-routine, create-agent, create-command, create-heartbeat, create-goal, create-ticket, manage-heartbeats) | 7 |
| `plugin-` | Plugins (install, list, uninstall, update, marketplace, health) | 6 |

> **Note:** `evo-*` skills (Evo Method) have been moved to the separate [EVO-METHOD](https://github.com/EvolutionAPI/EVO-METHOD) project.

## Phase 1 skills (Heartbeats + Goals + Tickets)

- **`create-heartbeat`** — create a proactive agent (9-step protocol) via interactive wizard
- **`create-goal`** — create Mission / Project / Goal in the 4-level cascade
- **`create-ticket`** — create a persistent work thread with assignee + priority
- **`manage-heartbeats`** — list, enable/disable, manually trigger, review runs

See `.claude/rules/heartbeats.md`, `.claude/rules/goals.md`, `.claude/rules/tickets.md`.

## How to Use

- Use skills with the correct prefix for each domain.
- See `.claude/skills/CLAUDE.md` for the full index with triggers and descriptions.
- Heavy skills (reports, analytics) may use `context: fork` to isolate context.
