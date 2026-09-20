#!/usr/bin/env python3
"""Offline administrator CLI for application users and crawler grants."""

from __future__ import annotations

import argparse
import getpass
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application_auth.store import AuthStore
from backend.audit_agent.config import settings


def _store() -> AuthStore:
    return AuthStore(
        settings.app_auth_db,
        default_validity_days=settings.app_account_validity_days,
        activation_mode=settings.app_account_activation_mode,
    )


def _password(confirm: bool = True) -> str:
    value = getpass.getpass("Password: ")
    if confirm and value != getpass.getpass("Confirm password: "):
        raise ValueError("password confirmation does not match")
    return value


def _user(store: AuthStore, value: str) -> dict:
    direct = store.get_user(value)
    if direct is not None:
        return direct
    key = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    matches = [
        item
        for item in store.list_users()
        if unicodedata.normalize("NFKC", str(item["username"])).strip().casefold()
        == key
    ]
    if len(matches) != 1:
        raise KeyError(value)
    return matches[0]


def _create(store: AuthStore, args: argparse.Namespace, role: str) -> None:
    user = store.create_user(
        username=args.username,
        password=_password(),
        role=role,
        validity_days=args.validity_days,
        activation_mode=args.activation_mode,
        actor_user_id="offline-admin-cli",
    )
    print(f"created {user['role']} {user['username']} ({user['id']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("create-admin", "create-user"):
        command = sub.add_parser(name)
        command.add_argument("username")
        command.add_argument("--validity-days", type=int, default=7)
        command.add_argument(
            "--activation-mode",
            choices=("first_login", "created_at"),
            default=settings.app_account_activation_mode,
        )

    for name in ("disable", "renew", "reset-password"):
        command = sub.add_parser(name)
        command.add_argument("user", help="user UUID or username")
        if name == "renew":
            command.add_argument("--days", type=int, default=7)

    for name in ("grant-crawler-account", "revoke-crawler-account"):
        command = sub.add_parser(name)
        command.add_argument("user", help="user UUID or username")
        command.add_argument("account_id")
        command.add_argument("--permission", choices=("use", "manage"), default="use")

    args = parser.parse_args()
    store = _store()
    try:
        if args.command == "create-admin":
            _create(store, args, "admin")
        elif args.command == "create-user":
            _create(store, args, "user")
        else:
            user = _user(store, args.user)
            if args.command == "disable":
                updated = store.update_user(
                    user["id"], actor_user_id="offline-admin-cli", status="disabled"
                )
                print(f"disabled {updated['username']} ({updated['id']})")
            elif args.command == "renew":
                updated = store.update_user(
                    user["id"],
                    actor_user_id="offline-admin-cli",
                    status="active",
                    renew_days=args.days,
                )
                print(
                    f"renewed {updated['username']} ({updated['id']}) "
                    f"until {updated['expires_at']}"
                )
            elif args.command == "reset-password":
                updated = store.update_user(
                    user["id"],
                    actor_user_id="offline-admin-cli",
                    password=_password(),
                )
                print(f"reset password for {updated['username']} ({updated['id']})")
            elif args.command == "grant-crawler-account":
                store.grant_resource(
                    user_id=user["id"],
                    resource_type="crawler-account",
                    resource_id=args.account_id,
                    permission=args.permission,
                    actor_user_id="offline-admin-cli",
                )
                print(
                    f"granted {args.permission} on crawler account "
                    f"{args.account_id} to {user['username']}"
                )
            elif args.command == "revoke-crawler-account":
                changed = store.revoke_resource(
                    user_id=user["id"],
                    resource_type="crawler-account",
                    resource_id=args.account_id,
                    permission=args.permission,
                    actor_user_id="offline-admin-cli",
                )
                if not changed:
                    raise KeyError("grant")
                print(
                    f"revoked {args.permission} on crawler account "
                    f"{args.account_id} from {user['username']}"
                )
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
