"""Binary sensor — Mac Focus Mode active."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
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
        async_add_entities([AppleHASyncFocusActiveBinarySensor(coordinator, entry)])
        added = True

    _maybe_add()
    entry.async_on_unload(coordinator.async_add_listener(_maybe_add))


class AppleHASyncFocusActiveBinarySensor(
    CoordinatorEntity[AppleHASyncCoordinator], BinarySensorEntity
):
    """On when a Focus mode is active on the Mac."""

    _attr_has_entity_name = True
    _attr_name = "Focus active"
    _attr_icon = "mdi:focus-field"

    def __init__(
        self, coordinator: AppleHASyncCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_focus_active"

    @property
    def available(self) -> bool:
        focus = (self.coordinator.data or {}).get("focus")
        if focus is None:
            return False
        return focus.get("permission") == "ok"

    @property
    def is_on(self) -> bool:
        focus = (self.coordinator.data or {}).get("focus") or {}
        return bool(focus.get("active"))

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
