---
name: prod-trends
description: "Weekly trends analysis — compares community, GitHub, and financial metrics week-over-week to detect patterns, risks and opportunities. Use when user says 'trends analysis', 'trends', 'how are the metrics', 'weekly comparison', 'metrics evolution', or as part of the weekly review routine."
---

# Trends Analysis — Weekly Comparison

Routine that compares community, GitHub, and financial metrics week-over-week to detect patterns, risks, and opportunities.

**Always respond in English.**

## Data Sources

### 1. Community (Discord)
Read previous reports in:
- `workspace/community/reports/daily/` — daily pulses (HTML)
- `workspace/community/reports/weekly/` — weekly reports (HTML)

Extract from HTML or generate from data:
- Messages per day (volume)
- Active members (WAM)
- Unanswered questions
- Overall sentiment
- Top recurring topics

### 2. GitHub
Read previous reports in:
- `workspace/projects/github-reviews/` — reviews (HTML)

Extract or generate:
- Open PRs (trend: accumulating or being resolved?)
- Open vs closed issues
- Stars/forks (growth)
- Commits per week (team activity)
- Average open PR time

### 3. Financial
Query data via skills:
- `/int-stripe` — MRR, cobranças, reembolsos, assinaturas ativas
- `/int-omie` — accounts receivable/payable (if available)

Metrics:
- MRR (Monthly Recurring Revenue)
- Monthly charges vs previous month
- Refunds
- Active subscriptions (growth/churn)

### 4. Operational (ADWs)
Read runner metrics:
- `ADWs/logs/metrics.json` — runs, success rate, avg time per routine

## Workflow

### Step 1 — Collect current week's data

Fetch the most recent data from each source (last 7 days).

### Step 2 — Collect previous week's data

Fetch data from 7-14 days ago for comparison. If it does not exist (first run), mark as "baseline" and skip comparison.

### Step 3 — Calculate trends

For each metric, calculate:
- Current vs previous value
- Absolute and percentage variance
- Direction: ↑ (rising), ↓ (falling), = (stable)
- Classification: 🟢 healthy, 🟡 attention, 🔴 risk

**Classification criteria:**

| Metric | 🟢 Healthy | 🟡 Attention | 🔴 Risk |
|---------|------------|-----------|---------|
| WAM | stable or ↑ | drop <10% | drop >10% |
| Unanswered questions | <5 | 5-10 | >10 |
| Sentiment | positive | neutral | negative |
| Open PRs | <10 | 10-20 | >20 accumulating |
| Unanswered issues | <5 | 5-15 | >15 |
| Stars (weekly) | >10 | 5-10 | <5 |
| MRR | stable or ↑ | drop <5% | drop >5% |
| Success rate ADWs | >90% | 70-90% | <70% |

### Step 4 — Detect patterns

Analyze recent weeks (as many as available) and identify:
- **Persistent trends** — metric rising/falling for 2+ consecutive weeks
- **Correlations** — e.g., increase in GitHub issues + increase in Discord questions = possible bug
- **Anomalies** — unusual spike or drop vs average
- **Seasonality** — recurring patterns (e.g., Monday has more activity)

### Step 5 — Generate HTML report

Read the template at `.claude/templates/html/custom/trends-report.html`.
Replace the placeholders `{{...}}` with the actual data.

Overall health classification:
- All 🟢 or mostly 🟢: `healthy` — "Healthy"
- Mix of 🟢 and 🟡: `mixed` — "Attention"
- Any 🔴: `risk` — "Risk"

**REQUIRED:** Always generate the HTML first. Read the template, replace the placeholders, and save the complete HTML file. This applies even on the first run (baseline) — even without comparison, fill the scorecard with current values and "—" for previous.

Save HTML to `workspace/daily-logs/[C] YYYY-WXX-trends.html`.

Then, also save a summarized markdown version to `workspace/daily-logs/[C] YYYY-WXX-trends.md`:

```markdown
# Trends Analysis — Week {WXX}

## Executive Summary
{3 bullets: what improved, what worsened, opportunity}

## Scorecard

| Area | Metric | Current | Previous | Var | Trend | Status |
|------|---------|-------|----------|-----|-------|--------|
| Community | WAM | {N} | {N} | {+/-X%} | ↑/↓/= | 🟢/🟡/🔴 |
| Community | Unanswered questions | {N} | {N} | | | |
| Community | Sentiment | {label} | {label} | | | |
| GitHub | Open PRs | {N} | {N} | | | |
| GitHub | Unanswered issues | {N} | {N} | | | |
| GitHub | Stars (week) | {N} | {N} | | | |
| Financial | MRR | R${N} | R${N} | {var%} | | |
| Financial | Active subscriptions | {N} | {N} | | | |
| Operational | Success rate ADWs | {X}% | {X}% | | | |

## Detected Patterns
- {pattern 1 with evidence}
- {pattern 2 with evidence}

## Risks
- {risk with supporting metric}

## Opportunities
- {opportunity based on data}

## Recommendations
1. {concrete action based on data}
2. {concrete action}
```

### Step 6 — Save snapshot

Save a snapshot of current metrics to `memory/trends/YYYY-WXX.json` to accumulate history:

```json
{
  "week": "YYYY-WXX",
  "date": "YYYY-MM-DD",
  "community": {"wam": N, "messages": N, "unanswered": N, "sentiment": "positive"},
  "github": {"prs_open": N, "issues_open": N, "issues_unanswered": N, "stars_week": N, "commits_week": N},
  "financial": {"mrr": N, "subscriptions": N, "refunds": N},
  "operational": {"adw_runs": N, "adw_success_rate": N, "adw_avg_seconds": N}
}
```

Create `memory/trends/` if it does not exist.

## Rules

- **First run = baseline** — no comparison, just collect and save snapshot
- **Real data** — do not fabricate metrics, use what is available
- **If a source has no data, skip** — do not block due to a missing report
- **Focus on action** — each insight should lead to a concrete recommendation
- **Do not alarm without evidence** — red only when the metric truly indicates risk
