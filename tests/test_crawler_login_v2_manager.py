import json
import os
from pathlib import Path
import sys
import tempfile
import time

import pytest
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_login_v2_manager import (
    CrawlerLoginV2Manager,
    LoginV2Error,
    digest,
    TERMINAL,
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
def manager(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.audit_agent.config import settings
    from backend.audit_agent import crawler_route, crawler_child_runtime

    # This suite owns the manager protocol, not browser networking. Keep the
    # subprocess real, while isolating route/authority admission at that boundary.
    # Full inherited-FD authority behavior is exercised by its integration suite.
    crawler_root = os.environ.get("MEDIACRAWLER_DIR") or os.environ.get("MEDIA_CRAWLER_DIR")
    monkeypatch.setattr(settings, "media_crawler_dir", Path(crawler_root) if crawler_root else Path(__file__).resolve().parents[2] / "wukong-proxy-crawler")
    monkeypatch.setattr(settings, "crawler_browser_profile_root", tmp_path / "profiles")
    monkeypatch.setattr(settings, "crawler_camoufox_state_root", tmp_path / "child-state")
    monkeypatch.setattr(settings, "request_scheduler_db", tmp_path / "scheduler.db")
    monkeypatch.setattr(settings, "task_resource_lock_dir", tmp_path / "locks")
    monkeypatch.setattr(crawler_route, "resolve_account_route", lambda *a, **k: None)
    monkeypatch.setattr(crawler_route, "acquire_route_execution", lambda *a, **k: SimpleNamespace(
        snapshot=None, execution_id=a[1], pass_fds=(), close=lambda: None,
    ))
    monkeypatch.setattr(crawler_child_runtime, "authority_for", lambda *a, **k: SimpleNamespace(
        env={}, pass_fds=(), bind_child=lambda pid: None, close=lambda: None,
    ))
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
    with accounts._connect() as conn:
        conn.execute("UPDATE crawler_accounts SET profile_engine='camoufox',profile_generation='unit-generation' WHERE id=?", (account["id"],))
    account = accounts.get(account["id"])
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


def test_authority_fd_is_inherited_bound_to_child_and_closed_on_cancel(manager, monkeypatch):
    from backend.audit_agent import crawler_child_runtime
    from types import SimpleNamespace

    m, account = manager
    reader, writer = os.pipe()
    bound = []
    closed = []

    def close():
        if not closed:
            closed.append(True)
            os.close(reader)
            os.close(writer)

    authority = SimpleNamespace(env={"MEDIACRAWLER_REQUEST_AUTHORITY_FD": str(reader)},
                                pass_fds=(reader,), bind_child=bound.append, close=close)
    monkeypatch.setattr(crawler_child_runtime, "authority_for", lambda *a, **k: authority)
    original = m.process_factory
    launches = []

    def launch(args, **kwargs):
        launches.append((args, kwargs))
        return original(args, **kwargs)

    m.process_factory = launch
    try:
        snapshot = start(m, account)
        session = m.sessions[snapshot["id"]]
        wait_until(lambda: session.snapshot["phase"] == "sms_required")
        assert bound == [session.process.pid]
        assert reader in launches[0][1]["pass_fds"]
        assert launches[0][1]["env"]["MEDIACRAWLER_REQUEST_AUTHORITY_FD"] == str(reader)
        assert launches[0][0].count("--headed") == int(sys.platform == "linux")
        m.cancel(snapshot["id"], user_id="user", app_session_id="app", owner_token="x" * 32)
        wait_until(lambda: bool(closed))
        with pytest.raises(OSError):
            os.fstat(reader)
        assert session.process.poll() is not None
    finally:
        close()


def test_spawn_failure_closes_request_authority_and_account_leases(manager, monkeypatch):
    from backend.audit_agent import crawler_child_runtime
    from types import SimpleNamespace

    m, account = manager
    handles = [tempfile.TemporaryFile()]
    closed = []
    m.lease_factory = lambda _: handles
    authority = SimpleNamespace(env={}, pass_fds=(), bind_child=lambda pid: None,
                                close=lambda: closed.append(True))
    monkeypatch.setattr(crawler_child_runtime, "authority_for", lambda *a, **k: authority)

    def fail(*args, **kwargs):
        raise OSError("fixture spawn failure")

    m.process_factory = fail
    with pytest.raises(OSError, match="fixture spawn failure"):
        start(m, account)
    assert closed == [True]
    assert all(handle.closed for handle in handles)
    assert not m.sessions


def test_authority_bind_failure_reaps_already_started_child(manager, monkeypatch):
    from backend.audit_agent import crawler_child_runtime
    from types import SimpleNamespace

    m, account = manager
    children = []
    closed = []
    original = m.process_factory

    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    def fail_binding(pid):
        raise RuntimeError("fixture authority bind failure")

    m.process_factory = launch
    authority = SimpleNamespace(env={}, pass_fds=(), bind_child=fail_binding,
                                close=lambda: closed.append(True))
    monkeypatch.setattr(crawler_child_runtime, "authority_for", lambda *a, **k: authority)
    try:
        with pytest.raises(RuntimeError, match="fixture authority bind failure"):
            start(m, account)
        assert len(children) == 1
        assert children[0].poll() is not None, "unregistered helper survives failed authority binding"
        assert closed == [True]
        assert not m.sessions
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)


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
    "platform,mode,policy,headed,blocked",
    [
        ("linux", "compatibility", "precise", True, True),
        ("darwin", "headless", "conservative", False, False),
    ],
)
def test_runtime_browser_and_resource_policy_reach_helper(
    manager, monkeypatch, platform, mode, policy, headed, blocked
):
    from backend.audit_agent.config import settings

    m, a = manager
    monkeypatch.setattr(sys, "platform", platform)
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


def test_control_writer_supports_pipe_descriptors_above_select_limit(manager):
    import fcntl
    import os
    import resource
    import threading
    from types import SimpleNamespace
    from backend.audit_agent.crawler_login_v2_manager import V2Session

    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft <= 1025:
        pytest.skip("RLIMIT_NOFILE does not permit a descriptor above 1024")
    m, _ = manager
    read_fd, write_fd = os.pipe()
    high_fd = fcntl.fcntl(write_fd, fcntl.F_DUPFD, 1024)
    os.close(write_fd)
    stream = os.fdopen(high_fd, "wb", buffering=0)
    os.set_blocking(read_fd, False)
    session = V2Session(
        "high-fd",
        "operation",
        "account",
        "user",
        "app",
        "owner",
        {"phase": "checking"},
        process=SimpleNamespace(stdin=stream),
    )
    assert stream.fileno() >= 1024
    session.epoch = 1
    session.commands.put(
        {"type": "controller_authorized", "controller_epoch": 1, "attached": True}
    )
    writer = threading.Thread(target=m._writer, args=(session,), daemon=True)
    writer.start()
    chunks = []

    def received():
        try:
            chunks.append(os.read(read_fd, 4096))
        except BlockingIOError:
            pass
        return b"\n" in b"".join(chunks)

    try:
        wait_until(received)
        assert json.loads(b"".join(chunks)) == {
            "type": "controller_authorized",
            "controller_epoch": 1,
            "attached": True,
        }
        assert session.snapshot["phase"] == "checking"
    finally:
        session.stopped.set()
        writer.join(timeout=3)
        stream.close()
        os.close(read_fd)
    assert not writer.is_alive()


@pytest.mark.parametrize("stale_kind", ["state", "challenge"])
def test_current_controller_stale_up_reliably_aborts_without_accepting(
    manager, stale_kind
):
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
            "challenge": {"challenge_id": "current", "area_version": 2},
        },
    )
    session = m.sessions[s["id"]]
    command = dict(
        type="pointer_batch",
        command_id="stale-up",
        operation_id=s["operation_id"],
        controller_epoch=epoch,
        sequence=1,
        expected_state_revision=session.snapshot["state_revision"]
        - (stale_kind == "state"),
        challenge_id="old" if stale_kind == "challenge" else "current",
        area_version=2,
        gesture_id="applied-down",
        mode="draw",
        points=[{"point_sequence": 2, "x": 0.5, "y": 0.5, "dt_ms": 0, "event": "up"}],
    )
    with m._lock:
        with pytest.raises(LoginV2Error) as error:
            m.command(
                s["id"],
                command,
                user_id="user",
                app_session_id="app",
                owner_token="x" * 32,
            )
        assert error.value.code == (
            "STALE_STATE" if stale_kind == "state" else "STALE_CHALLENGE"
        )
        assert session.pending_abort and session.graph_generation == 1
        assert m.operations.get_command(s["operation_id"], "stale-up") is None
    # The child explicitly acknowledges the reliable abort channel in this fixture.
    wait_until(
        lambda: any(
            result.get("type") == "notice" and result.get("code") == "GESTURE_EXPIRED"
            for result in session.results
        )
    )
    assert session.snapshot["phase"] == "challenge_required"


def test_obsolete_controller_stale_up_cannot_abort_successor(manager):
    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    old_epoch = attach(m, s)
    current_epoch = attach(m, s)
    m.handle_event(
        s["id"],
        {
            "type": "snapshot",
            "phase": "challenge_required",
            "available_actions": ["pointer_batch"],
            "challenge": {"challenge_id": "current", "area_version": 2},
        },
    )
    session = m.sessions[s["id"]]
    command = dict(
        type="pointer_batch",
        command_id="old-up",
        operation_id=s["operation_id"],
        controller_epoch=old_epoch,
        sequence=1,
        expected_state_revision=0,
        challenge_id="old",
        area_version=0,
        gesture_id="old",
        mode="draw",
        points=[{"point_sequence": 2, "x": 0.5, "y": 0.5, "dt_ms": 0, "event": "up"}],
    )
    with m._lock:
        with pytest.raises(LoginV2Error) as error:
            m.command(
                s["id"],
                command,
                user_id="user",
                app_session_id="app",
                owner_token="x" * 32,
                epoch=old_epoch,
            )
        assert error.value.code == "CONTROLLER_REPLACED"
        assert not session.pending_abort and session.graph_generation == 0
        assert session.epoch == current_epoch
        assert m.operations.get_command(s["operation_id"], "old-up") is None


@pytest.mark.parametrize("replace", [False, True])
def test_accepted_up_dispatch_rechecks_release_only_current_epoch(manager, replace):
    m, a = manager
    snapshot = start(m, a)
    wait_until(lambda: m.sessions[snapshot["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, snapshot)
    session = m.sessions[snapshot["id"]]
    m.handle_event(
        session.id,
        {
            "type": "snapshot",
            "phase": "challenge_required",
            "available_actions": ["pointer_batch"],
            "challenge": {"challenge_id": "current", "area_version": 2},
        },
    )
    up = dict(
        type="pointer_batch",
        command_id="queued-up",
        operation_id=session.operation_id,
        controller_epoch=epoch,
        sequence=1,
        expected_state_revision=session.snapshot["state_revision"],
        challenge_id="current",
        area_version=2,
        gesture_id="prior-applied-down",
        mode="draw",
        points=[{"point_sequence": 2, "x": 0.5, "y": 0.5, "dt_ms": 0, "event": "up"}],
    )
    with m._lock:
        m.command(
            session.id, up, user_id="user", app_session_id="app", owner_token="x" * 32
        )
        # Holding the manager lock guarantees the writer cannot validate before
        # this state/controller transition, even if it already dequeued the up.
        if replace:
            session.epoch += 1
            session.snapshot["controller_epoch"] = session.epoch
        else:
            m.handle_event(
                session.id, {"type": "snapshot", "phase": "challenge_required"}
            )
    wait_until(
        lambda: m.operations.get_command(session.operation_id, "queued-up")[
            "execution_status"
        ]
        == "not_executed"
    )
    result = m.operations.get_command(session.operation_id, "queued-up")
    assert result["error_code"] == ("CONTROLLER_REPLACED" if replace else "STALE_STATE")
    if replace:
        assert session.graph_generation == 0 and not session.pending_abort
        assert not any(r.get("code") == "GESTURE_EXPIRED" for r in session.results)
    else:
        wait_until(
            lambda: any(
                r.get("type") == "notice" and r.get("code") == "GESTURE_EXPIRED"
                for r in session.results
            )
        )
        assert session.graph_generation == 1


def test_direct_edit_journal_contains_only_fingerprint_and_clears_owner_frame(manager):
    m, a = manager
    s = start(m, a)
    wait_until(lambda: m.sessions[s["id"]].snapshot["phase"] == "sms_required")
    epoch = attach(m, s)
    m.handle_event(
        s["id"],
        {
            "type": "snapshot",
            "phase": "manual_required",
            "available_actions": ["edit_input"],
            "challenge": {"challenge_id": "area", "area_version": 1},
        },
    )
    current = m.get(s["id"], user_id="user", app_session_id="app", owner_token="x" * 32)
    edit = {
        "type": "edit_input",
        "command_id": "direct-edit",
        "operation_id": s["operation_id"],
        "controller_epoch": epoch,
        "sequence": 1,
        "expected_state_revision": current["state_revision"],
        "challenge_id": "area",
        "area_version": 1,
        "field_id": "opaque-field",
        "client_edit_revision": 1,
        "value": "SECRET-OTP-123",
        "selection_start": 14,
        "selection_end": 14,
    }
    result = m.command(
        s["id"], edit, user_id="user", app_session_id="app", owner_token="x" * 32
    )
    assert result["execution_status"] == "accepted"
    row = m.operations.get_command(s["operation_id"], "direct-edit")
    assert row["kind"] == "manual_action" and row["request_fingerprint"]
    assert "SECRET-OTP-123" not in str(row)
    with m._lock:
        m.sessions[s["id"]].frame = b"transient-sensitive-frame"
    m.detach(s["id"], epoch)
    assert m.sessions[s["id"]].frame == b""


def test_explicit_claim_preserves_browser_and_fences_previous_owner_and_epoch(manager):
    m,a=manager
    s=start(m,a)
    wait_until(lambda:m.sessions[s['id']].snapshot['phase']=='sms_required')
    epoch=attach(m,s)
    live=m.sessions[s['id']]
    process,deadline,operation,handles=live.process,live.deadline,live.operation_id,list(live.handles)
    old_ticket=m.ticket(s['id'],user_id='user',app_session_id='app',owner_token='x'*32)['ticket']
    current_authorization=[]
    with m._lock:
        queued=command(m,s,epoch)
        m.command(s['id'],queued,user_id='user',app_session_id='app',owner_token='x'*32)
        new,old=m.claim_active(a['id'],user_id='user',app_session_id='new-app',owner_token='y'*32,
            authorize=lambda:current_authorization.append('new'),expected_operation_id=operation)
    wait_until(lambda:m.operations.get_command(operation,queued['command_id'])['execution_status']=='not_executed')
    assert new['id']==s['id'] and new['operation_id']==operation
    assert live.process is process and process.poll() is None and live.deadline==deadline and live.handles==handles
    assert live.epoch>epoch and live.controller is None
    op=m.operations.get(operation)
    assert op['application_session_id']=='new-app' and op['owner_token_hash']==digest('y'*32)
    assert len(m.operations.list(account_id=a['id']))==1
    before=live.epoch
    repeated,_=m.claim_active(a['id'],user_id='user',app_session_id='new-app',owner_token='y'*32,authorize=lambda:None)
    assert repeated['controller_epoch']==before
    with pytest.raises(LoginV2Error,match='属于'):
        m.get(s['id'],user_id='user',app_session_id='app',owner_token='x'*32)
    with pytest.raises(LoginV2Error):
        m.claim_active(a['id'],user_id='user',app_session_id='app',owner_token='x'*32,authorize=lambda:None)
    with pytest.raises(LoginV2Error):
        m.attach(s['id'],old_ticket,user_id='user',app_session_id='app',controller=object())
    with pytest.raises(LoginV2Error):
        start(m,a)  # old automatic start/replay cannot steal back the browser.
    ticket=m.ticket(s['id'],user_id='user',app_session_id='new-app',owner_token='y'*32)
    fresh,_=m.attach(s['id'],ticket['ticket'],user_id='user',app_session_id='new-app',controller=object())
    m.wait_controller(s['id'],fresh)
    m.detach(s['id'],epoch)
    m.revoke_application(s['id'],'user','app',expected_epoch=epoch)
    assert live.snapshot['phase'] not in TERMINAL and live.epoch==fresh
    # Same application cookie with a different tab token is also epoch-fenced.
    another,_=m.claim_active(a['id'],user_id='user',app_session_id='new-app',owner_token='z'*32,authorize=lambda:None)
    m.revoke_application(s['id'],'user','new-app',expected_epoch=fresh)
    assert live.snapshot['phase'] not in TERMINAL


def test_explicit_claim_rejects_other_user_stale_operation_or_missing_authorization(manager):
    m,a=manager;s=start(m,a)
    for kwargs in (dict(user_id='admin',authorize=lambda:None),dict(user_id='user',authorize=None),
                   dict(user_id='user',authorize=lambda:None,expected_operation_id='wrong')):
        with pytest.raises(LoginV2Error):
            m.claim_active(a['id'],app_session_id='new',owner_token='y'*32,**kwargs)
    assert m.sessions[s['id']].owner_hash==digest('x'*32)


def test_claim_rebind_failure_or_retired_limit_leaves_live_controller_untouched(manager,monkeypatch):
    m,a=manager;s=start(m,a)
    live=m.sessions[s['id']];before=(live.owner_hash,live.app_session_id,live.epoch,live.deadline,live.process)
    original=m.operations.rebind_active_owner
    def fail(*args,**kwargs):raise ValueError('SESSION_OWNER_MISMATCH')
    monkeypatch.setattr(m.operations,'rebind_active_owner',fail)
    with pytest.raises(ValueError):
        m.claim_active(a['id'],user_id='user',app_session_id='new',owner_token='y'*32,authorize=lambda:None)
    assert (live.owner_hash,live.app_session_id,live.epoch,live.deadline,live.process)==before
    monkeypatch.setattr(m.operations,'rebind_active_owner',original)
    live.retired_owner_hashes={digest('retired-'+str(index)) for index in range(128)}
    with pytest.raises(LoginV2Error) as exc:
        m.claim_active(a['id'],user_id='user',app_session_id='new',owner_token='y'*32,authorize=lambda:None)
    assert exc.value.code=='SESSION_CLAIM_LIMIT'
    assert (live.owner_hash,live.app_session_id,live.epoch,live.deadline,live.process)==before


def test_old_owner_idempotency_key_cannot_rebind_durable_operation_after_claim(manager):
    m,a=manager;s=start(m,a)
    old=m.operations.get(s['operation_id'])
    m.claim_active(a['id'],user_id='user',app_session_id='new',owner_token='y'*32,authorize=lambda:None)
    with pytest.raises(ValueError,match='SESSION_OWNER_MISMATCH'):
        m.operations.begin(account_id=a['id'],owner_user_id='user',application_session_id=old['application_session_id'],
            owner_token_hash=old['owner_token_hash'],request_key_hash=old['request_key_hash'],
            request_fingerprint=old['request_fingerprint'],intent=old['intent'])
    assert m.operations.get(s['operation_id'])['application_session_id']=='new'


def test_claim_between_validated_command_and_first_write_rejects_old_frame(manager,monkeypatch):
    import threading
    from backend.audit_agent import crawler_login_v2_manager as module
    m,a=manager
    validated=threading.Event();at_readiness=threading.Event();release=threading.Event()
    target_thread=[None];target_fd=[None];captured=[]
    real_selector=module.selectors.DefaultSelector
    real_write=module.os.write
    class Selector:
        def __init__(self):self.inner=real_selector()
        def __getattr__(self,name):return getattr(self.inner,name)
        def select(self,timeout=None):
            if threading.get_ident()==target_thread[0] and validated.is_set() and not at_readiness.is_set():
                at_readiness.set()
                assert release.wait(3)
            return self.inner.select(timeout)
    original_validate=m._validate_runtime
    def validate(session,command,*args,**kwargs):
        result=original_validate(session,command,*args,**kwargs)
        if command['command_id']=='cmd-77':
            target_thread[0]=threading.get_ident();validated.set()
        return result
    def write(fd,data):
        result=real_write(fd,data)
        if fd==target_fd[0]:captured.append(bytes(data[:result]))
        return result
    monkeypatch.setattr(module.selectors,'DefaultSelector',Selector)
    monkeypatch.setattr(module.os,'write',write)
    monkeypatch.setattr(m,'_validate_runtime',validate)
    s=start(m,a);wait_until(lambda:m.sessions[s['id']].snapshot['phase']=='sms_required')
    old_epoch=attach(m,s)
    live=m.sessions[s['id']];target_fd[0]=live.process.stdin.fileno()
    old=command(m,s,old_epoch,seq=77)
    m.command(s['id'],old,user_id='user',app_session_id='app',owner_token='x'*32)
    try:
        assert at_readiness.wait(3)
        # The first write has not happened. Claim must complete before this
        # selector barrier is released, rather than waiting for a platform call.
        claimed,_=m.claim_active(a['id'],user_id='user',app_session_id='new-app',owner_token='y'*32,authorize=lambda:None)
        assert claimed['controller_epoch']>old_epoch
        release.set()
        wait_until(lambda:any(b'controller_authorized' in chunk for chunk in captured))
        frames=[json.loads(line) for line in b''.join(captured).splitlines()]
        assert frames[0]=={'type':'controller_authorized','controller_epoch':claimed['controller_epoch'],'attached':False}
        assert not any(frame.get('command',{}).get('command_id')=='cmd-77' for frame in frames)
        wait_until(lambda:m.operations.get_command(live.operation_id,'cmd-77')['execution_status']=='not_executed')
        assert live.process.poll() is None
    finally:
        release.set()


def test_claim_after_partial_write_finishes_one_bounded_frame_before_new_controller(manager,monkeypatch):
    import threading
    from backend.audit_agent import crawler_login_v2_manager as module
    m,a=manager
    written=threading.Event();paused=threading.Event();release=threading.Event()
    target_fd=[None];target_thread=[None];chunks=[]
    real_selector=module.selectors.DefaultSelector;real_write=module.os.write
    class Selector:
        def __init__(self):self.inner=real_selector()
        def __getattr__(self,name):return getattr(self.inner,name)
        def select(self,timeout=None):
            if threading.get_ident()==target_thread[0] and written.is_set() and not paused.is_set():
                paused.set();assert release.wait(3)
            return self.inner.select(timeout)
    def write(fd,data):
        if fd==target_fd[0]:
            target_thread[0]=threading.get_ident()
            prefix=data[:8] if not written.is_set() else data
            result=real_write(fd,prefix);chunks.append(bytes(prefix[:result]));written.set()
            return result
        return real_write(fd,data)
    monkeypatch.setattr(module.selectors,'DefaultSelector',Selector)
    monkeypatch.setattr(module.os,'write',write)
    s=start(m,a);wait_until(lambda:m.sessions[s['id']].snapshot['phase']=='sms_required')
    epoch=attach(m,s);live=m.sessions[s['id']];target_fd[0]=live.process.stdin.fileno()
    old=command(m,s,epoch,seq=78)
    m.command(s['id'],old,user_id='user',app_session_id='app',owner_token='x'*32)
    try:
        assert paused.wait(3)
        claimed,_=m.claim_active(a['id'],user_id='user',app_session_id='new-app',owner_token='y'*32,authorize=lambda:None)
        release.set()
        wait_until(lambda:b''.join(chunks).count(b'\n')>=2)
        frames=[json.loads(line) for line in b''.join(chunks).splitlines()]
        assert frames[0]['command']['command_id']=='cmd-78'
        assert frames[1]=={'type':'controller_authorized','controller_epoch':claimed['controller_epoch'],'attached':False}
        assert m.operations.get_command(live.operation_id,'cmd-78')['execution_status'] in {'dispatched','executed'}
    finally:
        release.set()


@pytest.mark.parametrize('failure',['socketpair','route_pipe','diagnostic','child_environment','authority','process_options'])
def test_pre_spawn_setup_failure_releases_route_and_handles_then_next_start_succeeds(manager,monkeypatch,failure):
    from backend.audit_agent import crawler_login_v2_manager as module,crawler_child_runtime,crawler_route
    m,a=manager
    handles=[];bundles=[];pairs=[];diagnostics=[];pipes=[];authorities=[];launches=[]
    real_temp=module.tempfile.TemporaryFile
    real_pair=module.socket.socketpair
    real_pipe=crawler_route.RouteConfigPipe
    real_env=crawler_child_runtime.child_environment
    real_options=crawler_child_runtime.child_process_options
    real_authority=crawler_child_runtime.authority_for
    real_acquire=crawler_route.acquire_route_execution
    real_spawn=m.process_factory
    def leases(account_id):
        item=real_temp();handles.append(item);return [item]
    def acquire(*args,**kwargs):
        item=real_acquire(*args,**kwargs);item.closed=False
        item.close=lambda:setattr(item,'closed',True)
        bundles.append(item);return item
    def pair():
        result=real_pair();pairs.extend(result);return result
    def diagnostic(*args,**kwargs):
        result=real_temp(*args,**kwargs);diagnostics.append(result);return result
    def pipe(*args,**kwargs):
        result=real_pipe(*args,**kwargs);pipes.append(result);return result
    def authority(*args,**kwargs):
        result=real_authority(*args,**kwargs);result.closed=False
        result.close=lambda:setattr(result,'closed',True)
        authorities.append(result);return result
    def spawn(*args,**kwargs):
        result=real_spawn(*args,**kwargs);launches.append(result);return result
    monkeypatch.setattr(m,'lease_factory',leases)
    monkeypatch.setattr(m,'process_factory',spawn)
    monkeypatch.setattr(crawler_route,'acquire_route_execution',acquire)
    monkeypatch.setattr(module.socket,'socketpair',pair)
    monkeypatch.setattr(module.tempfile,'TemporaryFile',diagnostic)
    monkeypatch.setattr(crawler_route,'RouteConfigPipe',pipe)
    monkeypatch.setattr(crawler_child_runtime,'authority_for',authority)
    targets={'socketpair':(module.socket,'socketpair'),'route_pipe':(crawler_route,'RouteConfigPipe'),
        'diagnostic':(module.tempfile,'TemporaryFile'),'child_environment':(crawler_child_runtime,'child_environment'),
        'authority':(crawler_child_runtime,'authority_for'),'process_options':(crawler_child_runtime,'child_process_options')}
    target,name=targets[failure]
    def fail(*args,**kwargs):raise AttributeError('fixture missing config field')
    with monkeypatch.context() as injected:
        injected.setattr(target,name,fail)
        with pytest.raises(AttributeError,match='missing config field'):
            start(m,a,key='setup-failure')
    assert not launches and not m.sessions
    assert handles and all(item.closed for item in handles)
    assert bundles and all(item.closed for item in bundles)
    assert all(item.fileno()==-1 for item in pairs)
    assert all(item.closed for item in diagnostics)
    assert all(item.closed for item in authorities)
    operation=m.operations.list(account_id=a['id'])[0]
    assert operation['stage']=='failed' and operation['error_code']=='BROWSER_START_FAILED'
    with pytest.raises(LoginV2Error) as missing:
        m.claim_active(a['id'],user_id='user',app_session_id='new',owner_token='y'*32,authorize=lambda:None)
    assert missing.value.code=='ACTIVE_LOGIN_NOT_FOUND'
    snapshot=start(m,a,key='normal-after-failure')
    wait_until(lambda:m.sessions[snapshot['id']].snapshot['phase']=='sms_required')
    assert len(launches)==1 and launches[0].poll() is None


def test_child_environment_failure_releases_real_account_proxy_exit_and_admission_fences(manager,monkeypatch):
    from backend.audit_agent import crawler_child_runtime,crawler_route
    from backend.task_admission import resources
    from backend.audit_agent.config import settings
    m,a=manager
    c=crawler_route.route_contract()
    bundles=[]
    m.store.observe_shared_exit('198.51.100.17',service_exit=True)
    def leases(account_id):
        handle=resources.acquire_account_handle('dy',account_id)
        assert handle is not None
        return [handle]
    def acquire(account_id,execution_id,owner_id,*,account_handle,**kwargs):
        snapshot=c.BoundRoute('dy',account_id,c.derive_profile_key(account_id),'fixture-proxy',1,1,
            'proxy.invalid',8080,'','',time.time()+300,'198.51.100.17',time.time(),
            str(settings.request_scheduler_db),c.derive_scope_ids('dy',account_id,'fixture-proxy','198.51.100.17'))
        bundle=resources.ExecutionLeaseBundle(execution_id,owner_id,account_id,snapshot)
        assert bundle.acquire(account_handle=account_handle,max_active_accounts=1)
        assert bundle.acquire_exit_capacity('198.51.100.17')
        token=m.store.admit_route(account_id,'198.51.100.17',1,execution_id,login_bootstrap=True)
        bundle.add_closer(lambda:m.store.release_route_admission(token))
        bundles.append(bundle)
        return bundle
    monkeypatch.setattr(m,'lease_factory',leases)
    monkeypatch.setattr(crawler_route,'acquire_route_execution',acquire)
    original_environment=crawler_child_runtime.child_environment
    def fail():raise AttributeError('missing configured field')
    monkeypatch.setattr(crawler_child_runtime,'child_environment',fail)
    with pytest.raises(AttributeError):
        start(m,a,key='real-fence-failure')
    assert not m.sessions
    with m.store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM crawler_route_admissions WHERE state='active'").fetchone()[0]==0
    free=resources.acquire_account_handle('dy',a['id'])
    assert free is not None
    free.close()
    with resources.proxy_edit_lease('fixture-proxy') as editable:
        assert editable
    # The next actual helper start must acquire the same account, proxy active
    # slot and exit slot. This proves cleanup, not just the absence of a process.
    monkeypatch.setattr(crawler_child_runtime,'child_environment',original_environment)
    snapshot=start(m,a,key='real-fence-success')
    wait_until(lambda:m.sessions[snapshot['id']].snapshot['phase']=='sms_required')
    assert m.sessions[snapshot['id']].process.poll() is None
    assert len(bundles)==2
