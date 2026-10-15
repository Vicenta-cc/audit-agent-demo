from __future__ import annotations

from datetime import datetime, timedelta
import time

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
        account = self.store.get(account_id)
        if account and account['platform'] == 'dy' and settings.crawler_safety_enabled:
            self.store.pause_account_exit(account_id, until=time.time()+seconds)
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
            except CrawlerVerificationError as exc:
                if not getattr(exc, 'account_risk_recorded', False):
                    self.cool_down_after_risk(current_id, reason="verify")
                    exc.account_risk_recorded = True
                if max_switches is not None and switches >= max(0, int(max_switches)):
                    raise
                accounts = self.store.available_accounts(platform, exclude_ids=attempted)
                if not accounts:
                    raise
                account = min(accounts,key=self.store.fair_key) if platform=='dy' and settings.crawler_safety_enabled else accounts[0]
                self.store.record_fair_selection(account['id'],[item['id'] for item in accounts])
                current_id = account["id"]
                current_auth = self.cipher.decrypt(
                    self.store.get_auth_state_ciphertext(current_id)
                )
                switches += 1
        raise RuntimeError("account rotation exhausted")

    def recover_once(self, account_id, check):
        """No I/O until the incident's only attempt is durably claimed.

        Caller holds the account/Profile lease and provides one bounded low-rate
        operation. A crashed claim stays claimed, preventing automatic replay.
        """
        from backend.task_admission.resources import account_lease
        account=self.store.get(account_id)
        if not account or account['platform']!='dy': return None
        with account_lease('dy',account_id) as acquired:
            if not acquired: return None
            claim=self.store.claim_recovery(account_id)
            if claim is None: return None
            result='uncertain'
            try:
                outcome=check(account_id,claim)
                if outcome is True: result='success'
                elif outcome is False: result='challenge'
            except CrawlerVerificationError:
                result='challenge'
            except Exception:
                result='uncertain'
            self.store.finish_recovery(account_id,claim,result)
            return result

    def refresh_authorized(self, platform, owner_id, authorized_ids):
        """Dispatch-only, echo-only refresh; risk recovery has a separate claim."""
        if platform!='dy' or not settings.crawler_safety_enabled: return
        from backend.task_admission.resources import account_lease
        from .crawler_route import refresh_existing_route
        for account in self.store.stale_route_candidates(platform):
            if account['id'] not in authorized_ids: continue
            with account_lease(platform,account['id']) as acquired:
                if not acquired: continue
                claim=self.store.claim_exit_refresh(account['id'])
                if claim is None: continue
                success=False
                try:
                    success=refresh_existing_route(account['id'],account_store=self.store,owner_id=owner_id) is True
                except Exception:
                    success=False
                self.store.finish_exit_refresh(account['id'],claim,success)

    def recover_authorized(self, platform, owner_id, authorized_ids):
        """Explicit dispatch work, never invoked by construction or listing."""
        if platform != 'dy' or not settings.crawler_safety_enabled:
            return
        from .crawler_route import check_recovery_route
        for account in self.store.list(platform=platform):
            if account['id'] in authorized_ids and account.get('recovery_state')=='manual_verified_waiting':
                self.store.release_verified_manual(account['id'])
                continue
            if account['id'] not in authorized_ids or account.get('recovery_state') != 'pending':
                continue
            self.recover_once(account['id'],lambda account_id,claim: check_recovery_route(
                account_id,claim,account_store=self.store,owner_id=owner_id))

    def healthy_capacity_available(self, platform, owner_id, authorized_ids):
        """Inspect the same inherited account/proxy/physical-exit capacity FDs.

        This bounded dry reservation performs no probe and issues no admission.
        Platform request cooldown/budget remains enforced by the request gate.
        """
        from types import SimpleNamespace
        from backend.task_admission.resources import ExecutionLeaseBundle, account_lease
        for account in self.store.available_accounts(platform):
            if account['id'] not in authorized_ids: continue
            with account_lease(platform,account['id']) as acquired:
                if not acquired: continue
                key=account.get('shared_exit_key')
                limit=1
                proxy_id=account.get('proxy_id')
                if account.get('route_mode')=='direct_degraded':
                    proxy_id='service-direct'
                elif proxy_id:
                    with self.store._connect() as db:
                        resource=db.execute('SELECT shared_exit_key,max_active_accounts FROM proxy_resources WHERE id=?',(proxy_id,)).fetchone()
                    if not resource: continue
                    key,limit=resource['shared_exit_key'],resource['max_active_accounts']
                bundle=ExecutionLeaseBundle('capacity-check',owner_id,account['id'],SimpleNamespace(proxy_id=proxy_id) if proxy_id else None)
                try:
                    if not bundle.acquire(max_active_accounts=min(limit,max(1,settings.request_concurrency))): continue
                    if key and not bundle.acquire_exit_capacity(key): continue
                    return True
                finally:
                    bundle.close()
        return False

    def prepare_exhausted_pool(self, platform, owner_id, authorized_ids):
        """Healthy capacity wins before a drained transport account may degrade.

        Profile locks and zero active durable admissions prove old execution
        drained; no timestamp or process-local lock substitutes for that proof.
        """
        if platform != 'dy' or not settings.crawler_safety_enabled:
            return None
        if self.healthy_capacity_available(platform,owner_id,authorized_ids):
            return None
        from backend.task_admission.resources import account_lease
        from .crawler_route import prepare_direct_degraded
        for candidate in self.store.degraded_candidates(platform):
            if candidate['id'] not in authorized_ids: continue
            with account_lease(platform,candidate['id']) as acquired:
                if not acquired: continue
                if prepare_direct_degraded(candidate['id'],candidate['transport_error'],True,
                        account_store=self.store,old_context_drained=True,owner_id=owner_id):
                    return self.store.get(candidate['id'])
        return None
