"""Sensor platform — Mac Focus Mode name."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AppleHASyncCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AppleHASyncCoordinator = data["coordinator"]
    added = False

    def _maybe_add() -> None:
        nonlocal added
        if added:
            return
        if (coordinator.data or {}).get("focus") is None:
            return
        async_add_entities([AppleHASyncFocusSensor(coordinator, entry)])
        added = True

    _maybe_add()
    entry.async_on_unload(coordinator.async_add_listener(_maybe_add))


class AppleHASyncFocusSensor(
    CoordinatorEntity[AppleHASyncCoordinator], SensorEntity
):
    """Current Focus mode name (or off)."""

    _attr_has_entity_name = True
    _attr_name = "Focus"
    _attr_icon = "mdi:moon-waning-crescent"

    def __init__(
        self, coordinator: AppleHASyncCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_focus"

    @property
    def available(self) -> bool:
        focus = (self.coordinator.data or {}).get("focus")
        if focus is None:
            return False
        return focus.get("permission") == "ok"

    @property
    def native_value(self) -> str:
        focus = (self.coordinator.data or {}).get("focus") or {}
        if not focus.get("active"):
            return "off"
        return str(focus.get("mode_name") or focus.get("mode_id") or "on")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        focus = (self.coordinator.data or {}).get("focus") or {}
        return {
            "mode_id": focus.get("mode_id"),
            "available_modes": focus.get("available_modes") or [],
            "permission": focus.get("permission"),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
