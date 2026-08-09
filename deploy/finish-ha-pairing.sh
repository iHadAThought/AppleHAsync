#!/bin/bash
# Run ON m2server to finish Home Assistant pairing (LAN HTTP).
# HA integration is already configured; this points the Mac agent at HA over LAN.
set -euo pipefail

DATA_DIR="${APPLE_HASYNC_DATA_DIR:-$HOME/Library/Application Support/appleHAsync}"
ROOT="${APPLE_HASYNC_ROOT:-$HOME/appleHAsync}"
BIN="${BIN:-$HOME/Applications/appleHAsync.app/Contents/MacOS/appleHAsync}"
PY="${ROOT}/.venv/bin/python"
HA_URL="${HA_URL:-http://172.16.1.252:8123}"
HA_KEY="${HA_KEY:-Home}"

if [[ ! -x "$PY" ]]; then
  echo "ERROR: venv python missing at $PY" >&2
  exit 1
fi

echo "==> Enable allow_insecure_http (LAN HA is HTTP-only)"
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

# Hot-reload is watched; give it a moment
sleep 2

AGENT_TOKEN="$("$BIN" token show 2>/dev/null || APPLE_HASYNC_DATA_DIR="$DATA_DIR" PYTHONPATH="$ROOT" "$PY" -m mac_agent.cli token show)"
AUTH="Authorization: Bearer ${AGENT_TOKEN}"

echo "==> Retarget HA registry → ${HA_URL}"
curl -sk -X PUT -H "$AUTH" -H "Content-Type: application/json" \
  -d "$(jq -n --arg u "$HA_URL" '{base_url:$u, verify_tls:false}')" \
  "https://127.0.0.1:8745/v1/admin/home-assistants/${HA_KEY}" | jq .

echo "==> Test HA connection from Mac"
curl -sk -X POST -H "$AUTH" \
  "https://127.0.0.1:8745/v1/admin/home-assistants/${HA_KEY}/test" | jq .

echo "==> Health"
curl -sk "https://127.0.0.1:8745/health" | jq .

echo "==> Done. Webhooks should use ${HA_URL}/api/webhook/…"
