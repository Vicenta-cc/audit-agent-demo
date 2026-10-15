import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from backend.audit_agent import crawler_route as app
from scripts import crawler_proxy_probe as helper


def test_real_crawler_httpx_exception_is_classified_without_raw_message():
    script="import httpx;from scripts.crawler_proxy_probe import emit_probe_diagnostic;emit_probe_diagnostic(httpx.ConnectError('https://secret.invalid/password'))"
    result=subprocess.run(['/Users/hose/project/zhanyun/media-crawler/.venv/bin/python','-c',script],capture_output=True,text=True,check=True,timeout=5)
    assert 'secret' not in result.stdout+result.stderr
    assert json.loads(result.stdout.split(' ',1)[1])=={'stage':'unknown','error_class':'httpx','category':'unknown'}


@pytest.mark.asyncio
@pytest.mark.parametrize('origin',['_browser_sample','_http_sample','unrecognized'])
async def test_actual_helper_failure_branch_preserves_primary_safe_stage_before_cleanup(tmp_path,monkeypatch,capsys,origin):
    monkeypatch.setattr(helper,'_diagnostic_emitted',False)
    monkeypatch.setenv('MEDIACRAWLER_DIR',str(tmp_path))
    monkeypatch.setenv('MEDIACRAWLER_CAMOUFOX_PROFILE_ROOT',str(tmp_path))
    namespace={}
    exec(compile('async def '+origin+'():\n raise RuntimeError("https://secret.invalid/password")','/private/local/bound_proxy.py','exec'),namespace)
    async def launch(*args,**kwargs):await namespace[origin]()
    class Session:
        def __init__(self,*args,**kwargs):pass
        async def close(self):raise ValueError('secret-cleanup-cookie')
    monkeypatch.setitem(sys.modules,'tools.collection_route',SimpleNamespace(load_route_from_env=lambda **kwargs:SimpleNamespace()))
    monkeypatch.setitem(sys.modules,'tools.bound_proxy',SimpleNamespace(BoundProxySession=Session))
    monkeypatch.setitem(sys.modules,'tools.camoufox_browser',SimpleNamespace(launch_account_context=launch))
    with pytest.raises(ValueError):await helper.probe('a')
    lines=capsys.readouterr().out.splitlines()
    assert len(lines)==1 and 'secret' not in lines[0]
    value=json.loads(lines[0].split(' ',1)[1])
    assert value=={'stage':'browser_echo' if origin=='_browser_sample' else 'http_echo' if origin=='_http_sample' else 'browser_startup','error_class':'runtime','category':'unknown'}


@pytest.mark.parametrize('alteration',[{}, {'url':'https://secret.invalid/password'}, {'category':'secret-cookie'}, {'stage':True}, {'error_class':[]}])
def test_parent_only_logs_fixed_diagnostic_fields(tmp_path,caplog,alteration):
    value={'stage':'browser_echo','error_class':'route','category':'unknown'};value.update(alteration)
    accepted=app._consume_probe_diagnostic(json.dumps(value),SimpleNamespace(execution_id='login-safe'))
    assert accepted==(not alteration)
    assert 'secret' not in caplog.text
    assert ('execution_id=login-safe' in caplog.text)==(not alteration)
    assert not app._consume_probe_diagnostic('{"stage":"browser_echo","stage":"http_echo","error_class":"route","category":"unknown"}',SimpleNamespace(execution_id='login-safe'))


def test_actual_subprocess_diagnostic_does_not_replace_probe_error(tmp_path,monkeypatch,caplog):
    c=app.route_contract()
    snapshot=c.BoundRoute('dy','a',c.derive_profile_key('a'),'p',1,1,'127.0.0.1',9,'','',time.time()+120,None,None,str(tmp_path/'gate.db'),c.derive_scope_ids('dy','a','p',None))
    bundle=SimpleNamespace(snapshot=snapshot,account_id='a',execution_id='login-safe',pass_fds=(),account_store=SimpleNamespace(get=lambda account_id: {'profile_engine':'camoufox','profile_generation':'fixture-generation'}))
    original=subprocess.Popen
    def child(command,**kwargs):
        if len(command)>1 and command[1].endswith('crawler_proxy_probe.py'):
            script="import os,json;os.read(int(os.environ['MEDIACRAWLER_PROXY_CONFIG_FD']),65536);print('PROXY_PROBE_DIAGNOSTIC '+json.dumps(dict(stage='http_echo',error_class='route',category='proxy_auth')),flush=True);print('PROXY_PROBE_ERROR '+json.dumps(dict(code='PROXY_AUTH_FAILED')),flush=True)"
            return original([sys.executable,'-c',script],**kwargs)
        return original(command,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',child)
    with pytest.raises(app.CollectionRouteError,match='PROXY_AUTH_FAILED'):
        app._run_maintenance_probe(bundle,timeout=5)
    assert 'execution_id=login-safe stage=http_echo' in caplog.text


@pytest.mark.parametrize('failure',['partial_result','no_result','drain','profile_drain'])
def test_failed_child_or_unconfirmed_drain_cannot_retire_health(tmp_path,monkeypatch,failure):
    c=app.route_contract()
    snapshot=c.BoundRoute('dy','a',c.derive_profile_key('a'),'p',1,1,'127.0.0.1',9,'','',time.time()+120,'203.0.113.10',time.time(),str(tmp_path/'gate.db'),c.derive_scope_ids('dy','a','p','203.0.113.10'))
    bundle=SimpleNamespace(snapshot=snapshot,account_id='a',execution_id='login-safe',pass_fds=(),account_store=SimpleNamespace(get=lambda account_id: {'profile_engine':'camoufox','profile_generation':'fixture-generation'}))
    result=dict(schema_version=1,account_id='a',proxy_id='p',binding_revision=1,config_revision=1,context_id='verified',ip='203.0.113.10',observed_at=time.time())
    original=subprocess.Popen;retired=[]
    def child(command,**kwargs):
        if len(command)>1 and command[1].endswith('crawler_proxy_probe.py'):
            output='' if failure=='no_result' else 'PROXY_PROBE_RESULT '+json.dumps(result)
            script="import os;os.read(int(os.environ['MEDIACRAWLER_PROXY_CONFIG_FD']),65536);print("+repr(output)+",flush=True);raise SystemExit("+('0' if failure in {'drain','profile_drain'} else '1')+")"
            return original([sys.executable,'-c',script],**kwargs)
        return original(command,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',child)
    monkeypatch.setattr(app,'_retire_verified_probe_uncertainty',lambda *args:retired.append(True))
    if failure=='drain':
        def failed_drain(process):raise app.CollectionRouteError('PROXY_UNAVAILABLE')
        monkeypatch.setattr(app,'_stop_probe_process_group',failed_drain)
    elif failure=='profile_drain':
        def failed_profile(account_id):raise app.CollectionRouteError('PROXY_BUSY')
        monkeypatch.setattr(app,'assert_profile_browser_stopped',failed_profile)
    with pytest.raises(app.CollectionRouteError):app._run_maintenance_probe(bundle,timeout=5)
    assert retired==[]
