"""FastAPI application for the Mac companion agent."""

from __future__ import annotations

import logging
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shared.models import Event, EventPatch, TodoItem, TodoItemPatch, TodoItemStatus

from .config import ConfigStore, HomeAssistantTarget
from .ha_notify import HaNotifier
from .security import client_ip_allowed, generate_self_signed_cert, test_ha_connection
from .sync_meta import SyncMetaStore
from .watcher import ChangeWatcher

_LOGGER = logging.getLogger(__name__)

store: ConfigStore | None = None
backend: Any = None
meta: SyncMetaStore | None = None
notifier: HaNotifier | None = None
watcher: ChangeWatcher | None = None
_auth_failures: dict[str, list[float]] = defaultdict(list)
_mutate_hits: dict[str, list[float]] = defaultdict(list)


def _rate_limit_auth(client_ip: str | None) -> None:
    if not client_ip:
        return
    now = time.time()
    window = [t for t in _auth_failures[client_ip] if now - t < 60]
    _auth_failures[client_ip] = window
    if len(window) >= 20:
        raise HTTPException(status_code=429, detail="too_many_auth_failures")


def _record_auth_failure(client_ip: str | None) -> None:
    if client_ip:
        _auth_failures[client_ip].append(time.time())


def _rate_limit_mutate(client_ip: str | None) -> None:
    """Limit mutating requests per client (120/min)."""
    if not client_ip:
        return
    now = time.time()
    window = [t for t in _mutate_hits[client_ip] if now - t < 60]
    window.append(now)
    _mutate_hits[client_ip] = window
    if len(window) > 120:
        raise HTTPException(status_code=429, detail="too_many_mutations")


def get_store() -> ConfigStore:
    assert store is not None
    return store


class ShareUpdate(BaseModel):
    shared_calendars: list[str] | None = None
    shared_reminder_lists: list[str] | None = None
    calendar_titles: dict[str, str] | None = None
    reminder_titles: dict[str, str] | None = None


class ShareToggle(BaseModel):
    id: str
    shared: bool
    title: str | None = None
    kind: str = Field(description="calendar or reminder_list")


class PermissionsAction(BaseModel):
    action: str  # request | open_settings | reset
    which: str = "both"
    confirm_reset: bool = False


class HaCreate(BaseModel):
    name: str
    base_url: str
    token: str = ""
    webhook_id: str = ""
    webhook_secret: str = ""
    verify_tls: bool = True
    ca_path: str | None = None
    enabled: bool = True


class HaUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    token: str | None = None
    webhook_id: str | None = None
    webhook_secret: str | None = None
    verify_tls: bool | None = None
    ca_path: str | None = None
    enabled: bool | None = None


class EventCreateBody(BaseModel):
    summary: str
    start: str
    end: str
    description: str | None = None
    location: str | None = None
    all_day: bool = False


class TodoCreateBody(BaseModel):
    summary: str
    description: str | None = None
    status: str = "needs_action"
    due: str | None = None
    priority: int | None = None


def _parse_dt(value: str):
    if len(value) == 10:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    cfg = get_store().config
    client = request.client.host if request.client else None
    _rate_limit_auth(client)
    if not client_ip_allowed(client, cfg.allowed_source_ips):
        raise HTTPException(status_code=403, detail="source_ip_not_allowed")
    if not authorization or not authorization.lower().startswith("bearer "):
        _record_auth_failure(client)
        raise HTTPException(status_code=401, detail="missing_bearer_token")
    token = authorization.split(" ", 1)[1].strip()
    if token != cfg.agent_token:
        _record_auth_failure(client)
        raise HTTPException(status_code=401, detail="invalid_token")


def _ensure_backend():
    if backend is None:
        raise HTTPException(
            status_code=503,
            detail="eventkit_unavailable",
        )


def _require_calendar_perm():
    _ensure_backend()
    perms = backend.get_permissions()
    if not perms.calendar_full:
        raise HTTPException(
            status_code=403,
            detail={"error": "permission_required", "permissions": perms.to_dict()},
        )


def _require_reminders_perm():
    _ensure_backend()
    perms = backend.get_permissions()
    if not perms.reminders_full:
        raise HTTPException(
            status_code=403,
            detail={"error": "permission_required", "permissions": perms.to_dict()},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, backend, meta, notifier, watcher
    import asyncio

    store = ConfigStore()
    meta = SyncMetaStore(store.sqlite_path)
    notifier = HaNotifier(lambda: store.config)
    try:
        from .eventkit_backend import EventKitBackend

        backend = EventKitBackend()
    except Exception as exc:
        _LOGGER.error("EventKit backend unavailable: %s", exc)
        backend = None
    # Ensure TLS certs exist when not allowing insecure
    if not store.config.allow_insecure_http:
        cert, key = generate_self_signed_cert(store.certs_dir)
        if not store.config.tls_cert_file:
            store.config.tls_cert_file = str(cert)
            store.config.tls_key_file = str(key)
            store.save()

    async def _notify(reason: str, details: dict[str, Any] | None) -> None:
        if notifier:
            await notifier.notify_refresh(reason, details)

    watcher = ChangeWatcher(
        config_path=store.config_path,
        reload_config=lambda: store.reload() if store else None,
        notify=_notify,
        eventkit_backend=backend,
    )
    watcher.start(asyncio.get_running_loop())
    yield
    if watcher:
        watcher.stop()
    if meta:
        meta.close()


app = FastAPI(title="appleHAsync Mac Agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    cfg = get_store().config
    perms = backend.get_permissions().to_dict() if backend else {
        "calendar": "unavailable",
        "reminders": "unavailable",
    }
    return {
        "ok": True,
        "backend": "eventkit" if backend else None,
        "permissions": perms,
        "shared_calendars": len(cfg.shared_calendars),
        "shared_reminder_lists": len(cfg.shared_reminder_lists),
        "home_assistants": len([h for h in cfg.home_assistants if h.enabled]),
        "allow_insecure_http": cfg.allow_insecure_http,
        "tls_configured": bool(cfg.tls_cert_file and cfg.tls_key_file),
        "last_webhooks": notifier.last_results if notifier else [],
    }


@app.get("/v1/calendars", dependencies=[Depends(require_auth)])
async def list_calendars():
    _require_calendar_perm()
    st = get_store()
    cfg = st.config
    live = backend.list_calendars()
    stale_c, _ = cfg.prune_missing_shares({c.id for c in live}, set(cfg.shared_reminder_lists))
    if stale_c:
        st.save()
        _LOGGER.warning("Pruned stale shared calendar IDs: %s", stale_c)
    items = []
    for cal in live:
        if not cfg.is_calendar_shared(cal.id):
            continue
        cal.shared = True
        items.append(cal.to_dict())
    return {"calendars": items}


@app.get("/v1/reminder-lists", dependencies=[Depends(require_auth)])
async def list_reminder_lists():
    _require_reminders_perm()
    st = get_store()
    cfg = st.config
    live = backend.list_reminder_lists()
    _, stale_l = cfg.prune_missing_shares(set(cfg.shared_calendars), {lst.id for lst in live})
    if stale_l:
        st.save()
        _LOGGER.warning("Pruned stale shared reminder list IDs: %s", stale_l)
    items = []
    for lst in live:
        if not cfg.is_list_shared(lst.id):
            continue
        lst.shared = True
        items.append(lst.to_dict())
    return {"reminder_lists": items}


@app.get("/v1/calendars/{calendar_id}/events", dependencies=[Depends(require_auth)])
async def get_events(calendar_id: str, start: str, end: str):
    _require_calendar_perm()
    cfg = get_store().config
    if not cfg.is_calendar_shared(calendar_id):
        raise HTTPException(status_code=404, detail="not_shared")
    events = backend.get_events(calendar_id, _parse_dt(start), _parse_dt(end))
    return {"events": [e.to_dict() for e in events]}


@app.get("/v1/lists/{list_id}/items", dependencies=[Depends(require_auth)])
async def get_items(list_id: str):
    _require_reminders_perm()
    cfg = get_store().config
    if not cfg.is_list_shared(list_id):
        raise HTTPException(status_code=404, detail="not_shared")
    items = backend.get_todo_items(list_id)
    return {"items": [i.to_dict() for i in items]}


@app.post("/v1/calendars/{calendar_id}/events", dependencies=[Depends(require_auth)])
async def create_event(calendar_id: str, body: EventCreateBody, request: Request):
    _rate_limit_mutate(request.client.host if request.client else None)
    _require_calendar_perm()
    cfg = get_store().config
    if not cfg.is_calendar_shared(calendar_id):
        raise HTTPException(status_code=404, detail="not_shared")
    event = Event(
        uid="",
        calendar_id=calendar_id,
        summary=body.summary,
        start=_parse_dt(body.start),
        end=_parse_dt(body.end),
        description=body.description,
        location=body.location,
        all_day=body.all_day,
    )
    created = backend.create_event(calendar_id, event)
    if meta and created.content_hash:
        meta.set_hash("event", calendar_id, created.uid, created.content_hash)
        meta.mark_echo("event", created.uid, cfg.echo_suppress_seconds)
    if notifier:
        await notifier.notify_refresh("event_created", {"uid": created.uid})
    return created.to_dict()


@app.patch("/v1/calendars/{calendar_id}/events/{uid}", dependencies=[Depends(require_auth)])
async def patch_event(calendar_id: str, uid: str, request: Request):
    _rate_limit_mutate(request.client.host if request.client else None)
    _require_calendar_perm()
    cfg = get_store().config
    if not cfg.is_calendar_shared(calendar_id):
        raise HTTPException(status_code=404, detail="not_shared")
    data = await request.json()
    if not isinstance(data, dict) or not data:
        raise HTTPException(status_code=400, detail="empty_patch")
    if len(data) > 20:
        raise HTTPException(status_code=400, detail="patch_too_large")
    # Parse date fields
    parsed = dict(data)
    for key in ("start", "end"):
        if key in parsed and isinstance(parsed[key], str):
            parsed[key] = _parse_dt(parsed[key])
    patch = EventPatch.from_dict(parsed)
    updated = backend.patch_event(calendar_id, uid, patch)
    if meta and updated.content_hash:
        meta.set_hash("event", calendar_id, updated.uid, updated.content_hash)
        meta.mark_echo("event", updated.uid, cfg.echo_suppress_seconds)
    if notifier:
        await notifier.notify_refresh("event_patched", {"uid": uid, "fields": list(patch._fields_set)})
    return updated.to_dict()


@app.delete("/v1/calendars/{calendar_id}/events/{uid}", dependencies=[Depends(require_auth)])
async def delete_event(calendar_id: str, uid: str, request: Request):
    _rate_limit_mutate(request.client.host if request.client else None)
    _require_calendar_perm()
    cfg = get_store().config
    if not cfg.is_calendar_shared(calendar_id):
        raise HTTPException(status_code=404, detail="not_shared")
    backend.delete_event(calendar_id, uid)
    if meta:
        meta.mark_echo("event", uid, cfg.echo_suppress_seconds)
    if notifier:
        await notifier.notify_refresh("event_deleted", {"uid": uid})
    return {"ok": True}


@app.post("/v1/lists/{list_id}/items", dependencies=[Depends(require_auth)])
async def create_item(list_id: str, body: TodoCreateBody, request: Request):
    _rate_limit_mutate(request.client.host if request.client else None)
    _require_reminders_perm()
    cfg = get_store().config
    if not cfg.is_list_shared(list_id):
        raise HTTPException(status_code=404, detail="not_shared")
    item = TodoItem(
        uid="",
        list_id=list_id,
        summary=body.summary,
        description=body.description,
        status=TodoItemStatus(body.status),
        due=_parse_dt(body.due) if body.due else None,
        priority=body.priority,
    )
    created = backend.create_todo_item(list_id, item)
    if meta and created.content_hash:
        meta.set_hash("todo", list_id, created.uid, created.content_hash)
        meta.mark_echo("todo", created.uid, cfg.echo_suppress_seconds)
    if notifier:
        await notifier.notify_refresh("todo_created", {"uid": created.uid})
    return created.to_dict()


@app.patch("/v1/lists/{list_id}/items/{uid}", dependencies=[Depends(require_auth)])
async def patch_item(list_id: str, uid: str, request: Request):
    _rate_limit_mutate(request.client.host if request.client else None)
    _require_reminders_perm()
    cfg = get_store().config
    if not cfg.is_list_shared(list_id):
        raise HTTPException(status_code=404, detail="not_shared")
    data = await request.json()
    if not isinstance(data, dict) or not data:
        raise HTTPException(status_code=400, detail="empty_patch")
    if len(data) > 20:
        raise HTTPException(status_code=400, detail="patch_too_large")
    parsed = dict(data)
    if "due" in parsed and isinstance(parsed["due"], str):
        parsed["due"] = _parse_dt(parsed["due"])
    patch = TodoItemPatch.from_dict(parsed)
    updated = backend.patch_todo_item(list_id, uid, patch)
    if meta and updated.content_hash:
        meta.set_hash("todo", list_id, updated.uid, updated.content_hash)
        meta.mark_echo("todo", updated.uid, cfg.echo_suppress_seconds)
    if notifier:
        await notifier.notify_refresh("todo_patched", {"uid": uid, "fields": list(patch._fields_set)})
    return updated.to_dict()


@app.delete("/v1/lists/{list_id}/items/{uid}", dependencies=[Depends(require_auth)])
async def delete_item(list_id: str, uid: str, request: Request):
    _rate_limit_mutate(request.client.host if request.client else None)
    _require_reminders_perm()
    cfg = get_store().config
    if not cfg.is_list_shared(list_id):
        raise HTTPException(status_code=404, detail="not_shared")
    backend.delete_todo_item(list_id, uid)
    if meta:
        meta.mark_echo("todo", uid, cfg.echo_suppress_seconds)
    if notifier:
        await notifier.notify_refresh("todo_deleted", {"uid": uid})
    return {"ok": True}


# ---- Admin: sources / share ----


@app.get("/v1/admin/sources", dependencies=[Depends(require_auth)])
async def admin_sources():
    _ensure_backend()
    cfg = get_store().config
    calendars = []
    for cal in backend.list_calendars():
        cal.shared = cfg.is_calendar_shared(cal.id)
        calendars.append(cal.to_dict())
    lists = []
    for lst in backend.list_reminder_lists():
        lst.shared = cfg.is_list_shared(lst.id)
        lists.append(lst.to_dict())
    return {"calendars": calendars, "reminder_lists": lists}


@app.put("/v1/admin/share", dependencies=[Depends(require_auth)])
async def admin_share(body: ShareUpdate):
    st = get_store()
    st.set_share(
        calendar_ids=body.shared_calendars,
        list_ids=body.shared_reminder_lists,
        calendar_titles=body.calendar_titles,
        reminder_titles=body.reminder_titles,
    )
    if notifier:
        await notifier.notify_refresh("share_changed")
    return {
        "shared_calendars": st.config.shared_calendars,
        "shared_reminder_lists": st.config.shared_reminder_lists,
    }


@app.put("/v1/admin/share/toggle", dependencies=[Depends(require_auth)])
async def admin_share_toggle(body: ShareToggle):
    st = get_store()
    if body.kind == "calendar":
        if body.shared:
            st.enable_calendar(body.id, body.title)
        else:
            st.disable_calendar(body.id)
    elif body.kind == "reminder_list":
        if body.shared:
            st.enable_list(body.id, body.title)
        else:
            st.disable_list(body.id)
    else:
        raise HTTPException(status_code=400, detail="invalid_kind")
    if notifier:
        await notifier.notify_refresh("share_changed", {"id": body.id, "shared": body.shared})
    return {"ok": True}


# ---- Admin: permissions ----


@app.get("/v1/admin/permissions", dependencies=[Depends(require_auth)])
async def admin_permissions_get():
    _ensure_backend()
    return backend.get_permissions().to_dict()


@app.post("/v1/admin/permissions", dependencies=[Depends(require_auth)])
async def admin_permissions_action(body: PermissionsAction):
    _ensure_backend()
    if body.action == "request":
        perms = await backend.request_permissions()
        return perms.to_dict()
    if body.action == "open_settings":
        backend.open_privacy_settings(body.which)
        return {"ok": True, "action": "open_settings"}
    if body.action == "reset":
        if not body.confirm_reset:
            raise HTTPException(status_code=400, detail="confirm_reset_required")
        backend.reset_tcc(body.which)
        perms = await backend.request_permissions()
        return {"ok": True, "action": "reset", "permissions": perms.to_dict()}
    raise HTTPException(status_code=400, detail="unknown_action")


# ---- Admin: home assistants ----


@app.get("/v1/admin/home-assistants", dependencies=[Depends(require_auth)])
async def admin_ha_list():
    return {
        "home_assistants": [h.to_public_dict() for h in get_store().config.home_assistants]
    }


@app.post("/v1/admin/home-assistants", dependencies=[Depends(require_auth)])
async def admin_ha_create(body: HaCreate):
    st = get_store()
    # HTTP HA is allowed when agent allow_insecure_http is on, or this target disables TLS verify (lab LAN).
    if body.base_url.startswith("http://") and not (
        st.config.allow_insecure_http or not body.verify_tls
    ):
        raise HTTPException(status_code=400, detail="https_required")
    import secrets
    import uuid

    target = HomeAssistantTarget(
        id=str(uuid.uuid4()),
        name=body.name,
        base_url=body.base_url.rstrip("/"),
        token=body.token,
        webhook_id=body.webhook_id,
        webhook_secret=body.webhook_secret or secrets.token_urlsafe(24),
        verify_tls=body.verify_tls,
        ca_path=body.ca_path,
        enabled=body.enabled,
    )
    st.upsert_ha(target)
    return target.to_public_dict()


@app.put("/v1/admin/home-assistants/{key}", dependencies=[Depends(require_auth)])
async def admin_ha_update(key: str, body: HaUpdate):
    st = get_store()
    ha = st.find_ha(key)
    if not ha:
        raise HTTPException(status_code=404, detail="not_found")
    data = ha.to_storage()
    updates = body.model_dump(exclude_unset=True)
    if "base_url" in updates and updates["base_url"]:
        updates["base_url"] = updates["base_url"].rstrip("/")
        verify = updates.get("verify_tls", ha.verify_tls)
        if updates["base_url"].startswith("http://") and not (
            st.config.allow_insecure_http or not verify
        ):
            raise HTTPException(status_code=400, detail="https_required")
    data.update(updates)
    updated = HomeAssistantTarget.from_dict(data)
    st.upsert_ha(updated)
    return updated.to_public_dict()


@app.delete("/v1/admin/home-assistants/{key}", dependencies=[Depends(require_auth)])
async def admin_ha_delete(key: str):
    if not get_store().remove_ha(key):
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True}


@app.post("/v1/admin/home-assistants/{key}/test", dependencies=[Depends(require_auth)])
async def admin_ha_test(key: str):
    st = get_store()
    ha = st.find_ha(key)
    if not ha:
        raise HTTPException(status_code=404, detail="not_found")
    result = await test_ha_connection(
        base_url=ha.base_url,
        token=ha.token,
        verify_tls=ha.verify_tls,
        ca_path=ha.ca_path,
        allow_insecure_http=st.config.allow_insecure_http or not ha.verify_tls,
    )
    return result


@app.post("/v1/admin/token/rotate", dependencies=[Depends(require_auth)])
async def admin_token_rotate():
    token = get_store().rotate_agent_token()
    return {"agent_token": token}


@app.exception_handler(KeyError)
async def key_error_handler(_request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": "not_found", "id": str(exc)})


def create_app() -> FastAPI:
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    cfg_store = ConfigStore()
    cfg = cfg_store.config
    ssl_kwargs: dict[str, Any] = {}
    if not cfg.allow_insecure_http:
        cert, key = generate_self_signed_cert(cfg_store.certs_dir)
        cert_file = cfg.tls_cert_file or str(cert)
        key_file = cfg.tls_key_file or str(key)
        ssl_kwargs = {"ssl_certfile": cert_file, "ssl_keyfile": key_file}
        _LOGGER.info("Starting HTTPS on %s:%s", cfg.listen_host, cfg.listen_port)
    else:
        _LOGGER.warning(
            "allow_insecure_http=true — agent serving cleartext HTTP (lab only)"
        )
    # Ensure shared package importable
    root = str(__file__).rsplit("/mac_agent", 1)[0]
    if root not in sys.path:
        sys.path.insert(0, root)
    uvicorn.run(
        "mac_agent.api:app",
        host=cfg.listen_host,
        port=cfg.listen_port,
        reload=False,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
