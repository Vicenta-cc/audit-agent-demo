"""Offline persistent service contracts, with real source trees and artifact hashes."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import InvalidRelease, digest
from services import pinned_config, quoted, render, working_directory
from test_control import manifest


@pytest.fixture
def release(tmp_path):
    root = tmp_path.resolve()
    config = manifest(root)
    config['ports'].update(frontend=13299, allowed=[18298, 13299])
    app = root / 'app'
    support = app / 'deploy/camoufox/runtime-support'
    support.mkdir(parents=True)
    for name in ('serve.py', 'worker.py', 'frontend.py'):
        (support / name).write_text('# pinned entry\n')
    (support.parent / 'services.py').write_text('# pinned verifier\n')
    config['artifacts']['runtime_support']['path'] = str(support)
    for name, item in config['artifacts'].items():
        path = Path(item['path'])
        if name not in {'app', 'runtime_support'}:
            path.mkdir()
            (path / 'artifact').write_text(name)
    for name, source_key in (('app', 'app_commit'), ('crawler', 'crawler_commit')):
        path = Path(config['artifacts'][name]['path'])
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
        subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(path), '-c', 'user.name=fixture',
                        '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'fixture'], check=True)
        config['sources'][source_key] = subprocess.check_output(
            ['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    # Existing artifact hashing includes the .git directory; calculate after git init.
    for item in config['artifacts'].values():
        item['sha256'] = digest(item['path'])
    python = Path(config['runtime']['app_python'])
    python.parent.mkdir(parents=True)
    python.write_text('fixture python')
    python.chmod(0o755)
    path = root / 'manifest.json'
    path.write_text(json.dumps(config))
    return path, config


def test_render_contract_and_private_exclusive_output(release):
    path, config = release
    output = path.parent / 'review'
    receipt = render(path, output, 'content-audit-candidate')
    assert receipt['installed'] is False and receipt['linux_verified'] is False
    assert receipt['sources'] == config['sources']
    assert output.stat().st_mode & 0o777 == 0o700
    for role, memory in (('api', '2G'), ('worker', '4G'), ('frontend', '256M')):
        name = f'content-audit-candidate-{role}.service'
        text = (output / name).read_text()
        assert (output / name).stat().st_mode & 0o777 == 0o600
        assert 'User=root\nGroup=root\nUMask=0077' in text
        assert 'KillMode=control-group\nTimeoutStopSec=45' in text
        assert f'MemoryMax={memory}' in text
        assert 'CPUQuota=200%\nTasksMax=512' in text
        assert 'Restart=on-failure' in text
        assert 'RestartSec=310' in text if role == 'worker' else 'RestartSec=10' in text
        assert 'PYTHON_DOTENV_DISABLED=1' in text
        assert 'PYTHONDONTWRITEBYTECODE=1' in text
        assert f'--sha256 {digest(path)}' in text
        assert config['runtime']['app_python'] in text
        assert config['artifacts']['runtime_support']['path'] in text
        assert '[Install]\nWantedBy=multi-user.target' in text
        assert f'WorkingDirectory={config["artifacts"]["app"]["path"]}\n' in text
        assert 'EnvironmentFile=' not in text
        assert 'ExecStop=' not in text
    frontend = (output / 'content-audit-candidate-frontend.service').read_text()
    assert 'After=network-online.target content-audit-candidate-api.service content-audit-candidate-worker.service' in frontend
    with pytest.raises(FileExistsError):
        render(path, output, 'content-audit-candidate')


def test_production_uses_same_identity_and_protected_validation(release):
    path, config = release
    config['mode'] = 'production'
    config['ports'].update(backend=8298, frontend=3298, allowed=[8298, 3298], protected=[80, 443])
    path.write_text(json.dumps(config))
    receipt = render(path, path.parent / 'production-review', 'content-audit-production')
    assert receipt['mode'] == 'production'


def test_manifest_change_prevents_start(release):
    path, config = release
    expected = digest(path)
    config['maintenance'] = False
    path.write_text(json.dumps(config))
    with pytest.raises(InvalidRelease, match='service_manifest_changed'):
        pinned_config(path, expected)


def test_source_or_support_mismatch_rejected(release):
    path, config = release
    config['sources']['app_commit'] = 'f' * 40
    path.write_text(json.dumps(config))
    with pytest.raises(InvalidRelease, match='source_head_mismatch'):
        render(path, path.parent / 'bad', 'audit')
    config['artifacts']['runtime_support']['path'] = str(path.parent / 'other-support')
    path.write_text(json.dumps(config))
    with pytest.raises(InvalidRelease, match='service_runtime_support_source_mismatch'):
        render(path, path.parent / 'bad', 'audit')
    assert not (path.parent / 'bad').exists()


@pytest.mark.parametrize('prefix', ['../audit', 'audit.service', 'audit\nExecStart=x', '-audit', 'a' * 64])
def test_invalid_unit_names(release, prefix):
    with pytest.raises(InvalidRelease, match='service_prefix_invalid'):
        render(release[0], release[0].parent / 'bad', prefix)


def test_systemd_token_escaping():
    assert quoted('/x %n $HOME "quote"') == '"/x %%n $HOME \\"quote\\""'
    assert quoted('/x %n $HOME', command=True) == '"/x %%n $$HOME"'
    with pytest.raises(InvalidRelease, match='service_control_character'):
        quoted('/path\nExecStart=bad')


def test_symlink_and_traversal_rejected(release):
    path, _ = release
    link = path.parent / 'link.json'
    link.symlink_to(path)
    with pytest.raises(InvalidRelease, match='service_path_symlink'):
        pinned_config(link)
    with pytest.raises(InvalidRelease, match='service_absolute_clean_path_required'):
        pinned_config(path.parent / '..' / 'manifest.json')


def test_dirty_asset_does_not_create_review_output(release):
    path, config = release
    (Path(config['artifacts']['app']['path']) / 'deploy/camoufox/services.py').write_text('modified')
    with pytest.raises(InvalidRelease, match='artifact_hash_mismatch_app'):
        render(path, path.parent / 'bad', 'audit')
    assert not (path.parent / 'bad').exists()


def test_no_render_into_live_units_or_pinned_source(release):
    path, config = release
    for output in (Path(config['artifacts']['app']['path']) / 'review',
                   Path('/etc/systemd/system/review')):
        # macOS /etc is itself a symlink; either boundary must reject it.
        with pytest.raises(InvalidRelease, match='service_output_protected_path|service_path_symlink|service_output_parent_missing'):
            render(path, output, 'audit')


def test_conventional_venv_interpreter_symlink_keeps_venv_identity(release):
    path, config = release
    python = Path(config['runtime']['app_python'])
    target = python.with_name('python-real')
    python.rename(target)
    python.symlink_to(target.name)
    output = path.parent / 'symlink-python-review'
    render(path, output, 'audit')
    assert str(python) in (output / 'audit-api.service').read_text()


@pytest.mark.parametrize('value', ['/opt/space path', '/opt/%n', '/opt/"quoted"', '/opt/path\\name'])
def test_working_directory_rejects_ambiguous_path_syntax(value):
    with pytest.raises(InvalidRelease, match='service_working_directory_requires_plain_path'):
        working_directory(value)
