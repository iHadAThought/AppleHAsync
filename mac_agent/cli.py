#!/usr/bin/env python3
"""CLI for appleHAsync Mac agent administration."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import uuid
from pathlib import Path

# Allow running from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mac_agent.config import ConfigStore, HomeAssistantTarget  # noqa: E402
from mac_agent.security import test_ha_connection  # noqa: E402


def _backend():
    from mac_agent.eventkit_backend import EventKitBackend

    return EventKitBackend()


def cmd_serve(_args: argparse.Namespace) -> int:
    from mac_agent.api import main

    main()
    return 0


def cmd_token_show(_args: argparse.Namespace) -> int:
    store = ConfigStore()
    print(store.config.agent_token)
    return 0


def cmd_token_rotate(_args: argparse.Namespace) -> int:
    store = ConfigStore()
    print(store.rotate_agent_token())
    return 0


def cmd_share_list(_args: argparse.Namespace) -> int:
    store = ConfigStore()
    backend = _backend()
    print("Calendars:")
    for cal in backend.list_calendars():
        flag = "SHARED" if store.config.is_calendar_shared(cal.id) else "-"
        print(f"  [{flag}] {cal.title} ({cal.id})")
    print("Reminder lists:")
    for lst in backend.list_reminder_lists():
        flag = "SHARED" if store.config.is_list_shared(lst.id) else "-"
        print(f"  [{flag}] {lst.title} ({lst.id})")
    return 0


def cmd_share_enable(args: argparse.Namespace) -> int:
    store = ConfigStore()
    backend = _backend()
    if args.kind == "calendar":
        title = next(
            (c.title for c in backend.list_calendars() if c.id == args.id), None
        )
        store.enable_calendar(args.id, title)
    else:
        title = next(
            (c.title for c in backend.list_reminder_lists() if c.id == args.id), None
        )
        store.enable_list(args.id, title)
    print("ok")
    return 0


def cmd_share_disable(args: argparse.Namespace) -> int:
    store = ConfigStore()
    if args.kind == "calendar":
        store.disable_calendar(args.id)
    else:
        store.disable_list(args.id)
    print("ok")
    return 0


def cmd_permissions_status(_args: argparse.Namespace) -> int:
    print(json.dumps(_backend().get_permissions().to_dict(), indent=2))
    return 0


def cmd_permissions_request(_args: argparse.Namespace) -> int:
    perms = asyncio.run(_backend().request_permissions())
    print(json.dumps(perms.to_dict(), indent=2))
    return 0


def cmd_permissions_open_settings(args: argparse.Namespace) -> int:
    _backend().open_privacy_settings(args.which)
    print("opened System Settings")
    return 0


def cmd_permissions_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing without --yes (this clears TCC entries for Calendar/Reminders)")
        return 1
    backend = _backend()
    backend.reset_tcc(args.which)
    perms = asyncio.run(backend.request_permissions())
    print(json.dumps(perms.to_dict(), indent=2))
    return 0


def cmd_ha_list(_args: argparse.Namespace) -> int:
    store = ConfigStore()
    for ha in store.config.home_assistants:
        print(json.dumps(ha.to_public_dict()))
    return 0


def cmd_ha_add(args: argparse.Namespace) -> int:
    store = ConfigStore()
    if args.url.startswith("http://") and not store.config.allow_insecure_http:
        print("HTTPS required (or set allow_insecure_http in config)", file=sys.stderr)
        return 1
    target = HomeAssistantTarget(
        id=str(uuid.uuid4()),
        name=args.name,
        base_url=args.url.rstrip("/"),
        token=args.token or "",
        webhook_id=args.webhook_id or "",
        webhook_secret=args.webhook_secret or secrets.token_urlsafe(24),
        verify_tls=not args.insecure,
        ca_path=args.ca_path,
        enabled=True,
    )
    store.upsert_ha(target)
    print(json.dumps(target.to_public_dict(), indent=2))
    return 0


def cmd_ha_update(args: argparse.Namespace) -> int:
    store = ConfigStore()
    ha = store.find_ha(args.key)
    if not ha:
        print("not found", file=sys.stderr)
        return 1
    data = ha.to_storage()
    if args.name:
        data["name"] = args.name
    if args.url:
        data["base_url"] = args.url.rstrip("/")
    if args.token is not None:
        data["token"] = args.token
    if args.webhook_id is not None:
        data["webhook_id"] = args.webhook_id
    if args.webhook_secret is not None:
        data["webhook_secret"] = args.webhook_secret
    if args.enable:
        data["enabled"] = True
    if args.disable:
        data["enabled"] = False
    if args.insecure:
        data["verify_tls"] = False
    if args.verify_tls:
        data["verify_tls"] = True
    updated = HomeAssistantTarget.from_dict(data)
    store.upsert_ha(updated)
    print(json.dumps(updated.to_public_dict(), indent=2))
    return 0


def cmd_ha_remove(args: argparse.Namespace) -> int:
    if not ConfigStore().remove_ha(args.key):
        print("not found", file=sys.stderr)
        return 1
    print("ok")
    return 0


def cmd_ha_test(args: argparse.Namespace) -> int:
    store = ConfigStore()
    ha = store.find_ha(args.key)
    if not ha:
        print("not found", file=sys.stderr)
        return 1
    result = asyncio.run(
        test_ha_connection(
            base_url=ha.base_url,
            token=ha.token,
            verify_tls=ha.verify_tls,
            ca_path=ha.ca_path,
            allow_insecure_http=store.config.allow_insecure_http,
        )
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apple-hasync", description="appleHAsync Mac agent CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Run the HTTPS agent")
    s.set_defaults(func=cmd_serve)

    t = sub.add_parser("token")
    ts = t.add_subparsers(dest="token_cmd", required=True)
    ts.add_parser("show").set_defaults(func=cmd_token_show)
    ts.add_parser("rotate").set_defaults(func=cmd_token_rotate)

    sh = sub.add_parser("share")
    shs = sh.add_subparsers(dest="share_cmd", required=True)
    shs.add_parser("list").set_defaults(func=cmd_share_list)
    en = shs.add_parser("enable")
    en.add_argument("kind", choices=["calendar", "reminder_list"])
    en.add_argument("id")
    en.set_defaults(func=cmd_share_enable)
    dis = shs.add_parser("disable")
    dis.add_argument("kind", choices=["calendar", "reminder_list"])
    dis.add_argument("id")
    dis.set_defaults(func=cmd_share_disable)

    perm = sub.add_parser("permissions")
    ps = perm.add_subparsers(dest="perm_cmd", required=True)
    ps.add_parser("status").set_defaults(func=cmd_permissions_status)
    ps.add_parser("request").set_defaults(func=cmd_permissions_request)
    op = ps.add_parser("open-settings")
    op.add_argument("--which", default="both")
    op.set_defaults(func=cmd_permissions_open_settings)
    rs = ps.add_parser("reset")
    rs.add_argument("--which", default="both")
    rs.add_argument("--yes", action="store_true")
    rs.set_defaults(func=cmd_permissions_reset)

    ha = sub.add_parser("ha")
    hs = ha.add_subparsers(dest="ha_cmd", required=True)
    hs.add_parser("list").set_defaults(func=cmd_ha_list)
    add = hs.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--token", default="")
    add.add_argument("--webhook-id", default="")
    add.add_argument("--webhook-secret", default="")
    add.add_argument("--ca-path", default=None)
    add.add_argument("--insecure", action="store_true")
    add.set_defaults(func=cmd_ha_add)
    upd = hs.add_parser("update")
    upd.add_argument("key")
    upd.add_argument("--name")
    upd.add_argument("--url")
    upd.add_argument("--token")
    upd.add_argument("--webhook-id")
    upd.add_argument("--webhook-secret")
    upd.add_argument("--enable", action="store_true")
    upd.add_argument("--disable", action="store_true")
    upd.add_argument("--insecure", action="store_true")
    upd.add_argument("--verify-tls", action="store_true")
    upd.set_defaults(func=cmd_ha_update)
    rm = hs.add_parser("remove")
    rm.add_argument("key")
    rm.set_defaults(func=cmd_ha_remove)
    test = hs.add_parser("test")
    test.add_argument("key")
    test.set_defaults(func=cmd_ha_test)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
