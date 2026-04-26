---
name: pulse-weekly
description: "Weekly community analysis report — aggregates Discord AND WhatsApp activity, engagement metrics, sentiment trends, top contributors, product insights, and docs gaps. Generates an HTML report using the Evolution brand. Use when user says 'weekly community', 'community analysis', 'weekly community report', or any reference to weekly community analysis."
---

# Weekly Community Report

Weekly routine that analyzes Discord and WhatsApp activity from the last 7 days and generates a complete HTML report.

**Always respond in English.**

## Workflow

### Step 1 — Collect the week's data

Use the `/discord-get-messages` skill to fetch messages from the last 7 days in the main channels.

Guild ID: `YOUR_GUILD_ID`

Channels to monitor:
- All community text channels (chat-pt, chat-en, chat-es, help, feedback, suggestions, showcase, news)
- New members channel (`🆕・new-members`)

For each channel, fetch paginated messages (100 per request) to cover 7 days.

### Step 1b — Collect WhatsApp data (7 days)

Use the `/int-whatsapp` skill to fetch messages and stats from the last 7 days:

```bash
python3 {project-root}/.claude/skills/int-whatsapp/scripts/whatsapp_client.py messages_7d
python3 {project-root}/.claude/skills/int-whatsapp/scripts/whatsapp_client.py stats --start $(date -u -v-7d '+%Y-%m-%d') --end $(date -u '+%Y-%m-%d')
python3 {project-root}/.claude/skills/int-whatsapp/scripts/whatsapp_client.py groups --start $(date -u -v-7d '+%Y-%m-%d') --end $(date -u '+%Y-%m-%d')
```

Include in the report as a separate "WhatsApp" section with: active groups, total messages, unique participants, topics, support questions.

### Step 2 — Calculate metrics

1. **Growth**: total members (estimate), new vs departed, net churn
2. **WAM (Weekly Active Members)**: unique members who sent a message
3. **Communicators**: % of visitors who chat (goal: 50%)
4. **Resolution rate**: answered questions / total in #help (goal: >80%)
5. **First response time**: median time between question and first response
6. **Messages per active member**: total msgs / WAM (goal: >4)

### Step 3 — Analyze sentiment and topics

For each day of the week:
1. **Sentiment**: classify messages as positive/neutral/negative
2. **Topics**: group discussions by theme, count frequency

Consolidate:
- Top 5 topics with sentiment bar
- Sentiment trend throughout the week

### Step 4 — Identify highlights

1. **Top 5 most active members**: by message volume + answers given
2. **New members who contributed**: who is new and already participated
3. **Members at risk of churn**: previously active, inactive this week

### Step 5 — Extract product insights

Analyze messages and identify:
1. **Most requested features**: spontaneous feature requests
2. **Reported bugs**: technical problems mentioned
3. **Docs gap**: questions whose answers should be in the documentation (indicate frequency)

### Step 6 — Comparison

If previous reports exist in `workspace/community/reports/weekly/`, compare:
- WAM this week vs previous
- New members vs previous
- Resolution rate vs previous
- Response time vs previous

### Step 7 — Generate HTML report

Read the template at `.claude/templates/html/custom/community-weekly-report.html`.

Replace the placeholders `{{...}}` with the actual data.

Logo available at: `workspace/projects/Evolution Foundation/Logos finais/Favicon logo/SVG/Favicon Color 500.svg`

Save to:
```
workspace/community/reports/weekly/[C] YYYY-WXX-community-report.html
```

### Step 8 — Executive summary

Present in the terminal:

```
## Weekly Report — Week {WXX}

Members: {N} ({+/-}) | WAM: {N} ({X}%)
Resolution: {X}% | 1st response: {X} min
Sentiment: {label}
Top: {topic 1}, {topic 2}, {topic 3}
Insights: {N} features, {N} bugs, {N} docs gaps

Report saved to workspace/community/reports/weekly/
```

## Rules

- **Do not reply to messages** — only read and analyze
- **Real data** — metrics based on collected messages, no fabrication
- **Docs gap is gold** — each question without docs becomes a backlog item
- **Comparison is fundamental** — always show trend vs previous week
- **Product insights** — the most valuable section, handle with care
