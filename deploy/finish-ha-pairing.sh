#!/bin/bash
# Finish Home Assistant pairing from the Mac (optional helper).
#
# Prerequisites:
#   - Apple HA Sync already added in Home Assistant
#   - You have HA base URL + know the HA registry key name used on the Mac
#
# Env:
#   HA_URL   Home Assistant base URL (required unless already correct in agent)
#   HA_KEY   Name of the HA target in the Mac agent registry (default: Home)
#
set -euo pipefail

DATA_DIR="${APPLE_HASYNC_DATA_DIR:-$HOME/Library/Application Support/appleHAsync}"
ROOT="${APPLE_HASYNC_ROOT:-$HOME/appleHAsync}"
BIN="${BIN:-$HOME/Applications/appleHAsync.app/Contents/MacOS/appleHAsync}"
PY="${ROOT}/.venv/bin/python"
HA_URL="${HA_URL:-}"
HA_KEY="${HA_KEY:-Home}"
AGENT_PORT="${AGENT_PORT:-8745}"

if [[ -z "$HA_URL" ]]; then
  echo "ERROR: set HA_URL to your Home Assistant base URL, e.g.:" >&2
  echo "  HA_URL=http://homeassistant.local:8123 $0" >&2
  exit 1
fi

if [[ ! -x "$PY" ]]; then
  echo "ERROR: venv python missing at $PY" >&2
  exit 1
fi

if [[ "$HA_URL" == http://* ]]; then
  echo "==> HA URL is HTTP — enabling allow_insecure_http on the Mac agent (lab/LAN)"
  APPLE_HASYNC_DATA_DIR="$DATA_DIR" "$PY" - <<'PY'
from pathlib import Path
import os, yaml
p = Path(os.environ["APPLE_HASYNC_DATA_DIR"]) / "config.yaml"
raw = yaml.safe_load(p.read_text()) if p.exists() else {}
raw = raw or {}
raw["allow_insecure_http"] = True
p.write_text(yaml.safe_dump(raw, sort_keys=False))
print("wrote", p)
PY
  sleep 2
fi

AGENT_TOKEN="$("$BIN" token show 2>/dev/null || APPLE_HASYNC_DATA_DIR="$DATA_DIR" PYTHONPATH="$ROOT" "$PY" -m mac_agent.cli token show)"
AUTH="Authorization: Bearer ${AGENT_TOKEN}"

echo "==> Retarget HA registry → ${HA_URL}"
curl -sk -X PUT -H "$AUTH" -H "Content-Type: application/json" \
  -d "$(jq -n --arg u "$HA_URL" '{base_url:$u, verify_tls:false}')" \
  "https://127.0.0.1:${AGENT_PORT}/v1/admin/home-assistants/${HA_KEY}" | jq .

echo "==> Test HA connection from Mac"
curl -sk -X POST -H "$AUTH" \
  "https://127.0.0.1:${AGENT_PORT}/v1/admin/home-assistants/${HA_KEY}/test" | jq .

echo "==> Health"
curl -sk "https://127.0.0.1:${AGENT_PORT}/health" | jq .

echo "==> Done. Register webhook id/secret from HA service apple_hasync.get_pairing_info if needed."
