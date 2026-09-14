from cryptography.fernet import Fernet

from backend.audit_agent.account_rotation import AccountRotationManager
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore


def test_rotation_cools_current_and_returns_decrypted_next_account(tmp_path):
    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    cipher = AuthStateCipher(encryption_key=Fernet.generate_key())
    first = store.create(platform="dy", display_name="a")
    second = store.create(platform="dy", display_name="b")
    store.save_auth_state(first["id"], cipher.encrypt({"cookies": [], "origins": []}))
    store.save_auth_state(second["id"], cipher.encrypt({"cookies": [], "origins": []}))
    rotated = AccountRotationManager(store, cipher).rotate("dy", first["id"], reason="verify", cooldown_seconds=60)
    assert rotated[0]["id"] == second["id"]
    assert rotated[1]["cookies"] == []
    assert store.get(first["id"])["failure_kind"] == "verify"
