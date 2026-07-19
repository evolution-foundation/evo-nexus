# Automated Routines

Managed by the scheduler (`make scheduler`, runs `scheduler.py`) — see `ROUTINES.md` for
narrative details.

**Reality check (panorama 2026-07-17, item 3):** a prior version of this file documented
~20 daily/weekly/monthly routines as if all were scheduled. Most had no backing script at
all — `scripts/publish_scheduled.py` (the one that actually dispatches real posts to X)
was even silently failing every tick because `scheduler.py` looked for it under the wrong
path. That's fixed. What's below is now split into what's genuinely scheduled vs what
still requires a manual skill invocation — no more promising automation that doesn't run.

## Core (`scheduler.py`, ships with the repo)

| Time | Routine | Script |
|---------|--------|--------|
| 07:00 | Good Morning (briefing) | `good_morning.py` |
| 21:00 | End of Day | `end_of_day.py` |
| 21:00 | Daily Backup | `backup.py` |
| 21:15 | Memory Sync | `memory_sync.py` |
| 04:00 | Uso Modelos DIA (cost telemetry) | `uso_modelos_dia.py` |
| Every hour, 08h-20h BRT | Hourly Report | `hourly_report.py` |
| Sunday 09:00 | Memory Lint | `memory_lint.py` |
| Friday 08:00 | Weekly Review | `weekly_review.py` — reactivated; checks overdue items weekly |

`run_adw()` resolves a script's real location with a 3-candidate fallback
(`ADWs/routines/custom/<name>` → `ADWs/routines/<name>` → top-level `scripts/<name>`) so
a script doesn't need to be relocated just to be scheduled — see `scheduler.py::run_adw`.

## Deadline heartbeat (in-process, zero Claude cost)

`deadline-check` (`config/heartbeats.example.yaml`) — every 4h, checks for active Goals
and open/in_progress/blocked Tickets past their `due_date` and alerts via Telegram if any
exist. Closes the gap between Weekly Review runs (Fridays only). Handler:
`dashboard/backend/deadline_check.py::tick`. Ships `enabled: false` — enable via
`/scheduler` → Heartbeats after reviewing.

## Operacional diário (`config/routines.yaml`, local/gitignored — notifies via WhatsApp)

Decision (2026-07-17): only this cluster gets formalized scheduling; notification channel
is WhatsApp (Evolution Go, instância `sistema-britto`) to the superadmin's number, **not
e-mail**. Requires `WHATSAPP_PHONE` + `EVOLUTION_GO_URL`/`EVOLUTION_GO_KEY` in `.env` — see
`.env.example`. All ship `enabled: false`; flip to `true` in `config/routines.yaml` once
those env vars are set.

| Time | Routine | Script | Agent |
|---------|--------|--------|--------|
| 06:50 | Review Todoist | `custom/review_todoist.py` → skill `prod-review-todoist` | @clawdia |
| 07:15 | Email Triage | `custom/email_triage.py` → skill `gog-email-triage` | @clawdia |
| every 30min | Sync Meetings (Fathom) | `custom/sync_meetings.py` → skill `int-fathom` | @clawdia |
| 21:30 | Dashboard Consolidado (WhatsApp) | `daily_status_report.py` | system (no Claude — pure SQL report) |

`config/routines.yaml` and `ADWs/routines/custom/*.py` are gitignored by design (same
pattern as `config/heartbeats.yaml` vs `.example.yaml`, or `.claude/agents/custom-*.md`) —
personal-to-workspace automation, not shipped in the repo. **On a VPS deploy, these do
NOT ride along with a `docker service update`** — they need to be placed on that machine
directly (same as `config/heartbeats.yaml` and `.env` already are).

## Not scheduled — invoke manually via skill

Everything below has a skill (or documented `/skill-name`) but no backing routine script —
run it in a Claude Code session (`/skill-name`) or ask the relevant agent directly. Adding
scheduling for any of these is the natural next step of this same pattern, once there's a
real script wired the way Review Todoist/Email Triage/Sync Meetings are above.

| Routine | Agent | How to run today |
|---------|--------|--------|
| Social Analytics (daily/weekly/monthly) | @pixel | `/social-analytics-report` or ask Pixel |
| Licensing (daily/weekly/monthly) | @atlas | ask Atlas |
| Financial Pulse / Weekly / Monthly Close | @flux | ask Flux |
| Community Pulse / Weekly / Monthly | @pulse | ask Pulse |
| FAQ Sync | @pulse | ask Pulse |
| Trends | @clawdia | ask Clawdia |
| Strategy Digest | @sage | ask Sage |
| Linear Review / GitHub Review | @atlas | ask Atlas |
| Learning Review Weekly | learn-* skills | `/learner` or relevant `learn-*` skill |
| Health Check-in | @kai | ask Kai |
