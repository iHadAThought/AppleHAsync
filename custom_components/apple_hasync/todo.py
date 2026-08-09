"""Todo platform — Mac Reminders lists as HA todo entities."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, cast

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    lists = (coordinator.data or {}).get("lists", {})
    entities = [
        AppleHASyncTodoList(coordinator, entry, list_id, info)
        for list_id, info in lists.items()
    ]
    async_add_entities(entities)

    known = set(lists)

    def _check_new() -> None:
        current = set((coordinator.data or {}).get("lists", {}))
        added = current - known
        if not added:
            return
        new_entities = []
        for list_id in added:
            info = coordinator.data["lists"][list_id]
            new_entities.append(
                AppleHASyncTodoList(coordinator, entry, list_id, info)
            )
            known.add(list_id)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_check_new))


class AppleHASyncTodoList(CoordinatorEntity[AppleHASyncCoordinator], TodoListEntity):
    """One shared Mac Reminders list."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    )

    def __init__(
        self,
        coordinator: AppleHASyncCoordinator,
        entry: ConfigEntry,
        list_id: str,
        info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._list_id = list_id
        self._attr_name = info.get("title") or list_id
        self._attr_unique_id = f"{entry.entry_id}_todo_{list_id}"
        self._attr_todo_items = self._map_items()

    @property
    def available(self) -> bool:
        if self.coordinator.permission_error:
            return False
        return self._list_id in (self.coordinator.data or {}).get("lists", {})

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_todo_items = self._map_items()
        super()._handle_coordinator_update()

    def _map_items(self) -> list[TodoItem]:
        raw = (self.coordinator.data or {}).get("todo_items", {}).get(self._list_id, [])
        items: list[TodoItem] = []
        for item in raw:
            uid = item.get("uid")
            if uid and self.coordinator.is_echo("todo", uid):
                # Still show Mac master data; echo flag only avoids thrash loops externally
                pass
            status = (
                TodoItemStatus.COMPLETED
                if item.get("status") == "completed"
                else TodoItemStatus.NEEDS_ACTION
            )
            due = None
            if item.get("due"):
                due_s = item["due"]
                due = (
                    date.fromisoformat(due_s)
                    if len(due_s) == 10
                    else datetime.fromisoformat(due_s.replace("Z", "+00:00"))
                )
            items.append(
                TodoItem(
                    uid=uid,
                    summary=item.get("summary") or "",
                    status=status,
                    description=item.get("description"),
                    due=due,
                )
            )
        return items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        body: dict[str, Any] = {
            "summary": item.summary,
            "description": item.description,
            "status": (
                "completed"
                if item.status == TodoItemStatus.COMPLETED
                else "needs_action"
            ),
        }
        if item.due is not None:
            body["due"] = item.due.isoformat()
        created = await self.coordinator.client.create_item(self._list_id, body)
        if uid := created.get("uid"):
            self.coordinator.mark_echo("todo", uid)
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        uid = cast(str, item.uid)
        new_data: dict[str, Any] = {
            "summary": item.summary,
            "description": item.description,
            "status": (
                "completed"
                if item.status == TodoItemStatus.COMPLETED
                else "needs_action"
            ),
            "due": item.due.isoformat() if item.due else None,
        }
        # Only send changed fields vs Mac snapshot
        patch = self.coordinator.diff_todo_patch(self._list_id, uid, new_data)
        if not patch:
            return
        await self.coordinator.client.patch_item(self._list_id, uid, patch)
        self.coordinator.mark_echo("todo", uid)
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self.coordinator.client.delete_item(self._list_id, uid)
            self.coordinator.mark_echo("todo", uid)
        await self.coordinator.async_request_refresh()
