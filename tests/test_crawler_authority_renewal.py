"""Owner renewal uses the original persistent CAS, without a child receipt race."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from types import SimpleNamespace
import time

from backend.audit_agent.crawler_route import RouteOperation, RouteOperationStore
from backend.audit_agent.crawler_request_authority import parent_authority_renew_ticket


def test_concurrent_owner_renewal_commits_one_ticket_without_waiting_for_live_child(tmp_path,monkeypatch):
    from backend.audit_agent import crawler_child_runtime
    monkeypatch.setattr(crawler_child_runtime,'require_browser_available',lambda *args,**kwargs: None)
    clock=[time.time()]
    monkeypatch.setattr(time,'time',lambda:clock[0])
    store=RouteOperationStore(tmp_path/'scheduler.sqlite3')
    operation=RouteOperation.create(owner_id='owner',account_id='account',profile_key='profile',
        route_mode='proxy_required',proxy_id='proxy',expires_at=clock[0]+300,
        proxy_expires_at=clock[0]+1200,context_id='context',snapshot_digest='a'*64)
    store.issue(operation)
    store.ack(operation.operation_id,owner_id='owner',lease_version=1,
        snapshot_digest=operation.snapshot_digest,context_id='context')
    prior=store.ticket(operation.operation_id,owner_id='owner',snapshot_digest=operation.snapshot_digest,context_id='context')
    bundle=SimpleNamespace(snapshot=operation,owner_id='owner',account_id='account',execution_id='execution',
        route_operation_store=store,child_receipt_pipe=SimpleNamespace(process=SimpleNamespace(poll=lambda:None)))
    clock[0]+=275
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:parent_authority_renew_ticket(bundle,prior),range(2)))
    assert results[0]==results[1]
    assert results[0]['lease_version']==2 and results[0]['expires_at']==clock[0]+300
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM route_lease_commands').fetchone()[0]==1
