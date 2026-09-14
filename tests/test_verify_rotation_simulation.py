from cryptography.fernet import Fernet

from backend.audit_agent.account_rotation import AccountRotationManager
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_adapter import CrawlerVerificationError


def test_simulated_verify_switches_and_retries_with_next_account(tmp_path):
    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    cipher = AuthStateCipher(encryption_key=Fernet.generate_key())
    first = store.create(platform="dy", display_name="first")
    second = store.create(platform="dy", display_name="second")
    store.save_auth_state(first["id"], cipher.encrypt({"cookies": [{"name": "a"}], "origins": []}))
    store.save_auth_state(second["id"], cipher.encrypt({"cookies": [{"name": "b"}], "origins": []}))
    calls = []

    def runner(account_id, auth):
        calls.append((account_id, auth["cookies"][0]["name"]))
        if len(calls) == 1:
            raise CrawlerVerificationError("simulated verify")
        return {"contents": ["post-1"], "account_id": account_id}

    manager = AccountRotationManager(store, cipher)
    result = manager.run_with_rotation(
        "dy", first["id"], {"cookies": [{"name": "a"}], "origins": []}, runner
    )

    assert result["contents"] == ["post-1"]
    assert calls == [(first["id"], "a"), (second["id"], "b")]
    assert store.get(first["id"])["failure_kind"] == "verify"
