#!/bin/bash
# appleHAsync — one-click Mac agent update / patch
#
# Pulls latest code, refreshes the venv, rebuilds appleHAsync.app, restarts
# launchd. Preserves Application Support config, secrets, and shares.
#
# Usage:
#   ~/appleHAsync/deploy/update-mac-agent.sh
#   ./deploy/update-mac-agent.sh
#
# Env / flags:
#   SKIP_PULL=1       don't git pull (use local tree only)
#   SKIP_DEPS=1       skip pip install
#   SKIP_APP=1        skip rebuilding .app
#   SKIP_RESTART=1    don't restart LaunchAgent
#   REPAIR_SHARES=1   also run deploy/repair-shares.sh after update
#   BRANCH=main       git branch to pull (default: current / main)
#   REPO_URL=…        remote if re-cloning is needed
#
set -euo pipefail

BUNDLE_ID="app.iHadAThought.appleHAsync"
LABEL="$BUNDLE_ID"
REPO_URL="${REPO_URL:-https://github.com/iHadAThought/AppleHAsync.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/appleHAsync}"
APP_DIR="${APP_DIR:-$HOME/Applications/appleHAsync.app}"
DATA_DIR="${APPLE_HASYNC_DATA_DIR:-$HOME/Library/Application Support/appleHAsync}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BRANCH="${BRANCH:-}"
SKIP_PULL="${SKIP_PULL:-0}"
SKIP_DEPS="${SKIP_DEPS:-0}"
SKIP_APP="${SKIP_APP:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
REPAIR_SHARES="${REPAIR_SHARES:-0}"

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_python_version_check.sh"

for arg in "$@"; do
  case "$arg" in
    --skip-pull) SKIP_PULL=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    --skip-app) SKIP_APP=1 ;;
    --skip-restart) SKIP_RESTART=1 ;;
    --repair-shares) REPAIR_SHARES=1 ;;
    -h|--help)
      sed -n '2,28p' "$0"
      exit 0
      ;;
  esac
done

# Prefer the checkout that contains this script
SCRIPT_SRC="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_SRC" && -f "$SCRIPT_SRC" ]]; then
  HERE="$(cd "$(dirname "$SCRIPT_SRC")" && pwd)"
  if [[ -d "$HERE/../mac_agent" ]]; then
    INSTALL_DIR="$(cd "$HERE/.." && pwd)"
  fi
fi

# Prefer existing origin remote (Forgejo/GitHub) over REPO_URL default when pulling
if [[ -d "$INSTALL_DIR/.git" ]]; then
  ORIGIN_URL="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
  if [[ -n "$ORIGIN_URL" ]]; then
    REPO_URL="$ORIGIN_URL"
  fi
fi

echo "==> appleHAsync Mac agent updater"
echo "    install: $INSTALL_DIR"
echo "    app:     $APP_DIR"
echo "    data:    $DATA_DIR (preserved)"
applehasync_require_python "$PYTHON_BIN"

if [[ ! -d "$INSTALL_DIR/mac_agent" ]]; then
  echo "ERROR: $INSTALL_DIR/mac_agent missing." >&2
  echo "Run deploy/install-mac-agent.sh first (or clone the repo to $INSTALL_DIR)." >&2
  exit 1
fi

cd "$INSTALL_DIR"
export APPLE_HASYNC_ROOT="$INSTALL_DIR"
export APPLE_HASYNC_DATA_DIR="$DATA_DIR"
export PYTHONPATH="$INSTALL_DIR"

BEFORE_REV="(no git)"
AFTER_REV="(no git)"
if [[ -d .git ]]; then
  BEFORE_REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

if [[ "$SKIP_PULL" != "1" ]]; then
  if [[ -d .git ]]; then
    echo "==> git fetch / pull"
    git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REPO_URL"
    git fetch --prune origin
    if [[ -n "$BRANCH" ]]; then
      git checkout "$BRANCH"
      git pull --ff-only origin "$BRANCH"
    else
      # Stay on current branch; prefer upstream if set
      if git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
        git pull --ff-only
      else
        CUR="$(git rev-parse --abbrev-ref HEAD)"
        git pull --ff-only origin "$CUR" || git pull --ff-only origin main
      fi
    fi
    AFTER_REV="$(git rev-parse --short HEAD)"
    echo "    $BEFORE_REV → $AFTER_REV"
    if [[ "$BEFORE_REV" == "$AFTER_REV" ]]; then
      echo "    already up to date"
    fi
  else
    echo "==> No .git in $INSTALL_DIR — skipping pull (copy/rsync updates manually or re-clone)"
    AFTER_REV="$BEFORE_REV"
  fi
else
  echo "==> Skipping git pull"
  AFTER_REV="$BEFORE_REV"
fi

chmod +x deploy/*.sh mac_agent/macos_app/build_app.sh 2>/dev/null || true

if [[ "$SKIP_DEPS" != "1" ]]; then
  echo "==> Refresh Python venv + dependencies"
  if [[ ! -d .venv ]]; then
    "$PYTHON_BIN" -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -U pip
  python -m pip install -r mac_agent/requirements.txt
else
  # shellcheck disable=SC1091
  [[ -f .venv/bin/activate ]] && source .venv/bin/activate
  echo "==> Skipping dependency refresh"
fi

mkdir -p "$DATA_DIR" "$INSTALL_DIR/logs"

if [[ "$SKIP_APP" != "1" ]]; then
  echo "==> Rebuild appleHAsync.app"
  APPLE_HASYNC_ROOT="$INSTALL_DIR" APP_DIR="$APP_DIR" \
    mac_agent/macos_app/build_app.sh
else
  echo "==> Skipping app rebuild"
fi

BIN="$APP_DIR/Contents/MacOS/appleHAsync"
if [[ ! -x "$BIN" ]]; then
  echo "ERROR: app binary missing at $BIN — re-run without SKIP_APP=1" >&2
  exit 1
fi

# Keep LaunchAgent ProgramArguments pointing at current app (rewrite if missing/stale)
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

if [[ ! -f "$PLIST" ]]; then
  echo "==> LaunchAgent missing — writing ${LABEL}.plist"
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
fi

if [[ "$SKIP_RESTART" != "1" ]]; then
  echo "==> Restart LaunchAgent ${LABEL}"
  for old in app.ghostnetwork.appleHAsync app.ghostnetwork.applehasync; do
    launchctl bootout "${DOMAIN}/${old}" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/${old}.plist"
  done
  launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
  # Refresh plist paths in case install dir / app moved
  /usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 ${BIN}" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :WorkingDirectory ${INSTALL_DIR}" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:APPLE_HASYNC_ROOT ${INSTALL_DIR}" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:PYTHONPATH ${INSTALL_DIR}" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
  launchctl enable "${DOMAIN}/${LABEL}" 2>/dev/null || true
  launchctl kickstart -k "${DOMAIN}/${LABEL}" 2>/dev/null || true
  sleep 2
else
  echo "==> Skipping LaunchAgent restart"
fi

if [[ "$REPAIR_SHARES" == "1" ]]; then
  echo "==> Repair shares"
  bash "$INSTALL_DIR/deploy/repair-shares.sh"
fi

echo "==> Health check"
if curl -sk --connect-timeout 5 "https://127.0.0.1:8745/health" | tee /tmp/applehasync-health.json | grep -q '"ok"'; then
  echo
  python3 -m json.tool </tmp/applehasync-health.json 2>/dev/null || cat /tmp/applehasync-health.json
  echo
else
  echo "WARNING: health endpoint did not respond yet — check:"
  echo "  launchctl print ${DOMAIN}/${LABEL} | head"
  echo "  tail -50 ${INSTALL_DIR}/logs/agent.err.log"
fi

echo
echo "==> Update complete"
echo "    revision: ${AFTER_REV}"
echo "    binary:   ${BIN}"
echo "    Settings: https://127.0.0.1:8745/ui/"
echo "    Optional: REPAIR_SHARES=1 $0   # if HA entities went unavailable after iCloud ID churn"
echo "    HA: reload Apple HA Sync after share changes"
