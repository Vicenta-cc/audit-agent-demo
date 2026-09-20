from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from contextlib import AbstractContextManager
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError

from backend.audit_agent.ingestion import (
    SELECTED_CONTENT_PAYLOAD_UNAVAILABLE,
    SelectedContentPayloadUnavailableError,
)
from backend.audit_agent.job_state import failure_from_job
from backend.hermes_runtime.service import HermesInvestigationAgentService

from .adapters import (
    AuditPipelineExecutionAdapter,
    ProductSessionAdapter,
    R31ReportAdapter,
)
from .contracts import (
    InvestigationRun,
    ResolvedExecutionConfiguration,
    RunStatus,
    parse_confirmed_configuration_snapshot,
)
from .errors import (
    AuthoritativeAuditProviderUnavailableError,
    CrawlerAccountAuthenticationRequiredError,
    LeaseLostError,
)
from .ports import ExecutionAdapter, ReportAdapter, ReportSessionAdapter
from .store import InvestigationCreationStore


logger = logging.getLogger(__name__)


class InvestigationWorker:
    """Single-process M3 worker with durable recovery and fencing."""

    def __init__(
        self,
        store: InvestigationCreationStore,
        *,
        execution_adapter: ExecutionAdapter,
        report_adapter: ReportAdapter,
        session_adapter: ReportSessionAdapter,
        worker_id: str | None = None,
        lease_timeout_seconds: int = 300,
        heartbeat_interval_seconds: float = 30.0,
        lifecycle_hook: Callable[[str, InvestigationRun, str], None] | None = None,
    ) -> None:
        self.store = store
        self.execution_adapter = execution_adapter
        if isinstance(execution_adapter, AuditPipelineExecutionAdapter):
            execution_adapter.creation_store = store
        self.report_adapter = report_adapter
        self.session_adapter = session_adapter
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid4().hex[:8]}"
        self.lease_timeout_seconds = max(1, int(lease_timeout_seconds))
        self.heartbeat_interval_seconds = max(0.05, float(heartbeat_interval_seconds))
        self.lifecycle_hook = lifecycle_hook

    def run_once(self) -> InvestigationRun | None:
        if not self.store.admission.enabled:
            return self._run_once()
        from backend.task_admission.execution import execution_lock
        from backend.task_admission.recovery import reconcile
        from backend.task_admission.legacy import dispatch_one
        with execution_lock(self.store.db_path) as acquired:
            if not acquired:
                return None
            reconcile(self)
            dispatched = dispatch_one(self)
            result = dispatched if dispatched else self._run_once()
        # Closing the original file description does not prove crawler children
        # stopped. Only acquiring a NEW description proves none still holds it.
        with execution_lock(self.store.db_path) as stopped:
            if stopped:
                reconcile(self)
        return result

    def _run_once(self) -> InvestigationRun | None:
        run = self.store.claim_next(
            self.worker_id,
            lease_timeout_seconds=self.lease_timeout_seconds,
        )
        if run is None:
            return None
        try:
            from contextlib import nullcontext
            from backend.task_admission.execution import execution_context
            from backend.task_admission.store import AdmissionError
            admission = self.store.admission
            if admission.enabled:
                try:
                    admission.start(run.id, run.claim_token)
                except AdmissionError as exc:
                    return self.store.mark_failed(run.id,run.claim_token,error_code=exc.code,error_message=str(exc))
            with execution_context(admission,run.id,run.claim_token) if admission.enabled else nullcontext():
                if run.status == RunStatus.REPORT_GENERATING:
                    return self._finish_report(run, allow_generation=False)
                if run.recovery_required:
                    return self._recover_execution(run)
                return self._execute_claimed_run(run)
        except LeaseLostError:
            return self.store.get_run_for_worker(run.id)

    def run_forever(self, *, poll_seconds: float = 1.0) -> None:
        while True:
            try:
                completed = self.run_once()
            except Exception:
                logger.exception("Task worker retained durable state after an execution error")
                completed = None
            if completed is None:
                time.sleep(max(0.05, poll_seconds))

    def _execute_claimed_run(self, run: InvestigationRun) -> InvestigationRun:
        configuration = self._validated_configuration_or_fail(run)
        if isinstance(configuration, InvestigationRun):
            return configuration
        snapshot = parse_confirmed_configuration_snapshot(run.confirmed_configuration)
        schema_version = snapshot.schema_version
        try:
            with self._lease(run) as lease:
                lease.assert_owned()
                job_id = self.execution_adapter.ensure_job(run)
                self._notify("after_job_created", run, job_id)
                lease.assert_owned()
                run = self.store.bind_job(run.id, run.claim_token, job_id)
                lease.assert_owned()
                self._validate_account_before_execution(
                    configuration, job_id, schema_version=schema_version
                )
                run = self.store.mark_pipeline_started(run.id, run.claim_token)
                lease.assert_owned()
                pipeline_configuration = dict(configuration)
                # This internal projection scopes the SQL selection contract to
                # confirmed Investigation Runs without changing ordinary Jobs.
                pipeline_configuration["_confirmed_analyze_limit"] = int(
                    snapshot.execution.analyze_limit
                )
                if schema_version in {
                    "investigation-run-config-v3",
                    "investigation-run-config-v4",
                }:
                    pipeline_configuration["_authoritative_m3_contract"] = True
                self.execution_adapter.run_pipeline(job_id, pipeline_configuration)
                lease.assert_owned()
                run = self.store.mark_pipeline_returned(run.id, run.claim_token)
        except LeaseLostError:
            raise
        except CrawlerAccountAuthenticationRequiredError as exc:
            self._invalidate_job_for_account(run, exc)
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code=exc.code,
                error_message=str(exc),
            )
        except AuthoritativeAuditProviderUnavailableError as exc:
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code=exc.code,
                error_message=str(exc),
            )
        except Exception as exc:
            if not self.store.owns_claim(run.id, run.claim_token):
                raise LeaseLostError("Run lease was lost during Pipeline execution") from exc
            if run.pipeline_started_at or self.store.get_run_for_worker(
                run.id
            ).pipeline_started_at:
                return self.store.mark_interrupted(
                    run.id,
                    run.claim_token,
                    error_code="pipeline_result_unknown",
                    error_message=str(exc)
                    or "AuditPipeline execution result is unknown.",
                )
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code="job_creation_failed",
                error_message=str(exc),
            )
        self._notify("after_pipeline_returned", run, job_id)
        return self._classify_job_result(run, job_id)

    def _recover_execution(self, run: InvestigationRun) -> InvestigationRun:
        configuration = self._validated_configuration_or_fail(run)
        if isinstance(configuration, InvestigationRun):
            return configuration
        deterministic_job_id = self.execution_adapter.job_id_for_run(run.id)
        if run.job_id and run.job_id != deterministic_job_id:
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code="job_binding_conflict",
                error_message="Run Job does not match its deterministic identity.",
            )
        job_state = self.execution_adapter.get_job_state(deterministic_job_id)
        if job_state is None:
            if run.pipeline_started_at:
                return self.store.mark_interrupted(
                    run.id,
                    run.claim_token,
                    error_code="started_pipeline_job_missing",
                    error_message="Pipeline was marked started but its Job is missing.",
                )
            return self._execute_claimed_run(run)

        if not run.job_id:
            run = self.store.bind_job(run.id, run.claim_token, deterministic_job_id)
        status = str(job_state.get("status") or "")
        if status == "queued":
            if run.pipeline_started_at:
                return self.store.mark_interrupted(
                    run.id,
                    run.claim_token,
                    error_code="queued_job_after_pipeline_start",
                    error_message=(
                        "Pipeline start was persisted but Job execution is not provably unstarted."
                    ),
                )
            return self._execute_claimed_run(run)
        return self._classify_job_result(run, deterministic_job_id, job_state=job_state)

    def _classify_job_result(
        self,
        run: InvestigationRun,
        job_id: str,
        *,
        job_state: dict[str, Any] | None = None,
    ) -> InvestigationRun:
        if self.store.admission.enabled:
            row = self.store.admission.for_task(run.id)
            if row and row["decision"] == "CANCELLED":
                return self.store.mark_failed(run.id,run.claim_token,error_code="task_cancelled",error_message="任务已取消。")
        failure = self._verify_job_or_fail(run)
        if failure is not None:
            return failure
        state = job_state or self.execution_adapter.get_job_state(job_id)
        if state is None:
            return self.store.mark_interrupted(
                run.id,
                run.claim_token,
                error_code="job_result_missing",
                error_message="Job state is unavailable after AuditPipeline returned.",
            )
        status = str(state.get("status") or "")
        if status == "failed":
            error_message = str(state.get("error") or "AuditPipeline Job failed.")
            structured_failure = failure_from_job(state)
            error_code = str(structured_failure.get("code") or "")
            if not error_code:
                # Compatibility for Jobs created before structured failure
                # metadata was persisted.
                error_code = (
                    SELECTED_CONTENT_PAYLOAD_UNAVAILABLE
                    if error_message.startswith(f"{SELECTED_CONTENT_PAYLOAD_UNAVAILABLE}:")
                    else "crawler_account_login_required"
                    if error_message.startswith("crawler_account_login_required:")
                    else "crawler_account_verification_required"
                    if error_message.startswith("crawler_account_verification_required:")
                    else "crawler_rate_limited"
                    if error_message.startswith("crawler_rate_limited:")
                    else "audit_provider_unavailable"
                    if error_message.startswith("audit_provider_unavailable:")
                    else "audit_provider_failed"
                    if error_message.startswith("audit_provider_failed:")
                    else "no_valid_content_selected"
                    if error_message.startswith("no_valid_content_selected:")
                    else "audit_job_failed"
                )
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code=error_code,
                error_message=error_message,
            )
        if status in {
            "stopped",
            "interrupted",
            "crawl_paused",
            "analysis_stopped",
            "analysis_paused",
        }:
            return self.store.mark_interrupted(
                run.id,
                run.claim_token,
                error_code="audit_job_stopped",
                error_message=f"Job stopped with status: {status}",
            )
        if status != "completed":
            return self.store.mark_interrupted(
                run.id,
                run.claim_token,
                error_code="collection_result_unknown",
                error_message=f"Job has non-final status: {status or 'unknown'}",
            )
        failed_posts = int((state.get("task_stats") or {}).get("failed_analysis_count") or 0)
        completed_posts = int((state.get("task_stats") or {}).get("completed_analysis_count") or 0)
        snapshot = parse_confirmed_configuration_snapshot(run.confirmed_configuration)
        if snapshot.execution.auto_analyze is not None and int((state.get("task_stats") or {}).get("queued_analysis_count") or 0) > 0:
            return self.store.mark_interrupted(run.id, run.claim_token,
                error_code="analysis_pending", error_message="采集已完成，仍有待分析内容，可继续分析。")
        gate_failure = self._completion_gate_failure(run, state=state, allow_failed_posts=failed_posts > 0)
        if gate_failure is not None:
            error_code, error_message = gate_failure
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code=error_code,
                error_message=error_message,
            )
        if completed_posts < 1:
            # Processing is drained, but a report cannot be built without at
            # least one authoritative completed audit result.
            return self.store.mark_audit_completed(run.id, run.claim_token)
        run = self.store.mark_report_generating(run.id, run.claim_token)
        return self._finish_report(run, allow_generation=True)

    def _finish_report(
        self, run: InvestigationRun, *, allow_generation: bool
    ) -> InvestigationRun:
        if not run.job_id:
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code="report_job_missing",
                error_message="REPORT_GENERATING Run has no Job binding.",
            )
        state = self.execution_adapter.get_job_state(run.job_id)
        failed_posts = int(
            ((state or {}).get("task_stats") or {}).get("failed_analysis_count")
            or 0
        )
        gate_failure = self._completion_gate_failure(
            run,
            state=state,
            allow_failed_posts=failed_posts > 0,
        )
        if gate_failure is not None:
            error_code, error_message = gate_failure
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code=error_code,
                error_message=error_message,
            )
        binding = self.store.get_report_binding(run.id)
        if binding is None:
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code="report_generation_binding_missing",
                error_message="REPORT_GENERATING Run has no generation binding.",
            )

        try:
            published = self.report_adapter.find_published(
                run.job_id, r31_run_id=binding.r31_run_id
            )
            if published is None and not allow_generation:
                return self.store.mark_interrupted(
                    run.id,
                    run.claim_token,
                    error_code="report_generation_result_unknown",
                    error_message=(
                        "The prior report generation lease expired without a published result; "
                        "automatic Provider replay is fenced."
                    ),
                )
            if published is None and binding.state != "RESERVED":
                return self.store.mark_interrupted(
                    run.id,
                    run.claim_token,
                    error_code="report_generation_result_unknown",
                    error_message="R3.1 generation started but has no published result.",
                )

            report_version_id = published or ""
            if not report_version_id:
                with self._lease(run) as lease:
                    lease.assert_owned()

                    def bind_generation(
                        r31_run_id: str, generated_report_version_id: str
                    ) -> None:
                        self.store.bind_report_generation_started(
                            run.id,
                            generation_key=binding.generation_key,
                            r31_run_id=r31_run_id,
                            report_version_id=generated_report_version_id,
                        )
                        lease.assert_owned()

                    report_version_id = self.report_adapter.generate(
                        run.job_id,
                        on_generation_started=bind_generation,
                    )
                    lease.assert_owned()
            return self._complete_published_report(run, report_version_id)
        except LeaseLostError:
            raise
        except Exception as exc:
            return self._recover_report_exception(run, exc)

    def _complete_published_report(
        self, run: InvestigationRun, report_version_id: str
    ) -> InvestigationRun:
        self.report_adapter.verify_published(report_version_id, task_id=run.job_id)
        self.store.mark_report_binding_published(
            run.id,
            run.claim_token,
            report_version_id=report_version_id,
        )
        self._notify("after_report_published", run, report_version_id)
        with self._lease(run) as lease:
            lease.assert_owned()
            report_session_id = self.session_adapter.ensure_session(
                run.id, report_version_id
            )
            lease.assert_owned()
        self._notify("after_session_created", run, report_version_id)
        return self.store.mark_published(
            run.id,
            run.claim_token,
            report_version_id=report_version_id,
            report_session_id=report_session_id,
        )

    def _recover_report_exception(
        self, run: InvestigationRun, original_error: Exception
    ) -> InvestigationRun:
        if not self.store.owns_claim(run.id, run.claim_token):
            raise LeaseLostError("Run lease was lost during report generation") from original_error
        try:
            binding = self.store.get_report_binding(run.id)
            published = (
                self.report_adapter.find_published(
                    run.job_id,
                    r31_run_id=binding.r31_run_id if binding else "",
                )
                if binding is not None
                else None
            )
        except Exception as discovery_error:
            if not self.store.owns_claim(run.id, run.claim_token):
                raise LeaseLostError(
                    "Run lease was lost while reconciling report publication"
                ) from discovery_error
            return self.store.mark_interrupted(
                run.id,
                run.claim_token,
                error_code="report_generation_result_unknown",
                error_message=(
                    f"{original_error}; publication lookup failed: {discovery_error}"
                ),
            )
        if not published:
            return self.store.mark_interrupted(
                run.id,
                run.claim_token,
                error_code="report_generation_result_unknown",
                error_message=str(original_error)
                or "R3.1 generation result is unknown; automatic replay is fenced.",
            )
        try:
            return self._complete_published_report(run, published)
        except LeaseLostError:
            raise
        except Exception as completion_error:
            if not self.store.owns_claim(run.id, run.claim_token):
                raise LeaseLostError(
                    "Run lease was lost while finalizing a published report"
                ) from completion_error
            return self.store.defer_report_completion(
                run.id,
                run.claim_token,
                error_message=(
                    f"Published ReportVersion {published} requires finalization retry: "
                    f"{completion_error}"
                ),
            )

    def _verify_job_or_fail(self, run):
        validator = getattr(self.execution_adapter, "verify_run_job", None)
        if not callable(validator):
            return None
        try:
            validator(run)
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            return self.store.mark_failed(run.id, run.claim_token,
                error_code="frozen_execution_mismatch", error_message=str(exc))
        return None

    def _validated_configuration_or_fail(
        self, run: InvestigationRun
    ) -> dict[str, Any] | InvestigationRun:
        try:
            snapshot = parse_confirmed_configuration_snapshot(
                run.confirmed_configuration
            )
            configuration = ResolvedExecutionConfiguration.model_validate(
                snapshot.execution.model_dump(mode="json")
            ).model_dump(mode="json")
            if snapshot.schema_version in {
                "investigation-run-config-v3",
                "investigation-run-config-v4",
            }:
                validator = getattr(
                    self.execution_adapter,
                    "validate_m3_configuration",
                    None,
                )
                if callable(validator):
                    validator(configuration, schema_version=snapshot.schema_version,
                              job_id=self.execution_adapter.job_id_for_run(run.id))
            return configuration
        except CrawlerAccountAuthenticationRequiredError as exc:
            self._invalidate_job_for_account(run, exc)
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code=exc.code,
                error_message=str(exc),
            )
        except AuthoritativeAuditProviderUnavailableError as exc:
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code=exc.code,
                error_message=str(exc),
            )
        except (ValidationError, ValueError) as exc:
            return self.store.mark_failed(
                run.id,
                run.claim_token,
                error_code="invalid_confirmed_configuration",
                error_message=str(exc),
            )

    def _validate_account_before_execution(
        self,
        configuration: dict[str, Any],
        job_id: str,
        *,
        schema_version: str,
    ) -> None:
        validator = getattr(
            self.execution_adapter,
            "validate_execution_configuration",
            None,
        )
        try:
            if callable(validator) and schema_version in {
                "investigation-run-config-v3",
                "investigation-run-config-v4",
            }:
                validator(configuration, schema_version=schema_version, job_id=job_id)
        except CrawlerAccountAuthenticationRequiredError:
            invalidator = getattr(
                self.execution_adapter,
                "invalidate_job_for_account",
                None,
            )
            if callable(invalidator):
                invalidator(job_id, "crawler account login is required")
            raise

    def _completion_gate_failure(
        self,
        run: InvestigationRun,
        *,
        state: dict[str, Any] | None = None,
        allow_failed_posts: bool = False,
    ) -> tuple[str, str] | None:
        if not run.job_id:
            return "job_result_missing", "Run has no Job binding."
        snapshot = parse_confirmed_configuration_snapshot(run.confirmed_configuration)
        authoritative_m3 = snapshot.schema_version in {
            "investigation-run-config-v3",
            "investigation-run-config-v4",
        }
        analyze_limit = int(snapshot.execution.analyze_limit)
        state = state or self.execution_adapter.get_job_state(run.job_id)
        if state is None:
            return "job_result_missing", "Job state is unavailable before completion."
        stats = dict(state.get("task_stats") or {})
        ingested = int(stats.get("ingested_count") or 0)
        pending = int(stats.get("pending_analysis_count") or 0)
        analyzing = int(stats.get("analyzing_count") or 0)
        completed = int(stats.get("completed_analysis_count") or 0)
        failed = int(stats.get("failed_analysis_count") or 0) if allow_failed_posts else 0
        if ingested < 1:
            return "no_valid_content_selected", "No valid content was selected for analysis."
        if snapshot.execution.auto_analyze is None and (analyze_limit < 1 or ingested > analyze_limit):
            return (
                "ingested_count_exceeds_analyze_limit",
                f"Job selected {ingested} contents with analyze_limit={analyze_limit}.",
            )
        if pending != failed or analyzing:
            return (
                "analysis_not_drained",
                f"Job has pending={pending}, analyzing={analyzing} before audit completion.",
            )
        if completed + failed != ingested:
            return (
                "completed_analysis_count_mismatch",
                f"Job completed {completed} analyses for {ingested} selected contents.",
            )
        if authoritative_m3:
            audit_results = state.get("audit_results")
            if not isinstance(audit_results, list) or len(audit_results) != completed:
                return (
                    "completed_audit_result_missing",
                    "Completed audit result count does not match selected content membership.",
                )
            content_keys = {
                str(item.get("content_key") or "").strip()
                for item in audit_results
                if isinstance(item, dict)
            }
            if "" in content_keys or len(content_keys) != completed:
                return (
                    "completed_audit_result_missing",
                    "Completed audit results do not have a unique selected content identity.",
                )
            for item in audit_results:
                decision = str(item.get("decision") or "").lower()
                risk_level = str(item.get("risk_level") or "").lower()
                if decision not in {"pass", "review", "reject"} or risk_level not in {
                    "none",
                    "low",
                    "medium",
                    "high",
                }:
                    return (
                        "completed_audit_result_invalid",
                        "Completed audit result has an invalid decision or risk level.",
                    )
        try:
            self.execution_adapter.validate_selected_content_payloads(run.job_id)
        except SelectedContentPayloadUnavailableError as exc:
            return SELECTED_CONTENT_PAYLOAD_UNAVAILABLE, str(exc)
        except Exception as exc:
            return (
                SELECTED_CONTENT_PAYLOAD_UNAVAILABLE,
                f"{SELECTED_CONTENT_PAYLOAD_UNAVAILABLE}: {exc}",
            )
        return None

    def _invalidate_job_for_account(
        self, run: InvestigationRun, error: CrawlerAccountAuthenticationRequiredError
    ) -> None:
        job_id = run.job_id
        if not job_id:
            resolver = getattr(self.execution_adapter, "job_id_for_run", None)
            if callable(resolver):
                job_id = resolver(run.id)
        if not job_id:
            return
        invalidator = getattr(
            self.execution_adapter,
            "invalidate_job_for_account",
            None,
        )
        if callable(invalidator):
            invalidator(job_id, str(error))

    def _lease(self, run: InvestigationRun) -> "_LeaseHeartbeat":
        return _LeaseHeartbeat(
            self.store,
            run.id,
            run.claim_token,
            interval_seconds=self.heartbeat_interval_seconds,
        )

    def _notify(
        self, event: str, run: InvestigationRun, detail: str
    ) -> None:
        if self.lifecycle_hook is not None:
            self.lifecycle_hook(event, run, detail)


class _LeaseHeartbeat(AbstractContextManager["_LeaseHeartbeat"]):
    def __init__(
        self,
        store: InvestigationCreationStore,
        run_id: str,
        claim_token: str,
        *,
        interval_seconds: float,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.claim_token = claim_token
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_LeaseHeartbeat":
        if not self.store.heartbeat(self.run_id, self.claim_token):
            self._lost.set()
            raise LeaseLostError("Run lease ownership was lost")
        self._thread = threading.Thread(
            target=self._run,
            name=f"m3-heartbeat-{self.run_id[-8:]}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))
        if exc_type is None:
            self.assert_owned()

    def assert_owned(self) -> None:
        if self._lost.is_set() or not self.store.owns_claim(
            self.run_id, self.claim_token
        ):
            self._lost.set()
            raise LeaseLostError("Run fencing token is no longer current")

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            if not self.store.heartbeat(self.run_id, self.claim_token):
                self._lost.set()
                return


def build_worker() -> InvestigationWorker:
    store = InvestigationCreationStore()
    return InvestigationWorker(
        store,
        execution_adapter=AuditPipelineExecutionAdapter(),
        report_adapter=R31ReportAdapter(),
        session_adapter=ProductSessionAdapter(HermesInvestigationAgentService()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes M3 Investigation worker")
    parser.add_argument("--once", action="store_true", help="process at most one Run")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    worker = build_worker()
    if args.once:
        worker.run_once()
    else:
        worker.run_forever(poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
