#!/usr/bin/env python3
"""ADW: Weekly Review — Weekly review via Clawdia

Usage:
    python weekly_review.py           # Normal (single agent, sequential)
    python weekly_review.py --team    # Agent Team (parallel, higher token cost)
"""

import sys
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runner import run_claude, banner, summary

PROMPT = """Run the full weekly review:

1. **Week's meetings** — use /int-sync-meetings with the week's period
2. **Tasks** — use /prod-review-todoist, then list completed, overdue, and next week
3. **Next week's agenda** — use /gog-calendar to list events
4. **Memory** — review the week's daily logs, consolidate decisions/learnings

Save the report in two formats:
- **HTML:** read the template '.claude/templates/html/weekly-review.html', fill all {{PLACEHOLDER}} with collected data and save to 'workspace/daily-logs/[C] YYYY-WXX-weekly-review.html'
- **MD:** also save the markdown version to 'workspace/daily-logs/[C] YYYY-WXX-weekly-review.md' using the template in .claude/templates/weekly-review.md

Create the directory 'workspace/daily-logs/' if it doesn't exist."""

TEAM_PROMPT = """
Create an agent team for the weekly review.

Spawn these teammates using the corresponding agent types:
- clawdia-assistant teammate: Sync meetings (/int-sync-meetings), review Todoist (/prod-review-todoist), and check next week's calendar (/gog-calendar). Report meetings, task status, and upcoming agenda.
- atlas-project teammate: Summarize this week's Linear and GitHub activity. Report sprint progress, merged PRs, open issues, and blockers.
- flux-finance teammate: Summarize this week's financial highlights from Stripe and Omie. Report revenue, expenses, and notable transactions.
- pulse-community teammate: Summarize this week's community activity and sentiment from Discord and WhatsApp. Report engagement, hot topics, and unanswered questions.

Wait for all teammates to complete their tasks before proceeding.

Then consolidate all teammate findings into a weekly review.
Generate HTML + MD using the weekly review template.
Include: what was accomplished, what's planned for next week, blockers, and decisions made.
Save to workspace/daily-logs/.
Send a Telegram notification with the summary.
"""

def main():
    use_team = "--team" in sys.argv

    if use_team:
        banner("📊 Weekly Review", "Agent Team Mode (parallel) — higher token cost | @clawdia")
        results = [run_claude(TEAM_PROMPT, log_name="team-weekly-review", timeout=900, agent="clawdia-assistant", daily_output_kind="weekly")]
    else:
        banner("📊 Weekly Review", "Meetings • Tasks • Agenda • Memory | @clawdia")
        results = [run_claude(PROMPT, log_name="weekly-review", timeout=900, agent="clawdia-assistant", daily_output_kind="weekly")]

    sys.exit(1 if summary(results, "Weekly Review" + (" (Team)" if use_team else "")) else 0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠ Cancelled.")
