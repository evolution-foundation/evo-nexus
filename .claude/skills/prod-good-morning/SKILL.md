---
name: prod-good-morning
description: "Morning orientation that recaps recent work, checks agenda, emails, meetings and tasks, then helps decide what to work on. Trigger when user says 'good morning', 'morning', 'start my day', 'what do I have today', or anything that signals beginning of a work session. Use it proactively — if a user opens with a greeting at the start of a session, run this skill before doing anything else."
---

# Good Morning

This skill orients a new session by reading recent logs, recapping what happened, and helping the user decide what to work on.

## Step 1 — Read the workspace

Read these files before saying anything:

1. **CLAUDE.md** — the master context file. This tells you who the user is, what projects are active, and what skills are available.
2. **Last 3 session logs** — find them in `workspace/daily-logs/`, sorted by date, most recent first. These are Claude's previous session notes.
3. **All active project overviews** — for each project listed in CLAUDE.md, read its overview file. These contain the goal, why, and open problems for each project.

If any of these files don't exist yet (the user might be very new), that's fine — just work with what's there.

## Step 2 — Check agenda, emails and tasks

Before building the recap, gather live data silently (don't narrate each step):

1. **Agenda do dia** — use `/gog-calendar` to list today's events. Note meetings, times, people, and free blocks.
2. **Emails importantes** — use the Gmail MCP directly (`list_emails` then `get_email` for each relevant one) to check unread emails needing action or attention. Do NOT invoke `/gog-email-triage` as a sub-skill — it sends its own Telegram notification and would cause a duplicate.
3. **Tarefas de hoje** — run `todoist today` to list today's and overdue tasks from Todoist.

## Step 3 — Brief recap

Give the user a short morning briefing in **pt-BR**. Keep it tight — this is an orientation, not a report:

- What was worked on recently (2–4 bullets from the session logs)
- Anything left open or mid-flight
- Today's agenda (meetings, times, people)
- Emails needing attention (if any)
- Today's priority tasks from Todoist

Then immediately give your **recommendation** — one clear sentence on what seems most important to work on based on recency, open problems, agenda, and project momentum. Make a real call; don't hedge.

If there are no previous logs (brand new user), skip the recap and go straight to Step 4.

## Step 4 — Ask what they want to do

After the recap, ask:

> "Want to jump into a project, or start something new?"

### If they pick a project:

Show each active project with its open problems as options. Pull from the "Open Problems" section of each project overview. Keep it scannable — one line per problem.

Ask them to pick a project and problem. Once they choose, read whatever additional context is needed and get to work.

### If they want something new:

Tell them to say "new project" and the new-project skill will walk them through it.

## Step 5 — Save briefing

Read the template at `.claude/templates/html/morning-briefing.html`, fill all `{{PLACEHOLDER}}` values with the data gathered in Steps 2–3 (agenda, emails, tasks, recommendation), and save the completed HTML to `workspace/daily-logs/[C] YYYY-MM-DD-morning.html`.

Create the `workspace/daily-logs/` directory if it does not exist.

## Tone

Keep the morning briefing conversational and brief. The user is starting their day — they don't need a wall of text. Punchy bullets, one clear recommendation, then move into action.
