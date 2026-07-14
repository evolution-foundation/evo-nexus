#!/usr/bin/env bash
# Start the LiteLLM proxy that fronts NVIDIA NIM with an Anthropic-compatible
# /v1/messages endpoint, so `claude --channels` (Telegram bot) can run on NVIDIA.
set -euo pipefail
cd "$(dirname "$0")/.."

# LiteLLM config references os.environ/NVIDIA_API_KEY. Prefer an explicit env
# value, then .env, then the dashboard-managed provider config.
if [ -z "${NVIDIA_API_KEY:-}" ]; then
  if [ -f .env ]; then
    NVIDIA_API_KEY="$(awk -F= '/^NVIDIA_API_KEY=/{sub(/^NVIDIA_API_KEY=/,""); gsub(/^["'\'']|["'\'']$/, ""); print; exit}' .env)"
  fi
  if [ -z "${NVIDIA_API_KEY:-}" ] && [ -f config/providers.json ]; then
    NVIDIA_API_KEY="$(node -e "const fs=require('fs'); const c=JSON.parse(fs.readFileSync('config/providers.json','utf8')); const e=c.providers?.nvidia?.env_vars||{}; process.stdout.write(e.NVIDIA_API_KEY || e.OPENAI_API_KEY || '')")"
  fi
  export NVIDIA_API_KEY
fi

if [ -z "${NVIDIA_API_KEY:-}" ]; then
  echo "NVIDIA_API_KEY is missing. Configure NVIDIA in the dashboard or set it in .env." >&2
  exit 1
fi

PORT="${LITELLM_PORT:-4000}"
exec .venv/bin/litellm \
  --config config/litellm-telegram.yaml \
  --host 127.0.0.1 \
  --port "$PORT"
