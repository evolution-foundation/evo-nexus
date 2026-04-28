---
name: int-linear-review
description: "Review Linear projects — check issues in review, blockers, stale items, sprint progress, and assigned tasks. Use when user says 'check linear', 'linear review', 'sprint status', 'issues in review', 'project status', 'what is blocked', or any reference to checking project/issue status in Linear."
---

# Linear Review — Project Check

Skill to review the state of Linear projects: issues in review, blockers, stale items, sprint progress, and assigned tasks.

**Always respond in English.**

## Workflow

Execute the steps below silently and present a consolidated report at the end.

### Step 1 — Gather context

Use the Linear MCP tools to collect data:

1. **Issues in Review** — list issues with state "In Review" or "Review":
   ```
   list_issues(state="In Review")
   ```

2. **User's issues** — list issues assigned to the user:
   ```
   list_issues(assignee="me")
   ```

3. **Blockers** — list issues with Urgent (1) or High (2) priority:
   ```
   list_issues(priority=1)
   list_issues(priority=2)
   ```

4. **Stale issues** — list "In Progress" issues not updated in the last 3 days:
   ```
   list_issues(state="In Progress", updatedAt="-P3D")
   ```
   Compare: if the issue is "In Progress" but hasn't been updated in more than 3 days, mark as stale.

5. **Current cycle** — check active sprint/cycle progress:
   ```
   list_cycles(teamId="...", type="current")
   ```

### Step 2 — Analyze

For each group, identify:
- **In Review:** who needs to review, how long it has been pending
- **Blockers:** what is blocking and who is responsible
- **Stale:** issues stagnant without activity — need attention or re-prioritization
- **My issues:** what the user needs to do first (by priority)

### Step 3 — Report

Present in the format:

```
## Linear Review — {data}

### In Review ({N})
| Issue | Title | Assignee | Days in review |
|-------|--------|-------------|----------------|

### Blockers ({N})
| Issue | Title | Priority | Assignee | Block description |
|-------|--------|------------|-------------|----------------------|

### Stale — Inactive >3 days ({N})
| Issue | Title | Assignee | Last updated |
|-------|--------|-------------|-------------------|

### My Issues ({N})
| Issue | Title | Status | Priority |
|-------|--------|--------|------------|

### Current Sprint/Cycle
- Progress: {X}% ({completed}/{total})
- Deadline: {end date}
- Risk: {high/medium/low}
```

### Step 4 — Save HTML artifact

Read the template at `.claude/templates/html/custom/linear-review.html`, fill all `{{PLACEHOLDER}}` with the data collected in previous steps, and save the complete HTML to `workspace/projects/linear-reviews/[C] YYYY-MM-DD-linear-review.html`.

Create the directory `workspace/projects/linear-reviews/` if it does not exist.

## Rules

- **Do not modify issues** — only read and report. Changes only with user approval
- **Prioritize clarity** — if unable to determine the team or cycle, list what you find without blocking
- **Highlight risks** — issues in review for more than 2 days or stale are warning signs
- **Be direct** — numbers, not narrative


### Notification line

Write as the last line of your output:
TELEGRAM_MSG: 📋 Linear Review [date] | [main result in 1 line]
