"""Host-local execution and account leases backed by inherited file locks.

A lease expires only when all owners (including crawler children) close their
file descriptors. Database timestamps describe state; they never unlock a live
process. Use one configured runtime on a local filesystem, not across hosts.
"""

from __future__ import annotations

import fcntl
import hashlib
import time
from contextlib import contextmanager
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
