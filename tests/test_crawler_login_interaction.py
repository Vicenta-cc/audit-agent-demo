import base64
from datetime import datetime, timedelta, timezone
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_login_interaction import validate_login_input
from backend.audit_agent.crawler_login_manager import CrawlerAccountLoginManager


@pytest.mark.parametrize('event', [
    {'type': 'key', 'key': 'Control+l'}, {'type': 'key', 'key': 'F12'},
    {'type': 'navigate', 'url': 'file:///etc/passwd'}, {'type': []},
    {'type': 'key', 'key': {}}, {'type': 'click', 'x': float('nan'), 'y': 0},
    {'type': 'click', 'x': 1000, 'y': 0}, {'type': 'text', 'text': 'x' * 257},
    {'type': 'text', 'text': '\n'}, {'type': 'scroll', 'delta': float('inf')},
])
def test_remote_input_rejects_shortcuts_and_unbounded_data(event):
    with pytest.raises(ValueError):
        validate_login_input(event)


def test_interactive_process_owner_frame_input_and_encrypted_completion():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        helper = root / 'helper.py'
        helper.write_text('''import json, sys, base64
assert '--interactive' in sys.argv
print(json.dumps({'type':'reauth_started'}), flush=True)
assert sys.stdin.readline().strip() == 'reauth_ready'
print(json.dumps({'type':'interactive'}), flush=True)
print(json.dumps({'type':'frame','jpeg':base64.b64encode(b'\\xff\\xd8frame').decode()}), flush=True)
event = json.loads(sys.stdin.readline())
assert event == {'type':'text','text':'123456'}
print(json.dumps({'type':'success','auth_state':{'cookies':[{'name':'sessionid','value':'test-secret'}],'origins':[]}}), flush=True)
''')
        store = CrawlerAccountStore(root / 'accounts.sqlite3')
        cipher = AuthStateCipher(key_file=root / 'auth.key')
        manager = CrawlerAccountLoginManager(store=store, cipher=cipher, python_path=Path(sys.executable), helper_path=helper)
        account = store.create(platform='dy', display_name='isolated')
        token = 'a' * 64
        try:
            with patch.object(settings, 'crawler_login_interactive', True):
                with pytest.raises(PermissionError):
                    manager.start(account)
                session = manager.start(account, token)
                assert 'owner_token' not in session and 'frame' not in session
                assert session['interactive'] is True
                with pytest.raises(PermissionError):
                    manager.start(account, 'b' * 64)
                for action in (lambda: manager.get(session['id']),
                               lambda: manager.get_frame(session['id'], 'wrong'),
                               lambda: manager.cancel(session['id'], 'wrong'),
                               lambda: manager.send_input(session['id'], 'wrong', {'type':'text','text':'bad'})):
                    with pytest.raises(PermissionError):
                        action()
                deadline = time.monotonic() + 3
                frame = b''
                while not frame and time.monotonic() < deadline:
                    frame, sequence = manager.get_frame(session['id'], token)
                    time.sleep(.02)
                assert frame == b'\xff\xd8frame'
                assert manager.get_frame(session['id'], token, after=sequence)[0] == b''
                assert manager.start(account, token)['id'] == session['id']
                manager.send_input(session['id'], token, {'type':'text','text':'123456'})
                while time.monotonic() < deadline:
                    if manager.get(session['id'], token)['status'] == 'success':
                        break
                    time.sleep(.02)
                assert manager.get(session['id'], token)['status'] == 'success'
                assert store.get(account['id'])['has_auth_state'] is True
                assert manager._sessions[session['id']].frame == b''
                with pytest.raises(ValueError):
                    manager.get_frame(session['id'], token)
        finally:
            manager.shutdown()


def test_expiry_revokes_interaction_and_stops_process():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        helper = root / 'helper.py'
        helper.write_text('import time\ntime.sleep(60)\n')
        store = CrawlerAccountStore(root / 'accounts.sqlite3')
        manager = CrawlerAccountLoginManager(store=store, cipher=AuthStateCipher(key_file=root/'key'), python_path=Path(sys.executable), helper_path=helper)
        try:
            with patch.object(settings, 'crawler_login_interactive', True):
                session = manager.start(store.create(platform='dy', display_name='isolated'), 'a'*64)
            state = manager._sessions[session['id']]
            state.frame = b'private-frame'
            state.expires_at = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            manager._expire_if_needed(session['id'])
            assert state.process.poll() is not None
            assert state.frame == b''
            assert manager.get(session['id'], 'a'*64)['status'] == 'expired'
            with pytest.raises(ValueError):
                manager.send_input(session['id'], 'a'*64, {'type':'text','text':'123'})
        finally:
            manager.shutdown()
