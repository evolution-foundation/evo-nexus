---
name: social-analytics-report
description: "Unified social media analytics report — consolidates YouTube, Instagram, and LinkedIn data into one cross-platform HTML dashboard. Compares engagement, followers, top content across all platforms. Use when user says 'social analytics', 'social media report', 'social report', 'social metrics', 'cross-platform', or any reference to unified social media performance."
---

# Social Analytics — Consolidated Cross-Platform Report

Routine that pulls data from ALL connected social platforms (YouTube, Instagram, LinkedIn) and generates a single comparative HTML report.

**Always respond in English.**
**Agente:** @pixel

## Workflow

### Step 1 — Collect data from all platforms (silently)

Execute the scripts for each integration and capture the results:

#### YouTube (all accounts)
```bash
python3 {project-root}/.claude/skills/int-youtube/scripts/youtube_client.py summary
python3 {project-root}/.claude/skills/int-youtube/scripts/youtube_client.py recent_videos 1 10
```

#### Instagram (all accounts)
```bash
python3 {project-root}/.claude/skills/int-instagram/scripts/instagram_client.py summary
python3 {project-root}/.claude/skills/int-instagram/scripts/instagram_client.py recent_posts your_account 10
python3 {project-root}/.claude/skills/int-instagram/scripts/instagram_client.py recent_posts secondary_account 10
```

#### LinkedIn (all accounts)
```bash
python3 {project-root}/.claude/skills/int-linkedin/scripts/linkedin_client.py summary
```

If any platform fails or has no data, include in the report as "No data — platform not configured or API limited" without breaking the report.

### Step 2 — Consolidate cross-platform metrics

Calculate:

1. **Total followers** (sum of all accounts across all platforms)
2. **Total publications** in the period (YouTube videos + Instagram posts)
3. **Average engagement rate** weighted by platform
4. **Platform with highest growth** (follower delta, if previous report exists)
5. **Platform with highest engagement** (compare engagement rates)

### Step 3 — Build comparison table

One row per account:

| Platform | Account | Followers | Delta | Posts | Engagement | Best Content |
|------------|-------|-----------|-------|-------|------------|-----------------|
| YouTube | Your Channel | 7.450 | — | 27 | 7.0% | "Evo v3 chegando" |
| Instagram | your_account | 686 | — | 18 | 3.9% | "Evo V3 funcionalidades" |
| Instagram | secondary_account | 273 | — | 76 | — | — |
| LinkedIn | Your Profile | — | — | — | — | Perfil apenas |

### Step 4 — Top cross-platform content

Rank the top 10 content from ALL platforms by engagement (likes + comments / views or followers). Show:
- Platform
- Title/caption (summary)
- Views or reach
- Engagement %
- Link

### Step 5 — Compare with previous period

Read the previous report in `workspace/social/reports/consolidated/` if it exists. Calculate deltas for:
- Followers per platform
- Average engagement
- Publication volume

### Step 6 — Cross-platform insights

Generate analysis with:
- Which platform is growing the most?
- Which content type performs best where? (video on YouTube vs image on Instagram)
- Publication frequency per platform
- Recommendations: where to invest more content, which format to prioritize
- Platforms without data (LinkedIn posts, etc.) — what is missing to unlock

### Step 7 — Generate HTML

Read template `.claude/templates/html/custom/social-analytics-report.html` and fill all `{{PLACEHOLDER}}`.

`{{REPORT_TYPE}}` depende da frequência:
- Daily: "Daily"
- Weekly: "Weekly" 
- Monthly: "Monthly"

For comparison table rows:
```html
<tr>
  <td><span class="badge blue">YouTube</span></td>
  <td>Your Channel</td>
  <td class="right">7.450</td>
  <td class="right delta up">+32</td>
  <td class="right">27</td>
  <td class="right">7.0%</td>
  <td>Evo v3 chegando...</td>
</tr>
```

For platforms without data:
```html
<tr style="opacity:0.5">
  <td><span class="badge muted">LinkedIn</span></td>
  <td>Your Profile</td>
  <td class="right">—</td>
  <td class="right">—</td>
  <td class="right">—</td>
  <td class="right">—</td>
  <td style="color:var(--text-muted)">API limited — profile only</td>
</tr>
```

For `{{MISSING_INTEGRATIONS}}` — if any platform is not configured:
```html
<div class="highlight-card" style="border-color: rgba(102,112,133,0.3);">
  <div class="title" style="color: var(--text-muted);">Pending Integrations</div>
  <div class="body">
    <ul style="list-style:none;padding:0;">
      <li>LinkedIn — Posts and Company Page (requires Community Management API)</li>
      <li>Twitter — Not configured</li>
    </ul>
  </div>
</div>
```

### Step 8 — Save

```
workspace/social/reports/consolidated/[C] YYYY-MM-DD-social-analytics.html
```

Create directory if it does not exist.
