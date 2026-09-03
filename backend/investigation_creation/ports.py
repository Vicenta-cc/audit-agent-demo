from __future__ import annotations

from contextlib import AbstractContextManager
import sqlite3
from typing import Any, Callable, Protocol

from .contracts import (
    AuthoritativeDraftResolution,
    ConfirmationPreview,
    ConfirmationResolution,
    InvestigationDraft,
    InvestigationOptions,
    InvestigationConfiguration,
    InvestigationDraftConfiguration,
    InvestigationRun,
    QueryInvestigationOptions,
)
from .principal import Principal


class ConfigurationResolver(Protocol):
    def resolve(self, configuration: InvestigationConfiguration) -> dict[str, Any]: ...


class ResourceService(Protocol):
    def authoritative_draft_fence(
        self,
    ) -> AbstractContextManager[sqlite3.Connection]: ...

    def resolve_authoritative_draft(
        self,
        configuration: InvestigationDraftConfiguration,
        *,
        principal: Principal,
        resource_connection: sqlite3.Connection | None,
    ) -> AuthoritativeDraftResolution: ...

    def query_options(
        self, query: QueryInvestigationOptions, *, principal: Principal
    ) -> InvestigationOptions: ...

    def confirmation_preview(
        self, draft: InvestigationDraft, *, principal: Principal
    ) -> ConfirmationPreview: ...

    def confirmation_fence(
        self,
    ) -> AbstractContextManager[sqlite3.Connection]: ...

    def resolve_confirmation(
        self,
        draft: InvestigationDraft,
        *,
        principal: Principal,
        resource_connection: sqlite3.Connection | None = None,
    ) -> ConfirmationResolution: ...


class RunProjector(Protocol):
    def project(self, run: InvestigationRun) -> dict[str, Any]: ...


class ExecutionAdapter(Protocol):
    def job_id_for_run(self, run_id: str) -> str: ...

    def ensure_job(self, run: InvestigationRun) -> str: ...

    def run_pipeline(self, job_id: str, configuration: dict[str, Any]) -> None: ...

    def get_job_state(self, job_id: str) -> dict[str, Any] | None: ...

    def validate_selected_content_payloads(self, job_id: str) -> None: ...


class ReportAdapter(Protocol):
    def find_published(
        self, task_id: str, *, r31_run_id: str = ""
    ) -> str | None: ...

    def generate(
        self,
        task_id: str,
        *,
        on_generation_started: Callable[[str, str], None],
    ) -> str: ...

    def verify_published(self, report_version_id: str, *, task_id: str) -> None: ...


class ReportSessionAdapter(Protocol):
    def ensure_session(self, run_id: str, report_version_id: str) -> str: ...


class EmptyRunProjector:
    def project(self, run: InvestigationRun) -> dict[str, Any]:
        report_status = {
            "AUDIT_COMPLETED": "pending",
            "REPORT_GENERATING": "generating",
            "PUBLISHED": "published",
            "FAILED": "failed",
            "INTERRUPTED": "interrupted",
        }.get(run.status.value, "pending")
        return {
            "crawl_status": "pending" if not run.job_id else "unknown",
            "analysis_status": "pending" if not run.job_id else "unknown",
            "task_stats": {},
            "audit_results": [],
            "report_status": report_status,
        }
