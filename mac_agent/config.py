"""Paths, config, and secrets for the Mac agent."""

from __future__ import annotations

import os
import secrets
import stat
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

APP_NAME = "appleHAsync"
DEFAULT_PORT = 8745

# Per-source detail fields the user can toggle in the Web UI.
CALENDAR_SYNC_FIELDS = ("notes", "location", "url")
REMINDER_SYNC_FIELDS = (
    "notes",
    "due",
    "priority",
    "flagged",
    "location",
    "url",
    "tags",
)


def default_calendar_sync_fields() -> dict[str, bool]:
    return {k: True for k in CALENDAR_SYNC_FIELDS}


def default_reminder_sync_fields() -> dict[str, bool]:
    return {k: True for k in REMINDER_SYNC_FIELDS}


def normalize_calendar_sync_fields(raw: Any) -> dict[str, bool]:
    base = default_calendar_sync_fields()
    if not isinstance(raw, dict):
        return base
    for key in CALENDAR_SYNC_FIELDS:
        if key in raw:
            base[key] = bool(raw[key])
    return base


def normalize_reminder_sync_fields(raw: Any) -> dict[str, bool]:
    base = default_reminder_sync_fields()
    if not isinstance(raw, dict):
        return base
    for key in REMINDER_SYNC_FIELDS:
        if key in raw:
            base[key] = bool(raw[key])
    return base


def default_data_dir() -> Path:
    override = os.environ.get("APPLE_HASYNC_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_NAME


@dataclass
class HomeAssistantTarget:
    id: str
    name: str
    base_url: str
    token: str = ""
    webhook_id: str = ""
    webhook_secret: str = ""
    verify_tls: bool = True
    ca_path: str | None = None
    enabled: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "webhook_id": self.webhook_id,
            "verify_tls": self.verify_tls,
            "ca_path": self.ca_path,
            "enabled": self.enabled,
            "has_token": bool(self.token),
            "has_webhook_secret": bool(self.webhook_secret),
        }

    def to_storage(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HomeAssistantTarget:
        return cls(
            id=data.get("id") or str(uuid.uuid4()),
            name=data["name"],
            base_url=data["base_url"].rstrip("/"),
            token=data.get("token") or "",
            webhook_id=data.get("webhook_id") or "",
            webhook_secret=data.get("webhook_secret") or "",
            verify_tls=bool(data.get("verify_tls", True)),
            ca_path=data.get("ca_path"),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class AgentConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = DEFAULT_PORT
    allow_insecure_http: bool = False
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    allowed_source_ips: list[str] = field(default_factory=list)
    shared_calendars: list[str] = field(default_factory=list)
    shared_reminder_lists: list[str] = field(default_factory=list)
    # Fail-closed: Focus Mode is not shared until explicitly enabled.
    share_focus: bool = False
    # Per-source field allowlists (id -> {field: bool}); missing id = all enabled.
    calendar_sync_fields: dict[str, dict[str, bool]] = field(default_factory=dict)
    reminder_sync_fields: dict[str, dict[str, bool]] = field(default_factory=dict)
    home_assistants: list[HomeAssistantTarget] = field(default_factory=list)
    calendar_titles: dict[str, str] = field(default_factory=dict)
    reminder_titles: dict[str, str] = field(default_factory=dict)
    agent_token: str = ""
    echo_suppress_seconds: float = 8.0
    setup_completed: bool = False

    def is_calendar_shared(self, calendar_id: str) -> bool:
        return calendar_id in self.shared_calendars

    def is_list_shared(self, list_id: str) -> bool:
        return list_id in self.shared_reminder_lists

    def is_focus_shared(self) -> bool:
        return bool(self.share_focus)

    def calendar_fields(self, calendar_id: str) -> dict[str, bool]:
        return normalize_calendar_sync_fields(
            self.calendar_sync_fields.get(calendar_id)
        )

    def reminder_fields(self, list_id: str) -> dict[str, bool]:
        return normalize_reminder_sync_fields(
            self.reminder_sync_fields.get(list_id)
        )

    def prune_missing_shares(
        self, calendar_ids: set[str], list_ids: set[str]
    ) -> tuple[list[str], list[str]]:
        """Drop share allowlist entries that no longer exist in EventKit."""
        stale_c = [i for i in self.shared_calendars if i not in calendar_ids]
        stale_l = [i for i in self.shared_reminder_lists if i not in list_ids]
        if stale_c:
            self.shared_calendars = [
                i for i in self.shared_calendars if i in calendar_ids
            ]
        if stale_l:
            self.shared_reminder_lists = [
                i for i in self.shared_reminder_lists if i in list_ids
            ]
        self.calendar_sync_fields = {
            k: v for k, v in self.calendar_sync_fields.items() if k in calendar_ids
        }
        self.reminder_sync_fields = {
            k: v for k, v in self.reminder_sync_fields.items() if k in list_ids
        }
        self.calendar_titles = {
            k: v for k, v in self.calendar_titles.items() if k in calendar_ids
        }
        self.reminder_titles = {
            k: v for k, v in self.reminder_titles.items() if k in list_ids
        }
        return stale_c, stale_l


class ConfigStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.config_path = self.data_dir / "config.yaml"
        self.secrets_path = self.data_dir / "secrets.yaml"
        self.sqlite_path = self.data_dir / "sync_meta.sqlite3"
        self.certs_dir = self.data_dir / "certs"
        self._config = AgentConfig()
        self.ensure_dirs()
        self.load()

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, stat.S_IRWXU)  # 0700
        self.certs_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.certs_dir, stat.S_IRWXU)

    @property
    def config(self) -> AgentConfig:
        return self._config

    def load(self) -> AgentConfig:
        raw: dict[str, Any] = {}
        if self.config_path.exists():
            with self.config_path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        secrets_raw: dict[str, Any] = {}
        if self.secrets_path.exists():
            with self.secrets_path.open("r", encoding="utf-8") as fh:
                secrets_raw = yaml.safe_load(fh) or {}

        agent_token = secrets_raw.get("agent_token") or raw.get("agent_token") or ""
        if not agent_token:
            agent_token = secrets.token_urlsafe(32)

        ha_list = []
        for item in raw.get("home_assistants") or []:
            # Merge secrets by id
            sid = item.get("id")
            secret_entry = (secrets_raw.get("home_assistants") or {}).get(sid or "", {})
            merged = {**item, **secret_entry}
            ha_list.append(HomeAssistantTarget.from_dict(merged))

        cal_fields_raw = raw.get("calendar_sync_fields") or {}
        rem_fields_raw = raw.get("reminder_sync_fields") or {}
        calendar_sync_fields = {
            str(k): normalize_calendar_sync_fields(v)
            for k, v in cal_fields_raw.items()
            if isinstance(v, dict)
        }
        reminder_sync_fields = {
            str(k): normalize_reminder_sync_fields(v)
            for k, v in rem_fields_raw.items()
            if isinstance(v, dict)
        }

        self._config = AgentConfig(
            listen_host=raw.get("listen_host", "127.0.0.1"),
            listen_port=int(raw.get("listen_port", DEFAULT_PORT)),
            allow_insecure_http=bool(raw.get("allow_insecure_http", False)),
            tls_cert_file=raw.get("tls_cert_file"),
            tls_key_file=raw.get("tls_key_file"),
            allowed_source_ips=list(raw.get("allowed_source_ips") or []),
            shared_calendars=list(raw.get("shared_calendars") or []),
            shared_reminder_lists=list(raw.get("shared_reminder_lists") or []),
            share_focus=bool(raw.get("share_focus", False)),
            calendar_sync_fields=calendar_sync_fields,
            reminder_sync_fields=reminder_sync_fields,
            home_assistants=ha_list,
            calendar_titles=dict(raw.get("calendar_titles") or {}),
            reminder_titles=dict(raw.get("reminder_titles") or {}),
            agent_token=agent_token,
            echo_suppress_seconds=float(raw.get("echo_suppress_seconds", 8.0)),
            setup_completed=bool(raw.get("setup_completed", False)),
        )
        # Persist generated token if missing
        if not self.secrets_path.exists() or not secrets_raw.get("agent_token"):
            self.save()
        return self._config

    def save(self) -> None:
        self.ensure_dirs()
        public = {
            "listen_host": self._config.listen_host,
            "listen_port": self._config.listen_port,
            "allow_insecure_http": self._config.allow_insecure_http,
            "tls_cert_file": self._config.tls_cert_file,
            "tls_key_file": self._config.tls_key_file,
            "allowed_source_ips": self._config.allowed_source_ips,
            "shared_calendars": self._config.shared_calendars,
            "shared_reminder_lists": self._config.shared_reminder_lists,
            "share_focus": bool(self._config.share_focus),
            "calendar_sync_fields": self._config.calendar_sync_fields,
            "reminder_sync_fields": self._config.reminder_sync_fields,
            "calendar_titles": self._config.calendar_titles,
            "reminder_titles": self._config.reminder_titles,
            "echo_suppress_seconds": self._config.echo_suppress_seconds,
            "setup_completed": self._config.setup_completed,
            "home_assistants": [
                {
                    "id": ha.id,
                    "name": ha.name,
                    "base_url": ha.base_url,
                    "webhook_id": ha.webhook_id,
                    "verify_tls": ha.verify_tls,
                    "ca_path": ha.ca_path,
                    "enabled": ha.enabled,
                }
                for ha in self._config.home_assistants
            ],
        }
        secrets_data = {
            "agent_token": self._config.agent_token,
            "home_assistants": {
                ha.id: {
                    "token": ha.token,
                    "webhook_secret": ha.webhook_secret,
                }
                for ha in self._config.home_assistants
            },
        }
        with self.config_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(public, fh, sort_keys=False)
        with self.secrets_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(secrets_data, fh, sort_keys=False)
        os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)
        os.chmod(self.secrets_path, stat.S_IRUSR | stat.S_IWUSR)

    def reload(self) -> AgentConfig:
        return self.load()

    def rotate_agent_token(self) -> str:
        self._config.agent_token = secrets.token_urlsafe(32)
        self.save()
        return self._config.agent_token

    def set_share(
        self,
        *,
        calendar_ids: list[str] | None = None,
        list_ids: list[str] | None = None,
        share_focus: bool | None = None,
        calendar_titles: dict[str, str] | None = None,
        reminder_titles: dict[str, str] | None = None,
        calendar_sync_fields: dict[str, dict[str, bool]] | None = None,
        reminder_sync_fields: dict[str, dict[str, bool]] | None = None,
    ) -> None:
        if calendar_ids is not None:
            self._config.shared_calendars = list(dict.fromkeys(calendar_ids))
        if list_ids is not None:
            self._config.shared_reminder_lists = list(dict.fromkeys(list_ids))
        if share_focus is not None:
            self._config.share_focus = bool(share_focus)
        if calendar_titles:
            self._config.calendar_titles.update(calendar_titles)
        if reminder_titles:
            self._config.reminder_titles.update(reminder_titles)
        if calendar_sync_fields is not None:
            merged = dict(self._config.calendar_sync_fields)
            for sid, fields in calendar_sync_fields.items():
                merged[str(sid)] = normalize_calendar_sync_fields(fields)
            # Drop configs for calendars that are no longer shared
            if calendar_ids is not None:
                shared = set(calendar_ids)
                merged = {k: v for k, v in merged.items() if k in shared}
            self._config.calendar_sync_fields = merged
        if reminder_sync_fields is not None:
            merged = dict(self._config.reminder_sync_fields)
            for sid, fields in reminder_sync_fields.items():
                merged[str(sid)] = normalize_reminder_sync_fields(fields)
            if list_ids is not None:
                shared = set(list_ids)
                merged = {k: v for k, v in merged.items() if k in shared}
            self._config.reminder_sync_fields = merged
        self.save()

    def enable_calendar(self, calendar_id: str, title: str | None = None) -> None:
        if calendar_id not in self._config.shared_calendars:
            self._config.shared_calendars.append(calendar_id)
        if title:
            self._config.calendar_titles[calendar_id] = title
        self.save()

    def disable_calendar(self, calendar_id: str) -> None:
        self._config.shared_calendars = [
            c for c in self._config.shared_calendars if c != calendar_id
        ]
        self.save()

    def enable_list(self, list_id: str, title: str | None = None) -> None:
        if list_id not in self._config.shared_reminder_lists:
            self._config.shared_reminder_lists.append(list_id)
        if title:
            self._config.reminder_titles[list_id] = title
        self.save()

    def disable_list(self, list_id: str) -> None:
        self._config.shared_reminder_lists = [
            c for c in self._config.shared_reminder_lists if c != list_id
        ]
        self.save()

    def set_share_focus(self, enabled: bool) -> None:
        self._config.share_focus = bool(enabled)
        self.save()

    def upsert_ha(self, target: HomeAssistantTarget) -> HomeAssistantTarget:
        existing = {h.id: i for i, h in enumerate(self._config.home_assistants)}
        if target.id in existing:
            self._config.home_assistants[existing[target.id]] = target
        else:
            self._config.home_assistants.append(target)
        self.save()
        return target

    def remove_ha(self, key: str) -> bool:
        before = len(self._config.home_assistants)
        self._config.home_assistants = [
            h
            for h in self._config.home_assistants
            if h.id != key and h.name.lower() != key.lower()
        ]
        changed = len(self._config.home_assistants) != before
        if changed:
            self.save()
        return changed

    def find_ha(self, key: str) -> HomeAssistantTarget | None:
        for h in self._config.home_assistants:
            if h.id == key or h.name.lower() == key.lower():
                return h
        return None
