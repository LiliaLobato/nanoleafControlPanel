#!/usr/bin/env bash
# deploy.sh — Bootstrap nanoleafControlPanel on the Raspberry Pi.
#
# Usage:
#   bash deploy.sh           # real deploy (Pi / Linux)
#   bash deploy.sh --dry-run # print what would happen, make no changes
#
# Safe to re-run (idempotent).

set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

# Wrap every state-changing call through run() so --dry-run is one switch.
run() {
    if $DRY_RUN; then
        echo "  [dry-run] $*"
    else
        "$@"
    fi
}

# Detect available Python 3 command (test actual execution, not just PATH presence —
# Windows has a python3.exe stub that opens the Store instead of running Python).
_find_python() {
    for cmd in python3 python py; do
        if $cmd -c "import sys; sys.exit(0 if sys.version_info[0]==3 else 1)" 2>/dev/null; then
            echo "$cmd"; return 0
        fi
    done
    return 1
}
PYTHON="$(_find_python)" || { echo "ERROR: Python 3 not found. Install it and re-run."; exit 1; }

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SHARE_DIR="$HOME/.local/share/nanoleafControlPanel"
STATE_DIR="$HOME/.local/state/nanoleafControlPanel"
CONFIG_DIR="$HOME/.config/nanoleafControlPanel"
BIN_DIR="$HOME/.local/bin"
CLI_LINK="$BIN_DIR/nanoleaf-cli"
CLI_TARGET="$REPO_DIR/nanoleaf_cli.py"
CONTROLLER="$REPO_DIR/sunrise_sunset_controller.py"
PYTHON_PATH="$(command -v "$PYTHON")"
CRON_ENTRY="*/2 * * * * $PYTHON_PATH $CONTROLLER >> $STATE_DIR/cron.log 2>&1"

echo "=== nanoleafControlPanel deploy ==="
echo "Repo:    $REPO_DIR"
echo "Mode:    $( $DRY_RUN && echo DRY-RUN || echo LIVE )"
echo

# 1. Verify timezone
echo "[1/7] Timezone check..."
if command -v timedatectl &>/dev/null; then
    CURRENT_TZ="$(timedatectl show --property=Timezone --value 2>/dev/null || true)"
elif [ -f /etc/timezone ]; then
    CURRENT_TZ="$(cat /etc/timezone)"
else
    CURRENT_TZ="unknown (timedatectl not available — are you on Linux?)"
fi
echo "      current: $CURRENT_TZ"
if [ "$CURRENT_TZ" != "America/Los_Angeles" ]; then
    echo "      WARNING: expected America/Los_Angeles — to fix:"
    echo "        sudo timedatectl set-timezone America/Los_Angeles"
fi

# 2. Create runtime directories
echo "[2/7] Creating runtime directories..."
run mkdir -p "$SHARE_DIR" "$STATE_DIR" "$CONFIG_DIR" "$BIN_DIR"
echo "      $SHARE_DIR"
echo "      $STATE_DIR"
echo "      $CONFIG_DIR"
echo "      $BIN_DIR"

# 3. Install Python dependencies
echo "[3/7] Installing Python dependencies..."
if $DRY_RUN; then
    echo "  [dry-run] $PYTHON -m pip install --quiet --break-system-packages -r $REPO_DIR/requirements.txt"
else
    $PYTHON -m pip install --quiet --break-system-packages -r "$REPO_DIR/requirements.txt"
fi
echo "      done"

# 4. Symlink nanoleaf-cli
echo "[4/7] Setting up nanoleaf-cli symlink..."
echo "      $CLI_LINK -> $CLI_TARGET"
if [ -L "$CLI_LINK" ] && [ "$(readlink "$CLI_LINK")" = "$CLI_TARGET" ]; then
    echo "      (already correct — skipping)"
else
    run ln -sf "$CLI_TARGET" "$CLI_LINK"
fi
run chmod +x "$CLI_TARGET"

# 5. Add crontab entry (skip if already present)
echo "[5/7] Configuring crontab..."
echo "      entry: $CRON_ENTRY"
if command -v crontab &>/dev/null; then
    if crontab -l 2>/dev/null | grep -qF "$CRON_ENTRY"; then
        echo "      (already present — skipping)"
    elif crontab -l 2>/dev/null | grep -qF "$CONTROLLER"; then
        # Entry exists with a different schedule — replace it
        if ! $DRY_RUN; then
            (crontab -l 2>/dev/null | grep -vF "$CONTROLLER"; echo "$CRON_ENTRY") | crontab -
            echo "      updated (replaced old schedule)"
        else
            echo "  [dry-run] crontab replace"
        fi
    else
        if ! $DRY_RUN; then
            (crontab -l 2>/dev/null || true; echo "$CRON_ENTRY") | crontab -
            echo "      added"
        else
            echo "  [dry-run] crontab add"
        fi
    fi
else
    echo "      WARNING: crontab not available — add this manually on the Pi:"
    echo "        $CRON_ENTRY"
fi

# 6. Set file permissions
echo "[6/7] Setting permissions..."
if [ -f "$REPO_DIR/.env" ]; then
    run chmod 600 "$REPO_DIR/.env"
    echo "      .env — 600"
else
    echo "      WARNING: .env not found — create $REPO_DIR/.env with:"
    echo "        NANOLEAF_NAME, NANOLEAF_IP_ADDRESS, NANOLEAF_AUTH_TOKEN"
    echo "        OPENWEATHER_LATITUDE, OPENWEATHER_LONGITUDE, OPENWEATHER_AUTH_TOKEN"
fi
if [ -d "$CONFIG_DIR" ]; then
    run chmod 700 "$CONFIG_DIR"
fi
if [ -f "$CONFIG_DIR/config.json" ]; then
    run chmod 600 "$CONFIG_DIR/config.json"
    echo "      config.json — 600"
fi

# 7. Smoke-check imports (always runs, even in dry-run — read-only)
echo "[7/7] Verifying Python imports..."
$PYTHON - "$REPO_DIR" <<'PYCHECK'
import sys
sys.path.insert(0, sys.argv[1])
try:
    import requests, dotenv, filelock
    from controller.config import load_config
    from controller.state import load_state
    from nanoleaf.nanoleafLight import NanoleafLight
    from nanoleaf_cli import main as cli_main
    print("      all imports OK")
except ImportError as e:
    print(f"      FAILED: {e}")
    sys.exit(1)
PYCHECK

echo
echo "=== $( $DRY_RUN && echo Dry-run complete — no changes made || echo Deploy complete ) ==="
if ! $DRY_RUN; then
    echo "Next steps:"
    echo "  1. Fix any WARNINGs printed above"
    echo "  2. Ensure .env has all 6 credentials"
    echo "  3. Add ~/.local/bin to PATH if needed:"
    echo "       echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
    echo "  4. nanoleaf-cli status        (phase + weather)"
    echo "  5. nanoleaf-cli lamp info     (device info)"
    echo "  6. nanoleaf-cli logs -n 20    (watch first cron tick)"
fi
