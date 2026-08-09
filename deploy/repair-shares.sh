#!/bin/bash
# Repair stale EventKit share IDs (common after iCloud re-sync) and print
# what HA should select. Run on the Mac that hosts the agent.
set -euo pipefail

ROOT="${APPLE_HASYNC_ROOT:-$HOME/appleHAsync}"
BIN="${BIN:-$HOME/Applications/appleHAsync.app/Contents/MacOS/appleHAsync}"
DATA_DIR="${APPLE_HASYNC_DATA_DIR:-$HOME/Library/Application Support/appleHAsync}"
# Share all iCloud calendars + all reminder lists by default
SHARE_ALL="${SHARE_ALL:-1}"
AGENT_PORT="${AGENT_PORT:-8745}"

if [[ ! -x "$BIN" ]]; then
  echo "ERROR: app binary missing: $BIN" >&2
  exit 1
fi

export APPLE_HASYNC_ROOT="$ROOT"
export APPLE_HASYNC_DATA_DIR="$DATA_DIR"
export PYTHONPATH="$ROOT"

echo "==> Current sources"
"$BIN" share list

echo "==> Rebuild shares (drop stale IDs)"
"$ROOT/.venv/bin/python" - <<'PY'
import os, sys
sys.path.insert(0, os.environ["APPLE_HASYNC_ROOT"])
from mac_agent.config import ConfigStore
from mac_agent.eventkit_backend import EventKitBackend

share_all = os.environ.get("SHARE_ALL", "1") == "1"
store = ConfigStore()
backend = EventKitBackend()
cals = backend.list_calendars()
lists = backend.list_reminder_lists()
cal_ids = {c.id for c in cals}
list_ids = {lst.id for lst in lists}

stale_c = [i for i in store.config.shared_calendars if i not in cal_ids]
stale_l = [i for i in store.config.shared_reminder_lists if i not in list_ids]
print("stale calendars:", stale_c or "(none)")
print("stale lists:", stale_l or "(none)")

if share_all:
    # iCloud calendars only (skip Birthdays / subscribed holidays)
    new_cals = [
        c.id
        for c in cals
        if (c.source_name or "").lower() == "icloud"
    ]
    new_lists = [lst.id for lst in lists]
else:
    new_cals = [i for i in store.config.shared_calendars if i in cal_ids]
    new_lists = [i for i in store.config.shared_reminder_lists if i in list_ids]

titles_c = {c.id: c.title for c in cals}
titles_l = {lst.id: lst.title for lst in lists}
store.set_share(
    calendar_ids=new_cals,
    list_ids=new_lists,
    calendar_titles={i: titles_c[i] for i in new_cals},
    reminder_titles={i: titles_l[i] for i in new_lists},
)
print("shared calendars:")
for i in new_cals:
    print(f"  - {titles_c.get(i)} ({i})")
print("shared reminder lists:")
for i in new_lists:
    print(f"  - {titles_l.get(i)} ({i})")
PY

echo "==> Verify API"
TOK=$("$BIN" token show)
curl -sk -H "Authorization: Bearer $TOK" "https://127.0.0.1:${AGENT_PORT}/v1/calendars" | python3 -m json.tool | head -40
curl -sk -H "Authorization: Bearer $TOK" "https://127.0.0.1:${AGENT_PORT}/v1/reminder-lists" | python3 -m json.tool | head -40
curl -sk "https://127.0.0.1:${AGENT_PORT}/health" | python3 -m json.tool | head -20

echo
echo "==> Next in Home Assistant"
echo "  Settings → Devices & services → Apple HA Sync → Configure / Reload"
echo "  Agent URL example: https://<mac-lan-ip>:${AGENT_PORT}"
echo "  Use: $BIN token show   (do not paste tokens into shared logs)"
