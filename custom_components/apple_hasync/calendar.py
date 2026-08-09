"""Calendar platform — Mac EventKit calendars as HA calendar entities."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AppleHASyncCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AppleHASyncCoordinator = data["coordinator"]
    calendars = (coordinator.data or {}).get("calendars", {})
    entities = [
        AppleHASyncCalendar(coordinator, entry, cal_id, info)
        for cal_id, info in calendars.items()
    ]
    async_add_entities(entities)

    # Track newly shared calendars on refresh
    known = set(calendars)

    def _check_new() -> None:
        current = set((coordinator.data or {}).get("calendars", {}))
        added = current - known
        if not added:
            return
        new_entities = []
        for cal_id in added:
            info = coordinator.data["calendars"][cal_id]
            new_entities.append(AppleHASyncCalendar(coordinator, entry, cal_id, info))
            known.add(cal_id)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_check_new))


class AppleHASyncCalendar(CoordinatorEntity[AppleHASyncCoordinator], CalendarEntity):
    """One shared Mac calendar."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
    )

    def __init__(
        self,
        coordinator: AppleHASyncCoordinator,
        entry: ConfigEntry,
        calendar_id: str,
        info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._calendar_id = calendar_id
        self._attr_name = info.get("title") or calendar_id
        self._attr_unique_id = f"{entry.entry_id}_cal_{calendar_id}"
        self._event: CalendarEvent | None = None

    @property
    def available(self) -> bool:
        if self.coordinator.permission_error:
            return False
        return self._calendar_id in (self.coordinator.data or {}).get("calendars", {})

    @property
    def event(self) -> CalendarEvent | None:
        return self._event

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        raw = await self.coordinator.client.get_events(
            self._calendar_id, start_date, end_date
        )
        return [_to_calendar_event(item) for item in raw]

    async def async_create_event(self, **kwargs: Any) -> None:
        body = {
            "summary": kwargs.get("summary"),
            "description": kwargs.get("description"),
            "location": kwargs.get("location"),
            "start": _fmt(kwargs["start"]),
            "end": _fmt(kwargs["end"]),
            "all_day": isinstance(kwargs.get("start"), date)
            and not isinstance(kwargs.get("start"), datetime),
        }
        created = await self.coordinator.client.create_event(self._calendar_id, body)
        if uid := created.get("uid"):
            self.coordinator.mark_echo("event", uid)
        await self.coordinator.async_request_refresh()

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        await self.coordinator.client.delete_event(self._calendar_id, uid)
        self.coordinator.mark_echo("event", uid)
        await self.coordinator.async_request_refresh()

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        # Field-level patch only — send keys HA provided
        patch: dict[str, Any] = {}
        for key in ("summary", "description", "location"):
            if key in event:
                patch[key] = event[key]
        if "dtstart" in event:
            patch["start"] = _fmt(event["dtstart"])
        if "start" in event:
            patch["start"] = _fmt(event["start"])
        if "dtend" in event:
            patch["end"] = _fmt(event["dtend"])
        if "end" in event:
            patch["end"] = _fmt(event["end"])
        if not patch:
            return
        await self.coordinator.client.patch_event(self._calendar_id, uid, patch)
        self.coordinator.mark_echo("event", uid)
        await self.coordinator.async_request_refresh()


def _fmt(value: date | datetime) -> str:
    return value.isoformat()


def _to_calendar_event(item: dict[str, Any]) -> CalendarEvent:
    start = _parse(item["start"])
    end = _parse(item["end"])
    return CalendarEvent(
        uid=item.get("uid"),
        summary=item.get("summary") or "",
        start=start,
        end=end,
        description=item.get("description"),
        location=item.get("location"),
    )


def _parse(value: str) -> date | datetime:
    if len(value) == 10:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
