"""Calendar platform — Mac EventKit calendars as HA calendar entities."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AppleHASyncCoordinator

_LOGGER = logging.getLogger(__name__)
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CALENDAR_DOMAIN = "calendar"


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
        # Seed HA calendar color from Mac (entity registry stores it on first add).
        if color := _normalize_hex_color(info.get("color")):
            self._attr_initial_color = color

    @property
    def available(self) -> bool:
        if self.coordinator.permission_error:
            return False
        return self._calendar_id in (self.coordinator.data or {}).get("calendars", {})

    @property
    def initial_color(self) -> str | None:
        """Mac calendar color as #RRGGBB (Mac is master)."""
        info = (self.coordinator.data or {}).get("calendars", {}).get(self._calendar_id) or {}
        if color := _normalize_hex_color(info.get("color")):
            return color
        return getattr(self, "_attr_initial_color", None)

    @property
    def event(self) -> CalendarEvent | None:
        return self._event

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._sync_mac_color_to_registry()

    @callback
    def _handle_coordinator_update(self) -> None:
        # Keep HA frontend color aligned when the Mac calendar color changes.
        self._sync_mac_color_to_registry()
        super()._handle_coordinator_update()

    @callback
    def _sync_mac_color_to_registry(self) -> None:
        color = self.initial_color
        if not color or not self.registry_entry:
            return
        registry = er.async_get(self.hass)
        current = (self.registry_entry.options.get(_CALENDAR_DOMAIN) or {}).get("color")
        if current == color:
            return
        registry.async_update_entity_options(
            self.entity_id,
            _CALENDAR_DOMAIN,
            {**(self.registry_entry.options.get(_CALENDAR_DOMAIN) or {}), "color": color},
        )

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
        notes, url = _split_notes_and_url(kwargs.get("description"))
        body = {
            "summary": kwargs.get("summary"),
            "description": notes,
            "location": kwargs.get("location"),
            "url": url,
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
        for key in ("summary", "location"):
            if key in event:
                patch[key] = event[key]
        if "description" in event:
            notes, url = _split_notes_and_url(event.get("description"))
            patch["description"] = notes
            patch["url"] = url
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


def _normalize_hex_color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    color = value.strip()
    if _HEX_COLOR.fullmatch(color):
        return color.lower()
    return None


def _to_calendar_event(item: dict[str, Any]) -> CalendarEvent:
    start = _parse(item["start"])
    end = _parse(item["end"])
    return CalendarEvent(
        uid=item.get("uid"),
        summary=item.get("summary") or "",
        start=start,
        end=end,
        description=_compose_description(item.get("description"), item.get("url")),
        location=item.get("location") or None,
    )


def _compose_description(notes: Any, url: Any) -> str | None:
    """HA CalendarEvent has no URL field — fold EventKit URL into description."""
    text = notes.strip() if isinstance(notes, str) and notes.strip() else None
    link = url.strip() if isinstance(url, str) and url.strip() else None
    if text and link:
        if link in text:
            return text
        return f"{text}\n\n{link}"
    return text or link


_URL_ONLY_LINE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def _split_notes_and_url(description: Any) -> tuple[str | None, str | None]:
    """Best-effort split of HA description back into notes + URL for EventKit."""
    if not isinstance(description, str):
        return None, None
    text = description.strip()
    if not text:
        return None, None
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Drop trailing blank lines
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _URL_ONLY_LINE.fullmatch(lines[-1].strip()):
        link = lines[-1].strip()
        while len(lines) > 1 and not lines[-2].strip():
            lines.pop(-2)
        lines.pop()
        notes = "\n".join(lines).strip() or None
        return notes, link
    return text, None


def _parse(value: str) -> date | datetime:
    if len(value) == 10:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
