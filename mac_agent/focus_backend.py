"""Read macOS Focus mode from DoNotDisturb DB (requires Full Disk Access)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.models import FocusModeInfo, FocusStatus

_LOGGER = logging.getLogger(__name__)

_DND_DIR = Path.home() / "Library" / "DoNotDisturb" / "DB"
_ASSERTIONS = _DND_DIR / "Assertions.json"
_MODES = _DND_DIR / "ModeConfigurations.json"


def dnd_db_dir() -> Path:
    return _DND_DIR


def dnd_watch_paths() -> list[Path]:
    return [_ASSERTIONS, _MODES]


def focus_permission_status() -> str:
    """Return ok | denied | unavailable based on readability of Focus DB files."""
    if not _DND_DIR.exists():
        return "unavailable"
    try:
        # ModeConfigurations is enough to list modes; Assertions may be empty.
        if _MODES.is_file():
            _MODES.read_bytes()
            return "ok"
        if _ASSERTIONS.is_file():
            _ASSERTIONS.read_bytes()
            return "ok"
        return "unavailable"
    except PermissionError:
        return "denied"
    except OSError:
        return "unavailable"


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except PermissionError:
        raise
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        _LOGGER.debug("Focus DB parse failed for %s: %s", path.name, exc)
        return None


def _mode_configurations(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            modes = first.get("modeConfigurations")
            if isinstance(modes, dict):
                return modes
    modes = raw.get("modeConfigurations")
    return modes if isinstance(modes, dict) else {}


def _assertion_mode_id(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    data = raw.get("data")
    records = None
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            records = first.get("storeAssertionRecords")
    if records is None:
        records = raw.get("storeAssertionRecords")
    if not isinstance(records, list) or not records:
        return None
    first = records[0]
    if not isinstance(first, dict):
        return None
    details = first.get("assertionDetails")
    if not isinstance(details, dict):
        return None
    mode_id = details.get("assertionDetailsModeIdentifier")
    return str(mode_id) if mode_id else None


def _mode_name(modes: dict[str, Any], mode_id: str) -> str | None:
    entry = modes.get(mode_id)
    if not isinstance(entry, dict):
        return None
    mode = entry.get("mode")
    if isinstance(mode, dict) and mode.get("name"):
        return str(mode["name"])
    return None


def _available_modes(modes: dict[str, Any]) -> list[FocusModeInfo]:
    out: list[FocusModeInfo] = []
    for mode_id, entry in modes.items():
        if not isinstance(entry, dict):
            continue
        mode = entry.get("mode")
        name = None
        if isinstance(mode, dict):
            name = mode.get("name")
        out.append(
            FocusModeInfo(id=str(mode_id), name=str(name or mode_id))
        )
    out.sort(key=lambda m: m.name.lower())
    return out


def _scheduled_mode_id(modes: dict[str, Any], now: datetime | None = None) -> str | None:
    """Best-effort: pick a mode with an enabled time-period trigger covering now."""
    now = now or datetime.now().astimezone()
    minutes = now.hour * 60 + now.minute
    for mode_id, entry in modes.items():
        if not isinstance(entry, dict):
            continue
        triggers_wrap = entry.get("triggers")
        if not isinstance(triggers_wrap, dict):
            continue
        triggers = triggers_wrap.get("triggers")
        if not isinstance(triggers, list):
            continue
        for trig in triggers:
            if not isinstance(trig, dict):
                continue
            # enabledSetting == 2 means schedule enabled (community convention)
            if trig.get("enabledSetting") != 2:
                continue
            try:
                start = int(trig["timePeriodStartTimeHour"]) * 60 + int(
                    trig["timePeriodStartTimeMinute"]
                )
                end = int(trig["timePeriodEndTimeHour"]) * 60 + int(
                    trig["timePeriodEndTimeMinute"]
                )
            except (KeyError, TypeError, ValueError):
                continue
            if start < end:
                if start <= minutes < end:
                    return str(mode_id)
            elif start > end:  # wraps midnight
                if minutes >= start or minutes < end:
                    return str(mode_id)
    return None


def read_focus_status(*, shared: bool = False) -> FocusStatus:
    """Read current Focus status. Never raises for FDA/missing files."""
    perm = focus_permission_status()
    if perm != "ok":
        return FocusStatus(
            active=False,
            permission=perm,
            shared=shared,
        )
    try:
        modes_raw = _load_json(_MODES) if _MODES.is_file() else None
        assert_raw = _load_json(_ASSERTIONS) if _ASSERTIONS.is_file() else None
    except PermissionError:
        return FocusStatus(active=False, permission="denied", shared=shared)

    modes = _mode_configurations(modes_raw)
    available = _available_modes(modes)
    mode_id = _assertion_mode_id(assert_raw)
    if not mode_id:
        mode_id = _scheduled_mode_id(modes)
    if not mode_id:
        return FocusStatus(
            active=False,
            available_modes=available,
            permission="ok",
            shared=shared,
        )
    name = _mode_name(modes, mode_id) or mode_id
    return FocusStatus(
        active=True,
        mode_id=mode_id,
        mode_name=name,
        available_modes=available,
        permission="ok",
        shared=shared,
    )


def open_full_disk_access_settings() -> None:
    import subprocess

    subprocess.run(
        [
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        ],
        check=False,
    )
