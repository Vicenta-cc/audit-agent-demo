"""Exercise a managed direct route and its per-page comment grant over a real private socket."""
import asyncio
import os
import time
from contextlib import ExitStack

import pytest

from backend.audit_agent import crawler_route as app
from backend.audit_agent.crawler_child_runtime import authority_for
from tests.test_camoufox_direct_route import direct  # noqa: F401 - pytest fixture


def test_healthy_direct_collection_continues_across_ticket_renewals(direct, tmp_path, monkeypatch):
    store, account = direct
    clock = [time.time()]
    started = clock[0]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    monkeypatch.setattr(app.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    contract = app.route_contract()
    ip = '203.0.113.9'
    monkeypatch.setattr(app, '_run_maintenance_probe', lambda bundle: contract.ProbeReceipt(
        'service-direct', 1, 'probe', ip, time.time()))
    store.save_auth_state(account['id'], 'fixture-encrypted-state')

    from tools import bound_proxy
    from tools.request_authority import ENV_FD

    with ExitStack() as cleanup:
        bundle = app.acquire_route_execution(account['id'], 'continuous-crawl', 'owner', account_store=store)
        cleanup.callback(bundle.close)
        snap = bundle.snapshot
        ack = bundle.route_operation_store.ack(snap.operation_id, owner_id=snap.owner_id,
            lease_version=snap.lease_version, snapshot_digest=snap.snapshot_digest, context_id=snap.context_id)['ack']
        owner = authority_for(bundle)
        cleanup.callback(owner.close)
        # Connect both ends of the private socket in this process so they share the controlled clock.
        for name, value in owner.env.items():
            monkeypatch.setenv(name, str(os.dup(int(value))) if name == ENV_FD else value)
        owner.bind_child(os.getpid())
        session = bound_proxy.BoundProxySession(snap, echo_url='https://echo.invalid/ip', ack=ack)
        cleanup.callback(session.authority.close)
        session.context, session.context_id = object(), snap.context_id

        async def echo_sample():
            return ip

        def deliver_probe_receipt(_snapshot, receipt):
            store.observe_shared_exit(receipt.ip, observed_at=receipt.observed_at, service_exit=True)
            return True

        monkeypatch.setattr(session, '_browser_sample', echo_sample)
        monkeypatch.setattr(session, '_http_sample', echo_sample)
        monkeypatch.setattr(bound_proxy, 'emit_probe_receipt', deliver_probe_receipt)

        page_count = 80

        async def collect_pages():
            versions = []
            for page in range(page_count):
                if page:
                    clock[0] += session._next_probe_delay()
                await session._probe('open' if page == 0 else 'tick')
                async with session.slot('comments', max_wait=0):
                    ticket = session._lease_ack
                    assert 0 < ticket['expires_at'] - time.time() <= 300
                    versions.append(ticket['lease_version'])
            store.pause_account_exit(account['id'], until=time.time() + 300)
            with pytest.raises(bound_proxy.CollectionRouteError, match='PROXY_UNAVAILABLE'):
                async with session.slot('comments', max_wait=0):
                    pytest.fail('paused account was granted a new comment request')
            return versions

        assert asyncio.run(collect_pages()) == list(range(1, page_count + 1))
        assert clock[0] - started >= 6 * 60 * 60
