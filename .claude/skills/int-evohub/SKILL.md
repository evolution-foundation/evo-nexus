---
name: int-evohub
description: "EvoHub API — Manage WhatsApp, Instagram, and Facebook channels via Evolution Hub proxy. Use when the user wants to connect/disconnect channels, check channel status, send messages via EvoHub, manage webhooks, or troubleshoot EvoHub integrations. Also trigger for 'evohub', 'hub evolution', 'instagram evohub', 'whatsapp evohub', 'conectar instagram', 'conectar whatsapp', 'status do canal'."
---

# EvoHub Integration

Proxy/hub for Meta APIs (WhatsApp, Instagram, Facebook) via Evolution Foundation.

## Configuration

All requests use:
- **Base URL:** `https://api.evohub.ai/api/v1`
- **Auth Header:** `Authorization: Bearer ${EVO_HUB_API_TOKEN}`
- **Token location:** `config/.env` → `EVO_HUB_API_TOKEN`

## Available Tools

### Bash Helper

Use `bash` to make raw API calls. Always include auth header.

```bash
TOKEN=$(python3 -c "
import re
with open('.env') as f:
    for l in f:
        m = re.match(r'^EVO_HUB_API_TOKEN=(.*)', l.strip())
        if m: print(m.group(1))
")
BASE="https://api.evohub.ai/api/v1"

# Or simply:
grep EVO_HUB_API_TOKEN .env | cut -d= -f2
```

## API Reference

### User & Plan

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/me` | GET | Current user info |
| `/me/plan` | GET | Current plan details |
| `/me/limits` | GET | Plan limits and quotas |
| `/me/usage` | GET | Current usage stats |

### Channels

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/channels` | GET | List all channels |
| `/channels/{id}` | GET | Channel details |
| `/channels/{id}` | DELETE | Remove a channel |

### Channel Types

Channels have `type` field:
- `whatsapp` — WhatsApp via Evolution
- `instagram` — Instagram via Meta Graph API
- `facebook` — Facebook Pages via Meta Graph API

Channel `status`: `active` | `inactive` | `pending`
Channel `connection_mode`: `byo` (bring your own Meta app) | `proxy` (shared)

### Instagram

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/instagram/authorization` | GET | Generate Instagram OAuth URL |
| `/instagram/callback` | GET | OAuth callback — redirects after auth |
| `/instagram/webhook` | POST/GET | Manage Instagram webhook subscriptions |

### Facebook

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/facebook/channel` | POST | Create Facebook channel |
| `/facebook/pages` | GET | List Facebook Pages |

### Webhooks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhooks` | GET | List all webhooks |
| `/webhooks` | POST | Create webhook |
| `/webhooks/{id}` | PUT | Update webhook |
| `/webhooks/{id}` | DELETE | Delete webhook |
| `/webhooks/event-types` | GET | Available event types |

## Common Operations

### List all channels

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.evohub.ai/api/v1/channels" | python3 -m json.tool
```

### Check user plan

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.evohub.ai/api/v1/me/plan" | python3 -m json.tool
```

### Generate Instagram auth URL

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.evohub.ai/api/v1/instagram/authorization" | python3 -m json.tool
```

### Disconnect a channel

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "https://api.evohub.ai/api/v1/channels/{channel_id}"
```

## Current State (as of setup)

| Channel | Type | ID | Status |
|---------|------|-----|--------|
| Gringo | whatsapp | `9d8220a9-e68a-423d-88fc-aa4efd2a61de` | inactive |
| sistemabritto | instagram | `541c6345-9269-41b4-9f39-25b2e775824c` | inactive |
| Sistema Britto | whatsapp | `8a278e5d-bbfb-48f9-a2da-58231550bdc2` | inactive |

**Plan:** Free — 1 BYO credential, 3 channels each (WhatsApp/FB/IG), no message limits.
