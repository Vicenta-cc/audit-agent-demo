"""Host-local execution and account leases backed by inherited file locks.

A lease expires only when all owners (including crawler children) close their
file descriptors. Database timestamps describe state; they never unlock a live
process. Use one configured runtime on a local filesystem, not across hosts.
"""

from __future__ import annotations

import fcntl
import hashlib
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path

from backend.audit_agent.config import settings

_assigned = ContextVar("assigned_crawler_account", default="")
_held = ContextVar("task_resource_handles", default=())


class ResourceBusy(RuntimeError):
    pass


def lock_root():
    return Path(settings.task_resource_lock_dir).expanduser().resolve()


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def inherited_fds():
    return tuple(handle.fileno() for _, handle in _held.get() if not handle.closed)


@contextmanager
def file_lease(path, *, reentrant=False):
    path = Path(path).resolve()
    if reentrant and any(key == path for key, _ in _held.get()):
        yield True
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        marker = _held.set((*_held.get(), (path, handle)))
        try:
            yield True
        finally:
            _held.reset(marker)
            # Never LOCK_UN: a surviving child must retain this same lease.


def account_lock_path(platform, account_id):
    # Same profile root -> same resource identity, even for another checkout.
    root = Path(settings.crawler_browser_profile_root).expanduser().resolve()
    # Non-Douyin adapters still share browser_data/CDP: conservatively serialize
    # by platform until their profile implementation is also account-bound.
    key = f"{platform}:{account_id}" if platform == "dy" else "legacy:shared-browser"
    return root / ".resource-locks" / (digest(key) + ".lock")


@contextmanager
def account_lease(platform, account_id):
    with file_lease(
        account_lock_path(platform, account_id), reentrant=True
    ) as acquired:
        yield acquired


@contextmanager
def capacity_lease(kind, limit):
    for slot in range(max(1, int(limit))):
        with file_lease(lock_root() / f"{kind}-{slot}.lock") as acquired:
            if acquired:
                yield True
                return
    yield False


@contextmanager
def analysis_capacity(stop_checker=None):
    while True:
        if stop_checker and stop_checker():
            raise ResourceBusy("任务已停止，取消等待分析容量。")
        with capacity_lease("analysis", settings.task_analysis_capacity) as acquired:
            if acquired:
                yield
                return
        time.sleep(0.1)


@contextmanager
def assigned_account(account_id):
    token = _assigned.set(account_id)
    try:
        yield
    finally:
        _assigned.reset(token)


def selected_account():
    return _assigned.get()


def acquire_account_handle(platform, account_id):
    """Non-contextual handle for login processes consumed by another thread."""
    path = account_lock_path(platform, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


@contextmanager
def report_capacity(job_id):
    if settings.app_auth_mode != "required":
        yield
        return
    from .execution import assert_execution

    def check():
        assert_execution(job_id)
        return False

    with analysis_capacity(check):
        yield


def proxy_lock_path(proxy_id):
    return lock_root() / 'proxy' / (digest(proxy_id) + '.lock')


def _open_lock(path, mode=fcntl.LOCK_EX):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    try:
        fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


@contextmanager
def proxy_edit_lease(proxy_id):
    # Reentrancy applies only to a proven exclusive descriptor in this thread.
    # Shared execution descriptors are never recorded in _held.
    with file_lease(proxy_lock_path(proxy_id), reentrant=True) as acquired:
        yield acquired


@contextmanager
def proxy_mutation_guard(operation, proxy_id, account_id=None, *, account_store=None):
    from backend.audit_agent.crawler_route import CollectionRouteError
    with ExitStack() as stack:
        if account_id and not stack.enter_context(account_lease('dy', account_id)):
            raise CollectionRouteError('PROXY_BUSY')
        proxy_ids = {proxy_id} if proxy_id else set()
        if account_id:
            from backend.audit_agent.crawler_route import get_proxy_store
            account = (account_store or get_proxy_store().accounts).get(account_id)
            if account and account.get("proxy_id"):
                proxy_ids.add(account["proxy_id"])
        for resource_id in sorted(proxy_ids):
            if not stack.enter_context(proxy_edit_lease(resource_id)):
                raise CollectionRouteError("PROXY_BUSY")
        yield


class ExecutionLeaseBundle:
    """Explicit thread-safe resource ownership; children retain inherited locks.

    Closing the owner forbids new borrowers. Existing borrowers keep owned FDs
    alive. An outer account descriptor is duplicated, never unlocked or closed.
    """
    def __init__(self, execution_id, owner_id, account_id, snapshot=None):
        import threading
        self.execution_id, self.owner_id, self.account_id = execution_id, owner_id, account_id
        self.snapshot = snapshot
        self._lock = threading.RLock()
        self._handles = []
        self._borrowers = 0
        self._closing = False
        self._closers = []

    @property
    def pass_fds(self):
        with self._lock:
            return tuple(h.fileno() for h in self._handles if not h.closed)

    def acquire(self, *, account_handle=None, max_active_accounts=1):
        import os
        with self._lock:
            if self._handles or self._closing:
                raise ResourceBusy('PROXY_BUSY')
            if account_handle is None:
                path = account_lock_path('dy', self.account_id).resolve()
                account_handle = next((h for key, h in _held.get() if key == path and not h.closed), None)
            handle = os.fdopen(os.dup(account_handle.fileno()), 'a+b') if account_handle is not None else acquire_account_handle('dy', self.account_id)
            if handle is None:
                return False
            self._handles.append(handle)
            if self.snapshot is None:
                return True
            shared = _open_lock(proxy_lock_path(self.snapshot.proxy_id), fcntl.LOCK_SH)
            if shared is not None:
                self._handles.append(shared)
                for slot in range(max(1, int(max_active_accounts))):
                    active = _open_lock(lock_root() / 'proxy' / (digest(self.snapshot.proxy_id) + f'-active-{slot}.lock'))
                    if active is not None:
                        self._handles.append(active)
                        return True
            self._close_handles()
            return False

    def acquire_exit_capacity(self, observed_exit):
        """Actual exit capacity uses inherited FDs, never timestamp expiry.

        Call after observation and before business snapshot issuance. Independent
        proxy resources and direct service routes use the same physical-exit key.
        """
        import ipaddress
        try:
            key=str(ipaddress.ip_address(observed_exit))
        except (TypeError,ValueError):
            raise ResourceBusy('PROXY_CONFIG_INVALID') from None
        with self._lock:
            if self._closing or not self._handles:
                raise ResourceBusy('PROXY_BUSY')
            if getattr(self,'_exit_capacity_key',None)==key:
                return True
            if getattr(self,'_exit_capacity_key',None) is not None:
                raise ResourceBusy('PROXY_EXIT_CHANGED')
            for slot in range(settings.crawler_shared_exit_active_limit):
                handle=_open_lock(lock_root()/'exit'/(digest(key)+f'-active-{slot}.lock'))
                if handle is not None:
                    self._handles.append(handle)
                    self._exit_capacity_key=key
                    return True
            return False

    @contextmanager
    def borrow(self, *, execution_id=None, owner_id=None, account_id=None):
        with self._lock:
            if self._closing or not self._handles or any(value is not None and value != actual for value, actual in ((execution_id, self.execution_id), (owner_id, self.owner_id), (account_id, self.account_id))):
                raise ResourceBusy('PROXY_BUSY')
            self._borrowers += 1
        token = BorrowedRouteContext(self)
        try:
            yield token
        finally:
            token._active = False
            with self._lock:
                self._borrowers -= 1
                if self._closing and not self._borrowers:
                    self._close_handles()

    def add_closer(self, callback):
        with self._lock:
            if self._closing:
                raise ResourceBusy("PROXY_BUSY")
            self._closers.append(callback)

    def _close_handles(self):
        # A failed child/context drain retains the remaining callbacks and FDs;
        # it must not run the admission-release callback or free the Profile.
        while self._closers:
            callback=self._closers[-1]
            callback()
            self._closers.pop()
        for handle in reversed(self._handles):
            handle.close()
        self._handles.clear()

    def close(self):
        with self._lock:
            self._closing = True
            if not self._borrowers:
                self._close_handles()


class BorrowedRouteContext:
    """Transferable live borrow; nested use can finish after owner closes."""
    def __init__(self, bundle):
        object.__setattr__(self, '_bundle', bundle)
        object.__setattr__(self, '_active', True)

    def __getattr__(self, name):
        return getattr(self._bundle, name)

    def __setattr__(self, name, value):
        if name in {'_bundle', '_active'}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._bundle, name, value)

    @contextmanager
    def borrow(self, *, execution_id=None, owner_id=None, account_id=None):
        bundle = self._bundle
        with bundle._lock:
            if not self._active or any(value is not None and value != actual for value, actual in ((execution_id, bundle.execution_id), (owner_id, bundle.owner_id), (account_id, bundle.account_id))):
                raise ResourceBusy('PROXY_BUSY')
            bundle._borrowers += 1
        token = BorrowedRouteContext(bundle)
        try:
            yield token
        finally:
            token._active = False
            with bundle._lock:
                bundle._borrowers -= 1
                if bundle._closing and not bundle._borrowers:
                    bundle._close_handles()

    def add_closer(self, callback):
        with self._bundle._lock:
            if not self._active:
                raise ResourceBusy('PROXY_BUSY')
            self._bundle._closers.append(callback)
