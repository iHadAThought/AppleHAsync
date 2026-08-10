"""Trigger deploy/update-mac-agent.sh from the settings UI (fixed path only)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

_lock = threading.Lock()
_running = False


def install_root() -> Path:
    env = (os.environ.get("APPLE_HASYNC_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def update_script_path() -> Path:
    return install_root() / "deploy" / "update-mac-agent.sh"


def _status_path() -> Path:
    return install_root() / "logs" / "ui-update-status.json"


def _log_path() -> Path:
    return install_root() / "logs" / "ui-update.log"


def _write_status(payload: dict[str, Any]) -> None:
    path = _status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def git_revision() -> str | None:
    root = install_root()
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return out.strip() or None
    except Exception:
        return None


def status() -> dict[str, Any]:
    script = update_script_path()
    data: dict[str, Any] = {
        "running": _running,
        "script_present": script.is_file(),
        "script_path": str(script),
        "install_root": str(install_root()),
        "git_revision": git_revision(),
    }
    path = _status_path()
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data["last"] = saved
        except Exception:
            pass
    log = _log_path()
    if log.is_file():
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            data["log_tail"] = lines[-40:]
        except Exception:
            data["log_tail"] = []
    return data


def start_update(*, repair_shares: bool = False, delay_seconds: float = 1.5) -> dict[str, Any]:
    """Queue a detached updater run. Raises ValueError on conflict / missing script."""
    global _running
    script = update_script_path()
    root = install_root()
    if not script.is_file():
        raise ValueError("update_script_missing")
    # Refuse path escape if APPLE_HASYNC_ROOT is odd
    try:
        script.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("update_script_invalid") from exc

    with _lock:
        if _running:
            raise ValueError("update_already_running")
        _running = True

    started = datetime.now(timezone.utc).isoformat()
    _write_status(
        {
            "state": "starting",
            "started_at": started,
            "repair_shares": bool(repair_shares),
            "pid": None,
        }
    )

    def _worker() -> None:
        global _running
        log_path = _log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            time.sleep(max(0.5, float(delay_seconds)))
            argv = ["/bin/bash", str(script)]
            if repair_shares:
                argv.append("--repair-shares")
            with log_path.open("a", encoding="utf-8") as logf:
                logf.write(
                    f"\n===== ui update {started} repair_shares={repair_shares} =====\n"
                )
                logf.flush()
                proc = subprocess.Popen(
                    argv,
                    cwd=str(root),
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=os.environ.copy(),
                )
            _write_status(
                {
                    "state": "running",
                    "started_at": started,
                    "repair_shares": bool(repair_shares),
                    "pid": proc.pid,
                }
            )
            _LOGGER.info("UI-triggered update started pid=%s", proc.pid)
        except Exception as exc:
            _LOGGER.exception("UI-triggered update failed to start")
            _write_status(
                {
                    "state": "failed",
                    "started_at": started,
                    "repair_shares": bool(repair_shares),
                    "error": str(exc),
                }
            )
            with _lock:
                _running = False

    threading.Thread(target=_worker, name="applehasync-self-update", daemon=True).start()
    return {
        "ok": True,
        "state": "starting",
        "started_at": started,
        "repair_shares": bool(repair_shares),
        "message": "Update started. The agent will restart; keep this page open.",
        "log_path": str(_log_path()),
    }
