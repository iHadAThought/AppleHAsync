#!/bin/bash
# Run from a machine that can SSH to Proxmox (root@192.168.6.5) to install
# custom_components/apple_hasync into HAOS VM 105.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROXMOX="${PROXMOX:-root@192.168.6.5}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_proxmox_nuc}"
VMID="${VMID:-105}"
TAR=/tmp/apple_hasync_cc.tgz

tar -czf "$TAR" -C "$ROOT/custom_components" apple_hasync
scp -i "$SSH_KEY" -o IdentitiesOnly=yes "$TAR" "$PROXMOX:/tmp/apple_hasync_cc.tgz"

ssh -i "$SSH_KEY" -o IdentitiesOnly=yes "$PROXMOX" bash -s <<REMOTE
set -e
python3 - <<'PY'
import base64, subprocess, pathlib
data = pathlib.Path('/tmp/apple_hasync_cc.tgz').read_bytes()
b64 = base64.b64encode(data).decode()
chunk = 50000
paths = []
for i in range(0, len(b64), chunk):
    part = b64[i:i+chunk]
    name = f'/tmp/ah_{i//chunk}.b64'
    cmd = "printf %s '" + part + "' > " + name
    r = subprocess.run(['qm','guest','exec','${VMID}','--','bash','-c',cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stdout + r.stderr)
    paths.append(name)
    print('chunk', i//chunk, len(part))
script = (
    "cat " + " ".join(paths) +
    " | base64 -d > /tmp/apple_hasync_cc.tgz && rm -f " + " ".join(paths) +
    " && rm -rf /mnt/data/supervisor/homeassistant/custom_components/apple_hasync"
    " && tar -xzf /tmp/apple_hasync_cc.tgz -C /mnt/data/supervisor/homeassistant/custom_components"
    " && ls /mnt/data/supervisor/homeassistant/custom_components/apple_hasync"
)
r = subprocess.run(['qm','guest','exec','${VMID}','--','bash','-c',script], capture_output=True, text=True)
print(r.stdout)
print(r.stderr)
raise SystemExit(r.returncode)
PY
# Restart Home Assistant core so the integration is discovered
qm guest exec ${VMID} -- ha core restart || true
REMOTE

echo "Done. In HA: Settings → Devices & services → Add → Apple HA Sync"
