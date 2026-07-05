#!/usr/bin/env bash
# Swarm entrypoint for the Telegram bot service.
#
# Mirrors the local `make telegram` setup inside a single container:
#   1. waits for NVIDIA_API_KEY (configured via dashboard → Providers, lands
#      in /workspace/config/.env on the shared config volume)
#   2. seeds ~/.claude/channels/telegram/{.env,access.json} on first boot so
#      the channel has its bot token and the owner's DM allowlist
#   3. starts the LiteLLM proxy (Anthropic /v1/messages → NVIDIA NIM)
#   4. execs `claude --channels` pointed at the local proxy
#
# The real `claude` binary only speaks the Anthropic API; LiteLLM translates
# to NVIDIA NIM (OpenAI-compatible). See config/litellm-telegram.yaml.
set -euo pipefail
cd /workspace

CONFIG_DIR=/workspace/config
CHANNEL_DIR="$HOME/.claude/channels/telegram"
PROXY_PORT="${LITELLM_PORT:-4000}"
PROXY_KEY="sk-evonexus-telegram-local"

reload_env() {
    set -a
    # shellcheck disable=SC1091
    . "$CONFIG_DIR/.env" 2>/dev/null || true
    set +a
}

# --- 1. Wait for NVIDIA_API_KEY (same UI-first pattern as entrypoint.sh) ----
reload_env
while [ -z "${NVIDIA_API_KEY:-}" ]; do
    echo "[$(date -Is)] waiting for NVIDIA_API_KEY — configure via dashboard → Providers" >&2
    sleep 30
    reload_env
done

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    echo "[$(date -Is)] WARNING: TELEGRAM_BOT_TOKEN not set — the channel will not authenticate" >&2
fi

# --- 2. Seed channel config on the auth volume (first boot only) -----------
mkdir -p "$CHANNEL_DIR"
if [ ! -f "$CHANNEL_DIR/.env" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TELEGRAM_BOT_TOKEN" > "$CHANNEL_DIR/.env"
    chmod 600 "$CHANNEL_DIR/.env"
    echo "[$(date -Is)] seeded $CHANNEL_DIR/.env" >&2
fi
if [ ! -f "$CHANNEL_DIR/access.json" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    printf '{"dmPolicy":"allowlist","allowFrom":["%s"],"groups":{},"pending":{}}\n' \
        "$TELEGRAM_CHAT_ID" > "$CHANNEL_DIR/access.json"
    chmod 600 "$CHANNEL_DIR/access.json"
    echo "[$(date -Is)] seeded $CHANNEL_DIR/access.json (allowlist: $TELEGRAM_CHAT_ID)" >&2
fi

# --- 3. Start the LiteLLM proxy ---------------------------------------------
# Prefer a user-customized copy on the config volume; fall back to the image
# default stashed by the Dockerfile (the volume shadows the image's config/).
LITELLM_CONFIG="$CONFIG_DIR/litellm-telegram.yaml"
[ -f "$LITELLM_CONFIG" ] || LITELLM_CONFIG=/workspace/_defaults/config/litellm-telegram.yaml

.venv/bin/litellm --config "$LITELLM_CONFIG" --host 127.0.0.1 --port "$PROXY_PORT" &
PROXY_PID=$!
trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$PROXY_PORT/health/readiness" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$PROXY_PID" 2>/dev/null; then
        echo "[$(date -Is)] LiteLLM proxy died during startup" >&2
        exit 1
    fi
    sleep 2
done
echo "[$(date -Is)] LiteLLM proxy ready on 127.0.0.1:$PROXY_PORT" >&2

# --- 4. Restore /root/.claude.json (same rationale as start-dashboard.sh) --
# The main CLI config is a SIBLING of /root/.claude/ (the volume), so it
# lives in the container layer and is wiped on redeploy. Restore the latest
# backup from the volume, or seed a minimal one to skip first-run prompts.
if [ ! -f /root/.claude.json ]; then
    latest_backup=$(ls -t /root/.claude/backups/.claude.json.backup.* 2>/dev/null | head -n1 || true)
    if [ -n "${latest_backup:-}" ] && [ -f "${latest_backup}" ]; then
        echo "[$(date -Is)] restoring /root/.claude.json from ${latest_backup}" >&2
        cp "${latest_backup}" /root/.claude.json
    else
        echo "[$(date -Is)] seeding minimal /root/.claude.json" >&2
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

# --- 5. Run the channel against the proxy -----------------------------------
export ANTHROPIC_BASE_URL="http://127.0.0.1:$PROXY_PORT"
export ANTHROPIC_AUTH_TOKEN="$PROXY_KEY"
export ANTHROPIC_MODEL="telegram-nvidia"
unset ANTHROPIC_API_KEY 2>/dev/null || true

# The container runs as root; Claude Code refuses --dangerously-skip-
# permissions as root unless it knows it's inside a sandboxed container.
export IS_SANDBOX=1

exec claude \
    --channels "plugin:telegram@claude-plugins-official" \
    --dangerously-skip-permissions
