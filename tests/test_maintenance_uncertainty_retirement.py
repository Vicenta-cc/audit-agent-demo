from dataclasses import replace
import sqlite3
import time
from types import SimpleNamespace

import pytest

from backend.audit_agent import crawler_route as app
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore


@pytest.mark.parametrize('invalid',['none','changed_exit','changed_config','changed_binding','stale','expired','unknown_exit','typed','pause'])
def test_parent_retirement_compares_fresh_proof_and_current_store_without_resetting_history(tmp_path,monkeypatch,invalid):
    monkeypatch.setattr(app.settings,'crawler_proxy_enabled',True)
    monkeypatch.setattr(app.settings,'request_scheduler_db',tmp_path/'gate.db')
    accounts=CrawlerAccountStore(tmp_path/'accounts.db');proxies=CrawlerProxyStore(accounts)
    account=accounts.create(platform='dy',display_name='fixture')
    proxy=proxies.create(display_name='fixture',endpoint='http://127.0.0.1:9',expires_at=time.time()+600)
    proxies.bind(account['id'],proxy['id'],expected_binding_revision=account['binding_revision'])
    proxies.observe_exit(proxy['id'],'203.0.113.10')
    snapshot=app.resolve_account_route(account['id'],account_store=accounts,allow_probe=True)
    receipt=app.route_contract().ProbeReceipt(snapshot.proxy_id,1,'verified-context','203.0.113.10',time.time())
    code='PROXY_AUTH_FAILED' if invalid=='typed' else 'PROXY_UNAVAILABLE'
    with sqlite3.connect(snapshot.scheduler_db_path) as db:
        db.execute('CREATE TABLE collection_route_health(proxy_id TEXT,config_revision INTEGER,stop_code TEXT)')
        db.execute('INSERT INTO collection_route_health VALUES(?,?,?)',(snapshot.proxy_id,1,code))
        db.execute('INSERT INTO collection_route_health VALUES(?,?,?)',(snapshot.proxy_id,2,'PROXY_UNAVAILABLE'))
    if invalid=='changed_exit':receipt=replace(receipt,ip='203.0.113.11')
    elif invalid=='stale':receipt=replace(receipt,observed_at=time.time()-301)
    elif invalid=='expired':snapshot=replace(snapshot,expires_at=time.time()-1)
    elif invalid=='unknown_exit':snapshot=replace(snapshot,observed_exit_ip=None)
    with accounts._connect() as db:
        if invalid=='changed_config':db.execute('UPDATE proxy_resources SET config_revision=2')
        elif invalid=='changed_binding':db.execute('UPDATE crawler_accounts SET binding_revision=binding_revision+1')
        elif invalid=='pause':db.execute("UPDATE proxy_resources SET pause_reason='ACCOUNT_CHALLENGE'")
    app._retire_verified_probe_uncertainty(SimpleNamespace(snapshot=snapshot,account_store=accounts),receipt)
    with sqlite3.connect(snapshot.scheduler_db_path) as db:
        rows=db.execute('SELECT config_revision,stop_code FROM collection_route_health ORDER BY config_revision').fetchall()
    assert rows==([(2,'PROXY_UNAVAILABLE')] if invalid=='none' else [(1,code),(2,'PROXY_UNAVAILABLE')])
