---
name: create-goal
description: "Create a Mission, Project, or Goal (Mission → Project → Goal → Task hierarchy) in EvoNexus. Guides the user through picking a mission, choosing or creating a project, defining a measurable goal with metric_type and target_value. Writes to the SQLite goals tables via POST /api/goals. Use when the user says 'create a goal', 'add a mission', 'set a target', 'measure X', 'track Y towards Z', or wants to link work to a measurable outcome."
---

# Create Goal

Create a Goal in the Mission → Project → Goal → Task hierarchy.

## When to use

Use this skill when the user wants to:
- Declare a new measurable outcome ("100 paying customers by June")
- Add a project under a mission ("Evo AI is a project under our revenue mission")
- Create the mission itself (rare — usually 1 mission)

## Step 1: Identify the level

Ask the user:
- **Mission** — top-level purpose (usually already exists; check with `GET /api/missions`). v1 assumes 1 active mission.
- **Project** — major initiative (Evo AI, Evolution Summit, Academy). Creates under a mission.
- **Goal** — measurable target within a project (100 customers, $50k MRR, ship billing v2).

Start with the most common: a new Goal under an existing Project.

## Step 2: Collect fields

For a Goal:
- **Title** — what's being measured ("100 paying customers")
- **Description** — context (optional)
- **Project** — which existing project (fetch list via `GET /api/projects`)
- **metric_type** — one of:
  - `count` — integer (customers, tickets sold, users)
  - `currency` — USD or BRL value ($50k MRR)
  - `percentage` — 0.0 to 100.0 (retention rate)
  - `boolean` — yes/no (ship billing v2 → target_value=1)
- **target_value** — the number to reach
- **due_date** — ISO date (YYYY-MM-DD) — when the goal is expected to hit
- **target_metric** — short label to display on UI (e.g., "paying customers")

For a Project:
- **Slug** — unique kebab-case id (e.g., `evo-ai`)
- **Title** — display name
- **Description**
- **Mission** — parent mission slug
- **workspace_folder_path** — optional, points to `workspace/project/<slug>.md`

For a Mission:
- **Slug, title, description, target_metric, target_value, due_date, status** — same pattern

## Step 3: Write to Nexus

Use the local writer script. Do **not** rely on browser auth, cookies, or `DASHBOARD_API_TOKEN`; OpenClaude sessions may not share dashboard auth state.

List the current hierarchy first:

```bash
python3 .claude/skills/create-goal/scripts/nexus_goal.py list
```

Create a goal:

```bash
python3 .claude/skills/create-goal/scripts/nexus_goal.py create-goal \
  --title "100 paying customers by Jun 30" \
  --slug evo-ai-100-customers \
  --project-slug evo-ai \
  --project-title "Evo AI" \
  --metric-type count \
  --target-metric "paying customers" \
  --target-value 100 \
  --due-date 2026-06-30
```

Create a mission or project explicitly when needed:

```bash
python3 .claude/skills/create-goal/scripts/nexus_goal.py create-mission \
  --title "Evolution MRR Q4 2026" \
  --slug evolution-mrr-q4-2026 \
  --target-metric "MRR" \
  --target-value 1000000 \
  --due-date 2026-12-31

python3 .claude/skills/create-goal/scripts/nexus_goal.py create-project \
  --title "Evo AI" \
  --slug evo-ai \
  --mission-slug evolution-mrr-q4-2026
```

Create starter tasks:

```bash
python3 .claude/skills/create-goal/scripts/nexus_goal.py create-task \
  --goal-slug evo-ai-100-customers \
  --title "Launch annual billing plan" \
  --assignee-agent mako-marketing \
  --priority 2
```

Response includes `id` and `slug`.

## Step 4: Show the user how to link work

Return the slug and show how to attach it to a routine, heartbeat, or ticket:

```yaml
# routines.yaml
- name: my-routine
  goal_id: evo-ai-100-customers
  ...

# heartbeats.yaml
- id: flux-6h
  goal_id: evo-ai-100-customers
  ...

# ticket
Create or edit the ticket with goal_id=<numeric goal id>.
```

When linked, the agent running the work receives Mission → Project → Goal chain automatically in its prompt.

## Step 5: Show the progress view

Direct the user to `/goals` in the UI to see the new goal in the tree with 0% progress. As tasks complete (status='done'), the SQLite trigger increments `current_value` automatically. When `current_value >= target_value`, the goal status flips to `achieved`.

## Notes

- Drift: if `current_value` and `SELECT COUNT(*) FROM goal_tasks WHERE status='done'` disagree, call `POST /api/goals/<id>/recalculate` to rebuild.
- Target changes: `PATCH /api/goals/<id>` accepts `target_value` updates.
- Cancelling: set `status='cancelled'` to hide from active views without deleting.

Related: `.claude/rules/goals.md`, `.claude/rules/heartbeats.md`, `.claude/rules/tickets.md`.
