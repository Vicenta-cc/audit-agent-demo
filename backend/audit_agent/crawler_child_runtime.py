"""One restricted launch boundary for account browser helpers.

The App retains its private databases and credentials. Browser children receive
an explicit environment and existing capability FDs, never the App environment.
"""

from __future__ import annotations

import os
import sys

from .config import settings


def child_environment(values=None):
    state = settings.crawler_camoufox_state_root
    directories = (
        state,
        state / "home",
        state / "home" / ".camoufox",
        state / "cache",
        state / "tmp",
        state / "outputs",
    )
    if sys.platform == "linux" and os.geteuid() == 0:
        options = child_process_options()
        for path in directories:
            if (
                path.is_symlink()
                or not path.is_dir()
                or path.stat().st_uid != options["user"]
            ):
                raise RuntimeError("CRAWLER_CHILD_STATE_NOT_PROVISIONED")
    else:
        for path in directories:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = {
        name: os.environ[name]
        for name in ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR")
        if name in os.environ
    }
    home = state / "home"
    result.update(
        {
            "HOME": str(home),
            "XDG_CACHE_HOME": str(state / "cache"),
            "TMPDIR": str(state / "tmp"),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "CRAWLER_RESTRICTED_CHILD": "1",
            "CRAWLER_CAMOUFOX_STATE_ROOT": str(state),
            "CRAWLER_BROWSER_PROFILE_ROOT": str(settings.crawler_browser_profile_root),
            "CRAWLER_CAMOUFOX_BROWSER_VERSION": settings.crawler_camoufox_browser_version,
            "CRAWLER_LOGIN_V2_PREPARATION_TIMEOUT_SECONDS": str(
                settings.crawler_login_v2_preparation_timeout_seconds
            ),
            "MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION": settings.crawler_camoufox_browser_version,
            "MEDIACRAWLER_DIR": str(settings.media_crawler_dir.resolve()),
            "XHS_AUDIT_DATA_DIR": str(state / "outputs"),
            "XHS_AUDIT_OUTPUTS_DIR": str(state / "outputs"),
            "CRAWLER_LOGIN_DIAGNOSTICS_DIR": str(
                state / "outputs" / "login-diagnostics"
            ),
            "PYTHONPATH": os.pathsep.join(
                (str(settings.root_dir), str(settings.media_crawler_dir.resolve()))
            ),
        }
    )
    if settings.crawler_camoufox_executable:
        result["MEDIACRAWLER_CAMOUFOX_EXECUTABLE"] = (
            settings.crawler_camoufox_executable
        )
    if settings.crawler_camoufox_executable_sha256:
        result["MEDIACRAWLER_CAMOUFOX_EXECUTABLE_SHA256"] = (
            settings.crawler_camoufox_executable_sha256
        )
    # Each caller adds only protocol-owned, non-secret configuration. Inherited
    # HTTP_PROXY/ALL_PROXY, cloud keys and App auth snapshots are not copied.
    for name, value in (values or {}).items():
        if name.startswith("MEDIACRAWLER_") or name in {
            "CRAWLER_LOGIN_PREPARATION_DIAGNOSTICS_DIR",
            "CRAWLER_LOGIN_DIAGNOSTICS_DIR",
            "CRAWLER_LOGIN_V2_RESOURCE_POLICY",
            "CRAWLER_LOGIN_BROWSER_VERSION",
        }:
            result[name] = str(value)
    result.pop("MEDIACRAWLER_ACCOUNT_AUTH_STATE", None)
    result.pop("MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64", None)
    result.pop("MEDIACRAWLER_CLOAK_PROFILE_ROOT", None)
    return result


def child_process_options():
    if sys.platform != "linux":
        return {}
    uid, gid = settings.crawler_child_uid, settings.crawler_child_gid
    if not uid and not gid and os.geteuid() != 0:
        return {"umask": 0o077}
    if (
        not uid
        or not gid
        or not uid.isdecimal()
        or not gid.isdecimal()
        or int(uid) == 0
        or int(gid) == 0
    ):
        raise RuntimeError("CRAWLER_CHILD_IDENTITY_REQUIRED")
    return {"user": int(uid), "group": int(gid), "extra_groups": [], "umask": 0o077}


def require_browser_available(
    account_id, *, store=None, owner_id=None, migration_id=None, operation_id=None
):
    from .crawler_account_store import crawler_account_store
    from .crawler_browser_migration import get_browser_migration_service

    accounts = store or crawler_account_store
    service = get_browser_migration_service(accounts)
    service.guard(
        account_id,
        owner_id=owner_id,
        migration_id=migration_id,
        operation_id=operation_id,
    )
    if settings.crawler_browser_maintenance and not service.authorized_login(
        account_id,
        owner_id=owner_id,
        migration_id=migration_id,
        operation_id=operation_id,
    ):
        raise RuntimeError("CRAWLER_BROWSER_MAINTENANCE")
    return service


def authority_for(bundle, **options):
    if bundle is None or bundle.snapshot is None:
        raise RuntimeError("CRAWLER_AUTHORITY_ROUTE_REQUIRED")
    from .crawler_request_authority import (
        open_authority,
        parent_authority_renew_ticket,
        parent_authority_ticket,
    )
    from .crawler_route import parent_account_policy, probe_env

    env = probe_env(bundle)
    configured = {
        "echo_url": settings.crawler_proxy_echo_url,
        "budget_id": env.get("MEDIACRAWLER_PROXY_PROBE_BUDGET_ID", ""),
        "max_calls": settings.crawler_proxy_probe_budget,
        "ttl": settings.crawler_proxy_probe_ttl,
        "trial_id": getattr(settings, "crawler_proxy_trial_id", ""),
        "reuse_existing_policy": True,
        "account_policy": parent_account_policy(bundle),
        "initial_ack": (lambda: parent_authority_ticket(bundle))
        if bundle.snapshot.managed
        else None,
        "lease_renewer": (lambda prior: parent_authority_renew_ticket(bundle, prior))
        if bundle.snapshot.managed
        else None,
        **options,
    }
    handle = open_authority(
        bundle.snapshot,
        crawler_root=settings.media_crawler_dir,
        session_options=configured,
    )
    accounts = getattr(bundle, "account_store", None)
    account = accounts.get(bundle.account_id) if accounts else None
    if (
        not account
        or account.get("profile_engine") != "camoufox"
        or not account.get("profile_generation")
    ):
        handle.close()
        raise RuntimeError("CRAWLER_PROFILE_MIGRATION_REQUIRED")
    handle.env["MEDIACRAWLER_CAMOUFOX_PROFILE_GENERATION"] = account[
        "profile_generation"
    ]
    return handle
