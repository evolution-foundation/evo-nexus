---
name: int-github-review
description: "Review GitHub repos — open PRs, community issues, stars/forks, releases, contributors. Use when user says 'check github', 'github review', 'repo status', 'open PRs', 'github issues', 'repository status', or any reference to checking GitHub repos status."
---

# GitHub Review — Repository Status

Skill to review the organization's GitHub repositories: open PRs, community issues, activity, stars, and releases.

**Always respond in English.**

## Monitored repositories

| Repo | Descrição |
|------|-----------|
| `YOUR_ORG/main-api` | Main API (open source) |
| `YOUR_ORG/crm-product` | CRM + AI agents |
| `YOUR_ORG/go-service` | Go microservice |
| `YOUR_ORG/crm-community` | CRM Community edition |
| `YOUR_ORG/methodology` | Development methodology |

## Workflow

### Step 1 — Collect data from each repo

For each repository, use `gh` CLI to fetch:

```bash
# Open PRs
gh pr list --repo YOUR_ORG/{repo} --state open --json number,title,author,createdAt,updatedAt,labels,reviewDecision --limit 20

# Open issues (last 20)
gh issue list --repo YOUR_ORG/{repo} --state open --json number,title,author,createdAt,updatedAt,labels,comments --limit 20

# Repo statistics
gh api repos/YOUR_ORG/{repo} --jq '{stargazers_count, forks_count, open_issues_count, updated_at}'

# Latest release
gh release list --repo YOUR_ORG/{repo} --limit 1 --json tagName,publishedAt,name

# Recent activity (commits in last 7 days)
gh api "repos/YOUR_ORG/{repo}/commits?since=$(date -v-7d +%Y-%m-%dT00:00:00Z)&per_page=5" --jq 'length'
```

### Step 2 — Analyze

For each repo, classify:

1. **Open PRs**: how many, how long open, who needs to review, pending reviews
2. **Community issues**: reported bugs, feature requests, questions
3. **Stale issues**: open for more than 14 days without response
4. **Activity**: weekly commits, whether active or stagnant
5. **Growth**: stars/forks (compare with previous data if available)

### Step 3 — Report

Present in the format:

```
## GitHub Review — {data}

### Summary
| Repo | PRs | Issues | Stars | Commits (7d) | Status |
|------|-----|--------|-------|---------------|--------|

### PRs that need attention
| Repo | PR | Title | Author | Days open | Review |
|------|----|----|-------|-------------|--------|

### Community issues (unanswered)
| Repo | Issue | Title | Days without response |
|------|-------|--------|-------------------|

### Most voted / commented issues
| Repo | Issue | Title | Comments | Labels |
|------|-------|--------|------------|--------|

### Recent releases
| Repo | Version | Date |
|------|--------|------|

### Activity (last 7 days)
{activity summary per repo — active/moderate/stagnant}
```

### Step 4 — Generate HTML report

Read the template at `.claude/templates/html/custom/github-review.html`.

Replace the `{{...}}` placeholders with the actual collected data.

Time classifications:
- PRs/Issues < 2 days: `fresh` (green)
- 2-5 days: `aging` (yellow)
- > 5 days: `stale` (red)

Activity classifications:
- > 10 commits/week: `active` (green)
- 1-10 commits: `moderate` (yellow)
- 0 commits: `inactive` (red)

Save the filled HTML to:
```
workspace/projects/github-reviews/[C] YYYY-MM-DD-github-review.html
```

Create directory if it does not exist.

## Rules

- **Use `gh` CLI** — already authenticated on the system
- **Do not create issues or PRs** — only read and report
- **PRs without review > 2 days = alert** — highlight
- **Issues without response > 7 days = alert** — highlight
- **Compare with previous review** if it exists in the directory
- **Focus on action** — what needs the responsible person's attention, not just numbers


### Notify via Telegram

Upon completion, use the Telegram reply tool to send a short summary to the user.
Format: emoji + routine name + main result (1-3 lines).
If the routine had no updates, send anyway with "no updates".
