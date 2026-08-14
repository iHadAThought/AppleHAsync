"""Data update coordinator — Mac is master; field patches from HA only."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backend import BackendClient, build_backend
from .client import AppleHASyncAuthError, AppleHASyncPermissionError
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_CALENDARS,
    CONF_SELECTED_LISTS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class AppleHASyncCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll Mac agent for shared calendars/lists; Mac wins on conflict."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.client: BackendClient = build_backend(hass, entry.data)
        interval = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self._echo_until: dict[str, float] = {}
        self.permission_error: dict[str, Any] | None = None

    @property
    def selected_calendars(self) -> list[str] | None:
        opts = self.entry.options.get(CONF_SELECTED_CALENDARS)
        if opts is not None:
            return list(opts)
        data = self.entry.data.get(CONF_SELECTED_CALENDARS)
        return list(data) if data is not None else None

    @property
    def selected_lists(self) -> list[str] | None:
        opts = self.entry.options.get(CONF_SELECTED_LISTS)
        if opts is not None:
            return list(opts)
        data = self.entry.data.get(CONF_SELECTED_LISTS)
        return list(data) if data is not None else None

    def mark_echo(self, kind: str, uid: str, seconds: float = 8.0) -> None:
        # Mirror Mac sync_meta: ignore our own writes briefly to avoid loops.
        self._echo_until[f"{kind}:{uid}"] = time.time() + seconds

    def is_echo(self, kind: str, uid: str) -> bool:
        key = f"{kind}:{uid}"
        until = self._echo_until.get(key)
        if until is None:
            return False
        if until < time.time():
            self._echo_until.pop(key, None)
            return False
        return True

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            calendars = await self.client.list_calendars()
            lists = await self.client.list_reminder_lists()
            self.permission_error = None
        except AppleHASyncPermissionError as err:
            self.permission_error = getattr(err, "detail", {"error": str(err)})
            raise UpdateFailed(f"Mac permission required: {err}") from err
        except AppleHASyncAuthError as err:
            raise UpdateFailed(f"Auth failed: {err}") from err
        except Exception as err:
            raise UpdateFailed(str(err)) from err

        sel_cal = self.selected_calendars
        sel_list = self.selected_lists
        if sel_cal is not None:
            calendars = [c for c in calendars if c["id"] in sel_cal]
        if sel_list is not None:
            lists = [lst for lst in lists if lst["id"] in sel_list]

        todo_items: dict[str, list[dict[str, Any]]] = {}
        for lst in lists:
            try:
                todo_items[lst["id"]] = await self.client.get_items(lst["id"])
            except Exception as err:
                _LOGGER.warning("Failed to fetch list %s: %s", lst["id"], err)
                todo_items[lst["id"]] = []

        focus: dict[str, Any] | None = None
        try:
            focus = await self.client.get_focus()
        except KeyError:
            # Not shared (404) — fail closed; no Focus entities.
            focus = None
        except AppleHASyncPermissionError as err:
            # Unexpected for Focus; treat as unavailable this cycle.
            _LOGGER.debug("Focus fetch permission error: %s", err)
            focus = None
        except Exception as err:
            _LOGGER.warning("Failed to fetch Focus status: %s", err)
            focus = None

        return {
            "calendars": {c["id"]: c for c in calendars},
            "lists": {lst["id"]: lst for lst in lists},
            "todo_items": todo_items,
            "focus": focus,
        }

    @callback
    def request_refresh_from_webhook(self) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    def diff_todo_patch(
        self, list_id: str, uid: str, new_item: dict[str, Any]
    ) -> dict[str, Any]:
        """Return only changed fields vs last known Mac snapshot (Mac-master patch)."""
        items = (self.data or {}).get("todo_items", {}).get(list_id, [])
        old = next((i for i in items if i.get("uid") == uid), None)
        if not old:
            # Full create-style fields that are present
            return {k: v for k, v in new_item.items() if k != "uid" and v is not None}
        patch: dict[str, Any] = {}
        for key in (
            "summary",
            "description",
            "status",
            "due",
            "priority",
            "location",
            "url",
            "flagged",
            "tags",
        ):
            if key in new_item and new_item[key] != old.get(key):
                patch[key] = new_item[key]
        return patch

    def diff_event_patch(
        self, calendar_id: str, uid: str, new_event: dict[str, Any]
    ) -> dict[str, Any]:
        # Events are fetched on demand; patch only keys provided by HA update
        return {k: v for k, v in new_event.items() if k not in ("uid",) and v is not None}
