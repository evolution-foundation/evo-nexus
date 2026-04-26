---
name: sage-strategy-digest
description: "Generate weekly strategy digest consolidating financial, product, community, and market data into strategic insights. Use when user says 'strategy digest', 'weekly strategy summary', 'company status', or as part of the weekly strategy review routine."
---

# Strategy Digest — Weekly Strategic Summary

Weekly routine that consolidates data from all areas into a strategic view for decision-making.

**Always respond in English.**

## Workflow

### Step 1 — Collect data from each area

**Financial:**
- Query `/int-stripe` — current MRR, variance, new subscriptions, churn, refunds
- If available, read latest report in `workspace/finance/`

**Product:**
- Read latest `/int-linear-review` in `workspace/projects/linear-reviews/`
- Read latest `/int-github-review` in `workspace/projects/github-reviews/`
- Summarize: deliveries of the week, blockers, PRs, community issues

**Community:**
- Read latest report in `workspace/community/reports/weekly/`
- Summarize: WAM, sentiment, hot topics, FAQ gaps

**Sales:**
- If pipeline exists in `workspace/projects/comercial/`, read status
- Check active partnerships

**Trends:**
- Read latest trends report in `workspace/daily-logs/`

### Step 2 — Analyze strategically

Cross-reference the data and answer:
1. **Business health** — cash, revenue, runway. Are we secure?
2. **Product momentum** — are we delivering? What is blocked?
3. **Community** — growing? Positive sentiment? Support up to date?
4. **Market** — any relevant changes in competition or the sector?
5. **Risks** — what could go wrong in the next 2-4 weeks?
6. **Opportunities** — what should we consider doing?

### Step 3 — Generate digest (HTML + MD)

**HTML:** Read the template at `.claude/templates/html/custom/strategy-digest.html`, fill all `{{PLACEHOLDER}}` with the collected data and save to `workspace/strategy/digests/[C] YYYY-WXX-strategy-digest.html`. Create the directory if it does not exist.

**MD:** Also save markdown version to `workspace/strategy/digests/[C] YYYY-WXX-strategy-digest.md` with the following format:

```markdown
# Strategy Digest — Week {WXX}

> Generated on: {YYYY-MM-DD}
> Agent: @sage

## Business Health
**Status:** 🟢/🟡/🔴
- MRR: R${X} ({var%})
- Subscriptions: {N} ({+/-})
- Runway: {N} months

## Product
**Status:** 🟢/🟡/🔴
- Deliveries: {summary}
- Blockers: {N}
- Community issues: {N} open

## Community
**Status:** 🟢/🟡/🔴
- WAM: {N}
- Sentiment: {label}
- Docs gaps: {N}

## Sales
- Pipeline: {summary}
- Partnerships: {status}

## Risks (next 2-4 weeks)
1. {risk with evidence}

## Opportunities
1. {opportunity with justification}

## Recommendation of the week
{One sentence: what the owner should prioritize based on everything above}
```

### Step 4 — Terminal summary

Present a short and direct version.

## Rules
- **Real data** — do not fabricate metrics. If data is unavailable, say "no data"
- **Opinions flagged** — when it is opinion vs data, make it clear
- **One recommendation** — do not give 10 suggestions, give 1 clear one
- **Connect the dots** — the value of the digest is crossing areas, not repeating individual reports
