"""The owner retains App secrets; browser children get bounded capabilities."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.audit_agent import crawler_child_runtime as runtime


@pytest.fixture
def state_root(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.settings, "crawler_camoufox_state_root", tmp_path / "state")
    monkeypatch.setattr(runtime.settings, "crawler_browser_profile_root", tmp_path / "logical-profiles")
    return tmp_path / "state"


def test_child_environment_does_not_inherit_app_credentials_or_proxy(state_root, monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    forbidden = {
        "APP_AUTH_SECRET": "app-secret", "CRAWLER_AUTH_STATE_SECRET": "auth-secret",
        "OPENAI_API_KEY": "cloud-secret", "HTTP_PROXY": "http://127.0.0.1:7890",
        "HTTPS_PROXY": "http://127.0.0.1:7890", "ALL_PROXY": "socks5://127.0.0.1:7890",
        "MEDIACRAWLER_ACCOUNT_AUTH_STATE": "old-cookie", "MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64": "old-cookie-b64",
        "MEDIACRAWLER_CLOAK_PROFILE_ROOT": "/legacy", "PYTHONPATH": "/untrusted/site",
    }
    for name, value in forbidden.items():
        monkeypatch.setenv(name, value)
    env = runtime.child_environment({
        "MEDIACRAWLER_ACCOUNT_ID": "account-a", "MEDIACRAWLER_REQUEST_AUTHORITY_FD": "19",
        "MEDIACRAWLER_ACCOUNT_AUTH_STATE": "explicit-old-cookie",
        "MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64": "explicit-old-cookie-b64",
        "APP_AUTH_SECRET": "explicit-app-secret",
    })
    assert not set(forbidden).difference({"PYTHONPATH"}) & env.keys()
    assert "/untrusted/site" not in env["PYTHONPATH"]
    assert env["MEDIACRAWLER_ACCOUNT_ID"] == "account-a"
    assert env["MEDIACRAWLER_REQUEST_AUTHORITY_FD"] == "19"
    assert env["HOME"] == str(state_root / "home")
    assert env["CRAWLER_RESTRICTED_CHILD"] == "1"
    assert all((state_root / name).is_dir() for name in ("home", "cache", "tmp", "outputs"))


@pytest.mark.parametrize("uid,gid", [("", ""), ("0", "1001"), ("1001", "0"), ("bad", "1001"), ("1001", "")])
def test_linux_root_refuses_missing_or_root_child_identity(monkeypatch, uid, gid):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime.settings, "crawler_child_uid", uid)
    monkeypatch.setattr(runtime.settings, "crawler_child_gid", gid)
    with pytest.raises(RuntimeError, match="CRAWLER_CHILD_IDENTITY_REQUIRED"):
        runtime.child_process_options()


def test_linux_launch_drops_supplementary_groups_and_uses_private_umask(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime.settings, "crawler_child_uid", "1001")
    monkeypatch.setattr(runtime.settings, "crawler_child_gid", "1002")
    assert runtime.child_process_options() == {"user": 1001, "group": 1002, "extra_groups": [], "umask": 0o077}


def test_linux_nonroot_without_configured_uid_keeps_current_identity(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(runtime.settings, "crawler_child_uid", "")
    monkeypatch.setattr(runtime.settings, "crawler_child_gid", "")
    assert runtime.child_process_options() == {"umask": 0o077}


def test_linux_root_never_silently_provisions_root_owned_browser_state(state_root, monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime.settings, "crawler_child_uid", "1001")
    monkeypatch.setattr(runtime.settings, "crawler_child_gid", "1001")
    with pytest.raises(RuntimeError, match="CRAWLER_CHILD_STATE_NOT_PROVISIONED"):
        runtime.child_environment()
    assert not state_root.exists()


@pytest.mark.parametrize("bundle", [None, SimpleNamespace(snapshot=None)])
def test_authority_requires_bound_route(bundle):
    with pytest.raises(RuntimeError, match="CRAWLER_AUTHORITY_ROUTE_REQUIRED"):
        runtime.authority_for(bundle)


@pytest.mark.parametrize("account", [None, {"profile_engine": "legacy", "profile_generation": "old"}, {"profile_engine": "camoufox", "profile_generation": ""}])
def test_authority_closes_capability_when_account_generation_is_invalid(monkeypatch, account):
    from backend.audit_agent import crawler_route, crawler_request_authority
    handle = SimpleNamespace(env={}, close=Mock())
    monkeypatch.setattr(crawler_request_authority, "open_authority", lambda *a, **k: handle)
    monkeypatch.setattr(crawler_route, "probe_env", lambda bundle: {})
    monkeypatch.setattr(crawler_route, "parent_account_policy", lambda bundle: {})
    bundle = SimpleNamespace(snapshot=SimpleNamespace(managed=False), account_id="a", account_store=SimpleNamespace(get=lambda _: account))
    with pytest.raises(RuntimeError, match="CRAWLER_PROFILE_MIGRATION_REQUIRED"):
        runtime.authority_for(bundle)
    handle.close.assert_called_once()


def test_authority_carries_current_profile_generation_only(monkeypatch):
    from backend.audit_agent import crawler_route, crawler_request_authority
    handle = SimpleNamespace(env={}, close=Mock())
    opened = Mock(return_value=handle)
    monkeypatch.setattr(crawler_request_authority, "open_authority", opened)
    monkeypatch.setattr(crawler_route, "probe_env", lambda bundle: {"MEDIACRAWLER_PROXY_PROBE_BUDGET_ID": "bounded-budget"})
    monkeypatch.setattr(crawler_route, "parent_account_policy", lambda bundle: {"max_concurrency": 1})
    bundle = SimpleNamespace(snapshot=SimpleNamespace(managed=False), account_id="a", account_store=SimpleNamespace(get=lambda _: {"profile_engine": "camoufox", "profile_generation": "generation-a"}))
    assert runtime.authority_for(bundle, login_bootstrap=True) is handle
    assert handle.env == {"MEDIACRAWLER_CAMOUFOX_PROFILE_GENERATION": "generation-a"}
    assert opened.call_args.kwargs["session_options"]["login_bootstrap"] is True
    assert opened.call_args.kwargs["session_options"]["account_policy"] == {"max_concurrency": 1}
    handle.close.assert_not_called()


def test_maintenance_requires_matching_migration_authority_not_arbitrary_operation(monkeypatch):
    from backend.audit_agent import crawler_browser_migration
    service = SimpleNamespace(guard=Mock(), authorized_login=Mock(return_value=False))
    monkeypatch.setattr(crawler_browser_migration, "get_browser_migration_service", lambda store: service)
    monkeypatch.setattr(runtime.settings, "crawler_browser_maintenance", True)
    with pytest.raises(RuntimeError, match="CRAWLER_BROWSER_MAINTENANCE"):
        runtime.require_browser_available("account-a", store=object(), owner_id="owner", migration_id="wrong", operation_id="fake-op")
    service.authorized_login.assert_called_once_with("account-a", owner_id="owner", migration_id="wrong", operation_id="fake-op")
    service.authorized_login.return_value = True
    assert runtime.require_browser_available("account-a", store=object(), owner_id="owner", migration_id="approved", operation_id="approved-op") is service
