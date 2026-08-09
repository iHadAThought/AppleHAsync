"""Backend protocol — EventKit now; CalDAV later without changing entity code."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    CalendarSource,
    Event,
    EventPatch,
    PermissionStatus,
    ReminderList,
    TodoItem,
    TodoItemPatch,
)


@runtime_checkable
class Backend(Protocol):
    """Abstract calendar/reminders backend.

    Implementations:
    - EventKitBackend (mac_agent) — v1
    - CalDAVBackend — groundwork / future
    """

    backend_id: str  # "eventkit" | "caldav"

    def get_permissions(self) -> PermissionStatus: ...

    async def request_permissions(self) -> PermissionStatus: ...

    def list_calendars(self, *, shared_only: bool = True) -> list[CalendarSource]: ...

    def list_reminder_lists(self, *, shared_only: bool = True) -> list[ReminderList]: ...

    def get_events(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
    ) -> list[Event]: ...

    def get_todo_items(self, list_id: str) -> list[TodoItem]: ...

    def create_event(self, calendar_id: str, event: Event) -> Event: ...

    def patch_event(self, calendar_id: str, uid: str, patch: EventPatch) -> Event: ...

    def delete_event(
        self,
        calendar_id: str,
        uid: str,
        *,
        recurrence_id: str | None = None,
    ) -> None: ...

    def create_todo_item(self, list_id: str, item: TodoItem) -> TodoItem: ...

    def patch_todo_item(
        self, list_id: str, uid: str, patch: TodoItemPatch
    ) -> TodoItem: ...

    def delete_todo_item(self, list_id: str, uid: str) -> None: ...
