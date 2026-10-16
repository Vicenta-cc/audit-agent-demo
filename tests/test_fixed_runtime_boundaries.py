import json
from pathlib import Path

import pytest

from backend.audit_agent.crawler_browser import crawler_network_env
from backend.audit_agent.crawler_adapter import MediaCrawlerAdapter
from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability


def test_explicit_crawler_route_replaces_all_model_proxy_aliases(monkeypatch):
    for key in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        monkeypatch.setenv(key, 'http://127.0.0.1:19999')
    monkeypatch.setenv('CRAWLER_PROXY_URL', 'http://127.0.0.1:17890')
    env = MediaCrawlerAdapter(Path('/fixed/crawler'))._subprocess_env(platform='xhs')
    for key in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        assert env[key] == 'http://127.0.0.1:17890'
    assert env['NO_PROXY'] == '127.0.0.1,localhost,::1'


def test_direct_crawler_explicitly_removes_proxy_and_preserves_unmanaged_behavior():
    source = {'HTTP_PROXY': 'model-only', 'https_proxy': 'model-only', 'CRAWLER_PROXY_URL': ''}
    assert crawler_network_env(source) == {
        'CRAWLER_PROXY_URL': '', 'NO_PROXY': '127.0.0.1,localhost,::1',
        'no_proxy': '127.0.0.1,localhost,::1',
    }
    assert crawler_network_env({'HTTP_PROXY': 'legacy'}) == {'HTTP_PROXY': 'legacy'}
    assert source['HTTP_PROXY'] == 'model-only'


def test_manifest_owns_persistent_geographic_capability(tmp_path, monkeypatch):
    # A mocked temp base proves this works independently of pytest's temp root.
    monkeypatch.setattr('tempfile.gettempdir', lambda: '/unrelated-temporary-root')
    root = tmp_path / 'daily-runtime'
    root.mkdir()
    manifest = root / 'runtime.json'
    value = {'schema_version': 1, 'runtime_root': str(root),
             'repository': str(tmp_path / 'checkout'), 'geographic_runtime': True}
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='temporary isolation root'):
        IsolatedGeographicCapability(root)
    capability = IsolatedGeographicCapability(root, runtime_manifest=manifest)
    assert capability.allows_path(root / 'data/creation.sqlite3')
    assert not capability.allows_path(tmp_path / 'foreign.sqlite3')
    outside = root / 'linked'
    outside.symlink_to(tmp_path, target_is_directory=True)
    assert not capability.allows_path(outside / 'foreign.sqlite3')
    for invalid in ({**value, 'geographic_runtime': False},
                    {**value, 'runtime_root': str(tmp_path / 'foreign')},
                    {**value, 'repository': str(root / 'checkout')}):
        manifest.write_text(json.dumps(invalid))
        with pytest.raises(ValueError, match='manifest-owned'):
            IsolatedGeographicCapability(root, runtime_manifest=manifest)

