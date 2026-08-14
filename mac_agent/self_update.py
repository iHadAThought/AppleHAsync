"""Trigger deploy/update-mac-agent.sh from the settings UI (fixed path only)."""

from __future__ import annotations

import json
import logging
import os
import shutil
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


def _read_status_file() -> dict[str, Any] | None:
    path = _status_path()
    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        return saved if isinstance(saved, dict) else None
    except Exception:
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _python_version_ok(py: Path | str) -> bool:
    try:
        out = subprocess.check_output(
            [str(py), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        major_s, minor_s = out.split(".", 1)
        return int(major_s) > 3 or (int(major_s) == 3 and int(minor_s) >= 10)
    except Exception:
        return False


def resolve_python_bin(root: Path | None = None) -> str | None:
    """Pick a Python ≥3.10 for the updater (venv / Homebrew / PATH)."""
    root = root or install_root()
    candidates: list[str] = []
    env_py = (os.environ.get("PYTHON_BIN") or "").strip()
    if env_py:
        candidates.append(env_py)
    venv_py = root / ".venv" / "bin" / "python"
    if venv_py.is_file():
        candidates.append(str(venv_py))
    brew = shutil.which("brew")
    if brew:
        for formula in ("python@3.12", "python@3.13", "python@3.11", "python@3.10"):
            try:
                prefix = subprocess.check_output(
                    [brew, "--prefix", formula],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=10,
                ).strip()
            except Exception:
                continue
            if not prefix:
                continue
            base = Path(prefix) / "bin"
            for name in ("python3.12", "python3.13", "python3.11", "python3.10", "python3"):
                candidates.append(str(base / name))
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if _python_version_ok(cand):
            return cand
    return None


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


def _clear_stale_lock() -> None:
    """If a previous UI update died without clearing state, unlock."""
    global _running
    with _lock:
        saved = _read_status_file()
        if not saved:
            if _running:
                # In-memory only; no file — leave alone unless we know nothing is active.
                pass
            return
        state = saved.get("state")
        pid = saved.get("pid")
        if state in ("running", "starting") and not _pid_alive(
            int(pid) if isinstance(pid, int) else None
        ):
            # Updater process is gone (failed before restart, or agent never restarted).
            if _running or state in ("running", "starting"):
                _LOGGER.warning(
                    "Clearing stale UI update lock (state=%s pid=%s)", state, pid
                )
                saved = {
                    **saved,
                    "state": "failed",
                    "error": "updater_exited_without_restart",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                _write_status(saved)
            _running = False


def status() -> dict[str, Any]:
    _clear_stale_lock()
    script = update_script_path()
    data: dict[str, Any] = {
        "running": _running,
        "script_present": script.is_file(),
        "script_path": str(script),
        "install_root": str(install_root()),
        "git_revision": git_revision(),
        "python_bin": resolve_python_bin(),
    }
    saved = _read_status_file()
    if saved:
        data["last"] = saved
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
    _clear_stale_lock()
    script = update_script_path()
    root = install_root()
    if not script.is_file():
        raise ValueError("update_script_missing")
    # Refuse path escape if APPLE_HASYNC_ROOT is odd
    try:
        script.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("update_script_invalid") from exc

    python_bin = resolve_python_bin(root)
    if not python_bin:
        raise ValueError(
            "python_too_old: need Python 3.10+ "
            "(brew install python@3.12, then retry Update)"
        )

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
            "python_bin": python_bin,
        }
    )

    def _worker() -> None:
        global _running
        log_path = _log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        proc: subprocess.Popen[bytes] | None = None
        try:
            time.sleep(max(0.5, float(delay_seconds)))
            argv = ["/bin/bash", str(script)]
            if repair_shares:
                argv.append("--repair-shares")
            env = os.environ.copy()
            env["PYTHON_BIN"] = python_bin
            env["APPLE_HASYNC_ROOT"] = str(root)
            with log_path.open("a", encoding="utf-8") as logf:
                logf.write(
                    f"\n===== ui update {started} repair_shares={repair_shares} "
                    f"PYTHON_BIN={python_bin} =====\n"
                )
                logf.flush()
                proc = subprocess.Popen(
                    argv,
                    cwd=str(root),
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )
            _write_status(
                {
                    "state": "running",
                    "started_at": started,
                    "repair_shares": bool(repair_shares),
                    "pid": proc.pid,
                    "python_bin": python_bin,
                }
            )
            _LOGGER.info(
                "UI-triggered update started pid=%s python=%s", proc.pid, python_bin
            )
            # Wait so we clear the lock if the script fails without restarting us.
            rc = proc.wait()
            finished = datetime.now(timezone.utc).isoformat()
            if rc != 0:
                _LOGGER.error("UI-triggered update exited rc=%s", rc)
                _write_status(
                    {
                        "state": "failed",
                        "started_at": started,
                        "finished_at": finished,
                        "repair_shares": bool(repair_shares),
                        "pid": proc.pid,
                        "python_bin": python_bin,
                        "exit_code": rc,
                        "error": f"updater_exit_{rc}",
                    }
                )
            else:
                # Success usually restarts the agent; if we are still alive, mark done.
                _write_status(
                    {
                        "state": "completed",
                        "started_at": started,
                        "finished_at": finished,
                        "repair_shares": bool(repair_shares),
                        "pid": proc.pid,
                        "python_bin": python_bin,
                        "exit_code": 0,
                    }
                )
        except Exception as exc:
            _LOGGER.exception("UI-triggered update failed to start")
            _write_status(
                {
                    "state": "failed",
                    "started_at": started,
                    "repair_shares": bool(repair_shares),
                    "python_bin": python_bin,
                    "error": str(exc),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        finally:
            with _lock:
                _running = False

    threading.Thread(target=_worker, name="applehasync-self-update", daemon=True).start()
    return {
        "ok": True,
        "state": "starting",
        "started_at": started,
        "repair_shares": bool(repair_shares),
        "python_bin": python_bin,
        "message": (
            "Update started. The agent will restart; keep this page open. "
            "Home Assistant must be updated separately (HACS)."
        ),
        "log_path": str(_log_path()),
    }
