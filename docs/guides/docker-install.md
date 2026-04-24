# Installing EvoNexus with Docker

The fastest way to run EvoNexus on any machine — Linux, macOS (Intel or Apple Silicon), Windows with WSL2, or a bare VPS. Pulls official images from Docker Hub, no source checkout or build step required.

## TL;DR

```bash
curl -O https://raw.githubusercontent.com/EvolutionAPI/evo-nexus/main/docker-compose.hub.yml
docker compose -f docker-compose.hub.yml up -d
open http://localhost:8080
```

The setup wizard on first boot asks for a provider key (Anthropic, OpenAI or Codex). Everything else — integrations, Telegram, Stripe, Omie, Bling, Asaas, etc. — is configured through the UI.

---

## Prerequisites

- **Docker Engine 24+** with Docker Compose v2. [Install on Mac / Windows / Linux](https://docs.docker.com/engine/install/).
- **4 GB RAM minimum** (8 GB recommended — the Claude CLI and embedding models can be hungry).
- **2 GB free disk** for images + a few hundred MB for data volumes.
- An **Anthropic API key** (or OpenAI / ChatGPT Codex account) — you'll paste this into the wizard on first boot.

No Node.js, Python, or any SDK needs to be installed on the host. The images ship everything.

### Multi-arch — ARM64 works out of the box

Images are published as multi-arch manifests (`linux/amd64` + `linux/arm64`), so:

- **Apple Silicon** (M1/M2/M3 Macs) — pulls `arm64` natively, no Rosetta overhead.
- **AWS Graviton**, **Oracle Cloud ARM free tier**, **Raspberry Pi 4/5** — pulls `arm64` natively.
- **Traditional x86_64 servers** — pulls `amd64` as usual.

You never pass `--platform`. Docker picks the right arch from the manifest list.

---

## Step 1 — Get the compose file

Download the ready-to-run compose file. It pulls images from `evoapicloud/evo-nexus-{dashboard,runtime}` on Docker Hub — no git clone required.

```bash
curl -O https://raw.githubusercontent.com/EvolutionAPI/evo-nexus/main/docker-compose.hub.yml
```

Open it — it's commented and self-explanatory. Key things to know:

- **3 services**: `dashboard` (Flask + React + terminal), `telegram` (bot listener), `scheduler` (automated routines). Telegram and scheduler are optional — remove them from the file if you don't need them.
- **Ports exposed**: `8080` (dashboard UI + API) and `32352` (terminal WebSocket).
- **Volumes**: 6 named volumes that survive `docker compose down` and hold everything the UI configures.

---

## Step 2 — Start the stack

```bash
docker compose -f docker-compose.hub.yml up -d
```

First run downloads the images (~800 MB total, a few minutes depending on your connection). Subsequent boots are instant.

Check that all 3 services are up:

```bash
docker compose -f docker-compose.hub.yml ps
```

You should see:

```
NAME                   STATUS
evonexus-dashboard     Up (healthy)
evonexus-telegram      Up
evonexus-scheduler     Up
```

If the dashboard takes longer than 30 seconds to go healthy, check logs:

```bash
docker compose -f docker-compose.hub.yml logs -f dashboard
```

---

## Step 3 — Open the UI and run the setup wizard

Open **http://localhost:8080** in your browser. You'll see the first-boot wizard:

1. **Create admin user** — username + password for the dashboard itself.
2. **Pick a provider** — paste your Anthropic API key, OpenAI key, or click "Login with ChatGPT" for Codex OAuth.
3. **Optional integrations** — Telegram bot token, Stripe, Omie, etc. Everything else is optional and can be added later.

Once you save a provider key, the `telegram` and `scheduler` services (which were waiting in a 30s poll loop) pick it up and start working. No restart needed.

---

## Step 4 — Everyday commands

| Action | Command |
|---|---|
| View logs | `docker compose -f docker-compose.hub.yml logs -f` |
| Restart | `docker compose -f docker-compose.hub.yml restart` |
| Stop | `docker compose -f docker-compose.hub.yml down` |
| Stop + wipe data | `docker compose -f docker-compose.hub.yml down -v` ⚠️ |
| Update to latest | See [Updating](#updating) below |

---

## Updating

### Pull the latest stable image

```bash
docker compose -f docker-compose.hub.yml pull
docker compose -f docker-compose.hub.yml up -d
```

Your data volumes are preserved — everything you configured through the UI stays intact.

### Pin to a specific version

Edit `docker-compose.hub.yml` and replace `:latest` with `:vX.Y.Z` (e.g. `:v0.30.4`). Available tags: https://hub.docker.com/r/evoapicloud/evo-nexus-dashboard/tags

Rolling back is just bumping the tag down and running `pull && up -d`.

---

## Backup and restore

Your configuration, workspace files, memory, and agent state live in 6 named volumes. To snapshot them all:

```bash
# Backup
mkdir -p evonexus-backup
for vol in config workspace dashboard_data memory adw_logs agent_memory; do
  docker run --rm \
    -v evonexus_${vol}:/src:ro \
    -v "$PWD/evonexus-backup":/dst \
    alpine tar czf /dst/${vol}.tgz -C /src .
done

# Restore on a fresh host
for vol in config workspace dashboard_data memory adw_logs agent_memory; do
  docker volume create evonexus_${vol}
  docker run --rm \
    -v evonexus_${vol}:/dst \
    -v "$PWD/evonexus-backup":/src:ro \
    alpine tar xzf /src/${vol}.tgz -C /dst
done
```

---

## Advanced: passing secrets via environment variables

The default flow is "configure everything through the UI, which writes to a persisted `.env` inside the `evonexus_config` volume." Most users should stick with that.

If you prefer to keep secrets out of the volume (for CI/CD, Vault, Doppler, or immutable infra), you can pass any env var directly in the compose file. They **take precedence** over the volume's `.env`:

```yaml
services:
  dashboard:
    image: evoapicloud/evo-nexus-dashboard:latest
    environment:
      - TZ=America/Sao_Paulo
      - EVONEXUS_PORT=8080
      # Bypass the UI — pass keys directly
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}      # read from shell or .env.compose
      - EVONEXUS_SECRET_KEY=${EVONEXUS_SECRET_KEY}
      - KNOWLEDGE_MASTER_KEY=${KNOWLEDGE_MASTER_KEY}
    env_file:
      - ./secrets.env   # or load from a file — this file is NOT inside the volume
```

Tradeoffs:

- **Pro**: secrets live with the infrastructure, not the user-editable volume. Rotate by redeploying.
- **Con**: the Providers page in the UI will still work (and will write to the volume's `.env`), but any value you pass via `environment:` wins. This can be confusing for non-technical users.

For the absolutely purist flow, pass `REQUIRE_ANTHROPIC_KEY=0` on the telegram and scheduler services so they don't wait for a UI-saved key — they'll just use whatever is in `environment:`.

---

## Advanced: Docker Secrets

Every env var also supports a `_FILE` counterpart. Point it at a file, and the entrypoint reads the content into the var at boot. This works natively with Docker Secrets in Swarm mode:

```yaml
services:
  dashboard:
    environment:
      - ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_key
    secrets:
      - anthropic_key

secrets:
  anthropic_key:
    external: true
```

Additionally, every file under `/run/secrets/` is auto-discovered: `/run/secrets/anthropic_api_key` → sets `ANTHROPIC_API_KEY` if it's not already set.

---

## Running on a VPS with a public domain

For production on a single-host VPS (not a Swarm cluster), put a reverse proxy in front of `docker-compose.hub.yml`. The simplest path is Caddy:

```yaml
# Caddyfile next to docker-compose.hub.yml
evonexus.example.com {
    reverse_proxy /terminal/* localhost:32352
    reverse_proxy localhost:8080
}
```

Then `caddy run` and you have HTTPS with automatic Let's Encrypt certificates pointing at your EvoNexus stack.

For Docker Swarm with Traefik, see [README.swarm.md](https://github.com/EvolutionAPI/evo-nexus/blob/main/README.swarm.md) and [`evonexus.stack.yml`](https://github.com/EvolutionAPI/evo-nexus/blob/main/evonexus.stack.yml).

---

## Troubleshooting

### Dashboard port 8080 is already in use

Another service on your host is bound to 8080. Change the host port in the compose file:

```yaml
ports:
  - "9090:8080"     # host 9090 → container 8080
```

Then open `http://localhost:9090`.

### Telegram / scheduler logs say "waiting for ANTHROPIC_API_KEY"

Expected. These services poll `.env` every 30s. Configure a provider key in **Dashboard → Providers** and they'll pick it up automatically — no restart needed.

### Services keep restarting after `up -d`

Check logs:

```bash
docker compose -f docker-compose.hub.yml logs --tail=50 <service>
```

99% of the time this is either (a) port conflict, (b) volume mount permission issue (rare, usually only on SELinux-enabled hosts — add `:Z` to volume mounts), or (c) disk full.

### Fresh install doesn't show any agents / skills on `/agents` or `/skills`

On a brand-new volume, the APIs return `{"error": "Setup required", "needs_setup": true}` until the wizard completes. Open http://localhost:8080 and complete the wizard first.

### Want to see what's inside the running container

```bash
docker compose -f docker-compose.hub.yml exec dashboard bash
```

Useful paths inside the container:

- `/workspace/config/.env` — everything the UI saves (ports, keys, integrations)
- `/workspace/workspace/` — generated artifacts (reports, dashboards, daily logs)
- `/workspace/memory/` — long-term memory the agents write
- `/workspace/.claude/agent-memory/` — per-agent persistent state
- `/workspace/ADWs/logs/` — routine execution logs (JSONL)

---

## Uninstall

```bash
docker compose -f docker-compose.hub.yml down -v
```

The `-v` flag also deletes the named volumes — **all your configuration and data is gone**. Skip the flag to keep volumes around for a future reinstall.

---

## See also

- [Updating EvoNexus](./updating.md) — version bumps across all install methods
- [README.swarm.md](https://github.com/EvolutionAPI/evo-nexus/blob/main/README.swarm.md) — production Swarm / Portainer deployments with Traefik
- [Environment variables reference](../reference/env-variables.md) — every variable the image recognizes
