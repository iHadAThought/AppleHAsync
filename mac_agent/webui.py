"""Local settings Web UI for appleHAsync Mac agent."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import ConfigStore
from . import self_update

WEB_DIR = Path(__file__).resolve().parent / "web"


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


class SettingsUpdate(BaseModel):
    listen_host: str | None = None
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    allow_insecure_http: bool | None = None
    allowed_source_ips: list[str] | None = None
    setup_completed: bool | None = None


class UpdateRequest(BaseModel):
    repair_shares: bool = False


def build_ui_router(
    *,
    get_store,
    require_auth,
    backend_getter,
) -> APIRouter:
    """Router factory so api.py can inject store/auth without circular imports."""
    router = APIRouter(tags=["ui"])

    @router.get("/")
    async def root_redirect():
        return RedirectResponse(url="/ui/", status_code=307)

    @router.get("/ui")
    async def ui_redirect():
        return RedirectResponse(url="/ui/", status_code=307)

    @router.get("/ui/")
    async def ui_index():
        index = WEB_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="ui_missing")
        return FileResponse(index, media_type="text/html; charset=utf-8")

    @router.get("/v1/setup/bootstrap")
    async def setup_bootstrap(request: Request):
        """Loopback-only auto-login for the settings UI.

        Any process on the Mac can call this without a bearer token — that is
        intentional for first-run UX. Remote clients always get 403.
        """
        if not _is_loopback(request):
            raise HTTPException(status_code=403, detail="loopback_only")
        st: ConfigStore = get_store()
        cfg = st.config
        perms = {"calendar": "unavailable", "reminders": "unavailable"}
        be = backend_getter()
        if be is not None:
            try:
                perms = be.get_permissions().to_dict()
            except Exception:
                pass
        setup_needed = not bool(getattr(cfg, "setup_completed", False))
        if not cfg.home_assistants and not (
            cfg.shared_calendars or cfg.shared_reminder_lists
        ):
            setup_needed = True
        return {
            "ok": True,
            "loopback": True,
            "agent_token": cfg.agent_token,
            "setup_needed": setup_needed,
            "setup_completed": bool(getattr(cfg, "setup_completed", False)),
            "listen_host": cfg.listen_host,
            "listen_port": cfg.listen_port,
            "permissions": perms,
            "shared_calendars": len(cfg.shared_calendars),
            "shared_reminder_lists": len(cfg.shared_reminder_lists),
            "home_assistants": len(cfg.home_assistants),
        }

    @router.get("/v1/admin/settings", dependencies=[Depends(require_auth)])
    async def admin_settings_get():
        cfg = get_store().config
        return {
            "listen_host": cfg.listen_host,
            "listen_port": cfg.listen_port,
            "allow_insecure_http": cfg.allow_insecure_http,
            "allowed_source_ips": list(cfg.allowed_source_ips),
            "setup_completed": bool(getattr(cfg, "setup_completed", False)),
            "tls_configured": bool(cfg.tls_cert_file and cfg.tls_key_file),
            "version": __version__,
            "git_revision": self_update.git_revision(),
            "update_available": self_update.update_script_path().is_file(),
            "agent_token_hint": (cfg.agent_token[:4] + "…" + cfg.agent_token[-4:])
            if len(cfg.agent_token) > 8
            else "••••",
            "restart_required_note": (
                "Changing listen_host/port requires restarting the appleHAsync LaunchAgent."
            ),
        }

    @router.get("/v1/admin/update", dependencies=[Depends(require_auth)])
    async def admin_update_status():
        data = self_update.status()
        data["version"] = __version__
        return data

    @router.post("/v1/admin/update", dependencies=[Depends(require_auth)])
    async def admin_update_start(body: UpdateRequest):
        """Run deploy/update-mac-agent.sh (git pull, rebuild, LaunchAgent restart).

        Auth-gated; argv is fixed (optional --repair-shares only). The process is
        detached after a short delay so this response can finish before restart.
        """
        try:
            return self_update.start_update(repair_shares=bool(body.repair_shares))
        except ValueError as exc:
            detail = str(exc) or "update_failed"
            code = 409 if detail == "update_already_running" else 400
            raise HTTPException(status_code=code, detail=detail) from exc

    @router.put("/v1/admin/settings", dependencies=[Depends(require_auth)])
    async def admin_settings_put(body: SettingsUpdate):
        st: ConfigStore = get_store()
        cfg = st.config
        changed = False
        restart_needed = False
        if body.listen_host is not None and body.listen_host != cfg.listen_host:
            cfg.listen_host = body.listen_host.strip() or "127.0.0.1"
            changed = True
            restart_needed = True
        if body.listen_port is not None and body.listen_port != cfg.listen_port:
            cfg.listen_port = int(body.listen_port)
            changed = True
            restart_needed = True
        if (
            body.allow_insecure_http is not None
            and body.allow_insecure_http != cfg.allow_insecure_http
        ):
            cfg.allow_insecure_http = bool(body.allow_insecure_http)
            changed = True
            restart_needed = True
        if body.allowed_source_ips is not None:
            cfg.allowed_source_ips = [
                ip.strip() for ip in body.allowed_source_ips if ip and ip.strip()
            ]
            changed = True
        if body.setup_completed is not None:
            cfg.setup_completed = bool(body.setup_completed)
            changed = True
        if changed:
            st.save()
        return {
            "ok": True,
            "restart_needed": restart_needed,
            "settings": {
                "listen_host": cfg.listen_host,
                "listen_port": cfg.listen_port,
                "allow_insecure_http": cfg.allow_insecure_http,
                "allowed_source_ips": list(cfg.allowed_source_ips),
                "setup_completed": bool(getattr(cfg, "setup_completed", False)),
            },
        }

    return router


def mount_static(app) -> None:
    assets = WEB_DIR / "assets"
    if assets.is_dir():
        app.mount("/ui/assets", StaticFiles(directory=str(assets)), name="ui-assets")
