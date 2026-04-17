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

echo "[start-dashboard] terminal-server on :${TERMINAL_PORT}, Flask on :${FLASK_PORT}"

# Start terminal-server in the background
node /workspace/dashboard/terminal-server/bin/server.js --port "${TERMINAL_PORT}" &
TERMINAL_PID=$!

# Start Flask in the background
uv run python /workspace/dashboard/backend/app.py &
FLASK_PID=$!

# When this script exits for any reason, kill both children
# shellcheck disable=SC2317  # invoked by trap below
cleanup() {
    echo "[start-dashboard] shutting down (terminal=${TERMINAL_PID}, flask=${FLASK_PID})"
    kill "${TERMINAL_PID}" "${FLASK_PID}" 2>/dev/null || true
    wait "${TERMINAL_PID}" 2>/dev/null || true
    wait "${FLASK_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for EITHER process to exit, then propagate the exit code. Swarm
# restart_policy will bring the whole container back up on any failure.
wait -n
EXIT_CODE=$?
echo "[start-dashboard] a child process exited with code ${EXIT_CODE}"
exit "${EXIT_CODE}"
