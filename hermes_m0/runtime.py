"""Process-local binding between the Hermes plugin and Investigation service."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from hermes_m0.account_activity_repository import DEFAULT_ACCOUNT_CORPUS_PATH
from hermes_m0.account_activity_service import AccountActivityToolService
from hermes_m0.ledger import ToolExecutionLedger
from hermes_m0.real_report_repository import PublishedReportRepository
from hermes_m0.repository import DEFAULT_FIXTURE_PATH, InvestigationRepository
from hermes_m0.report_task_service import ReportTaskInvestigationToolService
from hermes_m0.service import InvestigationToolService
from hermes_m0.task_repository import DEFAULT_TASK_FIXTURE_PATH, TaskRepository
from hermes_m0.task_service import TaskInvestigationToolService


_lock = threading.RLock()
_service: InvestigationToolService | None = None
_task_service: TaskInvestigationToolService | None = None
_report_task_service: ReportTaskInvestigationToolService | None = None


@dataclass(frozen=True)
class AuthorizedReportSource:
    database_path: Path | str
    report_version_id: str
    database_sha256: str
    content_hash: str
    snapshot_hash: str


def configure_runtime(
    fixture_path: Path | str = DEFAULT_FIXTURE_PATH,
    *,
    ledger_path: Path | str | None = None,
) -> InvestigationToolService:
    global _service
    service = InvestigationToolService(
        InvestigationRepository.load(fixture_path),
        ledger=ToolExecutionLedger(ledger_path or default_ledger_path()),
    )
    with _lock:
        _service = service
    return service


def get_runtime() -> InvestigationToolService:
    global _service
    with _lock:
        if _service is None:
            _service = InvestigationToolService(
                InvestigationRepository.load(),
                ledger=ToolExecutionLedger(default_ledger_path()),
            )
        return _service


def configure_task_runtime(
    fixture_path: Path | str = DEFAULT_TASK_FIXTURE_PATH,
    *,
    ledger_path: Path | str | None = None,
) -> TaskInvestigationToolService:
    global _task_service
    service = TaskInvestigationToolService(
        TaskRepository.load_all(fixture_path),
        ledger=ToolExecutionLedger(ledger_path or default_task_ledger_path()),
    )
    with _lock:
        _task_service = service
    return service


def get_task_runtime() -> TaskInvestigationToolService:
    global _task_service
    with _lock:
        if _task_service is None:
            _task_service = TaskInvestigationToolService(
                TaskRepository.load_all(),
                ledger=ToolExecutionLedger(default_task_ledger_path()),
            )
        return _task_service


def configure_report_task_runtime(
    fixture_path: Path | str = DEFAULT_FIXTURE_PATH,
    *,
    ledger_path: Path | str | None = None,
) -> ReportTaskInvestigationToolService:
    global _report_task_service
    service = ReportTaskInvestigationToolService(
        InvestigationRepository.load(fixture_path),
        ledger=ToolExecutionLedger(ledger_path or default_report_task_ledger_path()),
    )
    with _lock:
        _report_task_service = service
    return service


def configure_real_report_runtime(
    database_path: Path | str,
    *,
    report_version_id: str,
    expected_database_sha256: str,
    expected_content_hash: str,
    expected_snapshot_hash: str,
    ledger_path: Path | str | None = None,
    account_corpus_path: Path | str | None = None,
    additional_account_report_sources: Iterable[AuthorizedReportSource] = (),
) -> ReportTaskInvestigationToolService:
    """Bind M1/M2 to one verified published report without mutating its store."""

    global _report_task_service
    report_path = Path(database_path).expanduser().resolve(strict=True)
    execution_ledger_path = Path(
        ledger_path or default_real_report_ledger_path()
    ).expanduser().resolve()
    if execution_ledger_path == report_path or (
        execution_ledger_path.exists()
        and os.path.samefile(execution_ledger_path, report_path)
    ):
        raise ValueError("tool execution ledger must be separate from report SQLite")
    repository = PublishedReportRepository.load(
        report_path,
        report_version_id=report_version_id,
        expected_database_sha256=expected_database_sha256,
        expected_content_hash=expected_content_hash,
        expected_snapshot_hash=expected_snapshot_hash,
    )
    account_report_repositories = [repository]
    for source in additional_account_report_sources:
        source_path = Path(source.database_path).expanduser().resolve(strict=True)
        if source_path == execution_ledger_path or (
            execution_ledger_path.exists()
            and os.path.samefile(execution_ledger_path, source_path)
        ):
            raise ValueError("tool execution ledger must be separate from report SQLite")
        account_report_repositories.append(
            PublishedReportRepository.load(
                source_path,
                report_version_id=source.report_version_id,
                expected_database_sha256=source.database_sha256,
                expected_content_hash=source.content_hash,
                expected_snapshot_hash=source.snapshot_hash,
            )
        )
    account_report_task_ids = [
        item.fixture.provenance.source_task_id for item in account_report_repositories
    ]
    if len(account_report_task_ids) != len(set(account_report_task_ids)):
        raise ValueError("authorized Account report sources must have unique source tasks")
    account_activity = (
        None
        if account_corpus_path is None
        else AccountActivityToolService(
            repository,
            corpus_path=account_corpus_path or DEFAULT_ACCOUNT_CORPUS_PATH,
            authorized_report_repositories=tuple(account_report_repositories),
        )
    )
    service = ReportTaskInvestigationToolService(
        repository,
        ledger=ToolExecutionLedger(execution_ledger_path),
        account_activity=account_activity,
    )
    with _lock:
        _report_task_service = service
    return service


def get_report_task_runtime() -> ReportTaskInvestigationToolService:
    global _report_task_service
    with _lock:
        if _report_task_service is None:
            _report_task_service = ReportTaskInvestigationToolService(
                InvestigationRepository.load(),
                ledger=ToolExecutionLedger(default_report_task_ledger_path()),
            )
        return _report_task_service


def bind_report_task_session(
    session_id: str, *, force_new_generation: bool = False
):
    return get_report_task_runtime().bind_session(
        session_id, force_new_generation=force_new_generation
    )


def report_task_runtime_for_session(
    session_id: str,
) -> ReportTaskInvestigationToolService | None:
    with _lock:
        service = _report_task_service
    if service is not None and service.has_session(session_id):
        return service
    return None


def bind_task_session(
    session_id: str, *, task_id: str, force_new_generation: bool = False
):
    return get_task_runtime().bind_session(
        session_id,
        task_id=task_id,
        force_new_generation=force_new_generation,
    )


def has_task_session(session_id: str) -> bool:
    with _lock:
        service = _task_service
    return service is not None and service.has_session(session_id)


def task_runtime_for_session(session_id: str) -> TaskInvestigationToolService | None:
    with _lock:
        service = _task_service
    if service is not None and service.has_session(session_id):
        return service
    return None


def bind_session(session_id: str, *, force_new_generation: bool = False):
    return get_runtime().bind_session(
        session_id, force_new_generation=force_new_generation
    )


def default_ledger_path() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return hermes_home / "state" / "investigation_m0_tool_executions.sqlite3"


def default_task_ledger_path() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return hermes_home / "state" / "investigation_m0_cross_dataset_tool_executions.sqlite3"


def default_report_task_ledger_path() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return hermes_home / "state" / "investigation_m1_r1_tool_executions.sqlite3"


def default_real_report_ledger_path() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return hermes_home / "state" / "investigation_m1_real_report_tool_executions.sqlite3"
