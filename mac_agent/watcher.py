"""Watch EventKit store changes and config file updates; notify HA instances."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

_LOGGER = logging.getLogger(__name__)

NotifyFn = Callable[[str, Optional[dict[str, Any]]], Awaitable[Any]]


class ChangeWatcher:
    """Debounced EventKit + config.yaml watcher."""

    def __init__(
        self,
        *,
        config_path: Path,
        reload_config: Callable[[], None],
        notify: NotifyFn,
        debounce_seconds: float = 1.5,
        eventkit_backend: Any | None = None,
    ) -> None:
        self._config_path = config_path
        self._reload_config = reload_config
        self._notify = notify
        self._debounce = debounce_seconds
        self._backend = eventkit_backend
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._pending_reason: str | None = None
        self._pending_details: dict[str, Any] | None = None
        self._flush_handle: asyncio.TimerHandle | None = None
        self._config_mtime: float | None = None
        self._observer: Any = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        if self._config_path.exists():
            self._config_mtime = self._config_path.stat().st_mtime
        t = threading.Thread(target=self._config_poll_loop, name="applehasync-config", daemon=True)
        t.start()
        self._threads.append(t)
        if self._backend is not None:
            try:
                self._start_eventkit_observer()
            except Exception as exc:
                _LOGGER.warning("EventKit change observer unavailable: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                from Foundation import NSNotificationCenter

                NSNotificationCenter.defaultCenter().removeObserver_(self._observer)
            except Exception:
                pass
            self._observer = None
        if self._flush_handle and self._loop:
            self._flush_handle.cancel()

    def _schedule_notify(self, reason: str, details: dict[str, Any] | None = None) -> None:
        if self._loop is None:
            return
        self._pending_reason = reason
        self._pending_details = details

        def _arm() -> None:
            if self._flush_handle:
                self._flush_handle.cancel()
            self._flush_handle = self._loop.call_later(  # type: ignore[union-attr]
                self._debounce, lambda: asyncio.create_task(self._flush())
            )

        self._loop.call_soon_threadsafe(_arm)

    async def _flush(self) -> None:
        reason = self._pending_reason or "change"
        details = self._pending_details
        self._pending_reason = None
        self._pending_details = None
        try:
            await self._notify(reason, details)
        except Exception as exc:
            _LOGGER.warning("Notify after %s failed: %s", reason, exc)

    def _config_poll_loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                if not self._config_path.exists():
                    continue
                mtime = self._config_path.stat().st_mtime
                if self._config_mtime is None:
                    self._config_mtime = mtime
                    continue
                if mtime != self._config_mtime:
                    self._config_mtime = mtime
                    try:
                        self._reload_config()
                        _LOGGER.info("Reloaded config from disk")
                    except Exception as exc:
                        _LOGGER.error("Config reload failed: %s", exc)
                    self._schedule_notify("config_reloaded")
            except Exception as exc:
                _LOGGER.debug("Config poll error: %s", exc)

    def _start_eventkit_observer(self) -> None:
        """Observe EKEventStoreChangedNotification on a background runloop thread."""
        import EventKit  # type: ignore
        from Foundation import NSDate, NSNotificationCenter, NSRunLoop

        watcher = self

        def _on_change(_notification) -> None:
            watcher._schedule_notify("eventkit_changed")

        self._observer = (
            NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                EventKit.EKEventStoreChangedNotification,
                self._backend._store,
                None,
                _on_change,
            )
        )

        def _runloop() -> None:
            while not self._stop.is_set():
                NSRunLoop.currentRunLoop().runUntilDate_(
                    NSDate.dateWithTimeIntervalSinceNow_(0.5)
                )

        t = threading.Thread(target=_runloop, name="applehasync-ek-runloop", daemon=True)
        t.start()
        self._threads.append(t)
        _LOGGER.info("EventKit change observer started")
