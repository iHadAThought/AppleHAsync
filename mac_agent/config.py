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
    home_assistants: list[HomeAssistantTarget] = field(default_factory=list)
    calendar_titles: dict[str, str] = field(default_factory=dict)
    reminder_titles: dict[str, str] = field(default_factory=dict)
    agent_token: str = ""
    echo_suppress_seconds: float = 8.0

    def is_calendar_shared(self, calendar_id: str) -> bool:
        return calendar_id in self.shared_calendars

    def is_list_shared(self, list_id: str) -> bool:
        return list_id in self.shared_reminder_lists

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

        self._config = AgentConfig(
            listen_host=raw.get("listen_host", "127.0.0.1"),
            listen_port=int(raw.get("listen_port", DEFAULT_PORT)),
            allow_insecure_http=bool(raw.get("allow_insecure_http", False)),
            tls_cert_file=raw.get("tls_cert_file"),
            tls_key_file=raw.get("tls_key_file"),
            allowed_source_ips=list(raw.get("allowed_source_ips") or []),
            shared_calendars=list(raw.get("shared_calendars") or []),
            shared_reminder_lists=list(raw.get("shared_reminder_lists") or []),
            home_assistants=ha_list,
            calendar_titles=dict(raw.get("calendar_titles") or {}),
            reminder_titles=dict(raw.get("reminder_titles") or {}),
            agent_token=agent_token,
            echo_suppress_seconds=float(raw.get("echo_suppress_seconds", 8.0)),
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
            "calendar_titles": self._config.calendar_titles,
            "reminder_titles": self._config.reminder_titles,
            "echo_suppress_seconds": self._config.echo_suppress_seconds,
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
        calendar_titles: dict[str, str] | None = None,
        reminder_titles: dict[str, str] | None = None,
    ) -> None:
        if calendar_ids is not None:
            self._config.shared_calendars = list(dict.fromkeys(calendar_ids))
        if list_ids is not None:
            self._config.shared_reminder_lists = list(dict.fromkeys(list_ids))
        if calendar_titles:
            self._config.calendar_titles.update(calendar_titles)
        if reminder_titles:
            self._config.reminder_titles.update(reminder_titles)
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
