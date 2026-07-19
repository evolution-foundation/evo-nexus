#!/usr/bin/env bash
# Swarm entrypoint for the Telegram bot service.
#
# Two runtime modes (TELEGRAM_MODE=channels|provider, default: auto):
#   * channels — `claude --channels` direto na Anthropic. Requer login
#     claude.ai no volume de auth (rode `claude /login` uma vez via docker
#     exec; persiste em /root/.claude/.credentials.json). A disponibilidade
#     de channels é verificada server-side pela Anthropic e NÃO funciona
#     através de proxies OpenAI-compatíveis (NVIDIA NIM etc.).
#   * provider — scripts/telegram_provider_bot.py: bot de polling que
#     responde pelo provider ativo em config/providers.json (NVIDIA NIM,
#     OpenAI, ...). Não depende de channels nem de login Anthropic. É o
#     mesmo runtime do `make telegram` local.
#
# Auto: se existir login claude.ai → channels; senão → provider. O antigo
# caminho LiteLLM-proxy foi removido: channels atrás do proxy nunca passa
# na verificação server-side, então o canal ficava morto ("desconectado").
set -euo pipefail
cd /workspace

CONFIG_DIR=/workspace/config
CHANNEL_DIR="$HOME/.claude/channels/telegram"

reload_env() {
    set -a
    # shellcheck disable=SC1091
    . "$CONFIG_DIR/.env" 2>/dev/null || true
    set +a
}

# --- 1. Decide runtime mode --------------------------------------------------
reload_env
MODE="${TELEGRAM_MODE:-auto}"
if [ "$MODE" = "auto" ]; then
    if grep -q '"claudeAiOauth"' /root/.claude/.credentials.json 2>/dev/null; then
        MODE=channels
    else
        MODE=provider
    fi
fi
echo "[$(date -Is)] telegram mode: $MODE" >&2

# --- 2. Wait for the bot token (both modes need it) --------------------------
# Lands in config/.env via dashboard → Integrations, or comes pre-set in the
# channel dir from a previous boot. No crash-loop while onboarding.
has_channel_token() {
    grep -q '^TELEGRAM_BOT_TOKEN=..*' "$CHANNEL_DIR/.env" 2>/dev/null
}
while [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && ! has_channel_token; do
    echo "[$(date -Is)] waiting for TELEGRAM_BOT_TOKEN — configure via dashboard → Integrations" >&2
    sleep 30
    reload_env
done

# --- 3. Seed channel config on the auth volume -------------------------------
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

# The container runs as root; Claude Code refuses --dangerously-skip-
# permissions as root unless it knows it's inside a sandboxed container.
export IS_SANDBOX=1

# --- 5. Run the bot -----------------------------------------------------------
if [ "$MODE" = "provider" ]; then
    echo "[$(date -Is)] starting telegram_provider_bot.py on the active provider" >&2
    exec /workspace/.venv/bin/python scripts/telegram_provider_bot.py
fi

# channels mode — the claude.ai OAuth login must win: any Anthropic env
# credential (possibly sourced from config/.env) would shadow it and fail
# the server-side channels entitlement check.
unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_MODEL ANTHROPIC_API_KEY 2>/dev/null || true

if [ "$MODE" = "channels" ] && ! grep -q '"claudeAiOauth"' /root/.claude/.credentials.json 2>/dev/null; then
    echo "[$(date -Is)] WARNING: TELEGRAM_MODE=channels but no claude.ai login on the auth volume — run 'claude /login' via docker exec or the channel will stay dead" >&2
fi

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
