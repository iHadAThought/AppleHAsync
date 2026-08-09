#!/bin/bash
# appleHAsync — one-click Mac agent installer
#
# Usage (from a clone):
#   ./deploy/install-mac-agent.sh
#
# Usage (curl | bash, private Forgejo needs auth or a local clone):
#   curl -fsSL …/deploy/install-mac-agent.sh | bash
#
# Result:
#   - Code in ~/appleHAsync (or INSTALL_DIR)
#   - App: ~/Applications/appleHAsync.app  (shows as "appleHAsync" in Privacy + Login Items)
#   - LaunchAgent: app.ghostnetwork.appleHAsync (Background Items / Login Items)
#
set -euo pipefail

BUNDLE_ID="app.ghostnetwork.appleHAsync"
APP_NAME="appleHAsync"
LABEL="$BUNDLE_ID"
REPO_URL="${REPO_URL:-https://git.ghostnetwork.app/Brendan/appleHAsync.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/appleHAsync}"
APP_DIR="${APP_DIR:-$HOME/Applications/appleHAsync.app}"
DATA_DIR="${APPLE_HASYNC_DATA_DIR:-$HOME/Library/Application Support/appleHAsync}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LISTEN_HOST="${LISTEN_HOST:-0.0.0.0}"
LISTEN_PORT="${LISTEN_PORT:-8745}"
SKIP_PERMS="${SKIP_PERMS:-0}"

echo "==> appleHAsync Mac agent installer"
echo "    install: $INSTALL_DIR"
echo "    app:     $APP_DIR"
echo "    data:    $DATA_DIR"

# Resolve repo: script location, existing INSTALL_DIR, or clone
SCRIPT_SRC="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_SRC" && -f "$SCRIPT_SRC" ]]; then
  HERE="$(cd "$(dirname "$SCRIPT_SRC")" && pwd)"
  if [[ -d "$HERE/../mac_agent" ]]; then
    SRC_ROOT="$(cd "$HERE/.." && pwd)"
  fi
fi

mkdir -p "$HOME/Applications" "$HOME/Library/LaunchAgents" "$(dirname "$DATA_DIR")"

if [[ ! -d "$INSTALL_DIR/mac_agent" ]]; then
  if [[ -n "${SRC_ROOT:-}" ]]; then
    echo "==> Copying from $SRC_ROOT → $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    rsync -a --delete \
      --exclude '.venv' --exclude '__pycache__' --exclude '.git' --exclude '*.pyc' \
      "$SRC_ROOT/" "$INSTALL_DIR/"
  else
    echo "==> Cloning $REPO_URL → $INSTALL_DIR"
    if [[ -d "$INSTALL_DIR/.git" ]]; then
      git -C "$INSTALL_DIR" pull --ff-only || true
    else
      git clone "$REPO_URL" "$INSTALL_DIR"
    fi
  fi
fi

if [[ ! -d "$INSTALL_DIR/mac_agent" ]]; then
  echo "ERROR: $INSTALL_DIR/mac_agent missing after install attempt." >&2
  exit 1
fi

cd "$INSTALL_DIR"
export APPLE_HASYNC_ROOT="$INSTALL_DIR"
export APPLE_HASYNC_DATA_DIR="$DATA_DIR"
export PYTHONPATH="$INSTALL_DIR"

echo "==> Python venv + dependencies"
"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r mac_agent/requirements.txt

echo "==> Seed config (listen ${LISTEN_HOST}:${LISTEN_PORT})"
mkdir -p "$DATA_DIR" "$INSTALL_DIR/logs"
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

echo "==> Build $APP_NAME.app (TCC / Login Items identity)"
chmod +x mac_agent/macos_app/build_app.sh
APPLE_HASYNC_ROOT="$INSTALL_DIR" APP_DIR="$APP_DIR" \
  mac_agent/macos_app/build_app.sh

BIN="$APP_DIR/Contents/MacOS/appleHAsync"
if [[ ! -x "$BIN" ]]; then
  echo "ERROR: app binary missing at $BIN" >&2
  exit 1
fi

# Stop old agent labels (previous name + current)
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
for old in app.ghostnetwork.applehasync "$LABEL"; do
  launchctl bootout "${DOMAIN}/${old}" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/${old}.plist"
done

echo "==> Install LaunchAgent + Login Items / Background Items ($APP_NAME)"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>AssociatedBundleIdentifiers</key>
  <array>
    <string>${BUNDLE_ID}</string>
  </array>
  <key>ProgramArguments</key>
  <array>
    <string>${BIN}</string>
    <string>serve</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${INSTALL_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${INSTALL_DIR}/logs/agent.out.log</string>
  <key>StandardErrorPath</key>
  <string>${INSTALL_DIR}/logs/agent.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>APPLE_HASYNC_ROOT</key>
    <string>${INSTALL_DIR}</string>
    <key>APPLE_HASYNC_DATA_DIR</key>
    <string>${DATA_DIR}</string>
    <key>PYTHONPATH</key>
    <string>${INSTALL_DIR}</string>
  </dict>
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
EOF

# Legacy Login Items entry (pre–Background Items UIs still list this)
osascript <<OSA 2>/dev/null || true
tell application "System Events"
  set appPath to "${APP_DIR}"
  set existing to (every login item whose path is appPath)
  if (count of existing) is 0 then
    make new login item at end of login items with properties {path:appPath, hidden:true, name:"${APP_NAME}"}
  end if
end tell
OSA

launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl kickstart -k "${DOMAIN}/${LABEL}" 2>/dev/null || true

if [[ "$SKIP_PERMS" != "1" ]]; then
  echo "==> Request Calendar + Reminders access (prompt shows as ${APP_NAME})"
  # Run via app binary so TCC records appleHAsync, not Python
  "$BIN" permissions request || true
  "$BIN" permissions status || true
  echo "    If denied earlier: System Settings → Privacy & Security → Calendars / Reminders → enable ${APP_NAME}"
  "$BIN" permissions open-settings both 2>/dev/null || true
fi

echo "==> Waiting for agent health"
UI_URL="https://127.0.0.1:${LISTEN_PORT}/ui/"
READY=0
for _ in $(seq 1 30); do
  if curl -sk --connect-timeout 2 "https://127.0.0.1:${LISTEN_PORT}/health" | grep -q '"ok"'; then
    READY=1
    break
  fi
  sleep 1
done

TOKEN="$("$BIN" token show)"
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"

echo
echo "==> Installed"
echo "    Service / Login Items name: ${APP_NAME}"
echo "    Bundle ID: ${BUNDLE_ID}"
echo "    Settings UI: ${UI_URL}"
echo "    LAN health:  https://${IP:-127.0.0.1}:${LISTEN_PORT}/health"
echo "    Agent token: ${TOKEN}"
if [[ "$READY" == "1" ]]; then
  curl -sk "https://127.0.0.1:${LISTEN_PORT}/health" || true
  echo
  echo "==> Opening setup UI (localhost auto-signs in)"
  open "$UI_URL" 2>/dev/null || true
else
  echo "WARNING: health not ready yet — open ${UI_URL} once the agent is up"
  echo "  launchctl print ${DOMAIN}/${LABEL} | head"
  echo "  tail -50 ${INSTALL_DIR}/logs/agent.err.log"
fi
echo
echo "==> Initial setup in the browser"
echo "    1. Approve Calendar + Reminders if still prompted"
echo "    2. Shares tab — enable calendars / reminder lists"
echo "    3. Home Assistant tab — add URL + long-lived token, Test connection, Save"
echo "    4. Copy agent token into HA → Add integration → Apple HA Sync"
echo "    5. Mark setup complete"
echo
echo "==> Later edits: open ${UI_URL} (or https://${IP:-127.0.0.1}:${LISTEN_PORT}/ui/)"
echo "==> Later updates:  ${INSTALL_DIR}/deploy/update-mac-agent.sh"
