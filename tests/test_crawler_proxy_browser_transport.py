"""Real pinned browser and explicit bound route over local forward + CONNECT."""
import pytest
from proxy_fixture import local_proxy, run_browser, run_runtime_browser


def test_browser_forward_connect_reopen_and_independent_identity(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        result=run_browser(fixture,tmp_path)
        assert result['seed_independent'] is True
        assert result['reopen_used']==6
        assert result['ignore_https_errors'] is False
        fixture.assert_routed()
        assert {'forward','CONNECT'} <= {e['kind'] for e in fixture.events}
        assert any(o['path']=='/redirect' for o in fixture.origins)
        assert any(o['path']=='/final' for o in fixture.origins)
        assert any(o['path']=='/image' for o in fixture.origins)
        echoes=[o for o in fixture.origins if o['path']=='/echo']
        assert len(echoes)==8
        assert all(not o['cookie'] and not o['authorization'] for o in echoes)


def test_browser_rejected_proxy_never_reaches_origin(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        fixture.reject=True
        result=run_browser(fixture,tmp_path,mode='probe')
        assert result['error']=='PROXY_AUTH_FAILED'
        assert any(e['kind']=='407' for e in fixture.events)
        assert not fixture.origins and not fixture.traps


def test_browser_disconnect_never_reaches_origin(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        fixture.disconnect=True
        result=run_browser(fixture,tmp_path,mode='probe')
        assert result['error']=='PROXY_UNAVAILABLE'
        assert not fixture.origins and not fixture.traps


def test_browser_http_exit_mismatch_stops_before_business(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        fixture.http_exit_ip='198.51.100.9'
        result=run_browser(fixture,tmp_path,mode='probe')
        assert result['error']=='PROXY_EXIT_CHANGED'
        assert all(o['path']=='/echo' for o in fixture.origins)
        fixture.assert_routed()


def test_browser_unicode_special_proxy_password_is_authenticated(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        fixture.password+='中文'
        result=run_browser(fixture,tmp_path,mode='probe')
        assert result['ip']==fixture.exit_ip
        fixture.assert_routed()
        assert all(o['path']=='/echo' for o in fixture.origins)


def test_storage_export_does_not_send_echo_origin_favicon(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        result=run_browser(fixture,tmp_path,mode='storage')
        assert result['ip']==fixture.exit_ip
        assert result['echo_origin_exported'] is False
        assert len(fixture.origins)==4
        assert all(o['path']=='/echo' for o in fixture.origins)
        fixture.assert_routed()


def test_periodic_probe_budget_exhaustion_blocks_new_business(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        result=run_browser(fixture,tmp_path,mode='budget',ttl=.3,max_calls=4)
        assert result['error']=='PROBE_BUDGET_EXHAUSTED'
        assert not any(o['path'] in ('/redirect','/final','/image') for o in fixture.origins)
        fixture.assert_routed()


def test_observed_exit_change_stops_new_business(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        result=run_browser(fixture,tmp_path,mode='change',ttl=.3)
        assert result['error']=='PROXY_EXIT_CHANGED'
        assert not any(o['path'] in ('/redirect','/final','/image') for o in fixture.origins)
        fixture.assert_routed()


def test_maintenance_probe_uses_route_without_reenabling_business(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        result=run_browser(fixture,tmp_path,mode='maintenance')
        assert result=={'maintenance_probe_ip':fixture.exit_ip,'stop_preserved':'PROXY_UNAVAILABLE','business_allowed':False}
        assert all(o['path']=='/echo' for o in fixture.origins)
        fixture.assert_routed()


def test_unknown_expiry_can_probe_but_cannot_start_business(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        result=run_browser(fixture,tmp_path,mode='unknown')
        assert result=={'unknown_expiry_probe_ip':fixture.exit_ip,'business_allowed':False}
        assert all(o['path']=='/echo' for o in fixture.origins)
        fixture.assert_routed()


@pytest.mark.parametrize('failure',['none','407','timeout'])
def test_maintenance_api_uses_real_crawler_child_and_releases_resources(tmp_path,monkeypatch,failure):
    import importlib.util
    import json
    import os
    from pathlib import Path
    import subprocess
    import time
    from backend.audit_agent.config import settings
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent import crawler_route
    from backend.task_admission.resources import proxy_edit_lease, acquire_account_handle

    assert importlib.util.find_spec('playwright') is None
    assert importlib.util.find_spec('cloakbrowser') is None
    crawler=Path(os.environ['MEDIACRAWLER_DIR'])
    with local_proxy(tmp_path/'network') as fixture:
        for name,value in dict(media_crawler_dir=crawler,crawler_login_python=Path(os.environ['LOGIN_BROWSER_TEST_PYTHON']),
                              crawler_proxy_enabled=True,app_auth_mode='disabled',request_scheduler_db=tmp_path/'scheduler.sqlite3',
                              crawler_browser_profile_root=tmp_path/'profiles',task_resource_lock_dir=tmp_path/'locks',
                              crawler_proxy_echo_url=fixture.url(host='echo'),crawler_proxy_probe_ttl=300,crawler_proxy_probe_budget=96).items():
            monkeypatch.setattr(settings,name,value)
        accounts=CrawlerAccountStore(tmp_path/'accounts.sqlite3')
        proxies=CrawlerProxyStore(accounts)
        account=accounts.create(platform='dy',display_name='fixture-maintenance')
        resource=proxies.create(display_name='fixture-maintenance',endpoint=fixture.server,
            credentials={'username':fixture.username,'password':fixture.password},expires_at=time.time()+120)
        bound=proxies.bind(account['id'],resource['id'],expected_binding_revision=account['binding_revision'])
        proxies.set_health(resource['id'],status='maintenance',stop_reason='FIXTURE_PAUSED')
        monkeypatch.setattr(crawler_route,'_store',proxies)
        # Only test trust is injected: the real production script parses its FD,
        # creates its real session/browser, emits results and closes normally.
        helper=Path(crawler_route.__file__).resolve().parents[2]/'scripts/crawler_proxy_probe.py'
        wrapper=tmp_path/'trusted_probe.py'
        wrapper.write_text('''import os,sys,runpy,functools
from pathlib import Path
sys.path.insert(0,os.environ['MEDIACRAWLER_DIR'])
from cloakbrowser.config import get_binary_path
assert Path(get_binary_path()).is_file()
os.environ['CLOAKBROWSER_BINARY_PATH']=str(get_binary_path())
os.environ['CLOAKBROWSER_AUTO_UPDATE']='false'
import tools.bound_proxy as transport
import tools.cloak_browser as browser
transport.BoundProxySession=functools.partial(transport.BoundProxySession,verify=os.environ['FIXTURE_CA'])
original=browser.launch_account_context
async def trusted(*args,**kwargs):
 kwargs['browser_args']=['--ignore-certificate-errors-spki-list='+os.environ['FIXTURE_SPKI'],'--host-resolver-rules=MAP *.proxy-test.invalid 127.0.0.1','--proxy-bypass-list=<-loopback>']
 return await original(*args,**kwargs)
browser.launch_account_context=trusted
runpy.run_path(os.environ['FIXTURE_PROBE_SCRIPT'],run_name='__main__')
''')
        original_popen=subprocess.Popen;children=[];commands=[]
        def child(command,**kwargs):
            assert command[0]==str(settings.crawler_login_python)
            assert command[1]==str(helper)
            assert fixture.password not in json.dumps(command)+json.dumps(kwargs['env'])
            commands.append(command)
            env={**kwargs['env'],'FIXTURE_CA':fixture.ca,'FIXTURE_SPKI':fixture.spki,'FIXTURE_PROBE_SCRIPT':str(helper)}
            process=original_popen([command[0],str(wrapper),*command[2:]],**{**kwargs,'env':env})
            children.append(process)
            return process
        monkeypatch.setattr(subprocess,'Popen',child)
        if failure=='407':fixture.reject=True
        if failure=='timeout':
            original_probe=crawler_route._run_maintenance_probe
            monkeypatch.setattr(crawler_route,'_run_maintenance_probe',lambda bundle:original_probe(bundle,timeout=.3))
        started=time.monotonic()
        if failure=='none':
            result=crawler_route.probe_account_route(account['id'],'fixture-owner',account_store=accounts,
                proxy_id=resource['id'],expected_binding_revision=bound['binding_revision'],expected_config_revision=1)
            assert result['observed_exit_ip']==fixture.exit_ip and result['business_enabled'] is False
            assert result['status']=='maintenance'
            assert proxies.get_public(resource['id'])['observed_exit_ip']==fixture.exit_ip
            assert len(fixture.origins)==4
            fixture.assert_routed()
        else:
            expected='PROXY_AUTH_FAILED' if failure=='407' else 'PROXY_UNAVAILABLE'
            with pytest.raises(crawler_route.CollectionRouteError,match=expected):
                crawler_route.probe_account_route(account['id'],'fixture-owner',account_store=accounts)
            assert not fixture.origins and not fixture.traps
            if failure=='timeout':assert time.monotonic()-started<3
        assert commands and all(p.poll() is not None for p in children)
        assert all(o['path']=='/echo' and not o['cookie'] and not o['authorization'] for o in fixture.origins)
        with proxy_edit_lease(resource['id']) as editable:assert editable
        account_handle=acquire_account_handle('dy',account['id']);assert account_handle is not None;account_handle.close()
        assert proxies.get_public(resource['id'])['status']=='maintenance'


def test_actual_old_v2_reopen_guard_periodic_and_child_receipt_pipe(tmp_path):
    with local_proxy(tmp_path/'network') as fixture:
        result=run_runtime_browser(fixture,tmp_path)
        assert result['old_v2_reopen'] and result['periodic_after_business_guard']
        assert result['business_echo_blocked'] and result['recovered_profile']
        assert result['headless_state_reused_profile']
        assert result['receipt_contexts']>=3
        fixture.assert_routed()
        assert {'forward','CONNECT'} <= {e['kind'] for e in fixture.events}
        assert all(not o['cookie'] and not o['authorization'] for o in fixture.origins if o['path']=='/echo')
        assert not any(o['host'].startswith('echo.') and o['path']!='/echo' for o in fixture.origins)


@pytest.mark.parametrize('download_mode',['normal','timeout','trial_timeout','trial_operations','echo_timeout'])
def test_live_child_receipt_borrowed_stream_downloads_share_original_route(tmp_path,monkeypatch,download_mode):
    import os
    import sys
    import threading
    import time
    from pathlib import Path
    from backend.audit_agent.config import settings
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent import crawler_route
    from backend.audit_agent.asset_utils import download_url_with_error
    from backend.task_admission.resources import ExecutionLeaseBundle

    crawler=Path(os.environ['MEDIACRAWLER_DIR'])
    sys.path.insert(0,str(crawler))
    import config
    import tools.bound_proxy as transport
    with local_proxy(tmp_path/'network') as fixture:
        for name,value in dict(media_crawler_dir=crawler,crawler_proxy_enabled=True,app_auth_mode='disabled',
                               request_scheduler_db=tmp_path/'scheduler.sqlite3',crawler_browser_profile_root=tmp_path/'profiles',
                               task_resource_lock_dir=tmp_path/'locks',crawler_proxy_echo_url=fixture.url(host='echo'),
                               crawler_proxy_probe_ttl=300,crawler_proxy_probe_budget=96).items():
            monkeypatch.setattr(settings,name,value)
        accounts=CrawlerAccountStore(tmp_path/'accounts.sqlite3')
        cipher=AuthStateCipher('fixture-only-key')
        proxies=CrawlerProxyStore(accounts,cipher=cipher)
        account=accounts.create(platform='dy',display_name='local-fixture')
        resource=proxies.create(display_name='local-fixture',endpoint=fixture.server,
                                credentials={'username':fixture.username,'password':fixture.password},expires_at=time.time()+120)
        bound=proxies.bind(account['id'],resource['id'],expected_binding_revision=account['binding_revision'])
        proxies.observe_exit(resource['id'],fixture.exit_ip)
        accounts.save_auth_state(account['id'],cipher.encrypt({'cookies':[],'origins':[]}))
        proxies.mark_route_login_validated(account['id'],expected_binding_revision=bound['binding_revision'],
                                           expected_config_revision=resource['config_revision'])
        monkeypatch.setattr(crawler_route,'_store',proxies)
        snapshot=crawler_route.resolve_account_route(account['id'])
        bundle=ExecutionLeaseBundle('fixture-execution','fixture-owner',account['id'],snapshot)
        assert bundle.acquire(max_active_accounts=1)
        bundle.child_active=True
        bundle.proxy_store=proxies
        cancel=threading.Event()
        original_session=transport.BoundProxySession
        def trusted_session(*args,**kwargs):
            kwargs['verify']=fixture.ca
            kwargs['policy']=dict(min_interval=0,per_minute=1000,max_concurrency=1,media_interval=0,cooldown_seconds=1)
            kwargs['stop_checker']=cancel.is_set
            return original_session(*args,**kwargs)
        monkeypatch.setattr(transport,'BoundProxySession',trusted_session)

        def stream_download(active_bundle,receipt):
            result=[];failure=[]
            provenance=crawler_route.route_contract().collection_route_provenance(snapshot,receipt,
                observation_id='fixture-observation',source_task_id='fixture-task',source_batch_id='fixture-batch',
                collected_at=time.time(),context_id=receipt.context_id)
            if download_mode in ('trial_timeout','trial_operations'):
                import sqlite3
                from tools.trial_limits import SharedTrialLimits
                SharedTrialLimits(snapshot.scheduler_db_path,'fixture-trial')
                monkeypatch.setattr(settings,'crawler_proxy_trial_id','fixture-trial')
                with sqlite3.connect(snapshot.scheduler_db_path) as db:
                    if download_mode=='trial_timeout':
                        db.execute('UPDATE proxy_trial_limits SET media_seconds=299.7 WHERE trial_id=?',('fixture-trial',))
                    else:
                        db.execute('UPDATE proxy_trial_limits SET operations=39 WHERE trial_id=?',('fixture-trial',))
            if download_mode=='echo_timeout':fixture.echo_delay=.6
            def stream():
                try:
                    with active_bundle.borrow(execution_id=active_bundle.execution_id,owner_id=active_bundle.owner_id,account_id=active_bundle.account_id) as borrowed:
                        if download_mode!='normal':
                            output=tmp_path/'drip.bin'
                            expected='PROBE_BUDGET_EXHAUSTED' if download_mode in ('trial_timeout','trial_operations') else 'PROXY_UNAVAILABLE'
                            prior=len(fixture.origins)
                            with pytest.raises(crawler_route.CollectionRouteError,match=expected):
                                download_url_with_error(fixture.url('/redirect' if download_mode=='trial_operations' else '/slow-drip'),output,timeout=5 if download_mode in ('trial_timeout','trial_operations') else .3,
                                    platform='dy',collection_route_provenance=provenance,owner_id=active_bundle.owner_id,
                                    execution_id=active_bundle.execution_id,route_context=borrowed,verify=fixture.ca)
                            assert not output.exists()
                            if download_mode in ('timeout','trial_timeout'):
                                assert fixture.drip_cancelled.wait(timeout=1),'client left slow origin connection alive'
                            if download_mode=='echo_timeout':
                                assert not any(o['path']=='/slow-drip' for o in fixture.origins[prior:])
                            if download_mode=='trial_operations':
                                assert sum(o['path']=='/redirect' for o in fixture.origins[prior:])==1
                                assert not any(o['path']=='/final' for o in fixture.origins[prior:])
                            import sqlite3
                            with sqlite3.connect(snapshot.scheduler_db_path) as db:
                                assert db.execute('SELECT COUNT(*) FROM request_scheduler_leases').fetchone()[0]==0
                            result.append(expected)
                            return
                        for tls in (False,True):
                            downloaded=download_url_with_error(fixture.url('/redirect',https=tls),tmp_path/('https.bin' if tls else 'http.bin'),
                                timeout=5,platform='dy',collection_route_provenance=provenance,owner_id=active_bundle.owner_id,
                                execution_id=active_bundle.execution_id,route_context=borrowed,verify=fixture.ca)
                            assert downloaded.path and downloaded.path.is_file()
                            result.append(str(downloaded.path.name))
                except Exception as exc:
                    failure.append(exc)
            thread=threading.Thread(target=stream)
            started=time.monotonic();thread.start();thread.join(timeout=2)
            if thread.is_alive():
                cancel.set();thread.join(timeout=1)
            assert not thread.is_alive(),'stream join deadlock or unbounded wait'
            if failure:raise failure[0]
            if download_mode=='echo_timeout':
                helper_session=active_bundle._borrowed_child_session.session
                assert helper_session is None or helper_session.receipt is None
            else:
                assert active_bundle._borrowed_child_session.session.context_id==receipt.context_id
            assert not hasattr(active_bundle,'_asset_browser'),'supplement opened a second profile despite live child'
            return {'files':result,'seconds':time.monotonic()-started,'same_context_id':receipt.context_id}
        try:
            result=run_runtime_browser(fixture,tmp_path,snapshot=snapshot,bundle=bundle,stream_download=stream_download,stop_after_stream=download_mode!='normal')
            assert result['stream']['files']==(['http.bin','https.bin'] if download_mode=='normal' else ['PROBE_BUDGET_EXHAUSTED' if download_mode in ('trial_timeout','trial_operations') else 'PROXY_UNAVAILABLE'])
            assert result['stream']['seconds']<2
            assert result['receipt_contexts']>=3
            fixture.assert_routed()
            assert {'forward','CONNECT'} <= {e['kind'] for e in fixture.events}
            assert not any(o['host'].startswith('echo.') and o['path']!='/echo' for o in fixture.origins)
        finally:
            bundle.close()
