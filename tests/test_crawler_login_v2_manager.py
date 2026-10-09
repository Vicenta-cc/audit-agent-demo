import json
import sys
import tempfile
import time

import pytest
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_login_v2_manager import (
    CrawlerLoginV2Manager,
    LoginV2Error,
)

HELPER = """
import sys,json,socket,time
from backend.audit_agent.crawler_login_protocol import pack_packet,write_packet
fd=int(sys.argv[sys.argv.index('--media-fd')+1]); media=socket.socket(fileno=fd)
op=sys.argv[sys.argv.index('--operation-id')+1]
def emit(**event): print(json.dumps(event),flush=True)
emit(type='guard_requested')
for raw in sys.stdin:
 e=json.loads(raw); typ=e['type']
 if typ=='guard_ready':
  emit(type='guard_established')
  emit(type='snapshot',phase='sms_required',available_actions=['request_sms','submit_sms','open_manual','cancel'],sms={'send_available':True},challenge=None)
 elif typ=='controller_authorized': emit(type='controller_ready',controller_epoch=e['controller_epoch'])
 elif typ=='command':
  c=e['command']
  emit(type='command_result',command_id=c['command_id'],execution_status='executed',platform_status='confirmed')
  if c.get('action')=='submit_sms':
   data={'auth_state':{'cookies':[]},'platform_account_id':'identity','identity_verified':True,'reuse_verified':True}
   write_packet(media,pack_packet(json.dumps(data).encode(),{'operation_id':op},kind=2))
   emit(type='save_ready')
 elif typ=='abort_gesture': emit(type='notice',code='GESTURE_EXPIRED',message='child released')
 elif typ in ('shutdown','save_committed'): break
media.close()
"""


@pytest.fixture
def manager(tmp_path):
    accounts = CrawlerAccountStore(tmp_path / "db")
    cipher = AuthStateCipher("test")
    script = tmp_path / "helper.py"
    script.write_text(HELPER)
    manager = CrawlerLoginV2Manager(
        accounts,
        cipher,
        python_path=sys.executable,
        helper_path=script,
        lease_factory=lambda _: [tempfile.TemporaryFile()],
        timeout_seconds=20,
    )
    account = accounts.create(platform="dy", display_name="test")
    yield manager, account
    manager.shutdown()


def start(manager, account, key="key"):
    return manager.start(
        account,
        user_id="user",
        app_session_id="app",
        owner_token="x" * 32,
        request_key=key,
    )[0]


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("condition timeout")


def attach(manager, snapshot):
    ticket = manager.ticket(
        snapshot["id"], user_id="user", app_session_id="app", owner_token="x" * 32
    )
    epoch, _ = manager.attach(
        snapshot["id"],
        ticket["ticket"],
        user_id="user",
        app_session_id="app",
        controller=object(),
    )
    manager.wait_controller(snapshot["id"], epoch)
    return epoch


def command(manager, snapshot, epoch, action="request_sms", seq=1):
    current = manager.get(
        snapshot["id"], user_id="user", app_session_id="app", owner_token="x" * 32
    )
    return dict(
        type="action",
        command_id=f"cmd-{seq}",
        operation_id=current["operation_id"],
        controller_epoch=epoch,
        sequence=seq,
        expected_state_revision=current["state_revision"],
        action=action,
        payload={"code": "123456"}
        if action == "submit_sms"
        else {"phone": "13800000000"},
    )


def test_owner_tickets_and_epoch_barrier(manager):
    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    with pytest.raises(LoginV2Error):
        m.get(s["id"], user_id="user", app_session_id="wrong", owner_token="x" * 32)
    ticket = m.ticket(
        s["id"], user_id="user", app_session_id="app", owner_token="x" * 32
    )
    with pytest.raises(LoginV2Error):
        m.attach(
            s["id"],
            ticket["ticket"],
            user_id="other",
            app_session_id="app",
            controller=object(),
        )
    with pytest.raises(LoginV2Error):
        m.attach(
            s["id"],
            ticket["ticket"],
            user_id="user",
            app_session_id="app",
            controller=object(),
        )
    first = attach(m, s)
    c = command(m, s, first)
    second = attach(m, s)
    assert second > first
    with pytest.raises(LoginV2Error, match="旧操作"):
        m.command(
            s["id"], c, user_id="user", app_session_id="app", owner_token="x" * 32
        )
    m.detach(s["id"], second)
    with pytest.raises(LoginV2Error):
        m.command(
            s["id"],
            command(m, s, second),
            user_id="user",
            app_session_id="app",
            owner_token="x" * 32,
        )


def test_sensitive_command_idempotency_and_conflict(manager):
    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    c = command(m, s, epoch)
    m.command(s["id"], c, user_id="user", app_session_id="app", owner_token="x" * 32)
    wait_until(
        lambda: (m.operations.get_command(s["operation_id"], "cmd-1") or {}).get(
            "execution_status"
        )
        == "executed"
    )
    duplicate = m.command(
        s["id"], c, user_id="user", app_session_id="app", owner_token="x" * 32
    )
    assert duplicate["execution_status"] == "executed"
    c["payload"]["phone"] = "13900000000"
    with pytest.raises(ValueError, match="COMMAND_ID_CONFLICT"):
        m.command(
            s["id"], c, user_id="user", app_session_id="app", owner_token="x" * 32
        )


def test_commit_before_success_and_late_cancel_preserves_cooldown(manager):
    m, a = manager
    m.store.mark_cooldown(a["id"], "risk", "2099-01-01", failure_kind="verify")
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    m.command(
        s["id"],
        command(m, s, epoch, "submit_sms"),
        user_id="user",
        app_session_id="app",
        owner_token="x" * 32,
    )
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "done")
    result = m.cancel(
        s["id"], user_id="user", app_session_id="app", owner_token="x" * 32
    )
    assert (
        result["status"] == "success"
        and result["result"]["account_eligibility"] == "cooling_down"
    )
    assert m.operations.get(s["operation_id"])["stage"] == "committed"
    assert m.store.get(a["id"])["failure_kind"] == "verify"
    wait_until(lambda: m.sessions[s["id"]].stopped.is_set())
    assert all(h.closed for h in m.sessions[s["id"]].handles)


def test_save_failure_recoverable_without_payload_leak(manager, monkeypatch):
    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    monkeypatch.setattr(
        m.operations,
        "commit_auth",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db failed")),
    )
    m.command(
        s["id"],
        command(m, s, epoch, "submit_sms"),
        user_id="user",
        app_session_id="app",
        owner_token="x" * 32,
    )
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "failed")
    assert m.operations.get(s["operation_id"])["stage"] == "recovery_required"
    assert m.sessions[s["id"]].candidate is None
    assert "cookies" not in json.dumps(m.sessions[s["id"]].snapshot)


def test_eof_and_timeout_cleanup(manager):
    m, a = manager
    s = start(m, a)
    m.sessions[s["id"]].process.kill()
    wait_until(lambda: m.sessions[s["id"]].stopped.is_set())
    assert m.sessions[s["id"]].snapshot["phase"] == "failed"
    s = start(m, a, key="next")
    m.sessions[s["id"]].deadline = time.monotonic() - 0.01
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "expired")


def test_drain_preserves_save_reset_handshakes(manager):
    m, a = manager
    s = start(m, a)
    session = m.sessions[s["id"]]
    # Test queue independently while the writer is paused by the manager lock.
    with m._lock:
        m._enqueue(session, {"type": "reset_ready", "operation_id": s["operation_id"]})
        m._enqueue(
            session, {"type": "save_committed", "operation_id": s["operation_id"]}
        )
        m._drain(session)
        queued = list(session.commands.queue)
        assert {entry["type"] for entry in queued} >= {"reset_ready", "save_committed"}
        while not session.commands.empty():
            session.commands.get_nowait()


def test_start_idempotency_recovery_user_and_stale_revision(manager):
    m, a = manager
    s = start(m, a)
    assert start(m, a)["operation_id"] == s["operation_id"]
    with pytest.raises(ValueError, match="OPERATION_REQUEST_CONFLICT"):
        m.start(
            a,
            user_id="user",
            app_session_id="app",
            owner_token="x" * 32,
            request_key="key",
            intent="reauth",
        )
    m.cancel(s["id"], internal=True)
    wait_until(lambda: m.sessions[s["id"]].stopped.is_set())
    source = m.operations.begin(
        account_id=a["id"],
        owner_user_id="user",
        application_session_id="old",
        owner_token_hash="lost",
        request_key_hash="source",
        request_fingerprint="source",
    )
    m.operations.mark_stage(source["id"], "recovery_required")
    recovered = m.start(
        a,
        user_id="user",
        app_session_id="new",
        owner_token="y" * 32,
        request_key="recovery",
        intent="recover",
        source_operation_id=source["id"],
    )[0]
    assert recovered["source_operation_id"] == source["id"]
    assert m.sessions[recovered["id"]].app_session_id == "new"
    m.cancel(recovered["id"], internal=True)
    wait_until(lambda: m.sessions[recovered["id"]].stopped.is_set())
    m.store.save_douyin_auth_state(
        a["id"], m.cipher.encrypt({"cookies": []}), "identity"
    )
    with pytest.raises(ValueError, match="RECOVERY_SOURCE_SUPERSEDED"):
        m.start(
            a,
            user_id="user",
            app_session_id="new",
            owner_token="y" * 32,
            request_key="recovery2",
            intent="recover",
            source_operation_id=source["id"],
        )


def test_stale_area_and_state_rejected_before_dispatch(manager):
    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    m.handle_event(
        s["id"],
        {
            "type": "snapshot",
            "phase": "challenge_required",
            "available_actions": ["key"],
            "challenge": {"challenge_id": "new", "area_version": 3},
        },
    )
    snapshot = m.sessions[s["id"]].snapshot
    c = dict(
        type="key",
        key="Enter",
        command_id="key",
        operation_id=s["operation_id"],
        controller_epoch=epoch,
        sequence=1,
        expected_state_revision=snapshot["state_revision"],
        challenge_id="old",
        area_version=2,
    )
    with pytest.raises(LoginV2Error, match="验证码已刷新"):
        m.command(
            s["id"], c, user_id="user", app_session_id="app", owner_token="x" * 32
        )
    c.update(challenge_id="new", area_version=3, expected_state_revision=0)
    with pytest.raises(LoginV2Error, match="登录步骤已变化"):
        m.command(
            s["id"], c, user_id="user", app_session_id="app", owner_token="x" * 32
        )
    assert m.operations.get_command(s["operation_id"], "key") is None


def test_cancel_racing_commit_returns_committed_result(manager, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    entered, release = threading.Event(), threading.Event()
    commit = m.operations.commit_auth

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return commit(*args, **kwargs)

    monkeypatch.setattr(m.operations, "commit_auth", paused)
    m.command(
        s["id"],
        command(m, s, epoch, "submit_sms"),
        user_id="user",
        app_session_id="app",
        owner_token="x" * 32,
    )
    assert entered.wait(3)
    with ThreadPoolExecutor() as pool:
        cancel = pool.submit(m.cancel, s["id"], internal=True)
        release.set()
        assert cancel.result(timeout=3)["status"] == "success"
    assert m.operations.get(s["operation_id"])["stage"] == "committed"


def test_media_eof_and_detached_auth_revocation_cleanup(manager):
    import socket

    m, a = manager
    s = start(m, a)
    m.sessions[s["id"]].media.shutdown(socket.SHUT_RD)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "failed")
    wait_until(lambda: m.sessions[s["id"]].cleanup_done.is_set())
    allowed = [True]

    def authorize():
        if not allowed[0]:
            raise PermissionError("revoked")

    s = m.start(
        a,
        user_id="user",
        app_session_id="app",
        owner_token="x" * 32,
        request_key="new",
        authorize=authorize,
    )[0]
    allowed[0] = False
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "cancelled", timeout=3)
    assert m.sessions[s["id"]].snapshot["error"]["code"] == "APPLICATION_AUTH_REVOKED"


def test_wrong_identity_cleanup_and_existing_invalidation_preserve_risk(manager):
    m, a = manager
    m.store.save_douyin_auth_state(a["id"], m.cipher.encrypt({"cookies": []}), "bound")
    m.store.mark_cooldown(a["id"], "risk", "2099-01-01", failure_kind="verify")
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    m.handle_event(s["id"], {"type": "identity_rejected", "cleanup": False})
    existing = m.store.get(a["id"])
    assert existing["status"] == "login_required"
    assert m.store.get_auth_state_ciphertext(a["id"])
    assert existing["failure_kind"] == "verify"
    m.handle_event(s["id"], {"type": "cleanup_requested"})
    assert not m.store.get_auth_state_ciphertext(a["id"])
    assert m.store.get(a["id"])["failure_kind"] == "verify"
    assert (
        m.operations.get(s["operation_id"])["expected_auth_revision"]
        == m.store.get(a["id"])["auth_state_revision"]
    )


@pytest.mark.parametrize(
    "outcome", ["success", "failed", "expired", "cancelled", "timeout"]
)
def test_protected_legacy_douyin_preserves_risk(manager, outcome):
    from datetime import datetime, timedelta, timezone
    from backend.audit_agent.crawler_login_manager import (
        CrawlerAccountLoginManager,
        LoginSession,
    )

    m, a = manager
    m.store.mark_cooldown(a["id"], "risk message", "2099-01-01", failure_kind="verify")
    legacy = CrawlerAccountLoginManager(m.store, m.cipher)
    now = datetime.now(timezone.utc)
    legacy._sessions["legacy"] = LoginSession(
        id="legacy",
        account_id=a["id"],
        platform="dy",
        status="waiting_scan",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        expires_at=(
            now + timedelta(seconds=-1 if outcome == "timeout" else 30)
        ).isoformat(),
    )
    if outcome == "success":
        legacy._handle_event(
            "legacy",
            {
                "type": "success",
                "auth_state": {"cookies": []},
                "platform_account_id": "identity",
            },
        )
    elif outcome == "cancelled":
        legacy.cancel("legacy")
    elif outcome == "timeout":
        legacy.get("legacy")
    else:
        legacy._fail("legacy", "login error", status=outcome)
    account = m.store.get(a["id"])
    assert account["cooldown_until"] == "2099-01-01"
    assert (
        account["failure_kind"] == "verify" and account["last_error"] == "risk message"
    )


@pytest.mark.parametrize("identity", ["", "wrong"])
def test_legacy_douyin_rejects_unverified_identity(manager, identity):
    from datetime import datetime, timedelta, timezone
    from backend.audit_agent.crawler_login_manager import (
        CrawlerAccountLoginManager,
        LoginSession,
    )

    m, a = manager
    m.store.save_douyin_auth_state(a["id"], m.cipher.encrypt({"cookies": []}), "bound")
    m.store.mark_cooldown(a["id"], "risk message", "2099-01-01", failure_kind="verify")
    legacy = CrawlerAccountLoginManager(m.store, m.cipher)
    now = datetime.now(timezone.utc)
    legacy._sessions["legacy"] = LoginSession(
        id="legacy",
        account_id=a["id"],
        platform="dy",
        status="waiting_scan",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=30)).isoformat(),
    )
    legacy._handle_event(
        "legacy",
        {
            "type": "success",
            "auth_state": {"cookies": []},
            "platform_account_id": identity,
        },
    )
    assert legacy.get("legacy")["status"] == "failed"
    account = m.store.get(a["id"])
    assert (
        account["status"] == "login_required"
        and account["platform_account_id"] == "bound"
    )
    assert (
        account["failure_kind"] == "verify" and account["last_error"] == "risk message"
    )


@pytest.mark.parametrize(
    "mode,policy,headed,blocked",
    [
        ("compatibility", "precise", True, True),
        ("headless", "conservative", False, False),
    ],
)
def test_runtime_browser_and_resource_policy_reach_helper(
    manager, monkeypatch, mode, policy, headed, blocked
):
    from backend.audit_agent.config import settings

    m, a = manager
    monkeypatch.setattr(settings, "crawler_login_v2_browser_mode", mode)
    monkeypatch.setattr(settings, "crawler_login_v2_resource_policy", policy)
    s = start(m, a)
    args = m.sessions[s["id"]].process.args
    assert ("--headed" in args) == headed
    assert ("--block-media" in args) == blocked


@pytest.mark.parametrize(
    "code",
    [
        "CAPTURE_FAILED",
        "IDENTITY_MISMATCH",
        "AUTH_REVISION_CONFLICT",
        "SAVE_FAILED",
        "MEDIA_CHANNEL_FAILED",
        "CONTROLLER_REPLACED",
    ],
)
def test_terminal_messages_are_safe_chinese(manager, code):
    m, a = manager
    s = start(m, a)
    m.handle_event(
        s["id"], {"type": "error", "code": code, "message": "/private/path secret"}
    )
    error = m.sessions[s["id"]].snapshot["error"]
    assert error["code"] == code
    assert any("\u4e00" <= c <= "\u9fff" for c in error["message"])
    assert "secret" not in error["message"] and "/private" not in error["message"]


def test_unknown_exceptions_do_not_leak_details():
    from backend.audit_agent.crawler_login_v2_api import fail

    response = fail(ValueError("/private/path password=secret"))
    assert response.detail["code"] == "INVALID_COMMAND"
    assert "secret" not in response.detail["message"]
    assert "private" not in response.detail["message"]


def test_backup_marker_flag_and_candidate_provenance_are_persisted(manager):
    m, a = manager
    m.store.save_douyin_auth_state(
        a["id"], m.cipher.encrypt({"cookies": []}), "identity"
    )
    s = start(m, a)
    assert "--has-auth-backup" in m.sessions[s["id"]].process.args
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    m.command(
        s["id"],
        command(m, s, epoch, "submit_sms"),
        user_id="user",
        app_session_id="app",
        owner_token="x" * 32,
    )
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "done")
    assert m.operations.get(s["operation_id"])["credentials_changed"] == 1


def test_terminal_memory_retention_excludes_incomplete_cleanup(manager):
    from backend.audit_agent.crawler_login_v2_manager import V2Session

    m, _ = manager
    for i in range(132):
        s = V2Session(
            str(i), str(i), "account", "user", "app", "owner", {"phase": "done"}
        )
        s.cleanup_done.set()
        m.sessions[s.id] = s
    retained = V2Session(
        "cleaning", "cleaning", "account", "user", "app", "owner", {"phase": "done"}
    )
    m.sessions[retained.id] = retained
    with m._lock:
        m._prune_terminal_sessions()
    assert len(m.sessions) == 129 and "cleaning" in m.sessions
    assert "0" not in m.sessions and "131" in m.sessions
    # These synthetic records have no child process and are not shutdown targets.
    m.sessions.clear()


@pytest.mark.parametrize(
    "options",
    [
        {"intent": {}},
        {"intent": []},
        {"browser_mode": []},
        {"browser_mode": {}},
        {"source_operation_id": []},
        {"source_operation_id": 1},
        {"source_operation_id": ""},
        {"source_operation_id": "a" * 129},
    ],
)
def test_start_rejects_invalid_json_option_types_without_spawning(manager, options):
    m, a = manager
    with pytest.raises(LoginV2Error) as error:
        m.start(
            a,
            user_id="user",
            app_session_id="app",
            owner_token="x" * 32,
            request_key="invalid",
            **options,
        )
    assert error.value.code == "INVALID_LOGIN_OPTIONS" and error.value.status == 400
    assert not m.sessions


def test_public_cancel_waits_for_process_exit_and_lease_release(manager):
    m, a = manager
    s = start(m, a)
    result = m.cancel(
        s["id"], user_id="user", app_session_id="app", owner_token="x" * 32
    )
    assert result["cleanup_state"] == "complete"
    assert m.sessions[s["id"]].process.poll() is not None
    assert all(handle.closed for handle in m.sessions[s["id"]].handles)


def test_helper_notice_is_sanitized_and_nonterminal(manager):
    m, a = manager
    s = start(m, a)
    before = m.sessions[s["id"]].snapshot["phase"]
    m.handle_event(
        s["id"],
        {
            "type": "notice",
            "code": "GESTURE_EXPIRED",
            "message": "/private secret",
            "cookies": "secret",
        },
    )
    notice = m.sessions[s["id"]].results[-1]
    assert notice == {
        "type": "notice",
        "code": "GESTURE_EXPIRED",
        "message": "验证轨迹等待过久已停止，请在当前画面重新操作。",
    }
    assert m.sessions[s["id"]].snapshot["phase"] == before


def test_full_parent_graph_queue_aborts_before_more_input_and_preserves_sms(
    manager, monkeypatch
):
    import queue

    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    m.handle_event(
        s["id"],
        {
            "type": "snapshot",
            "phase": "challenge_required",
            "available_actions": ["pointer_batch"],
            "challenge": {"challenge_id": "area", "area_version": 1},
        },
    )
    session = m.sessions[s["id"]]
    with m._lock:
        original = session.commands
        session.commands = queue.Queue(maxsize=1)
        session.commands.put(
            {
                "type": "command",
                "command": {"type": "pointer_batch", "command_id": "old"},
            }
        )
        c = dict(
            type="pointer_batch",
            command_id="up",
            operation_id=s["operation_id"],
            controller_epoch=epoch,
            sequence=1,
            expected_state_revision=session.snapshot["state_revision"],
            challenge_id="area",
            area_version=1,
            gesture_id="gesture",
            mode="draw",
            points=[
                {"point_sequence": 1, "x": 0.5, "y": 0.5, "dt_ms": 0, "event": "up"}
            ],
        )
        with pytest.raises(LoginV2Error) as error:
            m.command(
                s["id"], c, user_id="user", app_session_id="app", owner_token="x" * 32
            )
        assert error.value.code == "INPUT_BACKPRESSURE"
        assert session.pending_abort and session.commands.empty()
        session.commands.put(
            {"type": "command", "command": {"type": "action", "command_id": "sms"}}
        )
        m._abort_pending_graphics(session, "INPUT_BACKPRESSURE")
        assert session.commands.get_nowait()["command"]["command_id"] == "sms"
        session.commands = original
    wait_until(
        lambda: any(
            result.get("type") == "notice" and result.get("code") == "GESTURE_EXPIRED"
            for result in session.results
        )
    )
    assert session.snapshot["phase"] == "challenge_required"


def test_aged_parent_graph_input_is_not_dispatched(manager):
    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    m.handle_event(
        s["id"],
        {
            "type": "snapshot",
            "phase": "challenge_required",
            "available_actions": ["key"],
            "challenge": {"challenge_id": "area", "area_version": 1},
        },
    )
    session = m.sessions[s["id"]]
    c = dict(
        type="key",
        key="Enter",
        command_id="aged",
        operation_id=s["operation_id"],
        controller_epoch=epoch,
        sequence=1,
        expected_state_revision=session.snapshot["state_revision"],
        challenge_id="area",
        area_version=1,
    )
    with m._lock:
        m.operations.record_command(
            s["operation_id"],
            "aged",
            "manual_action",
            c,
            application_session_id="app",
            controller_epoch=epoch,
            sequence=1,
        )
        m._enqueue(
            session,
            {
                "type": "command",
                "command": c,
                "queued_mono": time.monotonic() - 1,
                "graph_generation": 0,
            },
        )
    wait_until(
        lambda: (m.operations.get_command(s["operation_id"], "aged") or {}).get(
            "execution_status"
        )
        == "not_executed"
    )
    assert (
        m.operations.get_command(s["operation_id"], "aged")["error_code"]
        == "GESTURE_EXPIRED"
    )
    wait_until(lambda: not session.pending_abort)
    assert session.snapshot["phase"] == "challenge_required"


def test_cancel_clears_candidate_before_save_ready(manager):
    m, a = manager
    s = start(m, a)
    with m._lock:
        m.sessions[s["id"]].candidate = {
            "auth_state": {"cookies": [{"name": "sessionid", "value": "private"}]}
        }
        m.cancel(s["id"], internal=True)
        assert m.sessions[s["id"]].candidate is None
    wait_until(lambda: m.sessions[s["id"]].cleanup_done.is_set())
    assert m.sessions[s["id"]].candidate is None
