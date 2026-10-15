"""Offline App/Crawler operation handshake over real inherited pipes."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from backend.audit_agent import crawler_route as app


@pytest.fixture(autouse=True)
def isolated_managed_runtime(tmp_path, monkeypatch):
    """Real children must not touch the application's shared runtime paths."""
    crawler_root = os.environ.get('MEDIACRAWLER_DIR') or os.environ.get('MEDIA_CRAWLER_DIR')
    monkeypatch.setattr(app.settings, 'media_crawler_dir', Path(crawler_root) if crawler_root else Path(__file__).resolve().parents[2] / 'wukong-proxy-crawler')
    monkeypatch.setattr(app.settings, 'crawler_camoufox_state_root', tmp_path / 'child-state')
    monkeypatch.setattr(app.settings, 'crawler_browser_profile_root', tmp_path / 'logical-profiles')
    monkeypatch.setattr(app.settings, 'request_scheduler_db', tmp_path / 'scheduler.sqlite3')
    monkeypatch.setattr(app.settings, 'task_resource_lock_dir', tmp_path / 'resource-locks')


def managed(tmp_path, mode='proxy_preferred'):
    c = app.route_contract()
    scopes = c.derive_scope_ids('dy', 'a', 'p', '198.51.100.8')
    base = c.BoundRoute('dy', 'a', c.derive_profile_key('a'), 'p', 1, 1,
        'proxy.invalid', 8080, 'fixture', 'fixture', time.time()+120,
        '198.51.100.8', time.time(), str(tmp_path/'gate.db'), scopes,
        route_mode=mode, operation_id='op', lease_id='lease', lease_version=1,
        owner_id='owner', exit_generation=1, context_id='context')
    return replace(base, snapshot_digest=c.snapshot_digest(base))


def store_operation(store, snap):
    return store.issue(app.RouteOperation(operation_id=snap.operation_id, lease_id=snap.lease_id,
        lease_version=snap.lease_version, owner_id=snap.owner_id, account_id=snap.account_id,
        profile_key=snap.profile_key, route_mode=snap.route_mode, expires_at=snap.expires_at, observed_exit=snap.observed_exit_ip,
        context_id=snap.context_id, snapshot_digest=snap.snapshot_digest))


def test_real_subprocess_ack_is_bound_and_survives_delivery_delay(tmp_path):
    snap = managed(tmp_path)
    store = app.RouteOperationStore(tmp_path/'ops.db')
    store_operation(store, snap)
    bundle = SimpleNamespace(snapshot=snap, route_operation_store=store, _lock=threading.RLock())
    route_pipe, receipt_pipe = app.RouteConfigPipe(snap), app.RouteReceiptPipe(bundle)
    crawler = str(app.settings.media_crawler_dir.resolve())
    script = """import time
from tools.bound_proxy import initialize_bound_route, obtain_route_ack
s=initialize_bound_route(); ack=obtain_route_ack(s); time.sleep(.05)
s.require_managed_ack(ack)
print('ACK_OK', flush=True)
"""
    env = {**os.environ, **route_pipe.env, **receipt_pipe.env,
        'PYTHONPATH':crawler, 'MEDIACRAWLER_ACCOUNT_ID':'a', 'MEDIACRAWLER_PROXY_REQUIRED':'true'}
    child = subprocess.Popen(['/Users/hose/project/zhanyun/media-crawler/.venv/bin/python', '-c',script],
        env=env, cwd=crawler, pass_fds=(*route_pipe.pass_fds,*receipt_pipe.pass_fds),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        route_pipe.start(child); receipt_pipe.start(child)
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, err
        assert out.strip() == 'ACK_OK'
        assert store.ack('op', owner_id='owner', lease_version=1,
            snapshot_digest=snap.snapshot_digest, context_id='context')['state']=='acked'
    finally:
        if child.poll() is None: child.kill(); child.wait()
        route_pipe.close(); receipt_pipe.close()


@pytest.mark.parametrize('bad', [{}, {'snapshot_digest':'wrong'}, {'context_id':'wrong'}, {'lease_version':True}])
def test_ack_rejects_missing_or_conflicting_identity(tmp_path, bad):
    snap=managed(tmp_path); store=app.RouteOperationStore(tmp_path/'ops.db'); store_operation(store,snap)
    kwargs=dict(owner_id='owner',lease_version=1,snapshot_digest=snap.snapshot_digest,context_id='context')
    if not bad: kwargs.pop('snapshot_digest')
    kwargs.update(bad)
    with pytest.raises(ValueError): store.ack('op',**kwargs)


def test_transition_cannot_bypass_ack(tmp_path):
    snap=managed(tmp_path); store=app.RouteOperationStore(tmp_path/'ops.db'); store_operation(store,snap)
    with pytest.raises(ValueError): store.transition('op',state='acked',owner_id='owner',lease_version=1)
    store.transition('op',state='draining',owner_id='owner',lease_version=1)
    with pytest.raises(ValueError): store.transition('op',state='issued',owner_id='owner',lease_version=1)


@pytest.mark.parametrize('error', ['PROXY_UNAVAILABLE','ACCOUNT_AUTH_INVALID','ACCOUNT_CHALLENGE','HTTP_429'])
def test_ambiguous_or_risk_faults_never_degrade(error):
    assert not app.direct_degraded_allowed(transport_error=error, health_pool_exhausted=True,
        observed_exit='198.51.100.8',shared_exit_key='198.51.100.8',old_context_drained=True)


@pytest.mark.parametrize('mode,fault',[('proxy_preferred','none'),('direct_degraded','none'),('proxy_preferred','407'),('proxy_preferred','429'),('direct_degraded','429'),('proxy_preferred','rendering'),('direct_degraded','rendering')])
def test_real_browser_managed_ack_and_explicit_transport(tmp_path,monkeypatch,mode,fault):
    import http.server
    import ssl
    from proxy_fixture import local_proxy
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    direct_requests=[]
    with local_proxy(tmp_path/'network') as fixture:
        server=None
        if mode=='direct_degraded':
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    direct_requests.append({'path':self.path,**dict(self.headers)})
                    self.send_response(200); self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Content-Type','application/json' if self.path=='/echo' else 'text/html'); self.end_headers()
                    self.wfile.write(b'{"ip":"198.51.100.7"}' if self.path=='/echo' else b'<html>direct fixture</html>')
                def log_message(self,*args): pass
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(fixture.root/'server.crt',fixture.root/'server.key')
            server.socket=tls.wrap_socket(server.socket,server_side=True)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            echo=f'https://echo.proxy-test.invalid:{server.server_port}/echo'
            target=f'https://www.douyin.com:{server.server_port}/business'
        else:
            echo=fixture.url(host='echo');target=fixture.url('/business',host='www.douyin.com')
        accounts=CrawlerAccountStore(tmp_path/'accounts.sqlite3');account=accounts.create(platform='dy',display_name='fixture')
        accounts.save_auth_state(account['id'],'fixture-ciphertext')
        accounts.observe_shared_exit(fixture.exit_ip,service_exit=mode=='direct_degraded')
        base=managed(tmp_path,mode)
        scopes=app.route_contract().derive_scope_ids('dy',account['id'],'p',fixture.exit_ip)
        if mode=='direct_degraded':
            scopes=tuple(sorted(['dy','dy:account:'+account['id'],'dy:service_exit:'+fixture.exit_ip,'dy:exit:'+fixture.exit_ip]))
            accounts.set_route_state(account['id'],state='active',route_mode=mode,shared_exit_key=fixture.exit_ip,generation=1)
            accounts.mark_direct_validated(account['id'],1)
        snap=replace(base,account_id=account['id'],profile_key=app.route_contract().derive_profile_key(account['id']),
            host='127.0.0.1' if mode=='proxy_preferred' else 'direct.invalid',port=fixture.proxy.server_port if mode=='proxy_preferred' else 1,
            username=fixture.username if mode=='proxy_preferred' else '',password=fixture.password if mode=='proxy_preferred' else '',
            observed_exit_ip=fixture.exit_ip,scope_ids=scopes,safety_db_path=str(accounts.db_path))
        snap=replace(snap,snapshot_digest=app.route_contract().snapshot_digest(snap))
        proxies=None
        if fault=='407':
            from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
            proxies=CrawlerProxyStore(accounts)
            resource=proxies.create(display_name='auth-fault',endpoint=fixture.server,credentials={'username':fixture.username,'password':fixture.password},expires_at=time.time()+120)
            bound=proxies.bind(account['id'],resource['id'],expected_binding_revision=account['binding_revision'])
            snap=replace(snap,proxy_id=resource['id'],binding_revision=bound['binding_revision'],scope_ids=app.route_contract().derive_scope_ids('dy',account['id'],resource['id'],fixture.exit_ip))
            snap=replace(snap,snapshot_digest=app.route_contract().snapshot_digest(snap))
            fixture.reject=True
            monkeypatch.setattr(app.settings,'request_scheduler_db',tmp_path/'gate.db')
        accounts.admit_route(account['id'],fixture.exit_ip,1,snap.operation_id)
        store=app.RouteOperationStore(tmp_path/'ops.db');store_operation(store,snap)
        bundle=SimpleNamespace(snapshot=snap,account_id=account['id'],execution_id='managed-browser',owner_id='owner',pass_fds=(),route_operation_store=store,_lock=threading.RLock(),account_store=accounts,proxy_store=proxies)
        route_pipe,receipt_pipe=app.RouteConfigPipe(snap),app.RouteReceiptPipe(bundle)
        script=r'''import asyncio,json,os,socket,sys,shutil,subprocess
from pathlib import Path
config=json.loads(sys.stdin.readline())
original=socket.getaddrinfo
def local_dns(host,*args,**kwargs):
 local = (isinstance(host,str) and (host.endswith('.proxy-test.invalid') or host=='www.douyin.com')) or (isinstance(host,bytes) and (host.endswith(b'.proxy-test.invalid') or host==b'www.douyin.com'))
 return original('127.0.0.1' if local else host,*args,**kwargs)
socket.getaddrinfo=local_dns
from tools.bound_proxy import initialize_bound_route,BoundProxySession
from tools import camoufox_browser as browser
original_identity=browser._generate_identity
def local_identity(*args,**kwargs):
 value=original_identity(*args,**kwargs)
 value['firefox_user_prefs']['network.dns.localDomains']='echo.proxy-test.invalid,www.douyin.com'
 return value
browser._generate_identity=local_identity
profile=browser.profile_for(config['account_id'],Path(config['profiles']),os.environ['MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION'])
certutil=shutil.which('certutil');assert certutil
subprocess.run([certutil,'-N','-d','sql:'+str(profile['directory']),'--empty-password'],check=True,capture_output=True)
subprocess.run([certutil,'-A','-d','sql:'+str(profile['directory']),'-n','fixture CA','-t','C,,','-i',config['ca']],check=True,capture_output=True)
async def main():
 snap=initialize_bound_route()
 session=await asyncio.to_thread(BoundProxySession,snap,echo_url=config['echo'],verify=config['ca'],budget_id='managed-browser')
 context=None
 try:
  context,profile=await browser.launch_account_context(snap.account_id,Path(config['profiles']),headless=sys.platform=='darwin',route_snapshot=snap,bound_proxy_session=session)
  async with session.slot('navigation',timeout=15):
   page=await context.new_page();await page.goto(config['target'],wait_until='domcontentloaded');await page.close()
  assert session.receipt.ip==snap.observed_exit_ip
  assert session.context_id==snap.context_id
  print('BROWSER_ACK_OK',flush=True)
  command=await asyncio.to_thread(sys.stdin.readline)
  if command.strip()=='render':
   page=await context.new_page();await page.goto(config['target'],wait_until='domcontentloaded')
   await page.evaluate('html => {document.body.innerHTML=html}', '<html><img id="qr" width="200" height="200" src="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%27200%27 height=%27200%27%3E%3Crect width=%27200%27 height=%27200%27 fill=%27black%27/%3E%3C/svg%3E"><button id="login">fixture login control</button></html>')
   await page.evaluate('url => Promise.all(Array.from({length:400},(_,i) => fetch(url+"/render-resource?i="+i).then(r=>r.text())))',config['target'])
   await page.wait_for_function("document.querySelector('#qr').naturalWidth > 0",timeout=3000)
   assert await page.locator('#qr').is_visible() and await page.locator('#login').is_visible()
   assert not page.is_closed() and not getattr(session,'_browser_guard_first_reason',None)
   await page.close();print('BROWSER_RENDER_RESOURCES_OK',flush=True)
   await asyncio.to_thread(sys.stdin.readline)
  try:
   page=await context.new_page()
   await page.evaluate('url => fetch(url)',config['target']+'/paused')
   raise AssertionError('automatic request bypassed exit pause')
  except Exception as exc:
   if isinstance(exc,AssertionError):raise
  print('BROWSER_PAUSE_FENCED',flush=True)
 finally:
  if context: await context.close()
asyncio.run(main())
'''
        crawler=str(app.settings.media_crawler_dir.resolve())
        from backend.audit_agent.crawler_child_runtime import authority_for
        monkeypatch.setattr(app.settings,'crawler_proxy_echo_url',echo)
        monkeypatch.setattr(app.settings,'crawler_account_min_interval',0)
        monkeypatch.setattr(app.settings,'crawler_account_requests_per_minute',1000)
        monkeypatch.syspath_prepend(crawler)
        from tools.persistent_request_gate import RequestScheduler
        RequestScheduler(snap.scheduler_db_path,platform='dy',min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0)
        authority=authority_for(bundle,verify=fixture.ca,policy=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1))
        env={**os.environ,**route_pipe.env,**receipt_pipe.env,'PYTHONPATH':crawler,'MEDIACRAWLER_ACCOUNT_ID':account['id'],'MEDIACRAWLER_PROXY_REQUIRED':'true',
            'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':'',
            'MEDIACRAWLER_DY_BROWSER_ENGINE':'camoufox',**authority.env}
        child=subprocess.Popen([os.environ['LOGIN_BROWSER_TEST_PYTHON'],'-c',script],env=env,cwd=crawler,
            pass_fds=(*route_pipe.pass_fds,*receipt_pipe.pass_fds,*authority.pass_fds),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            authority.bind_child(child.pid)
            route_pipe.start(child);receipt_pipe.start(child)
            import json
            child.stdin.write(json.dumps(dict(account_id=account['id'],echo=echo,target=target,ca=fixture.ca,profiles=str(tmp_path/'profiles'),stop=fault))+'\n');child.stdin.flush()
            lines=[]
            def until(marker):
                import selectors
                deadline=time.monotonic()+45
                with selectors.DefaultSelector() as poll:
                    poll.register(child.stdout,selectors.EVENT_READ)
                    while time.monotonic()<deadline:
                        if not poll.select(.2):continue
                        line=child.stdout.readline()
                        if not line:return False
                        lines.append(line)
                        if marker in line:return True
                raise AssertionError('managed browser protocol timed out')
            acked=until('BROWSER_ACK_OK')
            if acked:
                if fault=='rendering':
                    with __import__('sqlite3').connect(snap.scheduler_db_path) as db:
                        db.execute('CREATE TABLE IF NOT EXISTS collection_browser_requests (token TEXT PRIMARY KEY,exit_key TEXT,pid INTEGER,started_at REAL,active INTEGER)')
                        db.executemany('INSERT INTO collection_browser_requests VALUES(?,?,?,?,1)',((str(i),snap.observed_exit_ip,child.pid,time.time()) for i in range(400)))
                        before=db.execute("SELECT request_times_json FROM request_scheduler_state WHERE platform='dy'").fetchone()[0]
                    child.stdin.write('render\n');child.stdin.flush()
                    assert until('BROWSER_RENDER_RESOURCES_OK')
                    with __import__('sqlite3').connect(snap.scheduler_db_path) as db:
                        assert db.execute('SELECT COUNT(*) FROM collection_browser_requests').fetchone()[0]==400
                        assert db.execute("SELECT request_times_json FROM request_scheduler_state WHERE platform='dy'").fetchone()[0]==before
                if fault=='429':authority.session.enter_cooldown('http_429',30)
                else:
                    with accounts._connect() as db:
                        db.execute("UPDATE crawler_shared_exits SET pause_reason='ACCOUNT_CHALLENGE',paused_until=? WHERE exit_key=?",(time.time()+30,snap.observed_exit_ip))
                child.stdin.write('pause\n');child.stdin.flush()
            tail,err=child.communicate(timeout=45)
            out=''.join(lines)+tail
            assert fixture.password not in out+err
            if fault=='407':
                assert child.returncode!=0 and 'PROXY_AUTH_FAILED' in err
                assert bundle.route_error=='PROXY_AUTH_FAILED'
                assert proxies.get_public(snap.proxy_id)['stop_reason']=='PROXY_AUTH_FAILED'
                assert app.direct_degraded_allowed(transport_error=bundle.route_error,health_pool_exhausted=True,old_context_drained=True,observed_exit=fixture.exit_ip,shared_exit_key=fixture.exit_ip)
                assert not fixture.origins and any(e['kind']=='407' for e in fixture.events)
                return
            assert child.returncode==0,err[-2500:]
            assert 'BROWSER_ACK_OK' in out and 'BROWSER_PAUSE_FENCED' in out
            if fault=='rendering':
                assert 'BROWSER_RENDER_RESOURCES_OK' in out
                origins=fixture.origins if mode=='proxy_preferred' else direct_requests
                assert sum('/render-resource?' in o['path'] for o in origins)==400
            with __import__('sqlite3').connect(snap.scheduler_db_path) as db:
                assert not db.execute('SELECT 1 FROM collection_route_health').fetchone()
            if mode=='proxy_preferred':
                fixture.assert_routed();assert any(e['kind']=='CONNECT' for e in fixture.events)
                assert not any(o['path'].endswith('/paused') for o in fixture.origins)
            else:
                assert not fixture.events and len(direct_requests)>=3
                assert all('Proxy-Authorization' not in h for h in direct_requests)
                assert not any(h['path'].endswith('/paused') for h in direct_requests)
        finally:
            app._stop_probe_process_group(child)
            authority.close()
            route_pipe.close();receipt_pipe.close()
            if server:server.shutdown();server.server_close()


def test_renew_ticket_is_idempotent_fenced_and_bounded(tmp_path,monkeypatch):
    clock=[1000.0];monkeypatch.setattr(app.time,'time',lambda:clock[0])
    operation=app.RouteOperation.create(owner_id='owner',account_id='account',profile_key='profile',route_mode='proxy_required',
        context_id='context',snapshot_digest='a'*64,expires_at=1300,proxy_expires_at=5000)
    store=app.RouteOperationStore(tmp_path/'ops.db');store.issue(operation)
    store.ack(operation.operation_id,owner_id='owner',lease_version=1,snapshot_digest='a'*64,context_id='context')
    clock[0]=1275
    result=store.renew('renew-1',route_operation_id=operation.operation_id,owner_id='owner',lease_version=1)
    assert result['lease_version']==2 and result['expires_at']==1575
    assert store.renew('renew-1',route_operation_id=operation.operation_id,owner_id='owner',lease_version=1)==result
    with pytest.raises(ValueError):store.renew('renew-2',route_operation_id=operation.operation_id,owner_id='owner',lease_version=1)
    with pytest.raises(ValueError):store.renew('renew-1',route_operation_id=operation.operation_id,owner_id='other',lease_version=1)
    clock[0]=1576
    with pytest.raises(ValueError):store.renew('renew-3',route_operation_id=operation.operation_id,owner_id='owner',lease_version=2)


def test_risk_receipt_pauses_shared_exit_before_live_child_drains(tmp_path,monkeypatch):
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    monkeypatch.setattr(app.settings,'crawler_safety_enabled',True)
    accounts=CrawlerAccountStore(tmp_path/'accounts.db');account=accounts.create(platform='dy',display_name='risk')
    accounts.save_auth_state(account['id'],'fixture-ciphertext')
    accounts.observe_shared_exit('198.51.100.8')
    base=managed(tmp_path)
    snap=replace(base,account_id=account['id'],profile_key=app.route_contract().derive_profile_key(account['id']),
        scope_ids=app.route_contract().derive_scope_ids('dy',account['id'],'p','198.51.100.8'),safety_db_path=str(accounts.db_path))
    snap=replace(snap,snapshot_digest=app.route_contract().snapshot_digest(snap))
    accounts.admit_route(account['id'],snap.observed_exit_ip,1,snap.operation_id)
    store=app.RouteOperationStore(tmp_path/'ops.db');store_operation(store,snap)
    bundle=SimpleNamespace(snapshot=snap,route_operation_store=store,_lock=threading.RLock(),account_store=accounts)
    config,receipts=app.RouteConfigPipe(snap),app.RouteReceiptPipe(bundle)
    crawler=str(app.settings.media_crawler_dir.resolve())
    env={**os.environ,**config.env,**receipts.env,'PYTHONPATH':crawler,'MEDIACRAWLER_ACCOUNT_ID':account['id']}
    script='from tools.bound_proxy import initialize_bound_route,obtain_route_ack,emit_account_risk\nimport time\ns=initialize_bound_route();obtain_route_ack(s);assert emit_account_risk(s);time.sleep(3)'
    child=subprocess.Popen(['/Users/hose/project/zhanyun/media-crawler/.venv/bin/python','-c',script],env=env,cwd=crawler,
        pass_fds=(*config.pass_fds,*receipts.pass_fds),stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:
        config.start(child);receipts.start(child)
        deadline=time.monotonic()+2
        while accounts.get(account['id'])['route_state']!='challenge_paused' and time.monotonic()<deadline:time.sleep(.01)
        assert child.poll() is None
        assert accounts.get(account['id'])['risk_count']==1
        assert accounts.get(account['id'])['route_state']=='challenge_paused'
        assert bundle.account_risk_recorded is True
        with accounts._connect() as conn:
            assert conn.execute('SELECT pause_reason FROM crawler_shared_exits WHERE exit_key=?',(snap.observed_exit_ip,)).fetchone()[0]=='ACCOUNT_CHALLENGE'
            assert conn.execute('SELECT state FROM crawler_route_admissions WHERE operation_id=?',(snap.operation_id,)).fetchone()[0]=='active'
    finally:
        if child.poll() is None:child.terminate();child.wait(timeout=5)
        config.close();receipts.close()


@pytest.mark.parametrize('cold_direct,manual,outcome',[(False,False,'success'),(True,False,'success'),(False,True,'success'),(False,True,'failed'),(False,True,'challenge')])
def test_actual_bounded_login_checker_uses_same_profile_and_local_identity(tmp_path,monkeypatch,cold_direct,manual,outcome):
    import json
    from proxy_fixture import local_proxy, prepare_camoufox_account
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    with local_proxy(tmp_path/'network') as fixture:
        direct_server=None
        echo=fixture.url(host='echo')
        if cold_direct:
            import http.server,ssl
            class Direct(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200);self.send_header('Access-Control-Allow-Origin','*');self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"ip":"198.51.100.7"}')
                def log_message(self,*args):pass
            direct_server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Direct)
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(fixture.root/'server.crt',fixture.root/'server.key');direct_server.socket=tls.wrap_socket(direct_server.socket,server_side=True)
            threading.Thread(target=direct_server.serve_forever,daemon=True).start()
            echo=f'https://echo.proxy-test.invalid:{direct_server.server_port}/echo'
        crawler=Path(app.settings.media_crawler_dir)
        for name,value in dict(crawler_login_python=Path(os.environ['LOGIN_BROWSER_TEST_PYTHON']),
            crawler_proxy_echo_url=echo,crawler_browser_profile_root=tmp_path/'profiles',request_scheduler_db=tmp_path/'gate.db',
            crawler_safety_enabled=True,crawler_direct_degrade_enabled=True,app_auth_mode='disabled',task_resource_lock_dir=tmp_path/'locks').items():
            monkeypatch.setattr(app.settings,name,value)
        accounts=CrawlerAccountStore(tmp_path/'accounts.db');account=accounts.create(platform='dy',display_name='bounded-login')
        profile=prepare_camoufox_account(fixture,app.settings.crawler_camoufox_state_root/'profiles',account['id'],account['profile_generation'],
            local_domains=('echo.proxy-test.invalid','www.douyin.com') if cold_direct else None)
        monkeypatch.setattr(app.settings,'crawler_camoufox_browser_version',profile['version'])
        monkeypatch.setattr(app.settings,'crawler_camoufox_executable',profile['executable'])
        monkeypatch.syspath_prepend(str(crawler))
        from tools.persistent_request_gate import RequestScheduler
        RequestScheduler(tmp_path/'gate.db',platform='dy',min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0)
        accounts.save_auth_state(account['id'],'fixture-ciphertext');accounts.observe_shared_exit(fixture.exit_ip)
        manual_claim=None
        if manual:
            accounts.update(account['id'],platform_account_id='fixture_identity_123')
            accounts.record_risk(account['id'])
            accounts.set_route_state(account['id'],state='active',shared_exit_key=fixture.exit_ip,generation=1)
            accounts.pause_account_exit(account['id'],until=time.time()+.1)
            manual_claim=accounts.begin_manual_recovery(account['id'],'manual-login',owner_id='owner')
            assert manual_claim and accounts.get(account['id'])['recovery_state']=='manual_claimed'
        snap=replace(managed(tmp_path),account_id=account['id'],profile_key=app.route_contract().derive_profile_key(account['id']),
            host='127.0.0.1',port=fixture.proxy.server_port,username=fixture.username,password=fixture.password,
            observed_exit_ip=fixture.exit_ip,scope_ids=app.route_contract().derive_scope_ids('dy',account['id'],'p',fixture.exit_ip),safety_db_path=str(accounts.db_path),recovery_claim=manual_claim or '')
        snap=replace(snap,snapshot_digest=app.route_contract().snapshot_digest(snap))
        accounts.admit_route(account['id'],fixture.exit_ip,1,snap.operation_id,recovery_claim=manual_claim)
        store=app.RouteOperationStore(tmp_path/'ops.db');store_operation(store,snap)
        bundle=SimpleNamespace(snapshot=snap,account_id=account['id'],owner_id='owner',execution_id='bounded-login',pass_fds=(),
            route_operation_store=store,account_store=accounts,_lock=threading.RLock())
        helper=Path(app.__file__).resolve().parents[2]/'scripts/crawler_proxy_probe.py'
        wrapper=tmp_path/'local_login.py'
        wrapper.write_text(r'''import os,sys,runpy,functools,json,socket
original_dns=socket.getaddrinfo
def local_dns(host,*args,**kwargs):
 local=(isinstance(host,str) and host.endswith('.proxy-test.invalid')) or (isinstance(host,bytes) and host.endswith(b'.proxy-test.invalid'))
 return original_dns('127.0.0.1' if local else host,*args,**kwargs)
socket.getaddrinfo=local_dns
sys.path.insert(0,os.environ['MEDIACRAWLER_DIR'])
import tools.bound_proxy as transport
import tools.camoufox_browser as browser
transport.BoundProxySession=functools.partial(transport.BoundProxySession,echo_url=os.environ['FIXTURE_ECHO'],verify=os.environ['FIXTURE_CA'])
original=browser.launch_account_context
async def trusted(*args,**kwargs):
 context,profile=await original(*args,**kwargs)
 async def local(route):
  url=route.request.url
  if url.startswith(os.environ['FIXTURE_ECHO']):await route.fallback();return
  if url.startswith('https://www.douyin.com/user/self'):
   body='<html><body>local identity fixture'+('<div id="captcha_container" style="width:300px;height:200px">fixture challenge</div>' if os.environ.get('FIXTURE_CASE')=='challenge' else '')+'<script>fetch("/aweme/v1/web/user/profile/self");</script></body></html>'
   await route.fulfill(status=200,content_type='text/html',body=body);return
  if url.startswith('https://www.douyin.com/aweme/v1/web/user/profile/self'):
   await route.fulfill(status=200,content_type='application/json',body=json.dumps({'status_code':0,'user':{'sec_uid':'fixture_identity_123'}}));return
  await route.abort()
 await context.route('**/*',local)
 return context,profile
browser.launch_account_context=trusted
runpy.run_path(os.environ['FIXTURE_HELPER'],run_name='__main__')
''')
        original_popen=subprocess.Popen
        def local_child(command,**kwargs):
            if len(command)>1 and command[1]==str(helper):
                env={**kwargs['env'],'FIXTURE_ECHO':echo,'FIXTURE_CA':fixture.ca,'FIXTURE_HELPER':str(helper),'FIXTURE_CASE':outcome}
                return original_popen([command[0],str(wrapper),*command[2:]],**{**kwargs,'env':env})
            return original_popen(command,**kwargs)
        monkeypatch.setattr(subprocess,'Popen',local_child)
        if cold_direct:
            accounts.release_route_admission(snap.operation_id)
            from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
            from scripts.crawler_proxy_config import apply_config
            proxies=CrawlerProxyStore(accounts)
            resource=proxies.create(display_name='expired',endpoint=fixture.server,credentials={'username':fixture.username,'password':fixture.password},expires_at=time.time()+120)
            bound=proxies.bind(account['id'],resource['id'],expected_binding_revision=account['binding_revision'])
            apply_config(proxies,dict(operation='safety-policy',account_id=account['id'],expected_binding_revision=bound['binding_revision'],route_mode='proxy_preferred'))
            accounts.update(account['id'],platform_account_id='fixture_identity_123')
            proxies.update(resource['id'],expected_revision=1,expires_at=time.time()-1)
            monkeypatch.setattr(app,'_store',proxies)
            with pytest.raises(app.CollectionRouteError) as fault:app.resolve_account_route(account['id'],account_store=accounts)
            assert fault.value.code=='PROXY_EXPIRED'
            assert accounts.get_direct_route(account['id']) is None
            # Remove the prior generic fixture admission; no service metadata exists.
            accounts.release_route_admission(snap.operation_id)
            assert app.prepare_direct_degraded(account['id'],fault.value.code,True,account_store=accounts,old_context_drained=True,owner_id='owner')
            current=accounts.get(account['id'])
            assert current['route_mode']=='direct_degraded' and current['validated_direct_generation']==current['shared_exit_generation']
            assert not fixture.events
        else:
            expected='different_identity_456' if outcome=='failed' else 'fixture_identity_123'
            result=app._run_maintenance_probe(bundle,check_login=True,expected_identity=expected)
            assert result==('success' if outcome=='success' else 'challenge' if outcome=='challenge' else 'uncertain')
            if manual:
                with accounts._connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    if result=='success':accounts.save_douyin_auth_state(account['id'],'fresh-fixture-ciphertext','fixture_identity_123',connection=conn)
                    settled=accounts.finish_manual_recovery(account['id'],'manual-login',manual_claim,outcome,owner_id='owner',verified_identity='fixture_identity_123' if result=='success' else '',connection=conn)
                    assert settled==(outcome=='success')
                current=accounts.get(account['id'])
                assert current['route_state']==('active' if outcome=='success' else 'manual_required') and current['risk_count']==1
                with accounts._connect() as conn:
                    pause=conn.execute('SELECT pause_reason FROM crawler_shared_exits WHERE exit_key=?',(fixture.exit_ip,)).fetchone()[0]
                    assert bool(pause)==(outcome!='success')
                if outcome=='success':
                    # A new real browser and HTTP pair can resume on the same Profile after recovery.
                    accounts.release_route_admission(snap.operation_id)
                    restored=replace(snap,operation_id='restored-operation',lease_id='restored-lease',context_id='restored-context',recovery_claim='',snapshot_digest='')
                    restored=replace(restored,snapshot_digest=app.route_contract().snapshot_digest(restored))
                    accounts.admit_route(account['id'],fixture.exit_ip,1,restored.operation_id)
                    store_operation(store,restored)
                    resumed=SimpleNamespace(snapshot=restored,account_id=account['id'],owner_id='owner',execution_id='restored-login',pass_fds=(),route_operation_store=store,account_store=accounts,_lock=threading.RLock())
                    assert app._run_maintenance_probe(resumed,check_login=True,expected_identity='fixture_identity_123')=='success'
                    assert restored.profile_key==snap.profile_key and restored.context_id!=snap.context_id
                    assert accounts.get(account['id'])['risk_count']==1
        assert (app.settings.crawler_camoufox_state_root/'profiles'/snap.profile_key/'adapter.lock').exists()
        assert all(o['path']=='/echo' for o in fixture.origins)
        assert not fixture.traps
        if direct_server:direct_server.shutdown();direct_server.server_close()


def test_real_child_renews_ticket_without_changing_snapshot_identity(tmp_path):
    import json
    base=replace(managed(tmp_path),expires_at=time.time()+3600)
    snap=replace(base,snapshot_digest=app.route_contract().snapshot_digest(base))
    store=app.RouteOperationStore(tmp_path/'ops.db')
    store.issue(app.RouteOperation(operation_id=snap.operation_id,lease_id=snap.lease_id,lease_version=1,
        owner_id=snap.owner_id,account_id=snap.account_id,profile_key=snap.profile_key,route_mode=snap.route_mode,
        expires_at=time.time()+10,proxy_expires_at=snap.expires_at,context_id=snap.context_id,snapshot_digest=snap.snapshot_digest))
    bundle=SimpleNamespace(snapshot=snap,route_operation_store=store,_lock=threading.RLock())
    route_pipe,receipt_pipe=app.RouteConfigPipe(snap),app.RouteReceiptPipe(bundle)
    crawler=str(app.settings.media_crawler_dir.resolve())
    env={**os.environ,**route_pipe.env,**receipt_pipe.env,'PYTHONPATH':crawler,'MEDIACRAWLER_ACCOUNT_ID':'a'}
    script="from tools.bound_proxy import initialize_bound_route,BoundProxySession\nimport json\ns=initialize_bound_route();value=BoundProxySession(s,echo_url='https://echo.invalid/ip',budget_id='renew-fixture');value.check_stop();print(json.dumps({'ticket_version':value._lease_ack['lease_version'],'snapshot_version':s.lease_version,'digest':s.snapshot_digest}))"
    child=subprocess.Popen(['/Users/hose/project/zhanyun/media-crawler/.venv/bin/python','-c',script],env=env,cwd=crawler,
        pass_fds=(*route_pipe.pass_fds,*receipt_pipe.pass_fds),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        route_pipe.start(child);receipt_pipe.start(child)
        out,err=child.communicate(timeout=10)
        assert child.returncode==0,err
        assert json.loads(out.strip())==dict(ticket_version=2,snapshot_version=1,digest=snap.snapshot_digest)
        with pytest.raises(ValueError):store.renew('stale-command',route_operation_id=snap.operation_id,owner_id=snap.owner_id,lease_version=1)
    finally:
        if child.poll() is None:child.kill();child.wait()
        route_pipe.close();receipt_pipe.close()


def test_live_orphan_retains_account_profile_fences_then_exact_reconciliation(tmp_path,monkeypatch):
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.task_admission.resources import acquire_account_handle
    monkeypatch.setattr(app.settings,'task_resource_lock_dir',tmp_path/'locks')
    monkeypatch.setattr(app.settings,'crawler_browser_profile_root',tmp_path/'profiles')
    accounts=CrawlerAccountStore(tmp_path/'accounts.db');account=accounts.create(platform='dy',display_name='orphan')
    accounts.save_auth_state(account['id'],'fixture-ciphertext');accounts.observe_shared_exit('198.51.100.8')
    accounts.admit_route(account['id'],'198.51.100.8',1,'orphan-operation')
    handle=acquire_account_handle('dy',account['id']);assert handle
    directory=tmp_path/'profiles'/app.route_contract().derive_profile_key(account['id']);directory.mkdir(parents=True)
    script="import fcntl,sys,time\nh=open(sys.argv[1],'a+b');fcntl.flock(h,fcntl.LOCK_EX);print('READY',flush=True);time.sleep(30)"
    child=subprocess.Popen(['/Users/hose/project/zhanyun/media-crawler/.venv/bin/python','-c',script,str(directory/'adapter.lock')],
        pass_fds=(handle.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        assert child.stdout.readline().strip()=='READY'
        handle.close()
        assert acquire_account_handle('dy',account['id']) is None
        with accounts._connect() as db:assert db.execute("SELECT state FROM crawler_route_admissions WHERE operation_id='orphan-operation'").fetchone()[0]=='active'
        child.terminate();child.wait(timeout=5)
        fresh=acquire_account_handle('dy',account['id']);assert fresh
        try:
            assert app.reconcile_stopped_route_admissions(accounts,account['id'],fresh)==1
        finally:fresh.close()
        with accounts._connect() as db:assert db.execute("SELECT state FROM crawler_route_admissions WHERE operation_id='orphan-operation'").fetchone()[0]=='ended'
    finally:
        if not handle.closed:handle.close()
        if child.poll() is None:child.kill();child.wait()


def test_inactive_crawl_child_supplement_uses_crawler_helper_in_app_interpreter(tmp_path,monkeypatch):
    import functools,importlib.util,json
    from proxy_fixture import local_proxy, prepare_camoufox_account
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent.asset_utils import download_url_with_error
    from backend.task_admission.resources import ExecutionLeaseBundle
    assert importlib.util.find_spec('cloakbrowser') is None
    with local_proxy(tmp_path/'network') as fixture:
        for name,value in dict(crawler_login_python=Path(os.environ['LOGIN_BROWSER_TEST_PYTHON']),
            crawler_safety_enabled=True,crawler_proxy_enabled=True,app_auth_mode='disabled',
            crawler_proxy_echo_url=fixture.url(host='echo'),crawler_browser_profile_root=tmp_path/'profiles',
            request_scheduler_db=tmp_path/'gate.db',task_resource_lock_dir=tmp_path/'locks',crawler_account_min_interval=0,
            crawler_account_requests_per_minute=1000).items():monkeypatch.setattr(app.settings,name,value)
        accounts=CrawlerAccountStore(tmp_path/'accounts.db');proxies=CrawlerProxyStore(accounts)
        account=accounts.create(platform='dy',display_name='asset-source')
        profile=prepare_camoufox_account(fixture,app.settings.crawler_camoufox_state_root/'profiles',account['id'],account['profile_generation'])
        monkeypatch.setattr(app.settings,'crawler_camoufox_browser_version',profile['version'])
        monkeypatch.setattr(app.settings,'crawler_camoufox_executable',profile['executable'])
        monkeypatch.syspath_prepend(str(app.settings.media_crawler_dir))
        from tools.persistent_request_gate import RequestScheduler
        RequestScheduler(tmp_path/'gate.db',platform='dy',min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0)
        resource=proxies.create(display_name='fixture',endpoint=fixture.server,credentials={'username':fixture.username,'password':fixture.password},expires_at=time.time()+600)
        bound=proxies.bind(account['id'],resource['id'],expected_binding_revision=account['binding_revision'])
        proxies.observe_exit(resource['id'],fixture.exit_ip);accounts.save_auth_state(account['id'],'fixture-ciphertext')
        proxies.mark_route_login_validated(account['id'],expected_binding_revision=bound['binding_revision'],expected_config_revision=1)
        monkeypatch.setattr(app,'_store',proxies)
        helper=Path(app.__file__).resolve().parents[2]/'scripts/crawler_proxy_probe.py'
        wrapper=tmp_path/'asset_helper.py'
        wrapper.write_text(r'''import os,sys,runpy,functools
sys.path.insert(0,os.environ['MEDIACRAWLER_DIR'])
import tools.bound_proxy as transport
transport.BoundProxySession=functools.partial(transport.BoundProxySession,echo_url=os.environ['FIXTURE_ECHO'],verify=os.environ['FIXTURE_CA'])
runpy.run_path(os.environ['FIXTURE_HELPER'],run_name='__main__')
''')
        original_popen=subprocess.Popen;children=[]
        def local_child(command,**kwargs):
            if len(command)>1 and command[1]==str(helper):
                assert command[0]==str(app.settings.crawler_login_python)
                env={**kwargs['env'],'FIXTURE_ECHO':fixture.url(host='echo'),'FIXTURE_CA':fixture.ca,'FIXTURE_HELPER':str(helper)}
                child=original_popen([command[0],str(wrapper),*command[2:]],**{**kwargs,'env':env});children.append(child);return child
            return original_popen(command,**kwargs)
        monkeypatch.setattr(subprocess,'Popen',local_child)
        bundle=app.acquire_route_execution(account['id'],'source-task','owner',account_store=accounts)
        old=bundle.snapshot
        try:
            # A real original browser has ended; a new helper must preserve its source route.
            with accounts._connect() as health_db:
                health_db.execute('ATTACH DATABASE ? AS probe_health',(old.scheduler_db_path,))
                health_db.execute('CREATE TABLE IF NOT EXISTS probe_health.collection_route_health(proxy_id TEXT,config_revision INTEGER,stop_code TEXT,PRIMARY KEY(proxy_id,config_revision))')
                health_db.execute('INSERT OR REPLACE INTO probe_health.collection_route_health VALUES(?,?,?)',(old.proxy_id,old.config_revision,'PROXY_UNAVAILABLE'))
            original_receipt=app._run_maintenance_probe(bundle)
            with accounts._connect() as health_db:
                health_db.execute('ATTACH DATABASE ? AS probe_health',(old.scheduler_db_path,))
                assert not health_db.execute('SELECT * FROM probe_health.collection_route_health').fetchall()
            assert children[0].poll()==0
            source=app.route_contract().collection_route_provenance(old,original_receipt,observation_id='source-observation',source_task_id='source-task',source_batch_id='source-batch',collected_at=time.time(),context_id=old.context_id)
            monkeypatch.syspath_prepend(str(app.settings.media_crawler_dir))
            from tools import bound_proxy as transport
            from tools.persistent_request_gate import RequestScheduler
            RequestScheduler(tmp_path/'gate.db',platform='dy',min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0)
            monkeypatch.setattr(transport,'BoundProxySession',functools.partial(transport.BoundProxySession,verify=fixture.ca))
            bundle.child_active=False
            result=download_url_with_error(fixture.url('/final'),tmp_path/'asset',timeout=25,platform='dy',
                collection_route_provenance=source,owner_id='owner',execution_id='source-task',route_context=bundle,verify=fixture.ca)
            assert result.path==tmp_path/'asset' and result.status_code==200
            assert len(children)==2 and children[1].poll() is None
            assert bundle.snapshot.operation_id!=old.operation_id and bundle.snapshot.context_id!=old.context_id
            assert bundle.snapshot.profile_key==old.profile_key and bundle.snapshot.proxy_id==old.proxy_id and bundle.snapshot.exit_generation==old.exit_generation
            assert source['operation_id']==old.operation_id
            fixture.assert_routed();assert any(o['path']=='/final' for o in fixture.origins)
            with bundle.borrow():
                with pytest.raises(app.CollectionRouteError,match='PROXY_BUSY'):
                    app.prepare_collection_child(bundle,allowed_borrowers=0,timeout=.05)
                assert children[1].poll() is None
            app.prepare_collection_child(bundle,allowed_borrowers=0,timeout=1)
            assert children[1].poll() is not None and bundle.asset_browser_process is None
            assert bundle.snapshot.proxy_id==old.proxy_id and bundle.snapshot.exit_generation==old.exit_generation
        finally:
            bundle.close()
        assert all(child.poll() is not None for child in children)
        with accounts._connect() as db:assert not db.execute("SELECT 1 FROM crawler_route_admissions WHERE account_id=? AND state='active'",(account['id'],)).fetchone()


@pytest.mark.parametrize('original_identity',['original_identity_123',''])
@pytest.mark.parametrize('manual_without_risk',[False,True])
def test_actual_manager_challenge_pauses_once_but_qr_and_human_claim_remain_valid(tmp_path,monkeypatch,original_identity,manual_without_risk):
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager,V2Session
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    for name,value in dict(crawler_safety_enabled=True,crawler_direct_degrade_enabled=True,crawler_proxy_enabled=True,app_auth_mode='disabled',
        request_scheduler_db=tmp_path/'gate.db',task_resource_lock_dir=tmp_path/'locks',crawler_browser_profile_root=tmp_path/'profiles').items():monkeypatch.setattr(app.settings,name,value)
    accounts=CrawlerAccountStore(tmp_path/'accounts.db');proxies=CrawlerProxyStore(accounts)
    a=accounts.create(platform='dy',display_name='human');accounts.update(a['id'],platform_account_id=original_identity)
    p=proxies.create(display_name='fixture',endpoint='http://127.0.0.1:9',expires_at=time.time()+3600)
    proxies.bind(a['id'],p['id'],expected_binding_revision=a['binding_revision'],route_mode='proxy_preferred' if manual_without_risk else None);proxies.observe_exit(p['id'],'198.51.100.8')
    prior_claim=None
    if manual_without_risk:
        observation=accounts.observe_shared_exit('198.51.100.8',service_exit=True)
        accounts.set_route_state(a['id'],state='manual_required',route_mode='direct_degraded',pause_reason='LOGIN_REVALIDATION_REQUIRED',shared_exit_key='198.51.100.8',generation=observation['shared_exit_generation'])
        prior_claim=accounts.begin_manual_recovery(a['id'],'human-login',owner_id='owner')
        assert prior_claim
    bundle=app.acquire_route_execution(a['id'],'human-login','owner',account_store=accounts,allow_probe=True,human_login_operation_id='human-login',recovery_claim=prior_claim)
    manager=CrawlerLoginV2Manager(accounts,AuthStateCipher('fixture'))
    session=V2Session('session','human-login',a['id'],'owner','app-session','owner-hash',dict(phase='checking',state_revision=1))
    session.route_bundle=bundle;manager.sessions[session.id]=session
    b=accounts.create(platform='dy',display_name='neighbor');accounts.save_auth_state(b['id'],'fixture-ciphertext')
    try:
        manager.handle_event(session.id,dict(type='snapshot',phase='qr_ready'))
        assert accounts.get(a['id'])['risk_count']==0
        accounts.admit_route(b['id'],'198.51.100.8',1,'before-challenge');accounts.release_route_admission('before-challenge')
        manager.handle_event(session.id,dict(type='snapshot',phase='challenge_required'))
        manager.handle_event(session.id,dict(type='snapshot',phase='challenge_required'))
        assert accounts.get(a['id'])['risk_count']==1
        with pytest.raises(ValueError,match='ACCOUNT_CHALLENGE'):accounts.admit_route(b['id'],'198.51.100.8',1,'after-challenge')
        # The existing explicit human claim may continue verification despite expired credentials.
        accounts.mark_douyin_auth_invalid(a['id'],status='login_required',reset=True)
        assert accounts.admit_route(a['id'],'198.51.100.8',1,'human-continue',recovery_claim=bundle.snapshot.recovery_claim)=='human-continue'
        with pytest.raises(ValueError):accounts.admit_route(a['id'],'198.51.100.8',1,'automatic-hidden-login')
        accounts.release_route_admission('human-continue')
        if not original_identity:
            # First trusted human identity is frozen atomically, including a deferred release.
            identity='trusted_new_identity_123'
            with accounts._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                accounts.save_douyin_auth_state(a['id'],'fresh-fixture-ciphertext',identity,connection=db)
                assert accounts.finish_manual_recovery(a['id'],'human-login',bundle.snapshot.recovery_claim,'success',owner_id='owner',verified_identity=identity,connection=db)
            assert accounts.get(a['id'])['recovery_state']=='manual_verified_waiting'
            with accounts._connect() as db:
                operation=db.execute("SELECT expected_identity,cooldown_until FROM crawler_manual_recovery_operations WHERE login_operation_id='human-login'").fetchone()
            assert operation['expected_identity']==identity
            assert not accounts.release_verified_manual(a['id'])
            release_at=max(operation['cooldown_until'],accounts.get(a['id'])['paused_until'])+1
            from datetime import datetime
            from backend.audit_agent import crawler_account_store
            class ReleasedDatetime(datetime):
                @classmethod
                def now(cls,tz=None):return cls.fromtimestamp(release_at,tz)
            with monkeypatch.context() as clock_patch:
                clock_patch.setattr(time,'time',lambda:release_at)
                clock_patch.setattr(crawler_account_store,'datetime',ReleasedDatetime)
                assert accounts.release_verified_manual(a['id'])
            assert accounts.get(a['id'])['risk_count']==1
    finally:
        manager.sessions.clear();bundle.close()


def test_two_fresh_proxy_samples_share_newer_canonical_exit_without_timestamp_replay(tmp_path,monkeypatch):
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from crawler_auth_fixtures import mark_camoufox_login_verified
    for name,value in dict(crawler_safety_enabled=True,crawler_proxy_enabled=True,app_auth_mode='disabled',
        request_scheduler_db=tmp_path/'gate.db',task_resource_lock_dir=tmp_path/'locks',crawler_browser_profile_root=tmp_path/'profiles').items():monkeypatch.setattr(app.settings,name,value)
    accounts=CrawlerAccountStore(tmp_path/'accounts.db');proxies=CrawlerProxyStore(accounts)
    now=time.time();items=[]
    for index,at in enumerate((now-.1,now)):
        a=accounts.create(platform='dy',display_name=str(index));accounts.save_auth_state(a['id'],'fixture-ciphertext')
        mark_camoufox_login_verified(accounts,a['id'])
        p=proxies.create(display_name=str(index),endpoint='http://127.0.0.1:9',expires_at=now+3600)
        bound=proxies.bind(a['id'],p['id'],expected_binding_revision=a['binding_revision'])
        proxies.observe_exit(p['id'],'198.51.100.8',observed_at=at)
        proxies.mark_route_login_validated(a['id'],expected_binding_revision=bound['binding_revision'],expected_config_revision=1)
        items.append((a,p,at))
    assert len(accounts.available_accounts('dy'))==2
    for index,(a,p,at) in enumerate(items):
        bundle=app.acquire_route_execution(a['id'],'shared-'+str(index),'owner',account_store=accounts)
        try:
            assert bundle.snapshot.observed_exit_at==at and bundle.snapshot.exit_generation==1
        finally:bundle.close()
    with accounts._connect() as db:
        assert db.execute('SELECT observed_at FROM crawler_shared_exits').fetchone()[0]==now
    with pytest.raises(ValueError,match='PROXY_STALE_OBSERVATION'):
        accounts.observe_shared_exit('198.51.100.8',observed_at=now-.1)
    with pytest.raises(ValueError,match='PROXY_STALE_OBSERVATION'):
        accounts.consume_cached_exit('198.51.100.8',observed_at=now-app.settings.crawler_exit_observation_ttl-1)
    bundle=app.acquire_route_execution(items[0][0]['id'],'risk','owner',account_store=accounts)
    try:app.record_login_challenge(bundle)
    finally:bundle.close()
    for a,_,_ in items:
        with pytest.raises(app.CollectionRouteError):app.acquire_route_execution(a['id'],'after-risk','owner',account_store=accounts)
