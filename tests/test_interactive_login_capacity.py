from contextlib import ExitStack, closing

import pytest

from backend.audit_agent import crawler_route as route
from backend.task_admission.resources import acquire_account_handle
from test_camoufox_direct_route import direct


@pytest.fixture
def runtime(direct, tmp_path, monkeypatch):
    accounts, collecting = direct
    monkeypatch.setattr(route.settings, "task_resource_lock_dir", tmp_path / "locks")
    monkeypatch.setattr(route.settings, "request_concurrency", 1)
    monkeypatch.setattr(route.settings, "crawler_shared_exit_active_limit", 1)
    accounts.observe_shared_exit("203.0.113.9", service_exit=True)
    accounts.save_auth_state(collecting["id"], "fixture-authority")
    login = accounts.create(platform="dy", display_name="Login")["id"]
    third = accounts.create(platform="dy", display_name="Other")["id"]
    accounts.save_auth_state(third, "fixture-authority")
    return accounts, collecting["id"], login, third


def test_direct_login_and_collection_each_have_one_slot(runtime):
    accounts, collecting, login, third = runtime
    with ExitStack() as children:
        def start(account_id, operation, *, interactive=False, **kw):
            if interactive:
                handle = children.enter_context(closing(acquire_account_handle("dy", account_id)))
                kw.update(human_login_operation_id=operation, account_handle=handle, allow_probe=True, interactive_intent=True)
            return children.enter_context(closing(route.acquire_route_execution(
                account_id, operation, "owner", account_store=accounts, **kw)))

        crawl = start(collecting, "collection")
        manual = start(login, "login", interactive=True)
        assert manual.snapshot.login_bootstrap and manual.snapshot.managed
        from backend.audit_agent.crawler_child_runtime import authority_for
        from backend.task_admission.resources import ResourceBusy
        with pytest.raises(RuntimeError, match="CRAWLER_AUTHORITY_LOGIN_ONLY"):
            authority_for(manual)
        with pytest.raises(ResourceBusy):
            with manual.borrow():
                pytest.fail("Interactive routes cannot be borrowed for asset collection")
        assert not manual.snapshot.recovery_claim and manual.manual_recovery_claim
        route.require_route_renewal(manual)
        assert not crawl.snapshot.login_bootstrap
        assert acquire_account_handle("dy", login) is None
        assert acquire_account_handle("dy", collecting) is None
        with pytest.raises(route.CollectionRouteError, match="PROXY_BUSY"):
            start(third, "automatic", allow_probe=True)
        with pytest.raises(route.CollectionRouteError, match="PROXY_BUSY"):
            start(third, "other-login", interactive=True)
        crawl.close()
        # Even while login is running, a new collection can take the freed slot.
        start(collecting, "next-collection")
        with accounts._connect() as db:
            db.execute("UPDATE crawler_shared_exits SET pause_reason='ACCOUNT_CHALLENGE'")
        with pytest.raises(route.CollectionRouteError, match="PROXY_UNAVAILABLE"):
            route.require_route_renewal(manual)


@pytest.mark.parametrize("reason", ["paused", "transport_blocked", "recovery", "migration"])
def test_only_ordinary_login_on_healthy_direct_route_can_overlap(runtime, reason):
    accounts, collecting, login, _ = runtime
    with closing(route.acquire_route_execution(collecting, "active", "owner", account_store=accounts)):
        if reason == "paused":
            with accounts._connect() as db:
                db.execute("UPDATE crawler_shared_exits SET pause_reason='ACCOUNT_CHALLENGE'")
        if reason == "transport_blocked":
            with accounts._connect() as db:
                db.execute("UPDATE crawler_accounts SET route_state='transport_blocked' WHERE id=?", (login,))
        with closing(acquire_account_handle("dy", login)) as profile:
            with pytest.raises(route.CollectionRouteError, match="PROXY_BUSY"):
                route.acquire_route_execution(
                    login, "login", "owner", account_store=accounts, account_handle=profile,
                    allow_probe=True, human_login_operation_id="login", interactive_intent=True,
                    recovery_claim="claim" if reason == "recovery" else None,
                    migration_id="migration" if reason == "migration" else None,
                )


@pytest.mark.parametrize("observation", ["first", "stale"])
def test_manager_starts_login_during_collection_after_echo(runtime, tmp_path, monkeypatch, observation):
    import time
    from types import SimpleNamespace

    from backend.audit_agent import crawler_child_runtime
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager
    from test_crawler_login_v2_manager import HELPER, start

    accounts, collecting, login, _ = runtime
    with closing(route.acquire_route_execution(collecting, "active", "owner", account_store=accounts)):
        with accounts._connect() as db:
            if observation == "stale":
                db.execute("UPDATE crawler_shared_exits SET observed_at=0")
            else:
                db.execute("DELETE FROM crawler_shared_exits")
        checked = []
        def probe(bundle):
            assert bundle.interactive_login and not bundle.snapshot.managed
            checked.append(bundle.execution_id)
            return route.route_contract().ProbeReceipt("service-direct", 1, "echo", "203.0.113.9", time.time())

        monkeypatch.setattr(route, "_run_maintenance_probe", probe)
        def login_authority(bundle, **options):
            assert options == {"login_bootstrap": True}
            assert bundle.snapshot.managed and bundle.snapshot.login_bootstrap
            return SimpleNamespace(env={}, pass_fds=(), bind_child=lambda pid: None, close=lambda: None)

        monkeypatch.setattr(crawler_child_runtime, "authority_for", login_authority)
        helper = tmp_path / "login_helper.py"
        helper.write_text(HELPER)
        import sys
        manager = CrawlerLoginV2Manager(accounts, AuthStateCipher("fixture"), python_path=sys.executable,
                                       helper_path=helper)
        try:
            snapshot = start(manager, accounts.get(login))
            assert checked == [snapshot["operation_id"]]
            assert manager.sessions[snapshot["id"]].process.pid > 0
        finally:
            manager.shutdown()


def test_recovery_login_uses_only_its_pinned_healthy_exit_and_owner(runtime):
    from backend.audit_agent.account_rotation import AccountRotationManager

    accounts, collecting, _, challenged = runtime
    with closing(route.acquire_route_execution(challenged, "first", "owner", account_store=accounts)):
        AccountRotationManager(accounts).cool_down_after_risk(challenged)
    claim = accounts.begin_manual_recovery(challenged, "login", owner_id="owner")
    assert claim
    with closing(route.acquire_route_execution(collecting, "active", "owner", account_store=accounts)):
        with closing(acquire_account_handle("dy", challenged)) as profile:
            def login(operation="login", owner="owner"):
                return route.acquire_route_execution(challenged, operation, owner, account_handle=profile,
                    account_store=accounts, recovery_claim=claim, human_login_operation_id=operation,
                    allow_probe=True, interactive_intent=True)
            for bad_operation, bad_owner in [("other", "owner"), ("login", "other")]:
                with pytest.raises(route.CollectionRouteError, match="PROXY_BUSY"):
                    login(bad_operation, bad_owner)
            with closing(login()) as session:
                assert session.interactive_login and session.snapshot.login_bootstrap
                assert not session.snapshot.recovery_claim
                route.require_route_renewal(session)
            accounts.finish_manual_recovery(challenged, "login", claim, "failed", owner_id="owner")
            assert accounts.get(challenged)["route_state"] == "manual_required"
            claim = accounts.begin_manual_recovery(challenged, "retry", owner_id="owner")
            assert claim
            with closing(login("retry")) as retried:
                assert retried.interactive_login
                route.require_route_renewal(retried)
            accounts.observe_shared_exit("203.0.113.11", service_exit=True)
            with pytest.raises(route.CollectionRouteError, match="PROXY_BUSY"):
                login("retry")


def test_login_child_challenge_reads_are_deduplicated_and_keep_manual_context(runtime):
    import os
    import subprocess
    import sys

    accounts, collecting, login, _ = runtime
    script = """
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from scripts.crawler_douyin_login_adapter import DouyinLoginAdapter
from tools.bound_proxy import initialize_bound_route, obtain_route_ack

async def observe():
    snapshot = initialize_bound_route()
    obtain_route_ack(snapshot)
    page = SimpleNamespace(evaluate=AsyncMock())
    adapter = SimpleNamespace(page=page, bound_proxy_session=SimpleNamespace(snapshot=snapshot),
        region=AsyncMock(return_value={'width': 100}), region_signature=AsyncMock(return_value='challenge'))
    for _ in range(2):
        assert (await DouyinLoginAdapter.observe_once(adapter))['phase'] == 'challenge_required'
asyncio.run(observe())
"""
    with ExitStack() as stack:
        for account_id, op in [(collecting, "collect"), (login, "login")]:
            kwargs = {}
            if account_id == login:
                handle = stack.enter_context(closing(acquire_account_handle("dy", login)))
                kwargs.update(account_handle=handle, allow_probe=True, human_login_operation_id=op, interactive_intent=True)
            bundle = stack.enter_context(closing(route.acquire_route_execution(
                account_id, op, "owner", account_store=accounts, **kwargs)))
        config = stack.enter_context(closing(route.RouteConfigPipe(bundle.snapshot)))
        receipts = stack.enter_context(closing(route.RouteReceiptPipe(bundle)))
        crawler = str(route.settings.media_crawler_dir)
        env = {**os.environ, **config.env, **receipts.env, "MEDIACRAWLER_ACCOUNT_ID": login,
               "PYTHONPATH": os.pathsep.join([str(route.settings.root_dir), crawler])}
        with subprocess.Popen([os.environ.get("LOGIN_BROWSER_TEST_PYTHON", sys.executable), "-c", script],
                              env=env, cwd=crawler, pass_fds=(*config.pass_fds, *receipts.pass_fds),
                              stderr=subprocess.PIPE, start_new_session=True) as child:
            config.start(child)
            receipts.start(child)
            _, error = child.communicate(timeout=12)
            assert child.returncode == 0, error
        receipts.drain()
        assert accounts.get(login)["risk_count"] == 1
        assert accounts.get(login)["recovery_state"] == "manual_claimed"
        assert not accounts.direct_exit_paused()
        route.require_route_renewal(bundle)


@pytest.mark.parametrize("echo_only", [True, False])
def test_login_authority_socket_cannot_grant_collection(runtime, echo_only):
    import os
    import subprocess
    import sys
    from backend.audit_agent.crawler_child_runtime import authority_for

    accounts, _, login, _ = runtime
    with ExitStack() as stack:
        with accounts._connect() as db:
            db.execute("UPDATE crawler_accounts SET profile_engine='camoufox',profile_generation='test-generation' WHERE id=?", (login,))
        profile = stack.enter_context(closing(acquire_account_handle("dy", login)))
        bundle = stack.enter_context(closing(route.acquire_route_execution(login, "login", "owner",
            account_store=accounts, account_handle=profile, allow_probe=True, human_login_operation_id="login", interactive_intent=True)))
        if echo_only:
            # During the startup echo, the owner has not issued a managed ticket yet.
            original = bundle.snapshot
            bundle.snapshot = route.resolve_account_route(login, account_store=accounts, allow_probe=True)
            stack.callback(setattr, bundle, "snapshot", original)
        authority = stack.enter_context(closing(authority_for(bundle, **({"maintenance_only": True} if echo_only else {"login_bootstrap": True}))))
        config = stack.enter_context(closing(route.RouteConfigPipe(bundle.snapshot)))
        receipts = stack.enter_context(closing(route.RouteReceiptPipe(bundle))) if not echo_only else None
        script = """
import os, time
from tools.bound_proxy import initialize_bound_route, obtain_route_ack
from tools.request_authority import AuthorityClient, REQUEST_KINDS
from tools.collection_route import CollectionRouteError
snap = initialize_bound_route()
echo = not snap.managed
if not echo:
    obtain_route_ack(snap)
client = AuthorityClient(snap)
def denied(op, payload=None):
    try:
        client.call(op, payload)
    except CollectionRouteError as exc:
        assert exc.code == 'PROXY_UNAVAILABLE', exc.code
    else:
        raise AssertionError(op + ' accepted: ' + str(payload))
client.call('initialize')
for op in ['cache_lookup', 'trial_snapshot', 'trial_operation', 'trial_begin_media']:
    denied(op)
client.call('probe_reserve', {'category':'open', 'use_remaining':False})
if echo:
    denied('make_gate')
    denied('reserve', {'operation':'login_navigation'})
else:
    receipt=dict(proxy_id=snap.proxy_id, config_revision=snap.config_revision, context_id=snap.context_id,
                 ip=snap.observed_exit_ip, observed_at=time.time(), exit_generation=snap.exit_generation)
    client.call('probe_confirm', {'ip':snap.observed_exit_ip})
    client.call('make_gate', {'receipt':receipt})
    for kind in REQUEST_KINDS - {'login_action','login_navigation'}:
        denied('reserve', {'operation':kind, 'lease_seconds':10})
    for kind in ['login_navigation', 'login_action']:
        result = client.call('reserve', {'operation':kind, 'lease_seconds':10})
        assert 'delay' in result
        if result['delay'] == 0:
            client.call('consume', {'grant':result['grant']})
            client.call('release', {'scope_tokens':result['scope_tokens']})
client.close()
"""
        crawler = str(route.settings.media_crawler_dir)
        env = {**os.environ, **config.env, **authority.env, **(receipts.env if receipts else {}),
               "MEDIACRAWLER_ACCOUNT_ID": login, "PYTHONPATH": crawler}
        passed = (*authority.pass_fds, *config.pass_fds, *(receipts.pass_fds if receipts else ()))
        with subprocess.Popen([sys.executable, "-c", script], cwd=crawler, env=env, pass_fds=passed,
                              stderr=subprocess.PIPE, start_new_session=True) as child:
            authority.bind_child(child.pid)
            config.start(child)
            if receipts:
                receipts.start(child)
            _, err = child.communicate(timeout=12)
            assert child.returncode == 0, err.decode()
