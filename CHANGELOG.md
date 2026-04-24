# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.32.1] - 2026-04-24

Patch release fixing a `tsc -b` strict-mode type error in `PluginDetail.tsx` that broke fresh frontend builds (`npm run build` fails with TS2322 on `manifest['description']`). Local incremental builds passed because `.tsbuildinfo` cached the file as clean; fresh installs hit the error on first compile.

### Fixed

- **`PluginDetail.tsx`** — narrow `manifest['description']` with `typeof === 'string'` before using it as a truthy check and JSX child. Previously `Record<string, unknown>` lookup was cast only in the `<dd>` body, not in the conditional, so `tsc -b` rejected the truthy check as `unknown` in JSX.

## [0.32.0] - 2026-04-24

Minor release introducing the **Plugin System v1** — a full extensibility layer with 15 capabilities, pre-install security scanning, per-capability toggles, update diff previews, and a reference plugin (`pm-essentials`). Ships alongside a security-hardening pass (PRD #37) and a batch of community-reported fixes.

### Added

- **Plugin System v1 — 15 capabilities, security gate, reference plugin (#41)** — end-to-end extensibility: Pydantic-validated manifests, git/zip/local install with SHA-256 integrity check, semver-aware migration runner with rollback, atomic file ops, Claude Code hooks dispatcher (PreToolUse / PostToolUse / Stop / SubagentStop) with per-plugin SQLite circuit breaker, and crash recovery for orphaned installs. Capabilities include agents, skills, commands, rules, routines, heartbeats, widgets, readonly_data, writable_data, claude_hooks, goals, tasks, triggers, MCP servers and custom UI pages. Plugin-contributed rows are tagged with `source_plugin` across `tickets`, `projects`, `goals`, `missions`, `goal_tasks` and `triggers` so uninstall cleans them without touching user data.
- **Plugins REST API + dashboard UI** — full CRUD (`GET/POST/PATCH/DELETE /api/plugins`), curated marketplace listing, upload endpoint (ZIP / tar.gz, 20 MB cap, zip-slip guard), preview-before-install flow, audit log, and per-plugin widget limits per tier (essential / standard / power). New `/plugins` page with marketplace grid, install wizard (source → security scan → config → confirm), plugin detail with widgets, capabilities toggles, MCP banner, and Update button with diff preview (Wave 1.2). New `/mcp-servers` system page aggregating `~/.claude.json` entries grouped by plugin / native with masked env values.
- **Plugins CLI (`cli/src/commands/plugin.mjs`)** — `plugin init` (scaffold from template), `plugin install <source>`, `plugin list`, `plugin uninstall <slug>`, `plugin update <slug>`. Starter template under `cli/templates/plugin-skeleton/` ships with a pre-filled `plugin.yaml`, sample agent, and README.
- **Plugin security scan (Wave 2.5)** — hybrid regex + LLM scanner with 13 pattern categories, 57-domain whitelist, anti-hallucination guard, 7-day cache in `plugin_scan_cache`, and an APPROVE / WARN / BLOCK adaptive button with admin BLOCK override. New `plugin-security-scan` skill exposes the semantic scanner.
- **Plugin MCP servers (Wave 2.3)** — plugins can declare MCP servers with command whitelist and shell-metachar block; 6-layer atomic write to `~/.claude.json` with flock, timestamped backups (retention 10) and drift detection (name + args_hash match). UI shows a restart-Claude-CLI callout on install and an MCP diff section in update preview.
- **Plugin integrations (Wave 2.2r)** — plugins can declare env-var-based integrations with optional HTTP health checks running as in-process heartbeats (zero Claude CLI overhead). New Plugin Integrations section on `/integrations` with schema-driven configure modal and secret masking.
- **Plugin custom UI (Wave 2.1)** — plugins can contribute React pages mounted at `/plugins-ui/:slug/*`, sidebar groups, and writable SQLite resources with column allowlist + jsonschema validation. `window.EvoNexus` SDK injected post-login for plugin frontends.
- **Plugin per-capability toggles (Wave 1.1)** — granular ON/OFF per capability without uninstalling the plugin. Disable cascades to `.claude/{agents,skills,commands}/plugin-{slug}-*` (rename to `.disabled`), routines skipped by the scheduler, hooks and heartbeats skipped by their dispatchers.
- **Plugin management skills** — `plugin-install`, `plugin-list`, `plugin-uninstall`, `plugin-update`, `plugin-marketplace`, `plugin-health` expose plugin operations to all agents.
- **`agent_meta_seed.py`** — 38-agent seed served via `GET /api/agent-meta` (in-process cache + invalidation). Plugin agents contribute metadata via `manifest.metadata.icon` + per-agent avatars; frontend registry (`agent-meta.ts`) is hydrated once post-login and plugin cards render custom icons with img fallback.
- **Merged routines listing** — `/api/routines` now merges declared routines (via `discover_routines()`) with execution metrics so newly installed plugin routines and unrun core routines show up in `/routines` with zeroed metrics.
- **`EVONEXUS_DEV=1`** — toggles Flask's auto-reloader (`debug=True`, `use_reloader=True`) for backend development. Default remains off; production uses systemd/docker.
- **`docker-compose.proxy.yml` (#45)** — sibling compose file for reverse-proxy hosts (Coolify, Dokploy, Traefik, Caddy): uses `expose:` instead of `ports:` so the proxy owns external traffic while containers stay reachable by name inside the Docker network. Volumes named identically to `hub.yml` for no-loss migration.
- **`docs/knowledge-database.md` (#46)** — provider cheat-sheet for Supabase / Neon / Railway PgBouncer gotchas (port 5432 vs 6543, Supabase IPv6-only edge case, error reference table). Inline hint added under the Knowledge connections wizard input.
- **`scripts/clean-history.sh` (#26)** — safe-by-default dry-run helper that clones the remote as `--mirror` and previews removal of ~283 MB of orphaned PNG avatar blobs via `git filter-repo --path-regex`. Verifies develop/main HEAD trees are byte-identical and all tags preserved before the maintainer force-pushes. `CONTRIBUTING.md` gains the partial-clone recipe (`git clone --filter=blob:none`) so contributors download ~10 MB instead of ~290 MB in the interim.

### Changed

- **`/api/health/deep` now requires an authenticated admin session** (PRD security hardening, #37) — previously leaked filesystem paths, provider identity, secret-key source, and error details to unauthenticated callers. `/api/health` is now a minimal public liveness probe returning status only. Tooling scraping `/api/health/deep` for internals must authenticate.
- **Frontend route splitting (#37)** — top-level route bundles are code-split so the main chunk size drops substantially on first load.
- **Plugin install sources restricted (hardening)** — `resolve_source` now rejects local filesystem paths, `file://`, `ssh://` and non-HTTPS schemes with a clear `ValueError`. Only `github:`, `https://` tarballs, or uploaded ZIP / tar.gz archives are accepted. Closes the doc/code mismatch where the skill promised rejection but the code accepted anything via `Path(s)`.
- **Plugin triggers ship disabled by default** — regardless of YAML value, unless explicitly `"true"`, so a malicious plugin cannot auto-fire hooks on install.
- **Telegram notifications moved from skills to routines** — `run_skill(notify_telegram=True)` appends a one-shot send instruction at the end of the prompt, guaranteeing exactly one send per execution (was duplicated when skills embedded the instruction themselves). Skills cleaned: `prod-end-of-day`, `prod-good-morning`, `pulse-faq-sync`, `pulse-daily`.
- **Integration status verifies all declared env keys (#49)** — `list_integrations` previously checked only a single key per entry, so Evolution API / Evolution Go / Evo CRM showed as "configured" with only the token set (URL missing) or vice-versa. Schema is now `keys: list[str]`; an integration is considered configured only when every declared key is non-empty.
- **Bling integration keys corrected (#49)** — `BLING_ACCESS_TOKEN` (which existed nowhere in the repo) replaced with `BLING_CLIENT_ID` / `BLING_CLIENT_SECRET` used by the real OAuth2 flow. **Migration:** users who set `BLING_ACCESS_TOKEN` manually must run `make bling-auth` to obtain the OAuth credentials.
- **Omie integration now requires both `OMIE_APP_KEY` and `OMIE_APP_SECRET`** — backend was only checking the key, so a half-configured Omie appeared green (#49).

### Fixed

- **`scripts/start-services.sh` no longer kills unrelated processes (#18)** — `pkill -f 'python.*app.py'` matched every `app.py` on the host, killing unrelated services. Replaced with an explicit pinned match on the venv interpreter + absolute script path, and TCP 8080 (or `EVONEXUS_PORT`) is now freed directly via `fuser` / `lsof` before restart — falls back to `lsof -ti tcp:$PORT | kill` when `fuser` is absent (BSD-ish / macOS).
- **Terminal client detects RFC1918 + CGNAT hostnames as local (#35)** — previously only `localhost` / `127.0.0.1` were treated as local, so bare-metal installs behind no reverse proxy fell back to `/terminal` on the same origin, which didn't exist. Heuristic widened to RFC1918 (`10/8`, `172.16/12`, `192.168/16`), RFC6598 CGNAT (`100.64.0.0/10`, common on Brazilian VPS), link-local (`169.254/16`), IPv6 loopback (`::1`), and IPv6 link-local (`fe80:`). New `VITE_TERMINAL_URL` explicit override for edge cases (reverse proxy on a private IP). `deriveWsBase` rewritten to use `URL()` instead of a regex that silently dropped the `https` branch and mangled uppercase schemes.
- **Plugin uninstall sweeps leftover `plugin-{slug}-*` files** — `reverse_remove_from_manifest` walks `.install-manifest.json`, but if that manifest is missing / corrupt / predates the name-rewrite change, files in `.claude/{agents,skills,rules,commands}/` stayed behind and the next install hit 409. An unconditional sweep runs after the manifest pass.
- **Plugin install seeds an anchor mission per plugin** — plugins seeding projects without `mission_id` left them orphaned and invisible in `/goals`. Installer now synthesizes one mission per plugin (`plugin-{slug}-root`) when the YAML doesn't declare one and links all orphan projects to it. Missions get `source_plugin` too so uninstall cleans them.
- **Plugin widget listing reads `ui_entry_points.widgets`** — was reading a non-existent `manifest.manifest.widgets` key and returning `[]` regardless of mount point.
- **Plugin install falls back to first `migrations/*.sql`** when `migrations/install.sql` is missing, so plugin authors using the `NNN_description.sql` convention don't have to rename.
- **Plugin skills install as directory trees** — `copy_with_manifest` now copies `skills/<name>/` as a whole directory and rewrites the `name:` field inside `SKILL.md` to match the prefixed dirname, enforcing the Claude Code contract that `name` == filename. Agents / commands / rules similarly get their `name:` frontmatter rewritten.
- **Plugin `GET /api/plugins/<slug>/audit` endpoint** — was missing, so `PluginDetail.tsx` hit the SPA catch-all, got `index.html` back, and threw `Unexpected token '<'` on JSON parse.
- **Plugin heartbeat agent references get auto-prefixed** — plugins can declare `assignee_agent: pm-nova` instead of the full `plugin-pm-essentials-pm-nova`; the installer rewrites bare references to match the prefixed file.
- **Plugin update endpoint now uses `resolve_source`** — works from `github:` / `https://` / uploaded path (was local-only).
- **`PATCH /api/triggers/<id>`** — the endpoint was simply missing.
- **Brain Repo sync no longer blocks HTTP requests** — new `brain_repo/job_runner.py` background executor serialises sync / milestone / bootstrap ops. `POST /sync/force`, `/sync/cancel`, `/tag/milestone` now enqueue jobs and return immediately; `GET /status` exposes job state. `/backups` and `/brain-repo` pages poll state and expose a Cancel button (cooperative `cancel_requested` flag checked between git steps).
- **`ticket_janitor.py`** — guards against `IsADirectoryError` on the health-check path that crashed on certain setups.

### Security

- **Pre-install plugin security scanning** — every plugin installed from an external source runs through a hybrid regex + LLM scanner before any file lands on disk. APPROVE / WARN / BLOCK with admin override + audit trail.
- **`/api/health/deep` requires admin** — see Changed.
- **Plugin install sources restricted to HTTPS / github / upload** — see Changed.
- **Plugin triggers disabled by default** — see Changed.

## [0.31.0] - 2026-04-24

Minor release introducing the **Brain Repo** — automatic GitHub versioning of workspace memory and customizations — plus a full onboarding wizard and a unified `/backups` page covering Local, S3 and Brain Repo destinations.

### Added

- **Brain Repo — GitHub versioning of `memory/`, `workspace/`, `customizations/` and `config-safe/`** — a dedicated private repo (`evo-brain-<username>`) is created or detected per user. A file watcher with 30s debounce persists every change to GitHub; daily, weekly and milestone snapshots can be restored via a streaming SSE engine. GitHub tokens are Fernet-encrypted at rest with `BRAIN_REPO_MASTER_KEY` (auto-generated at first boot); `connect`/`sync` endpoints return `500 CRYPTO_UNAVAILABLE` when encryption is unavailable — plaintext fallback was explicitly removed. A 21-pattern secrets scanner runs before every push (AWS / GitHub / Anthropic / OpenAI / Stripe / JWT / SSH, etc.) — matched files are deleted and never leave the machine.
- **Onboarding wizard** — post-account-creation flow replacing the cold-drop into Overview. First-time users pick an AI provider (Anthropic / OpenAI / OpenRouter / Codex OAuth) with per-provider sub-flows and optionally connect a brain repo; returning users can restore from any snapshot via a type-to-confirm safety gate. Includes an `OracleWelcomeBanner` on `/agents` (one-time, dismissable).
- **Unified `/backups` page** — single surface for Local ZIPs, S3 and Brain Repo with 3 destination cards, tabs (counts per source), import dropdown (upload `.zip` + pull from Brain Repo), contextual restore modal with SSE progress and a danger state when crypto is broken. 30s visibility-aware polling keeps watcher results fresh.
- **Brain Repo settings page** — `/settings/brain-repo` with status card (connected / pending / last_error / crypto danger), Sync-now, Create-milestone and Disconnect actions, fully i18n'd.
- **i18n** — ~270 new keys across `onboarding.*`, `restore.*`, `brainRepoSettings.*`, `backups.*`, `agents.welcomeBanner.*` (en-US / pt-BR / es), with identical key trees and natural translations.
- **CLI** — `setup.py` now prompts for brain-repo during initial setup and collects the PAT; `backup.py --target github` pushes a manual sync through the dashboard's own code path.
- **Dev environment** — `docker-compose.dev.yml` with live-reload backend + named volumes, `Dockerfile.dev` with `--legacy-peer-deps` and globally-installed `@anthropic-ai/claude-code` + `@gitlawb/openclaude`, CRLF-safe entrypoint scripts, and a step-by-step `DEV-SETUP.md`.

### Changed

- **`/api/backups/config`** extended with `brain_repo_configured`, `brain_repo` and `brain_crypto_ready` so the tabs UI renders in one round-trip.
- **`lib/api.ts`** — `buildError()` now extracts the JSON `error` / `description` / `message` from non-OK responses while preserving the status-code prefix, so existing `.includes('401')` callers keep working.
- **`useGlobalNotifications`** — HTTP health-probe before WS + visibility guard, eliminating the `WebSocket connection failed` console spam on pages that don't use the terminal.
- **`App.tsx`** — onboarding guard treats `null` state as "needs onboarding" and redirects accordingly.

### Fixed

- **Restore SSE crash on `execute_restore(install_dir=...)`** — call-site kwargs now match the function signature `(repo_url, ref, token, install_dir, include_kb, kb_key_matches)` 1:1; `install_dir` resolves to the workspace root (where the SWAP_DIRS are replaced), not the brain-repo clone. A new import-time signature check in `brain_repo/__init__.py` emits a CRITICAL log on drift so future regressions are visible at startup.
- **Watcher no-op on workspace changes** — `sync_force`, `tag_milestone` and the watcher now mirror `memory/workspace/customizations/config-safe` from workspace → brain-repo clone *before* `git add`, so `commit_all` actually sees the new files.
- **`start_brain_watcher` circular import** — now accepts the Flask app explicitly instead of importing it, eliminating the startup "current Flask app is not registered with this 'SQLAlchemy' instance" warning that silently disabled auto-sync.

## [0.30.4] - 2026-04-24

Patch release with a **P0 race-condition fix** in the container entrypoint plus a complete Docker install experience (ready-to-run compose + full tutorial).

### Fixed

- **Race condition on shared `/workspace/config` volume (P0)** — when `dashboard`, `telegram` and `scheduler` boot in parallel against the same named volume (the canonical Docker / Swarm / Portainer deploy pattern), the first-boot bootstrap raced on four operations:
  - `[ ! -f .env ] && cp .env.example .env` — one container wins, others crash with `File exists`. Scheduler died visibly on every fresh v0.30.3 deploy.
  - `[ ! -f ] && cp` for `providers.json` / `heartbeats.yaml` — same race.
  - `grep -q EVONEXUS_SECRET_KEY || echo … >> .env` — two processes both see "not found" and append **two different keys**; Flask picks one at random per request, invalidating sessions silently.
  - Same pattern for `KNOWLEDGE_MASTER_KEY` — silently corrupted Knowledge Base encryption, losing all configured DB connections on second boot.

  Fix: wrap the whole bootstrap in `flock` on a lockfile inside the shared volume, serializing all containers regardless of start order. Also added `cp -n` (no-clobber) as belt-and-suspenders. `flock` is part of `util-linux`, already present in both base images.

### Added

- **`docker-compose.hub.yml`** — ready-to-run compose file that pulls `evoapicloud/evo-nexus-*` from Docker Hub instead of building from source. Uses `depends_on: condition: service_healthy` to order the boot (dashboard first, then telegram/scheduler) — defense-in-depth on top of the flock fix. This is the recommended install for end users: `curl -O …docker-compose.hub.yml && docker compose -f docker-compose.hub.yml up -d`.
- **`docs/guides/docker-install.md`** — complete Docker install guide. Prerequisites (Docker Engine 24+ is the only requirement — image ships everything), one-command boot, first-boot wizard walkthrough, update flow (`docker compose pull && up -d`), backup/restore recipes, advanced section documenting how to pass secrets via `environment:` or Docker Secrets (for CI/CD and immutable-infra users who prefer to keep secrets out of the `.env` volume), running behind Caddy for public HTTPS, troubleshooting table.
- **Install method chooser in `docs/getting-started.md`** — table at the top comparing Docker vs CLI (`npx`) vs manual clone, with clear guidance on which to pick.

### Changed

- **`README.md`** — Docker promoted to Method 1 in Quick Start. Prerequisites split into two tracks (Docker-only vs CLI-flow), reflecting that the Docker image ships Claude Code, Python, Node, uv and `gh` baked in.
- **`docs/guides/updating.md`** — new `docker compose -f docker-compose.hub.yml pull && up -d` section documenting the Docker Hub upgrade path. Swarm examples bumped to `v0.30.4`.

## [0.30.3] - 2026-04-24

Patch release completing the Docker Hub migration: official images now ship as **multi-arch manifests** (`linux/amd64` + `linux/arm64`) so ARM hosts (Apple Silicon, AWS Graviton, Oracle Cloud ARM, Raspberry Pi, many modern VPS) can pull without platform overrides.

### Added

- **Multi-arch Docker images** — `.github/workflows/docker-publish.yml` now runs `docker/setup-qemu-action@v3` and passes `platforms: linux/amd64,linux/arm64` to `build-push-action`. Every published tag (`latest`, `vX.Y.Z`, `X.Y`, `X.Y.Z`, `main`, `sha-*`) ships a manifest list covering both architectures. The `node-pty` native addon compiles from source via node-gyp inside the arm64 builder, so there's no prebuilt-binary-per-arch concern.

### Removed

- **`evonexus.portainer.stack.yml`** — deleted the personal Portainer template from the repo root. It was a pre-configured stack for a specific host (`advancedbot.com.br`, `network_public` network) pointing at a fork's Docker Hub namespace (`marcelolealhub/*`), which could mislead new users into pulling from the wrong registry. The canonical template remains at [`evonexus.stack.yml`](https://github.com/EvolutionAPI/evo-nexus/blob/main/evonexus.stack.yml), which already supports Portainer/Traefik deployments and points at the official `evoapicloud/evo-nexus-*` images.

## [0.30.2] - 2026-04-23

Patch release focused on CI/distribution: Docker images now ship from the official `evoapicloud` namespace on Docker Hub (public, no auth required on Swarm managers), and the legacy dashboard-only workflow was removed to unblock the build pipeline.

### Changed

- **Docker images published to Docker Hub under `evoapicloud/`** — the Swarm workflow (`.github/workflows/docker-publish.yml`) now pushes `evo-nexus-runtime` and `evo-nexus-dashboard` to `docker.io/evoapicloud/*` instead of `ghcr.io`. Requires `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN` secrets on the repo. The `evonexus.stack.yml` template and `README.swarm.md` were updated to reference the public images — no `OWNER` placeholder to fill in, no `docker login` needed on Swarm managers.

### Removed

- **Redundant `dashboard.yml` workflow** — deleted `.github/workflows/dashboard.yml`, which built a Python+React-only dashboard image (`Dockerfile.dashboard`) that nothing consumes. The Swarm workflow already produces a strict superset (with terminal-server and both CLIs). This also fixes the build failure on `main` caused by a TypeScript peer-dep conflict in the legacy Dockerfile.

## [0.30.1] - 2026-04-23

Patch release focused on thread UX polish: session now swaps cleanly when switching threads via the sidebar, the agent is briefed explicitly about running inside a persistent thread (not a fresh one-shot session), the assignee dropdown stops hiding agents, and fresh installs no longer inherit Evolution-specific goal seed data.

### Fixed

- **Thread switch leaked previous conversation** — switching threads via the sidebar kept `threadSessionId` pinned to the old ticket and `<AgentChat>` kept rendering the old messages until a full page reload (or going back to `/topics` and entering again). Two fixes in `TicketDetail.tsx`: (1) a new effect resets `threadSessionId` whenever `ticket?.id` changes so the auto-init re-runs for the new ticket; (2) `<AgentChat key={ticket.id}>` forces a full remount so the WebSocket, message buffer and internal effects restart cleanly.
- **Topics assignee dropdown hid 18 of 38 agents** — the Assign-to-agent combobox in `/topics` sliced the filtered list at 20 items (`filteredAgents.slice(0, 20)`), silently dropping agents whose slugs come later alphabetically (from `m` onward). Removed the slice and bumped `max-h-48` to `max-h-72` so ~12 agents are visible at once without scrolling and all 38 are reachable.
- **Goals: Evolution-specific seed leaking into open-source installs** — `dashboard/backend/app.py` was seeding a hardcoded "Evolution Revenue $1M Q4 2026" mission with 3 projects (evo-ai, evo-summit, evo-academy) and 5 goals on first boot. Removed the seed block so new instances start empty. The `/goals` empty state now points users at the `/create-goal` skill instead of the misleading "Run the backend migration to seed initial data" message. Existing installations with the seed applied can clean it with `DELETE FROM goal_tasks; DELETE FROM goals; DELETE FROM projects; DELETE FROM missions;`.

### Changed

- **Thread context now always injected into the agent's system prompt** — when a thread session initialises, `TicketDetail.initThreadSession` always builds a "Thread Context" block explaining that the agent is running inside a persistent thread (not a fresh session): the thread title, description, assigned agent slug, default workspace folder, memory file path, summarization cadence, and resume behaviour. It also tells the agent **not** to re-invoke itself via the `Agent` tool (which was causing confusing `@zara-cs` calling `@zara-cs` patterns). Memory.md content is appended when present, so empty threads still get the full context and populated threads still surface prior-session knowledge. Respects the existing `!sdkSessionId` guard in `chat-bridge.js` — only injected on fresh sessions, not on `--resume`.

## [0.30.0] - 2026-04-23

Minor release adding a unified Activity Log — a single page aggregating execution history across routines, heartbeats and triggers so the user can answer "what did the system just do?" without visiting three separate pages.

### Added

- **`/activity` — Unified Activity Timeline** — new page aggregating execution history across all three automation primitives: scheduler routines, agent heartbeats, and event-based triggers. Presented as a reverse-chronological timeline (Linear / Vercel Logs / GitHub Actions style). Each row shows name + type badge + status pill (success / error / running) + duration + relative time. Click opens a right-side drawer (480px) with full output, metadata (started / finished / exit code / cost / tokens), and a "Open in dedicated page" link.
  - **Filters:** multi-select type chips (Routines / Heartbeats / Triggers), status dropdown (All / Success / Error / Running), period tabs (Today / 7d / 30d / All), debounced search (300ms, client-side).
  - **Auto-refresh:** 30s interval, paused automatically when the browser tab is in background (`visibilitychange`).
  - **Load more:** client-side pagination at 50 items per page.
  - **Accessibility:** drawer is `role="dialog"` `aria-modal="true"`, Escape closes, click-outside closes.
  - **Nav:** new sidebar item "Activity" (`Atividade` pt-BR / `Actividad` es) under the operations group. `View all →` on the Overview Routines card now points to `/activity` for a unified journey.
  - **Data sources:** reuses existing backend endpoints — `GET /api/routines/logs`, `GET /api/heartbeats/{id}/runs`, `GET /api/triggers/{id}`. Client-side aggregation (N+1 fetches via `Promise.all`) — acceptable for v1 volume; server-side aggregated endpoint can come later if needed.

### Fixed (same release)

- **Activity parser — real routine log shape** — initial parser was looking for `log.name` / `log.routine_name` / `log.status` / `log.exit_code`, which don't exist in `ADWs/logs/YYYY-MM-DD.jsonl`. Real shape is `{ timestamp, run, prompt, returncode, duration_seconds, input_tokens, output_tokens, cost_usd }`. Parser now reads `run` as the routine name (so rows show `good-morning`, `end-of-day`, etc. instead of `Unknown Routine`), derives status from `returncode`, and surfaces `cost_usd` / token counts / prompt preview in the drawer.

### Known limitations (v1)

- `/api/routines/logs` only accepts `?date=`, so the period filter (7d / 30d / All) affects heartbeats and triggers only — routines always show today. The routine log endpoint will need `from`/`to` params to honor longer periods; deferred to a follow-up.
- Client-side aggregation means timeline loads can do up to `1 + N + M` requests (1 for routines today, N for each heartbeat's last 10 runs, M for each trigger's detail). Fine under ~20 heartbeats/triggers, may need batching later.

## [0.29.3] - 2026-04-23

Patch release: fix the infinite page scroll in thread mode so the embedded chat behaves exactly like the agent chat (fixed input at the bottom, messages scroll inside the container), and harden `.gitignore` against nested `.claude/` folders that agents were accidentally creating from subdirectory cwds.

### Fixed

- **Thread mode — infinite page scroll** — `TicketDetail` in thread mode used `h-full` but the parent `<main>` in `App.tsx` only applied `h-screen overflow-hidden` for `/agents/:id` and `/workspace/*` routes. Any route falling into the default branch used `overflow-auto` without a fixed height, so the embedded `AgentChat` grew with its message list and pushed the input field off-screen. Fix: add `isTicketDetail` matcher to `App.tsx` so `/tickets/:id` joins the fixed-viewport branch; in `TicketDetail.tsx` the non-thread (document) view gains its own `h-full overflow-auto` wrapper with the original padding to preserve its vertical-document layout. Thread mode now mirrors the agent chat exactly.

### Changed

- **`.gitignore` hardening — nested `.claude/` in subdirectories** — agents running from `dashboard/frontend/` (e.g., `cd dashboard/frontend && npm run build`) were creating `dashboard/frontend/.claude/agent-memory/` relative to cwd instead of writing to the canonical `.claude/` at the repo root. Content was already ignored by the existing `.claude/agent-memory/` rule, but the `.claude/` folder itself showed up untracked in editors. Added `**/.claude/agent-memory/` and `dashboard/*/.claude/` patterns to block this at any depth.

## [0.29.2] - 2026-04-23

Patch release: in-app toasts and confirm dialogs replacing 47 native `alert()`/`confirm()` calls, agent avatars in the Topics list, plus fixes for PR #30 (provider routing + docker) and the archive endpoint.

### Added

- **In-app Toast system (`useToast`)** — stackable notifications in the bottom-right corner (max 5), auto-dismiss 4s, variants `success` / `error` / `warning` / `info`. Replaces all `window.alert()` usage in the dashboard with a consistent, non-blocking pattern in the EvoNexus dark tone. Zero new dependencies (pure CSS keyframes + Context API).
- **In-app Confirm dialog (`useConfirm`)** — promise-based modal with `default` / `danger` variants, keyboard support (Enter confirms, Escape cancels), focus on Cancel for danger variant (safer default). Replaces all `window.confirm()` usage.
- **Agent avatars in `/topics` list** — threads now show the assigned agent's avatar (24px, same as the sidebar) instead of a generic green chat icon, matching the visual language of `ThreadsSidebar`. Shared `AgentIcon` component extracted from the sidebar for reuse.

### Changed

- **47 UX call sites migrated from native dialogs to in-app components** across `AgentChat`, `ChatSessionList`, `Backups`, `Heartbeats`, `Roles`, `Scheduler`, `Systems`, `Tasks`, `TicketDetail`, `Topics`, `Triggers`, `Users`. All messages translated to pt-BR where they were in English.
- **Provider config centralized** (PR #30) — shared `provider-config.js` helper in the terminal-server centralizes loading, env var allow-listing, and model capability detection (`code` vs `chat`). Reduces duplication between `chat-bridge.js` and `claude-bridge.js`.
- **Chat uses OpenAI-compatible streaming for non-Anthropic providers** (PR #30) — `/chat/completions` streaming so chat-completion style models (GPT, Gemini, custom OmniRouter) work in dashboard Chat. Anthropic keeps the existing Agent SDK flow.
- **Terminal enforces code-only models for non-Anthropic providers** (PR #30) — chat-completion models are now blocked in the Terminal with a clear error directing the user to the Chat instead.
- **Telegram notification helper for ADW routines** (PR #30) — `run_skill(..., notify_telegram=True)` appends a deterministic notification instruction to the skill prompt so end-of-day and good-morning routines emit exactly one Telegram message via the MCP `reply()` call. `ADWs/runner.py` also exposes a `send_telegram()` helper that posts directly via Bot API as a fallback.

### Fixed

- **Archive thread endpoint — 500 on re-archive** — `shutil.move` was raising `OSError` when `memory/threads/_archive/{ticket_id}/` already existed from a previous partial archive. Now checks for existing path and falls back to a timestamped suffix; tombstone write is best-effort and wrapped in try/except; the endpoint surfaces a proper JSON error instead of a bare 500.
- **Docker dashboard container starts reliably** (PR #30) — `npm install --legacy-peer-deps` in `Dockerfile.swarm.dashboard` avoids peer-dep install failures on fresh rebuilds (same pin already applied to the non-Docker install).

## [0.29.1] - 2026-04-23

Patch iterating on the v0.29.0 thread-areas feature: UI rebrand, navigation polish, and fixes identified by the post-release verification pass.

### Changed

- **Renamed "Issues" → "Topics" across the UI** — the feature evolved from a pure issue tracker into a container for both tasks and persistent chat threads, so the label no longer fit. Page file renamed `Issues.tsx` → `Topics.tsx`, route moved `/issues` → `/topics` with a 302 redirect preserving old bookmarks, sidebar nav item updated, breadcrumb `Topics / {title}`, i18n updated across 3 locales: en `Topics`, pt-BR `Tópicos`, es `Temas`. Backend (`tickets` table, `/api/tickets/*` endpoints, `Ticket` model) intentionally unchanged — pure UX rebranding, zero data migration.

### Added

- **Threads sidebar — navigate between chat threads without leaving the conversation** — when viewing a ticket in thread mode, a 280px sidebar now appears on the left listing all threads, grouped by agent (Clawdia, Kai, Flux…), with active/archived split. Active thread is highlighted with a green left border. Toggle button collapses to 48px (persisted in localStorage). Each item shows title + relative time (`há 2h`, `ontem`, `3d`). On mobile (<768px), sidebar becomes a slide-in drawer triggered by a `PanelLeft` icon — 85vw from the left with backdrop, Escape/click-outside/close to dismiss, `role=dialog` accessibility. Desktop and mobile share the same `ThreadsSidebar` component via an `asDrawer` prop; drawer lazy-mounts to avoid double-fetch. Pure CSS transitions, zero new dependencies.
- **Create workspace folders from the Convert to Thread modal** — `+ Nova pasta` button inline in the folder dropdown opens an input accepting `[a-z0-9-]+` names (2-50 chars). Pressing Enter or clicking Create fires `POST /api/workspace/subfolders`; new folder appears in the dropdown pre-selected, no page reload. Backend validates name pattern, defends against path traversal, returns 409 if folder exists, 201 with `{name, path, full_path}` on success.

### Fixed

- **`convert-to-thread` is now idempotent** — calling the endpoint on a ticket that is already a thread returns 200 with the current ticket state instead of 409. Workspace path conflict (different path supplied) still returns 409 `workspace_path_conflict` with both paths in the error body. Prevents spurious errors when the UI double-fires the conversion.
- **`turn-completed` is now race-safe monotonic** — uses `UPDATE ... WHERE message_count < :n` with `n = current + 1`, so concurrent calls with the same base value only increment once (second call is a silent no-op). Implements option (a) from the summary-trigger ADR without extra IO.
- **Convert to Thread modal warns about agent immutability** — orange warning banner before the Convert button: "Após converter, o agente desta thread não poderá ser alterado. Crie uma thread nova para trocar de agente." Consistent with the existing `archived` badge style.
- **Archived threads are read-only in the UI** — when a thread's status is `archived`, the `TicketDetail` shows a "📦 Thread arquivada — read-only. [Unarchive]" banner above the chat, disables interaction on the embedded `AgentChat` via `pointer-events-none opacity-60`, and the Unarchive button calls `POST /api/tickets/:id/unarchive-thread` to reactivate. Previously the UI allowed typing and only the backend rejected it.

## [0.29.0] - 2026-04-23

### Added

- **Thread Areas — persistent chat threads with isolated memory** — tickets can be converted to "thread mode", turning them into a chat surface embedded in `TicketDetail` (via `AgentChat`). Each thread has a dedicated agent (immutable after conversion), a default `workspace_path`, and a curated `memory.md` at `memory/threads/{ticket_id}/memory.md` that persists across sessions. Solves context degradation in long conversations: fixed scope (1 agent × 1 area) + periodic summarization + `--resume` to keep conversation alive across days. Canonical use case: 1 financial agent × N companies, each as an isolated thread. Zero new tables — extends `tickets` with 5 columns (`workspace_path`, `memory_md_path`, `thread_session_id`, `message_count`, `last_summary_at_message`). New endpoints: `PATCH /api/tickets/:id/convert-to-thread` (idempotent), `POST /api/tickets/:id/turn-completed` (monotonic with `UPDATE WHERE message_count < :n`), `POST /api/tickets/:id/archive-thread` and `/unarchive-thread`, `GET /api/tickets/counts`, `GET /api/workspace/subfolders`, plus `display_mode` filter on list. UI: `/issues` splits into "Threads" (💬) and "Issues" sections; `TicketDetail` renders `AgentChat` when the ticket is a thread; modal guards agent immutability; archived threads show read-only banner with Unarchive action. Summary subsystem: `summary_worker.py` generates a new dated section in `memory.md` every 20 turns; `summary_watcher.py` heartbeat safety net (disabled by default) recovers turns missed when the browser tab closes mid-conversation (Option D + B hybrid per ADR).
- **Database integrations — Postgres, MySQL, MongoDB, Redis** — four new skills (`db-postgres`, `db-mysql`, `db-mongo`, `db-redis`) let the user query and explore databases configured via `.env` (`DB_POSTGRES_N_*`, `DB_MYSQL_N_*`, `DB_MONGO_N_*`, `DB_REDIS_N_*`). Integrations UI gains a full-page database section for connection management. Backend route `dashboard/backend/routes/databases.py` wires the dashboard to the skills. Documented in `docs/integrations/databases.md`.

### Fixed

- **Heartbeats — accept `system` sentinel for infra-only heartbeats** — allows heartbeats without an assigned agent (e.g., `summary-watcher`) to register without tripping validation.
- **VPS install — survive first reboot** — scheduler, start-services and firewall now persist across reboots on fresh VPS installs; prior setups would silently fail to come back up.

## [0.28.0] - 2026-04-22

### Added

- **Landing page reframe — work narrative over feature inventory** — hero rewritten across all three locales: EN `"Your AI team, pre-assembled."` / PT `"Seu time de IA, já montado."` / ES `"Tu equipo de IA, ya armado."`. New section "How Work Gets Done" with 4-beat narrative (Set the goal → Agents that know their lane → Docs your agents actually read → Every action, traceable). New standalone sections for **Knowledge Base** (hybrid RAG + BYO Postgres) and **Heartbeats** (cron for agents, with guardrails) — surfacing v0.25+v0.27 features that had been invisible on the LP. Full proposal doc in `workspace/marketing/[C]lp-reframe-v1.md` (gitignored).
- **Setup wizard i18n (pt-BR / en-US / es)** (#25) — `make setup` now asks for wizard language first (1=EN / 2=PT / 3=ES), then translates every user-visible message: banner, section headers, field prompts, progress lines, success/failure, final next-steps. Non-interactive contexts (`EVO_NEXUS_AUTO_INSTALL=1`, CI, pip backend) silently keep `en-US`. 153 keys per bundle, exact parity verified.
- **Auto-relocate install for non-root service user** (#25) — detects when `SUDO_USER` cannot read+enter the install dir via `su - <user> -c 'test -x ... && test -r setup.py'`. If not, copies project to `/home/<user>/evo-nexus`, chowns, updates the global `WORKSPACE`, and `chdir`s there. Every later step (uv sync, npm install, systemd `WorkingDirectory`, ownership fix) sees the new location automatically. Fixes the regression where direct-to-root installs with `SUDO_USER=ubuntu` silently failed on systemd unit start.
- **Tool bootstrap for non-root service user** (#25) — new `_ensure_user_has_tools(user)` bootstraps `uv`, `claude`, `openclaude` into `~/.local/` for any non-root service user (mirrors what the `evonexus` auto-created branch already did). Idempotent — skips tools already present.

### Changed

- **Image optimization — 265 KB saved across 50 assets** (#25) — PNG brand assets converted to WebP (quality 85, method 6), existing WebP avatars re-encoded at quality 82. Sweep over `dashboard/frontend/public/`, `public/`, `site/public/`. Before: 2,302 KB. After: 2,037 KB. Favicons intentionally kept as PNG (cross-browser WebP favicon support still patchy; files already 4 KB).
- **`dashboard/frontend/.npmrc`** (#25) — `legacy-peer-deps=true` with explanatory comment, so `npm install --silent` inside `setup.py` returns 0 despite `react-i18next@15` declaring peer `typescript@^5` while the dashboard pins `typescript@~6`.
- **Landing page section reorder** — hero → How Work Gets Done → Agents → Knowledge → Heartbeats → Screenshots → Integrations → Quick Start. Removed the 10-card "Features Grid" and the 6-card "Why EvoNexus?" section (dead post-reframe — the same arguments now live as prose in "How Work Gets Done"). Merged the 3-step "How It Works" into the Quick Start section above the terminal block. Removed the redundant Social Proof stats bar (numbers already in hero stats pills).
- **Hardcoded counts updated** — README, LP, and dashboard stats now show **190+ skills** (was 175+) and **25 integrations** (was 23-24 depending on location — inconsistent). Numbers verified against `ls .claude/skills/` (190 dirs) and `.claude/rules/integrations.md` (25 entries).
- **Landing page copy — editorial pass in pt-BR / en / es** — rewrote every user-facing string as if each locale were the original language, not a translation. Replaced abstract nouns with active verbs, dropped anglicisms (`pré-montado` → `já montado`; `pre-ensamblado` → `ya armado`), killed SaaS clichés (`never sleeps` → `never left hanging` / `nunca fica sem resposta` / `nunca se queda sin respuesta`). Heartbeats section renamed from `Agents that wake on schedule` → `Agents on autopilot` across all three locales.
- **5 placeholder integration icons removed from LP** — LinkedIn, Amplitude, DocuSign, Bling, Asaas were rendering as generic Lucide `<Activity>`, `<FileText>`, `<Workflow>`, `<Zap>` (and LinkedIn+Amplitude shared the same icon). `react-icons/si` has no match for these brands; showing a wrong icon was worse than omitting. Integration count stays 25 in copy (real count from `integrations.md`) — logos on home just show the most recognizable.
- **iMessage channel clarified** — `features.channels.desc` across all locales now appends `(macOS)` qualifier, since iMessage ships via the `@claude-plugins-official` plugin and depends on Messages.app being open on macOS. No behavior change, just accuracy.

### Fixed

- **`start-services.sh` self-discovering install dir** (#27) — replaced hard-coded `/home/evonexus/evo-nexus` with `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` so the same script works for any service user at any path. Fixes silent systemd failure where the unit reported `active (exited)` but no processes were running — triggered when `SUDO_USER` was set to something other than the auto-created `evonexus` user (typical VPS pattern: clone into `/root/*` while `SUDO_USER=ubuntu` is preserved by `sudo -i`). `install-service.sh` no longer regenerates `start-services.sh` via heredoc; just `chmod` + `chown` the checked-in version. Added `mkdir -p logs` and `cd ... || exit 1` guards so fresh installs and dir-less reboots fail loudly instead of silently running from the wrong cwd.

## [0.27.0] - 2026-04-22

### Added

- **Frontend i18n — pt-BR, en-US, es** (#24) — `react-i18next` + three locale bundles with 539 structurally identical keys each (validated via AST walker). Sidebar, Setup wizard (every label + validation string, live-switches as the user picks a language on step 1), Login, Settings, and 25+ page headers (Overview, Agents, Skills, Memory, Heartbeats, Goals, Providers, Integrations, Backups, Issues, Audit, Costs, Roles, Reports, MemPalace, Systems, Templates, Scheduler, Routines, Tasks, Knowledge layout, Knowledge Settings, API Keys, Connections) now render in the workspace's chosen language. Resolution order: `workspace.language` (backend) → `localStorage.evo_lang` → `navigator.language` → `en-US` fallback. Legacy codes (`ptBR`, `pt_BR`, `pt`, `enUS`, `en_US`) normalize to canonical BCP-47 transparently on both frontend and backend.
- **`dashboard/frontend/.npmrc`** — `legacy-peer-deps=true` so `make dashboard-app` installs cleanly despite `i18next@24`/`react-i18next@15` declaring `typescript@^5` as peer while the frontend is on TS 6.

### Changed

- **Backend UTF-8 everywhere** — every Python I/O path that persists or reads user-facing content now uses explicit `encoding="utf-8"`: `workspace.yaml` + `CLAUDE.md` (auth_routes), `.env` editor (config), `routines.yaml` (goals, scheduler), `triggers.yaml`, `heartbeats.yaml`, ADW script docstring parsing, secret key file, port read, and Knowledge CLI env round-trip. Flask JSON responses emit real UTF-8 (`ensure_ascii = False`, `Content-Type: application/json; charset=utf-8`) instead of `\uXXXX` escapes. Accented content (`João`, `Leilões`) now survives on Windows + Docker slim (locale=C) without mangling.
- **`settings.py` — `_normalize_language`** — transparent BCP-47 normalization on `GET` and `PUT /api/settings/workspace` so legacy `ptBR` in existing `workspace.yaml` promotes to `pt-BR` on read and canonicalizes on write. Alias lookup is case-insensitive (matches frontend's `/^ptBR$/i`).
- **`setup.py`** — default language is now `pt-BR` (BCP-47) instead of legacy `ptBR`. Matches the canonical form used by the UI.
- **`auth_routes._save_workspace_config`** — default language fallback changed from `"en"` to `"pt-BR"`, aligned with setup.py and frontend `DEFAULT_LOCALE`.

### Fixed

- **i18n resolver chain empty at runtime** — `LanguageDetector` + `supportedLngs` + `nonExplicitSupportedLngs` + `load: 'currentOnly'` combination left `i18n.languages = []` even with resources and language correctly loaded, so `t()` and `exists()` returned raw keys. Resolve the locale synchronously inline (localStorage → navigator.language → default) and pass it to `init({ lng })`. Drop `i18next-browser-languagedetector` — its job is now done inline.
- **Scheduler — duplicate firings** — removed the `_run_scheduler` thread embedded in `app.py` that was running alongside the standalone `scheduler.py` process, causing every routine to fire 2-3× per trigger. Kept a lightweight `_poll_scheduled_tasks` thread for one-off `ScheduledTask` DB entries only.
- **Scheduler — atomic PID lock** — replaced TOCTOU-prone check-then-create with `O_CREAT|O_EXCL` atomic open. Prevents multiple schedulers from starting simultaneously during rapid restarts (was causing `review-todoist` / `git-sync` to fire multiple times and send duplicate Telegram messages).
- **Dashboard `restart-all`** — `pkill` processes directly then re-run `start-services.sh` instead of `systemctl restart` (which on `Type=oneshot` + `KillMode=none` didn't reliably kill children). Works without sudo.
- **Heartbeat prompt passing** — pass prompt as positional arg instead of `-p` flag. Claude CLI has no `-p` flag; the YAML frontmatter (`---`) was being interpreted as an unknown CLI option, failing all heartbeats with `unknown option '---\nname: "zara-cs"'`.
- **`fin-daily-pulse`** — convert all Stripe amounts to BRL (USD/IDR→BRL via exchangerate-api with 5.75 fallback); fix churn to use `customer.subscription.deleted` events with full pagination; unify Telegram to a single `reply()` call per run.
- **`prod-good-morning` / `prod-end-of-day`** — replace sub-skill calls (`/gog-email-triage`, `/prod-review-todoist`) with direct Gmail MCP / Todoist calls, eliminating 2× Telegram notifications per run.
- **`pulse-faq-sync`** — explicit instruction to send exactly ONE Telegram per run.

## [0.26.0] - 2026-04-22

### Added

- **Gemini embedder for Knowledge Base** (#22) — third embedder provider alongside `local` (MPNet) and `openai`. Supports two models: `gemini-embedding-001` (stable, text-only, 2048-token input, accepts `task_type`) and `gemini-embedding-2-preview` (multimodal, 8192-token input). Uses Matryoshka Representation Learning (MRL) with selectable output dim: 768 (default, aligns with local storage cost), 1536, or 3072. L2-normalizes client-side for dim < 3072 per Google's embedding docs. Lazy SDK import — no cost when the provider is inactive. Free tier available at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
- **Auto-generated `KNOWLEDGE_MASTER_KEY`** (#23) — the Fernet key required by the Knowledge Base is now generated automatically during `make setup` (interactive wizard) and on Docker first boot (`entrypoint.sh`), matching the UI-first philosophy already used for `EVONEXUS_SECRET_KEY`. Fresh installs get Knowledge working out of the box, no manual `make init-key` required. Idempotent — existing keys are preserved. The CLI `evonexus init-key` is still available for legacy/rotation flows.

### Changed

- **`BaseEmbedder.embed()` accepts optional `task_type`** — providers that support task hints (Gemini `gemini-embedding-001`) use `RETRIEVAL_DOCUMENT` during ingestion and `RETRIEVAL_QUERY` at search time. Local (MPNet) and OpenAI ignore the parameter silently for API parity. Backward-compatible via default `task_type=None`.
- **Knowledge settings endpoint** (`PUT /api/knowledge/settings`) — now validates Gemini keys against Google AI Studio's `AIzaSy...` pattern, enforces MRL dim allowlist (`{768, 1536, 3072}`), and model allowlist for both Gemini models. Inherits CSRF guard + audit log from v0.25.0 hardening.
- **`.gitignore`** — cover runtime databases at repo root (`*.db`, `*.db-shm`, `*.db-wal`) and the full `dashboard/data/` directory (previously only `dashboard/data/*.db` literal files were ignored, missing subdirs like `mempalace/`, `knowledge/`, `openclaude.db`).

### Fixed

- **`Settings.tsx`** — removed unused `providerNeedsKey` variable that was breaking `tsc --noEmit` since the Gemini PR landed.

### Documentation

- **`docs/dashboard/knowledge.md`** — first-time setup now reflects auto-generated master key; embedder section lists all three providers (local, openai, gemini) with their dims and use cases.
- **`docs/reference/env-variables.md`** — new "Knowledge Base (pgvector)" section documenting `KNOWLEDGE_MASTER_KEY`, `KNOWLEDGE_EMBEDDER_PROVIDER`, OpenAI/Gemini keys, MRL dim selection, and parser choice.

## [0.25.0] - 2026-04-20

### Added — Knowledge Base (pgvector, multi-connection)

- **Knowledge Base feature** — full multi-tenant vector knowledge system on Postgres + pgvector. Users bring their own Postgres (Supabase, Neon, RDS, on-prem); EvoNexus is client-only, no Docker or infra provisioning.
- **1-click "Connect & Configure" wizard** (`/knowledge/connections`) — validates Postgres ≥14, pgvector ≥0.5, detects pgbouncer transaction pooling (blocks with HTTP 422 + actionable message), runs Alembic migrations, applies schema (8 tables including `knowledge_classify_queue`).
- **Fernet-encrypted credential storage** — DSN ciphertext at rest via `KNOWLEDGE_MASTER_KEY` (bootstrap: `evonexus init-key`). API responses mask passwords as `***`. Audit trail on settings mutations (who changed which keys, IP, timestamp — values never logged).
- **Hybrid search** — dense (pgvector HNSW) + sparse (Postgres FTS `plainto_tsquery('portuguese')`) fused via Reciprocal Rank Fusion, with metadata boost per `content_type` (faq=1.20, lesson=1.10, reference=1.00). Shipped as default, not opt-in.
- **Two embedders** — local (multilingual MPNet, 768 dim) and OpenAI (1536 / 3072 dim depending on model — `text-embedding-3-small`, `text-embedding-3-large`, `text-embedding-ada-002`). Provider locked once first connection is configured; changing requires full reindex (reindex endpoint deferred to v0.25.1).
- **Document intelligence async** — upload returns `status=ready` immediately after parse+chunk+embed; classification (`content_type`, `difficulty_level`, `topics`) runs in a separate worker fed by `knowledge_classify_queue` with `FOR UPDATE SKIP LOCKED`. Classification uses the `claude` CLI subprocess (same runner pattern as heartbeats) — no direct LLM API keys required. Disabled path logs warning once (UI badge deferred to v0.25.1).
- **Marker parser** — PDF, DOCX, PPTX, XLSX, HTML, EPUB with OCR. Lazy-loaded (~500 MB model download on first install via `POST /api/knowledge/parsers/install`). PlainText parser covers `.md`, `.txt`, `.csv`, `.json`.
- **Public API `/api/knowledge/v1/*`** — Bearer-token auth via `knowledge_api_keys` scoped by `connection_id` + `space_ids`; plus internal path via `DASHBOARD_API_TOKEN` which bypasses rate limit.
- **Rate limiter** — fixed-window UPSERT (`date_trunc('minute', now())`). Trade-off accepted: boundary burst can reach 2× limit across adjacent windows. Returns HTTP 429 with `Retry-After` header.
- **6 `knowledge-*` skills** — `knowledge-query`, `knowledge-summarize`, `knowledge-ingest`, `knowledge-browse`, `knowledge-organize`, `knowledge-admin`. Integrated in 7 agents (mentor, zara, nex, mako, flux, lumen, clawdia). Note: `knowledge-reindex` deferred to v0.25.1 — manual workflow today is TRUNCATE chunks + re-upload.
- **UI** — full Knowledge section in dashboard (`/knowledge/*`): connection switcher in top-bar, Connections list + wizard + detail, Spaces, Units (reorder), Browse, Search, Upload, API Keys, Settings (embedder + OpenAI key + parser).

### Changed

- **LLM providers removed from `/integrations`** — Anthropic, Gemini, Voyage, LlamaParse, OpenAI cards were cut. Agents and classifiers now use the `claude` CLI as the unified runner (subprocess), so users no longer configure provider API keys at the workspace level. OpenAI remains available, but scoped to the Knowledge embedder and configured inline at `/knowledge/settings`.
- **Dynamic embedder dimension** — migration 001 resolves `vector(N)` size from `KNOWLEDGE_EMBEDDER_PROVIDER` + `KNOWLEDGE_OPENAI_MODEL` at runtime instead of hardcoding 768. Fixes dimension-mismatch errors when switching to OpenAI (1536/3072) on a new connection.

### Security

- **CSRF protection** added to all session-authenticated write endpoints (POST/PUT/PATCH/DELETE) on Knowledge, Knowledge-proxy, and Integrations blueprints — requires `X-Requested-With: XMLHttpRequest` header. Pairs with `SESSION_COOKIE_SAMESITE=Strict` and restricted CORS allowlist (`localhost:5173`). Bearer-auth requests are exempt. **Breaking change for API clients:** curl or SDK scripts hitting session-authed endpoints must now send `X-Requested-With: XMLHttpRequest`.
- **Audit log** on credential mutations — `update_settings` and `create/update/delete_custom_integration` write to `AuditLog` with user/action/resource/IP/timestamp. Secret *values* are never logged; only the set of keys that changed.

### Fixed

- `Popen()` doesn't accept `input=` kwarg — stdin write/close pattern.
- Units schema alignment; `CAST(:x AS jsonb)` instead of `:x::jsonb` shortcut; tags array type.
- Connection-scoped navigation; connection switcher filtering all pages.
- `get_dsn()` now accepts either `id` or `slug` — public `/v1/*` endpoints that receive the slug as connection id no longer raise `KeyError`.
- `list_documents` aggregates `chunks_count` and `pages_count` via `LATERAL JOIN` — Browse UI no longer shows `—` for every row.

### Known Limitations (shipped as-is; tracked for v0.25.1)

- Embedder provider change requires manual reindex (TRUNCATE chunks + re-upload). Automated reindex endpoint + `knowledge-reindex` skill deferred.
- Classify worker silently disabled when no `claude` CLI present — logs warning once; UI badge deferred.
- `pages_count` in `list_documents` returns `0` (not `null`) for documents without page metadata (markdown, txt).
- Model→dim mapping is duplicated across 4 modules — tech debt to consolidate.
- Test suite requires cwd=`dashboard/backend/` or `PYTHONPATH=.` to run end-to-end.
- Search p95 at 10k+ chunks not load-verified in this release; target 500ms is architectural.
- `routes/providers.py` write endpoints (pre-existing since v0.24) lack CSRF/audit — flagged in release critique, addressed globally in v0.25.1 via `before_app_request`.

### Deferred to v0.26.0

- **Knowledge v2 (Smart Ingest + Agentic RAG)** — LLM-enhanced pre-parse classification, normalization, per-chunk enrichment (summary, questions_answered, entities, topics), semantic chunking, and an agentic retrieval loop (query rewrite + coverage evaluation + re-retrieval with max 1 retry). Separate feature folder: `workspace/development/features/knowledge-v2/` (Discovery complete).

### Not Included (v1.1+)

- Voyage embedder (hidden from UI; not implemented).
- LlamaParse image parser routing in upload pipeline (module exists, not wired).
- Per-space chunking config override.
- Re-ranker (Cohere / Voyage Rerank).
- `@librarian` agent.
- URL crawl ingestion.
- Document versioning.
- Access rules enforcement (stored but not applied).

## [0.24.0] - 2026-04-17

### Added

- **Docker Swarm / Portainer / Traefik deployment path** — new production overlay with `Dockerfile.swarm`, `Dockerfile.swarm.dashboard`, `evonexus.stack.yml`, `entrypoint.sh`, `start-dashboard.sh`, GitHub Actions workflow to publish images to ghcr.io, and full `README.swarm.md` guide. 100% additive: VPS bare-metal (`make setup`) and local Docker Compose paths are untouched. UI-first configuration — zero secrets baked in; everything configured via the dashboard after first boot. Services that need `ANTHROPIC_API_KEY` wait in a 30s polling loop instead of crash-looping (PR #13 by @NeritonDias).
- **Dedicated `codex_auth` provider for Codex OAuth** — split from the API-key `openai` provider. Codex OAuth now requires OpenClaude ≥0.3.0 and uses model aliases (`codexplan` / `codexspark`) to route to the Codex backend. Setting a raw `gpt-5.x` model name bypasses OAuth and falls back to chat-completions — the new provider entry and env-var defaults prevent that silent failure. Bilingual (EN + PT-BR) provider guide at `docs/providers/codex-oauth.md` (PR #12 by @NeritonDias).
- **Live model discovery for OpenAI provider** — dashboard Providers page now calls `GET /v1/models` with the user's API key, filters coding-relevant models, and renders them in a typed combobox with debounced validation. No more copy-pasting model names from docs (PR #12).
- **OpenClaude 0.3.0+ enforced across install surfaces** — `setup.py`, `cli/bin/cli.mjs`, and the Docker image all install / upgrade `@gitlawb/openclaude@latest`. Service user (`evonexus`) also gets OpenClaude under `~/.local` during systemd setup (PR #12).

### Fixed

- **`pip install -e .` / `npx @evoapi/evo-nexus` no longer crash with `EOFError`** — `setup.py` now detects pip build-backend context via `EVO_NEXUS_INSTALL=1` + narrow argv markers (`egg_info`, `dist_info`, `bdist_wheel`, `--editable`) and exposes proper package metadata via `setuptools.setup()` with `find_packages()` instead of running the interactive wizard. Version is read from `pyproject.toml` (single source of truth) instead of being hardcoded (PR #11 by @ricardosantisinc, refined in PR #12).
- **Scheduler no longer starts duplicate instances on rapid restarts** — atomic PID lock using `O_CREAT | O_EXCL` replaces the TOCTOU-prone check-then-create pattern. Prevents double-firing of routines (review-todoist, git-sync) and duplicate Telegram messages.
- **`restart-all` in the dashboard actually restarts everything** — replaced unreliable `systemctl restart evo-nexus` (broken by `Type=oneshot + KillMode=none`) with direct `pkill` of the known processes followed by `start-services.sh`. Works without sudo.
- **Heartbeats stopped failing with `unknown option '---\nname:...'`** — Claude CLI has no `-p` flag; the prompt (which starts with YAML frontmatter `---`) was being parsed as a CLI option. Now passed as a positional argument with `--output-format json`.

## [0.23.2] - 2026-04-16

### Fixed

- **`int-evo-crm` pipeline_items crashed on list responses** — `evo_crm_client.py`'s `cmd_pipeline_items` assumed `data` was always a dict with a `payload` key, but the API returns `{"data": [...items...]}` (a list) when `--stage_id` is passed. Calling `.get()` on the list raised `AttributeError: 'list' object has no attribute 'get'` and broke any agent that filtered by stage. Now handles both shapes.

### Added

- **`TELEGRAM_CHAT_ID` field in the Telegram integration card** — dashboard Integrations page now exposes `TELEGRAM_CHAT_ID` as an optional field alongside `TELEGRAM_BOT_TOKEN`, matching `.env.example`. Used as the default chat/group destination for notifications.

### Changed

- **`dev-skillify` repurposed to scaffold custom skills** — previously a retrospective "conversation → skill" capture tool (role now covered by `dev-learner`). The skill now follows the same interactive pattern as `create-agent` / `create-routine`: interview → generate `.claude/skills/custom-{slug}/SKILL.md` with frontmatter, workflow, anti-patterns and verification. Same filename, new purpose.

## [0.23.1] - 2026-04-16

### Fixed

- **Installer clone is ~10x faster** — `npx @evoapi/evo-nexus` now performs a shallow clone (`--depth=1`), cutting the download from ~454 MiB of pack data to roughly 30–50 MiB. The repo history carries ~272 MiB of superseded avatar PNGs that end users don't need for installation. Update path also switched to `git fetch --depth=1` + `git merge --ff-only origin/<branch>` to stay shallow-safe across updates while still surfacing conflicts on local modifications instead of silently discarding them.

## [0.23.0] - 2026-04-15

### Added

- **Chat message rewind + inline edit** — hover over a previous user message and click the pencil to turn it into an inline textarea (Esc cancels, Cmd/Ctrl+Enter commits). Committing truncates the conversation at that point and starts a fresh Agent SDK session, so the model genuinely forgets the rewound turns. JSONL persistence stays append-only: rewinds are recorded as `{type:"rewind", at:<uuid>}` markers applied at read time. Legacy messages without uuids get synthesized deterministic ids on load — zero disk migration (PR #10 by @gomessguii).
- **Copy button on chat messages** — hover-revealed icon on user and assistant messages copies the text to clipboard with a brief check-icon confirmation. Assistant copy concatenates text blocks and skips tool_use cards (PR #10 by @gomessguii).
- **Message uuids across the stack** — stable ids now flow through frontend, in-memory session cache, and persistent JSONL logs. Required for rewind; unlocks future features (reactions, per-message pins, etc.).

## [0.22.5] - 2026-04-15

### Added

- **Paste screenshots from clipboard into chat** — Cmd/Ctrl+V on the chat textarea now captures images from the clipboard and routes them through the existing file-attachment pipeline. Pasted images get a `pasted-{ts}.{ext}` filename. Plain-text pastes keep the default browser behavior unchanged (PR #9 by @gomessguii).

## [0.22.4] - 2026-04-15

### Added

- **Subagent tool list inside Agent card** — when an agent spawns a subagent (via the `Agent` tool), the chat card now shows a `N tools` badge in the header and, when expanded, lists every tool the subagent ran (icon + name + 60-char input preview, `max-h-80` with auto-scroll). Original JSON input moved behind a "View input" toggle. Uses the SDK's `parent_tool_use_id` to associate child tool calls with the parent Agent block.

## [0.22.3] - 2026-04-14

### Added

- **TodoWrite renders as checklist in chat** — instead of a JSON dump, the chat now shows ○ pending / ◐ in_progress / ● completed (with strikethrough for done items) plus a `N/M done` counter in the card header. Falls back to the generic JSON view if the input is malformed.

### Changed

- **Trust mode is re-read on every tool call** — toggling Settings → Trust now takes effect immediately for ongoing chat sessions, no restart needed. Previously the value was snapshotted at session start.

## [0.22.2] - 2026-04-14

### Added

- **Trust mode** — new toggle in `/settings` → Trust. When ON, agents auto-approve all tool calls (Write/Edit/Bash/Agent) without prompting. Persists in `config/workspace.yaml` under `chat.trustMode`. Backend endpoints `GET/PATCH /api/settings/chat`; terminal-server reads the flag at every session start and short-circuits both `canUseTool` and `PreToolUse` hook. OFF by default.

### Fixed

- **Custom integrations configurable via drawer** — custom integrations now open the same configuration drawer as core ones, so their env keys and metadata are editable directly from the dashboard.
- **Preserve SKILL.md body on custom integration PATCH** — editing a custom integration via PATCH no longer clobbers the hand-written body of its SKILL.md. Only the frontmatter metadata is rewritten.

## [0.22.1] - 2026-04-14

### Added

- **Right-click context menu on chat sessions** — Rename (inline edit), Archive/Unarchive, Delete (with confirm). Archived sessions collapse into a "Arquivadas" footer section at the bottom of the list; section is hidden entirely when there are no archived entries. New `PATCH /api/sessions/:id` endpoint accepts `{name?, archived?}`; `archived` field persists to `session-store.js`.

## [0.22.0] - 2026-04-14

### Added

- **Per-tool approval flow in chat** — Read/Glob/Grep/WebFetch/WebSearch/ToolSearch run silently; Write/Edit/Bash/NotebookEdit/Agent prompt the user via inline Allow/Deny cards. Approval now covers **subagents** too (spawned via the Agent tool) via `PreToolUse` hook, not only the main thread.
- **Global notification bell (topbar/sidebar)** — live WebSocket channel broadcasts `agent_awaiting` and `agent_finished` events from ANY session. Bell icon shows unread count, dropdown lists pending interactions, clicking navigates to the correct session. Persists to localStorage; auto-dismisses when you visit the origin session. Also updates tab title, favicon red-dot, per-session sidebar pulse, and OS notifications when tab is hidden.
- **Custom Integrations** — Integrations page now separates "Core" and "Custom" sections. Custom integrations live at `.claude/skills/custom-int-{slug}/SKILL.md` (gitignored). New UI: "+ Add custom integration" modal with fields for display name, slug, category, description, and env keys (name + value password inputs; values are upserted to `.env` atomically, names go to SKILL.md). Edit/delete supported via hover buttons on custom cards.
- **`create-integration` skill** — guides the creation of a custom integration through interview → `evo.post("/api/integrations/custom", ...)`.
- **Heartbeat costs in Costs page** — `/api/costs` now includes `by_heartbeat` aggregation and updates total KPIs. New "Per Heartbeat Breakdown" table.
- **Bling and Asaas** — added as core integrations (previously missing from the hardcoded list).
- **"Powered by EvoNexus" footer links** to evonexus.evolutionfoundation.com.br in shared-workspace views.

### Changed

- **Backup collection strategy** — `backup.py` `collect_files()` now uses a **dynamic filesystem walk** of `workspace/` and `memory/` instead of relying only on `git ls-files --ignored`. Sub-directories containing their own `.git` (workspace/projects/*) are treated as sub-repos and skipped. Captures files that the UI drops into `workspace/project/` that the git rules didn't list as ignored.
- **Licensing and WhatsApp** — moved from hardcoded core to custom integrations (they live as `custom-int-licensing` and `custom-int-whatsapp` skills, so they appear in the Custom section automatically).
- **Notification icons** — replaced emoji with lucide-react icons throughout the notification bell.

### Fixed

- **Heartbeat datetime columns on Python 3.10** — `created_at`/`updated_at`/`started_at`/`ended_at`/`consumed_at` in Heartbeat tables changed from `db.DateTime` to `db.String(30)`. The runner inserts ISO strings with trailing `Z`, which Python 3.10 `fromisoformat()` rejects (fixed in 3.11). Prod was throwing 500 on `/api/heartbeats`. No schema migration needed — SQLite is dynamically typed.

## [0.21.0] - 2026-04-14

### Added

- **Heartbeats — proactive agents with 9-step protocol** — agents wake on a schedule (interval, manual, new_task, mention, approval_decision), check state, and decide whether to act. Config in `config/heartbeats.yaml`, CRUD via `/scheduler` UI or `create-heartbeat` / `manage-heartbeats` skills. Atomic checkout prevents double-runs; janitor auto-releases stale locks. See `.claude/rules/heartbeats.md` and `docs/heartbeats.md`.
- **Goal Cascade — Mission → Project → Goal → Task** — 4-level hierarchy with SQLite triggers that auto-progress goals when tasks are marked done. Goals support `count` / `currency` / `percentage` / `boolean` metric types. Context is auto-injected into agent prompts when `goal_id` is set on a routine, heartbeat, or ticket. UI at `/goals`. See `.claude/rules/goals.md` and `docs/goals.md`.
- **Tickets — persistent work threads with atomic checkout** — assignable tickets with 6-state workflow (open → in_progress → blocked → review → resolved → closed), comments, activity log, `@agent-slug` mentions that wake heartbeats. Tickets feed the agent inbox in heartbeat step 3. UI at `/issues` with filters, search, bulk actions. See `.claude/rules/tickets.md` and `docs/tickets.md`.
- **SDK client for internal API (`dashboard/backend/sdk_client.py`)** — `EvoClient` singleton that auto-resolves base URL from `EVONEXUS_API_URL` → `FLASK_PORT` → `localhost:8080` and auto-injects `Authorization: Bearer $DASHBOARD_API_TOKEN`. Skills use `from dashboard.backend.sdk_client import evo` instead of hardcoded curl — works in dev, nginx, and production without changes.
- **Auto-bind session to created ticket** — when an agent creates a ticket inside a chat session, the terminal-server detects the POST `/api/tickets` response in tool_result output and auto-binds the ticket to the session. Chip in the chat header updates live via WebSocket `ticket_bound` event. Supports JSON and Python-repr output formats.
- **Ticket source attribution** — `source_agent` and `source_session_id` columns on tickets. Terminal-server injects a `## Runtime context` block into the agent's system prompt with the current agent slug and session id; skills pass them through so the ticket records provenance. Timeline renders "created this ticket via @agent (session #xxxx)"; ticket header has a "Source" field.
- **Slash-command autocomplete in chat** — typing `/` opens a popup filtered by substring match on skill name, with `↑↓` navigation, `Enter`/`Tab` to insert, `Esc` to close. Mirrors Claude Code terminal UX.
- **7 new creation/management skills** — `create-ticket`, `create-goal`, `create-heartbeat`, `manage-heartbeats`, `create-agent`, `create-command`, `create-routine`, `schedule-task`, `trigger-registry`, `workspace-share`, `initial-setup` refactored to use `EvoClient`.
- **19 engineering agents from oh-my-claudecode** — `apex-architect`, `bolt-executor`, `lens-reviewer`, `hawk-debugger`, `grid-tester`, `oath-verifier`, `compass-planner`, `raven-critic`, `zen-simplifier`, `vault-security`, `echo-analyst`, `trail-tracer`, `flow-git`, `scroll-docs`, `canvas-designer`, `prism-scientist`, `scout-explorer`, `probe-qa`, `quill-writer` + 2 native (`helm-conductor`, `mirror-retro`). Total agent count: 17 business + 21 engineering. See [NOTICE.md](./NOTICE.md).
- **Sessions sidebar badge** — chat sessions bound to a ticket show a `🎫 #xxxxxxxx` chip next to the session name.

### Changed

- **Agents have no `skills:` frontmatter block** — all 38 agents see the full skill catalog dynamically. Adding a new skill no longer requires editing frontmatter across agents.
- **Skill index auto-discovered** — `.claude/skills/CLAUDE.md` now lists 175+ skills organized by prefix (`dev-`, `fin-`, `hr-`, `int-`, `legal-`, `mkt-`, etc.).

### Fixed

- **`config/heartbeats.yaml` added to `.gitignore`** — user heartbeat config no longer accidentally committed.

## [0.20.6] - 2026-04-13

### Fixed

- **PDF preview in workspace** — PDFs were downloading instead of rendering inline. Added `?inline=1` parameter to the download endpoint that serves with `Content-Disposition: inline` instead of `attachment`

## [0.20.5] - 2026-04-13

### Fixed

- **Flask survives systemd restart** — `pkill` pattern changed from `dashboard/backend.*app.py` to `python.*app.py`. The `cd dashboard/backend` changes CWD but the process cmdline stays `python app.py`, so the old pattern never matched and Flask kept running with stale code across restarts

## [0.20.4] - 2026-04-13

### Fixed

- **Chat connection error feedback** — when terminal-server is offline, the chat UI now shows a red error pill instead of sitting silently. HTTP preflight check before WS connect, disabled input while connecting/errored, `cancelled` flag for clean unmount (PR #7 by @gomessguii)
- **Terminal-server IPv4 bind** — explicit `0.0.0.0` host so WSL2 localhost forwarding reaches the server from Windows browsers (PR #6 by @gomessguii)

## [0.20.3] - 2026-04-13

### Added

- **File tab context menu** — right-click on workspace file tabs for: Close, Close others, Close all to the left, Close all to the right, Close all
- **Scheduler in systemd** — `start-services.sh` and `ExecStop` now manage the scheduler process. Restarts properly kill and relaunch the scheduler so `routines.yaml` changes take effect

### Fixed

- **Licensing product slug** — changed `PRODUCT` and `TIER` from `"evonexus"` to `"evo-nexus"` to match the licensing server's product registry. This was causing 400 `INVALID_TIER` on new installations
- **Licensing error logging** — `_post()` now logs the server's error body (e.g., `MISSING_FIELD: email is required`) instead of the generic `400 Bad Request`
- **Setup requires email** — the initial setup endpoint now validates that email is provided (required for license registration)
- **Auto-register skips missing email** — `auto_register_if_needed()` no longer attempts registration if the admin user has no email
- **Makefile pkill self-kill** — applied `[p]attern` bracket trick to prevent `pkill -f` from matching its own shell process on Linux/WSL (PR #5 by @gomessguii)

## [0.20.2] - 2026-04-13

### Added

- **Durable chat history via JSONL logs** — chat messages are now append-only logged to `ADWs/logs/chat/{agent}_{session}.jsonl`. On session join, if the in-memory history is empty (e.g., after server restart), the JSONL log is read and restored automatically. This makes chat history survive restarts, `sessions.json` cleanups, and 7-day expiry

## [0.20.1] - 2026-04-13

### Added

- **Image generation cost estimates** — each image in the Costs page now shows an estimated USD cost based on model pricing (Gemini Flash $0.039/img, FLUX.2 $0.03/img, GPT-5 Image $0.04/img, etc.). Total image cost shown in section header and included in the "Total (All)" KPI card

## [0.20.0] - 2026-04-13

### Added

- **Workspace folder permissions** — roles can now restrict access to specific workspace folders (finance, marketing, personal, etc.). Three modes: All, Selected (checkbox grid), None. Admin always bypasses. Enforced on all workspace browser endpoints: tree, read, write, create, rename, delete, upload, download, recent, and file share creation
- **Role editor UI for folder access** — Settings → Roles now has a "Pastas do Workspace" section with radio buttons for mode and a dynamic checkbox grid that scans existing folders from disk
- **Dynamic folder scan endpoint** — `GET /api/roles/workspace-folders` lists all top-level directories under `workspace/` without hardcoding
- **SendMessage tool card** — chat UI now renders `SendMessage` tool calls with subagent avatar and description, same as Agent tool cards

### Fixed

- **SQLite auto-migration** — added `ALTER TABLE roles ADD COLUMN workspace_folders_json` to `app.py` startup migration, preventing crash on existing databases
- **Chat textarea height** — input area resets to single line after sending (carried over from v0.19.1)

## [0.19.1] - 2026-04-13

### Added

- **Subagent cards in chat** — when an agent delegates to another (e.g., Oracle → Sage), the tool card shows the subagent's avatar, name with `@`, description, live progress summary, and completion status
- **Subagent progress summaries** — enabled `agentProgressSummaries` in the SDK so subagent activity is streamed in real-time
- **Chat UI screenshot** — added `print-chat.webp` to README, site screenshots carousel, and i18n (en/pt-BR/es)

### Fixed

- **Textarea height reset** — Shift+Enter expanded the input area but it did not shrink back after sending. Now resets to single line on send
- **Agent SDK dependency** — added `npm install` step to production deploy (the SDK was listed in package.json but not installed on the server)

## [0.19.0] - 2026-04-13

### Added

- **Chat UI for agents** — new chat mode alongside the terminal on every agent page. Uses the Agent SDK (`query()`) with structured streaming: text deltas, tool use cards, thinking indicator. Messages persist across page refreshes via server-side `chatHistory` stored in session-store
- **Chat session management** — sidebar "Sessions" tab shows all conversations for an agent with preview of last message, sorted by most recent. Create new sessions, switch between them. Each session maintains its own SDK conversation context
- **Agent identity in chat** — chat mode loads the agent's `.claude/agents/{name}.md` system prompt via `systemPrompt.append` on the Claude Code preset, so agents (Oracle, Clawdia, etc.) respond in character
- **File attachments in chat** — attach images via paperclip button or drag-and-drop. Files are base64-encoded, saved to temp dir on server, and referenced in the prompt so the agent can `Read` them
- **Restart All button** — Scheduler page now has a "Restart All" button that triggers `systemctl restart evo-nexus` via a new `/api/services/restart-all` endpoint (systemd deployments only)

### Fixed

- **Chat event routing** — fixed duplicate `type` key bug in server.js where `{ type: 'chat_event', ...msg }` spread overwrote the envelope type with the inner message type, causing the frontend to silently drop all chat events
- **Session persistence** — `chatHistory` and `sdkSessionId` are now included in session-store serialization/deserialization so chat conversations survive server restarts

## [0.18.8] - 2026-04-13

### Added

- **Multi-terminal tabs per agent** — each agent page now supports multiple terminal sessions with a tab bar. Create new terminals with the `+` button, switch between them, and close sessions individually. Backend adds `GET /api/sessions/by-agent/:name` and `POST /api/sessions/create` endpoints
- **Recent Agents section** — the Agents page shows the last 6 visited agents at the top for quick access, with avatar, name, command, and running indicator. Tracked via localStorage

### Fixed

- **systemd KillMode=none** — nohup background processes (Flask, terminal-server) were being killed when the oneshot ExecStart script finished. `KillMode=none` prevents systemd from sending SIGTERM to child processes
- **install-service.sh regenerates start-services.sh** — the copied script had hardcoded `/root/` paths from the original installation, causing `Permission denied` errors when running as the `evonexus` user

## [0.18.7] - 2026-04-12

### Added

- **Dedicated `evonexus` user + systemd service** — VPS setup (`is_remote=True` as root) now automatically creates a dedicated system user, installs uv + Claude Code for it, and configures a systemd service (`evo-nexus`) that auto-starts on boot. Solves the Claude Code restriction that blocks `--dangerously-skip-permissions` as root
- **`install-service.sh`** — standalone script to install the systemd service on existing installations (`sudo bash install-service.sh`). Safe to re-run
- **CLI update mode uses systemd** — `npx @evoapi/evo-nexus .` now detects the systemd service and uses `systemctl restart` instead of calling `start-services.sh` directly. Syncs files to the service directory when they differ

### Fixed

- **systemd service type** — uses `Type=oneshot` with `RemainAfterExit=yes` since `start-services.sh` launches background processes with `nohup`

## [0.18.6] - 2026-04-12

### Fixed

- **Share viewer CSS isolation** — shared HTML files now render inside an `<iframe>` with `srcDoc` instead of `dangerouslySetInnerHTML`, preventing Tailwind preflight and global dashboard styles from overriding the shared file's internal CSS (e.g., centered headers appearing left-aligned)
- **Workspace file manager responsiveness** — FileTree sidebar now collapses into a slide-over drawer on mobile (`<lg` breakpoint) with overlay and toggle button. Toolbar buttons show icons-only on small screens (`<sm`). Selecting a file auto-closes the sidebar on mobile
- **Makefile `make run` IndentationError** — multiline Python `-c` commands had tab characters from Makefile indentation leaking into the Python source, causing `IndentationError: unexpected indent`. Collapsed to single-line commands

## [0.18.5] - 2026-04-12

### Added

- **Backup retention & auto-cleanup** — configurable via `BACKUP_RETAIN_LOCAL` and `BACKUP_RETAIN_S3` env vars (also editable in dashboard Storage Provider panel). Old backups beyond the limit are auto-deleted after each backup run
- **`boto3` as default dependency** — included in `pyproject.toml` so new installs have S3 support out of the box
- **`trigger-registry` and `schedule` skills** — added to Oracle and Clawdia agents so they can create/manage webhook triggers and scheduled tasks

### Changed

- **S3 backup is now S3-only** — when S3 is configured, daily routine and `make backup-s3` upload to S3 and delete the local copy. Local backup is fallback only when S3 is not configured
- **Dashboard restore runs post-migrate** — restore via the web UI now auto-fixes schema differences (missing columns, corrupted datetimes) after extracting, preventing 500 errors from old backups

## [0.18.4] - 2026-04-12

### Changed

- **`make restore` stops/restarts services** — restore now kills Flask and terminal-server before extracting, then restarts via `start-services.sh` after. Prevents SQLite lock conflicts and ensures auto-migrate runs on the restored database
- **Setup prompt clarified** — "Type 1 or 2" instead of "Choice" for Dashboard Access, rejects invalid input with clear message

### Fixed

- **SQLite auto-migrate fixes corrupted datetime columns** — on startup, Flask now detects and repairs NULL or non-string `created_at` values in `roles` and `users` tables. Prevents crash after restoring a backup from an older version

## [0.18.3] - 2026-04-12

### Added

- **CLI update mode** — `npx @evoapi/evo-nexus@latest .` now detects existing installations and runs pull + rebuild + restart instead of failing with "directory already exists". Stops services before pull, rebuilds frontend, restarts via `start-services.sh`
- **Backup import** — new "Importar" button in Backups page to upload external `.zip` backup files into the local backups list. Validates ZIP integrity before accepting
- **S3-compatible storage support** — added `AWS_ENDPOINT_URL` and `BACKUP_S3_PREFIX` fields to the backup Storage Provider config panel for Cloudflare R2, Backblaze B2, MinIO, and any S3-compatible provider

### Fixed

- **`npx @evoapi/evo-nexus .` on existing repo** — no longer crashes with "fatal: destination path '.' already exists". Auto-detects `.git` + `pyproject.toml` and switches to update flow
- **S3 client for non-AWS providers** — boto3 client now uses `AWS_ENDPOINT_URL` when set, enabling R2/Backblaze/MinIO connectivity

## [0.18.2] - 2026-04-12

### Added

- **`make uninstall`** — full cleanup command that stops services, removes nginx config, data, deps, and config files. Requires typing "UNINSTALL" to confirm
- **`make stop`** — stops all EvoNexus services (dashboard + terminal-server)

### Fixed

- **Setup nginx config not persisting** — now removes both `default` and `default.conf`, uses `systemctl reload` instead of `start`, shows clear error with fix command if `nginx -t` fails
- **CLI showing wrong instructions for VPS** — `npx @evoapi/evo-nexus` now detects remote mode (nginx config present) and shows `./start-services.sh` instead of `make dashboard-app`. Skips redundant frontend build when setup already built it
- **CLI redundant `npm run build`** — no longer rebuilds frontend after setup already did, avoiding "port already in use" cascade when services were already running

## [0.18.1] - 2026-04-12

### Added

- **AI Image Creator cost tracking in dashboard** — new "Geração de Imagens" section in Costs page showing per-image model, provider, tokens, size, and elapsed time with totals
- **Image costs API endpoint** — `GET /api/routines/image-costs` reads cost entries from `ADWs/logs/ai-image-creator-costs.json`

### Changed

- **AI Image Creator costs path** — cost logs now saved to `ADWs/logs/ai-image-creator-costs.json` (workspace-level) instead of `.ai-image-creator/costs.json` (project-level)

## [0.18.0] - 2026-04-12

### Added

- **Public share links** — generate public URLs for any workspace file (HTML, markdown, images, video, audio, PDF). Token-based with configurable expiration (1h/24h/7d/30d/permanent). New `FileShare` model, `shares` blueprint, public view page with EvoNexus branding footer, and management page to list/revoke links
- **Media preview in workspace** — video (mp4/webm/mov), audio (mp3/wav/ogg/aac/flac), and PDF files now render inline in both the workspace file manager and public share pages
- **Share button in toolbar** — new "Compartilhar" button in FileToolbar with modal for link generation, expiration selector, and clipboard copy
- **Share Links management page** — new `/shares` route with table view showing all active links, view counts, expiration status, copy and revoke actions
- **`workspace-share` skill** — conversational skill for Oracle and Clawdia to create/list/revoke share links via natural language
- **AI Image Creator skill** — generate images via multiple AI models (Gemini, FLUX.2, Riverflow, SeedDream, GPT-5) through Cloudflare AI Gateway or OpenRouter
- **AI Image Creator integration** — new integration card in dashboard with env var configuration for Cloudflare and OpenRouter keys
- **Integration env vars API** — scoped `GET/PUT /api/config/env` endpoints for reading and updating `.env` variables from the integration drawer

### Changed

- **Setup wizard hardened** — `uv sync`, `npm install`, and `npm run build` now check exit codes and show clear error messages with log paths instead of silently succeeding on failure
- **`make dashboard-app` runs `npm install`** — ensures frontend dependencies are up to date after `git pull` before building
- **AgentTerminal connection** — auto-detects local vs deployed environment for terminal-server WebSocket URL (supports `localhost` and `127.0.0.1` without reverse proxy)

## [0.17.2] - 2026-04-12

### Added

- **Settings page** — new `/settings` page in the dashboard with three tabs: Workspace config (`workspace.yaml`), Routines management (`routines.yaml`), and Reference (CLAUDE.md, Makefile, Commands)
- **Workspace config UI** — edit workspace name, owner, company, language (20 locales), timezone, and dashboard port
- **Routines toggle & inline edit** — enable/disable routines with toggle switches, edit schedules inline, grouped by frequency (daily/weekly/monthly)
- **Settings backend API** — 9 new endpoints for workspace and routine CRUD with audit logging and scheduler reload via sentinel file
- **API patch method** — `api.patch()` added to frontend API helper

### Changed

- **Config page replaced by Settings** — old `/config` removed, `/config` redirects to `/settings`
- **Sidebar updated** — "Settings" added as first item in System group

### Removed

- **.env editor** — removed from both frontend and backend (security risk; use terminal for .env changes)

## [0.17.1] - 2026-04-12

### Added

- **i18n support for landing page** — English, Portuguese (BR), and Spanish with language switcher in nav. Preference saved in localStorage.
- **Per-integration icons in dashboard** — each integration card now shows a distinct icon and color (24 mappings: Stripe purple, Discord blue, WhatsApp green, etc.)
- **Discord CTA in hero** — "Join 17,000+ developers on Discord" link below main CTAs
- **Evolution Foundation banner** — persistent top banner linking to evolutionfoundation.com.br

### Changed

- **Landing page copy overhaul** — new headline "Run your business with AI agents", rewritten subtitle listing business areas (finance, marketing, legal, sales, community, engineering), removed em-dashes, fixed buzzwords
- **Integration count updated** — 18 → 23 integrations (added WhatsApp, LinkedIn, Figma, Amplitude, Intercom, HubSpot, DocuSign, Bling, Asaas; removed Evolution API/Go/CRM from public LP)
- **Skills count corrected** — 150+ → 175+ across all pages and translations
- **Background simplified** — removed noise.svg + grid overlay, kept minimal gradient only
- **Agents showcase link** — "See all 38 agents" now points to /docs/agents/overview instead of broken /agents route
- **Config page removed from dashboard** — redundant with Integration drawer (from v0.17.0)
- **Canvas agent memory files** — removed from dashboard/frontend/.claude/ (wrong location)

### Fixed

- **Lucide icon name** — `Github` → `GitFork` (Github not exported in current Lucide version)

## [0.17.0] - 2026-04-12

### Added

- **Multi-file tabs in Workspace** — open multiple files simultaneously with a tab bar. Tabs persist in localStorage across page refreshes. Per-tab dirty state, editor content, and mode tracking. Middle-click to close, unsaved changes confirmation.
- **Integration config drawer** — integration cards are now clickable, opening a side drawer with integration-specific form fields (masked API keys with reveal toggle). Save writes to `.env` with safe merge. OAuth integrations show "Connect" button instead. Backend test endpoint (`POST /api/integrations/<name>/test`) with real connectivity tests for Stripe, Omie, Evolution API, and Todoist.
- **Agent-level permissions** — new `agent_access` field on roles with 4 modes: all, by layer (business/engineering), per-agent selection, or none. Locked agents appear with reduced opacity + lock icon in the dashboard. Direct URL access to locked agents shows "Acesso restrito" page. 38 agents mapped across business (17) and engineering (21) layers.
- **S3 backup browser** — new "Remote Backups (S3)" section on the Backups page lists existing backups in the configured S3 bucket. Download directly from S3. New backend endpoints `GET /api/backups/s3` and `GET /api/backups/s3/<key>/download`.
- **Backup storage provider config** — collapsible "Storage Provider" panel on the Backups page with S3 Bucket, Access Key, Secret Key, and Region fields (masked with reveal toggle).
- **Copy file path button** — click "Copiar" in the file path bar to copy the full path to clipboard.

### Changed

- **Tree view preserves state on refresh** — when page reloads, all ancestor folders of the selected file auto-expand to restore the navigation context.
- **Config page removed** — redundant with the new Integration drawer. `.env` vars for dashboard credentials (DASHBOARD_API_TOKEN) are no longer editable from the frontend (security improvement).
- **Logo consistency** — Login and Setup pages now use the official `EVO_NEXUS.png` logo instead of a generic inline SVG.

### Fixed

- **DB migration for agent_access** — auto-migrate adds `agent_access_json` column to existing SQLite databases on startup (ALTER TABLE before seed_roles).

## [0.16.0] - 2026-04-12

### Added

- **Multi-provider AI support** — switch between Anthropic (native Claude), OpenAI (GPT-5.x via Codex OAuth or API key), and OpenRouter (200+ models) from the dashboard. Provider toggle with on/off per provider, session blocking when none active, clean env whitelist to prevent stale API key leaks. (PR #4, @NeritonDias)
- **OpenAI Codex OAuth flow** — browser OAuth + device auth via dashboard endpoints (`auth-start`, `auth-complete`, `device-start`, `device-poll`, `status`, `logout`). Tokens saved in correct Codex format (`~/.codex/auth.json`).
- **Agent persona enforcement for non-Anthropic providers** — `--system-prompt` replaces default prompt for GPT/Gemini so agents respond in character.
- **Setup hardening for VPS** — auto-install prerequisites (Node.js 24.x, build-essential, uv, Claude CLI, OpenClaude), Nginx + SSL (certbot default, self-signed fallback), IPv6, firewall, proper sudo/permissions handling, service auto-start with health checks.
- **YouTube Competitive Analysis skill** (`social-yt-competitive`) — analyze YouTube channels for outlier videos and packaging patterns.
- **MemPalace worker** (`dashboard/backend/routes/_mempalace_worker.py`) — background worker for Knowledge Base indexing.

### Changed

- **Complete agent-skill audit** — all 38 agents now declare their skills in YAML frontmatter AND in the prompt body ("Skills You Can Use" section for engineering agents). 25/25 `dev-*` skills assigned to agent owners (zero orphans). Business agents expanded: Kai (+3), Sage (+3), Nex (+6), Mentor (+4), Oracle (+6), Atlas (+3). Engineering agents fixed: Raven, Zen, Vault, Trail, Scroll, Prism, Quill, Flow (+frontmatter). Orchestrators Helm and Mirror gained dedicated skill sections.
- **UI redesign — Setup, Login, Providers pages** — canvas API neural network animated background, solid cards, no glassmorphism/sparkles, professional form UX with autocomplete, accessible toggle switches with `role="switch"`.
- **Image optimization** — agent avatars PNG→WebP (271MB → 1.7MB, 99.4% reduction), docs/public screenshots PNG→WebP (67-73% reduction).
- **Onboarding flow restored** — `workspace-status` endpoint now checks if `owner` field is actually filled, not just if file exists.
- **Dead routes removed** — `/chat` quick actions replaced with Agents and Providers links.
- **Skill count bumped to 175+** across README, docs, site, rules.
- **README clone instruction** — added `--depth 1` for faster cloning.
- **Providers marked as coming soon** — Gemini, Bedrock, Vertex flagged with `coming_soon: true`.

### Fixed

- **Terminal server spread order** — `...options` moved before explicit properties in `startSession` to prevent `agent` being overwritten with `undefined`.
- **Clean env whitelist** — spawned CLI processes only inherit 22 whitelisted system vars + provider env, preventing stale `OPENAI_API_KEY` leaks.
- **Root detection** — skips `--dangerously-skip-permissions` for root users.
- **uv sync as SUDO_USER** — `.venv` symlinks now point to user's Python, not root's.
- **File ownership** — `chown -R` + `chmod +x .venv/bin/` before starting services.

## [0.15.1] - 2026-04-11

### Changed

- **Brand refresh — new EvoNexus logo** — `public/EVO_NEXUS.png` is now the canonical brand asset. `public/cover.svg` has the old `<text>Evo Nexus</text>` replaced by an embedded base64 `<image>` of the new logo, so the README banner renders the real brand mark in any viewer without external dependencies. Copies of `EVO_NEXUS.png` also live at `site/public/assets/EVO_NEXUS.png` and `dashboard/frontend/public/EVO_NEXUS.png` so the site and dashboard can serve it directly.
- **`site/src/pages/Home.tsx` — nav header** — the top navigation now shows only the EvoNexus PNG logo (`@assets/EVO_NEXUS.png`). The legacy Evolution logo (`@assets/logo.png`) and the duplicate `<span>Evo</span><span>Nexus</span>` text that sat next to it were both removed from the header — Evolution branding remains on the case-study card and the footer where it belongs.
- **`dashboard/frontend/src/components/Sidebar.tsx` — sidebar header** — the two-tone `<h1><span>Evo</span><span>Nexus</span></h1>` heading was replaced by `<img src="/EVO_NEXUS.png" className="h-8 w-auto" />`, matching the new brand.
- **Skill count bumped to 150+ across every source of truth** — `README.md` (4 spots: intro bullet, Key Features list, dashboard table, folder tree), `public/cover.svg` (badge), `.claude/rules/skills.md` (header), `docs/introduction.md`, `docs/architecture.md` (ASCII diagram + evo-skills note), `docs/skills/overview.md`, `docs/getting-started.md`, and `site/src/pages/Home.tsx` (4 spots: hero paragraph, stat card, feature tile, "Skills as Instructions" description). Previous counts of `~137`, `~140`, `137+`, `~130` all normalized to `150+`. `docs/llms-full.txt` regenerated via `make docs-build` to pick up the new numbers.

## [0.15.0] - 2026-04-11

### Added

- **Learning Loop feature** — knowledge retention system based on SM-2 spaced repetition. Four skills: `learn-capture` (extract 1-5 atomic facts from pasted content), `learn-review` (run SM-2 sessions with Again/Hard/Good/Easy grades updating `interval`/`ease` in-place), `learn-quiz` (retrieval-practice question sets, read-only), `learn-stats` (total facts, overdue count, retention rate, active decks, facts added this week). Facts are individual markdown files in `workspace/learning/facts/` with full SM-2 frontmatter (`interval`, `ease`, `reps`, `lapses`, `next_review`). Review history appended to `.state/review-log.jsonl` for audit. All user data gitignored by default — only `workspace/learning/README.md` is committed. Pull-only in v0 (no Telegram push, no Fathom auto-capture — deferred).
- **`@lumen-learning` agent** — new business-layer agent (17th) dedicated to learning retention. Orchestrates the four `learn-*` skills and keeps separation of concerns clean: `@mentor-courses` creates learning content, `@lumen-learning` makes it stick. Command: `/lumen-learning`. Model: sonnet. Color: yellow.
- **`learning_weekly` routine** — scheduled for Sundays 09:45 BRT via `ADWs/routines/custom/learning_weekly.py`. Generates a markdown digest in `workspace/daily-logs/YYYY-MM-DD-learning-weekly.md` with overdue facts and retention stats. Read-only — never mutates SM-2 frontmatter. Makefile target: `make learn-weekly`.
- **Agent avatars in the dashboard** — 35 custom PNG avatars under `dashboard/frontend/public/avatar/` covering all business agents (12) and 19 engineering agents (helm, mirror also now included). New `AgentAvatar` component renders the PNG as a circular image when available, or falls back to a colored circle with the Lucide icon when not. Integrated into the agent list cards (`Agents.tsx`, 56px) and the agent detail page header (`AgentDetail.tsx`, 60px with colored halo).
- **Agent count bumped across all docs** — README, `docs/introduction.md`, `docs/agents/overview.md`, `docs/architecture.md`, `docs/real-world/evolution-foundation.md`, `docs/dashboard/overview.md`, `docs/guides/initial-setup-skill.md`, `site/src/pages/Home.tsx`, `.claude/rules/agents.md`, `CLAUDE.md` updated from 37 (16 business) → 38 (17 business). `public/cover.svg` text updated from `37 Agents` → `38 Agents`.

### Changed

- **`dashboard/frontend/src/lib/agent-meta.ts`** — expanded from 19 entries to 38. All 21 engineering agents were previously falling through to `DEFAULT_META` (generic `Bot` icon, no slash command badge); each now has a dedicated entry with icon, color, command, label, and avatar path. Business agents `aria-hr`, `zara-cs`, `lex-legal`, `nova-product`, `dex-data`, `helm-conductor`, `mirror-retro` also gained their `avatar` field. `AgentMeta` interface extended with optional `avatar?: string`.
- **`AgentDetail.tsx` header** — grew from `h-14` to `h-20` to accommodate the 60px avatar with its colored halo to the left of the agent name and command.

## [0.14.1] - 2026-04-11

### Fixed

- **`/api/overview` endpoint** — dropped from ~16s to ~29ms (≈500× faster). `_recent_reports` was rglob'ing the entire `workspace/` tree, which on an active install holds vendored third-party repos under `workspace/projects/` (mcp-dev-brasil, oh-my-claudecode, evoai-services, etc.) — 16.853 of 17.116 MD/HTML files (98.5%) lived there and had nothing to do with "recent reports". The scan now skips top-level `projects/` (vendored repos) and `meetings/` (raw Fathom transcripts), iterates remaining areas, and formats the `date` field from the actual `mtime` instead of `path.split("/")[-1][:10]` (which was returning garbage like `"README.md"`).
- **Site typecheck errors** — `site/src/pages/Home.tsx` had 3 lucide icons (`MessageSquare`, `GitBranch`, `Database`) passing an invalid `title` prop. Wrapped them in `<span title="...">` to keep the hover tooltip and pass `tsc --noEmit`.
- **Dashboard frontend build** — `dashboard/frontend/src/pages/Providers.tsx` was importing `type LucideIcon` without using it, which caused `make dashboard-app` to fail with `TS6133`. Unused import removed.
- **Terminal startup garbage (WIP, 2 attempts included)** — on starting any agent terminal from the dashboard, bytes like `0?1;2c` / `000000` / `^[[0^[[0...` showed up in the prompt and status bar. Root cause is xterm.js auto-replying to terminal queries (DA1 `\x1b[c`, DA2 `\x1b[>c`, DSR `\x1b[5n`/`\x1b[6n`, window ops `\x1b[...t`) via `term.onData`, which the frontend was forwarding to the pty as if it were keyboard input. This release ships two defensive layers — passing `cols`/`rows` upfront on `start_claude` so the pty is born at the right size, and registering CSI handlers via `term.parser.registerCsiHandler({ final: 'c' | 'n' | 't' }, () => true)` to intercept queries at the parser level — plus a regex filter on `onData` as a second line of defense. **The bug is not fully resolved in this release.** Some payloads still slip through (likely via a non-CSI `triggerDataEvent` path that hasn't been pinned down yet). A debug log was added to `AgentTerminal.tsx` to capture the exact bytes in the next iteration.

### Changed

- **Feature folder convention** — `workspace/features/{slug}/` is now `workspace/development/features/{slug}/` across all engineering layer prompts (`.claude/rules/dev-phases.md`, `.claude/agents/compass-planner.md`, `.claude/agents/helm-conductor.md`, `.claude/commands/helm-conductor.md`, `.claude/agents/mirror-retro.md`, `docs/agents/engineering-layer.md`). Keeps all engineering artifacts (features, plans, architecture, reviews, verifications, retros) grouped under one development/ root.

### Docs

- **Multi-provider documentation** — README, `docs/introduction.md`, `docs/getting-started.md`, `docs/reference/env-variables.md`, `docs/dashboard/overview.md` updated with the OpenClaude-based multi-provider story introduced in v0.14.0. New `docs/dashboard/providers.md` documents the Providers page (supported providers, activation flow, security model with CLI + env var allowlists, logout warning). Site landing page replaces the "Full Control" feature card with "Multi-Provider, No Lock-In" highlighting the new capability.

## [0.14.0] - 2026-04-10

### Added

- **`dashboard/terminal-server/`** — lean terminal bridge powering the dashboard's per-agent xterm session. Fork of `vultuk/claude-code-web` stripped down from ~3.500 lines / 158 npm packages to ~440 lines / 74 packages, keeping only what the dashboard consumes: `POST /api/sessions/for-agent`, `GET/DELETE /api/sessions/:id`, and a WebSocket with `join_session` / `start_claude` / `input` / `resize` / `ping` / `stop`. Removed codex & cursor bridges, usage analytics, auth, HTTPS, ngrok, PWA, folder browser, and the entire legacy web UI. Spawns the local `claude` CLI via `node-pty` and persists sessions to `~/.claude-code-web/sessions.json`. New Makefile targets `terminal-logs` / `terminal-stop`. A `postinstall` hook restores the `darwin-arm64`/`darwin-x64` `node-pty` `spawn-helper` executable bit so `posix_spawnp` doesn't fail on fresh installs.
- **`make bling-auth`** — one-shot OAuth2 bootstrap for the Bling integration. Runs `.claude/skills/int-bling/scripts/bling_auth.py` to capture the initial access + refresh tokens into `.env`; subsequent refreshes are automatic via the skill.
- **Docs** — new `docs/integrations/bling.md` and `docs/integrations/asaas.md` with endpoint coverage, auth setup, and example calls. `docs/integrations/overview.md` expanded with the two Brazilian integrations.
- **Frontend** — new `dashboard/frontend/src/lib/agent-meta.ts` centralizing the agent icon/color/command/label metadata used by `Agents.tsx`, `AgentDetail.tsx`, and the refreshed `AgentTerminal.tsx`.

### Changed

- **`int-bling` skill** — upgraded from manual v1 Bearer token to OAuth2 with automatic refresh. Access token expires in 6 hours; the skill now reads `BLING_CLIENT_ID` / `BLING_CLIENT_SECRET` / `BLING_REFRESH_TOKEN` from `.env` and refreshes on 401, persisting the new token pair back to disk. `.env.example` documents the new variables and points to `make bling-auth` for first-time setup.
- **`.claude/rules/integrations.md`** — Bling row updated to reflect OAuth2 auto-refresh + `make bling-auth`. Asaas row now mentions marketplace split.
- **`dashboard/frontend/src/App.tsx`, `pages/Agents.tsx`, `pages/AgentDetail.tsx`, `components/AgentTerminal.tsx`** — refactored to consume the new `agent-meta.ts` module and the leaner terminal-server endpoints. Error messages updated from `cc-web` → `terminal-server`.
- **`Makefile`** — `dashboard-app` target now boots `dashboard/terminal-server/bin/server.js --dev` instead of the old `claude-code-web/bin/cc-web.js`. Helper targets renamed `cc-web-logs` → `terminal-logs`, `cc-web-stop` → `terminal-stop`.
- **`.gitignore`** — ignores `dashboard/terminal-server/node_modules/` and its `package-lock.json`.

### Fixed

- **Terminal spawn failures on fresh installs** — `node-pty`'s `spawn-helper` prebuild was being extracted without the execute bit on macOS, causing `posix_spawnp failed` when the dashboard tried to start a claude session. Fixed by adding a `postinstall` script that re-applies `chmod +x` on both `darwin-arm64` and `darwin-x64` prebuilds.

## [0.13.3] - 2026-04-10

### Added

- **New skill `int-bling`** — Bling ERP API v3 integration. 10 operations across products (list/create), sales orders (list/create), contacts (list/create, F/J types), fiscal invoices/NF-e (list/create from orders), and stock (get/update by warehouse). Uses OAuth2 Bearer token (`BLING_ACCESS_TOKEN`). Schemas and endpoint coverage derived from the `mcp-dev-brasil` TypeScript reference implementation under `workspace/projects/mcp-dev-brasil/packages/erp/bling/`, complemented by [developer.bling.com.br](https://developer.bling.com.br) for advanced endpoints.
- **New skill `int-asaas`** — Asaas payment platform API v3 integration. 15 operations across payments (create/get/list, PIX QR code, boleto PDF), customers (create/list with CPF/CNPJ validation), subscriptions (create/list/cancel), financial (balance, transfer), marketplace (subaccount for split payments), and utilities (installments, webhook events). Uses `ASAAS_API_KEY` header auth with `ASAAS_SANDBOX=true` as safe default (sandbox.asaas.com), switchable to production. Enums documented: `billingType` (BOLETO/CREDIT_CARD/PIX/UNDEFINED) and payment `status` (PENDING/RECEIVED/CONFIRMED/OVERDUE/REFUNDED/etc). Schemas derived from `mcp-dev-brasil/packages/payments/asaas/` with Zod validation patterns ported to the skill.
- **`.env.example`** — new `BLING_ACCESS_TOKEN`, `ASAAS_API_KEY`, and `ASAAS_SANDBOX` entries under dedicated Brazilian ERP/payments sections.

### Changed

- **`.claude/rules/skills.md`** — `int-*` row bumped from 13 → 15, now listing Bling and Asaas alongside Stripe, Omie, and the other integrations.
- **README + docs** — skill counts updated: ~138 → ~140 total (~113 → ~115 business layer).

## [0.13.2] - 2026-04-10

### Added

- **New skill `prod-activation-plan`** — canonical pattern for producing phased activation plans: single index file at `workspace/development/plans/[C]{plan-name}-{date}.md` + one folder per phase (`fase-1-quick-wins/`, `fase-2-conexoes/`, `fase-3-ciclo-completo/`) + one file per item with a rich template (frontmatter, axis, type, concrete steps, decisions pending, impact, dependencies, risks, suggested agent team, status checklist). Includes agent routing rules for `[ATIVAR]` / `[DECIDIR]` / `[CONSTRUIR NOVO]` / `[EVOLUIR]` item types, and an expansion mode that preserves existing items while appending new ones with a version bump in the history section. Lives at `.claude/skills/prod-activation-plan/SKILL.md`.

### Changed

- **Oracle — Step 6 rewritten to use `prod-activation-plan`** — Oracle no longer invents plan structures on the fly. The canonical flow is now `Oracle (interview) → @compass-planner (content) → prod-activation-plan skill (structure) → Oracle (delivery)`. Added explicit `Step 6a` (delegate content to Compass), `Step 6b` (materialize via skill), and `Step 6c` (handle plan expansions preserving existing files). Oracle prompt now contains an explicit "NEVER invent your own plan structure" directive to prevent drift.
- **README + `docs/getting-started.md`** — Quick Start callout and Step 5 both point to `/oracle` as the first thing to run after installation, with the 7-step Oracle flow explained, the activation-plan structure documented, and the 3 autonomy paths (Guided / Autonomous / Delegated) surfaced. Skill counts bumped from ~137 → ~138 (prod-* subcategory grew from 9 → 10).
- **`.claude/rules/skills.md`** — `prod-*` row updated to include `activation-plan` in the inline list and count bumped to 10.

## [0.13.1] - 2026-04-10

### Fixed

- **Dashboard — delete social account now works** — the trash icon on `/integrations` was calling `POST /disconnect/{platform}/{index}`, a route that only exists in the standalone `social-auth` Flask app (port 8765), not in the dashboard backend (port 8080), so clicks silently 404'd. Added `DELETE /api/social-accounts/<platform>/<int:index>` to `dashboard/backend/app.py` reusing `env_manager.delete_account`, and updated `dashboard/frontend/src/pages/Integrations.tsx` to call `api.delete()` and consume the returned `{platforms}` payload in a single round-trip.
- **YouTube — automatic OAuth token refresh** — `SOCIAL_YOUTUBE_*_ACCESS_TOKEN` expires after ~1h, forcing a manual reconnect through social-auth. The `social-auth` OAuth flow already requested `access_type=offline` + `prompt=consent` and saved `REFRESH_TOKEN`, but `youtube_client.py` never used it. Added `_refresh_access_token(account)` that exchanges the refresh token at `https://oauth2.googleapis.com/token`, persists the new access token to `.env` (`SOCIAL_YOUTUBE_{N}_ACCESS_TOKEN`) and `os.environ`, and made `_api_get` auto-retry once on `HTTP 401` when a refresh token is available. Transparent to all callers (skills, routines, agents). Requires `YOUTUBE_OAUTH_CLIENT_ID` and `YOUTUBE_OAUTH_CLIENT_SECRET` in `.env` (already present for any OAuth-connected account).

## [0.13.0] - 2026-04-10

### Added

- **2 native engineering agents** — bringing the Engineering Layer to **21 agents** (19 derived from oh-my-claudecode + 2 native):
  - **`helm-conductor`** (sonnet, teal) — cycle orchestration agent. Sequences features, decides "what next?", routes tasks to phase owners, coordinates sprint planning. Does not do the work of any phase itself; it orchestrates.
  - **`mirror-retro`** (sonnet, silver) — blameless retrospective agent. Reads the full feature folder end-to-end at the close of a feature, sprint, or incident, and produces a structured retro with "what worked / didn't / surprises / lessons / proposed memory updates". Requires explicit user approval before writing to `memory/`.
- **Canonical 6-phase engineering workflow** — `.claude/rules/dev-phases.md` documents the EvoNexus development lifecycle: **Discovery → Planning → Solutioning → Build → Verify → Retro**. Each phase has an owner, inputs, outputs, exit criteria, and skip conditions. Includes handoff protocol, inherited-context rules, and a feature-skip matrix (typo fixes skip most phases; high-stakes migrations use all 6).
- **Feature folders as unit of work** — `workspace/features/{feature-slug}/` groups all artifacts of one feature (discovery, PRD, plan, architecture, reviews, verification, retro) in one coherent location. Coexists with the type-based folders in `workspace/development/{plans,reviews,...}/` which remain the canonical location for standalone artifacts.
- **Oracle redesigned as consulting entry point** — `@oracle` is now the official entry door to EvoNexus. It runs a full 8-step flow: detect workspace state → run `initial-setup` if needed → business discovery interview → delegate capability mapping to `@scout-explorer` → delegate gap analysis to `@echo-analyst` → present the "potential" in business language → delegate plan production to `@compass-planner` → deliver with 3 autonomy paths (guided / autonomous / delegated). Oracle keeps the relationship with the user in a single voice while orchestrating specialist agents for the heavy lifting. Prime directive: the user must never be left with doubts — check-ins are mandatory before any side-effect action and after every substantive response.

### Changed

- **`@compass-planner` now produces PRD + Plan in Phase 2** — for non-trivial feature work, Compass first produces `[C]prd-{feature}.md` (problem, goals, non-goals, user stories, acceptance criteria in Given/When/Then, constraints, open questions) and then derives `[C]plan-{feature}.md` from it. Trivial changes skip the PRD. Handoff chain updated: Compass → Apex (Phase 3) → Bolt (Phase 4), not directly Compass → Bolt for non-trivial work.
- **`README.md`, `CLAUDE.md`, `docs/introduction.md`, `docs/architecture.md`, `docs/agents/overview.md`, `docs/agents/engineering-layer.md`, `site/src/pages/Home.tsx`, `public/cover.svg`** — agent count updated from 35 → 37 (16 business + 21 engineering). Engineering layer descriptions mention the 2 native additions (Helm, Mirror) and the 6-phase workflow.
- **`.claude/rules/agents.md`** — Engineering Layer bumped to 21 agents. Helm and Mirror marked with ⭐ as EvoNexus-native (not derived from oh-my-claudecode). Header reference added to `.claude/rules/dev-phases.md` as the canonical workflow.
- **`docs/agents/engineering-layer.md`** — the "19 Agents" section is now "21 Agents", split into Reasoning (opus/sonnet, 8 agents — Mirror added), Execution (sonnet, 11 agents — Helm added), and Speed (haiku, 2 agents, unchanged). New section "The 6-Phase Workflow" documents the canonical pipeline with phase owners and feature-folder convention.
- **`dashboard/frontend/src/pages/Agents.tsx`** — `AGENT_META` now includes `helm-conductor` and `mirror-retro` with icons (`Navigation`, `History`), colors, labels, and slash commands. `ENGINEERING_TIERS` updated: Mirror added to `reasoning`, Helm added to `execution`.
- **`NOTICE.md`** — clarifies that 19 of 21 engineering agents are derived from OMC; Helm and Mirror plus `dev-phases.md` are native EvoNexus additions.

### Documentation

- New canonical workflow doc: `.claude/rules/dev-phases.md` (auto-loaded by engineering agents as they work).
- Updated `docs/llms-full.txt` (regenerated via `make docs-build`).

## [0.12.0] - 2026-04-10

### Added

- **Engineering Layer (19 agents)** — complete software development team derived from [oh-my-claudecode](https://github.com/yeachan-heo/oh-my-claudecode) (MIT, by **Yeachan Heo**, v4.11.4). The layer is ortogonal to the existing Business Layer (16 agents). EvoNexus now ships with **35 specialized agents** in two layers + custom.
  - **Reasoning tier (opus, 7 agents):** `apex-architect`, `echo-analyst`, `compass-planner`, `raven-critic`, `lens-reviewer`, `zen-simplifier`, `vault-security`
  - **Execution tier (sonnet, 10 agents):** `bolt-executor`, `hawk-debugger`, `grid-tester`, `probe-qa`, `oath-verifier`, `trail-tracer`, `flow-git`, `scroll-docs`, `canvas-designer`, `prism-scientist`
  - **Speed tier (haiku, 2 agents):** `scout-explorer`, `quill-writer`
- **25 `dev-*` skills** organized in 3 tiers:
  - **Tier 1 — Core orchestration (15):** `dev-autopilot`, `dev-plan`, `dev-ralplan`, `dev-deep-interview`, `dev-deep-dive`, `dev-external-context`, `dev-trace`, `dev-verify`, `dev-ultraqa`, `dev-visual-verdict`, `dev-ai-slop-cleaner`, `dev-sciomc`, `dev-team`, `dev-ccg`, `dev-ralph`
  - **Tier 2 — Setup & infra (5):** `dev-mcp-setup`, `dev-deepinit`, `dev-project-session-manager`, `dev-configure-notifications`, `dev-release`
  - **Tier 3 — Meta utilities (5):** `dev-cancel`, `dev-remember`, `dev-ask`, `dev-learner`, `dev-skillify`
- **15 dev templates** in `.claude/templates/dev-*.md` — one per primary agent output: `dev-architecture-decision`, `dev-work-plan`, `dev-code-review`, `dev-bug-report`, `dev-verification-report`, `dev-deep-interview-spec`, `dev-security-audit`, `dev-test-strategy`, `dev-trace-report`, `dev-explore-report`, `dev-design-spec`, `dev-analysis-report`, `dev-research-brief`, `dev-critique`, `dev-simplification-report`.
- **`workspace/development/` folder** — engineering layer working directory with 7 subfolders (`architecture`, `plans`, `specs`, `reviews`, `debug`, `verifications`, `research`) and a `README.md`. Distinct from `workspace/projects/` (active git repos).
- **`NOTICE.md`** — third-party attribution for `oh-my-claudecode` with full MIT license, version pinned at v4.11.4, modifications listed (renaming, namespace `dev-*`, memory pattern adaptation, runtime stripping).
- **`docs/agents/engineering-layer.md`** — dedicated documentation page covering tiers, agents, pipelines, working folder, templates, memory pattern, cross-layer handoffs, and attribution.
- **Two-layer dashboard categorization** — `dashboard/frontend/src/pages/Agents.tsx` now categorizes agents into Business / Engineering (with reasoning/execution/speed tiers) / Custom, with auto-derived slash commands and dynamic icon assignment.

### Changed

- **Slash command naming** — all 35 core agents now use the **full agent name** as the slash command (e.g., `/clawdia-assistant`, `/flux-finance`, `/apex-architect`, `/bolt-executor`) instead of short aliases (`/clawdia`, `/flux`, `/apex`, `/bolt`). The only exception is `/oracle` which is already a single word. The 16 short business commands and the 13 short engineering commands were removed.
- **`README.md` updated** — agent count (16 → 35), skill count (~130 → ~137), Engineering Layer mention with attribution, two-layer description.
- **`CLAUDE.md` updated** — Active Projects table now lists "Engineering Layer" as delivered (v0.12.0). Folder Structure includes `workspace/development/`. "What Claude Should Do" rules cover both layers and link to NOTICE.md.
- **`docs/introduction.md`** — "35 specialized agents in two layers" framing, expanded "Chatbot vs EvoNexus" comparison table including engineering scenarios.
- **`docs/architecture.md`** — diagram refreshed to show 35 agents in two ortogonal layers, ~137 skills, attribution to Yeachan Heo.
- **`docs/agents/overview.md`** — Two-layer intro, 19 engineering agents grouped by tier, all 16 business agents updated with full slash commands.
- **`docs/skills/overview.md`** — engineering layer skills section with all 25 `dev-*` skills grouped by tier; total skill count updated to ~137.
- **`docs/agents/{16 individual}.md`** — slash commands updated to full names (e.g., `/clawdia` → `/clawdia-assistant`).
- **`site/src/pages/Home.tsx`** — `35 agents` / `137+ skills` stats, two-layer feature card, "Meet your new team" section now shows both Business Layer (16 cards) and Engineering Layer (19 cards) with full slash commands and attribution link.
- **`site/public/docs/`** — full mirror sync via `make docs-build`.
- **`docs/llms-full.txt`** — regenerated with 62 docs (added `engineering-layer.md`).
- **`.claude/rules/agents.md`** — both layers documented (16 + 19) with cross-layer handoff guidance.
- **`.claude/rules/skills.md`** — `dev-` category added with all 25 skills listed; total bumped to ~137.
- **`ROADMAP.md`** — new `v0.12 — Engineering Layer` section marking the deliverable as `[x]` with full agent / skill / template enumeration and recommended pipelines.

### Documentation

- **Engineering Layer attribution** — `NOTICE.md` at repo root + `README.md` Credits & Acknowledgments section + per-agent attribution comments + dedicated `docs/agents/engineering-layer.md`.
- **Pattern compliance** — all 19 engineering agents follow the EvoNexus standard pattern (rich frontmatter with Examples, Workspace Context, Shared Knowledge Base, Working Folder, Identity, Anti-patterns, Domain, How You Work, Skills You Can Use, Handoffs, Output Format, Continuity). Verified by `@lens-reviewer`, 3 fixes applied (oath-verifier `disallowedTools`, raven-critic and trail-tracer `Skills You Can Use` section).

## [0.11.4] - 2026-04-10

### Changed
- **Backup excludes reconstructible directories** — `backup.py` now excludes top-level dirs that don't contain user data: `site/`, `backups/`, `.venv/`, `_evo/`, `_evo-output/`. Also expanded `EXCLUDE_DIRS` to cover more cache/build folders (`.next`, `.cache`, `.local`, `build`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`). Reduces typical backup from ~63k files / 1GB to ~800 files / ~900MB while keeping all user data (workspace, agent-memory, custom skills, dashboard DB).
- **Custom skill convention unified** — product-specific skills (`int-licensing`, `int-whatsapp`, `prod-licensing-daily/weekly/monthly`, and the 45 `evo-*` skills) renamed to `custom-*` prefix so they're automatically gitignored via the existing `.claude/skills/custom-*` pattern. The `name:` frontmatter field in each `SKILL.md` was updated to match the new folder name (50 skills total).
- **Agent skill references updated** — `atlas-project`, `dex-data`, `nova-product`, `pulse-community` now reference the `custom-*` skill names instead of the old prefixed names.
- **`.gitignore` simplified** — removed the 5 explicit per-skill entries; the `.claude/skills/custom-*` pattern covers all custom skills.

## [0.11.3] - 2026-04-09

### Fixed
- **Stale folder references in docs** — replaced legacy Obsidian-style paths (`01 Daily Logs/`, `02 Projects/`, `05 Financeiro/`, `09 Estrategia/`) with new `workspace/` structure (`workspace/daily-logs/`, `workspace/projects/`, `workspace/finance/`, `workspace/strategy/`) in `CLAUDE.md`, status command, creating-skills/routines/updating guides, ops-vendor-review skill, and `llms-full.txt`.

### Changed
- **`.gitignore`** — added `config/triggers.yaml` to gitignored configs.

## [0.11.2] - 2026-04-09

### Added
- **SECURITY.md** — vulnerability disclosure policy with private reporting channels and contributor security guidelines

### Fixed
- **Command injection in dashboard backend** — replaced all `subprocess.run(..., shell=True)` with argument-list invocations across `systems.py`, `services.py`, and `tasks.py`; added container name validation and path traversal protection
- **WebSocket authentication bypass** — terminal WebSocket handler now verifies `current_user.is_authenticated` (previously skipped `before_request` middleware)
- **Code injection in MemPalace mining** — replaced f-string quote interpolation with `repr()` to prevent Python code injection via crafted path/wing values
- **Path traversal in MemPalace sources** — source paths now validated against home directory and workspace boundaries

## [0.11.1] - 2026-04-09

### Changed
- **Rebrand OpenClaude → EvoNexus** — full platform rename across ~80 files: docs, dashboard, CLI, site, templates, skills, agents, Docker, env vars (`OPENCLAUDE_PORT` → `EVONEXUS_PORT`), npm package (`@evoapi/evo-nexus`), GitHub repo (`EvolutionAPI/evo-nexus`), cover SVG, and all internal references.

## [0.11.0] - 2026-04-09

### Added
- **Workspace backup & restore** — new `backup.py` script that exports all gitignored user data (memory, agent-memory, config, dashboard DB, logs, custom agents/commands/templates/routines, `.env`) as a ZIP with manifest. Supports local storage (`backups/`) and S3-compatible cloud buckets. Restore with merge (skip existing) or replace (overwrite) mode.
- **Daily Backup routine** — core routine (`ADWs/routines/backup.py`) runs at 21:00 daily via scheduler. Pure Python (systematic, no AI, no tokens). Auto-uploads to S3 if `BACKUP_S3_BUCKET` is configured.
- **Backup dashboard page** — `/backups` page to list, create, download, restore, and delete backups from the browser. Shows S3 config status, backup metadata from manifest, and restore mode selection modal.
- **Trigger registry** — reactive event triggers (webhook & event-based) that execute skills or routines in response to external events. Supports GitHub, Stripe, Linear, Telegram, Discord, and custom webhooks with HMAC signature validation.
- **Triggers dashboard page** — `/triggers` page to create, edit, delete, test, enable/disable triggers. Copy webhook URL, regenerate secrets, view execution history.
- **`trigger-registry` skill** — CLI skill to create, manage, and test triggers.
- **Resume Claude sessions in chat** — dashboard chat now lists active/resumable Claude sessions with `--resume` support.
- **Makefile targets** — `make backup`, `make backup-s3`, `make restore`, `make backup-list`, `make backup-daily`.
- **S3 backup env vars** — `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`, `AWS_ENDPOINT_URL` in `.env.example`.

### Changed
- **Core routines** — 5 → 6 (Daily Backup added)
- **Dashboard screenshots** — all page screenshots optimized (50-70% smaller file sizes)
- **ROUTINES.md** — added Triggers and Daily Backup documentation sections
- **docs/** — updated core-routines, makefile reference, env-variables reference, dashboard overview

## [0.10.1] - 2026-04-09

### Fixed
- **Site and docs counts** — updated all references from 9/10 agents to 16, from ~68/~80 skills to ~130, across site Home page, introduction, architecture, getting-started, using-agents, initial-setup, dashboard overview, and evolution-foundation case study
- **Site Home features** — added Channels, Agent Teams, and Scheduled Tasks to the features grid; updated agent showcase to show all 16 agents
- **Channels docs in pt-BR** — rewrote `docs/guides/channels.md` and `docs/guides/channels-reference.md` to English (docs should always be in English)
- **README screenshots** — restored screenshots section using HTML `<img>` tags with consistent sizing (were broken by markdown table layout)

## [0.10.0] - 2026-04-09

### Added
- **6 new core agents** — Mako (Marketing), Aria (HR/People), Zara (Customer Success), Lex (Legal/Compliance), Nova (Product), Dex (Data/BI). Each with system prompt, slash command, dashboard card with icon and color, and dedicated skills.
- **~80 new skills** — HR (`hr-*`), Legal (`legal-*`), Ops (`ops-*`), Product Management (`pm-*`), Customer Success (`cs-*`), Data/BI (`data-*`), Marketing (`mkt-*`). Skill count: ~68 → ~180.
- **Channels** — bidirectional chat bridges that push messages into a running Claude Code session. Discord and iMessage channels added alongside existing Telegram. Each runs as a background screen session.
- **Channel documentation** — `docs/guides/channels.md` (setup guide for all 3 channels) and `docs/guides/channels-reference.md` (technical reference for building custom channels/webhooks).
- **Dashboard channels section** — Services page now shows "Channels" as a separate section with Telegram, Discord Channel, and iMessage Channel cards (Start/Stop/Logs).
- **Agent documentation** — individual doc pages for all 16 agents in `docs/agents/`.
- **Makefile targets** — `discord-channel`, `discord-channel-stop`, `discord-channel-attach`, `imessage`, `imessage-stop`, `imessage-attach`.

### Changed
- **Agent count** — 10 → 16 core agents across README, docs, dashboard, and rules
- **Skill count** — ~68 → ~180 across README, docs, and dashboard
- **Dashboard AGENT_META** — all 16 agents now have dedicated icons, colors, and command badges
- **README** — updated architecture diagram, agent list, skill count, dashboard features, and workspace structure

## [0.9.0] - 2026-04-09

### Added
- **Custom agents** — user-created agents with `custom-` prefix. Gitignored, auto-discovered by dashboard (gray "custom" badge vs green "core" badge). Backend returns `custom`, `color`, `model` fields from frontmatter.
- **Oracle agent** — 10th core agent. `/oracle` workspace knowledge agent that answers questions about agents, skills, routines, integrations, and configuration by reading the actual documentation. No RAG needed — reads `docs/llms-full.txt` and source files directly.
- **`create-agent` skill** — conversational interface to create custom agents (name, domain, personality, model, color, memory folder, slash command)
- **`create-command` skill** — conversational interface to create standalone slash commands for Claude Code

### Changed
- **Agent count** — 9 → 10 core agents (Oracle added) across README, docs, and rules
- **Dashboard Agents page** — core/custom badges, dynamic colors from frontmatter for custom agents, separate core/custom counters in stats bar
- **Documentation** — updated agents overview, creating-agents guide (core vs custom section), skills overview

## [0.8.0] - 2026-04-09

### Added
- **Scheduled Tasks** — new one-off task scheduling system. Schedule a skill, prompt, or script to run at a specific date/time without creating a full routine. Dashboard page at `/tasks` with create/edit/cancel/run-now/view-result. API CRUD at `/api/tasks`. Scheduler checks pending tasks every 30 seconds.
- **`schedule-task` skill** — conversational interface to create scheduled tasks ("agendar pra sexta 10h", "schedule this for tomorrow")
- **Dynamic routine discovery** — `ROUTINE_SCRIPTS` and `SCRIPT_AGENTS` are no longer hardcoded. Agent and script mappings are built dynamically by scanning `ADWs/routines/` scripts and extracting metadata from docstrings (`via AgentName` pattern). New scripts are auto-discovered.
- **`make run R=<id>`** — generic dynamic runner for any routine (core or custom)
- **`make list-routines`** — lists all discovered routines with agent, script, and name
- **Workspace file browser** — reports page replaced with a full file browser that navigates workspace folders

### Changed
- **Makefile cleaned** — custom routine targets (user-specific) removed from Makefile. Only core routine targets remain (`morning`, `eod`, `memory`, `memory-lint`, `weekly`). Custom routines run via `make run R=<id>`.
- **`ROUTINES.md`** — expanded with scheduled tasks docs, dynamic discovery, and updated manual execution section
- **Documentation** — new `docs/routines/scheduled-tasks.md`, updated makefile reference, dashboard overview, creating-routines guide, and skills overview

## [0.7.0] - 2026-04-09

### Added
- **Systematic routines** — new `run_script()` function in `ADWs/runner.py` for pure Python routines that run without Claude CLI, without AI, without tokens. Same logging/metrics infrastructure, but cost=$0 and duration in seconds instead of minutes.
- **`create-routine` skill updated** — now asks "AI or systematic?" and generates the correct script pattern. For systematic routines, Claude writes the Python logic once at creation time, then the script runs on its own forever.
- **Example routine** — `ADWs/routines/examples/log_cleanup.py` demonstrates the systematic pattern (deletes logs older than 30 days)
- **"systematic" badge** — dashboard Scheduler and Routines pages show a gray "systematic" badge for system routines instead of green `@agent`
- **Site docs CSS overhaul** — replaced fragile custom marked renderers with CSS-based styling on `.docs-content`. Tables, lists, code blocks, and all markdown elements now render correctly with the dark theme.
- **OAuth redirect URLs** — documented redirect URIs for YouTube, Instagram, and LinkedIn OAuth setup

### Changed
- **ROADMAP** — "Agent-less routines" marked as done

## [0.6.1] - 2026-04-09

### Added
- **Core routines documentation** (`docs/routines/core-routines.md`) — detailed explanation of all 5 core routines: what they do, why they matter, and how they form the daily loop
- **Memory Lint promoted to core** — moved from `ADWs/routines/custom/` to `ADWs/routines/`, hardcoded in `scheduler.py` (Sunday 09:00). Now 5 core routines instead of 4
- **Release skill** now syncs screenshots (`public/print-*.png` → `site/public/assets/`) on every release

### Changed
- **Dashboard pages redesigned** — 12 pages (Audit, Config, Costs, Files, Integrations, Memory, Reports, Roles, Routines, Scheduler, Skills, Systems, Templates, Users) with consistent dark theme and improved UX
- **Integration count** — 19 → 17 (removed internal-only Licensing and WhatsApp docs from public documentation)
- **Memory system** — LLM Wiki pattern: ingest propagation, weekly lint, centralized index, and operation log

### Removed
- **`docs/integrations/licensing.md`** — internal only, not public
- **`docs/integrations/whatsapp.md`** — internal only, not public

### Fixed
- **Dashboard build** — removed unused `totalTokens` variable in Costs page that blocked TypeScript compilation

## [0.6.0] - 2026-04-09

### Added
- **Evolution API skill** (`int-evolution-api`) — 33 commands: instances, messages (text, media, location, contact, buttons, lists, polls), chats, groups, webhooks
- **Evolution Go skill** (`int-evolution-go`) — 24 commands: instances, messages, reactions, presence
- **Evo CRM skill** (`int-evo-crm`) — 48 commands: contacts, conversations, messages, inboxes, pipelines, labels
- **Integration docs** — 3 new guides: `docs/integrations/evolution-api.md`, `evolution-go.md`, `evo-crm.md`
- **Dashboard integrations** — Evolution API, Evolution Go, and Evo CRM cards on Integrations page
- **`.env.example`** — added `EVOLUTION_API_URL/KEY`, `EVOLUTION_GO_URL/KEY`, `EVO_CRM_URL/TOKEN`

### Changed
- **Integration count** — 16 → 17 across README, site, and docs (removed internal-only Licensing and WhatsApp docs)
- **Community members** — 7,000+ → 17,000+ on site
- **v0.4 roadmap complete** — all 13 items done, Evolution product skills was the last one

## [0.5.1] - 2026-04-09

### Changed
- **Docs markdown rendering** — replaced regex parser with `marked` library. Code blocks, ASCII art, and nested formatting now render correctly on the site.
- **README and site** — `npx @evoapi/evo-nexus` is now the primary install method. Git clone shown as alternative.
- **Release skill** — `make docs-build` and frontend rebuild are now mandatory on every release (not conditional).

### Fixed
- **Site /docs navigation** — nested doc pages (e.g., `/docs/guides/creating-routines`) no longer 404. Switched from `useRoute` wildcard to direct URL parsing.
- **Site route matching** — changed from `/docs/:slug+` to `/docs/*` for reliable wouter matching.
- **CLI default directory** — `npx @evoapi/evo-nexus` without args now clones into current directory (`.`), not a subfolder.
- **Site CI build** — added missing `print-agents.png` to site assets.
- **Docs sync** — site now serves updated docs matching the repo (was stale).

## [0.5.0] - 2026-04-09

### Added
- **Active agent visualization** — Claude Code hooks (`PreToolUse`, `Stop`) track agent launches in `agent-status.json`. Dashboard polls `/api/agents/active` and shows animated "RUNNING" badges on agent cards and overview.
- **Agents page redesign** — unique icons and accent colors per agent, slash command badges, memory count pills, status dots, hover glow effects.
- **Overview page redesign** — stat cards with icons and trend indicators, active agents bar, quick actions row (Morning Briefing, Chat, Costs, GitHub), improved reports and routines tables with relative timestamps.
- **Claude Code hooks** — `agent-tracker.sh` hook registered in `settings.json` for real-time agent activity tracking.
- **Project settings.json** — permissions (allow/deny rules), hooks configuration.
- **Inner-loop commands** — `/status` (workspace status) and `/review` (recent changes + next actions).
- **Default system: Claude Status** — `seed_systems()` creates Anthropic status page as default external system on first boot.
- **Public roadmap** — `ROADMAP.md` with community input via GitHub discussions.

### Changed
- **CLAUDE.md split** — reduced from 263 to 128 lines. Detailed config moved to `.claude/rules/` (agents, integrations, routines, skills) — auto-loaded by Claude Code.
- **All 9 agent prompts generalized** — removed hardcoded personal references (Omie, Linear, Discord Evolution, Brazilian formats, etc.). User-specific context preserved in `_improvements.md` per agent memory folder.
- **Rules and commands translated** — all `.claude/rules/` and `.claude/commands/` files translated from Portuguese to English.

## [0.4.1] - 2026-04-09

### Added
- **Docker Compose for dashboard** — `Dockerfile.dashboard` (multi-stage: node + python) + `docker-compose.yml` with dashboard, telegram, and runner services. `make docker-dashboard` to start.
- **Dashboard CI** — GitHub Actions workflow builds and pushes dashboard image to `ghcr.io/evolutionapi/evo-nexus/dashboard` on push/release
- **npm CI** — GitHub Actions workflow publishes CLI to npm on release (requires `NPM_TOKEN` secret)

### Changed
- **Sidebar reorganized** — 5 collapsible groups (Main, Operations, Data, System, Admin) with collapse state persisted in localStorage
- **Scheduler removed from docker-compose** — runs embedded in dashboard, not as separate service
- **`make docker-up` → `make docker-telegram`** — reflects that only Telegram is a separate Docker service
- **Public roadmap updated** — removed internal Future/Research section, marked completed items

## [0.4.0] - 2026-04-09

### Added
- **CLI installer** — `npx @evoapi/evo-nexus` clones repo, checks prerequisites, installs deps, runs setup wizard, and builds dashboard
- **Version indicator in dashboard** — sidebar footer shows current version; `/api/version/check` compares against latest GitHub release with 1h cache
- **Public roadmap** — `ROADMAP.md` with 4 phases (v0.4 → Future), community input via GitHub discussions
- **Update guide** — `docs/guides/updating.md` with git pull, Docker, and custom content preservation instructions

### Changed
- **Privacy-first licensing** — removed heartbeat thread, deactivate endpoint, and shutdown hook. Only initial registration remains (who installed). No monitoring, no kill switch, no telemetry.
- **Licensing version** — now reads from `pyproject.toml` dynamically instead of hardcoded constant

### Fixed
- **nginx 403 on `/docs/`** — removed `$uri/` from `try_files` so directory paths fall through to SPA instead of returning Forbidden
- **`.gitignore` formatting** — `site/lib/` and `mempalace.yaml` were concatenated on one line
- **User-specific files removed from git** — `mempalace.yaml` and `entities.json` no longer tracked

## [0.3.2] - 2026-04-08

### Added
- **Docs page on site** (`/docs`) — full documentation viewer with sidebar, search, and markdown rendering
- **Auto-version system** — `pyproject.toml` is single source of truth, injected into site (Vite `__APP_VERSION__`), dashboard (`/api/version`), and CI (Docker build-arg)
- **Pre-build docs index** — `scripts/build-docs-index.mjs` generates `docs-index.json` at build time
- **`/api/version` endpoint** — dashboard serves current version from `pyproject.toml`

### Changed
- **`make docs-build`** — now also syncs `docs/` to `site/public/docs/`
- **Docs links** in landing page point to `/docs` (internal route, not dashboard)
- **Site version badge** — reads from `pyproject.toml` dynamically instead of hardcoded

## [0.3.1] - 2026-04-08

### Added
- **Landing page** (`site/`) — standalone React + Vite static site, extracted from Replit monorepo
- **Docker support for site** — multi-stage Dockerfile (node build → nginx serve) + docker-compose
- **GitHub Actions CI** — workflow builds site image and pushes to `ghcr.io/evolutionapi/evo-nexus/site` on push/release
- **Docs bundled in site image** — `docs/` copied into site build context automatically

### Changed
- **`.gitignore` updated** — site tracked in repo (Replit artifacts, node_modules, dist excluded)
- **Site assets renamed** — clean filenames (`logo.png`, `print-overview.png`, etc.) instead of Replit hashes

## [0.3.0] - 2026-04-08

### Added
- **Public Documentation** (`/docs`) — full docs site inside the dashboard, accessible without auth
- **MemPalace** — semantic knowledge base with ChromaDB for code/doc search (optional)
- **Content search** — docs search now matches inside file content, not just titles
- **llms-full.txt** — pre-generated plain text with all docs for LLM consumption (`/docs/llms-full.txt`)
- **23 routine examples** and **21 template examples** shipped with repo
- **14 documentation screenshots** in `docs/imgs/`
- **Comprehensive docs** — 28 markdown files across 9 sections (guides, dashboard, agents, skills, routines, integrations, real-world, reference)
- **Practical usage guides** — how to run routines, invoke agents, create custom skills

### Changed
- **Unofficial disclaimer** — README, docs, and landing page clearly state "unofficial, not affiliated with Anthropic"
- **Positioning** — "compatible with Claude Code and other LLM tooling" (not "purpose-built for")
- **Enterprise-safe language** — "integrates with" instead of "leverages", opens door for multi-provider future
- **Docs sidebar** — logical section ordering, section icons, content preview in search
- **llms-full.txt** — served as static pre-generated file (instant load, no on-the-fly concatenation)
- **i18n** — final cleanup, 18 files translated from Portuguese to English

### Fixed
- `/docs/llms-full.txt` redirect (was showing docs sidebar with "Loading..." instead of plain text)
- Screenshots with personal data removed and replaced
- 10 doc files corrected after full audit

## [0.2.0] - 2026-04-09

### Added
- **Core vs Custom split** — routines, templates, and skills separated into core (tracked) and custom (gitignored)
- **Create Routine skill** (`create-routine`) — guides users through creating custom routines step by step
- **Scheduler embedded in dashboard** — runs automatically with `make dashboard-app`, no separate process
- **Core/Custom badges** — scheduled routines and templates show green "core" or gray "custom" labels
- **Custom routines from YAML** — scheduler loads custom routines dynamically from `config/routines.yaml`
- **.env editor** — edit environment variables directly from the Config page in the dashboard
- **Auto-discover reports** — Reports page scans entire `workspace/` recursively, no hardcoded paths

### Changed
- **Routines reorganized** — 4 core routines in `ADWs/routines/`, custom in `ADWs/routines/custom/` (gitignored)
- **Templates reorganized** — 2 core HTML + 4 core MD templates, custom in `custom/` subfolders (gitignored)
- **`ADWs/rotinas/` renamed to `ADWs/routines/`** — full English naming
- **Agent files renamed** — `flux-financeiro` → `flux-finance`, `nex-comercial` → `nex-sales`
- **59 evo-* skills removed** — Evo Method is a separate project, skills gitignored
- **Docker removed from Services** — use Systems CRUD for Docker container management
- **ROUTINES.md rewritten** — generic, documents core vs custom split and YAML config format
- **scheduler.py rewritten** — only 4 core routines hardcoded, custom loaded from YAML
- **README updated** — correct agent names (`/clawdia`, `/flux`, `/atlas`, etc.), 4 core routines, ~67 skills

### Removed
- **ROADMAP.md** from Config page (file no longer exists)
- **Docker section** from Services page
- **Specific routine schedules** from scheduler.py (moved to user's `config/routines.yaml`)
- **Custom routines from git** — 23 scripts moved to gitignored `custom/` directory
- **Custom templates from git** — 15 HTML + 6 MD templates moved to gitignored `custom/` directories

### Fixed
- Custom routine scripts `sys.path` adjusted for `custom/` subdirectory (3 levels up for runner)
- Scheduler parser strips `custom/` prefix for agent mapping
- `SCRIPT_AGENTS` moved to module level (was inaccessible from `_load_yaml_routines`)
- Telegram `screen` command removed unsupported `-Logfile` flag
- Remaining Portuguese translated in skill bodies

## [0.1.1] - 2026-04-08

### Added
- **Silent Licensing** — automatic registration via Evolution Foundation licensing server
- **Systems CRUD** — register and manage apps/services from the dashboard
- **Roles & Permissions** — custom roles with granular permission matrix
- **Onboarding Skill** (`initial-setup`) — guides new users through the workspace
- **Screenshots** in README (overview, chat, integrations, costs)

### Changed
- **English-first codebase** — translated agents, skills, templates, routines, and config
- **Workspace folders** renamed from PT to EN (`workspace/daily-logs`, etc.)
- **Setup wizard** simplified — all agents enabled by default
- **HTML templates** standardized with Evolution Foundation branding
- **Makefile** auto-detects `uv` or falls back to `python3`
- All Python dependencies consolidated in `pyproject.toml`

### Removed
- **Evo Method** (`_evo/`) — separate project
- **Proprietary skills** — licensing and whatsapp excluded
- **Portuguese folder names** (01-09) — replaced with `workspace/`

### Fixed
- 16 bug fixes (scheduler logs, SQLite WAL, auth permissions, dates, etc.)

## [0.1.0] - 2026-04-08

### Added
- Initial open source release
- 9 Specialized Agents, ~67 Skills, 4 core routines
- Web Dashboard with auth, roles, web terminal, service management
- Integration clients (Stripe, Omie, YouTube, Instagram, LinkedIn, Discord)
- ADW Runner with token/cost tracking
- Persistent memory system
- Setup wizard (CLI + web)
