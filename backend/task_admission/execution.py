"""Phase-three execution fence; resource-level concurrency replaces it in phase four."""

import fcntl
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_current = ContextVar("admission_execution", default=None)
_inherited_fd = None


def crawler_fds():
    return (_inherited_fd,) if _inherited_fd is not None else ()


@contextmanager
def execution_lock(db_path):
    global _inherited_fd
    path = Path(db_path).with_suffix(".execution.lock")
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        _inherited_fd = handle.fileno()
        try:
            yield True
        finally:
            _inherited_fd = None
            # Close, don't LOCK_UN: a surviving crawler inherits this open file
            # description and must retain the fence until it actually exits.


@contextmanager
def execution_context(store, task_id, token):
    marker = _current.set((store, task_id, token))
    try:
        yield
    finally:
        _current.reset(marker)


def current_execution():
    return _current.get()


def assert_execution(job_id, execution=None):
    from .store import AdmissionError

    execution = execution or current_execution()
    if execution is None:
        raise AdmissionError("采集任务必须通过统一准入。", code="EXECUTION_FENCED")
    store, task_id, token = execution
    row = store.for_task(task_id)
    if (
        not row
        or row["job_id"] != job_id
        or row["execution_token"] != token
        or row["state"] != "RESERVED"
        or row["decision"] == "CANCELLED"
    ):
        raise AdmissionError("任务已结束或执行权失效。", code="EXECUTION_FENCED")
    with store.connect() as db:
        store.validate_user(db, row["owner_id"])
