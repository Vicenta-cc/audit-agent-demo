from __future__ import annotations

import socket
import time
from unittest.mock import Mock

import pytest

from backend.audit_agent import creator_url as urls

SHORT = 'https://v.douyin.com/fixtureOnly/'
SEC = 'MS4wLjABAAAA-fixture-user'
PROFILE = f'https://www.douyin.com/user/{SEC}'
SHARE_TEXT = f'4- 长按复制此条消息，打开抖音搜索，查看TA的更多作品。 {SHORT} 1@4.com :3pm'


@pytest.fixture
def redirect(monkeypatch):
    mock = Mock(return_value=f'https://www.iesdouyin.com/share/user/{SEC}/?from=share')
    monkeypatch.setattr(urls, '_read_share_redirect', mock)
    return mock


@pytest.mark.parametrize('value', [PROFILE, PROFILE + '?from_tab_name=main'])
def test_complete_profile_is_preserved_without_network(value, redirect):
    assert urls.resolve_creator_url('dy', value) == value
    redirect.assert_not_called()


@pytest.mark.parametrize('value', [SHORT, SHARE_TEXT, f'看看TA：（{SHORT}）。', SHORT + '\n' + SHORT])
def test_short_link_and_copied_prose_resolve_to_profile(value, redirect):
    assert urls.resolve_creator_url('dy', value) == PROFILE
    redirect.assert_called_once()
    assert redirect.call_args.args == (SHORT,)


def test_full_profile_in_share_prose_needs_no_network(redirect):
    assert urls.resolve_creator_url('dy', f'调查这个账号 {PROFILE} 谢谢') == PROFILE
    redirect.assert_not_called()


@pytest.mark.parametrize('value', [f'{PROFILE} 复制此链接打开抖音', f'{PROFILE}?from=share 1@4.com :3pm'])
def test_share_text_starting_with_full_url_is_not_saved_as_a_url(value, redirect):
    assert urls.resolve_creator_url('dy', value) == PROFILE
    redirect.assert_not_called()


@pytest.mark.parametrize('location', [
    f'https://www.iesdouyin.com/share/user/{SEC}/',
    f'https://www.douyin.com/user/{SEC}?from=share',
    f'https://www.iesdouyin.com/share/user/123456789?sec_uid={SEC}',
    f'http://www.iesdouyin.com/share/user/{SEC}/',
])
def test_profile_redirect_variants(location, redirect):
    redirect.return_value = location
    assert urls.resolve_creator_url('dy', SHORT) == PROFILE


@pytest.mark.parametrize('location', [
    'https://www.douyin.com/video/123?sec_uid=' + SEC,
    'https://www.iesdouyin.com/share/video/123?sec_uid=' + SEC,
    'https://www.iesdouyin.com/share/user/123456789',
    'https://www.iesdouyin.com/share/user/123?sec_uid=' + SEC + '&sec_uid=other',
    'https://www.iesdouyin.com/share/user/%2fother',
    'https://www.douyin.com/user/' + SEC + '?modal_id=123',
    'https://www.douyin.com/login?sec_uid=' + SEC,
    'https://example.com/user/' + SEC,
    'https://v.douyin.com.evil.example/fixture/',
    'https://v.douyin.com@127.0.0.1/fixture/',
    'https://v.douyin.com:8443/fixture/',
    'https://v.douyin.com/fixture/\r\nX-Test: value',
    'https://v.douyin.com\\@127.0.0.1/fixture/',
    'file:///etc/passwd',
    'https://[',
])
def test_invalid_destination_is_rejected_without_fetching_it(location, redirect):
    redirect.return_value = location
    with pytest.raises(urls.CreatorUrlValidationError):
        urls.resolve_creator_url('dy', SHORT)
    redirect.assert_called_once()


@pytest.mark.parametrize('value', [
    'https://example.com/fixture/',
    'https://v.douyin.com.evil.example/fixture/',
    'https://user:password@v.douyin.com/fixture/',
    'https://v.douyin.com:abc/fixture/',
    'http://127.0.0.1/fixture/',
    f'{SHORT} https://v.douyin.com/different/',
    '', 'not a URL', 'x' * 4001,
])
def test_invalid_or_ambiguous_input_never_makes_a_request(value, redirect):
    with pytest.raises(urls.CreatorUrlValidationError):
        urls.resolve_creator_url('dy', value)
    redirect.assert_not_called()


def test_relative_redirect_and_deadline_are_shared(redirect):
    redirect.side_effect = ['/second/', f'https://www.iesdouyin.com/share/user/{SEC}/']
    assert urls.resolve_creator_url('dy', SHORT) == PROFILE
    assert redirect.call_args_list[1].args == ('https://v.douyin.com/second/',)
    assert redirect.call_args_list[0].kwargs['deadline'] == redirect.call_args_list[1].kwargs['deadline']


def test_redirect_cycle_is_bounded(redirect):
    redirect.return_value = SHORT
    with pytest.raises(urls.CreatorUrlValidationError):
        urls.resolve_creator_url('dy', SHORT)
    redirect.assert_called_once()


def test_redirect_count_is_bounded(redirect):
    redirect.side_effect = [f'/hop-{n}/' for n in range(10)]
    with pytest.raises(urls.CreatorUrlValidationError):
        urls.resolve_creator_url('dy', SHORT)
    assert redirect.call_count == 5


@pytest.mark.parametrize('failure', [TimeoutError(), OSError(), ValueError(), urls.http.client.HTTPException()])
def test_network_failure_is_a_validation_error(failure, redirect):
    redirect.side_effect = failure
    with pytest.raises(urls.CreatorUrlValidationError, match='暂时无法解析'):
        urls.resolve_creator_url('dy', SHORT)


def test_no_location_is_a_recoverable_error(redirect):
    redirect.return_value = ''
    with pytest.raises(urls.CreatorUrlValidationError, match='暂时无法解析'):
        urls.resolve_creator_url('dy', SHORT)


@pytest.mark.parametrize(('platform', 'value'), [
    ('dy', SEC), ('ks', 'legacy-ks-id'), ('xhs', '5f58bd990000000001003753'),
])
def test_legacy_ids_still_work(platform, value, redirect):
    assert urls.resolve_creator_url(platform, value, allow_legacy_id=True) == value
    redirect.assert_not_called()


@pytest.mark.parametrize(('platform', 'value'), [
    ('ks', 'https://www.kuaishou.com/profile/user-id'),
    ('xhs', 'https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753?xsec_token=t&xsec_source=pc_share'),
])
def test_other_platforms_keep_their_contract(platform, value, redirect):
    assert urls.resolve_creator_url(platform, value) == value
    redirect.assert_not_called()


def test_frozen_validation_remains_pure_and_rejects_short_url(redirect):
    with pytest.raises(urls.CreatorUrlValidationError):
        urls.validate_creator_url('dy', SHORT)
    redirect.assert_not_called()


def address(ip):
    return (socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))


@pytest.mark.parametrize('ips', [
    ['127.0.0.1'], ['10.0.0.1'], ['169.254.169.254'], ['198.18.0.1'], ['::1'],
    ['::ffff:127.0.0.1'], ['93.184.216.34', '192.168.1.1'], [],
])
def test_private_or_mixed_dns_answers_never_connect(monkeypatch, ips):
    monkeypatch.setattr(urls.socket, 'getaddrinfo', lambda *a, **kw: [address(ip) for ip in ips])
    connection = Mock()
    monkeypatch.setattr(urls, '_ShareHTTPSConnection', connection)
    with pytest.raises(ValueError):
        urls._read_share_redirect(SHORT, deadline=time.monotonic() + 10)
    connection.assert_not_called()


def test_http_transport_uses_pinned_address_headers_only_and_closes(monkeypatch):
    monkeypatch.setattr(urls.socket, 'getaddrinfo', lambda *a, **kw: [address('93.184.216.34')])
    response = Mock(status=302)
    response.getheader.return_value = PROFILE
    response.read.side_effect = AssertionError('must never read response body')
    connection = Mock()
    connection.getresponse.return_value = response
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(urls, '_ShareHTTPSConnection', constructor)
    assert urls._read_share_redirect(SHORT, deadline=time.monotonic() + 10) == PROFILE
    assert constructor.call_args.args == ('v.douyin.com', '93.184.216.34')
    assert 0 < constructor.call_args.kwargs['timeout'] <= 3
    assert connection.request.call_args.args == ('GET', '/fixtureOnly/')
    assert 'Cookie' not in connection.request.call_args.kwargs['headers']
    response.read.assert_not_called()
    response.close.assert_called_once()
    connection.close.assert_called_once()


def test_tls_verifies_host_while_connecting_to_pinned_address(monkeypatch):
    sock = Mock()
    tls_context = Mock()
    monkeypatch.setattr(urls.ssl, 'create_default_context', lambda: tls_context)
    connect = Mock(return_value=sock)
    monkeypatch.setattr(urls.socket, 'create_connection', connect)
    connection = urls._ShareHTTPSConnection('v.douyin.com', '93.184.216.34', timeout=2)
    connection.connect()
    connect.assert_called_once_with(('93.184.216.34', 443), timeout=2)
    tls_context.wrap_socket.assert_called_once_with(sock, server_hostname='v.douyin.com')


def test_expired_budget_never_resolves_dns(monkeypatch):
    lookup = Mock()
    monkeypatch.setattr(urls.socket, 'getaddrinfo', lookup)
    with pytest.raises(TimeoutError):
        urls._read_share_redirect(SHORT, deadline=time.monotonic() - 1)
    lookup.assert_not_called()


@pytest.fixture(autouse=True)
def no_ambient_crawler_proxy(monkeypatch):
    monkeypatch.delenv("CRAWLER_PROXY_URL", raising=False)


def test_explicit_loopback_proxy_uses_connect_without_local_destination_dns(monkeypatch):
    monkeypatch.setenv("CRAWLER_PROXY_URL", "http://127.0.0.1:17890")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.example:1234")
    lookup = Mock(side_effect=AssertionError("must not resolve destination through TUN DNS"))
    monkeypatch.setattr(urls.socket, "getaddrinfo", lookup)
    tls_context = Mock()
    monkeypatch.setattr(urls.ssl, "create_default_context", lambda: tls_context)
    response = Mock(status=302)
    response.getheader.return_value = PROFILE
    connection = Mock()
    connection.getresponse.return_value = response
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(urls.http.client, "HTTPSConnection", constructor)
    assert urls._read_share_redirect(SHORT, deadline=time.monotonic() + 10) == PROFILE
    assert constructor.call_args.args == ("127.0.0.1", 17890)
    assert constructor.call_args.kwargs["context"] is tls_context
    connection.set_tunnel.assert_called_once_with("v.douyin.com", 443)
    lookup.assert_not_called()
    response.read.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize("proxy", [
    "http://proxy.example:17890", "http://192.168.1.1:17890",
    "https://127.0.0.1:17890", "http://127.0.0.1", "http://127.0.0.1:bad",
    "http://user:secret@127.0.0.1:17890", "http://127.0.0.1:17890/path",
    "http://127.0.0.1:17890?token=secret", "http://127.0.0.1:17890#fragment",
])
def test_untrusted_proxy_configuration_is_rejected_without_connection(monkeypatch, proxy):
    monkeypatch.setenv("CRAWLER_PROXY_URL", proxy)
    constructor = Mock()
    monkeypatch.setattr(urls.http.client, "HTTPSConnection", constructor)
    with pytest.raises(ValueError):
        urls._read_share_redirect(SHORT, deadline=time.monotonic() + 10)
    constructor.assert_not_called()


def test_ambient_proxy_is_ignored_and_direct_dns_is_still_validated(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:17890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:17890")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:17890")
    monkeypatch.setattr(urls.socket, "getaddrinfo", lambda *a, **kw: [address("198.18.0.1")])
    with pytest.raises(ValueError, match="public addresses"):
        urls._read_share_redirect(SHORT, deadline=time.monotonic() + 10)


def test_proxy_does_not_allow_non_douyin_transport_destinations(monkeypatch):
    monkeypatch.setenv("CRAWLER_PROXY_URL", "http://127.0.0.1:17890")
    constructor = Mock()
    monkeypatch.setattr(urls.http.client, "HTTPSConnection", constructor)
    with pytest.raises(urls.CreatorUrlValidationError):
        urls._read_share_redirect("https://127.0.0.1/private", deadline=time.monotonic() + 10)
    constructor.assert_not_called()


def test_tls_context_adds_existing_ca_bundle_without_disabling_verification(monkeypatch):
    context = urls.ssl.create_default_context()
    original_mode, original_hostname = context.verify_mode, context.check_hostname
    context_mock = Mock(wraps=context)
    monkeypatch.setattr(urls.ssl, "create_default_context", lambda: context_mock)
    assert urls._share_tls_context() is context_mock
    context_mock.load_verify_locations.assert_called_once_with(cafile=urls.certifi.where())
    assert context.verify_mode == original_mode == urls.ssl.CERT_REQUIRED
    assert context.check_hostname is original_hostname is True
