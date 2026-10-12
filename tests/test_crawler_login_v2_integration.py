"""Manager, real helper process and installed CloakBrowser against a local platform.

The wrapper is test-only: production still accepts only home/self entry names.
No real platform accounts, verification service or SMS provider are contacted.
"""
import base64
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_browser import account_profile_directory
from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager, LoginV2Error
from backend.task_admission.resources import acquire_account_handle


WRAPPER = '''
import asyncio, os, base64
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
from scripts.crawler_account_login import parse_args
import scripts.crawler_douyin_login_v2 as helper
class FixtureAdapter(helper.DouyinLoginAdapter):
    async def install_guard(self, page): pass
    async def navigate(self, entry):
        await self.page.goto(os.environ['LOGIN_FIXTURE_ORIGIN'])
        if 'qr_error=2046' in os.environ['LOGIN_FIXTURE_ORIGIN']:
            image = self.page.locator('#animate_qrcode_container img')
            if await image.count():
                source = await image.get_attribute('src')
                self.accept_qr_ticket({'token':'fixture-ticket','qrcode':source.split(',',1)[1]})
                self.current_qr_error = 2046
    async def has_auth(self):
        if 'qr_error=2046' in os.environ['LOGIN_FIXTURE_ORIGIN']:
            return any(c['name']=='sessionid' for c in await self.context.cookies(os.environ['LOGIN_FIXTURE_ORIGIN']))
        return await self.page.evaluate("localStorage.getItem('fixtureLogin')==='saved'")
    async def trusted_identity(self):
        if await self.has_auth():
            self.identity = 'fixture-account'
        return self.identity
helper.DouyinLoginAdapter = FixtureAdapter
asyncio.run(helper.run_login_v2(parse_args()))
'''


def wait_for(predicate, manager=None, session_id=None, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        if manager and session_id:
            snapshot = manager.sessions[session_id].snapshot
            if snapshot['phase'] in {'failed', 'expired', 'cancelled'}:
                raise AssertionError(f'Unexpected terminal result: {snapshot}')
        time.sleep(.03)
    raise AssertionError('Timed out waiting for integration boundary')


@pytest.fixture
def real_login(tmp_path, monkeypatch):
    python = Path(os.getenv('LOGIN_BROWSER_TEST_PYTHON', str(settings.crawler_login_python)))
    sibling_python = Path(__file__).resolve().parents[2]/'media-crawler'/'.venv'/'bin'/'python'
    if not python.exists() and 'LOGIN_BROWSER_TEST_PYTHON' not in os.environ and sibling_python.exists():
        python = sibling_python
    if not python.exists():
        pytest.skip('CloakBrowser Python runtime is not installed')
    probe = subprocess.run([str(python), '-c', 'from cloakbrowser.config import get_binary_path; print(get_binary_path())'], capture_output=True, text=True)
    if probe.returncode or not Path(probe.stdout.strip()).is_file():
        pytest.skip('Pinned CloakBrowser binary is not installed')
    import cv2
    qr = cv2.QRCodeEncoder_create().encode('https://fixture.invalid/scan')
    qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    qr = cv2.resize(qr, (180, 180), interpolation=cv2.INTER_NEAREST)
    _, png = cv2.imencode('.png', qr)
    image = base64.b64encode(png).decode()
    requests = []
    html = f'''<meta charset="utf-8"><style>
    .douyin_login_new_class{{position:absolute;left:100px;top:100px;width:500px;height:400px}}
    #captcha_container{{display:none;position:absolute;left:150px;top:180px;width:300px;height:180px;background:#ddd}}
    #slider{{position:absolute;left:0;top:40px}} #drawing{{position:absolute;left:0;top:90px}}
    </style><div class="douyin_login_new_class">
    <div id="animate_qrcode_container"><img src="data:image/png;base64,{image}"></div>
    <span>验证码登录</span><input placeholder="请输入手机号" type="tel">
    <input placeholder="验证码" maxlength="6">
    <button onclick="fetch('/sms');captcha_container.style.display='block'">获取验证码</button>
    <button onclick="localStorage.setItem('fixtureLogin','saved');document.querySelector('.douyin_login_new_class').remove()">登录</button>
    </div><div id="captcha_container"><p>拖动滑块，然后画图</p>
    <input id="slider" type="range" style="width:260px"><canvas id="drawing" width="280" height="80"></canvas></div>
    <script>let active=false,points=0; drawing.onpointerdown=e=>{{active=true;drawing.setPointerCapture(e.pointerId)}};
    drawing.onpointermove=e=>{{if(active)points++}};
    drawing.onpointerup=e=>{{active=false;if(points>=2&&+slider.value>80)captcha_container.style.display='none'}};
    if(location.search.includes('qr_error=2046')){{
      const complete=document.querySelector('.douyin_login_new_class button:last-of-type');
      complete.style.cssText='position:absolute;left:10px;top:350px;width:100px;height:30px';
      complete.onclick=()=>{{
        const delay=Number(new URLSearchParams(location.search).get('cookie_delay'));
        if(delay===0)document.cookie='sessionid=fixture;path=/;max-age=3600';
        localStorage.setItem('fixtureLogin','saved');document.querySelector('.douyin_login_new_class').remove();
        if(delay>0)setTimeout(()=>document.cookie='sessionid=fixture;path=/;max-age=3600',delay);
      }};
    }}
    if(localStorage.getItem('fixtureLogin')==='saved')document.querySelector('.douyin_login_new_class').remove();</script>'''

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write((html if self.path != '/sms' else 'sent').encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv('LOGIN_FIXTURE_ORIGIN', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('CRAWLER_AUTH_ENCRYPTION_KEY', 'integration-key')
    monkeypatch.setenv('XHS_AUDIT_DATA_DIR', str(tmp_path/'child-data'))
    monkeypatch.setenv('XHS_AUDIT_OUTPUTS_DIR', str(tmp_path/'child-output'))
    monkeypatch.setenv('CRAWLER_AUTH_KEY_FILE', str(tmp_path/'child-key'))
    monkeypatch.setattr(settings, 'crawler_browser_profile_root', tmp_path/'profiles')
    monkeypatch.setattr(settings, 'media_crawler_dir', python.parent.parent.parent)
    monkeypatch.setattr(settings, 'request_scheduler_db', tmp_path/'scheduler.sqlite3')
    monkeypatch.setattr(settings, 'crawler_login_browser_version', '')
    monkeypatch.setattr(settings, 'crawler_login_v2_browser_mode', 'headless')
    wrapper = tmp_path/'helper.py'
    wrapper.write_text(WRAPPER)
    accounts = CrawlerAccountStore(tmp_path/'accounts.sqlite3')
    cipher = AuthStateCipher('integration-key')
    manager = CrawlerLoginV2Manager(accounts, cipher, python_path=python, helper_path=wrapper, timeout_seconds=60)
    account = accounts.create(platform='dy', display_name='fixture')
    accounts.mark_cooldown(account['id'], 'keep-risk', '2099-01-01', failure_kind='verify')
    try:
        yield manager, account, requests
    finally:
        manager.shutdown()
        server.shutdown()
        server.server_close()
        assert all(s.process.poll() is not None and all(h.closed for h in s.handles)
                   for s in manager.sessions.values())


OWNER = dict(user_id='integration-user', app_session_id='integration-app', owner_token='x'*32)


def connect(manager, snapshot, owner=OWNER):
    ticket = manager.ticket(snapshot['id'], **owner)
    epoch, _ = manager.attach(snapshot['id'], ticket['ticket'], user_id=owner['user_id'], app_session_id=owner['app_session_id'], controller=object())
    manager.wait_controller(snapshot['id'], epoch)
    return epoch


def dispatch(manager, snapshot, epoch, sequence, action=None, owner=OWNER, **fields):
    current = manager.get(snapshot['id'], **owner)
    command = dict(type='action' if action else 'pointer_batch', command_id=f'{snapshot["id"]}-{sequence}',
                   operation_id=current['operation_id'], controller_epoch=epoch, sequence=sequence,
                   expected_state_revision=current['state_revision'], **fields)
    if action:
        command['action'] = action
    else:
        command['gesture_id'] = f'gesture-{sequence}'
        command['mode'] = 'draw'
        for index, point in enumerate(command['points'], 1):
            point['point_sequence'] = index
    manager.command(snapshot['id'], command, **owner)
    wait_for(lambda: any(r.get('command_id') == command['command_id'] for r in manager.sessions[snapshot['id']].results), manager, snapshot['id'])
    assert next(r for r in manager.sessions[snapshot['id']].results if r.get('command_id') == command['command_id'])['execution_status'] == 'executed'
    return command


def complete_fixture_login(manager, account, requests, key='first'):
    snapshot, created = manager.start(account, request_key=key, **OWNER)
    assert created
    sid = snapshot['id']
    wait_for(lambda: manager.sessions[sid].snapshot['phase'] == 'qr_ready', manager, sid)
    assert manager.sessions[sid].snapshot['qr']['image_data_url'].startswith('data:image/png;base64,')
    assert manager.sessions[sid].snapshot['qr']['state'] == 'valid'
    # Host account lease remains locked while a real child owns the browser.
    assert acquire_account_handle('dy', account['id']) is None
    assert (account_profile_directory(account['id'])/'auth-imported').is_file()
    with (account_profile_directory(account['id'])/'adapter.lock').open('a') as handle:
        with pytest.raises(BlockingIOError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    epoch = connect(manager, snapshot)
    # A replacement controller invalidates a delayed REST-style command.
    epoch = connect(manager, snapshot)
    stale = dict(type='action', command_id='late', operation_id=snapshot['operation_id'], controller_epoch=epoch-1,
                 sequence=1, expected_state_revision=manager.sessions[sid].snapshot['state_revision'], action='switch_to_sms')
    with pytest.raises(LoginV2Error) as error:
        manager.command(sid, stale, **OWNER)
    assert error.value.code == 'CONTROLLER_REPLACED'
    dispatch(manager, snapshot, epoch, 1, 'switch_to_sms')
    wait_for(lambda: manager.sessions[sid].snapshot['phase'] == 'sms_required', manager, sid)
    assert {f['name'] for f in manager.sessions[sid].snapshot['sms']['fields']} == {'phone', 'code'}
    sms = dispatch(manager, snapshot, epoch, 2, 'request_sms', payload={'phone':'13800000000'})
    wait_for(lambda: manager.sessions[sid].snapshot['phase'] == 'challenge_required', manager, sid)
    manager.command(sid, sms, **OWNER)
    assert requests.count('/sms') == 1
    wait_for(lambda: manager.sessions[sid].frame, manager, sid)
    challenge = manager.sessions[sid].snapshot['challenge']
    assert challenge['image_width'] > 0 and challenge['image_height'] > 0
    assert challenge['interaction_mode'] in {'draw', 'unknown', 'slider', 'click'}
    assert 'pointer_batch' in challenge['allowed_actions']
    common = dict(challenge_id=challenge['challenge_id'], area_version=challenge['area_version'])
    dispatch(manager, snapshot, epoch, 3, points=[
        {'x':.05,'y':.27,'event':'down','dt_ms':0}, {'x':.9,'y':.27,'event':'move','dt_ms':0},
        {'x':.9,'y':.27,'event':'up','dt_ms':0}], **common)
    dispatch(manager, snapshot, epoch, 4, points=[
        {'x':.1,'y':.65,'event':'down','dt_ms':0}, {'x':.3,'y':.7,'event':'move','dt_ms':0},
        {'x':.6,'y':.8,'event':'move','dt_ms':0}, {'x':.8,'y':.7,'event':'up','dt_ms':0}], **common)
    wait_for(lambda: manager.sessions[sid].snapshot['phase'] == 'sms_required', manager, sid)
    dispatch(manager, snapshot, epoch, 5, 'submit_sms', payload={'code':'123456'})
    return snapshot


def test_real_helper_commit_risk_and_profile_reuse(real_login):
    manager, account, requests = real_login
    snapshot = complete_fixture_login(manager, account, requests)
    sid = snapshot['id']
    wait_for(lambda: manager.sessions[sid].snapshot['phase'] == 'done', manager, sid)
    wait_for(lambda: manager.sessions[sid].cleanup_done.is_set())
    saved = manager.store.get(account['id'])
    assert saved['platform_account_id'] == 'fixture-account'
    assert saved['failure_kind'] == 'verify' and saved['cooldown_until'] == '2099-01-01'
    assert saved['auth_state_revision'] > account['auth_state_revision']
    state = manager.cipher.decrypt(manager.store.get_auth_state_ciphertext(account['id']))
    assert any(v['name'] == 'fixtureLogin' and v['value'] == 'saved' for o in state['origins'] for v in o['localStorage'])
    public = json.dumps(manager.get(sid, **OWNER))
    assert 'fixtureLogin' not in public and 'localStorage' not in public and 'cookies' not in public
    assert manager.sessions[sid].candidate is None
    assert manager.operations.get(snapshot['operation_id'])['stage'] == 'committed'
    assert manager.cancel(sid, **OWNER)['status'] == 'success'
    handle = acquire_account_handle('dy', account['id'])
    assert handle is not None
    handle.close()
    registry = settings.crawler_browser_profile_root/'profiles.sqlite3'
    with sqlite3.connect(registry) as db:
        before = db.execute('SELECT seed,version FROM profiles').fetchall()
    again, _ = manager.start(saved, request_key='check-persisted', **OWNER)
    wait_for(lambda: manager.sessions[again['id']].snapshot['phase'] == 'done', manager, again['id'])
    wait_for(lambda: manager.sessions[again['id']].cleanup_done.is_set())
    assert requests.count('/sms') == 1
    with sqlite3.connect(registry) as db:
        assert db.execute('SELECT seed,version FROM profiles').fetchall() == before


@pytest.mark.parametrize('existing_backup', [False, True], ids=['empty-backup', 'stale-backup'])
def test_real_helper_failed_commit_recovers_without_reauth(real_login, monkeypatch, existing_backup):
    manager, account, requests = real_login
    old_ciphertext = ''
    if existing_backup:
        old_ciphertext = manager.cipher.encrypt({'cookies': [], 'origins': [
            {'origin': 'https://www.douyin.com', 'localStorage': [{'name':'fixtureLogin','value':'old-credentials'}]}]})
        account = manager.store.save_douyin_auth_state(account['id'], old_ciphertext, 'fixture-account')
        assert account['has_auth_state']
        assert account['failure_kind'] == 'verify'
        # Model an already managed Profile whose login expired, not a legacy
        # database-only account (that separate case requires explicit migration).
        profile = account_profile_directory(account['id'])
        profile.mkdir(parents=True)
        (profile/'auth-imported').touch(mode=0o600)
    revision_before = manager.store.get(account['id'])['auth_state_revision']
    original = manager.operations.commit_auth
    monkeypatch.setattr(manager.operations, 'commit_auth', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('simulated SQLite failure')))
    snapshot = complete_fixture_login(manager, account, requests)
    wait_for(lambda: manager.sessions[snapshot['id']].snapshot['phase'] == 'failed')
    wait_for(lambda: manager.sessions[snapshot['id']].cleanup_done.is_set())
    source = manager.operations.get(snapshot['operation_id'])
    assert source['stage'] == 'recovery_required'
    failed_account = manager.store.get(account['id'])
    assert failed_account['auth_state_revision'] == revision_before + 1
    assert failed_account['auth_state_revision'] == source['expected_auth_revision']
    assert manager.store.get_auth_state_ciphertext(account['id']) == old_ciphertext
    assert failed_account['platform_account_id'] == ('fixture-account' if existing_backup else '')
    monkeypatch.setattr(manager.operations, 'commit_auth', original)
    new_owner = {**OWNER, 'app_session_id':'after-application-login', 'owner_token':'y'*32}
    recovered, _ = manager.start(manager.store.get(account['id']), request_key='recovery', intent='recover',
                                 source_operation_id=snapshot['operation_id'], **new_owner)
    wait_for(lambda: manager.sessions[recovered['id']].snapshot['phase'] == 'done', manager, recovered['id'])
    wait_for(lambda: manager.sessions[recovered['id']].cleanup_done.is_set())
    assert manager.sessions[recovered['id']].snapshot['result']['auth_saved']
    saved = manager.store.get(account['id'])
    assert saved['has_auth_state']
    assert saved['platform_account_id'] == 'fixture-account'
    assert saved['auth_state_revision'] == failed_account['auth_state_revision'] + 1
    ciphertext = manager.store.get_auth_state_ciphertext(account['id'])
    assert ciphertext and ciphertext != old_ciphertext
    state = manager.cipher.decrypt(ciphertext)
    assert any(v['name'] == 'fixtureLogin' and v['value'] == 'saved'
               for origin in state['origins'] for v in origin['localStorage'])
    assert requests.count('/sms') == 1
    assert manager.operations.get(snapshot['operation_id'])['stage'] == 'recovered'
    assert saved['failure_kind'] == 'verify' and saved['cooldown_until'] == '2099-01-01'


@pytest.mark.parametrize('cookie_delay', [0, 600, -1, -2])
def test_real_qr_error_verification_disappears_preserves_native_auth_completion(real_login, monkeypatch, cookie_delay):
    manager, account, requests = real_login
    origin = os.environ['LOGIN_FIXTURE_ORIGIN']
    if cookie_delay == -2:
        account = manager.store.update(account['id'], platform_account_id='fixture-original')
    actual_delay = 0 if cookie_delay == -2 else cookie_delay
    monkeypatch.setenv('LOGIN_FIXTURE_ORIGIN', origin + f'?qr_error=2046&cookie_delay={actual_delay}')
    snapshot, _ = manager.start(account, request_key=f'qr-error-{cookie_delay}', **OWNER)
    sid = snapshot['id']
    wait_for(lambda: manager.sessions[sid].snapshot['phase'] == 'manual_required', manager, sid)
    epoch = connect(manager, snapshot)
    wait_for(lambda: manager.sessions[sid].frame, manager, sid)
    challenge = manager.sessions[sid].snapshot['challenge']
    dispatch(manager, snapshot, epoch, 1, points=[
        {'x':.1,'y':.91,'event':'down','dt_ms':0},
        {'x':.1,'y':.91,'event':'up','dt_ms':0},
    ], challenge_id=challenge['challenge_id'], area_version=challenge['area_version'])
    if cookie_delay < 0:
        wait_for(lambda: manager.sessions[sid].snapshot['phase'] == 'failed', timeout=25)
        assert manager.sessions[sid].snapshot['error']['code'] == ('IDENTITY_MISMATCH' if cookie_delay == -2 else 'IDENTITY_UNCONFIRMED')
        assert not manager.store.get(account['id'])['has_auth_state']
    else:
        wait_for(lambda: manager.sessions[sid].snapshot['phase'] == 'done', manager, sid)
        saved = manager.store.get(account['id'])
        assert saved['has_auth_state'] and saved['platform_account_id'] == 'fixture-account'
        state = manager.cipher.decrypt(manager.store.get_auth_state_ciphertext(account['id']))
        assert any(c['name'] == 'sessionid' for c in state['cookies'])
        assert manager.operations.get(snapshot['operation_id'])['stage'] == 'committed'
        assert saved['failure_kind'] == 'verify' and saved['cooldown_until'] == '2099-01-01'
    assert requests.count('/sms') == 0
