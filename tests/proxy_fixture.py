"""Real local authenticated forward/CONNECT proxy; shared by both repositories.

Only *.proxy-test.invalid is allowed upstream. Origin peers must be recorded
proxy sockets; direct peers are counted as traps and rejected. No external DNS
or upstream network is used by this server.
"""
import base64
import hashlib
import http.client
import json
import os
import selectors
import shutil
import socket
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


class ProxyFixture:
    username = 'fixture-user'
    password = 'canary#/?@:secret'

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.events, self.origins, self.traps = [], [], []
        self.upstreams = {}
        self.reject = False
        self.disconnect = False
        self.exit_ip = '198.51.100.7'
        self.http_exit_ip = None
        self.echo_cors = True
        self.echo_delay = 0
        self.lock = threading.Lock()
        self.stream_ready = threading.Event()
        self.stream_done = threading.Event()
        self.drip_cancelled = threading.Event()
        self.drip_bytes = 0
        self._certificates()
        fixture = self

        class Origin(BaseHTTPRequestHandler):
            def do_GET(self):
                with fixture.lock:
                    link = fixture.upstreams.get(self.client_address[1])
                if link is None:
                    fixture.traps.append({'path': self.path})
                    self.send_error(403)
                    return
                item = {'path': self.path, 'peer': self.client_address[1],
                        'host': self.headers.get('Host'),
                        'request_id': 'origin-' + str(len(fixture.origins) + 1),
                        'proxy_event': link, 'cookie': self.headers.get('Cookie'),
                        'authorization': self.headers.get('Authorization'),
                        'user_agent': self.headers.get('User-Agent',''),
                        'origin_header': self.headers.get('Origin'),
                        'proxy_authorization': self.headers.get('Proxy-Authorization')}
                fixture.origins.append(item)
                if self.path == '/echo' and fixture.echo_delay:
                    time.sleep(fixture.echo_delay)
                if self.path == '/hold':
                    fixture.stream_ready.set()
                    fixture.stream_done.wait(timeout=10)
                if self.path == '/change':
                    fixture.exit_ip = '198.51.100.8'
                if self.path == '/slow-drip':
                    self.send_response(200)
                    self.send_header('Content-Type','application/octet-stream')
                    self.send_header('Content-Length','40')
                    self.end_headers()
                    try:
                        for _ in range(40):
                            self.wfile.write(b'x');self.wfile.flush()
                            fixture.drip_bytes+=1
                            time.sleep(.05)
                    except (BrokenPipeError,ConnectionResetError,OSError):
                        fixture.drip_cancelled.set()
                    return
                if self.path.startswith('/redirect') or self.path=='/headless-entry':
                    self.send_response(302)
                    self.send_header('Location', fixture.url('/identity') if self.path=='/headless-entry' else '/final')
                    body = b''
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json' if self.path.startswith('/echo') else 'text/html')
                    if self.path == '/echo' and fixture.echo_cors:
                        self.send_header('Access-Control-Allow-Origin','*')
                    ip = fixture.http_exit_ip if (self.headers.get('User-Agent', '').startswith('python-') or self.headers.get('User-Agent') == 'ZhanyunProxyProbe/1.0') and fixture.http_exit_ip else fixture.exit_ip
                    body = json.dumps({'ip': ip}).encode() if self.path in ('/echo','/change','/identity') else b'<html><body>proxy fixture<img src="/image"></body></html>'
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError,OSError):pass

            def log_message(self, *_):
                pass

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
        self.https = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.root / 'server.crt', self.root / 'server.key')
        self.https.socket = tls.wrap_socket(self.https.socket, server_side=True)

        class Proxy(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def authenticated(self):
                value = self.headers.get('Proxy-Authorization', '')
                expected = 'Basic ' + base64.b64encode((fixture.username + ':' + fixture.password).encode()).decode()
                if fixture.disconnect:
                    self.connection.close()
                    return False
                if fixture.reject or value != expected:
                    fixture.events.append({'kind': '407', 'target': self.path})
                    self.send_response(407)
                    self.send_header('Proxy-Authenticate', 'Basic realm="fixture"')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return False
                return True

            def upstream(self, host, port, kind):
                if not host.endswith('.proxy-test.invalid') or port not in (fixture.http.server_port, fixture.https.server_port):
                    self.send_error(502)
                    return None
                sock = socket.create_connection(('127.0.0.1', port), timeout=5)
                event = {'kind': kind, 'target': host + ':' + str(port), 'source_port': sock.getsockname()[1], 'path': self.path}
                with fixture.lock:
                    fixture.events.append(event)
                    fixture.upstreams[sock.getsockname()[1]] = len(fixture.events) - 1
                return sock

            def do_CONNECT(self):
                if not self.authenticated():
                    return
                host, port = self.path.rsplit(':', 1)
                upstream = self.upstream(host, int(port), 'CONNECT')
                if upstream is None:
                    return
                source_port=upstream.getsockname()[1]
                self.send_response(200, 'Connection established')
                self.end_headers()
                try:
                    while True:
                        with selectors.DefaultSelector() as poll:
                            poll.register(self.connection,selectors.EVENT_READ)
                            poll.register(upstream,selectors.EVENT_READ)
                            ready=[key.fileobj for key,_ in poll.select(3)]
                        if not ready:
                            break
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            (upstream if source is self.connection else self.connection).sendall(data)
                except (OSError, ConnectionError):
                    pass
                finally:
                    with fixture.lock:
                        fixture.upstreams.pop(source_port,None)
                    upstream.close()
                    self.close_connection = True

            def do_GET(self):
                if not self.authenticated():
                    return
                target = urlsplit(self.path)
                upstream = self.upstream(target.hostname or '', target.port or 80, 'forward')
                if upstream is None:
                    return
                source_port=upstream.getsockname()[1]
                path = target.path or '/'
                if target.query:
                    path += '?' + target.query
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ('proxy-authorization', 'proxy-connection', 'connection')}
                headers['Connection'] = 'close'
                connection = http.client.HTTPConnection('127.0.0.1')
                connection.sock = upstream
                try:
                    connection.request('GET', path, headers=headers)
                    response = connection.getresponse()
                    self.send_response(response.status)
                    for k, v in response.getheaders():
                        if k.lower() not in ('connection', 'transfer-encoding'):
                            self.send_header(k, v)
                    self.end_headers()
                    while True:
                        data=response.read1(65536)
                        if not data:break
                        self.wfile.write(data);self.wfile.flush()
                except (OSError,ConnectionError):
                    pass
                finally:
                    with fixture.lock:
                        fixture.upstreams.pop(source_port,None)
                    connection.close()

            def log_message(self, *_):
                pass

        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        self.servers = (self.http, self.https, self.proxy)
        self.threads = []

    def _certificates(self):
        openssl = shutil.which('openssl')
        assert openssl, 'mandatory OpenSSL dependency missing'
        def run(*args, input=None):
            result = subprocess.run([openssl, *args], cwd=self.root, input=input, capture_output=True, timeout=15)
            assert result.returncode == 0, 'local test certificate generation failed'
            return result.stdout
        run('req', '-sha256', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'ca.key', '-out', 'ca.crt', '-days', '1', '-subj', '/CN=zhanyun-local-fixture-CA',
            '-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign')
        run('req', '-sha256', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'server.key', '-out', 'server.csr', '-subj', '/CN=*.proxy-test.invalid')
        (self.root / 'extensions').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nsubjectAltName=DNS:*.proxy-test.invalid\nextendedKeyUsage=serverAuth\n')
        run('x509', '-sha256', '-req', '-in', 'server.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-CAcreateserial', '-out', 'server.crt', '-days', '1', '-extfile', 'extensions')
        public_key = run('x509', '-in', 'server.crt', '-pubkey', '-noout')
        der = run('pkey', '-pubin', '-outform', 'DER', input=public_key)
        self.spki = base64.b64encode(hashlib.sha256(der).digest()).decode()

    @property
    def server(self):
        return 'http://127.0.0.1:' + str(self.proxy.server_port)

    @property
    def authenticated_url(self):
        return 'http://' + quote(self.username, safe='') + ':' + quote(self.password, safe='') + '@127.0.0.1:' + str(self.proxy.server_port)

    @property
    def ca(self):
        return str(self.root / 'ca.crt')

    def url(self, path='/echo', *, https=True, host='origin'):
        server = self.https if https else self.http
        return ('https' if https else 'http') + '://' + host + '.proxy-test.invalid:' + str(server.server_port) + path

    def start(self):
        for server in self.servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        evidence=dict(forward=sum(e['kind']=='forward' for e in self.events),
                      connect=sum(e['kind']=='CONNECT' for e in self.events),
                      rejected=sum(e['kind']=='407' for e in self.events),
                      origin_requests=len(self.origins),trap_requests=len(self.traps),
                      paths=[o['path'] for o in self.origins],
                      echo_non_echo_requests=sum(o['host'].startswith('echo.') and o['path']!='/echo' for o in self.origins),
                      origin_ids=[o['request_id'] for o in self.origins],
                      linked_origin_requests=sum(self.events[o['proxy_event']]['source_port']==o['peer'] for o in self.origins),
                      tls_trust='explicit-test-CA-for-HTTP-and-specific-leaf-SPKI-for-browser')
        (self.root/'network-evidence.json').write_text(json.dumps(evidence,indent=2))
        print('PROXY_FIXTURE_EVIDENCE '+json.dumps(evidence,separators=(',',':')))

    def assert_routed(self):
        assert not self.traps, 'direct origin connection observed'
        assert self.origins, 'no real origin requests observed'
        assert all(self.events[o['proxy_event']]['source_port'] == o['peer'] for o in self.origins)
        assert all(not o['proxy_authorization'] for o in self.origins)


@contextmanager
def local_proxy(root):
    fixture = ProxyFixture(root).start()
    try:
        yield fixture
    finally:
        fixture.close()


BROWSER_DRIVER = r'''
import asyncio, json, sys, time, os, sqlite3
import importlib.metadata
from pathlib import Path
from dataclasses import replace
config = json.load(sys.stdin)
assert importlib.metadata.version('cloakbrowser')=='0.5.10', 'mandatory pinned CloakBrowser mismatch'
from cloakbrowser.config import get_binary_path
assert Path(get_binary_path()).is_file(), 'mandatory browser binary missing; no download allowed'
os.environ['CLOAKBROWSER_BINARY_PATH']=str(get_binary_path())
os.environ['CLOAKBROWSER_AUTO_UPDATE']='false'
sys.path.insert(0, config['crawler'])
from tools.collection_route import BoundRoute, derive_profile_key, derive_scope_ids, CollectionRouteError
from tools.bound_proxy import BoundProxySession
from tools.cloak_browser import launch_account_context
from tools.httpx_util import make_async_client

def snapshot(account):
 return BoundRoute('dy',account,derive_profile_key(account),'fixture-proxy',1,1,'127.0.0.1',config['proxy_port'],config['username'],config['password'],time.time()+120,None,None,config['db'],derive_scope_ids('dy',account,'fixture-proxy'))

async def main():
 args=['--ignore-certificate-errors-spki-list='+config['spki'], '--host-resolver-rules=MAP *.proxy-test.invalid 127.0.0.1', '--proxy-bypass-list=<-loopback>']
 route=snapshot('account-a')
 if config.get('mode')=='unknown':route=replace(route,expires_at=None)
 session=BoundProxySession(route,echo_url=config['echo'],budget_id='browser-fixture',verify=config['ca'],ttl=config.get('ttl',300),max_calls=config.get('max_calls',96),maintenance_only=config.get('mode')=='maintenance',policy=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1))
 if config.get('mode')=='maintenance':
  with sqlite3.connect(config['db']) as db:db.execute('INSERT OR REPLACE INTO collection_route_health VALUES(?,?,?)',(route.proxy_id,route.config_revision,'PROXY_UNAVAILABLE'))
 context=None
 try:
  context, profile=await launch_account_context('account-a',Path(config['profiles']),headless=True,route_snapshot=route,bound_proxy_session=session,browser_args=args)
  if config.get('mode')=='unknown':
   ip=session.receipt.ip
   try:await session.ensure_business();raise AssertionError('unknown expiry allowed business')
   except CollectionRouteError as exc:assert exc.code=='PROXY_UNAVAILABLE'
   return {'unknown_expiry_probe_ip':ip,'business_allowed':False}
  if config.get('mode')=='maintenance':
   ip=session.receipt.ip
   try:await session.ensure_business();raise AssertionError('maintenance allowed business')
   except CollectionRouteError as exc:assert exc.code=='PROXY_UNAVAILABLE'
   with sqlite3.connect(config['db']) as db:
    stop=db.execute('SELECT stop_code FROM collection_route_health WHERE proxy_id=? AND config_revision=?',(route.proxy_id,route.config_revision)).fetchone()[0]
   return {'maintenance_probe_ip':ip,'stop_preserved':stop,'business_allowed':False}
  if config.get('mode') in ('budget','change'):
   if config['mode']=='change':
    change=await context.new_page(); await change.goto(config['change'],wait_until='domcontentloaded');await change.close()
   await asyncio.sleep(config['ttl']+.1)
   await session.ensure_business()
   return {'unexpected_business_allowed':True}
  if config.get('mode') in ('probe','storage'):
   if config['mode']=='storage':
    state=await context.storage_state()
    return {'ip':session.receipt.ip,'pages':len(context.pages),'used':session.budget.snapshot()['used'],
            'echo_origin_exported':any(o['origin']==config['echo'].rsplit('/',1)[0] for o in state['origins'])}
   return {'ip':session.receipt.ip,'pages':len(context.pages),'used':session.budget.snapshot()['used']}
  page=await context.new_page()
  # Deliberately make the fixture cookie eligible for both business and echo
  # hosts; probe credentials=omit must still keep it away from echo.
  await context.add_cookies([{'name':'platform-secret','value':'fixture-cookie','domain':'.proxy-test.invalid','path':'/'}])
  for url in (config['http'],config['https']):
   async with session.slot('api',timeout=10):
    response=await page.goto(url,wait_until='networkidle',timeout=10000)
    assert response.status==200
  await page.evaluate("localStorage.setItem('identity','account-a')")
  first_seed=profile['seed']; first_context=session.context_id
  await context.close(); context=None
  session=BoundProxySession(route,echo_url=config['echo'],budget_id='browser-fixture',verify=config['ca'],policy=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1))
  context, profile=await launch_account_context('account-a',Path(config['profiles']),headless=True,route_snapshot=route,bound_proxy_session=session,browser_args=args)
  page=await context.new_page(); await page.goto(config['https'],wait_until='networkidle')
  assert await page.evaluate("localStorage.getItem('identity')")=='account-a'
  assert profile['seed']==first_seed and session.context_id!=first_context
  used=session.budget.snapshot()['used']
  await context.close(); context=None
  route_b=snapshot('account-b')
  session_b=BoundProxySession(route_b,echo_url=config['echo'],budget_id='browser-fixture',verify=config['ca'],policy=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1))
  context, profile_b=await launch_account_context('account-b',Path(config['profiles']),headless=True,route_snapshot=route_b,bound_proxy_session=session_b,browser_args=args)
  page=await context.new_page();await page.goto(config['https'],wait_until='networkidle')
  assert await page.evaluate("localStorage.getItem('identity')") is None
  assert profile_b['seed']!=first_seed
  return {'ip':session_b.receipt.ip,'reopen_used':used,'seed_independent':True,'tls':'specific-test-leaf-SPKI','ignore_https_errors':False}
 except CollectionRouteError as exc:
  return {'error':exc.code}
 finally:
  if context is not None:await context.close()
print(json.dumps(asyncio.run(main())))
'''


def run_browser(fixture, root, *, mode='full', ttl=300, max_calls=96, password=None, spki=None):
    """Use installed crawler interpreter; credentials travel over stdin only."""
    crawler = Path(os.environ.get('MEDIACRAWLER_DIR', Path(__file__).resolve().parents[2] / 'wukong-proxy-crawler'))
    python = Path(os.environ.get('LOGIN_BROWSER_TEST_PYTHON', crawler / '.venv/bin/python'))
    assert python.is_file(), 'mandatory crawler interpreter missing'
    payload = dict(crawler=str(crawler),proxy_port=fixture.proxy.server_port,username=fixture.username,
                   password=fixture.password if password is None else password,echo=fixture.url(host='echo'),
                   ca=fixture.ca,spki=fixture.spki if spki is None else spki,profiles=str(Path(root)/'profiles'),db=str(Path(root)/'scheduler.sqlite3'),
                   http=fixture.url('/redirect',https=False),https=fixture.url('/redirect'),change=fixture.url('/change'),mode=mode,ttl=ttl,max_calls=max_calls)
    environment = {**os.environ,'CLOAKBROWSER_AUTO_UPDATE':'false'}
    result = subprocess.run([str(python), '-c', BROWSER_DRIVER],input=json.dumps(payload),text=True,
                            capture_output=True,timeout=40,env=environment)
    assert fixture.password not in result.stdout + result.stderr, 'fixture credential leaked'
    assert quote(fixture.password,safe='') not in result.stdout + result.stderr, 'encoded fixture credential leaked'
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


# This driver injects test trust/local business URL only; production FD parsing,
# old login context, V2 open/reopen and business Document guard run unchanged.
RUNTIME_DRIVER = BROWSER_DRIVER.split('async def main():')[0] + r'''
import socket
from types import SimpleNamespace
from backend.audit_agent.config import settings
import scripts.crawler_account_login as old
import scripts.crawler_douyin_login_adapter as adapter_module
from scripts.crawler_douyin_login_v2 import DouyinLoginV2
from tools import cloak_browser as browser_module
from backend.audit_agent import crawler_route as app_route

settings.media_crawler_dir=Path(config['crawler'])
settings.crawler_browser_profile_root=Path(config['profiles'])
settings.crawler_login_browser_version=''
original_launch=browser_module.launch_account_context
def trusted_session(route,**kwargs):
 return BoundProxySession(route,**kwargs,echo_url=config['echo'],budget_id='runtime-fixture',verify=config['ca'],ttl=1,policy=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1))
app_route.make_bound_proxy_session=trusted_session
async def test_launch(*args,**kwargs):
 route=kwargs['route_snapshot']
 session=kwargs.get('bound_proxy_session') or trusted_session(route)
 kwargs.update(bound_proxy_session=session,browser_args=['--ignore-certificate-errors-spki-list='+config['spki'],'--host-resolver-rules=MAP *.proxy-test.invalid 127.0.0.1','--proxy-bypass-list=<-loopback>'])
 context,profile=await original_launch(*args,**kwargs)
 if config.get('verify_headless'):
  async def local_navigation(route):
   if route.request.is_navigation_request():await route.continue_(url=config['identity'])
   else:await route.abort()
  await context.route('https://www.douyin.com/**',local_navigation)
 return context,profile
browser_module.launch_account_context=test_launch
old.load_account_browser=lambda:browser_module
async def reset_handshake():pass
old.request_login_reset=reset_handshake
original_allowed=adapter_module.document_allowed
adapter_module.document_allowed=lambda url,top_level: url.startswith(config['business_origin']) or original_allowed(url,top_level=top_level)

async def main():
 async with old.login_context('dy',config['account_id'],headless=True) as context:
  session=context._bound_proxy_session
  async with session.slot('api',timeout=10):
   page=await context.new_page();await page.goto(config['http'],wait_until='networkidle')
  old_context=session.context_id
 left,right=socket.socketpair()
 helper=DouyinLoginV2(SimpleNamespace(operation_id='fixture-op',media_fd=left.detach(),account_id=config['account_id'],headed=False,block_media=False,intent='recover'))
 try:
  await helper.open_context()
  session=helper.bound_proxy_session
  assert session.context_id!=old_context
  # Actual installed adapter guard allows only local business fixture plus the
  # usual production allowlist, never echo for a business page.
  async with session.slot('api',timeout=10):
   await helper.adapter.page.goto(config['https'],wait_until='networkidle')
  await helper.adapter.page.evaluate("localStorage.setItem('runtimeIdentity','account-a')")
  before=session.budget.snapshot()['used']
  await asyncio.sleep(1.15)
  await session.ensure_business()
  assert session.budget.snapshot()['used']>before
  first=helper.profile['seed'];context_id=session.context_id
  await helper.close_context();await helper.open_context()
  assert helper.profile['seed']==first and helper.bound_proxy_session.context_id!=context_id
  async with helper.bound_proxy_session.slot('api',timeout=10):
   await helper.adapter.page.goto(config['https'],wait_until='networkidle')
  assert await helper.adapter.page.evaluate("localStorage.getItem('runtimeIdentity')")=='account-a'
  if config.get('stream'):
   try:await helper.adapter.page.goto(config['hold'],timeout=15000,wait_until='domcontentloaded')
   except Exception:
    if not config.get('stop_after_stream'):raise
   if config.get('stop_after_stream'):
    return {'old_v2_reopen':True,'periodic_after_business_guard':True,'recovered_profile':True,'stream_stop_boundary':True}
  await asyncio.sleep(.15)
  blocked_before=helper.adapter.blocked_documents
  try:
   await helper.adapter.page.goto(config['echo'],timeout=2000)
   raise AssertionError('business Document guard allowed echo')
  except Exception as exc:
   assert 'business Document guard allowed echo' not in str(exc)
  assert helper.adapter.blocked_documents>blocked_before, 'forbidden navigation did not hit actual CDP Document guard'
  await helper.close_context()
  config['verify_headless']=True
  import scripts.crawler_interactive_login as interactive
  async def fixture_authenticated(context):return True
  interactive.has_auth=fixture_authenticated
  state=await interactive.headless_state(config['account_id'])
  assert any(v.get('name')=='runtimeIdentity' and v.get('value')=='account-a' for o in state['origins'] for v in o['localStorage'])
  return {'old_v2_reopen':True,'periodic_after_business_guard':True,'business_echo_blocked':True,'recovered_profile':True,'headless_state_reused_profile':True}
 finally:
  await helper.close_context();helper.media.close();right.close()
print(json.dumps(asyncio.run(main())))
'''


def run_runtime_browser(fixture, root, *, snapshot=None, bundle=None, stream_download=None, stop_after_stream=False):
    import time
    from types import SimpleNamespace
    from backend.audit_agent.config import settings
    from backend.audit_agent.crawler_route import RouteReceiptPipe, route_contract
    crawler=Path(os.environ['MEDIACRAWLER_DIR'])
    python=Path(os.environ['LOGIN_BROWSER_TEST_PYTHON'])
    settings.media_crawler_dir=crawler
    contract=route_contract()
    snapshot=snapshot or contract.BoundRoute('dy','account-a',contract.derive_profile_key('account-a'),'fixture-proxy',1,1,'127.0.0.1',fixture.proxy.server_port,fixture.username,fixture.password,time.time()+120,None,None,str(Path(root)/'scheduler.sqlite3'),contract.derive_scope_ids('dy','account-a','fixture-proxy'))
    route_read,route_write=os.pipe()
    os.write(route_write,contract.serialize_route(snapshot));os.close(route_write)
    bundle=bundle or SimpleNamespace(snapshot=snapshot,_lock=threading.Lock(),child_receipt=None)
    receipt_pipe=RouteReceiptPipe(bundle)
    payload=dict(crawler=str(crawler),proxy_port=fixture.proxy.server_port,
                 spki=fixture.spki,echo=fixture.url(host='echo'),ca=fixture.ca,profiles=str(Path(root)/'profiles'),
                 db=snapshot.scheduler_db_path,http=fixture.url('/redirect',https=False),https=fixture.url('/redirect'),
                 business_origin=fixture.url('/',host='origin').rsplit('/',1)[0],account_id=snapshot.account_id,
                 stream=stream_download is not None,hold=fixture.url('/hold'),identity=fixture.url('/headless-entry'),stop_after_stream=stop_after_stream)
    environment={**os.environ,'MEDIACRAWLER_PROXY_CONFIG_FD':str(route_read),
                 'CLOAKBROWSER_AUTO_UPDATE':'false',
                 'MEDIACRAWLER_PROXY_REQUIRED':'true','MEDIACRAWLER_ACCOUNT_ID':snapshot.account_id,
                 'XHS_AUDIT_DATA_DIR':str(Path(root)/'child-data'),'XHS_AUDIT_OUTPUTS_DIR':str(Path(root)/'child-output'),
                 'CRAWLER_AUTH_KEY_FILE':str(Path(root)/'child-key'),
                 **receipt_pipe.env}
    process=subprocess.Popen([str(python),'-c',RUNTIME_DRIVER],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             text=True,env=environment,pass_fds=(route_read,*receipt_pipe.pass_fds),cwd=Path(__file__).resolve().parents[1])
    os.close(route_read)
    receipt_pipe.start(process)
    process.stdin.write(json.dumps(payload));process.stdin.close();process.stdin=None
    observed=[]
    stream_result=None
    deadline=time.monotonic()+40
    try:
        while process.poll() is None and time.monotonic()<deadline:
            try:
                receipt=receipt_pipe.receipt()
                observed.append(receipt.context_id)
            except Exception:
                pass
            if stream_download is not None and fixture.stream_ready.is_set() and stream_result is None:
                stream_result=stream_download(bundle,receipt_pipe.receipt())
                fixture.stream_done.set()
            time.sleep(.02)
        output,error=process.communicate(timeout=2)
        assert fixture.password not in output+error
        assert process.returncode==0,error[-3000:]
        assert observed,'actual child receipt pipe yielded no live browser receipt'
        result=json.loads(output.strip().splitlines()[-1])
        result['receipt_contexts']=len(set(observed))
        if stream_result is not None:
            result['stream']=stream_result
        assert bundle.child_receipt is None,'EOF must invalidate child receipt'
        print('RUNTIME_BROWSER_EVIDENCE '+json.dumps(result,separators=(',',':')))
        return result
    finally:
        fixture.stream_done.set()
        if process.poll() is None:
            process.kill();process.wait(timeout=5)
        receipt_pipe.close()
