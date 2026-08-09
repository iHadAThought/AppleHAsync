# appleHAsync

Sync **macOS Calendar & Reminders** (EventKit) to Home Assistant. macOS is the master copy; HA edits apply as field-level patches only.

Setup and operations live in BookStack: **[appleHAsync](https://bookstack.ghostnetwork.app/books/applehasync)**

## Layout

| Path | Role |
|------|------|
| `mac_agent/` | Always-on Mac Mini companion (EventKit + HTTPS API + CLI) |
| `custom_components/apple_hasync/` | Home Assistant integration (`calendar` + `todo`) |
| `shared/` | Shared domain models / backend protocol |

## Quick start (Mac Mini)

```bash
cd /path/to/appleHAsync
python3 -m venv .venv && source .venv/bin/activate
pip install -r mac_agent/requirements.txt
export PYTHONPATH=$PWD
python -m mac_agent.cli permissions request
python -m mac_agent.cli share list
python -m mac_agent.cli share enable calendar <CALENDAR_ID>
python -m mac_agent.cli share enable reminder_list <LIST_ID>
python -m mac_agent.cli token show
python -m mac_agent.cli serve
```

Register Home Assistant instances on the Mac:

```bash
python -m mac_agent.cli ha add --name Home --url https://HOMEASSISTANT:8123 \
  --token LONG_LIVED_TOKEN --webhook-id apple_hasync_ENTRYID --webhook-secret SECRET
python -m mac_agent.cli ha test Home
```

## Quick start (Home Assistant)

1. Copy `custom_components/apple_hasync` into HA `config/custom_components/`.
2. Restart HA → Settings → Devices & services → Add **Apple HA Sync**.
3. Enter agent HTTPS URL + agent token (TLS verify on by default).
4. Call service `apple_hasync.get_pairing_info` and paste `webhook_id` / `webhook_secret` into the Mac HA registry.

## Security

HTTPS by default, bearer tokens both ways, HMAC-signed webhooks, fail-closed share allowlist, secrets file mode `0600`. See BookStack for TLS/certs and firewall notes.
