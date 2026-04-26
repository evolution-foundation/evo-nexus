---
name: social-youtube-report
description: "YouTube analytics report — queries channel stats, recent videos, engagement rates, top content. Generates HTML report. Use when user says 'youtube report', 'youtube metrics', 'youtube analytics', or any reference to YouTube performance analysis. Supports daily, weekly, and monthly periods."
---

# YouTube Report — Channel Analytics

Routine that pulls data from YouTube via `/int-youtube` and generates an HTML channel performance report.

**Always respond in English.**

## Workflow

### Step 1 — Determine period

Based on the command:
- `make youtube` → período "daily" (snapshot do dia)
- `make youtube-weekly` → período "weekly" (últimos 7 dias)
- `make youtube-monthly` → período "monthly" (últimos 30 dias)

If called directly, ask or use "daily" as default.

### Step 2 — Collect data

For each configured YouTube account:

```bash
# Stats do canal
python3 {project-root}/.claude/skills/int-youtube/scripts/youtube_client.py channel_stats

# Últimos vídeos com métricas
python3 {project-root}/.claude/skills/int-youtube/scripts/youtube_client.py recent_videos 1 20

# Resumo de todas as contas
python3 {project-root}/.claude/skills/int-youtube/scripts/youtube_client.py summary
```

### Step 3 — Compare with previous period

Read previous report from `workspace/social/reports/youtube/` if it exists. Calculate deltas:
- Inscritos: delta absoluto e %
- Views total: delta
- Engagement rate médio: delta
- Vídeos publicados no período

### Step 4 — Analyze

1. **Channel KPIs:** subscribers, total views, average engagement rate
2. **Period videos:** published, views, likes, comments
3. **Top video:** best performance by engagement rate
4. **Trend:** growing, stable, decelerating
5. **Recent comments:** overall sentiment (if daily, pull comments from the most recent video)

### Step 5 — Generate HTML

Use template `.claude/templates/html/custom/social-analytics-report.html` with `{{REPORT_TYPE}}` = "YouTube Daily/Weekly/Monthly".

If it is the only configured platform, adapt the template to focus on YouTube (do not show an empty cross-platform comparison table).

### Step 6 — Save

```
workspace/social/reports/youtube/[C] YYYY-MM-DD-youtube-{period}.html
```

Criar diretório if it does not exist.
