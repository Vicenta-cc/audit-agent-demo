"""Explicit, local-OS-admin migration CLI; inventory/plan/status default read-only.

Never prints/decrypts cookies. Actual apply holds real account locks and inspects
processes itself: there is no --stopped/--force Boolean proof bypass.

Run under the validated release's owner environment. Offline plan/apply/resume/
commit --execute require --release-manifest plus a fresh --quiescence-receipt.
After starting the new runtime and scanning the same account, live-verify
--execute requires the same release manifest, active owner/admin authorization
and local OS root, but no stopped-unit receipt. It generates two real browser
restart proofs and commits only that account when every verification succeeds.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
from pathlib import Path

from backend.audit_agent.crawler_browser_migration import (
    BrowserMigrationService,
    MigrationError,
)


class ReadOnlyStore:
    def __init__(self, path):
        self.db_path = Path(path).absolute()
        self._lock = threading.Lock()

    def _connect(self):
        conn = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=(
            "inventory",
            "plan",
            "apply",
            "resume",
            "status",
            "reconcile",
            "verify",
            "live-verify",
            "commit",
        ),
    )
    for name in (
        "account-db",
        "owner-db",
        "logical-root",
        "state-root",
        "archive-root",
        "scheduler-db",
    ):
        result.add_argument("--" + name, required=True, type=Path)
    result.add_argument("--release-manifest", type=Path)
    result.add_argument("--quiescence-receipt", type=Path)
    result.add_argument("--account-id")
    result.add_argument("--migration-id")
    result.add_argument(
        "--actor-id",
        help="Existing active owner/admin identity; write commands additionally require local OS root",
    )
    result.add_argument(
        "--execute",
        action="store_true",
        help="Execute offline plan/apply/resume/commit or online live-verify-and-commit; default is read-only",
    )
    return result


def _verify_deployment_quiescence(args, *, offline=True):
    if not args.release_manifest or offline and not args.quiescence_receipt:
        raise MigrationError("DEPLOYMENT_QUIESCENCE_RECEIPT_REQUIRED")
    tools_dir = Path(__file__).resolve().parents[1] / "deploy" / "camoufox"
    sys.path.insert(0, str(tools_dir))
    try:
        from control import InvalidRelease, read_json, validate
        from quiescence import verify_receipt

        config = validate(read_json(args.release_manifest))
        for argument, manifest_key in (
            ("account_db", "account_db"),
            ("owner_db", "owner_db"),
            ("scheduler_db", "scheduler_db"),
            ("logical_root", "logical_profile_root"),
            ("state_root", "state_root"),
            ("archive_root", "archive_root"),
        ):
            configured = config["runtime"].get(manifest_key)
            if (
                not configured
                or getattr(args, argument).absolute() != Path(configured).absolute()
            ):
                raise MigrationError("DEPLOYMENT_MIGRATION_PATH_CONFLICT")
        from backend.audit_agent.config import settings

        # Run this CLI through the release's configured owner environment. A
        # manifest is evidence, not permission to load another runtime's binary.
        expected = {
            "crawler_login_python": config["runtime"]["crawler_python"],
            "media_crawler_dir": config["artifacts"]["crawler"]["path"],
            "crawler_camoufox_state_root": config["runtime"]["state_root"],
            "crawler_browser_profile_root": config["runtime"]["logical_profile_root"],
            "crawler_camoufox_archive_root": config["runtime"]["archive_root"],
            "request_scheduler_db": config["runtime"]["scheduler_db"],
            "app_auth_db": config["runtime"]["owner_db"],
            "crawler_child_uid": str(config["runtime"]["uid"]),
            "crawler_child_gid": str(config["runtime"]["gid"]),
            "crawler_camoufox_browser_version": config["browser"]["version"],
            "crawler_camoufox_executable": config["artifacts"]["browser_binary"][
                "path"
            ],
            "crawler_camoufox_executable_sha256": config["artifacts"]["browser_binary"][
                "sha256"
            ],
        }
        if any(
            str(getattr(settings, key)) != str(value) for key, value in expected.items()
        ):
            raise MigrationError("DEPLOYMENT_SETTINGS_CONFLICT")
        if offline:
            verify_receipt(config, args.quiescence_receipt, max_age=300)
        return config
    except InvalidRelease as exc:
        raise MigrationError("DEPLOYMENT_QUIESCENCE_FAILED:" + str(exc)) from exc
    finally:
        sys.path.remove(str(tools_dir))


def main(argv=None):
    args = parser().parse_args(argv)
    mutating = args.execute and args.command in (
        "plan",
        "apply",
        "resume",
        "live-verify",
        "commit",
    )
    if args.execute and not mutating:
        raise MigrationError("COMMAND_IS_READ_ONLY")
    if mutating and os.geteuid() != 0:
        raise MigrationError("LOCAL_OS_ADMIN_REQUIRED")
    if mutating:
        _verify_deployment_quiescence(args, offline=args.command != "live-verify")
    if (
        args.command
        in ("plan", "apply", "resume", "reconcile", "verify", "live-verify", "commit")
        and not args.actor_id
    ):
        raise MigrationError("ACTOR_REQUIRED")
    if args.command == "plan" and not args.account_id:
        raise MigrationError("EXACT_ACCOUNT_REQUIRED")
    if args.command not in ("inventory", "plan") and not (
        args.migration_id or args.command == "status" and args.account_id
    ):
        raise MigrationError("EXACT_MIGRATION_REQUIRED")
    if mutating:
        from backend.audit_agent.crawler_account_store import CrawlerAccountStore

        store = CrawlerAccountStore(args.account_db)
    else:
        store = ReadOnlyStore(args.account_db)

    def initializer(account_id, **kwargs):
        from backend.audit_agent.crawler_profile_initializer import (
            initialize_restricted_profile,
        )

        return initialize_restricted_profile(account_id, **kwargs)

    service = BrowserMigrationService(
        store,
        owner_db_path=args.owner_db,
        logical_root=args.logical_root,
        state_root=args.state_root,
        archive_root=args.archive_root,
        scheduler_db_path=args.scheduler_db,
        initializer=initializer,
    )
    if args.command == "inventory":
        output = service.inventory()
    elif args.command == "plan":
        output = service.plan(
            args.account_id, actor_id=args.actor_id, dry_run=not args.execute
        )
    elif args.command == "status":
        output = service.status(args.migration_id, account_id=args.account_id)
    elif args.command in ("apply", "resume"):
        output = service.apply(
            args.migration_id, actor_id=args.actor_id, dry_run=not args.execute
        )
    elif args.command in ("reconcile", "verify"):
        # Verify only inspects durable ledger/identity. Real route and login
        # verification is performed by authenticated App callbacks, never JSON
        # flags supplied to this CLI.
        output = service.reconcile(args.migration_id, actor_id=args.actor_id)
    elif args.command == "live-verify" and args.execute:
        from backend.audit_agent.crawler_browser_verification import (
            verify_migrated_account,
        )

        # Internal owner-side probe creates route/restart proof; no receipt JSON
        # or booleans are accepted from the operator. New App must be running.
        output = verify_migrated_account(
            args.migration_id, actor_id=args.actor_id, service=service, commit=True
        )
    elif not args.execute:
        output = {
            "dry_run": True,
            "migration": service.reconcile(args.migration_id, actor_id=args.actor_id),
        }
    else:
        output = service.commit(args.migration_id, actor_id=args.actor_id)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - CLI boundary exposes only allowlisted error codes.
        code = (
            str(exc)
            if isinstance(exc, MigrationError)
            else getattr(exc, "code", "MIGRATION_CLI_FAILED")
        )
        if not isinstance(code, str) or not re.fullmatch(
            r"[A-Za-z0-9_:.-]{1,200}", code
        ):
            code = "MIGRATION_CLI_FAILED"
        print(json.dumps({"error": code}), file=sys.stderr)
        sys.exit(2)
