"""EventKit backend via PyObjC (macOS only)."""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from shared.models import (
    CalendarSource,
    Event,
    EventPatch,
    PermissionStatus,
    ReminderList,
    TodoItem,
    TodoItemPatch,
    TodoItemStatus,
    content_hash_for,
)

_LOGGER = logging.getLogger(__name__)

# Authorization status string mapping (covers older + macOS 14+ enums)
_STATUS_MAP = {
    0: "not_determined",
    1: "restricted",
    2: "denied",
    3: "authorized",  # legacy full
    4: "write_only",
    5: "full_access",
}


def _nsdate_to_dt(nsdate: Any) -> datetime | None:
    if nsdate is None:
        return None
    # NSDate timeIntervalSince1970
    try:
        ts = float(nsdate.timeIntervalSince1970())
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    except Exception:
        return None


def _dt_to_nsdate(value: datetime | date, EventKit: Any) -> Any:
    from Foundation import NSDate

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.astimezone()
        return NSDate.dateWithTimeIntervalSince1970_(value.timestamp())
    # all-day date → midnight local
    dt = datetime(value.year, value.month, value.day)
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


class EventKitBackend:
    backend_id = "eventkit"

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("EventKitBackend requires macOS")
        try:
            import EventKit  # type: ignore
            from Foundation import NSDate  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "PyObjC EventKit not installed. pip install pyobjc-framework-EventKit"
            ) from exc
        self._EK = EventKit
        self._store = EventKit.EKEventStore.alloc().init()

    def get_permissions(self) -> PermissionStatus:
        EK = self._EK
        # Prefer modern APIs when present
        if hasattr(EK.EKEventStore, "authorizationStatusForEntityType_"):
            cal = int(
                EK.EKEventStore.authorizationStatusForEntityType_(
                    EK.EKEntityTypeEvent
                )
            )
            rem = int(
                EK.EKEventStore.authorizationStatusForEntityType_(
                    EK.EKEntityTypeReminder
                )
            )
        else:
            cal = rem = 0
        return PermissionStatus(
            calendar=_STATUS_MAP.get(cal, f"unknown_{cal}"),
            reminders=_STATUS_MAP.get(rem, f"unknown_{rem}"),
        )

    async def request_permissions(self) -> PermissionStatus:
        """Request full access; runs completion-handler APIs."""
        import asyncio

        EK = self._EK
        loop = asyncio.get_running_loop()

        def _request(entity_type: Any) -> None:
            done = asyncio.Event()
            result_box: dict[str, Any] = {}

            def handler(granted: bool, error: Any) -> None:
                result_box["granted"] = bool(granted)
                result_box["error"] = error
                loop.call_soon_threadsafe(done.set)

            # macOS 14+
            if entity_type == EK.EKEntityTypeEvent and hasattr(
                self._store, "requestFullAccessToEventsWithCompletion_"
            ):
                self._store.requestFullAccessToEventsWithCompletion_(handler)
            elif entity_type == EK.EKEntityTypeReminder and hasattr(
                self._store, "requestFullAccessToRemindersWithCompletion_"
            ):
                self._store.requestFullAccessToRemindersWithCompletion_(handler)
            elif hasattr(self._store, "requestAccessToEntityType_completion_"):
                self._store.requestAccessToEntityType_completion_(
                    entity_type, handler
                )
            else:
                done.set()

            # Wait synchronously from thread pool perspective via nest
            # We schedule on main and wait with asyncio from caller differently.
            # Simpler path: use concurrent future
            import time

            # Poll until handler fires (EventKit may need runloop)
            deadline = time.time() + 60
            while not done.is_set() and time.time() < deadline:
                time.sleep(0.05)
                # Spin NSRunLoop briefly
                try:
                    from Foundation import NSRunLoop, NSDate

                    NSRunLoop.currentRunLoop().runUntilDate_(
                        NSDate.dateWithTimeIntervalSinceNow_(0.05)
                    )
                except Exception:
                    pass

        await asyncio.to_thread(_request, EK.EKEntityTypeEvent)
        await asyncio.to_thread(_request, EK.EKEntityTypeReminder)
        return self.get_permissions()

    def open_privacy_settings(self, which: str = "both") -> None:
        urls = []
        if which in ("both", "calendar", "calendars"):
            urls.append(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars"
            )
        if which in ("both", "reminders"):
            urls.append(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Reminders"
            )
        for url in urls:
            subprocess.run(["open", url], check=False)

    def reset_tcc(self, which: str = "both") -> None:
        """Reset TCC entries so the system prompt can appear again."""
        services = []
        if which in ("both", "calendar", "calendars"):
            services.append("Calendar")
        if which in ("both", "reminders"):
            services.append("Reminders")
        for svc in services:
            subprocess.run(["tccutil", "reset", svc], check=False)

    def list_calendars(self, *, shared_only: bool = False) -> list[CalendarSource]:
        EK = self._EK
        out: list[CalendarSource] = []
        for cal in self._store.calendarsForEntityType_(EK.EKEntityTypeEvent) or []:
            cid = str(cal.calendarIdentifier())
            title = str(cal.title() or "")
            source = cal.source()
            source_name = str(source.title()) if source else None
            color = None
            try:
                cg = cal.CGColor()
                if cg is not None:
                    color = str(cg)
            except Exception:
                pass
            out.append(
                CalendarSource(
                    id=cid,
                    title=title,
                    color=color,
                    source_name=source_name,
                )
            )
        return out

    def list_reminder_lists(self, *, shared_only: bool = False) -> list[ReminderList]:
        EK = self._EK
        out: list[ReminderList] = []
        for cal in self._store.calendarsForEntityType_(EK.EKEntityTypeReminder) or []:
            cid = str(cal.calendarIdentifier())
            title = str(cal.title() or "")
            source = cal.source()
            source_name = str(source.title()) if source else None
            out.append(
                ReminderList(
                    id=cid,
                    title=title,
                    source_name=source_name,
                )
            )
        return out

    def _calendar_by_id(self, calendar_id: str, entity_type: Any) -> Any:
        for cal in self._store.calendarsForEntityType_(entity_type) or []:
            if str(cal.calendarIdentifier()) == calendar_id:
                return cal
        return None

    def get_events(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[Event]:
        EK = self._EK
        cal = self._calendar_by_id(calendar_id, EK.EKEntityTypeEvent)
        if cal is None:
            raise KeyError(calendar_id)
        predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            _dt_to_nsdate(start, EK),
            _dt_to_nsdate(end, EK),
            [cal],
        )
        events = self._store.eventsMatchingPredicate_(predicate) or []
        result: list[Event] = []
        for ev in events:
            uid = str(ev.eventIdentifier() or ev.calendarItemExternalIdentifier() or "")
            summary = str(ev.title() or "")
            description = str(ev.notes()) if ev.notes() else None
            location = str(ev.location()) if ev.location() else None
            all_day = bool(ev.isAllDay())
            start_dt = _nsdate_to_dt(ev.startDate())
            end_dt = _nsdate_to_dt(ev.endDate())
            if start_dt is None or end_dt is None:
                continue
            start_val: datetime | date = start_dt.date() if all_day else start_dt
            end_val: datetime | date = end_dt.date() if all_day else end_dt
            lm = _nsdate_to_dt(ev.lastModifiedDate()) if ev.lastModifiedDate() else None
            ch = content_hash_for(
                uid, summary, description, location, start_val, end_val, all_day
            )
            result.append(
                Event(
                    uid=uid,
                    calendar_id=calendar_id,
                    summary=summary,
                    start=start_val,
                    end=end_val,
                    description=description,
                    location=location,
                    all_day=all_day,
                    content_hash=ch,
                    last_modified=lm,
                )
            )
        return result

    def get_todo_items(self, list_id: str) -> list[TodoItem]:
        EK = self._EK
        cal = self._calendar_by_id(list_id, EK.EKEntityTypeReminder)
        if cal is None:
            raise KeyError(list_id)
        # Fetch incomplete + completed recently via predicate
        predicate = self._store.predicateForRemindersInCalendars_([cal])
        box: dict[str, list] = {"items": []}
        done = {"ok": False}

        def handler(reminders: Any) -> None:
            box["items"] = list(reminders or [])
            done["ok"] = True

        self._store.fetchRemindersMatchingPredicate_completion_(predicate, handler)
        # Spin runloop until completion fires
        import time
        from Foundation import NSDate, NSRunLoop

        deadline = time.time() + 10
        while not done["ok"] and time.time() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.05)
            )

        result: list[TodoItem] = []
        for rem in box["items"]:
            uid = str(
                rem.calendarItemIdentifier()
                or rem.calendarItemExternalIdentifier()
                or ""
            )
            summary = str(rem.title() or "")
            description = str(rem.notes()) if rem.notes() else None
            completed = bool(rem.isCompleted())
            status = (
                TodoItemStatus.COMPLETED if completed else TodoItemStatus.NEEDS_ACTION
            )
            due = None
            if rem.dueDateComponents() is not None:
                comps = rem.dueDateComponents()
                try:
                    y = comps.year()
                    m = comps.month()
                    d = comps.day()
                    if y and m and d:
                        if comps.hour() is not None and comps.hour() >= 0:
                            due = datetime(
                                int(y),
                                int(m),
                                int(d),
                                int(comps.hour() or 0),
                                int(comps.minute() or 0),
                            )
                        else:
                            due = date(int(y), int(m), int(d))
                except Exception:
                    due = None
            priority = int(rem.priority()) if rem.priority() else None
            lm = (
                _nsdate_to_dt(rem.lastModifiedDate())
                if rem.lastModifiedDate()
                else None
            )
            ch = content_hash_for(uid, summary, description, status.value, due, priority)
            result.append(
                TodoItem(
                    uid=uid,
                    list_id=list_id,
                    summary=summary,
                    status=status,
                    description=description,
                    due=due,
                    priority=priority or None,
                    content_hash=ch,
                    last_modified=lm,
                )
            )
        return result

    def create_event(self, calendar_id: str, event: Event) -> Event:
        EK = self._EK
        cal = self._calendar_by_id(calendar_id, EK.EKEntityTypeEvent)
        if cal is None:
            raise KeyError(calendar_id)
        ev = EK.EKEvent.eventWithEventStore_(self._store)
        ev.setCalendar_(cal)
        ev.setTitle_(event.summary)
        if event.description is not None:
            ev.setNotes_(event.description)
        if event.location is not None:
            ev.setLocation_(event.location)
        ev.setAllDay_(bool(event.all_day))
        ev.setStartDate_(_dt_to_nsdate(event.start, EK))
        ev.setEndDate_(_dt_to_nsdate(event.end, EK))
        ok, err = self._store.saveEvent_span_error_(ev, EK.EKSpanThisEvent, None)
        if not ok:
            raise RuntimeError(f"Failed to save event: {err}")
        return self._event_from_ek(ev, calendar_id)

    def patch_event(self, calendar_id: str, uid: str, patch: EventPatch) -> Event:
        EK = self._EK
        ev = self._store.eventWithIdentifier_(uid)
        if ev is None:
            raise KeyError(uid)
        if str(ev.calendar().calendarIdentifier()) != calendar_id:
            raise KeyError(uid)
        if "summary" in patch._fields_set:
            ev.setTitle_(patch.summary)
# Allow clearing a field by setting it to null/empty in the patch body.
        if "description" in patch._fields_set:
            ev.setNotes_(patch.description if patch.description is not None else "")
        if "location" in patch._fields_set:
            ev.setLocation_(patch.location if patch.location is not None else "")
        if "all_day" in patch._fields_set and patch.all_day is not None:
            ev.setAllDay_(bool(patch.all_day))
        if "start" in patch._fields_set and patch.start is not None:
            ev.setStartDate_(_dt_to_nsdate(patch.start, EK))
        if "end" in patch._fields_set and patch.end is not None:
            ev.setEndDate_(_dt_to_nsdate(patch.end, EK))
        ok, err = self._store.saveEvent_span_error_(ev, EK.EKSpanThisEvent, None)
        if not ok:
            raise RuntimeError(f"Failed to patch event: {err}")
        return self._event_from_ek(ev, calendar_id)

    def delete_event(
        self,
        calendar_id: str,
        uid: str,
        *,
        recurrence_id: str | None = None,
    ) -> None:
        EK = self._EK
        ev = self._store.eventWithIdentifier_(uid)
        if ev is None:
            raise KeyError(uid)
        if str(ev.calendar().calendarIdentifier()) != calendar_id:
            raise KeyError(uid)
        ok, err = self._store.removeEvent_span_error_(ev, EK.EKSpanThisEvent, None)
        if not ok:
            raise RuntimeError(f"Failed to delete event: {err}")

    def create_todo_item(self, list_id: str, item: TodoItem) -> TodoItem:
        EK = self._EK
        cal = self._calendar_by_id(list_id, EK.EKEntityTypeReminder)
        if cal is None:
            raise KeyError(list_id)
        rem = EK.EKReminder.reminderWithEventStore_(self._store)
        rem.setCalendar_(cal)
        rem.setTitle_(item.summary)
        if item.description is not None:
            rem.setNotes_(item.description)
        rem.setCompleted_(item.status == TodoItemStatus.COMPLETED)
        if item.priority:
            rem.setPriority_(int(item.priority))
        if item.due is not None:
            self._set_reminder_due(rem, item.due)
        ok, err = self._store.saveReminder_commit_error_(rem, True, None)
        if not ok:
            raise RuntimeError(f"Failed to save reminder: {err}")
        return self._todo_from_ek(rem, list_id)

    def patch_todo_item(
        self, list_id: str, uid: str, patch: TodoItemPatch
    ) -> TodoItem:
        rem = self._find_reminder(uid, list_id)
        if rem is None:
            raise KeyError(uid)
        if "summary" in patch._fields_set:
            rem.setTitle_(patch.summary)
        if "description" in patch._fields_set:
            rem.setNotes_(patch.description)
        if "status" in patch._fields_set and patch.status is not None:
            rem.setCompleted_(patch.status == TodoItemStatus.COMPLETED)
        if "priority" in patch._fields_set:
            rem.setPriority_(int(patch.priority or 0))
        if "due" in patch._fields_set:
            if patch.due is None:
                rem.setDueDateComponents_(None)
            else:
                self._set_reminder_due(rem, patch.due)
        ok, err = self._store.saveReminder_commit_error_(rem, True, None)
        if not ok:
            raise RuntimeError(f"Failed to patch reminder: {err}")
        return self._todo_from_ek(rem, list_id)

    def delete_todo_item(self, list_id: str, uid: str) -> None:
        rem = self._find_reminder(uid, list_id)
        if rem is None:
            raise KeyError(uid)
        ok, err = self._store.removeReminder_commit_error_(rem, True, None)
        if not ok:
            raise RuntimeError(f"Failed to delete reminder: {err}")

    def _set_reminder_due(self, rem: Any, due: datetime | date) -> None:
        from Foundation import NSDateComponents

        comps = NSDateComponents.alloc().init()
        comps.setYear_(due.year)
        comps.setMonth_(due.month)
        comps.setDay_(due.day)
        if isinstance(due, datetime):
            comps.setHour_(due.hour)
            comps.setMinute_(due.minute)
            comps.setSecond_(due.second)
        rem.setDueDateComponents_(comps)

    def _find_reminder(self, uid: str, list_id: str) -> Any | None:
        for item in self.get_todo_items(list_id):
            pass  # force fetch path for runloop; then search store
        # Direct lookup by calendarItemIdentifier across list
        EK = self._EK
        cal = self._calendar_by_id(list_id, EK.EKEntityTypeReminder)
        if cal is None:
            return None
        predicate = self._store.predicateForRemindersInCalendars_([cal])
        box: dict[str, Any] = {"match": None}
        import time
        from Foundation import NSDate, NSRunLoop

        def handler(reminders: Any) -> None:
            for rem in reminders or []:
                rid = str(
                    rem.calendarItemIdentifier()
                    or rem.calendarItemExternalIdentifier()
                    or ""
                )
                if rid == uid:
                    box["match"] = rem
                    return

        self._store.fetchRemindersMatchingPredicate_completion_(predicate, handler)
        deadline = time.time() + 5
        while box["match"] is None and time.time() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(
                NSDate.dateWithTimeIntervalSinceNow_(0.05)
            )
        return box["match"]

    def _event_from_ek(self, ev: Any, calendar_id: str) -> Event:
        uid = str(ev.eventIdentifier() or "")
        summary = str(ev.title() or "")
        description = str(ev.notes()) if ev.notes() else None
        location = str(ev.location()) if ev.location() else None
        all_day = bool(ev.isAllDay())
        start_dt = _nsdate_to_dt(ev.startDate()) or datetime.now().astimezone()
        end_dt = _nsdate_to_dt(ev.endDate()) or (start_dt + timedelta(hours=1))
        start_val: datetime | date = start_dt.date() if all_day else start_dt
        end_val: datetime | date = end_dt.date() if all_day else end_dt
        return Event(
            uid=uid,
            calendar_id=calendar_id,
            summary=summary,
            start=start_val,
            end=end_val,
            description=description,
            location=location,
            all_day=all_day,
            content_hash=content_hash_for(
                uid, summary, description, location, start_val, end_val, all_day
            ),
        )

    def _todo_from_ek(self, rem: Any, list_id: str) -> TodoItem:
        uid = str(rem.calendarItemIdentifier() or "")
        summary = str(rem.title() or "")
        description = str(rem.notes()) if rem.notes() else None
        status = (
            TodoItemStatus.COMPLETED
            if rem.isCompleted()
            else TodoItemStatus.NEEDS_ACTION
        )
        return TodoItem(
            uid=uid,
            list_id=list_id,
            summary=summary,
            status=status,
            description=description,
            content_hash=content_hash_for(uid, summary, description, status.value),
        )
