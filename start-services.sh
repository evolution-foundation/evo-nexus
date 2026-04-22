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

# Kill existing services (including scheduler)
pkill -f 'terminal-server/bin/server.js' 2>/dev/null
pkill -f 'python.*app.py' 2>/dev/null
pkill -f 'python.*scheduler.py' 2>/dev/null
sleep 1

# Start terminal-server (must run FROM the project root for agent discovery)
nohup node dashboard/terminal-server/bin/server.js > "$SCRIPT_DIR/logs/terminal-server.log" 2>&1 &

# Start scheduler
nohup "$SCRIPT_DIR/.venv/bin/python" scheduler.py > "$SCRIPT_DIR/logs/scheduler.log" 2>&1 &

# Start Flask dashboard
cd dashboard/backend || exit 1
nohup "$SCRIPT_DIR/.venv/bin/python" app.py > "$SCRIPT_DIR/logs/dashboard.log" 2>&1 &
