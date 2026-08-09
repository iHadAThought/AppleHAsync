#!/bin/bash
# Package the Home Assistant custom component for manual install.
#
# Creates /tmp/apple_hasync_cc.tgz containing apple_hasync/.
# Copy it into your HA config/custom_components/ (or use HACS — preferred).
#
# Example (HAOS Samba / SSH addon — adjust paths):
#   scp /tmp/apple_hasync_cc.tgz homeassistant:/config/
#   ssh homeassistant 'cd /config/custom_components && tar -xzf /config/apple_hasync_cc.tgz && rm /config/apple_hasync_cc.tgz'
# Then restart Home Assistant and add the Apple HA Sync integration.
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-/tmp/apple_hasync_cc.tgz}"

tar -czf "$OUT" -C "$ROOT/custom_components" apple_hasync
echo "Wrote $OUT"
echo "Install via HACS custom repository, or extract into config/custom_components/ then restart HA."
