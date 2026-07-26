#!/usr/bin/env bash
# ============================================================================
# start-dashboard.sh — multi-process entrypoint for the dashboard container.
#
# The dashboard needs TWO processes running simultaneously:
#   * Flask backend        → :8080   (/api/*, static SPA, OAuth, Providers...)
#   * Node terminal-server → :32352  (/terminal/*, embedded CLI sessions)
#
# The React frontend calls /terminal/* on the same origin and expects the
# reverse proxy (Traefik) to route it to :32352. If the terminal-server is
# not running inside the container, every "open agent chat" click fails
# with "Could not reach terminal-server".
#
# This wrapper starts both processes, then exec-waits. If EITHER dies, we
# kill the other and exit with a non-zero code so Docker/Swarm restarts
# the whole container — keeping both processes in sync.
# ============================================================================
set -euo pipefail

TERMINAL_PORT="${TERMINAL_SERVER_PORT:-32352}"
FLASK_PORT="${EVONEXUS_PORT:-8080}"
HERMES_UI_PORT="${HERMES_UI_PORT:-9119}"
HERMES_API_PORT="${HERMES_API_PORT:-8642}"

echo "[start-dashboard] terminal-server on :${TERMINAL_PORT}, Flask on :${FLASK_PORT}"

# ----------------------------------------------------------------------------
# Pre-seed Claude Code global settings so the first-run theme/onboarding
# prompts are skipped on every new agent terminal. Each agent runs in its
# own working directory, which Claude Code treats as a separate project —
# without this, the user has to pick a theme on every single agent.
# Only writes the file if it doesn't already exist (preserves user choices).
# ----------------------------------------------------------------------------
mkdir -p /root/.claude
if [ ! -f /root/.claude/settings.json ]; then
    echo "[start-dashboard] seeding /root/.claude/settings.json with default theme"
    cat > /root/.claude/settings.json <<'EOF'
{
  "theme": "dark",
  "hasCompletedOnboarding": true,
  "hasSeenWelcome": true,
  "telemetry": false
}
EOF
fi

# ----------------------------------------------------------------------------
# Restore /root/.claude.json from the most recent backup when missing.
#
# Claude Code's main config (theme, OAuth tokens, per-project state) lives
# at /root/.claude.json — a SIBLING of the /root/.claude/ directory, NOT
# inside it. The Swarm volume mounts /root/.claude/, so /root/.claude.json
# sits in the container's writable layer and is wiped on every redeploy.
# Result: theme picker and onboarding reappear on every release.
#
# Claude Code itself writes timestamped backups into /root/.claude/backups/
# (which IS in the volume). We just need to restore the latest on startup
# if the main file is missing. If no backup exists either, seed a minimal
# config so the first-run prompts are skipped.
# ----------------------------------------------------------------------------
if [ ! -f /root/.claude.json ]; then
    latest_backup=$(ls -t /root/.claude/backups/.claude.json.backup.* 2>/dev/null | head -n1 || true)
    if [ -n "${latest_backup:-}" ] && [ -f "${latest_backup}" ]; then
        echo "[start-dashboard] restoring /root/.claude.json from ${latest_backup}"
        cp "${latest_backup}" /root/.claude.json
    else
        echo "[start-dashboard] seeding minimal /root/.claude.json (no backup found)"
        cat > /root/.claude.json <<'EOF'
{
  "theme": "dark",
  "hasCompletedOnboarding": true,
  "hasSeenWelcome": true,
  "bypassPermissionsModeAccepted": true,
  "telemetry": false
}
EOF
    fi
fi

# ----------------------------------------------------------------------------
# Sync EvoNexus agents → Hermes profiles.
#
# Each .claude/agents/*.md becomes a Hermes profile whose SOUL.md contains
# the full agent instructions. This lets the terminal spawn agents with
# `hermes -p <agent> chat` and get the complete persona, not just a one-line
# description.
# ----------------------------------------------------------------------------
if command -v hermes &>/dev/null; then
    AGENTS_DIR="/workspace/.claude/agents"
    HERMES_PROFILES="/root/.hermes/profiles"
    if [ -d "${AGENTS_DIR}" ]; then
        echo "[start-dashboard] syncing EvoNexus agents → Hermes profiles"
        for agent_file in "${AGENTS_DIR}"/*.md; do
            [ -f "${agent_file}" ] || continue
            slug=$(basename "${agent_file}" .md)
            profile_dir="${HERMES_PROFILES}/${slug}"
            mkdir -p "${profile_dir}"

            # Extract body after YAML frontmatter (--- ... ---)
            body=$(sed -n '/^---$/,/^---$/!p' "${agent_file}" | tail -n +1)
            if [ -z "${body}" ]; then
                body="You are the ${slug} agent."
            fi

            # Copy base config (.env, config.yaml) from default profile so
            # each agent profile inherits provider/API key settings.
            HERMES_HOME="/root/.hermes"
            for cfg in ".env" "config.yaml" "auth.json"; do
                if [ -f "${HERMES_HOME}/${cfg}" ] && [ ! -f "${profile_dir}/${cfg}" ]; then
                    cp "${HERMES_HOME}/${cfg}" "${profile_dir}/${cfg}"
                fi
            done

            # Only write SOUL.md if changed (avoid unnecessary disk writes)
            soul_file="${profile_dir}/SOUL.md"
            if [ ! -f "${soul_file}" ] || [ "$(cat "${soul_file}")" != "${body}" ]; then
                echo "${body}" > "${soul_file}"
                echo "[start-dashboard]   synced profile: ${slug}"
            fi
        done
        echo "[start-dashboard] agent→profile sync done"
    fi
fi

# Start terminal-server in the background
node /workspace/dashboard/terminal-server/bin/server.js --port "${TERMINAL_PORT}" &
TERMINAL_PID=$!

# Start Hermes dashboard + API server in the background (if hermes is installed).
# Non-critical: if hermes crashes, log it but don't kill the container.
HERMES_PID=""
# EvoNexus-prefixed env vars → Hermes native env vars.
# Use EVONEXUS_HERMES_* in docker-compose to avoid clashing with
# standalone Hermes installations.
# Hermes dashboard auth for remote access.
# When EVONEXUS_HERMES_USERNAME/PASSWORD are set, Hermes binds 0.0.0.0
# (enabling remote access via Tailscale) and requires Basic Auth.
# The proxy injects credentials so the iframe remains seamless.
HERMES_ADMIN_USER="${EVONEXUS_HERMES_USERNAME:-}"
HERMES_ADMIN_PASS="${EVONEXUS_HERMES_PASSWORD:-}"
if [ -n "$HERMES_ADMIN_USER" ] && [ -n "$HERMES_ADMIN_PASS" ]; then
    HERMES_DASHBOARD_HOST="0.0.0.0"
    # Hermes basic_auth plugin reads dashboard.basic_auth from
    # ~/.hermes/config.yaml (NOT ~/.config/hermes/) and expects scrypt
    # hashes (NOT bcrypt). It also accepts env vars as fallback:
    #   HERMES_DASHBOARD_BASIC_AUTH_USERNAME
    #   HERMES_DASHBOARD_BASIC_AUTH_PASSWORD (plaintext — plugin hashes internally)
    # We use env vars to avoid path/hash-format fragility.
    export HERMES_DASHBOARD_BASIC_AUTH_USERNAME="$HERMES_ADMIN_USER"
    export HERMES_DASHBOARD_BASIC_AUTH_PASSWORD="$HERMES_ADMIN_PASS"
else
    HERMES_DASHBOARD_HOST="127.0.0.1"
fi

if command -v hermes &>/dev/null; then
    export HERMES_DASHBOARD=1
    export HERMES_DASHBOARD_HOST="${HERMES_DASHBOARD_HOST}"
    export HERMES_DASHBOARD_PORT="${HERMES_UI_PORT}"
    HERMES_API_ENABLED="${EVONEXUS_HERMES_API_ENABLED:-false}"
    if [ "${HERMES_API_ENABLED}" = "true" ] && [ -z "${EVONEXUS_HERMES_API_KEY:-}" ]; then
        echo "[start-dashboard] EVONEXUS_HERMES_API_KEY is required when EVONEXUS_HERMES_API_ENABLED=true" >&2
        exit 1
    fi
    export API_SERVER_ENABLED="${HERMES_API_ENABLED}"
    export API_SERVER_HOST=127.0.0.1
    export API_SERVER_PORT="${HERMES_API_PORT}"
    [ "${HERMES_API_ENABLED}" = "true" ] && export API_SERVER_KEY="${EVONEXUS_HERMES_API_KEY}"

    echo "[start-dashboard] starting Hermes dashboard on :${HERMES_UI_PORT} (host=${HERMES_DASHBOARD_HOST})"
    (hermes dashboard --port "${HERMES_UI_PORT}" --host "${HERMES_DASHBOARD_HOST}" --no-open || echo "[start-dashboard] hermes dashboard exited with code $?") &
    HERMES_PID=$!
else
    echo "[start-dashboard] hermes not found, skipping Hermes dashboard"
fi

# Start tailscaled if installed (for Tailscale VPN integration).
# Uses --tun=userspace-networking so the daemon needs NO NET_ADMIN cap and
# NO /dev/net/tun device, which keeps the dashboard compatible with restricted
# container runtimes that do not grant network-administration capabilities.
# Trade-off: the node appears in the tailnet and is reachable via MagicDNS
# (inbound TCP works), but it CANNOT act as a subnet router / exit node.
TAILSCALED_PID=""
if command -v tailscaled &>/dev/null; then
    mkdir -p /var/run/tailscale
    echo "[start-dashboard] starting tailscaled (userspace-networking, no NET_ADMIN needed)"
    tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock --tun=userspace-networking &>/var/log/tailscaled.log &
    TAILSCALED_PID=$!
    sleep 1
else
    echo "[start-dashboard] tailscaled not found, skipping VPN integration"
fi

# Apply schema changes before any application process can use the database.
echo "[start-dashboard] applying database migrations"
(cd /workspace/dashboard/alembic && uv run python -m alembic upgrade head)

# Start Flask in the background
uv run python /workspace/dashboard/backend/app.py &
FLASK_PID=$!

# When this script exits for any reason, kill both children
# shellcheck disable=SC2317  # invoked by trap below
cleanup() {
    echo "[start-dashboard] shutting down (terminal=${TERMINAL_PID}, flask=${FLASK_PID}, hermes=${HERMES_PID:-none}, tailscaled=${TAILSCALED_PID:-none})"
    kill "${TERMINAL_PID}" "${FLASK_PID}" 2>/dev/null || true
    [ -n "${HERMES_PID}" ] && kill "${HERMES_PID}" 2>/dev/null || true
    [ -n "${TAILSCALED_PID}" ] && kill "${TAILSCALED_PID}" 2>/dev/null || true
    wait "${TERMINAL_PID}" 2>/dev/null || true
    wait "${FLASK_PID}" 2>/dev/null || true
    [ -n "${HERMES_PID}" ] && wait "${HERMES_PID}" 2>/dev/null || true
    [ -n "${TAILSCALED_PID}" ] && wait "${TAILSCALED_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for EITHER critical process to exit, then propagate the exit code.
# Hermes is non-critical — only Flask and terminal-server trigger restart.
wait -n "${TERMINAL_PID}" "${FLASK_PID}"
EXIT_CODE=$?
echo "[start-dashboard] a critical process exited with code ${EXIT_CODE}"
exit "${EXIT_CODE}"
