from __future__ import annotations

from datetime import datetime, timedelta

from .auth_state_cipher import AuthStateCipher, auth_state_cipher
from .crawler_account_store import CrawlerAccountStore, crawler_account_store
from .crawler_adapter import CrawlerVerificationError


class AccountRotationManager:
    """Bounded account rotation primitive used by the crawler execution layer."""

    def __init__(self, store: CrawlerAccountStore | None = None, cipher: AuthStateCipher | None = None):
        self.store = store or crawler_account_store
        self.cipher = cipher or auth_state_cipher

    def cool_down(self, account_id: str, *, reason: str, cooldown_seconds: float = 300.0) -> None:
        until = (datetime.now() + timedelta(seconds=max(0.0, cooldown_seconds))).isoformat(timespec="seconds")
        self.store.mark_cooldown(account_id, reason, until, failure_kind=reason)

    def rotate(self, platform: str, current_account_id: str, *, reason: str, cooldown_seconds: float = 300.0) -> tuple[dict, dict] | None:
        self.cool_down(current_account_id, reason=reason, cooldown_seconds=cooldown_seconds)
        account = self.store.next_available(platform, exclude_id=current_account_id)
        if not account:
            return None
        auth = self.cipher.decrypt(self.store.get_auth_state_ciphertext(account["id"]))
        return account, auth

    def run_with_rotation(self, platform: str, account_id: str, auth_state: dict, runner, *, max_switches: int = 1):
        """Run a bounded operation, retrying once with a rotated account on verify."""
        current_id, current_auth = account_id, auth_state
        for attempt in range(max(0, int(max_switches)) + 1):
            try:
                return runner(current_id, current_auth)
            except CrawlerVerificationError:
                if attempt >= max_switches:
                    raise
                rotated = self.rotate(platform, current_id, reason="verify")
                if not rotated:
                    raise
                current_id, current_auth = rotated[0]["id"], rotated[1]
        raise RuntimeError("account rotation exhausted")
