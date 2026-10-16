"""Echo-only maintenance probe, executed by the configured crawler interpreter."""

import argparse
import asyncio
import json
import math
import time
import os
from pathlib import Path
import sys

_diagnostic_emitted = False


def emit_probe_diagnostic(exc, stage="unknown"):
    """Fixed diagnostic enums only; exception messages and paths stay private."""
    global _diagnostic_emitted
    if _diagnostic_emitted:
        return
    stages = {
        "unknown",
        "browser_startup",
        "browser_echo",
        "http_echo",
        "login_check",
        "cleanup",
    }
    stage = stage if stage in stages else "unknown"
    error_class, category = "unknown", "unknown"
    inferred_stage = None
    probe_frame = False
    current = exc
    seen = set()
    for _ in range(16):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if stage != "cleanup":
            frame = current.__traceback__
            while frame:
                if Path(frame.tb_frame.f_code.co_filename).name == "bound_proxy.py":
                    if frame.tb_frame.f_code.co_name in {
                        "open_context",
                        "_probe",
                        "_probe_locked",
                    }:
                        probe_frame = True
                    if (
                        inferred_stage is None
                        and frame.tb_frame.f_code.co_name == "_browser_sample"
                    ):
                        inferred_stage = "browser_echo"
                    elif (
                        inferred_stage is None
                        and frame.tb_frame.f_code.co_name == "_http_sample"
                    ):
                        inferred_stage = "http_echo"
                frame = frame.tb_next
        cls = type(current)
        if (
            isinstance(current, TimeoutError)
            or cls.__module__.startswith("playwright.")
            and cls.__name__ == "TimeoutError"
        ):
            if category == "unknown":
                error_class, category = "timeout", "timeout"
        elif cls.__module__ == "httpx" or cls.__module__.startswith("httpx."):
            if category == "unknown":
                error_class = "httpx"
        elif cls.__module__.startswith("playwright."):
            if category == "unknown":
                error_class = "playwright"
        elif getattr(current, "code", None) in {
            "PROXY_AUTH_FAILED",
            "PROXY_CONNECTION_REFUSED",
            "PROXY_TRANSPORT_FAILED",
        }:
            if category == "unknown":
                error_class = "route"
                category = (
                    "proxy_auth" if current.code == "PROXY_AUTH_FAILED" else "transport"
                )
        elif error_class == "unknown" and cls.__module__ == "builtins":
            error_class = (
                "os"
                if isinstance(current, OSError)
                else "runtime"
                if isinstance(current, RuntimeError)
                else "value"
                if isinstance(current, ValueError)
                else "unknown"
            )
        elif (
            error_class == "unknown"
            and cls.__module__ == "tools.collection_route"
            and cls.__name__ == "CollectionRouteError"
        ):
            error_class = "route"
        current = current.__cause__ or current.__context__
    if inferred_stage is not None:
        stage = inferred_stage
    elif probe_frame and stage == "browser_startup":
        stage = "unknown"
    _diagnostic_emitted = True
    print(
        "PROXY_PROBE_DIAGNOSTIC "
        + json.dumps(
            {"stage": stage, "error_class": error_class, "category": category},
            allow_nan=False,
        ),
        flush=True,
    )


async def observe_login_identity(adapter, expected_identity):
    """Bounded first-party identity observation after navigation has completed."""
    budget = float(os.getenv("CRAWLER_LOGIN_V2_PREPARATION_TIMEOUT_SECONDS", "120"))
    if not math.isfinite(budget):
        raise ValueError("LOGIN_PREPARATION_TIMEOUT_INVALID")
    deadline = time.monotonic() + min(180, max(15, budget))
    while True:
        observation = await adapter.observe_once()
        if observation.get("phase") in {
            "challenge_required",
            "manual_required",
            "sms_required",
        }:
            return "challenge"
        # A contradictory observation is decisive, not a pending response.
        # Never wait out the budget or treat another account as recoverable.
        if adapter.identity_conflict:
            return "uncertain"
        if adapter.identity_source == "douyin_self_response" and adapter.identity:
            return (
                "success"
                if expected_identity and adapter.identity == expected_identity
                else "uncertain"
            )
        if time.monotonic() >= deadline:
            return "uncertain"
        await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))


async def _probe(
    account_id, *, check_login=False, expected_identity="", hold_context=False
):
    crawler_root = Path(os.environ["MEDIACRAWLER_DIR"]).resolve()
    sys.path.insert(0, str(crawler_root))
    from tools.collection_route import load_route_from_env

    snapshot = load_route_from_env(account_id=account_id, proxy_required=True)
    from tools.bound_proxy import BoundProxySession
    from tools.camoufox_browser import launch_account_context

    options = {}
    if hold_context:
        options = dict(
            reuse_existing_policy=True,
            account_policy=json.loads(os.environ["MEDIACRAWLER_ASSET_ACCOUNT_POLICY"]),
        )
    session = await asyncio.to_thread(
        BoundProxySession,
        snapshot,
        maintenance_only=not check_login and not hold_context,
        **options,
    )
    context = None
    stage = "browser_startup"
    try:
        context, _ = await launch_account_context(
            account_id,
            Path(os.environ["MEDIACRAWLER_CAMOUFOX_PROFILE_ROOT"]),
            headless=sys.platform == "darwin",
            route_snapshot=snapshot,
            bound_proxy_session=session,
        )
        receipt = session.receipt
        login_result = "uncertain"
        if check_login:
            stage = "login_check"
            from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter

            page = await context.new_page()
            adapter = await DouyinLoginAdapter(
                context, page, block_media=True, bound_proxy_session=session
            ).install()
            try:
                await adapter.navigate("self")
                login_result = await observe_login_identity(adapter, expected_identity)
                if login_result == "challenge" and not snapshot.recovery_claim:
                    from tools.bound_proxy import emit_account_risk

                    emit_account_risk(snapshot)
            finally:
                await adapter.close()
        print(
            "PROXY_PROBE_RESULT "
            + json.dumps(
                dict(
                    schema_version=1,
                    account_id=snapshot.account_id,
                    proxy_id=snapshot.proxy_id,
                    binding_revision=snapshot.binding_revision,
                    config_revision=snapshot.config_revision,
                    context_id=receipt.context_id,
                    ip=receipt.ip,
                    observed_at=receipt.observed_at,
                    **({"login_result": login_result} if check_login else {}),
                ),
                separators=(",", ":"),
                allow_nan=False,
            ),
            flush=True,
        )
        if hold_context:
            command = await asyncio.wait_for(
                asyncio.to_thread(sys.stdin.readline), 1200
            )
            if command.strip() not in {"close", ""}:
                raise ValueError("PROXY_CONFIG_INVALID")
    except Exception as exc:
        emit_probe_diagnostic(exc, stage)
        raise
    finally:
        try:
            if context is not None:
                await context.close()
            else:
                await session.close()
        except Exception as exc:
            emit_probe_diagnostic(exc, "cleanup")
            raise


async def probe(
    account_id, *, check_login=False, expected_identity="", hold_context=False
):
    from contextlib import nullcontext

    if sys.platform == "linux" and not os.environ.get("DISPLAY"):
        from scripts.crawler_interactive_login import virtual_display

        display = virtual_display()
    else:
        display = nullcontext()
    with display:
        return await _probe(
            account_id,
            check_login=check_login,
            expected_identity=expected_identity,
            hold_context=hold_context,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--check-login", action="store_true")
    parser.add_argument("--expected-identity", default="")
    parser.add_argument("--hold-context", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(
            probe(
                args.account_id,
                check_login=args.check_login,
                expected_identity=args.expected_identity,
                hold_context=args.hold_context,
            )
        )
    except Exception as exc:
        emit_probe_diagnostic(exc)
        allowed = {
            "PROXY_BUSY",
            "PROXY_REVISION_MISMATCH",
            "PROXY_EXIT_CHANGED",
            "PROXY_UNAVAILABLE",
            "PROXY_AUTH_FAILED",
            "PROXY_CONFIG_INVALID",
            "PROBE_BUDGET_EXHAUSTED",
            "SCHEDULER_POLICY_CONFLICT",
        }
        code = getattr(exc, "code", None)
        code = code if code in allowed else "PROXY_UNAVAILABLE"
        print("PROXY_PROBE_ERROR " + json.dumps({"code": code}), flush=True)
        raise SystemExit(1)
