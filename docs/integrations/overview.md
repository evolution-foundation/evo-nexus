# Integrations Overview

EvoNexus connects to external services through three mechanisms: **MCP servers**, **API clients**, and **OAuth flows**. Each integration provides data to one or more agents and routines.

![Integrations overview](../imgs/doc-integrations.webp)

## Channels (Bidirectional)

Channels are a different kind of integration: external systems **push** events into your running Claude Code session, and Claude can reply back through the same channel. Unlike API clients (which Claude calls on-demand) or MCP servers (which expose tools), channels deliver messages in real time.

| Channel | Platform | Make Command | Guide |
|---------|----------|-------------|-------|
| Telegram | Mobile/Desktop | `make telegram` | [Setup](../guides/channels.md#setup-telegram) |
| Discord | Desktop/Mobile | `make discord-channel` | [Setup](../guides/channels.md#setup-discord-channel) |
| iMessage | macOS only | `make imessage` | [Setup](../guides/channels.md#setup-imessage) |

> **Note:** The Discord channel (bidirectional chat with Claude Code) is separate from the Discord API integration (community monitoring via @pulse). Both can coexist.

Full guide: [Channels](../guides/channels.md) | Technical reference: [Channels Reference](../guides/channels-reference.md)

## Integration Types

### MCP Servers

Model Context Protocol servers run as sidecar processes alongside Claude Code. They expose tools that Claude can call natively during a session.

| Integration | MCP Server | Used By |
|---|---|---|
| Google Calendar | `google-calendar` | @clawdia (agenda, scheduling) |
| Gmail | `gmail` | @clawdia (triage, drafts, send) |
| GitHub | `github` | @atlas (PRs, issues, releases) |
| Linear | `linear-server` | @atlas (issues, sprints) |
| Telegram | `plugin:telegram` | @clawdia (notifications, bot) |
| Canva | `canva` | @pixel (designs, presentations) |
| Notion | `claude_ai_Notion` | Knowledge base |

MCP servers are configured in `.claude/settings.json`. Claude Code starts them automatically when their tools are needed.

### API Clients

Direct HTTP calls to service APIs, executed by skills and routines via Python scripts.

| Integration | Used By | Skills |
|---|---|---|
| Stripe | @flux | `int-stripe`, `fin-daily-pulse`, `fin-weekly-report` |
| Omie | @flux | `int-omie`, `fin-monthly-close-kickoff` |
| Bling | @flux | `int-bling`, `fin-daily-pulse`, `fin-monthly-close-kickoff` |
| Asaas | @flux | `int-asaas`, `fin-daily-pulse`, `fin-weekly-report` |
| Discord | @pulse | `discord-get-messages`, `pulse-daily`, `pulse-weekly` |
| Fathom | @clawdia | `int-fathom`, `int-sync-meetings` |
| YouTube | @pixel | `int-youtube`, `social-youtube-report` |
| Instagram | @pixel | `int-instagram`, `social-instagram-report` |
| LinkedIn | @pixel | `int-linkedin`, `social-linkedin-report` |
| X / Twitter | @pixel | `post_to_x.py`, `publish_scheduled.py` (posting via editorial calendar) |
| Evolution API | @pulse | `int-evolution-api`, `pulse-daily` |
| Evolution Go | @pulse | `int-evolution-go`, `pulse-daily` |
| Evo CRM | @nex | `int-evo-crm` |

API clients read credentials from `.env` at runtime.

### OAuth Flows

Social media accounts (YouTube, Instagram, LinkedIn, X/Twitter) use OAuth for authentication. The dashboard provides a built-in OAuth flow:

1. Go to **Integrations** in the dashboard
2. Click **Connect** on the platform you want
3. Complete the OAuth consent screen
4. Tokens are saved automatically — `SOCIAL_*` keys go to the persistent store `config/social.env` (see [X / Twitter](x-twitter.md#how-tokens-are-stored)); other keys go to `.env`

OAuth requires client credentials configured in `.env`:

```env
# YouTube
YOUTUBE_OAUTH_CLIENT_ID=...
YOUTUBE_OAUTH_CLIENT_SECRET=...

# Instagram / Meta
META_APP_ID=...
META_APP_SECRET=...

# LinkedIn
LINKEDIN_CLIENT_ID=...
LINKEDIN_CLIENT_SECRET=...
```

## Checking Integration Status

### Dashboard

The **Integrations** page shows a status card for each service:
- **Green** -- connected and working
- **Yellow** -- credentials present but not verified
- **Red** -- missing credentials

### CLI

```bash
# Test a specific integration
make community     # tests Discord
make fin-pulse     # tests Stripe + Omie
make github        # tests GitHub
make social        # tests YouTube + Instagram + LinkedIn
```

## Configuration

All integration credentials live in `.env`. The setup wizard (`make setup`) generates this file with empty placeholders for each enabled integration.

You can edit credentials through:
- The dashboard **.env editor** (Config > .env)
- Directly in the terminal: `nano .env`

See individual integration guides for setup steps:

**Financial & ERP:**
- [Stripe](stripe.md) -- MRR, charges, subscriptions, churn
- [Omie](omie.md) -- Brazilian ERP: clients, invoices, financials, stock
- [Bling](bling.md) -- Brazilian ERP: products, orders, NF-e, stock (OAuth2 with auto-refresh)
- [Asaas](asaas.md) -- Brazilian payments: Pix, boleto, credit card, subscriptions, marketplace split

**Meetings & Tasks:**
- [Fathom](fathom.md) -- Meeting recordings, transcripts, action items
- [Todoist](todoist.md) -- Task management via CLI

**Messaging:**
- [Telegram](telegram.md) -- Bot messaging and notifications via MCP
- [Discord](discord.md) -- Community channels and messages

**Social Media:**
- [YouTube](youtube.md) -- Channel stats, videos, engagement (OAuth)
- [Instagram](instagram.md) -- Profile, posts, engagement (Graph API + OAuth)
- [LinkedIn](linkedin.md) -- Profile and org stats (OAuth)
- [X / Twitter](x-twitter.md) -- Automated posting with token rotation (OAuth PKCE)

**Development:**
- [GitHub](github.md) -- PRs, issues, releases via MCP + CLI
- [Linear](linear.md) -- Issues, sprints, project tracking via MCP
**Productivity:**
- [Google Calendar + Gmail](google.md) -- Agenda, scheduling, email triage
- [Obsidian](obsidian.md) -- Vault management, notes, search via CLI

**Evolution Platform:**
- [Evolution API](evolution-api.md) -- WhatsApp instances and messaging
- [Evolution Go](evolution-go.md) -- Evolution Go instances
- [Evo CRM](evo-crm.md) -- CRM contacts, conversations, pipelines

## Multi-Account Support

Social media integrations support multiple accounts per platform using a numbered pattern:

```env
SOCIAL_YOUTUBE_1_LABEL=Main Channel
SOCIAL_YOUTUBE_1_ACCESS_TOKEN=ya29...
SOCIAL_YOUTUBE_1_CHANNEL_ID=UC...

SOCIAL_YOUTUBE_2_LABEL=Second Channel
SOCIAL_YOUTUBE_2_ACCESS_TOKEN=ya29...
SOCIAL_YOUTUBE_2_CHANNEL_ID=UC...
```

Analytics skills automatically aggregate across all connected accounts.
