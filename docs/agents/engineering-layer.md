# Engineering Layer

> **Attribution:** the Engineering Layer is derived from [oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode) (MIT, by **Yeachan Heo**). EvoNexus imports the agents and adapts them to the workspace conventions (themed names, EvoNexus standard pattern, `workspace/development/` paths). See [NOTICE.md](https://github.com/EvolutionAPI/evo-nexus/blob/main/NOTICE.md) at the repo root for the full list of derived components and modifications.

> **Native additions (v0.13.0):** `helm-conductor` and `mirror-retro` are native EvoNexus agents, not derived from oh-my-claudecode. Helm orchestrates the engineering cycle and routes tasks to phase owners; Mirror runs blameless retrospectives at the end of features and sprints.

## What It Is

The Engineering Layer is a complete software development team built into EvoNexus. **21 specialized agents** (19 derived + 2 native) + **25 `dev-*` skills** + **15 templates** + a **canonical 6-phase workflow** ([`dev-phases.md`](https://github.com/EvolutionAPI/evo-nexus/blob/main/.claude/rules/dev-phases.md)) that cover the full dev lifecycle: discovery, planning, solutioning, build, verify, retro.

It exists as a layer ortogonal to the Business Layer (Clawdia, Flux, Atlas, etc.). Business agents handle operations (emails, finance, community); engineering agents handle code (architecture, bugs, reviews, tests). Cross-layer handoffs are common — e.g., Nova writes a PRD → Apex reviews the architecture → Bolt implements → Lens reviews → Oath verifies → Mirror captures lessons.

## When to Use

- You write code on top of EvoNexus
- You want a structured dev workflow with separation of concerns (planner ≠ executor ≠ reviewer ≠ verifier)
- You want code review, security audits, and verification as first-class agents
- You want autonomous end-to-end execution from a brief to working code (`dev-autopilot`)

If you only need EvoNexus for ops/business work, you can ignore the Engineering Layer entirely — it doesn't get in the way.

## The 21 Agents

### Reasoning (opus / sonnet) — 8 agents

These agents do deep cognitive work: design, analysis, planning, critique, reflection. Higher token cost, higher quality output.

| Agent | Slash | Role | READ-ONLY? |
|---|---|---|---|
| **Apex** | `/apex-architect` | Architect — architectural design, debugging diagnosis, tradeoffs with file:line citations | ✅ |
| **Echo** | `/echo-analyst` | Analyst — discovery, gap analysis, hidden assumptions, missing acceptance criteria | ✅ |
| **Compass** | `/compass-planner` | Planner — interview-driven 3-6 step work plans + PRD production with explicit ADRs | |
| **Raven** | `/raven-critic` | Critic — multi-perspective adversarial review with pre-commitment predictions | ✅ |
| **Lens** | `/lens-reviewer` | Code Reviewer — 2-stage review (spec compliance + quality), OWASP, SOLID, severity ratings | ✅ |
| **Zen** | `/zen-simplifier` | Code Simplifier — deslop, reduce nesting, eliminate redundancy without changing behavior | |
| **Vault** | `/vault-security` | Security Reviewer — OWASP Top 10, secrets scan, dependency audit, prioritized remediation | ✅ |
| **Mirror** ⭐ | `/mirror-retro` | Retrospective — blameless lessons learned, reads the full feature folder, proposes memory updates | |

### Execution (sonnet) — 11 agents

These agents do focused implementation work: writing code, running tests, investigating bugs, producing artifacts, sequencing work.

| Agent | Slash | Role |
|---|---|---|
| **Bolt** | `/bolt-executor` | Executor — precise multi-file implementation with smallest viable diff |
| **Hawk** | `/hawk-debugger` | Debugger — root cause analysis, build error fixing, 3-failure circuit breaker |
| **Grid** | `/grid-tester` | Test Engineer — TDD discipline, test pyramid (70/20/10), flaky test diagnosis |
| **Probe** | `/probe-qa` | QA Tester — interactive tmux sessions, real commands, real output, always cleans up |
| **Oath** | `/oath-verifier` | Verifier — evidence-based completion checks, never trusts "should work" |
| **Trail** | `/trail-tracer` | Tracer — causal investigation with competing hypotheses, ranks by evidence strength |
| **Flow** | `/flow-git` | Git Master — atomic commits, style detection, safe rebasing |
| **Scroll** | `/scroll-docs` | Document Specialist — external SDK/API docs lookup with citations |
| **Canvas** | `/canvas-designer` | Designer — production-grade UI/UX, framework-idiomatic, distinctive aesthetic |
| **Prism** | `/prism-scientist` | Scientist — formal statistical analysis with CI, effect size, p-value |
| **Helm** ⭐ | `/helm-conductor` | Conductor — cycle orchestration, sequencing, routing to phase owners, sprint planning |

⭐ = native EvoNexus agent (not derived from oh-my-claudecode)

### Speed (haiku) — 2 agents

Fast, parallel, low-token agents for high-volume work.

| Agent | Slash | Role |
|---|---|---|
| **Scout** | `/scout-explorer` | Explorer — parallel codebase search (Glob/Grep), absolute paths, file:line evidence |
| **Quill** | `/quill-writer` | Writer — quick technical docs, README, comments, every code example tested |

## The 6-Phase Workflow

The engineering layer follows a canonical workflow documented in [`.claude/rules/dev-phases.md`](https://github.com/EvolutionAPI/evo-nexus/blob/main/.claude/rules/dev-phases.md). It is a **guide**, not a rigid gate — simple changes can skip phases, complex features should follow them in order.

```
Discovery → Planning → Solutioning → Build → Verify → Retro
```

| Phase | Owner | Output |
|---|---|---|
| 1. Discovery | `@echo-analyst` | `[C]discovery-{feature}.md` — gaps, assumptions, open questions |
| 2. Planning | `@compass-planner` | `[C]prd-{feature}.md` + `[C]plan-{feature}.md` |
| 3. Solutioning | `@apex-architect` | `[C]architecture-{feature}.md` (ADR format) |
| 4. Build | `@bolt-executor` | code + tests + commits |
| 5. Verify | `@oath-verifier` | `[C]verification-{feature}.md` — evidence-based PASS/FAIL |
| 6. Retro | `@mirror-retro` | `[C]retro-{feature}.md` — lessons + memory updates |

**Cycle orchestration** — `@helm-conductor` sits above the phases and answers "what next?", "who does it?", and "what's blocked?". Call Helm when you have multiple active features or need sprint sequencing.

**Feature folders** — non-trivial work lives in `workspace/development/features/{feature-slug}/` where all artifacts of one feature are grouped together. Standalone artifacts continue to live in `workspace/development/{type}/`.

## The 25 `dev-*` Skills

Skills that orchestrate the engineering agents.

### Tier 1 — Core orchestration (15)

- `dev-autopilot` — full lifecycle: spec → plan → code → QA → validation
- `dev-plan` — interview-driven planning (delegates to compass-planner)
- `dev-ralplan` — multi-perspective consensus (Planner + Architect + Critic)
- `dev-deep-interview` — Socratic Q&A to crystallize requirements
- `dev-deep-dive` — causal trace + requirements crystallization combined
- `dev-external-context` — parallel external doc lookups
- `dev-trace` — evidence-driven causal investigation
- `dev-verify` — evidence-based completion verification
- `dev-ultraqa` — QA cycling loop (build/lint/test/fix up to 5 cycles)
- `dev-visual-verdict` — visual regression testing
- `dev-ai-slop-cleaner` — post-AI cleanup (single-use helpers, over-abstractions)
- `dev-sciomc` — scientific method scaffolding
- `dev-team` — on-demand parallel agent spawning
- `dev-ccg` — tri-model orchestration (Claude + Codex + Gemini)
- `dev-ralph` — persistence loop with iteration-based circuit breaker

### Tier 2 — Setup & infrastructure (5)

- `dev-mcp-setup` — configure MCP servers
- `dev-deepinit` — generate hierarchical AGENTS.md / CLAUDE.md for new projects
- `dev-project-session-manager` — git worktrees per issue/PR
- `dev-configure-notifications` — Telegram/Discord/Slack alerts for eng events
- `dev-release` — generic release prep (changelog, version bump, tag)

### Tier 3 — Meta / utilities (5)

- `dev-cancel` — clean stop of any active eng workflow
- `dev-remember` — quick context persistence (lighter than mempalace). See [MemPalace Guide](mempalace-guide.md) for the full memory reference.
- `dev-ask` — single-shot query to a specific model (Claude/Codex/Gemini)
- `dev-learner` — extract reusable skills from conversation patterns
- `dev-skillify` — convert current conversation into a new skill

## Recommended Pipelines

### End-to-end implementation

```
User: "build me a CRUD API for a bookstore inventory in TypeScript"
   ↓
dev-autopilot
   ├── Phase 0: echo-analyst → spec
   ├── Phase 1: compass-planner → plan
   ├── Phase 2: bolt-executor → code (parallel where safe)
   ├── Phase 3: dev-ultraqa → build/test/fix loop
   ├── Phase 4: lens-reviewer + vault-security + apex-architect → validation
   └── Phase 5: oath-verifier → final evidence-based verdict
```

### High-stakes plan

```
User: "we need to migrate auth from JWT to session cookies"
   ↓
dev-ralplan (consensus mode)
   ├── compass-planner → initial plan with principles, drivers, options
   ├── apex-architect → consensus addendum (steelman antithesis, tradeoff tension)
   └── raven-critic → adversarial review (gap analysis, severity ratings)
   ↓
Final plan with ADR + pre-mortem (deliberate mode)
```

### Bug investigation

```
User: "the bot drops messages but only on Tuesdays"
   ↓
trail-tracer (multi-hypothesis)
   ├── Generate 2+ competing explanations
   ├── Collect evidence FOR and AGAINST each
   ├── Apply lenses (systems / premortem / science)
   └── Identify the discriminating probe
   ↓
hawk-debugger (once root cause is clear)
   ├── Reproduce
   ├── Minimal fix
   └── 3-failure circuit breaker
   ↓
oath-verifier (regression check)
```

### Pre-merge gate

```
git diff
   ↓
lens-reviewer (code quality, severity-rated)
   ↓
vault-security (OWASP audit)
   ↓
dev-ultraqa (build/test/fix until green)
   ↓
oath-verifier (acceptance criteria mapping)
   ↓
flow-git (atomic commits matching project style)
```

## Working Folder

All engineering layer artifacts persist in `workspace/development/`:

```
workspace/development/
├── architecture/      — apex-architect: ADRs, design tradeoffs, debug analyses
├── plans/             — compass-planner: work plans, RALPLAN-DR consensus, open questions
├── specs/             — echo-analyst, dev-deep-interview: gap analyses, requirement specs
├── reviews/           — lens-reviewer, vault-security: code reviews, security audits
├── debug/             — hawk-debugger, trail-tracer: bug reports, trace investigations
├── verifications/     — oath-verifier, grid-tester, probe-qa: verification reports, test strategies
├── research/          — scroll-docs, prism-scientist, scout-explorer: external research, statistical analyses
└── README.md
```

**Distinction from `workspace/projects/`:**

- `workspace/development/` — engineering layer **artifacts** (analyses, plans, reports). Owned by Claude.
- `workspace/projects/` — **active git projects** (Evolution API, Evo AI, Evo Go). Owned by you. Engineering agents read for context; only `bolt-executor` writes code there, and only with your approval.

## Templates

Each engineering agent has a template at `.claude/templates/dev-*.md` for its primary output:

| Agent | Template |
|---|---|
| apex-architect | `dev-architecture-decision.md` |
| compass-planner | `dev-work-plan.md` |
| lens-reviewer | `dev-code-review.md` |
| hawk-debugger | `dev-bug-report.md` |
| oath-verifier | `dev-verification-report.md` |
| echo-analyst, dev-deep-interview | `dev-deep-interview-spec.md` |
| vault-security | `dev-security-audit.md` |
| grid-tester | `dev-test-strategy.md` |
| trail-tracer | `dev-trace-report.md` |
| scout-explorer | `dev-explore-report.md` |
| canvas-designer | `dev-design-spec.md` |
| prism-scientist | `dev-analysis-report.md` |
| scroll-docs | `dev-research-brief.md` |
| raven-critic | `dev-critique.md` |
| zen-simplifier | `dev-simplification-report.md` |

## Memory

Each engineering agent has its own memory folder at `.claude/agent-memory/{agent-name}/`. Agents update memory with patterns worth carrying forward (anti-patterns, gotchas, codebase conventions).

Engineering agents use a **lighter memory pattern** than business agents — no rich auto-memory block with user/feedback/project/reference types. They are tools, not "colleagues" — Apex doesn't need to learn "the Davidson way", it just needs to remember the architecture patterns of this codebase.

## Cross-Layer Handoffs

The two layers cooperate naturally:

| Trigger | Business → Engineering |
|---|---|
| `@nova` writes a PRD | → `@apex-architect` reviews the architecture |
| `@dex-data` finds a metric anomaly | → `@trail-tracer` investigates causally |
| `@atlas-project` flags an open PR | → `@lens-reviewer` reviews it |
| `@lex-legal` flags a compliance gap in code | → `@vault-security` audits |
| `@mako-marketing` needs a landing page | → `@canvas-designer` builds it |

## Attribution

The Engineering Layer is **derived** from [oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode):

- **Author:** Yeachan Heo
- **License:** MIT (Copyright © 2025 Yeachan Heo)
- **Version imported:** v4.11.4
- **Date imported:** 2026-04-10

EvoNexus modifications:
- Renamed agents to themed names (`architect` → `apex-architect`)
- Added `dev-` prefix to all skills for namespace consistency
- Adapted memory structure to match EvoNexus per-agent pattern
- Removed runtime dependencies on OMC TypeScript `src/` (kept only the markdown definitions)
- Discarded skills that overlapped with EvoNexus builtins or were OMC meta-skills

See [NOTICE.md](https://github.com/EvolutionAPI/evo-nexus/blob/main/NOTICE.md) for the complete attribution and modification list.
