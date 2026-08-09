"""Stable domain models shared by mac_agent and HA integration concepts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class TodoItemStatus(str, Enum):
    NEEDS_ACTION = "needs_action"
    COMPLETED = "completed"


@dataclass
class PermissionStatus:
    calendar: str  # not_determined | restricted | denied | write_only | full_access
    reminders: str

    def to_dict(self) -> dict[str, str]:
        return {"calendar": self.calendar, "reminders": self.reminders}

    @property
    def calendar_ok(self) -> bool:
        return self.calendar in ("full_access", "authorized", "write_only")

    @property
    def reminders_ok(self) -> bool:
        return self.reminders in ("full_access", "authorized", "write_only")

    @property
    def calendar_full(self) -> bool:
        return self.calendar in ("full_access", "authorized")

    @property
    def reminders_full(self) -> bool:
        return self.reminders in ("full_access", "authorized")


@dataclass
class CalendarSource:
    id: str
    title: str
    color: str | None = None
    source_name: str | None = None
    shared: bool = False
    content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReminderList:
    id: str
    title: str
    color: str | None = None
    source_name: str | None = None
    shared: bool = False
    content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Event:
    uid: str
    calendar_id: str
    summary: str
    start: datetime | date
    end: datetime | date
    description: str | None = None
    location: str | None = None
    url: str | None = None
    all_day: bool = False
    recurrence_id: str | None = None
    rrule: str | None = None
    content_hash: str | None = None
    last_modified: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("start", "end", "last_modified"):
            val = data.get(key)
            if isinstance(val, datetime):
                data[key] = val.isoformat()
            elif isinstance(val, date):
                data[key] = val.isoformat()
        return data


@dataclass
class TodoItem:
    uid: str
    list_id: str
    summary: str
    status: TodoItemStatus = TodoItemStatus.NEEDS_ACTION
    description: str | None = None  # EventKit notes (not HA-composed)
    due: datetime | date | None = None
    priority: int | None = None  # 1 high, 5 medium, 9 low (Apple-ish)
    location: str | None = None
    url: str | None = None
    flagged: bool | None = None
    tags: list[str] | None = None
    completed_at: datetime | None = None
    content_hash: str | None = None
    last_modified: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        if isinstance(data.get("due"), (datetime, date)):
            data["due"] = data["due"].isoformat()
        if isinstance(data.get("last_modified"), datetime):
            data["last_modified"] = data["last_modified"].isoformat()
        if isinstance(data.get("completed_at"), datetime):
            data["completed_at"] = data["completed_at"].isoformat()
        return data


@dataclass
class EventPatch:
    """Field-level patch; only set fields are applied."""

    summary: str | None = field(default=None)
    description: str | None = field(default=None)
    location: str | None = field(default=None)
    url: str | None = field(default=None)
    start: datetime | date | None = field(default=None)
    end: datetime | date | None = field(default=None)
    all_day: bool | None = field(default=None)
    _fields_set: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventPatch:
        known = {
            "summary",
            "description",
            "location",
            "url",
            "start",
            "end",
            "all_day",
        }
        present = {k for k in data if k in known}
        patch = cls(**{k: data[k] for k in present})
        patch._fields_set = present
        return patch

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self._fields_set:
            val = getattr(self, name)
            if isinstance(val, (datetime, date)):
                out[name] = val.isoformat()
            else:
                out[name] = val
        return out


@dataclass
class TodoItemPatch:
    """Field-level patch; only set fields are applied."""

    summary: str | None = field(default=None)
    description: str | None = field(default=None)
    status: TodoItemStatus | None = field(default=None)
    due: datetime | date | None = field(default=None)
    priority: int | None = field(default=None)
    location: str | None = field(default=None)
    url: str | None = field(default=None)
    flagged: bool | None = field(default=None)
    tags: list[str] | None = field(default=None)
    _fields_set: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoItemPatch:
        known = {
            "summary",
            "description",
            "status",
            "due",
            "priority",
            "location",
            "url",
            "flagged",
            "tags",
        }
        present = {k for k in data if k in known}
        kwargs: dict[str, Any] = {}
        for k in present:
            if k == "status" and data[k] is not None:
                kwargs[k] = TodoItemStatus(data[k])
            else:
                kwargs[k] = data[k]
        patch = cls(**kwargs)
        patch._fields_set = present
        return patch

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self._fields_set:
            val = getattr(self, name)
            if isinstance(val, TodoItemStatus):
                out[name] = val.value
            elif isinstance(val, (datetime, date)):
                out[name] = val.isoformat()
            else:
                out[name] = val
        return out


def content_hash_for(*parts: Any) -> str:
    import hashlib
    import json

    payload = json.dumps(parts, default=str, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
