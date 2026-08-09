#!/bin/bash
# Run ON m2server (brendan@m2server) to install appleHAsync Mac agent.
set -euo pipefail

REPO_URL="${REPO_URL:-}"  # optional: git clone URL if not copying locally
INSTALL_DIR="${INSTALL_DIR:-$HOME/appleHAsync}"
DATA_DIR="${APPLE_HASYNC_DATA_DIR:-$HOME/Library/Application Support/appleHAsync}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LISTEN_HOST="${LISTEN_HOST:-0.0.0.0}"
LISTEN_PORT="${LISTEN_PORT:-8745}"

echo "==> Install dir: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$(dirname "$DATA_DIR")" "$HOME/Library/LaunchAgents" "$INSTALL_DIR/logs"

if [[ ! -d "$INSTALL_DIR/mac_agent" ]]; then
  echo "ERROR: $INSTALL_DIR/mac_agent missing."
  echo "Copy the appleHAsync repo to $INSTALL_DIR first (scp/rsync), then re-run."
  exit 1
fi

cd "$INSTALL_DIR"
"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r mac_agent/requirements.txt

export PYTHONPATH="$INSTALL_DIR"
export APPLE_HASYNC_DATA_DIR="$DATA_DIR"

# Seed config with LAN listen
mkdir -p "$DATA_DIR"
python - <<PY
from pathlib import Path
import os, yaml
data = Path(os.environ["APPLE_HASYNC_DATA_DIR"])
cfg = data / "config.yaml"
raw = {}
if cfg.exists():
    raw = yaml.safe_load(cfg.read_text()) or {}
raw["listen_host"] = "${LISTEN_HOST}"
raw["listen_port"] = int("${LISTEN_PORT}")
cfg.write_text(yaml.safe_dump(raw, sort_keys=False))
print("config ready", cfg)
PY

# Request TCC (may show dialogs)
python -m mac_agent.cli permissions request || true
python -m mac_agent.cli permissions status || true
python -m mac_agent.cli token show

# launchd plist
PLIST="$HOME/Library/LaunchAgents/app.ghostnetwork.applehasync.plist"
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>app.ghostnetwork.applehasync</string>
  <key>ProgramArguments</key>
  <array>
    <string>$INSTALL_DIR/.venv/bin/python</string>
    <string>-m</string>
    <string>mac_agent.cli</string>
    <string>serve</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$INSTALL_DIR</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$INSTALL_DIR/logs/agent.out.log</string>
  <key>StandardErrorPath</key>
  <string>$INSTALL_DIR/logs/agent.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key>
    <string>$INSTALL_DIR</string>
    <key>APPLE_HASYNC_DATA_DIR</key>
    <string>$DATA_DIR</string>
  </dict>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/app.ghostnetwork.applehasync" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/app.ghostnetwork.applehasync" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/app.ghostnetwork.applehasync" 2>/dev/null || launchctl load "$PLIST"

sleep 2
TOKEN=$(python -m mac_agent.cli token show)
IP=$(ipconfig getifaddr en0 2>/dev/null || true)
echo
echo "==> Agent token: $TOKEN"
echo "==> Health: curl -k https://${IP:-127.0.0.1}:${LISTEN_PORT}/health"
echo "==> Next: share calendars/lists, register HA, then add integration in HA UI"
curl -sk "https://127.0.0.1:${LISTEN_PORT}/health" || true
echo
