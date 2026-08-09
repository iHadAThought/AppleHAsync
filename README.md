# appleHAsync

Sync **macOS Calendar & Reminders** (EventKit) to Home Assistant. macOS is the master copy; HA edits apply as field-level patches only.

Setup and operations live in BookStack: **[appleHAsync](https://bookstack.ghostnetwork.app/books/applehasync)**

## Layout

| Path | Role |
|------|------|
| `mac_agent/` | Always-on Mac Mini companion (EventKit + HTTPS API + CLI) |
| `mac_agent/macos_app/` | `appleHAsync.app` builder (TCC / Login Items identity) |
| `custom_components/apple_hasync/` | Home Assistant integration (`calendar` + `todo`) |
| `shared/` | Shared domain models / backend protocol |
| `deploy/install-mac-agent.sh` | One-click Mac installer |
| `deploy/uninstall-mac-agent.sh` | One-click Mac uninstaller |

## One-click Mac install

On the Mac Mini (clone first if the Forgejo repo is private):

```bash
git clone https://git.ghostnetwork.app/Brendan/appleHAsync.git ~/appleHAsync
~/appleHAsync/deploy/install-mac-agent.sh
```

Or from an existing checkout:

```bash
./deploy/install-mac-agent.sh
```

This installs:

- Code + venv → `~/appleHAsync`
- App → `~/Applications/appleHAsync.app` (shows as **appleHAsync** in Calendars, Reminders, and Login Items & Extensions)
- LaunchAgent → `app.ghostnetwork.appleHAsync` (runs at login / KeepAlive)

Approve **Calendars** and **Reminders** Full Access for **appleHAsync** when prompted.

```bash
# After install — CLI is the app binary
~/Applications/appleHAsync.app/Contents/MacOS/appleHAsync share list
~/Applications/appleHAsync.app/Contents/MacOS/appleHAsync token show
```

## One-click Mac uninstall

```bash
~/appleHAsync/deploy/uninstall-mac-agent.sh
# Full wipe (app + LaunchAgent + code + Application Support + TCC):
PURGE=1 ~/appleHAsync/deploy/uninstall-mac-agent.sh --force
```

Then remove **Apple HA Sync** under HA → Settings → Devices & services if desired.

## Manual / advanced Mac commands

```bash
export PATH="$HOME/Applications/appleHAsync.app/Contents/MacOS:$PATH"
appleHAsync permissions status
appleHAsync share enable calendar <CALENDAR_ID>
appleHAsync share enable reminder_list <LIST_ID>
appleHAsync ha add --name Home --url https://HOMEASSISTANT:8123 \
  --token LONG_LIVED_TOKEN --webhook-id apple_hasync_ENTRYID --webhook-secret SECRET
appleHAsync ha test Home
```

## Quick start (Home Assistant)

1. Copy `custom_components/apple_hasync` into HA `config/custom_components/`.
2. Restart HA → Settings → Devices & services → Add **Apple HA Sync**.
3. Enter agent HTTPS URL + agent token (TLS verify on by default).
4. Call service `apple_hasync.get_pairing_info` and paste `webhook_id` / `webhook_secret` into the Mac HA registry.

## Security

HTTPS by default, bearer tokens both ways, HMAC-signed webhooks, fail-closed share allowlist, secrets file mode `0600`. See BookStack for TLS/certs and firewall notes.
