"""Apple HA Sync Home Assistant integration."""

from __future__ import annotations

import hashlib
import hmac
import logging

from aiohttp.web import Request, Response
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

try:
    from homeassistant.core import SupportsResponse
except ImportError:  # pragma: no cover
    from homeassistant.helpers.service import SupportsResponse  # type: ignore

from homeassistant.helpers.typing import ConfigType

from .const import CONF_WEBHOOK_SECRET, DOMAIN
from .coordinator import AppleHASyncCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up from YAML is not supported."""

    async def _pairing_info(call: ServiceCall) -> dict:
        entry_id = call.data.get("entry_id")
        entries = hass.config_entries.async_entries(DOMAIN)
        entry = next((e for e in entries if e.entry_id == entry_id), None) if entry_id else (
            entries[0] if entries else None
        )
        if entry is None:
            return {"error": "no_entry"}
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        return {
            "entry_id": entry.entry_id,
            "agent_url": entry.data.get("agent_url"),
            "webhook_id": data.get("webhook_id") or get_webhook_id(entry),
            "webhook_secret": entry.data.get(CONF_WEBHOOK_SECRET),
            "webhook_url": f"/api/webhook/{data.get('webhook_id') or get_webhook_id(entry)}",
        }

    hass.services.async_register(
        DOMAIN,
        "get_pairing_info",
        _pairing_info,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    coordinator = AppleHASyncCoordinator(hass, entry)
    # Do not fail setup on transient Mac/TCC issues — entities become unavailable.
    await coordinator.async_refresh()

    webhook_id = get_webhook_id(entry)
    secret = entry.data.get(CONF_WEBHOOK_SECRET, "")

    async def handle_webhook(
        hass: HomeAssistant, wh_id: str, request: Request
    ) -> Response:
        body = await request.read()
        signature = request.headers.get("X-Apple-HASync-Signature", "")
        # Require a configured secret — unsigned refresh would let any LAN client
        # trigger polling if the webhook URL were guessed.
        if not secret:
            _LOGGER.error("Webhook secret missing; rejecting refresh")
            return Response(status=401, text="webhook secret not configured")
        expected = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature or ""):
            _LOGGER.warning("Rejected webhook with invalid HMAC")
            return Response(status=401, text="invalid signature")
        coordinator.request_refresh_from_webhook()
        return Response(status=200, text="ok")

    webhook.async_register(
        hass, DOMAIN, "Apple HA Sync refresh", webhook_id, handle_webhook
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "webhook_id": webhook_id,
    }

    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.CALENDAR, Platform.TODO]
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [Platform.CALENDAR, Platform.TODO]
    )
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data and data.get("webhook_id"):
        webhook.async_unregister(hass, data["webhook_id"])
    return unload_ok


def get_webhook_id(entry: ConfigEntry) -> str:
    return f"{DOMAIN}_{entry.entry_id}"
