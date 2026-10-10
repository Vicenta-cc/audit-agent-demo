"""Mandatory prerequisites fail explicitly; never silently skip real browser."""
import ssl

import httpx
import pytest

from proxy_fixture import local_proxy, run_browser


def test_real_browser_binary_ca_proxy_and_origins_precheck(tmp_path):
    with local_proxy(tmp_path / 'network') as fixture:
        ssl.create_default_context(cafile=fixture.ca)
        result = run_browser(fixture,tmp_path,mode='probe')
        assert result == {'ip':'198.51.100.7','pages':0,'used':4}
        fixture.assert_routed()
        assert any(e['kind']=='CONNECT' for e in fixture.events)
        assert all(o['path']=='/echo' for o in fixture.origins)
        browser_requests=[o for o in fixture.origins if not o['user_agent'].startswith('python-')]
        assert len(browser_requests)==2
        assert all(o['origin_header']=='null' for o in browser_requests)


def test_test_ca_is_required_by_http_client(tmp_path):
    with local_proxy(tmp_path) as fixture:
        with httpx.Client(proxy=fixture.authenticated_url,trust_env=False,verify=True,timeout=5) as client:
            with pytest.raises(httpx.ConnectError):
                client.get(fixture.url())
        assert not fixture.origins


def test_direct_connection_trap_detects_bypass(tmp_path):
    with local_proxy(tmp_path) as fixture:
        with httpx.Client(trust_env=False,timeout=5) as client:
            response=client.get('http://127.0.0.1:'+str(fixture.http.server_port)+'/direct-canary')
        assert response.status_code==403
        assert fixture.traps==[{'path':'/direct-canary'}]
        assert not fixture.origins and not fixture.events


def test_browser_trust_is_limited_to_explicit_test_leaf_spki(tmp_path):
    import base64
    with local_proxy(tmp_path) as fixture:
        result=run_browser(fixture,tmp_path,mode='probe',spki=base64.b64encode(bytes(32)).decode())
        assert result['error']=='PROXY_UNAVAILABLE'
        # The failed pair may use its reserved HTTP attempt to diagnose auth;
        # that trusted client is allowed one echo, the browser must reach none.
        assert len(fixture.origins)==1
        assert fixture.origins[0]['user_agent'].startswith('python-httpx/')
        assert fixture.origins[0]['path']=='/echo'
        assert not fixture.traps


def test_echo_without_cors_fails_before_business(tmp_path):
    with local_proxy(tmp_path) as fixture:
        fixture.echo_cors=False
        result=run_browser(fixture,tmp_path,mode='probe')
        assert result['error']=='PROXY_UNAVAILABLE'
        assert all(o['path']=='/echo' for o in fixture.origins)
        assert not fixture.traps
