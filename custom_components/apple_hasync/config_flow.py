"""Config flow for Apple HA Sync."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .client import AppleHASyncAuthError, AppleHASyncClient, AppleHASyncPermissionError
from .const import (
    BACKEND_CALDAV,
    BACKEND_LOCAL_AGENT,
    CONF_AGENT_TOKEN,
    CONF_AGENT_URL,
    CONF_ALLOW_INSECURE_HTTP,
    CONF_BACKEND,
    CONF_CA_PATH,
    CONF_CERT_PIN,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_CALENDARS,
    CONF_SELECTED_LISTS,
    CONF_VERIFY_TLS,
    CONF_WEBHOOK_SECRET,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_TLS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class AppleHASyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for apple_hasync."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._calendars: list[dict[str, Any]] = []
        self._lists: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # Safety: reject legacy CalDAV selections if present in stored drafts.
            backend = user_input.get(CONF_BACKEND, BACKEND_LOCAL_AGENT)
            if backend == BACKEND_CALDAV:
                return self.async_abort(reason="caldav_not_available")

            url = user_input[CONF_AGENT_URL].rstrip("/")
            allow_insecure = user_input.get(CONF_ALLOW_INSECURE_HTTP, False)
            if url.startswith("http://") and not allow_insecure:
                errors["base"] = "https_required"
            else:
                client = AppleHASyncClient(
                    self.hass,
                    url,
                    user_input[CONF_AGENT_TOKEN],
                    verify_tls=user_input.get(CONF_VERIFY_TLS, DEFAULT_VERIFY_TLS),
                    ca_path=user_input.get(CONF_CA_PATH) or None,
                    cert_pin=user_input.get(CONF_CERT_PIN) or None,
                    allow_insecure_http=allow_insecure,
                )
                try:
                    await client.health()
                    self._calendars = await client.list_calendars()
                    self._lists = await client.list_reminder_lists()
                except AppleHASyncAuthError:
                    errors["base"] = "invalid_auth"
                except AppleHASyncPermissionError:
                    errors["base"] = "permission_required"
                except Exception:
                    _LOGGER.exception("Connection failed")
                    errors["base"] = "cannot_connect"
                else:
                    self._data = {
                        CONF_BACKEND: BACKEND_LOCAL_AGENT,
                        CONF_AGENT_URL: url,
                        CONF_AGENT_TOKEN: user_input[CONF_AGENT_TOKEN],
                        CONF_VERIFY_TLS: user_input.get(
                            CONF_VERIFY_TLS, DEFAULT_VERIFY_TLS
                        ),
                        CONF_CA_PATH: user_input.get(CONF_CA_PATH) or None,
                        CONF_CERT_PIN: user_input.get(CONF_CERT_PIN) or None,
                        CONF_ALLOW_INSECURE_HTTP: allow_insecure,
                        CONF_WEBHOOK_SECRET: secrets.token_urlsafe(24),
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    }
                    await self.async_set_unique_id(url.lower())
                    self._abort_if_unique_id_configured()
                    return await self.async_step_sources()

        schema = vol.Schema(
            {
                vol.Required(CONF_AGENT_URL): str,
                vol.Required(CONF_AGENT_TOKEN): str,
                vol.Optional(CONF_VERIFY_TLS, default=True): bool,
                vol.Optional(CONF_CA_PATH): str,
                vol.Optional(CONF_CERT_PIN): str,
                vol.Optional(CONF_ALLOW_INSECURE_HTTP, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._data[CONF_SELECTED_CALENDARS] = user_input.get(
                CONF_SELECTED_CALENDARS, [c["id"] for c in self._calendars]
            )
            self._data[CONF_SELECTED_LISTS] = user_input.get(
                CONF_SELECTED_LISTS, [lst["id"] for lst in self._lists]
            )
            title = f"Apple HA Sync ({self._data[CONF_AGENT_URL]})"
            return self.async_create_entry(title=title, data=self._data)

        cal_options = [
            {"value": c["id"], "label": c.get("title") or c["id"]} for c in self._calendars
        ]
        list_options = [
            {"value": lst["id"], "label": lst.get("title") or lst["id"]}
            for lst in self._lists
        ]
        schema_dict: dict[Any, Any] = {}
        if cal_options:
            schema_dict[
                vol.Optional(
                    CONF_SELECTED_CALENDARS,
                    default=[c["id"] for c in self._calendars],
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=cal_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        if list_options:
            schema_dict[
                vol.Optional(
                    CONF_SELECTED_LISTS,
                    default=[lst["id"] for lst in self._lists],
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        if not schema_dict:
            # Nothing shared on Mac yet — still create entry
            self._data[CONF_SELECTED_CALENDARS] = []
            self._data[CONF_SELECTED_LISTS] = []
            return self.async_create_entry(
                title=f"Apple HA Sync ({self._data[CONF_AGENT_URL]})",
                data=self._data,
            )
        return self.async_show_form(
            step_id="sources",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "hint": "Only calendars/lists shared on the Mac appear here."
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        # HA 2025.12+: do not pass/assign config_entry — it is injected read-only.
        return AppleHASyncOptionsFlow()


class AppleHASyncOptionsFlow(config_entries.OptionsFlow):
    """Options gear flow — uses inherited self.config_entry (HA 2024.12+)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        client = AppleHASyncClient(
            self.hass,
            self.config_entry.data[CONF_AGENT_URL],
            self.config_entry.data[CONF_AGENT_TOKEN],
            verify_tls=self.config_entry.data.get(CONF_VERIFY_TLS, True),
            ca_path=self.config_entry.data.get(CONF_CA_PATH),
            cert_pin=self.config_entry.data.get(CONF_CERT_PIN),
            allow_insecure_http=self.config_entry.data.get(
                CONF_ALLOW_INSECURE_HTTP, False
            ),
        )
        calendars: list[dict[str, Any]] = []
        lists: list[dict[str, Any]] = []
        try:
            calendars = await client.list_calendars()
            lists = await client.list_reminder_lists()
        except Exception:
            _LOGGER.exception("Failed to refresh sources for options")

        current_cal = self.config_entry.options.get(
            CONF_SELECTED_CALENDARS,
            self.config_entry.data.get(
                CONF_SELECTED_CALENDARS, [c["id"] for c in calendars]
            ),
        )
        current_lists = self.config_entry.options.get(
            CONF_SELECTED_LISTS,
            self.config_entry.data.get(
                CONF_SELECTED_LISTS, [lst["id"] for lst in lists]
            ),
        )

        schema_dict: dict[Any, Any] = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_SCAN_INTERVAL,
                    self.config_entry.data.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
        }
        if calendars:
            schema_dict[
                vol.Optional(CONF_SELECTED_CALENDARS, default=current_cal)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": c["id"], "label": c.get("title") or c["id"]}
                        for c in calendars
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        if lists:
            schema_dict[
                vol.Optional(CONF_SELECTED_LISTS, default=current_lists)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": lst["id"], "label": lst.get("title") or lst["id"]}
                        for lst in lists
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
