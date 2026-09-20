"""Per-task execution identity and inherited stop-proof fences."""

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .resources import digest, file_lease, inherited_fds

_current = ContextVar("admission_execution", default=None)


def crawler_fds():
    return inherited_fds()


@contextmanager
def execution_lock(db_path, task_id=None):
    root = Path(db_path).resolve()
    path = (
        root.parent / (root.stem + ".task-locks") / (digest(task_id) + ".lock")
        if task_id is not None
        else root.with_suffix(".execution.lock")
    )
    with file_lease(path) as acquired:
        yield acquired


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
        store.validate_user(db, row["owner_id"], row.get("resource_account_id") or "")
