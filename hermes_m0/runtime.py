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
_report_task_services: dict[str, "ReportRuntimeBinding"] = {}


@dataclass(frozen=True)
class AuthorizedReportSource:
    database_path: Path | str
    report_version_id: str
    database_sha256: str
    content_hash: str
    snapshot_hash: str


@dataclass(frozen=True)
class ReportRuntimeBinding:
    """Worker-local cache entry for one product Investigation Session."""

    session_id: str
    report_version_id: str
    snapshot_id: str
    snapshot_hash: str
    content_hash: str
    database_sha256: str
    authorized_report_sources: tuple[tuple[str, str, str, str], ...]
    service: ReportTaskInvestigationToolService


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
    return ReportTaskInvestigationToolService(
        InvestigationRepository.load(fixture_path),
        ledger=ToolExecutionLedger(ledger_path or default_report_task_ledger_path()),
    )


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
    if repository.template_kind in {"all_pass", "single_risk_post"}:
        from .pass_support import PassReportToolService, SnapshotAccountData, SnapshotAccountRepository
        from .account_corpus import AccountCorpus
        legacy_corpus = (
            AccountCorpus.load(Path(account_corpus_path or DEFAULT_ACCOUNT_CORPUS_PATH))
            if any(item.template_kind not in {"all_pass", "single_risk_post"} for item in account_report_repositories)
            else None
        )
        account_repository = SnapshotAccountRepository(SnapshotAccountData(
            tuple(account_report_repositories), legacy_corpus=legacy_corpus,
        ))
        account_activity = AccountActivityToolService(
            repository, repository_loader=lambda: account_repository,
            authorized_report_repositories=tuple(account_report_repositories),
        )
        service_type = PassReportToolService if repository.template_kind == "all_pass" else ReportTaskInvestigationToolService
        return service_type(repository, ledger=ToolExecutionLedger(execution_ledger_path), account_activity=account_activity)
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
    return service


def bind_report_task_session(
    session_id: str,
    *,
    service: ReportTaskInvestigationToolService,
    force_new_generation: bool = False,
):
    """Atomically bind one service to one immutable product Session identity."""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id is required for report runtime binding")
    scope = service.bind_session(
        normalized_session_id, force_new_generation=force_new_generation
    )
    database_sha256 = str(
        getattr(service.repository, "database_sha256", "validation-fixture")
    )
    candidate = ReportRuntimeBinding(
        session_id=normalized_session_id,
        report_version_id=scope.report_version_id,
        snapshot_id=scope.snapshot_id,
        snapshot_hash=scope.snapshot_hash,
        content_hash=scope.content_hash,
        database_sha256=database_sha256,
        authorized_report_sources=tuple(
            (
                item.fixture.report_version.id,
                str(item.snapshot_hash),
                str(item.content_hash),
                str(item.database_sha256),
            )
            for item in (
                service.account_activity.authorized_report_repositories
                if service.account_activity is not None
                else (service.repository,)
            )
        ),
        service=service,
    )
    with _lock:
        existing = _report_task_services.get(normalized_session_id)
        if existing is not None:
            existing_identity = (
                existing.report_version_id,
                existing.snapshot_id,
                existing.snapshot_hash,
                existing.content_hash,
                existing.database_sha256,
                existing.authorized_report_sources,
            )
            candidate_identity = (
                candidate.report_version_id,
                candidate.snapshot_id,
                candidate.snapshot_hash,
                candidate.content_hash,
                candidate.database_sha256,
                candidate.authorized_report_sources,
            )
            if existing_identity != candidate_identity:
                raise RuntimeError(
                    "product Session cannot be rebound to a different ReportVersion "
                    "or FrozenSnapshot"
                )
            return existing.service.refs.scope(normalized_session_id)
        _report_task_services[normalized_session_id] = candidate
    return scope


def report_task_runtime_for_session(
    session_id: str,
) -> ReportTaskInvestigationToolService | None:
    with _lock:
        binding = _report_task_services.get(session_id)
    if binding is not None and binding.service.has_session(session_id):
        return binding.service
    return None


def report_runtime_binding_for_session(
    session_id: str,
) -> ReportRuntimeBinding | None:
    with _lock:
        return _report_task_services.get(session_id)


def release_report_task_session(session_id: str) -> bool:
    """Release one worker-local cache entry without changing product state."""

    with _lock:
        return _report_task_services.pop(session_id, None) is not None


def release_all_report_task_sessions() -> tuple[str, ...]:
    """Release all worker-local bindings during service shutdown."""

    with _lock:
        session_ids = tuple(sorted(_report_task_services))
        _report_task_services.clear()
    return session_ids


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
