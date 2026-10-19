"""Offline geographic enablement uses the existing, isolated release authority."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_control import manifest
from control import InvalidRelease, environment, validate
from backend.investigation_creation.geographic_runtime import (
    IsolatedGeographicCapability,
    install_runtime_geographic_capability,
)


@pytest.fixture
def release(tmp_path):
    root = tmp_path.resolve() / 'candidate'
    root.mkdir()
    config = manifest(root)
    config['ports'].update(frontend=13299, allowed=[18298, 13299])
    config['artifacts']['app']['path'] = str(Path(__file__).resolve().parents[3])
    path = root / 'manifest.json'
    config['geographic_runtime'] = True
    path.write_text(json.dumps(config))
    return path, config


def save(path, config):
    path.write_text(json.dumps(config))


def application(root):
    return SimpleNamespace(
        store=SimpleNamespace(db_path=root / 'db/creation.sqlite3'),
        resource_service=SimpleNamespace(resource_db_path=root / 'db/audit_index.sqlite3'),
    )


def test_explicit_staging_capability_installed_in_shared_composition(release):
    path, _ = release
    app = application(path.parent)
    conversation = SimpleNamespace(db_path=path.parent / 'db/conversation.sqlite3')
    capability = install_runtime_geographic_capability(
        app, conversation, environ={'CAMOUFOX_RELEASE_MANIFEST': str(path)},
    )
    assert app.geographic_capability is capability
    assert app.store.geographic_capability is capability
    assert app.resource_service.geographic_capability is capability
    assert capability.root == path.parent
    with pytest.raises(ValueError, match='authority fence'):
        capability.verify_confirmation(None, {})


@pytest.mark.parametrize('enabled', [None, False])
def test_release_default_is_disabled(release, enabled):
    path, config = release
    config.pop('geographic_runtime')
    if enabled is not None:
        config['geographic_runtime'] = enabled
    save(path, config)
    app = application(path.parent)
    assert install_runtime_geographic_capability(
        app, object(), environ={'CAMOUFOX_RELEASE_MANIFEST': str(path)},
    ) is None
    assert not hasattr(app, 'geographic_capability')


@pytest.mark.parametrize('value', ['true', 1, None, {}])
def test_release_enablement_is_strict_bool(release, value):
    path, config = release
    config['geographic_runtime'] = value
    save(path, config)
    with pytest.raises(ValueError, match='geographic_runtime_type'):
        IsolatedGeographicCapability.from_release_manifest(path)


def test_production_enablement_rejected(release):
    path, config = release
    config['mode'] = 'production'
    save(path, config)
    with pytest.raises(ValueError, match='requires_staging'):
        IsolatedGeographicCapability.from_release_manifest(path)


@pytest.mark.parametrize('key', ['scheduler_db', 'account_db', 'owner_db', 'archive_root'])
def test_configured_database_or_archive_cannot_escape_root(release, key):
    path, config = release
    config['runtime'][key] = str(path.parent.parent / Path(config['runtime'][key]).name)
    save(path, config)
    with pytest.raises(ValueError, match='source-bound'):
        IsolatedGeographicCapability.from_release_manifest(path)


@pytest.mark.parametrize('component', ['creation', 'conversation', 'resource'])
def test_actual_application_databases_cannot_escape_root(release, component):
    path, _ = release
    app = application(path.parent)
    conv = SimpleNamespace(db_path=path.parent / 'db/conversation.sqlite3')
    outside = path.parent.parent / 'shared.sqlite3'
    if component == 'creation':
        app.store.db_path = outside
    elif component == 'conversation':
        conv.db_path = outside
    else:
        app.resource_service.resource_db_path = outside
    with pytest.raises(ValueError, match='database outside'):
        install_runtime_geographic_capability(
            app, conv, environ={'CAMOUFOX_RELEASE_MANIFEST': str(path)},
        )
    assert not hasattr(app, 'geographic_capability')


def test_manifest_and_database_symlink_escape_rejected(release):
    path, config = release
    linked_manifest = path.parent / 'linked.json'
    linked_manifest.symlink_to(path)
    with pytest.raises(ValueError, match='symlinks'):
        IsolatedGeographicCapability.from_release_manifest(linked_manifest)
    (path.parent / 'db').symlink_to(path.parent.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='source-bound'):
        IsolatedGeographicCapability.from_release_manifest(path)


def test_release_must_bind_current_source(release):
    path, config = release
    config['artifacts']['app']['path'] = str(path.parent.parent / 'different-checkout')
    save(path, config)
    with pytest.raises(ValueError, match='source-bound'):
        IsolatedGeographicCapability.from_release_manifest(path)


def test_runtime_state_must_belong_to_manifest_directory(release):
    path, config = release
    runtime = config['runtime']
    state = path.parent.parent / 'shared-state'
    runtime['state_root'] = str(state)
    for key, suffix in [('profile_root', 'profiles'), ('cache_root', 'cache'), ('tmp_root', 'tmp'), ('home', 'home')]:
        runtime[key] = str(state / suffix)
    save(path, config)
    with pytest.raises(ValueError, match='source-bound'):
        IsolatedGeographicCapability.from_release_manifest(path)


def test_release_and_legacy_authorities_cannot_be_combined(release):
    path, _ = release
    with pytest.raises(ValueError, match='cannot be combined'):
        install_runtime_geographic_capability(object(), object(), environ={
            'CAMOUFOX_RELEASE_MANIFEST': str(path),
            'AUDIT_RUNTIME_MANIFEST': str(path.parent / 'runtime.json'),
        })


@pytest.mark.parametrize('name', ['AUDIT_RUNTIME_MANIFEST', 'CAMOUFOX_RELEASE_MANIFEST'])
def test_private_environment_cannot_inject_runtime_authority(release, name):
    import hashlib
    import os

    path, config = release
    overlay = path.parent / 'overlay.json'
    raw = json.dumps({name: '/bad'}).encode()
    overlay.write_bytes(raw)
    overlay.chmod(0o600)
    config['parent_env_files'] = [{
        'path': str(overlay), 'sha256': hashlib.sha256(raw).hexdigest(),
        'expected_uid': os.geteuid(), 'reviewed_allowlist': [name],
    }]
    with pytest.raises(InvalidRelease, match='controlled_override'):
        validate(config)
    with pytest.raises(InvalidRelease, match='parent_env_invalid_json'):
        environment(config)


@pytest.mark.parametrize('boundary', ['home', 'temporary'])
def test_shared_root_boundaries_rejected(release, monkeypatch, boundary):
    path, _ = release
    if boundary == 'home':
        monkeypatch.setattr(Path, 'home', lambda: path.parent)
    else:
        monkeypatch.setattr('tempfile.gettempdir', lambda: str(path.parent))
    with pytest.raises(ValueError, match='source-bound'):
        IsolatedGeographicCapability.from_release_manifest(path)


@pytest.mark.parametrize('entrypoint', ['serve', 'worker'])
def test_api_worker_and_turn_share_release_authority(release, entrypoint):
    import os
    import subprocess
    import sys

    path, _ = release
    deploy = Path(__file__).resolve().parents[1]
    code = r"""
import json, os, runpy, subprocess, sys, types
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[1] + '/runtime-support')
import control
probe = '''
import json, os
from pathlib import Path
from types import SimpleNamespace
from backend.investigation_creation.geographic_runtime import install_runtime_geographic_capability
root = Path(os.environ['CAMOUFOX_RELEASE_MANIFEST']).parent
app = SimpleNamespace(store=SimpleNamespace(db_path=root/'db/creation.sqlite3'),
    resource_service=SimpleNamespace(resource_db_path=root/'db/audit_index.sqlite3'))
conv = SimpleNamespace(db_path=root/'db/conversation.sqlite3')
cap = install_runtime_geographic_capability(app, conv)
print(json.dumps({'root':str(cap.root), 'manifest':os.environ['CAMOUFOX_RELEASE_MANIFEST'],
    'legacy':os.getenv('AUDIT_RUNTIME_MANIFEST')}))
'''
def inspect(*args, **kwargs):
    exec(probe)
    result = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, check=True)
    print(result.stdout.strip())
if sys.argv[2] == 'serve':
    module = types.ModuleType('uvicorn')
    module.run = inspect
    sys.modules['uvicorn'] = module
else:
    module = types.ModuleType('backend.investigation_creation.worker')
    module.build_worker = lambda: types.SimpleNamespace(run_forever=inspect)
    sys.modules['backend.investigation_creation.worker'] = module
with patch.object(control, 'check_artifacts'):
    runpy.run_path(sys.argv[1] + '/runtime-support/' + sys.argv[2] + '.py', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, '-c', code, str(deploy), entrypoint],
        env={'PATH': os.defpath, 'CAMOUFOX_RELEASE_MANIFEST': str(path),
             'AUDIT_RUNTIME_MANIFEST': '/hostile/inherited/runtime.json'},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    expected = {'root': str(path.parent), 'manifest': str(path), 'legacy': None}
    assert [json.loads(line) for line in result.stdout.splitlines()] == [expected, expected]
