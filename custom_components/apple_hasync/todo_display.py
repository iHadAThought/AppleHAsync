"""Compose / parse reminder fields for Home Assistant TodoItem.description.

HA TodoItem only supports summary, status, due, description, and completed.
Extra Mac fields (priority, flag, location, url, tags) are folded into
description with a machine-readable footer so round-trips stay stable.
"""

from __future__ import annotations

from typing import Any

META_MARKER = "---applehasync---"

_PRIORITY_LABEL = {1: "high", 5: "medium", 9: "low"}
_PRIORITY_VALUE = {"high": 1, "medium": 5, "low": 9, "urgent": 1}


def priority_label(priority: int | None) -> str | None:
    if not priority:
        return None
    if priority <= 4:
        return "high"
    if priority == 5:
        return "medium"
    if priority >= 6:
        return "low"
    return None


def compose_todo_description(
    *,
    notes: str | None = None,
    priority: int | None = None,
    flagged: bool | None = None,
    location: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
) -> str | None:
    """Build HA-visible description from Mac reminder fields."""
    notes_text = (notes or "").strip() or None
    lines: list[str] = []
    label = priority_label(priority)
    if label:
        # "urgent" is Apple's high priority in practice
        pretty = "Urgent / High" if label == "high" else label.capitalize()
        lines.append(f"Priority: {pretty}")
    if flagged:
        lines.append("Flagged: yes")
    if location and str(location).strip():
        lines.append(f"Location: {str(location).strip()}")
    if url and str(url).strip():
        lines.append(f"URL: {str(url).strip()}")
    if tags:
        cleaned = [t.strip() for t in tags if t and str(t).strip()]
        if cleaned:
            lines.append("Tags: " + ", ".join(cleaned))

    if not notes_text and not lines:
        return None
    if not lines:
        return notes_text

    meta_lines = [META_MARKER, *lines]
    if notes_text:
        return notes_text + "\n\n" + "\n".join(meta_lines)
    return "\n".join(meta_lines)


def parse_todo_description(description: str | None) -> dict[str, Any]:
    """Split HA description into notes + structured meta for EventKit writes."""
    result: dict[str, Any] = {
        "notes": None,
        "priority": None,
        "flagged": None,
        "location": None,
        "url": None,
        "tags": None,
    }
    if not isinstance(description, str) or not description.strip():
        return result

    text = description.strip()
    if META_MARKER in text:
        notes_part, _, meta_part = text.partition(META_MARKER)
        result["notes"] = notes_part.strip() or None
        for raw in meta_part.splitlines():
            line = raw.strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "priority":
                token = value.lower().replace("urgent / high", "high").split("/")[0].strip()
                # Accept "Urgent / High", "High", "high"
                for name, num in _PRIORITY_VALUE.items():
                    if name in token:
                        result["priority"] = num
                        break
            elif key == "flagged":
                result["flagged"] = value.lower() in {"yes", "true", "1", "flagged"}
            elif key == "location":
                result["location"] = value or None
            elif key == "url":
                result["url"] = value or None
            elif key == "tags":
                tags = [t.strip() for t in value.split(",") if t.strip()]
                result["tags"] = tags or None
        return result

    # No marker: treat whole text as notes (legacy)
    result["notes"] = text
    return result
