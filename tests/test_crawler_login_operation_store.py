import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_login_operation_store import CrawlerLoginOperationStore


@pytest.fixture
def stores(tmp_path):
    accounts = CrawlerAccountStore(tmp_path / "accounts.db")
    cipher = AuthStateCipher("test-key")
    operations = CrawlerLoginOperationStore(accounts, cipher)
    account = accounts.create(platform="dy", display_name="test")
    return accounts, operations, account, cipher


def begin(stores, key="one", **kwargs):
    _, operations, account, _ = stores
    return operations.begin(account_id=account["id"], owner_user_id="user", application_session_id="session",
        owner_token_hash="owner", request_key_hash=key, request_fingerprint=key, **kwargs)


def test_risk_preserving_save_and_reset_revision(stores):
    accounts, operations, account, cipher = stores
    accounts.mark_cooldown(account["id"], "risk message", "2099-01-01", failure_kind="verify")
    original = accounts.get(account["id"])
    op = begin(stores)
    reset = operations.authorize_reset(op["id"])
    assert reset["expected_auth_revision"] == original["auth_state_revision"] + 1
    operations.mark_stage(op["id"], "profile_ready", profile_authority_established=1)
    committed = operations.commit_auth(op["id"], cipher.encrypt({"cookies": []}), "identity")
    saved = committed["result"]["account"]
    assert saved["failure_kind"] == "verify"
    assert saved["last_error"] == "risk message"
    assert saved["cooldown_until"] == "2099-01-01"
    assert saved["auth_state_revision"] == reset["expected_auth_revision"] + 1
    assert not accounts.available_accounts("dy")
    assert operations.mark_stage(op["id"], "cancelled")["stage"] == "committed"


def test_source_recovered_and_atomic_rollback(stores, monkeypatch):
    accounts, operations, account, cipher = stores
    source = begin(stores)
    operations.mark_stage(source["id"], "recovery_required", profile_authority_established=1)
    recovery = begin(stores, "recovery", intent="recover", source_operation_id=source["id"])
    original = accounts.save_douyin_auth_state
    def failing(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fail after account write")
    monkeypatch.setattr(accounts, "save_douyin_auth_state", failing)
    with pytest.raises(RuntimeError):
        operations.commit_auth(recovery["id"], cipher.encrypt({}), "identity")
    assert accounts.get(account["id"])["auth_state_revision"] == 0
    assert operations.get(source["id"])["stage"] == "recovery_required"
    monkeypatch.setattr(accounts, "save_douyin_auth_state", original)
    completed = operations.commit_auth(recovery["id"], cipher.encrypt({}), "identity")
    assert operations.get(source["id"])["recovered_by_operation_id"] == completed["id"]
    assert operations.get(source["id"])["result"] == completed["result"]


def test_new_credentials_block_old_recovery(stores):
    accounts, operations, account, cipher = stores
    source = begin(stores)
    operations.mark_stage(source["id"], "recovery_required")
    accounts.save_auth_state(account["id"], cipher.encrypt({}), "identity")
    with pytest.raises(ValueError, match="RECOVERY_SOURCE_SUPERSEDED"):
        begin(stores, "recovery", intent="recover", source_operation_id=source["id"])
    assert operations.get(source["id"])["stage"] == "superseded"


def test_commands_no_replay_or_sensitive_plaintext(stores):
    accounts, operations, _, _ = stores
    op = begin(stores)
    payload = {"phone": "13912345678", "code": "917325"}
    def record():
        return operations.record_command(op["id"], "sms", "submit_sms", payload,
            application_session_id="session", controller_epoch=1, sequence=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: record(), range(8)))
    assert all(v["execution_status"] == "accepted" for v in values)
    operations.dispatch_command(op["id"], "sms")
    operations.interrupt_commands(op["id"])
    assert record()["execution_status"] == "unknown"
    with pytest.raises(ValueError, match="COMMAND_NOT_DISPATCHABLE"):
        operations.dispatch_command(op["id"], "sms")
    with pytest.raises(ValueError, match="COMMAND_ID_CONFLICT"):
        operations.record_command(op["id"], "sms", "submit_sms", {"code":"different"},
            application_session_id="session", controller_epoch=1, sequence=2)
    raw = accounts.db_path.read_bytes()
    assert b"13912345678" not in raw and b"917325" not in raw
    operations.record_command(op["id"], "new", "refresh_qr", {}, application_session_id="session",controller_epoch=1,sequence=2)
    operations.interrupt_commands(op["id"])
    assert operations.get_command(op["id"], "new")["execution_status"] == "not_executed"


def test_key_rotation_query_without_reexecution(stores):
    accounts, operations, _, _ = stores
    op = begin(stores)
    operations.record_command(op["id"], "sms", "request_sms", {"phone":"123"}, application_session_id="session",controller_epoch=1,sequence=1)
    rotated = CrawlerLoginOperationStore(accounts, AuthStateCipher("new-key"))
    assert rotated.get_command(op["id"], "sms")
    with pytest.raises(ValueError, match="COMMAND_FINGERPRINT_UNAVAILABLE"):
        rotated.record_command(op["id"], "sms", "request_sms", {"phone":"123"}, application_session_id="session",controller_epoch=1,sequence=1)


def test_revision_only_relevant_mutations(stores):
    accounts, operations, account, cipher = stores
    aid = account["id"]
    accounts.update(aid, display_name="renamed")
    accounts.mark_cooldown(aid, "risk", "2099-01-01")
    assert accounts.get(aid)["auth_state_revision"] == 0
    ciphertext = cipher.encrypt({})
    accounts.save_auth_state(aid, ciphertext, "id")
    revision = accounts.get(aid)["auth_state_revision"]
    accounts.save_auth_state(aid, ciphertext, "id")
    assert accounts.get(aid)["auth_state_revision"] == revision
    accounts.update(aid, status="disabled")
    assert accounts.get(aid)["auth_state_revision"] == revision + 1
    accounts.update(aid, status="disabled")
    assert accounts.get(aid)["auth_state_revision"] == revision + 1


def test_check_keeps_auth_age_and_revision(stores):
    accounts, _, account, cipher = stores
    first = accounts.save_douyin_auth_state(account["id"], cipher.encrypt({"version":1}), "identity")
    checked = accounts.save_douyin_auth_state(account["id"], cipher.encrypt({"version":2}), "identity", credentials_changed=False)
    assert checked["auth_state_revision"] == first["auth_state_revision"]
    assert checked["auth_state_updated_at"] == first["auth_state_updated_at"]
    assert cipher.decrypt(accounts.get_auth_state_ciphertext(account["id"])) == {"version":1}


def test_recovery_owner_and_active_source(stores):
    accounts, operations, account, _ = stores
    source = begin(stores)
    with pytest.raises(ValueError, match="RECOVERY_SOURCE_NOT_RECOVERABLE"):
        begin(stores, "active", intent="recover", source_operation_id=source["id"])
    operations.mark_stage(source["id"], "recovery_required")
    with pytest.raises(ValueError, match="RECOVERY_SOURCE_FORBIDDEN"):
        operations.begin(account_id=account["id"], owner_user_id="other", application_session_id="new",owner_token_hash="new",request_key_hash="other",request_fingerprint="other",intent="recover",source_operation_id=source["id"])
    recovery = operations.begin(account_id=account["id"], owner_user_id="user", application_session_id="new",owner_token_hash="new",request_key_hash="new",request_fingerprint="new",intent="recover",source_operation_id=source["id"])
    assert recovery["application_session_id"] == "new"
    assert operations.get(source["id"])["application_session_id"] == "session"
    with pytest.raises(ValueError, match="ACCOUNT_BUSY"):
        begin(stores, "second", intent="recover", source_operation_id=source["id"])


def test_rejected_result_cannot_store_verification_material(stores):
    _, operations, _, _ = stores
    op = begin(stores)
    operations.record_command(op["id"], "sms", "request_sms", {},application_session_id="session",controller_epoch=1,sequence=1)
    with pytest.raises(ValueError, match="SENSITIVE_COMMAND_RESULT"):
        operations.finish_command(op["id"], "sms", result={"phone":"13912345678"})


def test_concurrent_dispatch_only_one_sender(stores):
    _, operations, _, _ = stores
    op = begin(stores)
    operations.record_command(op["id"], "sms", "request_sms", {},application_session_id="session",controller_epoch=1,sequence=1)
    def dispatch(_):
        try:
            operations.dispatch_command(op["id"], "sms")
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(dispatch,range(8))) == 1
