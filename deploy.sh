#!/usr/bin/env bash
# deploy.sh — Bootstrap nanoleafControlPanel on the Raspberry Pi.
# Run from the repo root: bash deploy.sh
# Safe to re-run (idempotent).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SHARE_DIR="$HOME/.local/share/nanoleafControlPanel"
STATE_DIR="$HOME/.local/state/nanoleafControlPanel"
CONFIG_DIR="$HOME/.config/nanoleafControlPanel"
BIN_DIR="$HOME/.local/bin"
CLI_LINK="$BIN_DIR/nanoleaf-cli"
CLI_TARGET="$REPO_DIR/nanoleaf_cli.py"
CONTROLLER="$REPO_DIR/sunrise_sunset_controller.py"
CRON_ENTRY="*/5 * * * * /usr/bin/python3 $CONTROLLER >> $STATE_DIR/cron.log 2>&1"

echo "=== nanoleafControlPanel deploy ==="
echo "Repo: $REPO_DIR"
echo

# 1. Verify timezone
CURRENT_TZ="$(timedatectl show --property=Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo unknown)"
echo "[1/7] Timezone: $CURRENT_TZ"
if [ "$CURRENT_TZ" != "America/Los_Angeles" ]; then
    echo "      WARNING: expected America/Los_Angeles — run:"
    echo "        sudo timedatectl set-timezone America/Los_Angeles"
fi

# 2. Create runtime directories
echo "[2/7] Creating runtime directories..."
mkdir -p "$SHARE_DIR" "$STATE_DIR" "$CONFIG_DIR" "$BIN_DIR"
echo "      $SHARE_DIR"
echo "      $STATE_DIR"
echo "      $CONFIG_DIR"

# 3. Install Python dependencies
echo "[3/7] Installing Python dependencies..."
pip3 install --quiet -r "$REPO_DIR/requirements.txt"
echo "      done"

# 4. Symlink nanoleaf-cli
echo "[4/7] Setting up nanoleaf-cli symlink..."
if [ -L "$CLI_LINK" ] && [ "$(readlink "$CLI_LINK")" = "$CLI_TARGET" ]; then
    echo "      already linked: $CLI_LINK -> $CLI_TARGET"
else
    ln -sf "$CLI_TARGET" "$CLI_LINK"
    echo "      linked: $CLI_LINK -> $CLI_TARGET"
fi
chmod +x "$CLI_TARGET"

# 5. Add crontab entry (skip if already present)
echo "[5/7] Configuring crontab..."
if crontab -l 2>/dev/null | grep -qF "$CONTROLLER"; then
    echo "      crontab entry already present"
else
    (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
    echo "      added: $CRON_ENTRY"
fi

# 6. Set file permissions
echo "[6/7] Setting permissions..."
if [ -f "$REPO_DIR/.env" ]; then
    chmod 600 "$REPO_DIR/.env"
    echo "      .env — 600"
else
    echo "      WARNING: .env not found — copy it to $REPO_DIR/.env before running the controller"
fi
chmod 700 "$CONFIG_DIR"
if [ -f "$CONFIG_DIR/config.json" ]; then
    chmod 600 "$CONFIG_DIR/config.json"
    echo "      config.json — 600"
fi

# 7. Smoke-check imports
echo "[7/7] Verifying imports..."
python3 - "$REPO_DIR" <<'PYCHECK'
import sys
sys.path.insert(0, sys.argv[1])
import requests, dotenv, filelock
from controller.config import load_config
from controller.state import load_state
from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf_cli import main as cli_main
print("      all imports OK")
PYCHECK

echo
echo "=== Deploy complete ==="
echo "Next steps:"
echo "  1. Verify timezone is America/Los_Angeles (see warning above if shown)"
echo "  2. Ensure .env is present with NANOLEAF_IP_ADDRESS, NANOLEAF_AUTH_TOKEN,"
echo "     OPENWEATHER_LATITUDE, OPENWEATHER_LONGITUDE, OPENWEATHER_AUTH_TOKEN"
echo "  3. Run: nanoleaf-cli status   (should show phase + weather)"
echo "  4. Run: nanoleaf-cli lamp info (should show device info)"
echo "  5. Watch first cron tick: nanoleaf-cli logs -n 20"
