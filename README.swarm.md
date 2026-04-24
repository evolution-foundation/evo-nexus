# EvoNexus on Docker Swarm — Deploy Guide

This repo supports three independent deployment paths. Each one uses a
different set of files, and none of them interferes with the others:

| Path | Files used | Who it's for |
|---|---|---|
| **VPS bare-metal** — `git clone` + `make setup` + `install-service.sh` | `setup.py`, `install-service.sh`, `Makefile` | Single-VPS installs with systemd |
| **Local dev with Docker Compose** — `docker compose up` | `Dockerfile`, `Dockerfile.dashboard`, `docker-compose.yml` | Local development, single-machine demos |
| **Production on Docker Swarm** — Portainer / Traefik | `Dockerfile.swarm`, `Dockerfile.swarm.dashboard`, `evonexus.stack.yml` | Multi-node production behind a reverse proxy |

This guide covers the third path. The first two are documented elsewhere
and are **not** modified by the Swarm overlay.

## Architecture

Three Swarm services in one stack:

| Service | Image | Purpose |
|---|---|---|
| `evonexus_dashboard` | `evo-nexus-dashboard` | Flask + React + embedded terminal-server, exposed through Traefik |
| `evonexus_telegram` | `evo-nexus-runtime` | Long-running `claude --channels plugin:telegram…` daemon |
| `evonexus_scheduler` | `evo-nexus-runtime` | Background scheduler for ADW routines (`scheduler.py`) |

Seven named volumes (auto-created by Portainer on first deploy):

- `evonexus_config` — the UI-editable `.env` and providers config (writable)
- `evonexus_workspace` — agent workspace (daily-logs, projects, finance, etc.)
- `evonexus_dashboard_data` — dashboard SQLite
- `evonexus_memory` — persistent agent memory
- `evonexus_adw_logs` — routine execution logs
- `evonexus_agent_memory` — per-agent scratch memory
- `evonexus_codex_auth` — `~/.codex/auth.json` persisted across restarts

One external overlay network (`traefik-public` by default — rename to match
your Traefik setup).

## UI-first configuration

The stack intentionally ships **zero API keys and zero integration tokens**.
Everything is configured through the dashboard after the first boot:

- **Providers** (`/providers`) — Anthropic, OpenAI API Key, Codex OAuth, OpenRouter, Gemini, etc.
- **Integrations** (`/integrations`) — Telegram, Stripe, Omie, Bling, Asaas, Fathom, Todoist, GitHub, Linear, Gmail, Calendar, YouTube, Instagram, LinkedIn, Evolution API, Evo CRM, …
- **Env editor** — anything else

When you save a value in the dashboard, it lands in `/workspace/config/.env`
inside the `evonexus_config` volume. On the next container start, the
entrypoint sources that `.env` so every process sees the value as a regular
environment variable.

## Runtime daemon services — what does and doesn't need its own container

Only channels that maintain a persistent connection need a dedicated
long-running service. External REST APIs do not.

| Channel | Dedicated service? | Notes |
|---|---|---|
| Telegram | ✅ Included (`evonexus_telegram`) | Already in the stack |
| Discord | Optional | Duplicate the `evonexus_telegram` block; swap `plugin:telegram` → `plugin:discord` |
| iMessage | Optional | Same pattern; needs a BlueBubbles server on a Mac |
| WhatsApp (Evolution API / Go) | ❌ Not needed | External HTTP API — run the `evolution_api` stack separately |
| Stripe / Omie / Bling / Asaas / Fathom / Todoist | ❌ Not needed | External REST APIs, called on demand |
| GitHub / Linear / Gmail / Calendar / YouTube / Instagram / LinkedIn | ❌ Not needed | OAuth via dashboard, REST calls on demand |
| Evo CRM | ❌ Not needed | Reached via cross-stack HTTP over the shared Traefik network |

## Deploy flow

### Prerequisites

- Docker Swarm initialized on at least one manager node.
- A Traefik stack already running, attached to an external overlay network
  (this guide assumes `traefik-public` — rename to match your setup).
- Traefik configured with a `websecure` entrypoint and a certificate
  resolver called `letsencryptresolver` (rename in the stack labels if
  your resolver has a different name).
- A DNS A record pointing `evonexus.<yourdomain>` to the Swarm ingress.
- Docker images are pulled from Docker Hub at
  `evoapicloud/evo-nexus-{dashboard,runtime}` (published by the included
  GitHub Actions workflow on every version tag and push to `main`).
- The images are public, so no `docker login` is needed on the Swarm
  managers. If you fork the project and publish to your own namespace,
  update the `image:` lines in `evonexus.stack.yml`.

### Steps

1. **Build and publish the images.** Push a version tag (`vX.Y.Z`) or use
   the Actions tab to run *Build & Publish Docker Images (Swarm)*
   manually. The workflow builds both images in parallel and publishes
   them with the version tag and `:latest`.

2. **Open Portainer → Stacks → Add stack.**
   - Name: `evonexus`
   - Paste the contents of `evonexus.stack.yml`
   - Replace the placeholders: `evonexus.example.com` (your hostname,
     two places) and `traefik-public` (your Traefik overlay network
     name, several places). The images already point at the official
     `evoapicloud/evo-nexus-*` namespace on Docker Hub.
   - Click **Deploy**

3. **Watch the containers come up.** `evonexus_dashboard` serves the SPA
   immediately. `evonexus_telegram` and `evonexus_scheduler` will log
   `waiting for ANTHROPIC_API_KEY` every 30 seconds — this is expected
   until step 4.

4. **Open `https://evonexus.<yourdomain>`** → Setup wizard → create the
   admin user → Providers → pick and save a provider:
   - **Anthropic** — paste `sk-ant-…` (default, no extra setup).
   - **OpenAI API Key** — paste `sk-…` (create at
     [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
     with *All* permissions, not *Restricted*).
   - **Codex OAuth** — click the Login button and complete the ChatGPT
     device flow. The token is written to the `evonexus_codex_auth`
     volume and persists across restarts.

5. **Wait ~30 seconds.** The telegram and scheduler services detect the
   key on their next polling cycle and start running.

6. **Integrations.** Configure channels and external APIs from
   `/integrations`. Values land in the same config volume and are picked
   up at container startup. After changing something the telegram daemon
   uses, force-restart that service in Portainer
   (`docker service update --force evonexus_telegram`).

## Troubleshooting

### Terminal says "Could not reach terminal-server"

Confirm the dashboard container logs show both processes on boot:

```
[start-dashboard] terminal-server on :32352, Flask on :8080
🚀 Terminal server running at http://localhost:32352
 * Running on http://127.0.0.1:8080
```

If only Flask is running, the image is an old build — redeploy with the
latest tag. Also verify that the Traefik router includes the
`evonexus_terminal_strip` middleware (visible in the Traefik dashboard
if you have one enabled).

### telegram / scheduler crash-looping

They should not crash — they wait for `ANTHROPIC_API_KEY` via the
entrypoint's poll loop. If they exit immediately, the image predates
the bootstrap entrypoint. Redeploy with a current tag.

### Volumes lost after stack redeploy

Portainer preserves named volumes across stack updates by default. If
you removed and recreated the stack, data in `evonexus_config`,
`evonexus_workspace`, etc. is gone unless you backed them up. Confirm
with `docker volume ls` on the manager.

## What is NOT changed from the main codebase

The Swarm overlay is purely additive. Every upstream file is unmodified
and continues to support the VPS and local Compose paths:

- `Dockerfile`, `Dockerfile.dashboard`, `docker-compose.yml`
- `setup.py`, `install-service.sh`, `Makefile`
- `pyproject.toml`, `uv.lock`
- All Python, React, skills, agents, ADWs, configs

Swarm-specific files added by this overlay:

- `Dockerfile.swarm`, `Dockerfile.swarm.dashboard`
- `entrypoint.sh`, `start-dashboard.sh`
- `evonexus.stack.yml`
- `.github/workflows/docker-publish.yml`
- `README.swarm.md` (this file)
