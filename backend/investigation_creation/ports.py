from __future__ import annotations

from typing import Any, Callable, Protocol

from .contracts import InvestigationConfiguration, InvestigationRun


class ConfigurationResolver(Protocol):
    def resolve(self, configuration: InvestigationConfiguration) -> dict[str, Any]: ...


class RunProjector(Protocol):
    def project(self, run: InvestigationRun) -> dict[str, Any]: ...


class ExecutionAdapter(Protocol):
    def job_id_for_run(self, run_id: str) -> str: ...

    def ensure_job(self, run: InvestigationRun) -> str: ...

    def run_pipeline(self, job_id: str, configuration: dict[str, Any]) -> None: ...

    def get_job_state(self, job_id: str) -> dict[str, Any] | None: ...


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
            "REPORT_GENERATING": "generating",
            "PUBLISHED": "published",
            "FAILED": "failed",
            "INTERRUPTED": "interrupted",
        }.get(run.status.value, "pending")
        return {
            "crawl_status": "pending" if not run.job_id else "unknown",
            "analysis_status": "pending" if not run.job_id else "unknown",
            "task_stats": {},
            "report_status": report_status,
        }
