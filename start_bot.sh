#!/bin/bash
# Telegram Broadcast Bot - Start Script

set -e

# Configuration
BOT_DIR="/opt/telegram-broadcast-bot"
VENV_DIR="$BOT_DIR/venv"
PYTHON="$VENV_DIR/bin/python"
BOT_SCRIPT="$BOT_DIR/bot.py"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "  Telegram Broadcast Bot - Launcher"
echo "========================================="
echo ""

# Check if running from correct directory
if [ ! -f "$BOT_SCRIPT" ]; then
    echo -e "${RED}Error: bot.py not found!${NC}"
    echo "Please run this script from the bot directory or update BOT_DIR variable"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
    python3 -m venv "$VENV_DIR"
    echo -e "${GREEN}Virtual environment created.${NC}"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Check if requirements are installed
if [ ! -f "$VENV_DIR/.requirements_installed" ]; then
    echo -e "${YELLOW}Installing requirements...${NC}"
    pip install --upgrade pip
    pip install -r "$BOT_DIR/requirements.txt"
    touch "$VENV_DIR/.requirements_installed"
    echo -e "${GREEN}Requirements installed.${NC}"
fi

# Check if .env file exists
if [ ! -f "$BOT_DIR/.env" ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    echo "Please create .env file with required configuration"
    exit 1
fi

# Create necessary directories
echo "Creating necessary directories..."
mkdir -p "$BOT_DIR/telethon_sessions"
mkdir -p "$BOT_DIR/logs"
mkdir -p "$BOT_DIR/broadcast_control"
mkdir -p "$BOT_DIR/account_histories"

# Set permissions for session directory
chmod 700 "$BOT_DIR/telethon_sessions"

echo -e "${GREEN}Starting bot...${NC}"
echo ""

# Start the bot
cd "$BOT_DIR"
exec "$PYTHON" "$BOT_SCRIPT"
