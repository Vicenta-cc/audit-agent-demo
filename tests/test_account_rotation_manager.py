import pytest
from cryptography.fernet import Fernet

from backend.audit_agent.account_rotation import AccountRotationManager
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore


@pytest.mark.parametrize("reason", ["empty_search", "verify"])
def test_rotation_respects_reason(tmp_path, reason):
    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    cipher = AuthStateCipher(encryption_key=Fernet.generate_key())
    first = store.create(platform="dy", display_name="a")
    second = store.create(platform="dy", display_name="b")
    store.save_auth_state(first["id"], cipher.encrypt({"cookies": [], "origins": []}))
    store.save_auth_state(second["id"], cipher.encrypt({"cookies": [], "origins": []}))
    with store._connect() as db:
        db.execute("UPDATE crawler_accounts SET auth_source_generation=profile_generation")
    rotated = AccountRotationManager(store, cipher).rotate("dy", first["id"], reason=reason, cooldown_seconds=60)
    if reason == "verify":
        assert rotated is None
        assert store.get(first["id"])["risk_count"] == 1
        return
    assert rotated[0]["id"] == second["id"]
    assert rotated[1]["cookies"] == []
    assert store.get(first["id"])["failure_kind"] == "empty_search"
