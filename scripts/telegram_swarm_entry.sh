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

# --- 1. Decide auth mode -----------------------------------------------------
# Channels availability is verified server-side by Anthropic at startup and
# CANNOT be verified through the LiteLLM/NVIDIA proxy — claude prints
# "Channels are not currently available" and ignores --channels. If a
# claude.ai login exists on the auth volume (run `claude /login` once via
# docker exec; persisted in /root/.claude/.credentials.json), run the channel
# directly on Anthropic. TELEGRAM_FORCE_PROXY=1 keeps the NVIDIA proxy
# regardless (channel stays dead until Anthropic supports proxied channels).
reload_env
DIRECT_MODE=0
if [ "${TELEGRAM_FORCE_PROXY:-0}" != "1" ] \
   && grep -q '"claudeAiOauth"' /root/.claude/.credentials.json 2>/dev/null; then
    DIRECT_MODE=1
    echo "[$(date -Is)] claude.ai login found — running channel directly on Anthropic" >&2
fi

# --- 1b. Wait for NVIDIA_API_KEY (proxy mode only) ---------------------------
if [ "$DIRECT_MODE" = "0" ]; then
    while [ -z "${NVIDIA_API_KEY:-}" ]; do
        echo "[$(date -Is)] waiting for NVIDIA_API_KEY — configure via dashboard → Providers" >&2
        sleep 30
        reload_env
    done
fi

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
if [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    if [ ! -f "$CHANNEL_DIR/access.json" ]; then
        printf '{"dmPolicy":"allowlist","allowFrom":["%s"],"groups":{},"pending":{}}\n' \
            "$TELEGRAM_CHAT_ID" > "$CHANNEL_DIR/access.json"
        chmod 600 "$CHANNEL_DIR/access.json"
        echo "[$(date -Is)] seeded $CHANNEL_DIR/access.json (allowlist: $TELEGRAM_CHAT_ID)" >&2
    else
        # The file may predate TELEGRAM_CHAT_ID landing in .env (first DM
        # creates it with the sender stuck in "pending", and the bot keeps
        # answering with the pairing prompt forever). Merge the owner into
        # the allowlist on every boot — idempotent.
        _tmp_acc=$(mktemp)
        if jq --arg id "$TELEGRAM_CHAT_ID" \
              '.dmPolicy //= "allowlist"
               | .allowFrom = ((.allowFrom // []) + [$id] | unique)
               | .pending = ((.pending // {}) | del(.[$id]))' \
              "$CHANNEL_DIR/access.json" > "$_tmp_acc" 2>/dev/null; then
            mv "$_tmp_acc" "$CHANNEL_DIR/access.json"
            chmod 600 "$CHANNEL_DIR/access.json"
            echo "[$(date -Is)] ensured $TELEGRAM_CHAT_ID in access.json allowlist" >&2
        else
            rm -f "$_tmp_acc"
            echo "[$(date -Is)] WARNING: could not patch access.json allowlist" >&2
        fi
    fi
fi

# --- 3. Start the LiteLLM proxy (proxy mode only) ----------------------------
# Prefer a user-customized copy on the config volume; fall back to the image
# default stashed by the Dockerfile (the volume shadows the image's config/).
if [ "$DIRECT_MODE" = "0" ]; then
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
fi

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

# Whatever the restore produced, force the first-run flags — a backup taken
# from a half-initialized run leaves claude stuck at the theme picker or at
# the /workspace trust dialog, and the channel never starts.
_tmp_cfg=$(mktemp)
if jq '.theme //= "dark"
       | .hasCompletedOnboarding = true
       | .hasSeenWelcome = true
       | .bypassPermissionsModeAccepted = true
       | .projects["/workspace"] = ((.projects["/workspace"] // {}) + {
           "hasTrustDialogAccepted": true,
           "hasCompletedProjectOnboarding": true
         })' \
      /root/.claude.json > "$_tmp_cfg" 2>/dev/null; then
    mv "$_tmp_cfg" /root/.claude.json
else
    rm -f "$_tmp_cfg"
    echo "[$(date -Is)] WARNING: could not patch /root/.claude.json flags" >&2
fi

# --- 5. Run the channel --------------------------------------------------------
if [ "$DIRECT_MODE" = "1" ]; then
    # The claude.ai OAuth login must win — any Anthropic env credential
    # (possibly sourced from config/.env) would shadow it and fail the
    # server-side channels entitlement check.
    unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_MODEL ANTHROPIC_API_KEY 2>/dev/null || true
else
    export ANTHROPIC_BASE_URL="http://127.0.0.1:$PROXY_PORT"
    export ANTHROPIC_AUTH_TOKEN="$PROXY_KEY"
    export ANTHROPIC_MODEL="telegram-nvidia"
    unset ANTHROPIC_API_KEY 2>/dev/null || true
fi

# The container runs as root; Claude Code refuses --dangerously-skip-
# permissions as root unless it knows it's inside a sandboxed container.
export IS_SANDBOX=1

# First boot: the plugin cache lives on the /root/.claude volume and starts
# empty — install the telegram channel plugin before starting the channel.
if [ ! -d "$HOME/.claude/plugins/cache/claude-plugins-official/telegram" ]; then
    echo "[$(date -Is)] installing telegram channel plugin" >&2
    claude plugin marketplace add anthropics/claude-plugins-official >/dev/null 2>&1 || true
    claude plugin install telegram@claude-plugins-official --scope user \
        || echo "[$(date -Is)] WARNING: telegram plugin install failed" >&2
fi

exec claude \
    --channels "plugin:telegram@claude-plugins-official" \
    --dangerously-skip-permissions
