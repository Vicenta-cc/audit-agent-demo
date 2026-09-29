from __future__ import annotations

from datetime import datetime, timedelta

from .auth_state_cipher import AuthStateCipher, auth_state_cipher
from .config import settings
from .crawler_account_store import CrawlerAccountStore, crawler_account_store
from .crawler_adapter import CrawlerVerificationError


def risk_cooldown_seconds(risk_count: int) -> float:
    """Escalating cooldown: base × 2^(count-1), capped at the configured maximum."""
    exponent = max(0, int(risk_count) - 1)
    base = settings.crawler_risk_cooldown_base_seconds
    ceiling = settings.crawler_risk_cooldown_max_seconds
    return min(ceiling, base * 2 ** min(exponent, 32))


class AccountRotationManager:
    """Bounded account rotation primitive used by the crawler execution layer."""

    def __init__(self, store: CrawlerAccountStore | None = None, cipher: AuthStateCipher | None = None):
        self.store = store or crawler_account_store
        self.cipher = cipher or auth_state_cipher

    def cool_down(self, account_id: str, *, reason: str, cooldown_seconds: float = 300.0) -> None:
        until = (datetime.now() + timedelta(seconds=max(0.0, cooldown_seconds))).isoformat(timespec="seconds")
        self.store.mark_cooldown(account_id, reason, until, failure_kind=reason)

    def cool_down_after_risk(self, account_id: str, *, reason: str = "verify") -> float:
        """Record a verification/silent-risk signal and apply the escalated cooldown."""
        seconds = risk_cooldown_seconds(self.store.record_risk(account_id))
        self.cool_down(account_id, reason=reason, cooldown_seconds=seconds)
        return seconds

    def rotate(self, platform: str, current_account_id: str, *, reason: str, cooldown_seconds: float = 300.0) -> tuple[dict, dict] | None:
        self.cool_down(current_account_id, reason=reason, cooldown_seconds=cooldown_seconds)
        account = self.store.next_available(platform, exclude_id=current_account_id)
        if not account:
            return None
        auth = self.cipher.decrypt(self.store.get_auth_state_ciphertext(account["id"]))
        return account, auth

    def run_with_rotation(self, platform: str, account_id: str, auth_state: dict, runner, *, max_switches: int | None = None):
        """Try each currently eligible account at most once on verification.

        ``max_switches`` remains available for narrow callers, while the normal
        path consumes the whole eligible pool without ever looping an account.
        """
        current_id, current_auth = account_id, auth_state
        attempted: set[str] = set()
        switches = 0
        while current_id and current_id not in attempted:
            attempted.add(current_id)
            try:
                return runner(current_id, current_auth)
            except CrawlerVerificationError:
                self.cool_down(current_id, reason="verify")
                if max_switches is not None and switches >= max(0, int(max_switches)):
                    raise
                accounts = self.store.available_accounts(platform, exclude_ids=attempted)
                if not accounts:
                    raise
                account = accounts[0]
                current_id = account["id"]
                current_auth = self.cipher.decrypt(
                    self.store.get_auth_state_ciphertext(current_id)
                )
                switches += 1
        raise RuntimeError("account rotation exhausted")
