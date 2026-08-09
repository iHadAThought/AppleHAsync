"""Client for the Mac appleHAsync agent."""

from __future__ import annotations

import hashlib
import logging
import ssl
from datetime import date, datetime
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ALLOW_INSECURE_HTTP, CONF_CA_PATH, CONF_CERT_PIN, CONF_VERIFY_TLS

_LOGGER = logging.getLogger(__name__)


class AppleHASyncAuthError(Exception):
    """Authentication failed."""


class AppleHASyncPermissionError(Exception):
    """Mac TCC permission missing."""

    def __init__(self, detail: Any) -> None:
        super().__init__(str(detail))
        self.detail = detail


class AppleHASyncClient:
    """HTTPS client talking to the Mac agent."""

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: str,
        token: str,
        *,
        verify_tls: bool = True,
        ca_path: str | None = None,
        cert_pin: str | None = None,
        allow_insecure_http: bool = False,
    ) -> None:
        self.hass = hass
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verify_tls = verify_tls
        self.ca_path = ca_path
        self.cert_pin = cert_pin
        self.allow_insecure_http = allow_insecure_http
        if self.base_url.startswith("http://") and not allow_insecure_http:
            raise ValueError("HTTPS required unless allow_insecure_http is enabled")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _ssl_param(self) -> bool | ssl.SSLContext:
        if self.base_url.startswith("http://"):
            return False
        if not self.verify_tls and not self.cert_pin:
            return False
        if self.ca_path:
            ctx = ssl.create_default_context(cafile=self.ca_path)
            return ctx
        return True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        session = async_get_clientsession(self.hass)
        url = f"{self.base_url}{path}"
        try:
            async with session.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
                ssl=self._ssl_param(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if self.cert_pin and self.base_url.startswith("https://"):
                    # Best-effort pin check when transport exposes peer cert
                    try:
                        sslobj = resp.connection.transport.get_extra_info("ssl_object")  # type: ignore[union-attr]
                        if sslobj is not None:
                            der = sslobj.getpeercert(binary_form=True)
                            digest = hashlib.sha256(der).hexdigest()
                            if digest.lower() != self.cert_pin.lower():
                                raise AppleHASyncAuthError("certificate_pin_mismatch")
                    except AppleHASyncAuthError:
                        raise
                    except Exception:
                        _LOGGER.debug("Cert pin check skipped (no peer cert available)")

                if resp.status == 401:
                    raise AppleHASyncAuthError("invalid_token")
                if resp.status == 403:
                    detail = await resp.json(content_type=None)
                    raise AppleHASyncPermissionError(detail)
                if resp.status == 404:
                    raise KeyError(path)
                resp.raise_for_status()
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise ConnectionError(str(err)) from err

    async def health(self) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        async with session.get(
            f"{self.base_url}/health",
            ssl=self._ssl_param(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def list_calendars(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v1/calendars")
        return data.get("calendars", [])

    async def list_reminder_lists(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v1/reminder-lists")
        return data.get("reminder_lists", [])

    async def get_events(
        self, calendar_id: str, start: datetime | date, end: datetime | date
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            f"/v1/calendars/{calendar_id}/events",
            params={"start": _iso(start), "end": _iso(end)},
        )
        return data.get("events", [])

    async def get_items(self, list_id: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/v1/lists/{list_id}/items")
        return data.get("items", [])

    async def create_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/v1/calendars/{calendar_id}/events", json_body=body
        )

    async def patch_event(
        self, calendar_id: str, uid: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"/v1/calendars/{calendar_id}/events/{uid}", json_body=patch
        )

    async def delete_event(self, calendar_id: str, uid: str) -> None:
        await self._request("DELETE", f"/v1/calendars/{calendar_id}/events/{uid}")

    async def create_item(self, list_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/v1/lists/{list_id}/items", json_body=body
        )

    async def patch_item(
        self, list_id: str, uid: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"/v1/lists/{list_id}/items/{uid}", json_body=patch
        )

    async def delete_item(self, list_id: str, uid: str) -> None:
        await self._request("DELETE", f"/v1/lists/{list_id}/items/{uid}")


def _iso(value: datetime | date) -> str:
    return value.isoformat()


def client_from_entry_data(hass: HomeAssistant, data: dict[str, Any]) -> AppleHASyncClient:
    return AppleHASyncClient(
        hass,
        data["agent_url"],
        data["agent_token"],
        verify_tls=data.get(CONF_VERIFY_TLS, True),
        ca_path=data.get(CONF_CA_PATH),
        cert_pin=data.get(CONF_CERT_PIN),
        allow_insecure_http=data.get(CONF_ALLOW_INSECURE_HTTP, False),
    )
