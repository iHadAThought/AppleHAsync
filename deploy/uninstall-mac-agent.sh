#!/bin/bash
# appleHAsync — one-click Mac agent uninstaller
#
# Usage:
#   ./deploy/uninstall-mac-agent.sh
#   ~/appleHAsync/deploy/uninstall-mac-agent.sh
#
# Env / flags:
#   PURGE=1          also delete install dir + Application Support data + TCC entries
#   KEEP_APP=1       leave ~/Applications/appleHAsync.app
#   KEEP_CODE=1      leave INSTALL_DIR (default: remove only when PURGE=1)
#   KEEP_DATA=1      leave Application Support (default unless PURGE=1)
#   FORCE=1          skip confirmation prompt
#
set -euo pipefail

BUNDLE_ID="app.iHadAThought.appleHAsync"
APP_NAME="appleHAsync"
LABEL="$BUNDLE_ID"
OLD_LABELS=("app.ghostnetwork.appleHAsync" "app.ghostnetwork.applehasync")
INSTALL_DIR="${INSTALL_DIR:-$HOME/appleHAsync}"
APP_DIR="${APP_DIR:-$HOME/Applications/appleHAsync.app}"
DATA_DIR="${APPLE_HASYNC_DATA_DIR:-$HOME/Library/Application Support/appleHAsync}"
PURGE="${PURGE:-0}"
KEEP_APP="${KEEP_APP:-0}"
KEEP_CODE="${KEEP_CODE:-1}"
KEEP_DATA="${KEEP_DATA:-1}"
FORCE="${FORCE:-0}"

# PURGE implies removing code + data
if [[ "$PURGE" == "1" ]]; then
  KEEP_CODE=0
  KEEP_DATA=0
fi

# Allow --purge / --force as argv
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1; KEEP_CODE=0; KEEP_DATA=0 ;;
    --force|-y) FORCE=1 ;;
    --keep-data) KEEP_DATA=1 ;;
    --remove-code) KEEP_CODE=0 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
  esac
done

echo "==> appleHAsync Mac agent uninstaller"
echo "    app:     $APP_DIR"
echo "    launchd: $LABEL"
echo "    install: $INSTALL_DIR (remove code: $([[ "$KEEP_CODE" == "0" ]] && echo yes || echo no))"
echo "    data:    $DATA_DIR (remove data: $([[ "$KEEP_DATA" == "0" ]] && echo yes || echo no))"

if [[ "$FORCE" != "1" ]]; then
  read -r -p "Continue uninstall? [y/N] " ans
  case "$ans" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

echo "==> Stop LaunchAgent"
for lbl in "$LABEL" "${OLD_LABELS[@]}"; do
  launchctl bootout "${DOMAIN}/${lbl}" 2>/dev/null || true
  launchctl disable "${DOMAIN}/${lbl}" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/${lbl}.plist"
  echo "    removed ${lbl}"
done

echo "==> Remove Login Items entry"
osascript <<OSA 2>/dev/null || true
tell application "System Events"
  set appPath to "${APP_DIR}"
  delete (every login item whose path is appPath)
  -- also match by name in case path differs
  try
    delete (every login item whose name is "${APP_NAME}")
  end try
end tell
OSA

if [[ "$KEEP_APP" != "1" ]]; then
  echo "==> Remove ${APP_NAME}.app"
  if [[ -e "$APP_DIR" ]]; then
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
      -u "$APP_DIR" 2>/dev/null || true
    rm -rf "$APP_DIR"
    echo "    deleted $APP_DIR"
  else
    echo "    (not present)"
  fi
fi

# Kill any stray agent process still bound to our bundle / port
echo "==> Stop stray processes"
pkill -f "mac_agent.cli serve" 2>/dev/null || true
pkill -f "${APP_DIR}/Contents/MacOS/appleHAsync" 2>/dev/null || true
# Avoid killing unrelated listeners; only our default port if held by appleHAsync
if command -v lsof >/dev/null 2>&1; then
  for pid in $(lsof -nP -iTCP:8745 -sTCP:LISTEN -t 2>/dev/null || true); do
    cmd=$(ps -p "$pid" -o comm= 2>/dev/null || true)
    if [[ "$cmd" == *appleHAsync* ]] || [[ "$cmd" == *Python* ]]; then
      # Only kill if command line mentions appleHAsync / mac_agent
      if ps -p "$pid" -o args= 2>/dev/null | grep -qE 'appleHAsync|mac_agent'; then
        kill "$pid" 2>/dev/null || true
      fi
    fi
  done
fi

if [[ "$KEEP_DATA" == "0" ]]; then
  echo "==> Remove Application Support data"
  if [[ -d "$DATA_DIR" ]]; then
    rm -rf "$DATA_DIR"
    echo "    deleted $DATA_DIR"
  else
    echo "    (not present)"
  fi
  echo "==> Reset TCC for ${APP_NAME} (Calendar / Reminders)"
  tccutil reset Calendar "$BUNDLE_ID" 2>/dev/null || true
  tccutil reset Reminders "$BUNDLE_ID" 2>/dev/null || true
else
  echo "==> Keeping data at $DATA_DIR (PURGE=1 or omit KEEP_DATA to delete)"
fi

if [[ "$KEEP_CODE" == "0" ]]; then
  echo "==> Remove install directory"
  if [[ -d "$INSTALL_DIR" ]]; then
    # Safety: only delete if it looks like this project
    if [[ -d "$INSTALL_DIR/mac_agent" ]] || [[ -f "$INSTALL_DIR/deploy/install-mac-agent.sh" ]]; then
      rm -rf "$INSTALL_DIR"
      echo "    deleted $INSTALL_DIR"
    else
      echo "    SKIP: $INSTALL_DIR does not look like appleHAsync (no mac_agent/)"
    fi
  else
    echo "    (not present)"
  fi
else
  echo "==> Keeping code at $INSTALL_DIR (use PURGE=1 or --purge to remove)"
fi

echo
echo "==> Uninstall complete"
echo "    Home Assistant: remove the Apple HA Sync integration in HA if desired"
echo "    (Settings → Devices & services → Apple HA Sync → Delete)"
if [[ "$KEEP_DATA" == "1" ]]; then
  echo "    Data preserved: $DATA_DIR"
fi
echo "    Full wipe: PURGE=1 $0 --force"
