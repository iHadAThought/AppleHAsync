"""Notify all registered Home Assistant instances."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .config import AgentConfig, HomeAssistantTarget
from .security import post_ha_webhook

_LOGGER = logging.getLogger(__name__)


class HaNotifier:
    def __init__(self, get_config) -> None:
        self._get_config = get_config
        self.last_results: list[dict[str, Any]] = []

    async def notify_refresh(self, reason: str, details: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cfg: AgentConfig = self._get_config()
        body = json.dumps(
            {
                "type": "apple_hasync_refresh",
                "reason": reason,
                "details": details or {},
                "ts": time.time(),
            }
        ).encode("utf-8")
        results: list[dict[str, Any]] = []
        for ha in cfg.home_assistants:
            if not ha.enabled:
                continue
            result = await self._notify_one(cfg, ha, body)
            results.append({"id": ha.id, "name": ha.name, **result})
        self.last_results = results
        return results

    async def _notify_one(
        self, cfg: AgentConfig, ha: HomeAssistantTarget, body: bytes
    ) -> dict[str, Any]:
        return await post_ha_webhook(
            base_url=ha.base_url,
            webhook_id=ha.webhook_id,
            webhook_secret=ha.webhook_secret,
            token=ha.token,
            payload=body,
            verify_tls=ha.verify_tls,
            ca_path=ha.ca_path,
            allow_insecure_http=cfg.allow_insecure_http,
        )
