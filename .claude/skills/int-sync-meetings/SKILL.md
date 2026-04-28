---
name: int-sync-meetings
description: "Sync meetings from Fathom — fetch new recordings, save JSON, generate transcripts and summaries, update indexes. Use when user says 'sync meetings', 'sync fathom', 'update meetings', 'sync calls', or similar."
---

# Sync Meetings

Complete pipeline to sync Fathom meetings and organize them in `workspace/meetings/`.

## Prerequisites

- `FATHOM_API_KEY` configured (see skill `fathom`)
- `jq` installed
- Script `fathom.sh` available at `.claude/skills/fathom/fathom.sh`

## Full Workflow

When triggered, execute the steps below **in order**:

### Step 1 — Fetch today's meetings

By default, fetch only **today's** meetings:

```bash
# Fetch today's meetings with summary and action items
{project-root}/.claude/skills/fathom/fathom.sh meetings --after "$(date +%Y-%m-%d)" --include-summary --include-actions
```

If the user specifies a different period (e.g., "sync this week", "sync yesterday"), adjust `--after` and add `--before` as needed:
- "sync de ontem": `--after "$(date -v-1d +%Y-%m-%d)" --before "$(date +%Y-%m-%d)"`
- "sync da semana": `--after "$(date -v-7d +%Y-%m-%d)"`
- "sync do mês": `--after "$(date -v-1m +%Y-%m-%d)"`

The API already returns `default_summary.markdown_formatted` and complete `action_items` — no extra calls needed.

### Step 2 — Filter unprocessed (CRITICAL — anti-duplication)

Read the file of already processed IDs:
```
{project-root}/workspace/meetings/.state/fathom-processed-recording-ids.txt
```

Compare with the returned `recording_id`. Process only IDs that **do not exist** in this file.

**IMPORTANT:** This step is mandatory and cannot be skipped. If the ID already exists in the file, the meeting **MUST NOT be reprocessed** under any circumstances — no summary, no tasks, no notification.

If there are no new meetings, **stop immediately and stay silent** — do NOT send any Telegram notification. Do not continue to the following steps.

### Step 3 — Save raw JSON

For each new meeting, save the complete JSON to:
```
{project-root}/workspace/meetings/fathom/YYYY-MM-DD/YYYY-MM-DD__{recording_id}__{slug-do-titulo}.json
```

Where:
- `YYYY-MM-DD` = date from `created_at`
- `slug-do-titulo` = title in lowercase, spaces→hyphens, without special characters

### Step 4 — Classify project

Determine the project based on the meeting title:

| Title pattern | Project |
|---|---|
| Main API, API | `main-api` |
| CRM, Product | `crm-product` |
| Academy, Course | `academy` |
| Partner, Partnership | `partner` |
| Financial, NF, Invoice | `foundation` |
| Planning, Sprint, Grooming | infer from context |
| Sales, Partnership | `sales` |
| Operations, Internal, Daily | `internal` |
| (default) | `other` |

### Step 5 — Save summary

Use the `default_summary.markdown_formatted` that already came in the API response (Step 1).

Read the template at `.claude/templates/meeting-summary.md` and fill with the meeting data.

Save to:
```
{project-root}/workspace/meetings/summaries/{project}/YYYY-MM-DD__{project}__meeting__{slug}__{recording_id}.summary.md
```

File format (based on the template):
```markdown
---
date: YYYY-MM-DD
title: {original title}
project: {project}
type: meeting
status: summary
tags: [fathom, meeting]
recording_id: {recording_id}
recording_url: {url or share_url}
people: [{names from calendar_invitees}]
---

{content from default_summary.markdown_formatted}

## Action Items

{list of action_items formatted as checklist:}
- [ ] **{assignee.name}** — {description} ([{recording_timestamp}]({recording_playback_url}))
```

### Step 6 — Todoist triage (action items to tasks)

For each processed meeting, extract the `action_items` and create tasks in Todoist.

**BEFORE CREATING ANY TASK — mandatory anti-duplication check:**

1. Check the local state file:
   ```
   {project-root}/workspace/meetings/.state/fathom-todoist-sync.json
   ```
   If the `recording_id` already has synced tasks, **DO NOT create new tasks**. Skip to Step 7.

2. Search Todoist for existing tasks with the meeting title or recording_id in the comment:
   ```bash
   todoist list --filter "search: {meeting title}"
   ```
   If you find tasks that clearly correspond to the same action items, **DO NOT duplicate**. Record the existing IDs in `fathom-todoist-sync.json` and skip.

**Triage rules (only if passed the check above):**

1. **Translate to PT-BR** — all action items must be translated to Brazilian Portuguese
2. **Default project: `Evolution`** — all tasks go to the Evolution project in Todoist, unless explicitly instructed otherwise
3. **Actionable context** — each task must have:
   - Clear and translated title (do not copy raw English from Fathom)
   - Comment with concrete context: origin (meeting + date), objective, next step, and recording link
4. **Group by meeting** — use sections/labels to identify which meeting it came from
5. **Filter by assignee** — create tasks only for action items assigned to the user (or without assignee). Items assigned to others are recorded only in the summary as reference

**Todoist task format:**

```
Title: {translated and clear action in PT-BR}
Project: Evolution
Priority: p3 (default) — raise to p2 if blocker or near deadline
Comment: 
  📋 Origin: {meeting title} ({date})
  🎯 Objective: {what this action resolves}
  ➡️ Next step: {concrete action}
  🔗 Reference: {recording_playback_url link}
```

**Execute directly, without intermediate report.** Do not list tasks before creating — create and confirm at the end.

### Step 7 — Mark as processed (IMMEDIATELY after each meeting)

**CRITICAL:** This step must be executed **immediately after processing EACH meeting individually**, NOT at the end of all. This prevents a crash mid-processing from causing reprocessing.

Add the `recording_id` to the state file:
```
{project-root}/workspace/meetings/.state/fathom-processed-recording-ids.txt
```

One ID per line. Append, do not overwrite.

Also update `fathom-todoist-sync.json` with the created task IDs.

**Order per meeting:** Step 3 → 4 → 5 → 6 → **7 (write state)** → next meeting.

### Step 8 — Final report

When finished, present a short summary:

```
## Sync Fathom — Completed

**Period:** {oldest date} → {most recent date}
**New:** {N} meetings processed
**Already processed:** {M} skipped
**Tasks created:** {T} in Todoist (Evolution project)

### Synced meetings:
| Date | Title | Project | Tasks |
|------|--------|---------|---------|
| ... | ... | ... | {N tasks} |
```

Without listing tasks one by one — just counts. If the user wants details, they ask.

### Step 9 — Notification line

Only if at least one new meeting was processed, write this as the last line of your output:

TELEGRAM_MSG: 🎙️ Sync Fathom — N reunião(ões) processada(s) | N tarefas criadas

If no new meetings were processed (stopped at Step 2), do NOT write a TELEGRAM_MSG line.

## Notes

- **Do not reprocess** meetings that already exist in `fathom-processed-recording-ids.txt`
- If the transcript is not available in the API (empty return), save the summary anyway and mark the transcript as `status: pending`
- Create directories automatically if they do not exist (`fathom/YYYY-MM-DD/`, `raw/{project}/`, `summaries/{project}/`)
- Maintain the existing naming convention — check examples in `raw/` and `summaries/` before saving
- **Todoist triage:** translate to PT-BR, Evolution project, actionable context, execute without intermediate report
- **User's tasks only** — other people's action items stay only in the summary
- Always use pt-BR in status messages
