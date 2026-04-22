#!/bin/bash
# EvoNexus — Install as systemd service with dedicated user
# Usage: sudo bash install-service.sh [install_dir]
#
# Creates an 'evonexus' system user, copies/chowns the installation,
# installs uv + claude-code for that user, and sets up a systemd service.
# Safe to re-run — skips steps that are already done.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
DIM='\033[0;90m'
RESET='\033[0m'

# ── Preflight ──

if [ "$(id -u)" -ne 0 ]; then
  echo -e "${RED}✗ Must run as root (sudo bash install-service.sh)${RESET}"
  exit 1
fi

INSTALL_DIR="${1:-$(pwd)}"
if [ ! -f "$INSTALL_DIR/pyproject.toml" ]; then
  echo -e "${RED}✗ Not an EvoNexus installation: $INSTALL_DIR${RESET}"
  echo "  Run from the evo-nexus directory, or pass the path: sudo bash install-service.sh /path/to/evo-nexus"
  exit 1
fi

SERVICE_USER="evonexus"
SERVICE_HOME="/home/$SERVICE_USER"
SERVICE_DIR="$SERVICE_HOME/evo-nexus"
SERVICE_NAME="evo-nexus"

echo -e "\n${GREEN}EvoNexus — Service Installer${RESET}\n"

# ── Step 1: Create user ──

if id "$SERVICE_USER" &>/dev/null; then
  echo -e "  ${DIM}✓ User '$SERVICE_USER' already exists${RESET}"
else
  echo -e "  Creating user '$SERVICE_USER'..."
  useradd -m -s /bin/bash "$SERVICE_USER"
  echo -e "  ${GREEN}✓${RESET} User '$SERVICE_USER' created"
fi

# ── Step 2: Copy installation to user home (if not already there) ──

INSTALL_DIR_REAL=$(realpath "$INSTALL_DIR")
SERVICE_DIR_REAL=$(realpath "$SERVICE_DIR" 2>/dev/null || echo "$SERVICE_DIR")

if [ "$INSTALL_DIR_REAL" = "$SERVICE_DIR_REAL" ]; then
  echo -e "  ${DIM}✓ Already installed at $SERVICE_DIR${RESET}"
else
  echo -e "  Copying installation to $SERVICE_DIR..."
  # Remove old copy if exists
  rm -rf "$SERVICE_DIR"
  cp -a "$INSTALL_DIR_REAL" "$SERVICE_DIR"
  echo -e "  ${GREEN}✓${RESET} Copied to $SERVICE_DIR"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$SERVICE_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$SERVICE_HOME"

# ── Step 2b: Make start-services.sh executable ──
# The script self-discovers its install dir at runtime (SCRIPT_DIR=...),
# so no path substitution is needed here — chmod + chown is enough.

chmod 755 "$SERVICE_DIR/start-services.sh"
chown "$SERVICE_USER:$SERVICE_USER" "$SERVICE_DIR/start-services.sh"
echo -e "  ${GREEN}✓${RESET} start-services.sh ready"

# ── Step 3: Install uv for the user ──

if su - "$SERVICE_USER" -c "command -v uv" &>/dev/null; then
  echo -e "  ${DIM}✓ uv already installed${RESET}"
else
  echo -e "  Installing uv..."
  su - "$SERVICE_USER" -c "curl -LsSf https://astral.sh/uv/install.sh | sh" >/dev/null 2>&1
  echo -e "  ${GREEN}✓${RESET} uv installed"
fi

# ── Step 4: Install Claude Code for the user ──

if su - "$SERVICE_USER" -c "export PATH=\$HOME/.local/bin:\$PATH && command -v claude" &>/dev/null; then
  echo -e "  ${DIM}✓ Claude Code already installed${RESET}"
else
  echo -e "  Installing Claude Code..."
  su - "$SERVICE_USER" -c "npm install -g @anthropic-ai/claude-code --prefix ~/.local" >/dev/null 2>&1
  echo -e "  ${GREEN}✓${RESET} Claude Code installed"
fi

# ── Step 5: Sync Python dependencies ──

echo -e "  Syncing Python dependencies..."
su - "$SERVICE_USER" -c "export PATH=\$HOME/.local/bin:\$PATH && cd $SERVICE_DIR && uv sync -q" 2>/dev/null
echo -e "  ${GREEN}✓${RESET} Dependencies synced"

# ── Step 6: Create systemd service ──

echo -e "  Creating systemd service..."

cat > /etc/systemd/system/${SERVICE_NAME}.service << SERVICEEOF
[Unit]
Description=EvoNexus Dashboard + Scheduler + Terminal Server
After=network.target
Documentation=https://github.com/EvolutionAPI/evo-nexus

[Service]
Type=oneshot
RemainAfterExit=yes
KillMode=none
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$SERVICE_DIR
Environment=PATH=$SERVICE_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=$SERVICE_HOME
ExecStart=/bin/bash $SERVICE_DIR/start-services.sh
ExecStop=/bin/bash -c 'pkill -f "terminal-server/bin/server.js" 2>/dev/null; pkill -f "python.*app.py" 2>/dev/null; pkill -f "python.*scheduler.py" 2>/dev/null'
StandardOutput=append:$SERVICE_DIR/logs/service.log
StandardError=append:$SERVICE_DIR/logs/service.log

[Install]
WantedBy=multi-user.target
SERVICEEOF

# Ensure logs dir exists
su - "$SERVICE_USER" -c "mkdir -p $SERVICE_DIR/logs"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1

echo -e "  ${GREEN}✓${RESET} Systemd service created and enabled"

# ── Step 7: Stop old services running as root ──

echo -e "  Stopping old root services..."
pkill -f 'terminal-server/bin/server.js' 2>/dev/null || true
pkill -f 'dashboard/backend.*app.py' 2>/dev/null || true
sleep 1

# ── Step 8: Start the service ──

echo -e "  Starting $SERVICE_NAME service..."
systemctl start "$SERVICE_NAME"
sleep 3

# Verify
if systemctl is-active --quiet "$SERVICE_NAME"; then
  echo -e "  ${GREEN}✓${RESET} Service is running"
else
  echo -e "  ${YELLOW}!${RESET} Service may not have started — check: journalctl -u $SERVICE_NAME -n 30"
fi

# ── Done ──

echo -e "\n${GREEN}Done!${RESET} EvoNexus is running as '$SERVICE_USER' via systemd.\n"
echo -e "  Useful commands:"
echo -e "    ${DIM}systemctl status $SERVICE_NAME${RESET}     — check status"
echo -e "    ${DIM}systemctl restart $SERVICE_NAME${RESET}    — restart"
echo -e "    ${DIM}journalctl -u $SERVICE_NAME -f${RESET}     — follow logs"
echo -e "    ${DIM}su - $SERVICE_USER${RESET}                 — switch to service user"
echo -e "    ${DIM}su - $SERVICE_USER -c 'cd ~/evo-nexus && make run R=morning'${RESET} — run routine manually"
echo ""
