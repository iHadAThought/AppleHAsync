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


def _cgcolor_to_hex(cg: Any) -> str | None:
    """Convert EventKit CGColor / NSColor to #RRGGBB for Home Assistant."""
    if cg is None:
        return None

    def _rgb_to_hex(r: float, g: float, b: float) -> str:
        return (
            f"#{int(round(max(0.0, min(1.0, r)) * 255)):02x}"
            f"{int(round(max(0.0, min(1.0, g)) * 255)):02x}"
            f"{int(round(max(0.0, min(1.0, b)) * 255)):02x}"
        )

    # Preferred: NSColor bridge (handles color-space conversion)
    try:
        from AppKit import NSColor, NSColorSpace

        ns = None
        try:
            ns = NSColor.colorWithCGColor_(cg)
        except Exception:
            ns = cg if hasattr(cg, "redComponent") else None
        if ns is not None:
            rgb = ns.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace())
            if rgb is not None:
                return _rgb_to_hex(
                    float(rgb.redComponent()),
                    float(rgb.greenComponent()),
                    float(rgb.blueComponent()),
                )
    except Exception:
        _LOGGER.debug("NSColor hex conversion failed", exc_info=True)

    # Fallback: raw CGColor components (device RGB / sRGB)
    try:
        from Quartz import CGColorGetComponents, CGColorGetNumberOfComponents

        n = int(CGColorGetNumberOfComponents(cg))
        comps = CGColorGetComponents(cg)
        if n >= 3 and comps is not None:
            return _rgb_to_hex(float(comps[0]), float(comps[1]), float(comps[2]))
        if n == 2 and comps is not None:
            # grayscale + alpha
            g = float(comps[0])
            return _rgb_to_hex(g, g, g)
    except Exception:
        _LOGGER.debug("CGColor hex conversion failed", exc_info=True)

    return None


def _calendar_color_hex(cal: Any) -> str | None:
    try:
        return _cgcolor_to_hex(cal.CGColor())
    except Exception:
        return None


def _eventkit_url_string(ev: Any) -> str | None:
    try:
        url = ev.URL()
    except Exception:
        return None
    if url is None:
        return None
    try:
        if hasattr(url, "absoluteString"):
            value = str(url.absoluteString() or "").strip()
        else:
            value = str(url).strip()
    except Exception:
        return None
    return value or None


def _eventkit_location_string(ev: Any) -> str | None:
    """Prefer plain location; fall back to structured location title (addresses)."""
    try:
        loc = str(ev.location() or "").strip()
        if loc:
            return loc
    except Exception:
        pass
    try:
        structured = ev.structuredLocation()
        if structured is None:
            return None
        title = str(structured.title() or "").strip()
        return title or None
    except Exception:
        return None


def _set_eventkit_url(ev: Any, url: str | None) -> None:
    from Foundation import NSURL

    if not url:
        ev.setURL_(None)
        return
    nsurl = NSURL.URLWithString_(url)
    ev.setURL_(nsurl)


def _date_component_is_set(value: Any) -> bool:
    if value is None:
        return False
    try:
        from Foundation import NSDateComponentUndefined

        if value == NSDateComponentUndefined:
            return False
    except Exception:
        pass
    try:
        iv = int(value)
        if iv < 0 or iv > 10_000:
            return False
    except Exception:
        return False
    return True


def _reminder_due_from_components(comps: Any) -> date | datetime | None:
    if comps is None:
        return None
    try:
        y, m, d = comps.year(), comps.month(), comps.day()
        if not (
            _date_component_is_set(y)
            and _date_component_is_set(m)
            and _date_component_is_set(d)
        ):
            return None
        hour = comps.hour()
        minute = comps.minute()
        second = comps.second() if hasattr(comps, "second") else 0
        if _date_component_is_set(hour):
            local_tz = datetime.now().astimezone().tzinfo
            return datetime(
                int(y),
                int(m),
                int(d),
                int(hour),
                int(minute) if _date_component_is_set(minute) else 0,
                int(second) if _date_component_is_set(second) else 0,
                tzinfo=local_tz,
            )
        return date(int(y), int(m), int(d))
    except Exception:
        _LOGGER.debug("dueDateComponents parse failed", exc_info=True)
        return None


def _reminder_flagged(rem: Any) -> bool | None:
    """Best-effort; EventKit has no official flagged API."""
    for key in ("flagged", "isFlagged", "hasFlagged"):
        try:
            value = rem.valueForKey_(key)
            if value is None:
                continue
            return bool(value)
        except Exception:
            continue
    return None


def _set_reminder_flagged(rem: Any, flagged: bool | None) -> None:
    if flagged is None:
        return
    for key in ("flagged", "isFlagged"):
        try:
            rem.setValue_forKey_(bool(flagged), key)
            return
        except Exception:
            continue


def _reminder_tags(rem: Any) -> list[str] | None:
    """Best-effort; tags are not part of the public EventKit reminder API."""
    for key in ("tags", "tagNames"):
        try:
            value = rem.valueForKey_(key)
        except Exception:
            continue
        if value is None:
            continue
        tags: list[str] = []
        try:
            for item in list(value):
                if item is None:
                    continue
                if hasattr(item, "name"):
                    name = str(item.name() or "").strip()
                else:
                    name = str(item).strip()
                if name:
                    tags.append(name)
        except Exception:
            text = str(value).strip()
            if text:
                tags = [text]
        return tags or None
    return None


def _todo_from_reminder(rem: Any, list_id: str) -> TodoItem:
    uid = str(
        rem.calendarItemIdentifier()
        or rem.calendarItemExternalIdentifier()
        or ""
    )
    summary = str(rem.title() or "")
    description = str(rem.notes()) if rem.notes() else None
    completed = bool(rem.isCompleted())
    status = TodoItemStatus.COMPLETED if completed else TodoItemStatus.NEEDS_ACTION
    due = None
    try:
        due = _reminder_due_from_components(rem.dueDateComponents())
    except Exception:
        due = None
    priority = int(rem.priority()) if rem.priority() else None
    if priority == 0:
        priority = None
    location = _eventkit_location_string(rem)
    url = _eventkit_url_string(rem)
    flagged = _reminder_flagged(rem)
    tags = _reminder_tags(rem)
    completed_at = None
    try:
        if rem.completionDate():
            completed_at = _nsdate_to_dt(rem.completionDate())
    except Exception:
        completed_at = None
    lm = _nsdate_to_dt(rem.lastModifiedDate()) if rem.lastModifiedDate() else None
    ch = content_hash_for(
        uid,
        summary,
        description,
        status.value,
        due,
        priority,
        location,
        url,
        flagged,
        tags,
        completed_at,
    )
    return TodoItem(
        uid=uid,
        list_id=list_id,
        summary=summary,
        status=status,
        description=description,
        due=due,
        priority=priority,
        location=location,
        url=url,
        flagged=flagged,
        tags=tags,
        completed_at=completed_at,
        content_hash=ch,
        last_modified=lm,
    )


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
        # Prefer resetting only this app. If that fails, tccutil falls back to a
        # SERVICE-WIDE reset (all apps lose Calendar/Reminders grants) — dangerous.
        bundle_id = "app.iHadAThought.appleHAsync"
        services = []
        if which in ("both", "calendar", "calendars"):
            services.append("Calendar")
        if which in ("both", "reminders"):
            services.append("Reminders")
        for svc in services:
            r = subprocess.run(
                ["tccutil", "reset", svc, bundle_id],
                check=False,
                capture_output=True,
            )
            if r.returncode != 0:
                _LOGGER.warning(
                    "Bundle TCC reset failed for %s; falling back to service-wide reset",
                    svc,
                )
                subprocess.run(["tccutil", "reset", svc], check=False)

    def list_calendars(self, *, shared_only: bool = False) -> list[CalendarSource]:
        EK = self._EK
        out: list[CalendarSource] = []
        for cal in self._store.calendarsForEntityType_(EK.EKEntityTypeEvent) or []:
            cid = str(cal.calendarIdentifier())
            title = str(cal.title() or "")
            source = cal.source()
            source_name = str(source.title()) if source else None
            out.append(
                CalendarSource(
                    id=cid,
                    title=title,
                    color=_calendar_color_hex(cal),
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
                    color=_calendar_color_hex(cal),
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
            location = _eventkit_location_string(ev)
            url = _eventkit_url_string(ev)
            all_day = bool(ev.isAllDay())
            start_dt = _nsdate_to_dt(ev.startDate())
            end_dt = _nsdate_to_dt(ev.endDate())
            if start_dt is None or end_dt is None:
                continue
            start_val: datetime | date = start_dt.date() if all_day else start_dt
            end_val: datetime | date = end_dt.date() if all_day else end_dt
            lm = _nsdate_to_dt(ev.lastModifiedDate()) if ev.lastModifiedDate() else None
            ch = content_hash_for(
                uid,
                summary,
                description,
                location,
                url,
                start_val,
                end_val,
                all_day,
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
                    url=url,
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
            result.append(_todo_from_reminder(rem, list_id))
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
        if event.url is not None:
            _set_eventkit_url(ev, event.url)
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
        if "url" in patch._fields_set:
            _set_eventkit_url(ev, patch.url)
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
        if item.location is not None:
            rem.setLocation_(item.location)
        if item.url is not None:
            _set_eventkit_url(rem, item.url)
        if item.flagged is not None:
            _set_reminder_flagged(rem, item.flagged)
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
            rem.setNotes_(patch.description if patch.description is not None else "")
        if "status" in patch._fields_set and patch.status is not None:
            rem.setCompleted_(patch.status == TodoItemStatus.COMPLETED)
        if "priority" in patch._fields_set:
            rem.setPriority_(int(patch.priority or 0))
        if "due" in patch._fields_set:
            if patch.due is None:
                rem.setDueDateComponents_(None)
            else:
                self._set_reminder_due(rem, patch.due)
        if "location" in patch._fields_set:
            rem.setLocation_(patch.location if patch.location is not None else "")
        if "url" in patch._fields_set:
            _set_eventkit_url(rem, patch.url)
        if "flagged" in patch._fields_set:
            _set_reminder_flagged(rem, bool(patch.flagged) if patch.flagged is not None else False)
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
        location = _eventkit_location_string(ev)
        url = _eventkit_url_string(ev)
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
            url=url,
            all_day=all_day,
            content_hash=content_hash_for(
                uid,
                summary,
                description,
                location,
                url,
                start_val,
                end_val,
                all_day,
            ),
        )

    def _todo_from_ek(self, rem: Any, list_id: str) -> TodoItem:
        return _todo_from_reminder(rem, list_id)
