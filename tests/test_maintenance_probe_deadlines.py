"""Virtual-time parent protocol checks; no browser, network or wall-clock waits."""
import json
import os
import time
from types import SimpleNamespace

import pytest

from backend.audit_agent import crawler_route as route
from backend.audit_agent import crawler_child_runtime
from backend.audit_agent.config import settings


@pytest.fixture
def parent_probe(tmp_path,monkeypatch):
    contract=route.route_contract()
    snapshot=contract.BoundRoute('dy','deadline-account',contract.derive_profile_key('deadline-account'),
        'deadline-proxy',1,1,'proxy.invalid',8080,'','',time.time()+1000,'198.51.100.7',time.time(),
        str(tmp_path/'scheduler.sqlite3'),contract.derive_scope_ids('dy','deadline-account','deadline-proxy','198.51.100.7'))
    bundle=SimpleNamespace(snapshot=snapshot,account_id=snapshot.account_id,execution_id='deadline-fixture',pass_fds=())
    clock=[0.0];arrival=[0.0];payload=[{}];events=[];processes=[]
    class ConfigPipe:
        env={};pass_fds=();error=None
        def __init__(self,snapshot):pass
        def start(self,process):events.append('pipe_started')
        def close(self):events.append('pipe_closed')
    class Selector:
        def register(self,*args):pass
        def select(self,timeout):
            clock[0]+=min(timeout,max(0,arrival[0]-clock[0]))
            return [True] if clock[0]>=arrival[0] else []
        def close(self):events.append('poll_closed')
    class Process:
        pid=123456;returncode=None
        def __init__(self,*args,**kwargs):
            read_fd,write_fd=os.pipe()
            os.write(write_fd,('PROXY_PROBE_RESULT '+json.dumps(payload[0])+'\n').encode())
            os.close(write_fd)
            self.stdout=os.fdopen(read_fd,'rb',buffering=0)
            processes.append(self)
        def wait(self,timeout):
            assert timeout>0
            self.returncode=0
            return 0
    authority=SimpleNamespace(env={},pass_fds=(),start_identity='fixture-start',
        bind_child=lambda pid:events.append('bound'),close=lambda:events.append('authority_closed'))
    monkeypatch.setattr(route,'RouteConfigPipe',ConfigPipe)
    monkeypatch.setattr(route.selectors,'DefaultSelector',Selector)
    monkeypatch.setattr(route.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr('subprocess.Popen',Process)
    monkeypatch.setattr(crawler_child_runtime,'child_environment',lambda:{})
    monkeypatch.setattr(crawler_child_runtime,'child_process_options',lambda:{})
    monkeypatch.setattr(crawler_child_runtime,'authority_for',lambda *args,**kwargs:authority)
    monkeypatch.setattr(route,'_stop_probe_process_group',lambda process:events.append('drain'))
    monkeypatch.setattr(route,'assert_profile_browser_stopped',lambda account_id:events.append('profile_drained'))
    monkeypatch.setattr(route,'_retire_verified_probe_uncertainty',lambda *args:events.append('retired'))
    def run(*,delay,check_login=True,timeout=None,invalid_account=False):
        arrival[0]=delay
        payload[0]=dict(schema_version=1,account_id='other' if invalid_account else snapshot.account_id,
            proxy_id=snapshot.proxy_id,binding_revision=1,config_revision=1,context_id='deadline-context',
            ip='198.51.100.7',observed_at=time.time())
        if check_login:payload[0]['login_result']='success'
        options=dict(check_login=check_login,expected_identity='expected-platform-id',verified_result=check_login)
        if timeout is not None:options['timeout']=timeout
        return route._run_maintenance_probe(bundle,**options)
    yield run,clock,events,processes
    for process in processes:
        assert process.stdout.closed


@pytest.mark.parametrize('preparation,delay',[ (30,100),(240,290) ])
def test_default_login_parent_accepts_identity_after_45_and_90_seconds(parent_probe,monkeypatch,preparation,delay):
    run,clock,events,_=parent_probe
    monkeypatch.setattr(settings,'crawler_login_v2_preparation_timeout_seconds',preparation)
    result=run(delay=delay)
    assert result['login_result']=='success' and result['shutdown_verified'] is True
    assert clock[0]==delay
    assert 'drain' in events and 'profile_drained' in events and 'retired' in events
    assert 'authority_closed' in events and 'pipe_closed' in events


def test_default_echo_parent_still_stops_at_45_seconds(parent_probe):
    run,clock,events,_=parent_probe
    with pytest.raises(route.CollectionRouteError,match='PROXY_UNAVAILABLE'):
        run(delay=46,check_login=False)
    assert clock[0]==45
    assert 'drain' in events and 'retired' not in events
    assert 'authority_closed' in events and 'pipe_closed' in events


@pytest.mark.parametrize('timeout',[3,5,8,90])
def test_explicit_parent_timeout_is_never_extended(parent_probe,timeout):
    run,clock,events,_=parent_probe
    with pytest.raises(route.CollectionRouteError,match='PROXY_UNAVAILABLE'):
        run(delay=timeout+1,timeout=timeout)
    assert clock[0]==timeout
    assert 'drain' in events and 'retired' not in events


def test_explicit_long_echo_timeout_is_not_replaced_by_default_45(parent_probe):
    run,clock,events,_=parent_probe
    receipt=run(delay=60,check_login=False,timeout=90)
    assert receipt.ip=='198.51.100.7' and clock[0]==60
    assert 'retired' in events


def test_late_identity_still_requires_original_account_receipt(parent_probe,monkeypatch):
    run,clock,events,_=parent_probe
    monkeypatch.setattr(settings,'crawler_login_v2_preparation_timeout_seconds',30)
    with pytest.raises(route.CollectionRouteError,match='PROXY_CONFIG_INVALID'):
        run(delay=100,invalid_account=True)
    assert clock[0]==100 and 'drain' in events and 'retired' not in events


def test_default_login_parent_budget_remains_capped_at_300_seconds(parent_probe,monkeypatch):
    run,clock,events,_=parent_probe
    monkeypatch.setattr(settings,'crawler_login_v2_preparation_timeout_seconds',240)
    with pytest.raises(route.CollectionRouteError,match='PROXY_UNAVAILABLE'):
        run(delay=301)
    assert clock[0]==300 and 'drain' in events and 'retired' not in events
