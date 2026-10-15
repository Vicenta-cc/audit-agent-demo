"""Finite identity verification after real-page navigation; no browser/network."""

import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest


@pytest.mark.parametrize("outcome", ["delayed", "wrong", "challenge", "missing"])
def test_reopened_profile_waits_for_self_identity_and_keeps_security_fences(
    tmp_path, monkeypatch, outcome
):
    from scripts import crawler_douyin_login_v2 as module

    monkeypatch.setenv("CRAWLER_LOGIN_V2_PREPARATION_TIMEOUT_SECONDS", "60")

    async def run():
        clock = [0.0]
        opens = [0]
        reuse_start = [None]
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

        async def sleep(seconds):
            clock[0] += seconds

        monkeypatch.setattr(module.asyncio, "sleep", sleep)
        left, right = socket.socketpair()
        helper = module.DouyinLoginV2(
            SimpleNamespace(
                operation_id="op",
                media_fd=left.detach(),
                intent="check_or_login",
                entry="home",
                expected_platform_account_id="verified-original-self",
            )
        )

        async def open_context():
            opens[0] += 1

        async def navigate(entry):
            if opens[0] == 2:
                reuse_start[0] = clock[0]

        async def observe():
            return {
                "phase": "challenge_required"
                if opens[0] == 2 and outcome == "challenge"
                else "preparing"
            }

        async def trusted():
            if opens[0] == 1:
                return "verified-original-self"
            if outcome == "wrong":
                return "someone-else"
            if outcome == "delayed" and clock[0] - reuse_start[0] >= 26:
                return "verified-original-self"
            return ""

        helper.profile = {"directory": tmp_path}
        helper.adapter = SimpleNamespace(
            navigate=navigate,
            observe=observe,
            has_auth=AsyncMock(return_value=True),
            trusted_identity=trusted,
            identity="",
            identity_source="",
        )
        helper.context = SimpleNamespace(
            storage_state=AsyncMock(return_value={"cookies": [], "origins": []})
        )
        monkeypatch.setattr(
            module,
            "export_douyin_auth_state",
            AsyncMock(return_value={"cookies": [], "origins": []}),
        )
        helper.open_context = open_context
        helper.close_context = AsyncMock()
        helper.handshake = AsyncMock()
        helper.abort_gesture = AsyncMock()
        helper.emit = lambda *a, **k: None
        helper.publish = lambda *a, **k: None
        helper.media.auth = AsyncMock()
        try:
            if outcome == "delayed":
                await helper.flow()
                assert 29 <= clock[0] - reuse_start[0] < 30
                assert helper.media.auth.await_args.args[0]["reuse_verified"]
            else:
                with pytest.raises(ValueError, match="REUSE_CHECK_FAILED"):
                    await helper.flow()
                helper.media.auth.assert_not_awaited()
                if outcome == "missing":
                    assert 60 <= clock[0] - reuse_start[0] < 61
            assert opens[0] == 2 and not helper.display_fallback_used
        finally:
            helper.media.close()
            right.close()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["delayed", "wrong", "challenge", "missing"])
def test_login_maintenance_probe_uses_same_finite_identity_budget(monkeypatch, outcome):
    from scripts import crawler_proxy_probe as module

    monkeypatch.setenv("CRAWLER_LOGIN_V2_PREPARATION_TIMEOUT_SECONDS", "60")

    async def run():
        clock = [0.0]
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

        async def sleep(seconds):
            clock[0] += seconds

        monkeypatch.setattr(module.asyncio, "sleep", sleep)
        adapter = SimpleNamespace(
            identity_source="douyin_self_response", identity_conflict=False, identity=""
        )

        async def observe():
            if clock[0] >= 26:
                adapter.identity = (
                    "someone-else"
                    if outcome == "wrong"
                    else "original"
                    if outcome == "delayed"
                    else ""
                )
            return {
                "phase": "challenge_required" if outcome == "challenge" else "preparing"
            }

        adapter.observe_once = observe
        result = await module.observe_login_identity(adapter, "original")
        assert result == (
            "success"
            if outcome == "delayed"
            else "challenge"
            if outcome == "challenge"
            else "uncertain"
        )
        assert clock[0] == (
            26
            if outcome in {"delayed", "wrong"}
            else 0
            if outcome == "challenge"
            else 60
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "identity,conflict,phase,expected_result",
    [
        ("someone-else", False, "preparing", "uncertain"),
        ("original", True, "preparing", "uncertain"),
        ("someone-else", True, "challenge_required", "challenge"),
    ],
)
def test_probe_rejects_observed_identity_mismatch_immediately_without_risk_mutation(
    monkeypatch, identity, conflict, phase, expected_result
):
    from scripts import crawler_proxy_probe as module

    monkeypatch.setenv("CRAWLER_LOGIN_V2_PREPARATION_TIMEOUT_SECONDS", "120")

    async def run():
        adapter = SimpleNamespace(
            identity=identity,
            identity_source="douyin_self_response",
            identity_conflict=conflict,
            observe_once=AsyncMock(return_value={"phase": phase}),
        )

        async def no_wait(_):
            raise AssertionError(
                "known mismatch must not consume the identity waiting budget"
            )

        monkeypatch.setattr(module.asyncio, "sleep", no_wait)
        assert (
            await module.observe_login_identity(adapter, "original") == expected_result
        )
        adapter.observe_once.assert_awaited_once()
        assert adapter.identity == identity and adapter.identity_conflict == conflict

    asyncio.run(run())


@pytest.mark.asyncio
async def test_probe_mismatched_self_emits_uncertain_not_success_or_risk_clear(
    tmp_path, monkeypatch, capsys
):
    import json
    import sys
    from unittest.mock import Mock
    from scripts import crawler_proxy_probe as module
    from scripts import crawler_douyin_login_adapter as adapter_module

    snapshot = SimpleNamespace(
        account_id="fixture-account",
        proxy_id="fixture-proxy",
        binding_revision=1,
        config_revision=1,
        recovery_claim="",
    )
    context = SimpleNamespace(
        new_page=AsyncMock(return_value=object()), close=AsyncMock()
    )
    risk = Mock()
    receipt = SimpleNamespace(
        context_id="fixture-context", ip="198.51.100.2", observed_at=1.0
    )

    class Session:
        def __init__(self, *args, **kwargs):
            self.receipt = receipt

        async def close(self):
            pass

    adapter = SimpleNamespace(
        navigate=AsyncMock(),
        observe_once=AsyncMock(return_value={"phase": "preparing"}),
        identity_source="douyin_self_response",
        identity_conflict=False,
        identity="different-account",
        close=AsyncMock(),
    )
    adapter.install = AsyncMock(return_value=adapter)
    monkeypatch.setenv("MEDIACRAWLER_DIR", str(tmp_path))
    monkeypatch.setenv("MEDIACRAWLER_CAMOUFOX_PROFILE_ROOT", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "tools.collection_route",
        SimpleNamespace(load_route_from_env=lambda **kwargs: snapshot),
    )
    monkeypatch.setitem(
        sys.modules,
        "tools.bound_proxy",
        SimpleNamespace(BoundProxySession=Session, emit_account_risk=risk),
    )
    monkeypatch.setitem(
        sys.modules,
        "tools.camoufox_browser",
        SimpleNamespace(launch_account_context=AsyncMock(return_value=(context, {}))),
    )
    monkeypatch.setattr(
        adapter_module, "DouyinLoginAdapter", lambda *args, **kwargs: adapter
    )
    await module._probe(
        "fixture-account", check_login=True, expected_identity="original-account"
    )
    result = json.loads(capsys.readouterr().out.split("PROXY_PROBE_RESULT ", 1)[1])
    assert result["login_result"] == "uncertain"
    risk.assert_not_called()
    context.close.assert_awaited_once()
    assert (
        adapter.identity == "different-account"
    )  # No mutation/clearing to manufacture a match.
