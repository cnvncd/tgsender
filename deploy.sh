#!/bin/bash
# Deployment script for Telegram Broadcast Bot

set -e

# Configuration
INSTALL_DIR="/opt/telegram-broadcast-bot"
SERVICE_NAME="telegram-broadcast-bot"
BOT_USER="telegram-bot"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}  Telegram Broadcast Bot - Deployment${NC}"
echo -e "${BLUE}=========================================${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

# Create user if doesn't exist
if ! id "$BOT_USER" &>/dev/null; then
    echo -e "${YELLOW}Creating user $BOT_USER...${NC}"
    useradd -r -s /bin/bash -d "$INSTALL_DIR" "$BOT_USER"
    echo -e "${GREEN}User created.${NC}"
else
    echo -e "${GREEN}User $BOT_USER already exists.${NC}"
fi

# Create installation directory
echo -e "${YELLOW}Creating installation directory...${NC}"
mkdir -p "$INSTALL_DIR"

# Copy files
echo -e "${YELLOW}Copying files...${NC}"
cp -r ./* "$INSTALL_DIR/"
chown -R "$BOT_USER:$BOT_USER" "$INSTALL_DIR"

# Create virtual environment
echo -e "${YELLOW}Creating virtual environment...${NC}"
cd "$INSTALL_DIR"
sudo -u "$BOT_USER" python3 -m venv venv

# Install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
sudo -u "$BOT_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$BOT_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# Create necessary directories
echo -e "${YELLOW}Creating directories...${NC}"
sudo -u "$BOT_USER" mkdir -p "$INSTALL_DIR/telethon_sessions"
sudo -u "$BOT_USER" mkdir -p "$INSTALL_DIR/logs"
sudo -u "$BOT_USER" mkdir -p "$INSTALL_DIR/broadcast_control"
sudo -u "$BOT_USER" mkdir -p "$INSTALL_DIR/account_histories"

# Set permissions
chmod 700 "$INSTALL_DIR/telethon_sessions"
chmod +x "$INSTALL_DIR/start_bot.sh"

# Check if .env exists
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo -e "${YELLOW}Creating .env file...${NC}"
    sudo -u "$BOT_USER" touch "$INSTALL_DIR/.env"
    echo -e "${RED}IMPORTANT: Please edit $INSTALL_DIR/.env and add your configuration!${NC}"
fi

# Install systemd service
echo -e "${YELLOW}Installing systemd service...${NC}"
cp "$INSTALL_DIR/$SERVICE_NAME.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}  Installation completed!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo -e "1. Edit configuration: ${BLUE}nano $INSTALL_DIR/.env${NC}"
echo -e "2. Enable service: ${BLUE}systemctl enable $SERVICE_NAME${NC}"
echo -e "3. Start service: ${BLUE}systemctl start $SERVICE_NAME${NC}"
echo -e "4. Check status: ${BLUE}systemctl status $SERVICE_NAME${NC}"
echo -e "5. View logs: ${BLUE}journalctl -u $SERVICE_NAME -f${NC}"
echo ""
