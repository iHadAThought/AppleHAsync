"""Backend abstraction for HA side."""

from __future__ import annotations

from typing import Any, Protocol

from homeassistant.core import HomeAssistant

from .client import AppleHASyncClient, client_from_entry_data
from .const import BACKEND_CALDAV, BACKEND_LOCAL_AGENT, CONF_BACKEND


class BackendClient(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def list_calendars(self) -> list[dict[str, Any]]: ...

    async def list_reminder_lists(self) -> list[dict[str, Any]]: ...

    async def get_events(self, calendar_id: str, start, end) -> list[dict[str, Any]]: ...

    async def get_items(self, list_id: str) -> list[dict[str, Any]]: ...

    async def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]: ...

    async def patch_event(
        self, calendar_id: str, uid: str, patch: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_event(self, calendar_id: str, uid: str) -> None: ...

    async def create_item(self, list_id: str, body: dict[str, Any]) -> dict[str, Any]: ...

    async def patch_item(
        self, list_id: str, uid: str, patch: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_item(self, list_id: str, uid: str) -> None: ...


class CalDAVBackendClient:
    """Reserved stub — not shipped in the public v1 UI (Local Mac Agent only)."""

    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(
            "CalDAV backend is not available. Use Local Mac Agent."
        )


def build_backend(hass: HomeAssistant, data: dict[str, Any]) -> BackendClient:
    backend = data.get(CONF_BACKEND, BACKEND_LOCAL_AGENT)
    if backend == BACKEND_CALDAV:
        raise NotImplementedError("CalDAV backend coming soon")
    if backend != BACKEND_LOCAL_AGENT:
        raise ValueError(f"Unknown backend: {backend}")
    return client_from_entry_data(hass, data)
