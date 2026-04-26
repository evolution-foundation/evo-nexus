---
name: pulse-monthly
description: "Monthly community report — aggregates Discord and WhatsApp activity for the full month: MAM, growth, sentiment trends, top contributors, product insights, docs gaps, and week-over-week evolution. Generates an HTML report using the Evolution brand. Use when user says 'community monthly', 'monthly community report', 'monthly pulse', or any reference to monthly community analysis."
---

# Monthly Community Report

Monthly routine that analyzes all Discord and WhatsApp activity for the month and generates a complete HTML report with trends, insights, and recommendations.

**Always respond in English.**

## Workflow

### Step 1 — Determine period

- Reference month: previous month (e.g., if today is 04/01, analyze March)
- Period: first to last day of the reference month
- Divide the month into weeks (W1, W2, W3, W4/W5) for trend analysis

### Step 2 — Collect Discord data (30 days)

Use the `/discord-get-messages` skill to fetch messages for the month in the main channels.

Guild ID: `YOUR_GUILD_ID`

Channels to monitor:
- All community text channels (chat-pt, chat-en, chat-es, help, feedback, suggestions, showcase, news)
- New members channel (`🆕・new-members`)

For each channel, fetch paginated messages (100 per request) until covering the full month.

### Step 3 — Collect WhatsApp data (30 days)

Use the `/int-whatsapp` skill:

```bash
python3 {project-root}/.claude/skills/int-whatsapp/scripts/whatsapp_client.py messages_30d
python3 {project-root}/.claude/skills/int-whatsapp/scripts/whatsapp_client.py stats --start YYYY-MM-01 --end YYYY-MM-DD
python3 {project-root}/.claude/skills/int-whatsapp/scripts/whatsapp_client.py groups --start YYYY-MM-01 --end YYYY-MM-DD
```

### Step 4 — Calculate monthly KPIs

1. **MAM (Monthly Active Members)**: unique members who sent a message in the month (Discord + WhatsApp)
2. **Total Messages**: Discord + WhatsApp separate and combined
3. **New Members**: entries in Discord `🆕・new-members` for the month
4. **Resolution Rate**: answered questions / total in #help (goal: >80%)
5. **Comparison**: compare all KPIs with the previous month (read previous report if it exists in `workspace/community/reports/monthly/`)

### Step 5 — Weekly evolution

For each week of the month, calculate:
- Messages
- Active members
- New members
- Sentiment (positive/neutral/negative)
- Open support tickets

Present in a table to visualize the trend throughout the month.

### Step 6 — Metrics by platform

**Discord:**
- Total messages, active members, support tickets, sentiment
- Most active channel, most helped channel

**WhatsApp:**
- Total messages, active groups, unique participants, sentiment
- Most active group

### Step 7 — Top contributors

Rank by message volume + answers given in #help:
- Top 10 most active members
- Main platform (Discord/WhatsApp)
- Highlight (helper, active new member, topic leader)

### Step 8 — Month's topics

Group all discussions by theme:
- Top 10 most discussed topics
- Frequency, sentiment by topic
- Sources (Discord, WhatsApp, or both)

### Step 9 — Product insights

1. **Requested features**: spontaneous feature requests (with frequency)
2. **Reported bugs**: technical problems mentioned (with frequency)
3. **Docs gaps**: recurring questions whose answers should be in the documentation

### Step 10 — Sentiment trend

For each week of the month:
- % positive, % neutral, % negative
- Trend: improving, stable, worsening

### Step 11 — Analysis and recommendations

**Analysis** (3-5 bullets):
- Community growth or contraction
- Engagement patterns (peak days/times)
- Sentiment evolution
- Support effectiveness
- Discord vs WhatsApp: which platform is growing more?

**Recommendations** (3-5 bullets):
- Actions to improve engagement
- Docs to create/update
- Features to prioritize based on feedback
- Members to recognize/engage

### Step 12 — Generate HTML report

Read the template at `.claude/templates/html/custom/community-monthly-report.html` and replace ALL `{{PLACEHOLDER}}`.

For dynamic rows, use the pattern from the other pulse skills:

**Weeks:**
```html
<tr>
  <td>Week 1 (01-07/MM)</td>
  <td class="right">XXX</td>
  <td class="right">XX</td>
  <td class="right">X</td>
  <td class="right"><span class="badge green">Positive</span></td>
  <td class="right">X</td>
</tr>
```

**Top contributors:**
```html
<tr>
  <td>Nome</td>
  <td><span class="badge blue">Discord</span></td>
  <td class="right">XXX</td>
  <td class="right">XX</td>
  <td><span class="badge green">Helper</span></td>
</tr>
```

**Topics:**
```html
<div class="list-item">Topic — XX mentions, positive/mixed/negative sentiment</div>
```

**Features/Bugs:**
```html
<div class="list-item">Description — X mentions (Discord/WhatsApp)</div>
```

**Docs gaps:**
```html
<tr>
  <td>Recurring question</td>
  <td>Discord #help / WhatsApp</td>
  <td class="right">X times</td>
  <td><span class="badge yellow">Installation</span></td>
</tr>
```

### Step 13 — Save

Save to:
```
workspace/community/reports/monthly/[C] YYYY-MM-community-monthly.html
```

Create the directory `workspace/community/reports/monthly/` if it does not exist.

### Step 14 — Confirm

```
## Community Monthly generated

**File:** workspace/community/reports/monthly/[C] YYYY-MM-community-monthly.html
**Month:** {reference month}
**MAM:** {N} ({delta}%) | **Messages:** {N} | **New:** {N}
**Sentiment:** {trend} | **Resolution:** {X}%
**Highlights:** {N} features, {N} bugs, {N} docs gaps
```
