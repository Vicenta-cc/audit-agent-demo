from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture(autouse=True)
def helper_browser_types(monkeypatch):
    import importlib.util
    import sys
    from types import ModuleType
    if importlib.util.find_spec("playwright") is None:
        package = ModuleType("playwright")
        api = ModuleType("playwright.async_api")
        api.Locator = object
        api.TimeoutError = TimeoutError
        api.async_playwright = Mock()
        monkeypatch.setitem(sys.modules, "playwright", package)
        monkeypatch.setitem(sys.modules, "playwright.async_api", api)


@pytest.mark.asyncio
async def test_v1_login_uses_physical_profile_root_and_headed_context(monkeypatch, tmp_path):
    from scripts import crawler_account_login as login
    from scripts import crawler_login_pages
    from backend.audit_agent import crawler_route
    monkeypatch.setattr(login.settings, 'crawler_camoufox_state_root', tmp_path)
    monkeypatch.setattr(login.settings, 'crawler_camoufox_browser_version', '156.0.1-beta.39')
    # login_context publishes the selected version into the child contract.
    # Register that write with monkeypatch so later real-browser tests retain
    # their explicitly installed kernel instead of this mocked candidate.
    monkeypatch.setenv('MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION', '156.0.1-beta.39')
    context = SimpleNamespace(close=AsyncMock())
    adapter = SimpleNamespace(profile_for=Mock(), launch_account_context=AsyncMock(return_value=(context, {})))
    monkeypatch.setattr(login, 'load_account_browser', lambda: adapter)
    monkeypatch.setattr(login, 'request_login_reset', AsyncMock())
    monkeypatch.setattr(crawler_route, 'child_route_snapshot', lambda _: None)
    reset = AsyncMock()
    monkeypatch.setattr(crawler_login_pages, 'prepare_headed_login', reset)
    async with login.login_context('dy', 'account-a', headless=True) as result:
        assert result is context
    adapter.profile_for.assert_called_once_with('account-a', tmp_path / 'profiles', '156.0.1-beta.39')
    assert adapter.launch_account_context.call_args.kwargs['headless'] is True
    assert adapter.launch_account_context.call_args.args[1] == tmp_path / 'profiles'
    reset.assert_awaited_once()
    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_linux_allocates_display_only_when_missing(monkeypatch):
    from scripts import crawler_proxy_probe as probe
    from scripts import crawler_interactive_login
    entered = []
    @contextmanager
    def display():
        entered.append(True)
        yield
    monkeypatch.setattr(probe.sys, 'platform', 'linux')
    monkeypatch.setattr(crawler_interactive_login, 'virtual_display', display)
    monkeypatch.setattr(probe, '_probe', AsyncMock(return_value='done'))
    monkeypatch.delenv('DISPLAY', raising=False)
    assert await probe.probe('a') == 'done'
    assert entered == [True]
    monkeypatch.setenv('DISPLAY', ':123')
    await probe.probe('a')
    assert entered == [True]


def test_probe_source_has_unique_camoufox_contract():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'scripts/crawler_proxy_probe.py').read_text()
    assert 'tools.camoufox_browser' in source
    assert 'MEDIACRAWLER_CAMOUFOX_PROFILE_ROOT' in source
    assert 'headless=sys.platform == "darwin"' in source
    assert 'cloak' not in source.lower()
    assert 'new_cdp_session' not in source
