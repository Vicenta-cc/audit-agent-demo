import subprocess
from contextlib import contextmanager
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from backend.task_admission import resources


@contextmanager
def preserve_crawler_contract_modules():
    """Temporary runtime fixtures must not replace live contract class identity."""
    import importlib
    from backend.audit_agent import crawler_route
    contract = crawler_route.route_contract()
    original_path = list(sys.path)
    sys.path.insert(0,str(Path(contract.__file__).resolve().parents[1]))
    package = importlib.import_module('tools')
    # Load dependent classes against the canonical contract before selecting a
    # fake crawler root. Their module globals must retain this exact object.
    importlib.import_module('tools.bound_proxy')
    try:
        yield
    finally:
        sys.path[:] = original_path
        sys.modules['tools.collection_route'] = contract
        package.collection_route = contract
        crawler_route._load_contract.cache_clear()


@pytest.fixture
def temporary_crawler_contract_scope():
    with preserve_crawler_contract_modules():
        yield


@pytest.fixture
def locks(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, 'settings', SimpleNamespace(task_resource_lock_dir=tmp_path / 'locks', crawler_browser_profile_root=tmp_path / 'profiles'))


def bundle(account='a', proxy='p'):
    return resources.ExecutionLeaseBundle('execution', 'owner', account, SimpleNamespace(proxy_id=proxy))



def frozen_route(tmp_path):
    import time
    from backend.audit_agent.crawler_route import route_contract
    contract = route_contract()
    return contract.validate_route_envelope(dict(schema_version=1,route_mode='proxy_required',platform='dy',account_id='a',
        profile_key=contract.derive_profile_key('a'),proxy_id='p',binding_revision=1,config_revision=1,
        endpoint=dict(scheme='http',host='proxy.invalid',port=8080),credentials=dict(username='fixture',password='fixture'),
        expires_at=time.time()+3600,observed_exit=None,scheduler=dict(db_path=str(tmp_path/'gate.db'),
        scope_ids=list(contract.derive_scope_ids('dy','a','p')))),account_id='a')


def test_shared_capacity_and_edit_are_nonblocking(locks):
    a, b = bundle(), bundle('b')
    assert a.acquire()
    assert not b.acquire()
    with resources.proxy_edit_lease('p') as editable:
        assert not editable
    a.close()
    assert b.acquire()
    b.close()
    with resources.proxy_edit_lease('p') as editable:
        assert editable


def test_borrow_survives_owner_and_blocks_new_borrow(locks):
    a = bundle()
    assert a.acquire()
    with a.borrow(execution_id='execution', owner_id='owner'):
        a.close()
        with resources.proxy_edit_lease('p') as editable:
            assert not editable
        with pytest.raises(resources.ResourceBusy):
            with a.borrow():
                pass
    with resources.proxy_edit_lease('p') as editable:
        assert editable


def test_outer_account_lease_not_closed_by_bundle(locks):
    handle = resources.acquire_account_handle('dy', 'a')
    a = bundle()
    assert a.acquire(account_handle=handle)
    a.close()
    assert not handle.closed
    assert resources.acquire_account_handle('dy', 'a') is None
    handle.close()
    handle = resources.acquire_account_handle('dy', 'a')
    assert handle is not None
    handle.close()


def test_child_fd_keeps_proxy_busy_after_parent_close(locks):
    a = bundle()
    assert a.acquire()
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE, pass_fds=a.pass_fds)
    try:
        a.close()
        with resources.proxy_edit_lease('p') as editable:
            assert not editable
    finally:
        child.communicate(timeout=5)
    with resources.proxy_edit_lease('p') as editable:
        assert editable


def test_cross_execution_borrow_rejected(locks):
    a = bundle()
    assert a.acquire()
    try:
        with pytest.raises(resources.ResourceBusy):
            with a.borrow(execution_id='other'):
                pass
    finally:
        a.close()


@pytest.mark.parametrize("high_fds", [False, True])
def test_config_pipe_real_child_read_and_separate_stdin(tmp_path, monkeypatch, high_fds):
    import os
    import json
    from backend.audit_agent import crawler_route
    contract = crawler_route.route_contract()
    snapshot = contract.validate_route_envelope(dict(
        schema_version=1, route_mode='proxy_required', platform='dy', account_id='a',
        profile_key=contract.derive_profile_key('a'), proxy_id='p', binding_revision=1,
        config_revision=1, endpoint=dict(scheme='http', host='localhost', port=8080),
        credentials=dict(username='user', password='canary#/?@密'), expires_at=None,
        observed_exit=None, scheduler=dict(db_path=str(tmp_path / 'gate.db'),
        scope_ids=list(contract.derive_scope_ids('dy','a','p')))), account_id='a')
    pipe = crawler_route.RouteConfigPipe(snapshot)
    if high_fds:
        import fcntl
        read_fd = fcntl.fcntl(pipe.read_fd, fcntl.F_DUPFD, 1100)
        write_fd = fcntl.fcntl(pipe.write_fd, fcntl.F_DUPFD, 1100)
        os.close(pipe.read_fd)
        os.close(pipe.write_fd)
        pipe.read_fd, pipe.write_fd = read_fd, write_fd
    env = os.environ.copy()
    env.update(pipe.env)
    inherited_config_fd = pipe.read_fd
    code = "import sys,importlib.util; s=importlib.util.spec_from_file_location('contract',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); r=m.load_route_from_env(account_id='a'); print(r.proxy_id); print(input())"
    child = subprocess.Popen([sys.executable,'-c',code,contract.__file__], pass_fds=pipe.pass_fds, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    pipe.start(child)
    output,_ = child.communicate('old-handshake\n', timeout=5)
    pipe.close()
    assert child.returncode == 0, f"config FD {inherited_config_fd}: child exit {child.returncode}"
    assert output.splitlines() == ['p','old-handshake']
    assert 'canary' not in str(env)


@pytest.mark.parametrize("high_fds", [False, True])
def test_receipt_pipe_accepts_actual_live_child_and_invalidates_eof(locks, tmp_path, high_fds):
    import json
    from backend.audit_agent import crawler_route
    contract = crawler_route.route_contract()
    snapshot = frozen_route(tmp_path)
    a = resources.ExecutionLeaseBundle('execution','owner','a',snapshot)
    assert a.acquire()
    pipe = crawler_route.RouteReceiptPipe(a)
    if high_fds:
        import fcntl
        import os
        read_fd = fcntl.fcntl(pipe.read_fd, fcntl.F_DUPFD, 1100)
        write_fd = fcntl.fcntl(pipe.write_fd, fcntl.F_DUPFD, 1100)
        os.close(pipe.read_fd)
        os.close(pipe.write_fd)
        pipe.read_fd, pipe.write_fd = read_fd, write_fd
    receipt = dict(schema_version=1,type='probe_receipt',platform='dy',account_id='a',proxy_id='p',binding_revision=1,config_revision=1,context_id='context',ip='203.0.113.1',observed_at=__import__('time').time())
    child = subprocess.Popen([sys.executable,'-c','import os,sys;os.write(int(sys.argv[1]),sys.argv[2].encode());sys.stdin.read()',str(pipe.write_fd),json.dumps(receipt)+'\n'], stdin=subprocess.PIPE, pass_fds=pipe.pass_fds)
    try:
        pipe.start(child)
        import time
        deadline=time.monotonic()+2
        while getattr(a,'child_receipt',None) is None and time.monotonic()<deadline:
            time.sleep(.01)
        assert pipe.receipt().ip == '203.0.113.1'
        child.communicate(timeout=5)
        with pytest.raises(crawler_route.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
            pipe.receipt()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        pipe.close()
        a.close()


def test_transferred_borrow_can_finish_after_owner_closes(locks):
    a = bundle()
    assert a.acquire()
    with a.borrow() as transferred:
        a.close()
        with transferred.borrow(execution_id='execution'):
            with resources.proxy_edit_lease('p') as editable:
                assert not editable
    with resources.proxy_edit_lease('p') as editable:
        assert editable
    with pytest.raises(resources.ResourceBusy):
        with transferred.borrow():
            pass


def test_receipt_close_reopen_and_late_close_ordering(locks, tmp_path):
    import json
    import time
    from backend.audit_agent import crawler_route
    snapshot = frozen_route(tmp_path)
    a = resources.ExecutionLeaseBundle('execution','owner','a',snapshot)
    assert a.acquire()
    pipe = crawler_route.RouteReceiptPipe(a)
    base = dict(schema_version=1,platform='dy',account_id='a',proxy_id='p',binding_revision=1,config_revision=1,ip='203.0.113.1',observed_at=time.time())
    events = [dict(base,type='probe_receipt',context_id='a'), dict(base,type='probe_closed',context_id='a'), dict(base,type='probe_receipt',context_id='b'), dict(base,type='probe_closed',context_id='a')]
    child = subprocess.Popen([sys.executable,'-c','import os,sys;os.write(int(sys.argv[1]),sys.argv[2].encode());sys.stdin.read()',str(pipe.write_fd),'\n'.join(json.dumps(event) for event in events)+'\n'], stdin=subprocess.PIPE,pass_fds=pipe.pass_fds)
    try:
        pipe.start(child)
        deadline=time.monotonic()+2
        while (getattr(a,'child_receipt',None) is None or a.child_receipt.context_id!='b') and time.monotonic()<deadline:
            time.sleep(.01)
        assert pipe.receipt().context_id=='b'
    finally:
        child.communicate(timeout=5)
        pipe.close()
        a.close()


def test_explicit_maintenance_activation_clears_stop_only_when_exclusive(locks, tmp_path, monkeypatch):
    import sqlite3
    from backend.audit_agent import crawler_route
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    accounts = CrawlerAccountStore(tmp_path/'accounts.db')
    store = CrawlerProxyStore(accounts, cipher=AuthStateCipher(encryption_key='fixture'),
        mutation_guard=lambda op, proxy, account=None: resources.proxy_mutation_guard(op,proxy,account,account_store=accounts))
    proxy = store.create(display_name='test',endpoint='http://proxy.invalid:8080')
    store.set_health(proxy['id'],status='exit_changed',stop_reason='PROXY_EXIT_CHANGED')
    db = tmp_path/'gate.db'
    monkeypatch.setattr(crawler_route.settings,'request_scheduler_db',db)
    monkeypatch.setattr(crawler_route,'get_proxy_store',lambda account_store=None: store)
    crawler_route.publish_route_stop(proxy['id'],proxy['config_revision'],'PROXY_EXIT_CHANGED')
    active = resources.ExecutionLeaseBundle('run','owner','a',SimpleNamespace(proxy_id=proxy['id']))
    assert active.acquire()
    with pytest.raises(crawler_route.CollectionRouteError,match='PROXY_BUSY'):
        crawler_route.activate_proxy_route(proxy['id'],expected_revision=proxy['config_revision'])
    active.close()
    result = crawler_route.activate_proxy_route(proxy['id'],expected_revision=proxy['config_revision'])
    assert result['status']=='active' and not result['stop_reason']
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT stop_code FROM collection_route_health WHERE proxy_id=? AND config_revision=?',(proxy['id'],result['config_revision'])).fetchone() is None
        # Old revisions retain their sticky stop; no old child can resume.
        assert conn.execute('SELECT stop_code FROM collection_route_health WHERE proxy_id=? AND config_revision=?',(proxy['id'],proxy['config_revision'])).fetchone()[0]=='PROXY_EXIT_CHANGED'


def test_trial_echo_budget_is_shared_across_execution_ids(monkeypatch):
    from backend.audit_agent import crawler_route
    monkeypatch.setattr(crawler_route.settings,'crawler_proxy_trial_id','two-hour-fixture',raising=False)
    first = crawler_route.probe_env(SimpleNamespace(execution_id='run-a',snapshot=object()))
    second = crawler_route.probe_env(SimpleNamespace(execution_id='run-b',snapshot=object()))
    assert first['MEDIACRAWLER_PROXY_PROBE_BUDGET_ID']==second['MEDIACRAWLER_PROXY_PROBE_BUDGET_ID']
    assert first['MEDIACRAWLER_PROXY_TRIAL_ID']=='two-hour-fixture'


def test_trial_dispatch_is_idle_without_manifest_allowlist(monkeypatch):
    from backend.task_admission import scheduler
    from backend.audit_agent.config import settings
    monkeypatch.setattr(settings,'crawler_proxy_trial_id','fixture',raising=False)
    monkeypatch.setattr(settings,'crawler_proxy_trial_job_ids',(),raising=False)
    # No store access means no reconcile or automatic dispatch occurred.
    assert scheduler.dispatch(SimpleNamespace()) is None


def test_trial_manifest_ids_fail_closed_for_wildcards():
    from backend.audit_agent.config import trial_job_ids
    assert trial_job_ids('task-a, task-b,task-a')==('task-a','task-b')
    assert trial_job_ids('task-a,*')==()
    assert trial_job_ids('../all')==()


def test_child_receipt_failure_after_reservation_releases_all_scope_leases(tmp_path, monkeypatch):
    import asyncio
    import json
    import sqlite3
    from backend.audit_agent import crawler_route
    from backend.audit_agent.crawler_browser import load_request_scheduler
    scheduler = load_request_scheduler()
    db = tmp_path/'gate.db'
    scopes = ('dy','dy:account:a','dy:proxy:p','dy:exit:203.0.113.1')
    for scope in scopes:
        scheduler.RequestScheduler(db,platform=scope,min_interval=0,per_minute=100,
            max_concurrency=1,media_interval=0,cooldown_seconds=0)
    gate = scheduler.MultiScopeGate(db,scopes)
    checked = []
    def expired_receipt():
        with sqlite3.connect(db) as conn:
            checked.append(conn.execute('SELECT COUNT(*) FROM request_scheduler_leases').fetchone()[0])
        raise crawler_route.CollectionRouteError('SUPPLEMENT_DEFERRED')
    helper = object.__new__(crawler_route._BorrowedChildSession)
    helper.bundle = SimpleNamespace(child_receipt_pipe=SimpleNamespace(receipt=expired_receipt))
    helper.session = SimpleNamespace(slot=gate.slot)
    async def ensured():
        return gate
    monkeypatch.setattr(helper,'ensure',ensured)
    async def run():
        with pytest.raises(crawler_route.CollectionRouteError,match='SUPPLEMENT_DEFERRED'):
            await helper.reserve(1)
    asyncio.run(run())
    assert checked==[4]
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM request_scheduler_leases').fetchone()[0]==0
        histories = conn.execute('SELECT request_times_json FROM request_scheduler_state').fetchall()
        assert len(histories)==4 and all(len(json.loads(row[0]))==1 for row in histories)


@pytest.mark.parametrize('health_changed', [False, True])
def test_parent_login_commit_marks_frozen_route_atomically(tmp_path, health_changed):
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent.crawler_login_operation_store import CrawlerLoginOperationStore
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    accounts = CrawlerAccountStore(tmp_path/'accounts.db')
    cipher = AuthStateCipher('fixture')
    proxies = CrawlerProxyStore(accounts,cipher=cipher)
    account = accounts.create(platform='dy',display_name='fixture')
    resource = proxies.create(display_name='fixture',endpoint='http://proxy.invalid:8080',expires_at=__import__('time').time()+3600)
    account = proxies.bind(account['id'],resource['id'],expected_binding_revision=account['binding_revision'])
    operations = CrawlerLoginOperationStore(accounts,cipher)
    op = operations.begin(account_id=account['id'],owner_user_id='owner',application_session_id='session',
        owner_token_hash='ownerhash',request_key_hash='request',request_fingerprint='requestfingerprint')
    operations.mark_stage(op['id'],'profile_ready',profile_authority_established=1)
    if health_changed:
        proxies.set_health(resource['id'],status='unavailable',stop_reason='PROXY_UNAVAILABLE')
        with pytest.raises(ValueError,match='PROXY_UNAVAILABLE'):
            operations.commit_auth(op['id'],cipher.encrypt({'cookies':[], 'origins':[]}),'fixture-platform-id')
        unchanged = accounts.get(account['id'])
        assert unchanged['auth_state_revision']==account['auth_state_revision']
        assert not unchanged['has_auth_state']
        assert unchanged['validated_proxy_binding_revision']==0
        assert operations.get(op['id'])['stage']=='profile_ready'
    else:
        result = operations.commit_auth(op['id'],cipher.encrypt({'cookies':[], 'origins':[]}),'fixture-platform-id')
        validated = accounts.get(account['id'])
        assert validated['validated_proxy_binding_revision']==account['binding_revision']
        assert validated['validated_proxy_config_revision']==resource['config_revision']
        assert not validated['needs_proxy_login_validation']
        assert not result['result']['account']['needs_proxy_login_validation']


def test_legacy_login_disabled_and_unbound_route_is_explicit_direct(tmp_path, monkeypatch):
    from backend.audit_agent import crawler_route
    from backend.audit_agent.config import settings
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_login_manager import CrawlerAccountLoginManager
    from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    accounts = CrawlerAccountStore(tmp_path/'accounts.db')
    account = accounts.create(platform='dy',display_name='legacy')
    monkeypatch.setattr(settings,'crawler_proxy_enabled',True)
    monkeypatch.setattr(settings,'app_auth_mode','disabled')
    monkeypatch.setattr(settings,'crawler_browser_profile_root',tmp_path/'profiles')
    monkeypatch.setattr(settings,'task_resource_lock_dir',tmp_path/'locks')
    starts=[]
    def spawn(*args,**kwargs):
        starts.append(args)
        pytest.fail('unbound login spawned a network-capable child')
    monkeypatch.setattr(subprocess,'Popen',spawn)
    legacy = CrawlerAccountLoginManager(store=accounts,cipher=AuthStateCipher('fixture'))
    with pytest.raises(ValueError,match='Login V2'):
        legacy.start(account)
    # An unbound account now has an explicit managed direct route. Manual V2
    # may establish its first observed exit through the guarded probe path.
    direct = crawler_route.resolve_account_route(account['id'],account_store=accounts)
    assert direct.route_mode=='legacy_direct'
    assert starts==[]
    handle=resources.acquire_account_handle('dy',account['id'])
    assert handle is not None
    handle.close()
    monkeypatch.setattr(settings,'crawler_proxy_enabled',False)
    assert crawler_route.resolve_account_route(account['id'],account_store=accounts).route_mode=='legacy_direct'


def test_asset_hop_absolute_deadline_prevents_network_after_slow_helper_creation(tmp_path, monkeypatch):
    import asyncio
    import time
    import httpx
    from backend.audit_agent import crawler_route
    snapshot = frozen_route(tmp_path)
    active = SimpleNamespace(snapshot=snapshot,account_id='a',owner_id='owner')
    monkeypatch.setattr(crawler_route,'authorize_route_owner',lambda *args,**kwargs: None)
    monkeypatch.setattr(crawler_route,'resolve_account_route',lambda *args,**kwargs: snapshot)
    opened=[]
    class Helper:
        async def ensure_open(self):
            opened.append(True)
            pytest.fail('setup started after absolute deadline')
        def call(self, coroutine):
            return asyncio.run(coroutine)
    def slow_factory(*args,**kwargs):
        time.sleep(.1)
        return Helper()
    monkeypatch.setattr(crawler_route,'_asset_helper',slow_factory)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs: pytest.fail('network started after deadline'))
    with pytest.raises(crawler_route.CollectionRouteError,match='PROXY_UNAVAILABLE'):
        crawler_route.download_asset_hop(active,'https://origin.invalid/media',{},.05,True,lambda _: None)
    assert opened==[]


def test_v2_reader_accepts_real_prefixed_route_event_before_control_json(tmp_path, monkeypatch, capsys):
    import io
    import json
    import time
    from dataclasses import replace
    from backend.audit_agent import crawler_route
    from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    import sys
    sys.path.insert(0,str(crawler_route.settings.media_crawler_dir))
    from tools.bound_proxy import BoundProxySession
    accounts=CrawlerAccountStore(tmp_path/'accounts.db')
    proxies=CrawlerProxyStore(accounts,cipher=AuthStateCipher('fixture'))
    resource=proxies.create(display_name='fixture',endpoint='http://proxy.invalid:8080',expires_at=time.time()+3600)
    snapshot=replace(frozen_route(tmp_path),proxy_id=resource['id'],
        scope_ids=crawler_route.route_contract().derive_scope_ids('dy','a',resource['id']))
    sender=object.__new__(BoundProxySession)
    sender.snapshot=snapshot
    receipt=crawler_route.route_contract().ProbeReceipt(resource['id'],1,'context','203.0.113.1',time.time())
    sender._event('ROUTE_OBSERVED',receipt)
    line=capsys.readouterr().out
    monkeypatch.setattr(crawler_route, '_child_loaded', False)
    monkeypatch.setattr(crawler_route, '_child_snapshot', None)
    monkeypatch.setattr(crawler_route, 'load_child_route', lambda account_id: snapshot)
    crawler_route.child_route_snapshot('a')
    ack_line=capsys.readouterr().out
    ack=json.loads(ack_line)
    assert ack['type']=='proxy_config_ack' and ack['code']=='OK'
    normal=dict(type='notice',code='CURRENT_PHASE_UNKNOWN')
    session=SimpleNamespace(route_bundle=SimpleNamespace(snapshot=snapshot,proxy_store=proxies),
        process=SimpleNamespace(stdout=io.BytesIO((line+ack_line+json.dumps(normal)+'\n').encode()),wait=lambda **_: 0),
        snapshot={'phase':'done'},id='session')
    manager=object.__new__(CrawlerLoginV2Manager)
    manager._lock=__import__('threading').RLock()
    events=[];failures=[];cleanup=[]
    monkeypatch.setattr(manager,'handle_event',lambda sid,event: events.append(event))
    monkeypatch.setattr(manager,'_finish',lambda *args: failures.append(args))
    monkeypatch.setattr(manager,'_cleanup',lambda _: cleanup.append(True))
    manager._reader(session)
    assert events==[ack,normal] and failures==[] and cleanup==[True]
    assert proxies.get_public(resource['id'])['observed_exit_ip']=='203.0.113.1'


@pytest.mark.parametrize('failure_kind,expected_code', [
    ('malformed','PROXY_CONFIG_INVALID'),
    ('stale','PROXY_REVISION_MISMATCH'),
    ('fatal','PROXY_AUTH_FAILED'),
])
def test_v2_reader_preserves_route_error_classification(tmp_path, monkeypatch, failure_kind, expected_code):
    import io
    import json
    import time
    import threading
    from backend.audit_agent import crawler_route
    from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager
    snapshot=frozen_route(tmp_path)
    calls=[]
    store=SimpleNamespace(set_health=lambda *args,**kwargs: calls.append(('health',kwargs)))
    event=dict(schema_version=1,code='PROXY_AUTH_FAILED',proxy_id='p',config_revision=1,
        account_id='a',observed_exit_ip=None,observed_at=time.time())
    if failure_kind=='malformed':
        event['unexpected']='secret-canary'
    elif failure_kind=='stale':
        event['config_revision']=2
    monkeypatch.setattr(crawler_route,'publish_route_stop',lambda *args: calls.append(('stop',args)))
    session=SimpleNamespace(route_bundle=SimpleNamespace(snapshot=snapshot,proxy_store=store),
        process=SimpleNamespace(stdout=io.BytesIO(('COLLECTION_ROUTE_EVENT '+json.dumps(event)+'\n').encode())),
        snapshot={'phase':'checking'},id='session')
    manager=object.__new__(CrawlerLoginV2Manager)
    manager._lock=threading.RLock()
    errors=[];cleanup=[]
    monkeypatch.setattr(manager,'_finish',lambda s,phase,code: errors.append((phase,code)))
    monkeypatch.setattr(manager,'_cleanup',lambda _: cleanup.append(True))
    manager._reader(session)
    assert errors==[('failed',expected_code)]
    assert cleanup==[True]
    if failure_kind!='fatal':
        assert calls==[]
    assert 'secret-canary' not in str(errors)


@pytest.mark.parametrize("known_exit", [None, "198.51.100.9"])
def test_maintenance_probe_uses_crawler_interpreter_without_app_browser_dependencies(tmp_path, monkeypatch, known_exit, temporary_crawler_contract_scope):
    import builtins
    import shutil
    import time
    from backend.audit_agent import crawler_route
    from backend.audit_agent.config import settings
    snapshot=frozen_route(tmp_path)
    if known_exit is not None:
        from dataclasses import replace
        snapshot=replace(snapshot,observed_exit_ip=known_exit,observed_exit_at=time.time(),
            scope_ids=crawler_route.route_contract().derive_scope_ids('dy','a','p',known_exit))
    crawler=tmp_path/'crawler'
    (crawler/'tools').mkdir(parents=True)
    (crawler/'tools/__init__.py').write_text('')
    shutil.copyfile(crawler_route.route_contract().__file__,crawler/'tools/collection_route.py')
    shutil.copyfile(Path(crawler_route.route_contract().__file__).with_name('authority_ledger.py'),crawler/'tools/authority_ledger.py')
    (crawler/'tools/bound_proxy.py').write_text('''from tools.collection_route import ProbeReceipt
import time
class BoundProxySession:
 def __init__(self,snapshot,maintenance_only):
  assert maintenance_only is True
  self.receipt=ProbeReceipt(snapshot.proxy_id,snapshot.config_revision,'fixture-context','203.0.113.1',time.time())
 async def close(self): pass
''')
    (crawler/'tools/camoufox_browser.py').write_text('''import sys
async def launch_account_context(account_id,root,*,headless,route_snapshot,bound_proxy_session):
 assert sys.executable.endswith('python') or 'Python' in sys.executable or 'python3' in sys.executable
 class Context:
  async def close(self): pass
 return Context(),{}
''')
    monkeypatch.setattr(settings,'media_crawler_dir',crawler)
    monkeypatch.setattr(settings,'crawler_login_python',Path(sys.executable))
    monkeypatch.setattr(settings,'crawler_browser_profile_root',tmp_path/'profiles')
    original_import=builtins.__import__
    def app_without_browser(name,*args,**kwargs):
        if name.startswith(('playwright','camoufox','tools.bound_proxy','tools.camoufox_browser')):
            raise ModuleNotFoundError(name)
        return original_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',app_without_browser)
    receipt=crawler_route._run_maintenance_probe(SimpleNamespace(snapshot=snapshot,
        account_id='a',execution_id='maintenance-fixture',pass_fds=(),account_store=SimpleNamespace(get=lambda account_id: {'profile_engine':'camoufox','profile_generation':'fixture-generation'})),timeout=3)
    assert receipt.ip=='203.0.113.1'
    assert receipt.context_id=='fixture-context'


def test_maintenance_probe_missing_configured_dependency_is_safe_and_releases_child(tmp_path,monkeypatch,temporary_crawler_contract_scope):
    import shutil
    from backend.audit_agent import crawler_route
    from backend.audit_agent.config import settings
    snapshot=frozen_route(tmp_path)
    crawler=tmp_path/'crawler'
    (crawler/'tools').mkdir(parents=True)
    (crawler/'tools/__init__.py').write_text('')
    shutil.copyfile(crawler_route.route_contract().__file__,crawler/'tools/collection_route.py')
    shutil.copyfile(Path(crawler_route.route_contract().__file__).with_name('authority_ledger.py'),crawler/'tools/authority_ledger.py')
    # Deliberately absent bound_proxy/browser dependency in selected child runtime.
    monkeypatch.setattr(settings,'media_crawler_dir',crawler)
    monkeypatch.setattr(settings,'crawler_login_python',Path(sys.executable))
    with pytest.raises(crawler_route.CollectionRouteError,match='PROXY_UNAVAILABLE'):
        crawler_route._run_maintenance_probe(SimpleNamespace(snapshot=snapshot,
            account_id='a',execution_id='maintenance-fixture',pass_fds=(),account_store=SimpleNamespace(get=lambda account_id: {'profile_engine':'camoufox','profile_generation':'fixture-generation'})),timeout=3)


def test_maintenance_probe_crashed_leader_cleans_live_descendant_lease(locks,tmp_path,monkeypatch,temporary_crawler_contract_scope):
    import os
    import shutil
    import time
    from backend.audit_agent import crawler_route
    from backend.audit_agent.config import settings
    snapshot=frozen_route(tmp_path)
    crawler=tmp_path/'crawler'
    (crawler/'tools').mkdir(parents=True)
    (crawler/'tools/__init__.py').write_text('')
    shutil.copyfile(crawler_route.route_contract().__file__,crawler/'tools/collection_route.py')
    shutil.copyfile(Path(crawler_route.route_contract().__file__).with_name('authority_ledger.py'),crawler/'tools/authority_ledger.py')
    (crawler/'tools/bound_proxy.py').write_text('''import os,signal,time
from pathlib import Path
class BoundProxySession:
 def __init__(self,*args,**kwargs):
  pid=os.fork()
  if pid==0:
   os.close(1);os.close(2)
   signal.signal(signal.SIGTERM,signal.SIG_IGN)
   Path(FIXTURE_PIDFILE).write_text(str(os.getpid()))
   while True: time.sleep(1)
  time.sleep(.1)
  os._exit(1)
'''.replace('FIXTURE_PIDFILE',repr(str(tmp_path/'descendant.pid'))))
    (crawler/'tools/camoufox_browser.py').write_text('async def launch_account_context(*args,**kwargs): raise AssertionError()')
    monkeypatch.setattr(settings,'media_crawler_dir',crawler)
    monkeypatch.setattr(settings,'crawler_login_python',Path(sys.executable))
    pidfile=tmp_path/'descendant.pid'
    monkeypatch.setenv('FIXTURE_DESCENDANT_PID',str(pidfile))
    active=resources.ExecutionLeaseBundle('maintenance-fixture','owner','a',snapshot)
    active.account_store=SimpleNamespace(get=lambda account_id: {'profile_engine':'camoufox','profile_generation':'fixture-generation'})
    assert active.acquire()
    try:
        with pytest.raises(crawler_route.CollectionRouteError,match='PROXY_UNAVAILABLE'):
            crawler_route._run_maintenance_probe(active,timeout=3)
        assert pidfile.is_file()
        descendant=int(pidfile.read_text())
        output=subprocess.run(['ps','-p',str(descendant),'-o','stat='],capture_output=True,text=True).stdout.strip()
        assert not output or output.startswith('Z')
    finally:
        active.close()
    # A live descendant would still hold the inherited shared proxy FD.
    with resources.proxy_edit_lease('p') as editable:
        assert editable


def test_maintenance_changed_exit_records_health_without_activating_resource(locks,tmp_path,monkeypatch):
    import time
    from backend.audit_agent import crawler_route
    from backend.audit_agent.config import settings
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    accounts=CrawlerAccountStore(tmp_path/'accounts.db')
    proxies=CrawlerProxyStore(accounts,cipher=AuthStateCipher('fixture'))
    account=accounts.create(platform='dy',display_name='fixture')
    resource=proxies.create(display_name='fixture',endpoint='http://proxy.invalid:8080',expires_at=time.time()+3600)
    account=proxies.bind(account['id'],resource['id'],expected_binding_revision=account['binding_revision'])
    proxies.observe_exit(resource['id'],'198.51.100.9',observed_at=time.time()-1)
    proxies.set_health(resource['id'],status='maintenance',stop_reason='PROXY_UNAVAILABLE')
    monkeypatch.setattr(settings,'app_auth_mode','disabled')
    monkeypatch.setattr(settings,'crawler_proxy_enabled',True)
    monkeypatch.setattr(crawler_route,'get_proxy_store',lambda account_store=None: proxies)
    def changed_child(bundle):
        return crawler_route.route_contract().ProbeReceipt(resource['id'],resource['config_revision'],
            'maintenance-context','203.0.113.1',time.time())
    monkeypatch.setattr(crawler_route,'_run_maintenance_probe',changed_child)
    result=crawler_route.probe_account_route(account['id'],'owner',account_store=accounts,
        expected_binding_revision=account['binding_revision'],expected_config_revision=resource['config_revision'])
    assert result['observed_exit_ip']=='203.0.113.1'
    assert result['status']=='exit_changed' and result['business_enabled'] is False
    saved=proxies.get_public(resource['id'])
    assert saved['stop_reason']=='PROXY_EXIT_CHANGED'
    assert saved['config_revision']==resource['config_revision']
    with resources.proxy_edit_lease(resource['id']) as editable:
        assert editable


def test_route_failure_diagnostic_logs_stage_class_without_sensitive_arguments(monkeypatch,caplog):
    import threading
    from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager
    manager=object.__new__(CrawlerLoginV2Manager)
    manager._lock=threading.RLock()
    manager.sessions={'fixture-session':SimpleNamespace(operation_id='fixture-operation',snapshot={'phase':'checking'})}
    event=dict(type='diagnostic_failure',stage='entry_navigation',exception_class='CollectionRouteError',
        error_pattern='unknown',method='goto',timeout_ms=45000,
        source_frame={'file':'crawler_douyin_login_v2.py','function':'flow','line':800},
        message='http://user:SECRET@proxy.invalid',arguments=['SECRET'])
    with caplog.at_level('WARNING'):
        manager.handle_event('fixture-session',event)
    assert 'stage=entry_navigation' in caplog.text
    assert 'exception_class=CollectionRouteError' in caplog.text
    assert 'SECRET' not in caplog.text and 'proxy.invalid' not in caplog.text


@pytest.mark.parametrize('trial_id,login_bootstrap', [('paid-trial',True),('paid-trial',False),('',False)])
def test_bound_session_factory_explicit_login_bootstrap_does_not_change_other_consumers(tmp_path,monkeypatch,trial_id,login_bootstrap):
    from backend.audit_agent import crawler_route
    from backend.audit_agent.config import settings
    snapshot=frozen_route(tmp_path)
    import sys
    sys.path.insert(0,str(settings.media_crawler_dir))
    import tools.bound_proxy as transport
    captured=[]
    monkeypatch.setattr(transport,'BoundProxySession',lambda value,**kwargs: captured.append((value,kwargs)) or object())
    monkeypatch.setattr(settings,'crawler_proxy_trial_id',trial_id)
    monkeypatch.setattr(crawler_route,'resolve_account_route',lambda *args,**kwargs: snapshot)
    if login_bootstrap:
        crawler_route.make_bound_proxy_session(snapshot,login_bootstrap=True)
    else:
        crawler_route.make_bound_proxy_session(snapshot)
    assert captured[0][1]['login_bootstrap'] is login_bootstrap
    assert captured[0][1]['trial_id']==trial_id
    assert captured[0][1]['stop_checker']() is False



def test_temporary_crawler_runtime_restores_receipt_class_identity(tmp_path):
    import shutil
    from dataclasses import asdict
    from backend.audit_agent import crawler_route
    contract=crawler_route.route_contract()
    with preserve_crawler_contract_modules():
        from tools.bound_proxy import BoundProxySession
    snapshot=frozen_route(tmp_path)
    session=BoundProxySession(snapshot,authority_owner=True,echo_url='https://echo.invalid/ip',
        budget_id='contract-isolation',policy=dict(min_interval=0,per_minute=100,max_concurrency=1,media_interval=0,cooldown_seconds=1))
    receipt=session.guard.confirm_pair('context','198.51.100.1','198.51.100.1')
    session.context_id='context'
    alternate=tmp_path/'alternate_collection_route.py'
    shutil.copyfile(contract.__file__,alternate)
    with preserve_crawler_contract_modules():
        other=crawler_route._load_contract(str(alternate))
        assert sys.modules['tools.collection_route'] is other
        assert crawler_route.route_contract() is contract
        session.receipt=other.ProbeReceipt(**asdict(receipt))
        # Identical fields from a different class must still fail the original
        # receipt guard; test isolation fixes the module state, not that guard.
        with pytest.raises(contract.CollectionRouteError,match='PROXY_REVISION_MISMATCH'):
            session._make_gate()
    assert sys.modules['tools.collection_route'] is contract
    assert crawler_route.route_contract() is contract
    session.receipt=sys.modules['tools.collection_route'].ProbeReceipt(**asdict(receipt))
    assert session._make_gate().scope_ids
