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


@pytest.mark.parametrize('mode',['proxy_preferred','direct_degraded'])
def test_real_browser_managed_ack_and_explicit_transport(tmp_path,monkeypatch,mode):
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
            target=f'https://origin.proxy-test.invalid:{server.server_port}/business'
        else:
            echo=fixture.url(host='echo');target=fixture.url('/business')
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
        accounts.admit_route(account['id'],fixture.exit_ip,1,snap.operation_id)
        store=app.RouteOperationStore(tmp_path/'ops.db');store_operation(store,snap)
        bundle=SimpleNamespace(snapshot=snap,route_operation_store=store,_lock=threading.RLock(),account_store=accounts)
        route_pipe,receipt_pipe=app.RouteConfigPipe(snap),app.RouteReceiptPipe(bundle)
        script=r'''import asyncio,json,os,socket,sys
from pathlib import Path
from cloakbrowser.config import get_binary_path
assert Path(get_binary_path()).is_file()
os.environ['CLOAKBROWSER_BINARY_PATH']=str(get_binary_path());os.environ['CLOAKBROWSER_AUTO_UPDATE']='false'
config=json.load(sys.stdin)
original=socket.getaddrinfo
def local_dns(host,*args,**kwargs):
 local = (isinstance(host,str) and host.endswith('.proxy-test.invalid')) or (isinstance(host,bytes) and host.endswith(b'.proxy-test.invalid'))
 return original('127.0.0.1' if local else host,*args,**kwargs)
socket.getaddrinfo=local_dns
from tools.bound_proxy import initialize_bound_route,BoundProxySession
from tools.cloak_browser import launch_account_context
async def main():
 snap=initialize_bound_route()
 session=BoundProxySession(snap,echo_url=config['echo'],verify=config['ca'],budget_id='managed-browser',policy=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1))
 context=None
 try:
  context,profile=await launch_account_context(snap.account_id,Path(config['profiles']),headless=True,route_snapshot=snap,bound_proxy_session=session,browser_args=['--ignore-certificate-errors-spki-list='+config['spki'],'--host-resolver-rules=MAP *.proxy-test.invalid 127.0.0.1','--proxy-bypass-list=<-loopback>'])
  async with session.slot('navigation',timeout=15):
   page=await context.new_page();await page.goto(config['target'],wait_until='domcontentloaded');await page.close()
  assert session.receipt.ip==snap.observed_exit_ip
  assert session.context_id==snap.context_id
  print('BROWSER_ACK_OK',flush=True)
  import sqlite3
  with sqlite3.connect(snap.safety_db_path) as db:
   db.execute("UPDATE crawler_shared_exits SET pause_reason='ACCOUNT_CHALLENGE',paused_until=? WHERE exit_key=?",(__import__('time').time()+30,snap.observed_exit_ip))
  page=await context.new_page()
  try:
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
        env={**os.environ,**route_pipe.env,**receipt_pipe.env,'PYTHONPATH':crawler,'MEDIACRAWLER_ACCOUNT_ID':account['id'],'MEDIACRAWLER_PROXY_REQUIRED':'true',
            'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':''}
        child=subprocess.Popen(['/Users/hose/project/zhanyun/media-crawler/.venv/bin/python','-c',script],env=env,cwd=crawler,
            pass_fds=(*route_pipe.pass_fds,*receipt_pipe.pass_fds),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            route_pipe.start(child);receipt_pipe.start(child)
            import json
            out,err=child.communicate(json.dumps(dict(echo=echo,target=target,ca=fixture.ca,spki=fixture.spki,profiles=str(tmp_path/'profiles'))),timeout=45)
            assert fixture.password not in out+err
            assert child.returncode==0,err[-2500:]
            assert 'BROWSER_ACK_OK' in out and 'BROWSER_PAUSE_FENCED' in out
            if mode=='proxy_preferred':
                fixture.assert_routed();assert any(e['kind']=='CONNECT' for e in fixture.events)
                assert not any(o['path'].endswith('/paused') for o in fixture.origins)
            else:
                assert not fixture.events and len(direct_requests)>=3
                assert all('Proxy-Authorization' not in h for h in direct_requests)
                assert not any(h['path'].endswith('/paused') for h in direct_requests)
        finally:
            if child.poll() is None:child.kill();child.wait()
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


@pytest.mark.parametrize('cold_direct',[False,True])
def test_actual_bounded_login_checker_uses_same_profile_and_local_identity(tmp_path,monkeypatch,cold_direct):
    import json
    from proxy_fixture import local_proxy
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
        for name,value in dict(crawler_login_python=Path('/Users/hose/project/zhanyun/media-crawler/.venv/bin/python'),
            crawler_proxy_echo_url=echo,crawler_browser_profile_root=tmp_path/'profiles',request_scheduler_db=tmp_path/'gate.db',
            crawler_safety_enabled=True,crawler_direct_degrade_enabled=True,app_auth_mode='disabled',task_resource_lock_dir=tmp_path/'locks').items():
            monkeypatch.setattr(app.settings,name,value)
        accounts=CrawlerAccountStore(tmp_path/'accounts.db');account=accounts.create(platform='dy',display_name='bounded-login')
        accounts.save_auth_state(account['id'],'fixture-ciphertext');accounts.observe_shared_exit(fixture.exit_ip)
        snap=replace(managed(tmp_path),account_id=account['id'],profile_key=app.route_contract().derive_profile_key(account['id']),
            host='127.0.0.1',port=fixture.proxy.server_port,username=fixture.username,password=fixture.password,
            observed_exit_ip=fixture.exit_ip,scope_ids=app.route_contract().derive_scope_ids('dy',account['id'],'p',fixture.exit_ip),safety_db_path=str(accounts.db_path))
        snap=replace(snap,snapshot_digest=app.route_contract().snapshot_digest(snap))
        accounts.admit_route(account['id'],fixture.exit_ip,1,snap.operation_id)
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
from cloakbrowser.config import get_binary_path
os.environ['CLOAKBROWSER_BINARY_PATH']=str(get_binary_path());os.environ['CLOAKBROWSER_AUTO_UPDATE']='false'
import tools.bound_proxy as transport
import tools.cloak_browser as browser
transport.BoundProxySession=functools.partial(transport.BoundProxySession,echo_url=os.environ['FIXTURE_ECHO'],verify=os.environ['FIXTURE_CA'],policy=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1))
original=browser.launch_account_context
async def trusted(*args,**kwargs):
 kwargs['browser_args']=['--ignore-certificate-errors-spki-list='+os.environ['FIXTURE_SPKI'],'--host-resolver-rules=MAP *.proxy-test.invalid 127.0.0.1','--proxy-bypass-list=<-loopback>']
 context,profile=await original(*args,**kwargs)
 async def local(route):
  url=route.request.url
  if url.startswith(os.environ['FIXTURE_ECHO']):await route.fallback();return
  if url.startswith('https://www.douyin.com/user/self'):
   await route.fulfill(status=200,content_type='text/html',body='<html><body>local identity fixture<script>fetch("/aweme/v1/web/user/profile/self");</script></body></html>');return
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
                env={**kwargs['env'],'FIXTURE_ECHO':echo,'FIXTURE_CA':fixture.ca,'FIXTURE_SPKI':fixture.spki,'FIXTURE_HELPER':str(helper)}
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
            assert app._run_maintenance_probe(bundle,check_login=True,expected_identity='fixture_identity_123')=='success'
        assert (tmp_path/'profiles'/snap.profile_key/'adapter.lock').exists()
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
