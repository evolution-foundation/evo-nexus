# Goals — 4-Level Cascade (Mission → Project → Goal → Ticket)

Every piece of work traces back to a measurable outcome. Agents receive goal context automatically when `goal_id` is set.

## Hierarchy

```
Mission: Evolution MRR $1M Q4 2026
├── Project: Evo AI
│   ├── Goal: 100 paying customers by Jun 30 (count)
│   │   └── Tickets: pricing page copy, onboarding email, ... (real work)
│   └── Goal: Ship billing v2 (boolean)
├── Project: Evolution Summit
│   ├── Goal: Sell 200 tickets
│   └── Goal: Close 3 sponsors
└── Project: Evo Academy
    └── Goal: 50 beta students
```

Every rung can be proposed by AI and always requires human approval before it
exists — see [[ai-hierarchy-suggestions]] below. Nothing in this hierarchy is
ever created unsupervised.

## Data Model

SQLite tables (in `dashboard.db`):

| Table | Purpose |
|---|---|
| `missions` | Top-level purpose (v1 = single mission) |
| `projects` | Work group under a mission |
| `goals` | Measurable target within a project |
| `tickets` | The real unit of work post goal-ticket-unification — `tickets.goal_id` links here; `_recompute_goal_from_tickets` (see Auto-Progress) is the sole writer of `goals.current_value` |
| `goal_tasks` | **Frozen legacy** — predates goal-ticket-unification, read-only for goals created before it. Never created for new work; `tickets` is where real work lives now. |

Indexes on FKs + `status`. Foreign keys: `projects.mission_id ON DELETE CASCADE`, `goals.project_id ON DELETE CASCADE`, `tickets.goal_id ON DELETE SET NULL`, `goal_tasks.goal_id ON DELETE SET NULL`.

`missions`/`projects`/`goals`/`goal_tasks` all carry `completed_at` (set the
moment status transitions into a terminal value, cleared if reopened — unlike
`updated_at`, which changes on every edit). `goal_tasks` also carries
`started_at` (set once, the first time status leaves `open`).

## Metric Types

Goals declare `metric_type`:
- `count` — integer counter (e.g., "100 customers")
- `currency` — USD / BRL value
- `percentage` — 0.0 to 100.0
- `boolean` — target_value=1, current_value=0|1

UI formats display accordingly.

## Auto-Progress

There is no SQLite trigger driving this anymore — the old
`trg_task_done_updates_goal` trigger (fired on `goal_tasks.status`) was
dropped as part of goal-ticket-unification (see the comment in
`dashboard/backend/app.py` near the `_existing_tables` migrations, roughly
line 250). `goal_tasks` is frozen legacy; resolving one does not move any
goal's progress.

The real mechanism lives in `dashboard/backend/heartbeat_outcome.py::_recompute_goal_from_tickets(goal_id, conn)`,
the single idempotent source of truth for `goals.current_value` and the
`active`↔`achieved` transition:
- `current_value = COUNT(tickets WHERE goal_id = ? AND status IN ('resolved','closed'))`
- If `current_value >= target_value AND status == 'active'`: set `status = 'achieved'`, `completed_at = now()`
- If `current_value < target_value AND status == 'achieved'` (drift correction, e.g. a resolved ticket got reopened): set `status = 'active'`, `completed_at = NULL`

This is called from resolving/closing a ticket with a `goal_id` set — see
[[tickets]] (`_sync_goal_task_from_ticket` and `heartbeat_outcome._move_ticket`).
`POST /api/goals/{id}/recalculate` calls the same function directly for drift
correction (no separate view involved).

**Project rollup** (new): the same function also checks, every time a Goal
becomes `achieved`, whether *every* Goal under that Goal's `project_id` is now
terminal (`achieved` or `cancelled`). If so, it auto-`PATCH`es the Project to
`status = 'completed'` with `completed_at` set. Implemented twice, once per
data-access path that can trigger a Goal reaching a terminal state:
- ORM path: `routes/goals.py::patch_goal` → `_maybe_complete_project(project_id)` (human edits a Goal's status directly)
- Raw-SQL path: `heartbeat_outcome.py::_recompute_goal_from_tickets` → `_maybe_complete_project_raw(project_id, conn)` (the common real-world path, via ticket resolution)

Both are best-effort and never raise — a rollup failure must never break the
Goal update that triggered it.

## AI Hierarchy Suggestions

Every rung of Mission → Project → Goal can be proposed by AI instead of
typed by a human, gated behind the same Telegram approval mechanism used for
publish/decomposition gates (see `pending_approvals`, `gate_type` column):

```
POST /api/missions  → dispatch(project-planner, mission_created)
  → project-planner proposes 1-4 Projects → POST /api/approvals (gate_type=project_suggestion)
  → human approves on Telegram → routes/approvals.py creates the Projects
  → POST /api/projects (each) → dispatch(goal-suggester, project_created)
    → goal-suggester proposes 2-6 Goals → POST /api/approvals (gate_type=goal_suggestion)
    → human approves → routes/approvals.py creates the Goals
    → dispatch(goal-planner, goal_created)   ← pre-existing, unchanged
      → goal-planner decomposes into Tickets (see [[tickets]])
```

Neither `project-planner` nor `goal-suggester` ever creates rows directly —
they only ever call `POST /api/approvals`. Every step still requires a fresh
human tap, so the cascade (a Project created from an approval can itself
trigger a Goal proposal, which can itself trigger a Ticket decomposition) is
safe: there is no unsupervised recursion, only a chain of independently
gated approvals. Agents: `.claude/agents/project-planner.md`, `.claude/agents/goal-suggester.md`.

## Linking Work to Goals

### In routines (`config/routines.yaml`)
```yaml
- name: financial-weekly
  schedule: "0 9 * * 5"
  script: financial_weekly.py
  goal_id: evo-revenue-1m-q4-2026   # optional
```

### In heartbeats (`config/heartbeats.yaml`)
```yaml
- id: atlas-4h
  agent: atlas-project
  goal_id: evo-ai-100-customers     # optional
  ...
```

### In tickets
```bash
POST /api/tickets
{
  "title": "...",
  "goal_id": "evo-ai-100-customers"
}
```

## Context Injection

When a routine / heartbeat / agent action has `goal_id` set, the prompt gains:

```
## Goal Context
Mission: Evolution MRR $1M Q4 2026
Project: Evo AI
Goal: 100 paying customers by Jun 30 (progress: 23/100 tasks done, 45 days left)

---

{original prompt}
```

Implemented in `dashboard/backend/goal_context.py` (`inject_into_prompt`). Falls back to original prompt if goal not found — zero regression.

## UI

`/goals` — tree view:
- Mission card at top with overall %
- Project cards with % progress
- Goals list with progress bars
- Tickets collapsible per goal (the real work — see [[tickets]])
- Filters: status (active / achieved / on-hold / cancelled), due_date (overdue / this-week / this-month)

## How to Create

### Via UI
`/goals` → **New Mission** / **New Project** / **New Goal** buttons.

### Via skill
`/create-goal` — interactive: choose mission → project → define goal (title, metric_type, target_value, due_date).

### Via API
```
POST /api/missions
POST /api/projects       (body: mission_id)
POST /api/goals          (body: project_id, target_metric, metric_type, target_value, due_date)
POST /api/tickets        (body: goal_id, title, priority, assignee_agent)
```

Or let AI propose the rung — see [[ai-hierarchy-suggestions]] above.

## Integration Points

- **Heartbeats (F1.1)** — step 6 calls `goal_context.inject_into_prompt()` if `goal_id` set
- **Routines** — optional `goal_id` / `project_id` in YAML; runner injects context
- **Tickets (F1.3)** — optional `goal_id`; resolving/closing a ticket with a linked `task_id` recomputes the goal's progress via `_recompute_goal_from_tickets`, which can cascade into the project rollup above

## Related Rules

- `heartbeats.md` — consumes goal context in step 6
- `tickets.md` — can link to goals
- `routines.md` — optional goal linking
