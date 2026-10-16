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
from test_crawler_login_manager import joined_legacy_manager


@pytest.fixture(autouse=True)
def isolated_login_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'crawler_browser_profile_root', tmp_path / 'profiles')
    monkeypatch.setattr(settings, 'task_resource_lock_dir', tmp_path / 'locks')


@pytest.mark.parametrize(
    "event",
    [
        {"type": "key", "key": "Control+l"},
        {"type": "key", "key": "F12"},
        {"type": "navigate", "url": "file:///etc/passwd"},
        {"type": []},
        {"type": "key", "key": {}},
        {"type": "click", "x": float("nan"), "y": 0},
        {"type": "click", "x": 1000, "y": 0},
        {"type": "text", "text": "x" * 257},
        {"type": "text", "text": "\n"},
        {"type": "scroll", "delta": float("inf")},
    ],
)
def test_remote_input_rejects_shortcuts_and_unbounded_data(event):
    with pytest.raises(ValueError):
        validate_login_input(event)


def test_native_prompt_cancellation_cannot_accept_arbitrary_keys_or_displays():
    assert validate_login_input(
        {"type": "dismiss_browser_prompt", "key": "Return", "display": ":99"}
    ) == {"type": "dismiss_browser_prompt"}


@pytest.mark.parametrize("token", ["", "a" * 64])
def test_douyin_interactive_v1_is_rejected_before_spawning(tmp_path, token):
    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    manager = joined_legacy_manager(
        store=store,
        cipher=AuthStateCipher(key_file=tmp_path / "key"),
        python_path=Path(sys.executable),
        helper_path=tmp_path / "unused.py",
    )
    account = store.create(platform="dy", display_name="v2-only")
    try:
        with (
            patch.object(settings, "crawler_login_interactive", True),
            patch(
                "backend.audit_agent.crawler_login_manager.subprocess.Popen"
            ) as spawn,
        ):
            with pytest.raises(ValueError, match="Login V2"):
                manager.start(account, token, owner_user_id="owner")
            spawn.assert_not_called()
        assert not manager._sessions
    finally:
        manager.shutdown()


def test_generic_qr_owner_authorization_and_encrypted_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "crawler_browser_profile_root", tmp_path / "profiles")
    monkeypatch.setattr(settings, "task_resource_lock_dir", tmp_path / "locks")
    helper = tmp_path / "helper.py"
    release = tmp_path / "complete"
    helper.write_text(
        "import json,sys,time\nfrom pathlib import Path\n"
        "assert '--interactive' not in sys.argv and '--headed' not in sys.argv\n"
        "print(json.dumps({'type':'qr','image_data_url':'data:image/png;base64,AAAA'}),flush=True)\n"
        f"deadline=time.monotonic()+10\nwhile not Path({str(release)!r}).exists():\n"
        " if time.monotonic()>deadline:raise RuntimeError('owner did not finish fixture')\n time.sleep(.01)\n"
        "print(json.dumps({'type':'success','platform_account_id':'identity','auth_state':{'cookies':[{'name':'session','value':'test-secret'}],'origins':[]}}),flush=True)\n"
    )
    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    cipher = AuthStateCipher(key_file=tmp_path / "auth.key")
    manager = joined_legacy_manager(
        store=store, cipher=cipher, python_path=Path(sys.executable), helper_path=helper
    )
    account = store.create(platform="xhs", display_name="generic owner fixture")
    try:
        session = manager.start(account, owner_user_id="owner")
        assert "owner_token" not in session and "frame" not in session
        assert session["interactive"] is False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (
                manager.get(session["id"], owner_user_id="owner")["status"]
                == "waiting_scan"
            ):
                break
            time.sleep(0.01)
        assert (
            manager.get(session["id"], owner_user_id="owner")["status"]
            == "waiting_scan"
        )
        actions = (
            lambda: manager.start(account, owner_user_id="other"),
            lambda: manager.get(session["id"], owner_user_id="other"),
            lambda: manager.get_frame(session["id"], "", owner_user_id="other"),
            lambda: manager.cancel(session["id"], owner_user_id="other"),
            lambda: manager.send_input(
                session["id"],
                "",
                {"type": "text", "text": "bad"},
                owner_user_id="other",
            ),
            lambda: manager.get(
                session["id"], owner_user_id="other", owner_is_admin=True
            ),
        )
        for action in actions:
            with pytest.raises(PermissionError):
                action()
        assert manager.start(account, owner_user_id="owner")["id"] == session["id"]
        with pytest.raises(ValueError):
            manager.get_frame(session["id"], "", owner_user_id="owner")
        with pytest.raises(ValueError):
            manager.send_input(
                session["id"],
                "",
                {"type": "text", "text": "123456"},
                owner_user_id="owner",
            )
        release.touch()
        while time.monotonic() < deadline:
            if manager.get(session["id"], owner_user_id="owner")["status"] == "success":
                break
            time.sleep(0.01)
        assert manager.get(session["id"], owner_user_id="owner")["status"] == "success"
        ciphertext = store.get_auth_state_ciphertext(account["id"])
        assert "test-secret" not in ciphertext
        assert cipher.decrypt(ciphertext)["cookies"] == [
            {"name": "session", "value": "test-secret"}
        ]
        assert manager._sessions[session["id"]].frame == b""
    finally:
        manager.shutdown()


def test_generic_qr_expiry_revokes_session_and_stops_process():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        helper = root / "helper.py"
        helper.write_text("import time\ntime.sleep(60)\n")
        store = CrawlerAccountStore(root / "accounts.sqlite3")
        manager = joined_legacy_manager(
            store=store,
            cipher=AuthStateCipher(key_file=root / "key"),
            python_path=Path(sys.executable),
            helper_path=helper,
        )
        try:
            with patch.object(settings, "crawler_login_interactive", True):
                session = manager.start(
                    store.create(platform="xhs", display_name="isolated"),
                    owner_user_id="owner",
                )
            state = manager._sessions[session["id"]]
            state.frame = b"private-frame"
            state.expires_at = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            manager._expire_if_needed(session["id"])
            assert state.process.poll() is not None
            assert state.frame == b""
            assert (
                manager.get(session["id"], owner_user_id="owner")["status"] == "expired"
            )
            with pytest.raises(ValueError):
                manager.send_input(
                    session["id"],
                    "",
                    {"type": "text", "text": "123"},
                    owner_user_id="owner",
                )
        finally:
            manager.shutdown()
