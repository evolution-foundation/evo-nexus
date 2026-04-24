#!/bin/bash
# Self-discovering launcher for the EvoNexus dashboard, scheduler, and
# terminal-server. Resolves SCRIPT_DIR at runtime (instead of hard-coding
# /home/evonexus/evo-nexus) so the same file works regardless of which
# user owns the install or where it lives — required for setups where
# the operator ran the wizard from /root/* (with SUDO_USER=ubuntu) and
# the install ended up under /home/ubuntu/evo-nexus, or any other path.
#
# Invoked by:
#   • the systemd unit (`ExecStart=/bin/bash <install_dir>/start-services.sh`)
#   • Makefile targets (`make dashboard-app`)
#   • operators running it manually after a reboot

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"
cd "$SCRIPT_DIR" || exit 1

# Load environment variables
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

# Ensure logs dir exists (fresh installs / reboots after manual cleanup)
mkdir -p "$SCRIPT_DIR/logs"

# Kill existing services (including scheduler).
#
# The Python patterns used to be `python.*app.py` and `python.*scheduler.py`,
# which match *any* `app.py` or `scheduler.py` run in Python anywhere on the
# host — not just ours. On a machine with multiple projects (reported in
# issue #18) that would kill unrelated processes. Prefer to target the Flask
# listener by its actual port; fall back to a strict pattern pinned to the
# Python binary we spawn and the absolute script path so at worst we match
# siblings inside this repo, never strangers.
pkill -f 'terminal-server/bin/server.js' 2>/dev/null
DASHBOARD_PORT="${EVONEXUS_PORT:-8080}"
if command -v fuser >/dev/null 2>&1; then
  fuser -k -n tcp "$DASHBOARD_PORT" 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
  pids=$(lsof -ti "tcp:$DASHBOARD_PORT" 2>/dev/null || true)
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
fi
# Also kill by pinned interpreter + absolute script path (no generic python wildcard).
VENV_PY="$SCRIPT_DIR/.venv/bin/python"
pkill -f "$VENV_PY $SCRIPT_DIR/dashboard/backend/app.py" 2>/dev/null || true
pkill -f "$VENV_PY $SCRIPT_DIR/scheduler.py" 2>/dev/null || true
sleep 1

# Start terminal-server (must run FROM the project root for agent discovery)
nohup node dashboard/terminal-server/bin/server.js > "$SCRIPT_DIR/logs/terminal-server.log" 2>&1 &

# Start scheduler
nohup "$SCRIPT_DIR/.venv/bin/python" scheduler.py > "$SCRIPT_DIR/logs/scheduler.log" 2>&1 &

# Start Flask dashboard
cd dashboard/backend || exit 1
nohup "$SCRIPT_DIR/.venv/bin/python" app.py > "$SCRIPT_DIR/logs/dashboard.log" 2>&1 &
