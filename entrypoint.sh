#!/usr/bin/env bash
# ============================================================================
# entrypoint.sh — Bootstrap + source .env + wait-for-config wrapper
#
# Respects the UI-first config model EvoNexus ships upstream:
#   * /workspace/config is a writable volume. The dashboard's Providers,
#     Integrations, Settings and env-editor pages write there.
#   * This entrypoint sources /workspace/config/.env on startup so the
#     Claude CLI, Python code, and every library see the UI-configured
#     values as regular environment variables.
#   * Services that need ANTHROPIC_API_KEY (telegram, scheduler) wait in a
#     30s-poll loop until the user sets it via the dashboard, instead of
#     crash-looping and spamming the Swarm with restart attempts.
#
# The Docker Secrets / _FILE machinery is still honored for anyone who
# wants it, but it is optional. The default stack file ships zero
# secrets — every credential is configured through the dashboard after
# the first deploy.
# ============================================================================
set -euo pipefail

CONFIG_DIR=/workspace/config
DEFAULTS_DIR=/workspace/_defaults

# --- 1. Ensure writable dirs exist (volumes may mount empty) ---------------
mkdir -p "$CONFIG_DIR" \
         /workspace/workspace \
         /workspace/memory \
         /workspace/ADWs/logs \
         /workspace/.claude/agent-memory \
         /workspace/dashboard/data

# --- 1b. Serialize first-boot bootstrap across services --------------------
# The dashboard, telegram and scheduler services share /workspace/config on a
# named volume. On first boot they race on "[ ! -f .env ] && cp .env.example"
# (one succeeds, others crash with "File exists") and also on "grep -q KEY ||
# echo >> .env" (two processes both see "not found" and append two different
# keys, silently corrupting Flask sessions or Knowledge Base encryption).
#
# Serialize the whole bootstrap section with a flock on a lockfile inside the
# shared volume — that way every process that mounts this volume takes turns
# regardless of which container runs first.
LOCK_FILE="$CONFIG_DIR/.bootstrap.lock"
exec 200>"$LOCK_FILE"
flock 200

# --- 2. Bootstrap /workspace/config from image defaults (first boot only) --
if [ -d "$DEFAULTS_DIR" ]; then
    if [ ! -f "$CONFIG_DIR/.env" ]; then
        if [ -f "$DEFAULTS_DIR/.env.example" ]; then
            cp -n "$DEFAULTS_DIR/.env.example" "$CONFIG_DIR/.env"
        else
            touch "$CONFIG_DIR/.env"
        fi
    fi
    for f in providers.example.json heartbeats.example.yaml; do
        if [ -f "$DEFAULTS_DIR/config/$f" ] && [ ! -f "$CONFIG_DIR/$f" ]; then
            cp -n "$DEFAULTS_DIR/config/$f" "$CONFIG_DIR/$f"
        fi
    done
fi

# --- 2b. Seed/refresh /workspace/.claude from image defaults ---------------
# /workspace/.claude may be a named volume (persists custom-* skills/agents/
# commands and plugin-* artifacts across restarts). Built-ins are re-copied
# from the image on every boot so upgrades propagate; anything not shipped
# in the image (custom-*, plugin-*, settings.local.json) is left untouched.
# agent-memory is not in the stash — it has its own volume.
if [ -d "$DEFAULTS_DIR/claude" ]; then
    mkdir -p /workspace/.claude
    cp -a "$DEFAULTS_DIR/claude/." /workspace/.claude/ 2>/dev/null || true
fi

# --- 3. Ensure EVONEXUS_SECRET_KEY exists (Flask session signing) ----------
# Without this, Flask invalidates every session on restart. We generate it
# once on first boot and persist it in the same .env the UI edits.
if ! grep -q '^EVONEXUS_SECRET_KEY=' "$CONFIG_DIR/.env" 2>/dev/null; then
    echo "EVONEXUS_SECRET_KEY=$(openssl rand -hex 32)" >> "$CONFIG_DIR/.env"
fi

# --- 3b. Ensure KNOWLEDGE_MASTER_KEY exists (Knowledge Base DSN encryption) ---
# Without this, /api/knowledge/* endpoints raise on startup and the Knowledge
# section of the dashboard fails to load. Fernet requires a urlsafe-base64
# encoded 32-byte key, so `openssl rand` cannot be used directly — we go
# through Python's cryptography lib (already installed in the venv via
# pyproject.toml). Generated once on first boot; the UI never exposes it.
if ! grep -q '^KNOWLEDGE_MASTER_KEY=' "$CONFIG_DIR/.env" 2>/dev/null; then
    # Prefer the venv python (has `cryptography` pinned); fall back to system.
    _PYBIN="/workspace/.venv/bin/python3"
    [ -x "$_PYBIN" ] || _PYBIN="$(command -v python3 || true)"
    if [ -n "$_PYBIN" ]; then
        _KEY=$("$_PYBIN" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || true)
        if [ -n "$_KEY" ]; then
            {
                printf '\n# Knowledge encryption key — DO NOT delete, DO NOT commit.\n'
                printf '# Losing this key = losing access to ALL configured connections.\n'
                printf 'KNOWLEDGE_MASTER_KEY=%s\n' "$_KEY"
            } >> "$CONFIG_DIR/.env"
            echo "[$(date -Is)] Generated KNOWLEDGE_MASTER_KEY (first boot)" >&2
        else
            echo "[$(date -Is)] WARNING: could not generate KNOWLEDGE_MASTER_KEY (cryptography missing?)" >&2
        fi
    else
        echo "[$(date -Is)] WARNING: no python3 found — KNOWLEDGE_MASTER_KEY not generated" >&2
    fi
    unset _PYBIN _KEY
fi

# --- 3c. Release the bootstrap lock ----------------------------------------
flock -u 200
exec 200>&-

# --- 4. Symlinks so the app finds files at the paths it expects ------------
ln -sfn "$CONFIG_DIR/.env" /workspace/.env
if [ ! -e /workspace/CLAUDE.md ] && [ ! -L /workspace/CLAUDE.md ]; then
    ln -sfn "$CONFIG_DIR/CLAUDE.md" /workspace/CLAUDE.md
fi

# --- 5. Source .env (UI-configured values become env vars) -----------------
# Using `set -a` so every variable assigned here is auto-exported.
set -a
# shellcheck disable=SC1091
. "$CONFIG_DIR/.env" 2>/dev/null || true
set +a

# --- 6. Optional: _FILE env vars (explicit Docker Secrets pattern) ---------
for file_var in $(compgen -A variable | grep -E '_FILE$' || true); do
    var="${file_var%_FILE}"
    path_val="${!file_var:-}"
    if [ -n "$path_val" ] && [ -f "$path_val" ]; then
        export "${var}=$(cat "$path_val")"
    fi
done

# --- 7. Optional: auto-discover /run/secrets/* -----------------------------
if [ -d /run/secrets ]; then
    for secret_file in /run/secrets/*; do
        [ -f "$secret_file" ] || continue
        var_name=$(basename "$secret_file" | tr '[:lower:]-' '[:upper:]_')
        if [ -z "${!var_name:-}" ]; then
            export "${var_name}=$(cat "$secret_file")"
        fi
    done
fi

# --- 8. Wait for required config (telegram, scheduler) ---------------------
# The stack sets REQUIRE_ANTHROPIC_KEY=1 on services that can't run without
# a key. Instead of crash-looping, we wait and re-read .env every 30s. When
# the user saves the key in dashboard → Providers, it lands in .env and
# we pick it up on the next iteration — no manual restart needed.
if [ "${REQUIRE_ANTHROPIC_KEY:-0}" = "1" ]; then
    while [ -z "${ANTHROPIC_API_KEY:-}" ]; do
        echo "[$(date -Is)] waiting for ANTHROPIC_API_KEY — configure via dashboard → Providers" >&2
        sleep 30
        set -a
        # shellcheck disable=SC1091
        . "$CONFIG_DIR/.env" 2>/dev/null || true
        set +a
    done
    echo "[$(date -Is)] ANTHROPIC_API_KEY detected — starting $*" >&2
fi

# --- 8b. Claude CLI headless bootstrap --------------------------------------
# Heartbeats/routines invoke `claude --print --dangerously-skip-permissions`
# as root. Two first-run gates block that in a fresh container:
#   1) /root/.claude.json lives in the container layer (wiped on redeploy);
#      without the trust flags for /workspace the CLI ignores the project's
#      .claude/settings.json permissions and fails with "this workspace has
#      not been trusted".
#   2) Claude Code refuses --dangerously-skip-permissions as root unless
#      IS_SANDBOX=1 signals a containerized environment.
# Same fix as start-dashboard.sh / telegram_swarm_entry.sh.
export IS_SANDBOX="${IS_SANDBOX:-1}"
# Restore the latest backup BEFORE seeding/patching. The main config is a
# sibling of /root/.claude/ (the volume) and is wiped on redeploy; blind
# seeding here would shadow the backup restore downstream (telegram wrapper /
# start-dashboard check "[ ! -f ]") and lose account state — e.g. the
# channels feature gate — that only the backup carries.
if [ ! -f /root/.claude.json ]; then
    _latest_backup=$(ls -t /root/.claude/backups/.claude.json.backup.* 2>/dev/null | head -n1 || true)
    if [ -n "${_latest_backup:-}" ] && [ -f "$_latest_backup" ]; then
        echo "[$(date -Is)] restoring /root/.claude.json from $_latest_backup" >&2
        cp "$_latest_backup" /root/.claude.json
    fi
fi
unset _latest_backup
_PYBIN="/workspace/.venv/bin/python3"
[ -x "$_PYBIN" ] || _PYBIN="$(command -v python3 || true)"
if [ -n "$_PYBIN" ]; then
    "$_PYBIN" - <<'EOF' || echo "[$(date -Is)] WARNING: could not patch /root/.claude.json flags" >&2
import json, os

path = "/root/.claude.json"
cfg = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

cfg.setdefault("theme", "dark")
cfg["hasCompletedOnboarding"] = True
cfg["hasSeenWelcome"] = True
cfg["bypassPermissionsModeAccepted"] = True
project = cfg.setdefault("projects", {}).setdefault("/workspace", {})
project["hasTrustDialogAccepted"] = True
project["hasCompletedProjectOnboarding"] = True

with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
EOF
else
    echo "[$(date -Is)] WARNING: no python3 — /root/.claude.json not patched" >&2
fi
unset _PYBIN

# --- 9. Hand off to the actual process -------------------------------------
exec "$@"
