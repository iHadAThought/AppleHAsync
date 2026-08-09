# appleHAsync

<p align="center">
  <img src="docs/images/icon.png" alt="Apple HA Sync" width="160" />
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/iHadAThought/AppleHAsync)](https://github.com/iHadAThought/AppleHAsync/releases)

Sync **macOS Calendar & Reminders** (EventKit) to Home Assistant. The Mac is the master copy; HA edits apply as field-level patches only.

Full install/update guide: **[docs/INSTALL.md](docs/INSTALL.md)**

## Architecture

| Piece | Role |
|-------|------|
| `mac_agent/` | Always-on Mac companion (EventKit + HTTPS API + settings UI + CLI) |
| `custom_components/apple_hasync/` | Home Assistant integration (`calendar` + `todo`) — installable via HACS |
| `shared/` | Shared domain models |
| `deploy/` | One-click Mac install / update / uninstall helpers |

HACS installs **only** the Home Assistant component. The Mac agent requires cloning this repository on a Mac.

## Requirements

- macOS 13+ with Calendar and Reminders **Full Access** for **appleHAsync**
- Home Assistant **2025.12+** (options flow API)
- Network path between the Mac agent and Home Assistant

## Home Assistant (HACS)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Repository: `https://github.com/iHadAThought/AppleHAsync`
3. Category: **Integration**
4. Download **Apple HA Sync** → restart Home Assistant
5. Settings → Devices & services → **Add integration** → Apple HA Sync
6. Enter the Mac agent URL + bearer token (from the Mac settings UI or `token show`)

Manual install: copy `custom_components/apple_hasync` into your HA `config/custom_components/` directory, then restart.

## Mac agent (one-click)

```bash
git clone https://github.com/iHadAThought/AppleHAsync.git ~/appleHAsync
# For HA on another machine, bind the LAN interface:
LISTEN_HOST=0.0.0.0 ~/appleHAsync/deploy/install-mac-agent.sh
```

This installs:

- Code + venv → `~/appleHAsync`
- App → `~/Applications/appleHAsync.app`
- LaunchAgent → `app.iHadAThought.appleHAsync`

Approve **Calendars** and **Reminders** Full Access when prompted. The installer opens `https://127.0.0.1:8745/ui/`.

### Update / uninstall

```bash
~/appleHAsync/deploy/update-mac-agent.sh
# Optional: ~/appleHAsync/deploy/update-mac-agent.sh --repair-shares

~/appleHAsync/deploy/uninstall-mac-agent.sh
PURGE=1 ~/appleHAsync/deploy/uninstall-mac-agent.sh --force
```

After updating from an older install that used `app.ghostnetwork.appleHAsync`, re-check Calendar/Reminders permissions for **appleHAsync**.

## Pairing checklist

1. Share calendars/lists (and optional field sync) on the Mac **Shares** tab
2. Add Apple HA Sync in HA with agent URL + token
3. Run HA service `apple_hasync.get_pairing_info` and register webhook id/secret on the Mac **Home Assistant** tab

## Security

- HTTPS by default (self-signed cert generated locally); bearer tokens both ways
- HMAC-signed webhooks (required)
- Fail-closed share allowlist (nothing shared until you enable it)
- Installer defaults to `LISTEN_HOST=127.0.0.1` — set `0.0.0.0` only when HA needs LAN access, and prefer an IP allowlist

## License

MIT — see [LICENSE](LICENSE).
