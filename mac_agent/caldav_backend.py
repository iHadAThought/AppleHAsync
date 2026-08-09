"""CalDAV backend groundwork — not implemented for v1."""

from __future__ import annotations

from datetime import datetime

from shared.models import (
    CalendarSource,
    Event,
    EventPatch,
    PermissionStatus,
    ReminderList,
    TodoItem,
    TodoItemPatch,
)


class CalDAVBackend:
    """Placeholder for future iCloud/CalDAV backend (plan option B).

    Implement the same method surface as EventKitBackend / shared.Backend.
    """

    backend_id = "caldav"

    def __init__(self, url: str, username: str, password: str) -> None:
        self.url = url
        self.username = username
        self.password = password
        raise NotImplementedError(
            "CalDAV backend is not available yet. Use EventKit local agent (backend=local_agent)."
        )

    def get_permissions(self) -> PermissionStatus:
        raise NotImplementedError

    async def request_permissions(self) -> PermissionStatus:
        raise NotImplementedError

    def list_calendars(self, *, shared_only: bool = True) -> list[CalendarSource]:
        raise NotImplementedError

    def list_reminder_lists(self, *, shared_only: bool = True) -> list[ReminderList]:
        raise NotImplementedError

    def get_events(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[Event]:
        raise NotImplementedError

    def get_todo_items(self, list_id: str) -> list[TodoItem]:
        raise NotImplementedError

    def create_event(self, calendar_id: str, event: Event) -> Event:
        raise NotImplementedError

    def patch_event(self, calendar_id: str, uid: str, patch: EventPatch) -> Event:
        raise NotImplementedError

    def delete_event(
        self,
        calendar_id: str,
        uid: str,
        *,
        recurrence_id: str | None = None,
    ) -> None:
        raise NotImplementedError

    def create_todo_item(self, list_id: str, item: TodoItem) -> TodoItem:
        raise NotImplementedError

    def patch_todo_item(
        self, list_id: str, uid: str, patch: TodoItemPatch
    ) -> TodoItem:
        raise NotImplementedError

    def delete_todo_item(self, list_id: str, uid: str) -> None:
        raise NotImplementedError
