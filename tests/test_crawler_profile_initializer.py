import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from backend.audit_agent.config import settings
from backend.audit_agent.crawler_browser_migration import MigrationError, profile_key
from backend.audit_agent.crawler_profile_initializer import initialize_restricted_profile


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    (state / 'profiles').mkdir(parents=True)
    crawler = tmp_path / 'crawler'
    (crawler / 'tools').mkdir(parents=True)
    # A real isolated child executes this offline adapter. A canary makes any
    # inherited App credential/proxy environment an observable test failure.
    (crawler / 'tools' / 'camoufox_browser.py').write_text('''
import os, json, hashlib

def initialize_account_profile(account_id, *, root, generation):
    assert 'APP_MASTER_SECRET' not in os.environ
    assert 'ALL_PROXY' not in os.environ
    assert 'MEDIACRAWLER_ACCOUNT_AUTH_STATE' not in os.environ
    path = root / hashlib.sha256(('dy:' + account_id).encode()).hexdigest()
    path.mkdir(mode=0o700, exist_ok=True)
    identity = path / 'launch-identity.json'
    if not identity.exists():
        identity.write_text(json.dumps({'uid':os.geteuid(),'gid':os.getegid(),'generation':generation,'pid':os.getpid()}))
    digest=hashlib.sha256(json.dumps(json.loads(identity.read_text()),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result=dict(directory=str(path),engine='camoufox',version=os.environ['MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION'],os='macos',account_key=path.name,identity_hash=digest,generation=generation)
    (path/'profile.json').write_text(json.dumps({k:v for k,v in result.items() if k!='directory'}))
    return result
''')
    for key, value in dict(crawler_camoufox_state_root=state, media_crawler_dir=crawler,
            crawler_login_python=Path(sys.executable), crawler_camoufox_browser_version='test-pinned',
            crawler_camoufox_executable='', crawler_camoufox_executable_sha256='').items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setenv('APP_MASTER_SECRET', 'do-not-pass')
    monkeypatch.setenv('ALL_PROXY', 'http://do-not-pass')
    monkeypatch.setenv('MEDIACRAWLER_ACCOUNT_AUTH_STATE', 'do-not-pass')
    return state, crawler


def test_real_offline_child_and_retry_preserve_identity(isolated):
    state, _ = isolated
    first = initialize_restricted_profile('account', root=state/'profiles', generation='fixed')
    identity = json.loads((Path(first['directory'])/'launch-identity.json').read_text())
    assert identity['pid'] != os.getpid()
    assert identity['uid'] == os.geteuid()
    assert first == initialize_restricted_profile('account', root=state/'profiles', generation='fixed')
    assert identity == json.loads((Path(first['directory'])/'launch-identity.json').read_text())


def test_parent_forwards_restricted_uid_gid_and_no_fds(isolated):
    state, _ = isolated
    result = initialize_restricted_profile('account', root=state/'profiles', generation='fixed')
    class Finished:
        returncode = 0
        def communicate(self, **kwargs):
            return json.dumps(result).encode(), None
    with patch('backend.audit_agent.crawler_child_runtime.child_process_options',
            return_value={'user':os.geteuid(),'group':os.getegid(),'extra_groups':[],'umask':0o077}), \
         patch('backend.audit_agent.crawler_profile_initializer.subprocess.Popen', return_value=Finished()) as popen:
        initialize_restricted_profile('account', root=state/'profiles', generation='fixed')
    options = popen.call_args.kwargs
    assert options['user'] == os.geteuid() and options['group'] == os.getegid()
    assert options['close_fds'] is True and options['start_new_session'] is True
    assert 'pass_fds' not in options
    assert options['stdin'] == subprocess.DEVNULL
    assert popen.call_args.args[0][0] == str(settings.crawler_login_python)


def test_child_failure_keeps_retry_possible_and_cannot_fake_metadata(isolated):
    state, crawler = isolated
    module = crawler / 'tools' / 'camoufox_browser.py'
    original = module.read_text()
    module.write_text("raise RuntimeError('private error')")
    with pytest.raises(MigrationError, match='PROFILE_INITIALIZER_FAILED'):
        initialize_restricted_profile('account', root=state/'profiles', generation='fixed')
    module.write_text(original)
    result = initialize_restricted_profile('account', root=state/'profiles', generation='fixed')
    class Fake:
        returncode = 0
        def communicate(self, **kwargs):
            return json.dumps({**result, 'generation':'different'}).encode(), None
    with patch('backend.audit_agent.crawler_profile_initializer.subprocess.Popen', return_value=Fake()):
        with pytest.raises(MigrationError, match='RESULT_CONFLICT'):
            initialize_restricted_profile('account', root=state/'profiles', generation='fixed')


def test_initializer_refuses_unprovisioned_or_different_root(isolated):
    state, _ = isolated
    with pytest.raises(MigrationError, match='ROOT_CONFLICT'):
        initialize_restricted_profile('account', root=state/'other', generation='fixed')
    (state/'profiles').rmdir()
    with pytest.raises(MigrationError, match='STATE_NOT_PROVISIONED'):
        initialize_restricted_profile('account', root=state/'profiles', generation='fixed')


def test_initializer_child_refuses_linux_root_before_loading_sdk(monkeypatch):
    from scripts import crawler_profile_initialize as script
    monkeypatch.setattr(script.sys, 'platform', 'linux')
    monkeypatch.setattr(script.os, 'geteuid', lambda: 0)
    with pytest.raises(ValueError, match='PROFILE_INITIALIZER_ROOT_FORBIDDEN'):
        script.main(['--account-id','account','--generation','fixed','--profile-root','/state/profiles',
                     '--crawler-root','/crawler','--version','pinned'])
