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
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *"="* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | xargs)"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=$value"
  done < .env
fi

# Ensure logs dir exists (fresh installs / reboots after manual cleanup)
mkdir -p "$SCRIPT_DIR/logs"

if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
elif [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python3"
else
  PYTHON_BIN="python3"
fi

start_detached() {
  local log_file="$1"
  shift
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" > "$log_file" 2>&1 < /dev/null &
  else
    nohup "$@" > "$log_file" 2>&1 < /dev/null &
  fi
}

kill_repo_pid_file() {
  local pid_file="$1"
  local expected_name="$2"
  [ -f "$pid_file" ] || return 0

  local pid
  pid="$(tr -cd '0-9' < "$pid_file")"
  [ -n "$pid" ] || return 0
  [ -d "/proc/$pid" ] || return 0

  local cwd cmdline
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  if [ "$cwd" = "$SCRIPT_DIR" ] && [[ "$cmdline" == *"$expected_name"* ]]; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -0 "$pid" 2>/dev/null && kill -TERM "$pid" 2>/dev/null || true
  fi
}

kill_tcp_port() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then
    fuser -k -n tcp "$port" 2>/dev/null || true
  elif command -v lsof >/dev/null 2>&1; then
    local pids
    pids=$(lsof -ti "tcp:$port" 2>/dev/null || true)
    [ -n "$pids" ] && kill $pids 2>/dev/null || true
  fi
}

# Kill existing services (including scheduler).
#
# The Python patterns used to be `python.*app.py` and `python.*scheduler.py`,
# which match *any* `app.py` or `scheduler.py` run in Python anywhere on the
# host — not just ours. On a machine with multiple projects (reported in
# issue #18) that would kill unrelated processes. Prefer to target the Flask
# listener by its actual port; fall back to a strict pattern pinned to the
# Python binary we spawn and the absolute script path so at worst we match
# siblings inside this repo, never strangers.
TERMINAL_PORT="${EVONEXUS_TERMINAL_PORT:-32352}"
DASHBOARD_PORT="${EVONEXUS_PORT:-8080}"
kill_tcp_port "$TERMINAL_PORT"
kill_tcp_port "$DASHBOARD_PORT"
kill_repo_pid_file "$SCRIPT_DIR/ADWs/logs/scheduler.pid" "scheduler.py"
kill_repo_pid_file "$SCRIPT_DIR/ADWs/logs/scheduler-shell.pid" "scheduler.py"
sleep 1

# Start terminal-server (must run FROM the project root for agent discovery)
start_detached "$SCRIPT_DIR/logs/terminal-server.log" node dashboard/terminal-server/bin/server.js

# Start scheduler
start_detached "$SCRIPT_DIR/logs/scheduler.log" "$PYTHON_BIN" scheduler.py

# Start Flask dashboard
cd dashboard/backend || exit 1
start_detached "$SCRIPT_DIR/logs/dashboard.log" "$PYTHON_BIN" app.py
