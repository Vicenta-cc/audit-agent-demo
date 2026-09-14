from __future__ import annotations

from datetime import datetime, timedelta

from .auth_state_cipher import AuthStateCipher, auth_state_cipher
from .crawler_account_store import CrawlerAccountStore, crawler_account_store


class AccountRotationManager:
    """Bounded account rotation primitive used by the crawler execution layer."""

    def __init__(self, store: CrawlerAccountStore | None = None, cipher: AuthStateCipher | None = None):
        self.store = store or crawler_account_store
        self.cipher = cipher or auth_state_cipher

    def rotate(self, platform: str, current_account_id: str, *, reason: str, cooldown_seconds: float = 300.0) -> tuple[dict, dict] | None:
        until = (datetime.now() + timedelta(seconds=max(0.0, cooldown_seconds))).isoformat(timespec="seconds")
        self.store.mark_cooldown(current_account_id, reason, until, failure_kind=reason)
        account = self.store.next_available(platform, exclude_id=current_account_id)
        if not account:
            return None
        auth = self.cipher.decrypt(self.store.get_auth_state_ciphertext(account["id"]))
        return account, auth
