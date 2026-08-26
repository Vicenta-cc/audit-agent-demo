from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from backend.audit_agent.config import settings
from backend.domain.contracts import FindingFilters
from backend.domain.errors import FindingHasNoEvidenceError
from backend.domain.identity import stable_hash
from backend.domain.pagination import Pagination
from backend.domain.query_service import DomainQueryService
from backend.reporting.contracts import (
    ClaimType,
    FrozenSourceSnapshot,
    ModelStepResult,
    OutlinePlan,
    ReportGenerationResult,
    ReportGraphState,
    ReportMetric,
    SectionDraft,
    SectionKind,
)
from backend.reporting.errors import ReportGenerationError, ReportValidationError
from backend.reporting.presentation import HumanReportAssembler
from backend.reporting.prompts import outline_messages, section_messages
from backend.reporting.qwen_report_client import QwenReportClient
from backend.reporting.selection import select_representative_findings
from backend.reporting.store import ReportStore


NODE_ORDER = (
    "freeze_source_snapshot",
    "build_statistics",
    "select_findings",
    "plan_outline",
    "draft_sections",
    "validate_numbers",
    "validate_claim_support",
    "validate_citations",
    "assemble_report",
    "publish_report_version",
)

FORBIDDEN_PRESENTATION_PATTERNS = (
    re.compile(r"finding:audit_result:", re.IGNORECASE),
    re.compile(r"evidence:audit_result:", re.IGNORECASE),
    re.compile(r"report-claim:", re.IGNORECASE),
    re.compile(r"report-source-snapshot:", re.IGNORECASE),
    re.compile(r"metric:"),
    re.compile(r"metric_ref", re.IGNORECASE),
    re.compile(r"source_hash", re.IGNORECASE),
    re.compile(r"来源(?:快照|哈希)"),
    re.compile(r"\b(?:finding|findings|evidence)\b", re.IGNORECASE),
)

CONCLUSION_ABSENCE_PATTERNS = (
    re.compile(r"当前输入未提供"),
    re.compile(r"未提供(?:具体的?)?(?:调查)?(?:发现|证据)"),
    re.compile(r"没有(?:提供)?(?:具体的?)?(?:调查)?(?:发现|证据)"),
    re.compile(r"仅.{0,12}(?:方法论|分析流程|数据范围)(?:说明|介绍)"),
)

UNSUPPORTED_INTENT_PATTERNS = (
    re.compile(r"真实意图"),
    re.compile(r"发布者.{0,30}(?:规避审核|故意引流|蓄意引流|吸引目标群体)"),
    re.compile(r"利用.{0,20}(?:律师|警方|普法|科普|合规).{0,20}(?:规避|掩护)"),
)

UNEXPLAINED_ENGLISH_PATTERN = re.compile(
    r"\b(?:inadvertently|however|therefore|meanwhile|additionally|notably)\b",
    re.IGNORECASE,
)


class ReportGenerationGraph:
    def __init__(
        self,
        *,
        query_service: DomainQueryService | None = None,
        store: ReportStore | None = None,
        model_client: QwenReportClient | Any | None = None,
        checkpoint_path: Path | None = None,
        representative_finding_limit: int = 10,
        fail_once_section_index: int | None = None,
    ):
        self.query_service = query_service or DomainQueryService()
        self.store = store or ReportStore()
        self.model_client = model_client or QwenReportClient()
        self.presentation = HumanReportAssembler()
        self.representative_finding_limit = representative_finding_limit
        self.fail_once_section_index = fail_once_section_index
        self._failure_injected = False
        checkpoint_file = (
            checkpoint_path or (settings.data_dir / "report_checkpoints.sqlite3")
        ).resolve()
        checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_connection = sqlite3.connect(
            checkpoint_file, check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        self.checkpointer.setup()
        self.app = self._build_graph().compile(checkpointer=self.checkpointer)

    def close(self) -> None:
        self._checkpoint_connection.close()

    def generate(self, task_id: str) -> ReportGenerationResult:
        generation = self.store.create_generation(
            task_id,
            model=self.model_client.model,
            prompt_version=self.model_client.prompt_version,
        )
        self._on_generation_created(generation)
        initial: ReportGraphState = {
            **generation,
            "source_snapshot": {},
            "statistics": {},
            "selected_finding_ids": [],
            "selected_finding_cards": [],
            "outline": {},
            "section_drafts": [],
            "claims": [],
            "number_validation_errors": [],
            "claim_validation_errors": [],
            "citation_validation_errors": [],
            "citation_details": {},
            "assembled_report": {},
            "current_node": "",
            "retry_count": 0,
            "warnings": [],
            "prompt_version": self.model_client.prompt_version,
            "model": self.model_client.model,
        }
        config = {"configurable": {"thread_id": generation["run_id"]}}
        try:
            final_state = self.app.invoke(initial, config=config)
        except Exception as exc:
            self.store.update_run(generation["run_id"], status="recoverable", error=exc)
            raise
        return self._result_from_state(final_state)

    def resume(self, run_id: str) -> ReportGenerationResult:
        run = self.store.get_run(run_id)
        if run is None:
            raise ReportGenerationError(f"report generation run not found: {run_id}")
        if run["status"] == "completed":
            version = self.store.get_version(run["report_version_id"])
            if version is None:
                raise ReportGenerationError("completed run has no report version")
            return self._result_from_version(run, version)
        self.store.update_run(run_id, status="running", error=None)
        self._on_generation_created(run)
        config = {"configurable": {"thread_id": run_id}}
        try:
            final_state = self.app.invoke(None, config=config)
        except Exception as exc:
            self.store.update_run(run_id, status="recoverable", error=exc)
            raise
        return self._result_from_state(final_state)

    def _on_generation_created(self, generation: dict[str, Any]) -> None:
        """Hook for integrations that need run-scoped provider persistence."""
        return None

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(ReportGraphState)
        functions = {
            "freeze_source_snapshot": self._freeze_source_snapshot,
            "build_statistics": self._build_statistics,
            "select_findings": self._select_findings,
            "plan_outline": self._plan_outline,
            "draft_sections": self._draft_sections,
            "validate_numbers": self._validate_numbers,
            "validate_claim_support": self._validate_claim_support,
            "validate_citations": self._validate_citations,
            "assemble_report": self._assemble_report,
            "publish_report_version": self._publish_report_version,
        }
        for name in NODE_ORDER:
            function = functions[name]
            graph.add_node(name, self._wrap_node(name, function))
        graph.add_edge(START, NODE_ORDER[0])
        for current, following in zip(NODE_ORDER, NODE_ORDER[1:]):
            graph.add_edge(current, following)
        graph.add_edge(NODE_ORDER[-1], END)
        return graph

    def _wrap_node(
        self,
        name: str,
        function: Callable[[ReportGraphState], dict[str, Any]],
    ) -> Callable[[ReportGraphState], dict[str, Any]]:
        def wrapped(state: ReportGraphState) -> dict[str, Any]:
            run_id = state["run_id"]
            self.store.update_run(run_id, status="running", current_node=name)
            self.store.record_run_event(run_id, name, "started")
            try:
                output = function(state)
            except Exception as exc:
                self.store.record_run_event(
                    run_id,
                    name,
                    "failed",
                    {"error_type": type(exc).__name__, "message": str(exc)},
                )
                self.store.update_run(run_id, status="recoverable", current_node=name, error=exc)
                raise
            self.store.record_run_event(run_id, name, "succeeded")
            return {**output, "current_node": name}

        return wrapped

    def _freeze_source_snapshot(self, state: ReportGraphState) -> dict[str, Any]:
        task_id = state["task_id"]
        task_snapshot = self.query_service.get_task_snapshot(task_id)
        search = self._all_finding_cards(task_id)
        evidence_ids: list[str] = []
        warning_records = [item.model_dump(mode="json") for item in task_snapshot.warnings]
        for card in search:
            detail = self.query_service.get_finding_detail(card["finding_id"])
            warning_records.extend(item.model_dump(mode="json") for item in detail.warnings)
            try:
                evidence = self.query_service.get_finding_evidence(
                    card["finding_id"], limit=100
                )
            except FindingHasNoEvidenceError:
                continue
            evidence_ids.extend(item.evidence_id for item in evidence.data)
            warning_records.extend(item.model_dump(mode="json") for item in evidence.warnings)
        warning_records = self._deduplicate_dicts(warning_records)
        generated_at = datetime.now(timezone.utc).isoformat()
        snapshot_body = {
            "report_version_id": state["report_version_id"],
            "task_id": task_id,
            "task_status": task_snapshot.data.task_status,
            "source_hash": task_snapshot.data.source_hash,
            "configuration_revision_id": task_snapshot.data.configuration_revision_id,
            "finding_ids": list(task_snapshot.data.finding_ids),
            "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "data_quality_warnings": warning_records,
            "statistic_inputs": [
                item.model_dump(mode="json") for item in task_snapshot.data.statistic_inputs
            ],
            "generated_at": generated_at,
        }
        snapshot_hash = stable_hash(snapshot_body)
        frozen = FrozenSourceSnapshot(
            snapshot_id=f"report-source-snapshot:{stable_hash(state['report_version_id'])[:24]}",
            snapshot_hash=snapshot_hash,
            **snapshot_body,
        )
        persisted = self.store.save_source_snapshot(frozen.model_dump(mode="json"))
        normalized = {
            "snapshot_id": persisted["id"],
            "report_version_id": persisted["report_version_id"],
            "task_id": persisted["task_id"],
            "task_status": persisted["task_status"],
            "source_hash": persisted["source_hash"],
            "configuration_revision_id": persisted["configuration_revision_id"],
            "finding_ids": persisted["finding_ids"],
            "evidence_ids": persisted["evidence_ids"],
            "data_quality_warnings": persisted["data_quality_warnings"],
            "statistic_inputs": persisted["statistic_inputs"],
            "generated_at": persisted["generated_at"],
            "snapshot_hash": persisted["snapshot_hash"],
        }
        return {"source_snapshot": normalized, "warnings": warning_records}

    def _build_statistics(self, state: ReportGraphState) -> dict[str, Any]:
        statistics = self._calculate_statistics(state["task_id"])
        return {"statistics": statistics}

    def _select_findings(self, state: ReportGraphState) -> dict[str, Any]:
        cards = self._all_finding_cards(state["task_id"])
        selected = select_representative_findings(
            cards, limit=self.representative_finding_limit
        )
        return {
            "selected_finding_ids": [item["finding_id"] for item in selected],
            "selected_finding_cards": selected,
        }

    def _plan_outline(self, state: ReportGraphState) -> dict[str, Any]:
        overview = self._task_overview_for_state(state)
        task = {
            "task_id": state["task_id"],
            "task_name": overview.task_name,
            "task_status": overview.task_status,
            "source_platform": overview.source_platform,
        }
        messages = outline_messages(
            task=task,
            statistics=state["statistics"],
            finding_cards=state["selected_finding_cards"],
        )
        result = self._model_step(
            state=state,
            node_name="plan_outline",
            section_id="",
            messages=messages,
            response_model=OutlinePlan,
            semantic_validator=lambda output: self._validate_outline_semantics(output, state),
        )
        return {"outline": result.output}

    def _draft_sections(self, state: ReportGraphState) -> dict[str, Any]:
        task_overview = self._task_overview_for_state(state)
        task = {
            "task_id": state["task_id"],
            "task_name": task_overview.task_name,
            "task_status": task_overview.task_status,
            "source_platform": task_overview.source_platform,
            "data_quality_summary": task_overview.data_quality_summary,
            "data_quality_warning_count": len(state["warnings"]),
            "data_quality_warning_codes": sorted(
                {
                    str(item.get("code") or "")
                    for item in state["warnings"]
                    if isinstance(item, dict) and item.get("code")
                }
            ),
        }
        metric_registry = {
            item["metric_key"]: item for item in state["statistics"]["metrics"]
        }
        drafts = []
        for index, section in enumerate(state["outline"]["sections"]):
            section_finding_ids = list(section.get("finding_ids") or [])[:5]
            finding_inputs = [
                self._finding_prompt_input(finding_id) for finding_id in section_finding_ids
            ]
            prior_report_context = None
            prior_claims: list[dict[str, Any]] = []
            if section.get("section_kind") == SectionKind.CONCLUSION.value:
                overview_draft = next(
                    (
                        item
                        for item in drafts
                        if item.get("section_kind") == SectionKind.OVERVIEW.value
                    ),
                    None,
                )
                prior_claims = [
                    claim
                    for draft in drafts
                    for claim in draft.get("claims") or []
                ]
                prior_report_context = {
                    "overall_findings_summary": (
                        str(overview_draft.get("body") or "").strip()
                        if overview_draft
                        else ""
                    ),
                    "validated_prior_claims": [
                        {
                            "text": claim.get("text", ""),
                            "claim_type": claim.get("claim_type", ""),
                            "finding_ids": claim.get("finding_ids") or [],
                            "evidence_ids": claim.get("evidence_ids") or [],
                            "metric_refs": claim.get("metric_refs") or [],
                            "support_type": claim.get("support_type", ""),
                        }
                        for claim in prior_claims
                    ],
                }
            metric_refs = list(section.get("metric_refs") or [])
            metric_refs.extend(
                metric_ref
                for claim in prior_claims
                for metric_ref in claim.get("metric_refs") or []
            )
            metrics = [
                metric_registry[metric_ref]
                for metric_ref in dict.fromkeys(metric_refs)
                if metric_ref in metric_registry
            ]
            messages = section_messages(
                task=task,
                section=section,
                metrics=metrics,
                findings=finding_inputs,
                prior_report_context=prior_report_context,
            )
            if (
                self.fail_once_section_index is not None
                and index == self.fail_once_section_index
                and not self._failure_injected
            ):
                self._failure_injected = True
                raise ReportGenerationError(
                    f"injected one-time failure for section index {index}"
                )
            allowed_evidence = {
                evidence["evidence_id"]
                for finding in finding_inputs
                for evidence in finding["evidence"]
            }
            allowed_findings = set(section_finding_ids)
            allowed_findings.update(
                finding_id
                for claim in prior_claims
                for finding_id in claim.get("finding_ids") or []
            )
            allowed_evidence.update(
                evidence_id
                for claim in prior_claims
                for evidence_id in claim.get("evidence_ids") or []
            )
            evidence_finding_map = {
                evidence["evidence_id"]: finding["finding"]["finding_id"]
                for finding in finding_inputs
                for evidence in finding["evidence"]
            }
            for claim in prior_claims:
                for evidence_id in claim.get("evidence_ids") or []:
                    for finding_id in claim.get("finding_ids") or []:
                        if self._finding_evidence_link_is_valid(finding_id, evidence_id):
                            evidence_finding_map[evidence_id] = finding_id
                            break
            result = self._model_step(
                state=state,
                node_name="draft_sections",
                section_id=section["section_id"],
                messages=messages,
                response_model=SectionDraft,
                semantic_validator=lambda output, current=section, findings=allowed_findings, evidence=allowed_evidence, evidence_owners=evidence_finding_map, allowed_metrics=metrics, prior_context=prior_report_context: self._validate_section_semantics(
                    output,
                    current,
                    findings,
                    evidence,
                    evidence_owners,
                    {item["metric_key"] for item in allowed_metrics},
                    {item["metric_key"]: item for item in allowed_metrics},
                    prior_context,
                ),
            )
            draft = result.output
            draft["purpose"] = section.get("purpose", "")
            draft["section_kind"] = section.get("section_kind", "")
            draft["is_report_category"] = bool(section.get("is_report_category", False))
            draft["body"] = "\n\n".join(
                str(item.get("text") or "").strip()
                for item in draft.get("paragraphs") or []
                if str(item.get("text") or "").strip()
            )
            drafts.append(draft)
        claims = [claim for draft in drafts for claim in draft.get("claims") or []]
        return {"section_drafts": drafts, "claims": claims}

    def _task_overview_for_state(self, state: ReportGraphState) -> Any:
        """Return the task context used by prompts; integrations may freeze it."""
        return self.query_service.get_task_overview(state["task_id"]).data

    def _finding_evidence_link_is_valid(self, finding_id: str, evidence_id: str) -> bool:
        return self.query_service.validate_finding_evidence_link(
            finding_id, evidence_id
        ).data.valid

    def _validate_numbers(self, state: ReportGraphState) -> dict[str, Any]:
        current = self._calculate_statistics(state["task_id"])
        frozen_metrics = {item["metric_key"]: item for item in state["statistics"]["metrics"]}
        current_metrics = {item["metric_key"]: item for item in current["metrics"]}
        errors = []
        for metric_key, frozen in frozen_metrics.items():
            recalculated = current_metrics.get(metric_key)
            if recalculated is None:
                errors.append(f"metric cannot be recalculated: {metric_key}")
                continue
            for field in (
                "metric_name",
                "value",
                "denominator",
                "denominator_name",
                "filters",
                "group",
                "percentage_basis",
            ):
                if recalculated[field] != frozen[field]:
                    errors.append(f"metric changed for {metric_key}: {field}")
        for claim in state["claims"]:
            metric_refs = claim.get("metric_refs") or []
            numbers = self._numbers_in_text(str(claim.get("text") or ""))
            if claim.get("claim_type") == ClaimType.NUMERIC.value and not metric_refs:
                errors.append(f"numeric Claim has no metric_ref: {claim.get('claim_id')}")
            if numbers and not metric_refs:
                errors.append(f"Claim contains unbound numbers: {claim.get('claim_id')}")
                continue
            missing = [item for item in metric_refs if item not in current_metrics]
            if missing:
                errors.append(f"Claim references unknown metrics {missing}: {claim.get('claim_id')}")
                continue
            allowed_numbers = set()
            for metric_ref in metric_refs:
                metric = current_metrics[metric_ref]
                allowed_numbers.update(self._metric_number_forms(metric["value"]))
                allowed_numbers.update(self._metric_number_forms(metric["denominator"]))
            for number in numbers:
                if number.rstrip("%") not in allowed_numbers:
                    errors.append(
                        f"Claim number {number} does not match metric_refs: {claim.get('claim_id')}"
                    )
        for section in state["section_drafts"]:
            allowed_body_numbers = set()
            for claim in section.get("claims") or []:
                for metric_ref in claim.get("metric_refs") or []:
                    metric = current_metrics.get(metric_ref)
                    if metric:
                        allowed_body_numbers.update(self._metric_number_forms(metric["value"]))
                        allowed_body_numbers.update(self._metric_number_forms(metric["denominator"]))
            for number in self._numbers_in_text(str(section.get("body") or "")):
                if number.rstrip("%") not in allowed_body_numbers:
                    errors.append(
                        f"section body contains unbound number {number}: {section.get('section_id')}"
                    )
        if errors:
            raise ReportValidationError("validate_numbers", errors)
        return {"number_validation_errors": []}

    def _validate_claim_support(self, state: ReportGraphState) -> dict[str, Any]:
        snapshot_findings = set(state["source_snapshot"]["finding_ids"])
        errors = []
        for claim in state["claims"]:
            claim_type = claim.get("claim_type")
            finding_ids = list(claim.get("finding_ids") or [])
            evidence_ids = list(claim.get("evidence_ids") or [])
            if claim_type in (ClaimType.DOMAIN_FACT.value, ClaimType.SYNTHESIS.value):
                if not finding_ids:
                    errors.append(f"domain Claim has no Finding: {claim.get('claim_id')}")
                if not evidence_ids:
                    errors.append(f"domain Claim has no Evidence: {claim.get('claim_id')}")
            for finding_id in finding_ids:
                if finding_id not in snapshot_findings:
                    errors.append(f"Claim references Finding outside snapshot: {finding_id}")
                    continue
                try:
                    self.query_service.get_finding_detail(finding_id)
                except Exception as exc:
                    errors.append(f"Claim Finding is unavailable {finding_id}: {exc}")
        if errors:
            raise ReportValidationError("validate_claim_support", errors)
        return {"claim_validation_errors": []}

    def _validate_citations(self, state: ReportGraphState) -> dict[str, Any]:
        snapshot_evidence = set(state["source_snapshot"]["evidence_ids"])
        errors = []
        warnings = list(state["warnings"])
        citation_details: dict[str, dict[str, Any]] = {}
        for claim in state["claims"]:
            finding_ids = list(claim.get("finding_ids") or [])
            for evidence_id in claim.get("evidence_ids") or []:
                if evidence_id not in snapshot_evidence:
                    errors.append(f"Claim references Evidence outside snapshot: {evidence_id}")
                    continue
                valid_links = []
                for finding_id in finding_ids:
                    validation = self.query_service.validate_finding_evidence_link(
                        finding_id, evidence_id
                    )
                    if validation.data.valid:
                        valid_links.append(finding_id)
                if not valid_links:
                    errors.append(
                        f"Evidence is not linked to any Claim Finding: {evidence_id}"
                    )
                    continue
                detail = self.query_service.get_evidence_detail(evidence_id)
                if not detail.data.structured_content_available and not detail.data.asset_available:
                    errors.append(f"Evidence has no usable structured content or asset: {evidence_id}")
                    continue
                warnings.extend(item.model_dump(mode="json") for item in detail.warnings)
                citation_details[evidence_id] = {
                    "finding_ids": valid_links,
                    "citation_excerpt": (
                        detail.data.original_text
                        or detail.data.translated_text
                        or detail.data.summary
                    )[:1000],
                    "structured_content_available": detail.data.structured_content_available,
                    "asset_available": detail.data.asset_available,
                    "asset_status": (
                        "available"
                        if detail.data.asset_available
                        else "not_applicable"
                        if not detail.data.asset_path
                        else "unavailable"
                    ),
                    "availability": detail.data.availability.value,
                }
        if errors:
            raise ReportValidationError("validate_citations", errors)
        return {
            "citation_validation_errors": [],
            "warnings": self._deduplicate_dicts(warnings),
            "citation_details": citation_details,
        }

    def _assemble_report(self, state: ReportGraphState) -> dict[str, Any]:
        title = state["outline"]["report_title"]
        human_report, body_markdown = self.presentation.assemble(state)
        body_json = {
            "human_report": human_report.model_dump(mode="json"),
            "audit_model": {
                "task_id": state["task_id"],
                "source_snapshot_id": state["source_snapshot"]["snapshot_id"],
                "source_snapshot_hash": state["source_snapshot"]["snapshot_hash"],
                "statistics_source_hash": state["statistics"]["source_hash"],
                "metrics": state["statistics"]["metrics"],
                "sections": state["section_drafts"],
                "warnings": state["warnings"],
            },
        }
        return {
            "assembled_report": {
                "title": title,
                "body_markdown": body_markdown,
                "body_json": body_json,
            }
        }

    def _publish_report_version(self, state: ReportGraphState) -> dict[str, Any]:
        validation_errors = (
            state["number_validation_errors"]
            + state["claim_validation_errors"]
            + state["citation_validation_errors"]
        )
        if validation_errors:
            raise ReportValidationError("publish_report_version", validation_errors)
        assembled = state["assembled_report"]
        version = self.store.publish_version(
            report_version_id=state["report_version_id"],
            title=assembled["title"],
            body_markdown=assembled["body_markdown"],
            body_json=assembled["body_json"],
            sections=state["section_drafts"],
            citation_details=state.get("citation_details") or {},
        )
        self.store.update_run(
            state["run_id"], status="completed", current_node="publish_report_version", warnings=state["warnings"]
        )
        assembled["content_hash"] = version["content_hash"]
        return {"assembled_report": assembled}

    def _calculate_statistics(self, task_id: str) -> dict[str, Any]:
        snapshot = self.query_service.get_task_snapshot(task_id).data
        overview = self.query_service.get_task_overview(task_id).data
        metrics: list[dict[str, Any]] = []

        def add_scalar(
            name: str,
            label: str,
            value: int,
            validation_kind: str,
            request: dict[str, Any],
        ) -> None:
            key_payload = {"task_id": task_id, "name": name, "request": request}
            metrics.append(
                ReportMetric(
                    metric_key=f"metric:{stable_hash(key_payload)}",
                    metric_name="scalar",
                    label=label,
                    value=float(value),
                    denominator=int(value),
                    denominator_name=name,
                    source_hash=stable_hash({**key_payload, "value": value}),
                    validation_kind=validation_kind,
                    validation_request=request,
                ).model_dump(mode="json")
            )

        add_scalar(
            "content_count",
            "任务内容总数",
            snapshot.content_count,
            "task_snapshot",
            {"field": "content_count"},
        )
        add_scalar(
            "audit_result_count",
            "审核结果总数",
            snapshot.audit_result_count,
            "task_snapshot",
            {"field": "audit_result_count"},
        )
        add_scalar(
            "findings_with_evidence_count",
            "包含 Evidence 的 Finding 数",
            overview.findings_with_evidence_count,
            "task_overview",
            {"field": "findings_with_evidence_count"},
        )

        requests = (
            (["decision"], ["count", "percentage"]),
            (["risk_level"], ["count", "percentage"]),
            (["primary_risk"], ["count"]),
            (["evidence_type"], ["finding_count", "evidence_count", "percentage"]),
            (["author"], ["count"]),
        )
        for group_by, metric_names in requests:
            aggregation = self.query_service.aggregate_findings(
                task_id, FindingFilters(), group_by, metric_names
            )
            for row in aggregation.data.rows:
                group_text = ", ".join(f"{key}={value}" for key, value in row.group.items())
                metrics.append(
                    ReportMetric(
                        metric_key=row.metric_key,
                        metric_name=row.metric,
                        label=f"{group_text} 的 {row.metric}",
                        value=row.value,
                        denominator=row.denominator,
                        denominator_name=row.denominator_name,
                        group=row.group,
                        filters=row.filters,
                        source_hash=row.source_hash,
                        validation_kind="aggregate_findings",
                        validation_request={
                            "filters": {},
                            "group_by": group_by,
                            "metrics": metric_names,
                        },
                        percentage_basis=row.percentage_basis,
                    ).model_dump(mode="json")
                )
        return {
            "task_overview": overview.model_dump(mode="json"),
            "metrics": metrics,
            "source_hash": stable_hash(
                {
                    "task_source_hash": snapshot.source_hash,
                    "metrics": metrics,
                }
            ),
        }

    def _all_finding_cards(self, task_id: str) -> list[dict[str, Any]]:
        items = []
        page = 1
        while True:
            result = self.query_service.search_findings(
                task_id,
                FindingFilters(),
                Pagination(
                    page=page,
                    page_size=100,
                    sort_by="risk_score",
                    sort_order="desc",
                ),
            ).data
            items.extend(item.model_dump(mode="json") for item in result.items)
            if len(items) >= result.total:
                break
            page += 1
        return items

    def _finding_prompt_input(self, finding_id: str) -> dict[str, Any]:
        detail = self.query_service.get_finding_detail(finding_id)
        evidence_briefs = []
        evidence_details = []
        try:
            evidence = self.query_service.get_finding_evidence(finding_id, limit=100)
        except FindingHasNoEvidenceError:
            evidence = None
        if evidence:
            evidence_briefs = [item.model_dump(mode="json") for item in evidence.data]
            for brief in evidence.data[:2]:
                item = self.query_service.get_evidence_detail(brief.evidence_id).data
                evidence_details.append(
                    {
                        "evidence_id": item.evidence_id,
                        "evidence_type": item.evidence_type.value,
                        "original_text": item.original_text[:1000],
                        "translated_text": item.translated_text[:1000],
                        "summary": item.summary[:1000],
                        "timestamp_start": item.timestamp_start,
                        "timestamp_end": item.timestamp_end,
                        "asset_available": item.asset_available,
                        "availability": item.availability.value,
                    }
                )
        return {
            "finding": detail.data.finding.model_dump(mode="json"),
            "post": detail.data.post.model_dump(mode="json"),
            "risk_basis": detail.data.risk_basis,
            "evidence": evidence_briefs,
            "evidence_detail": evidence_details,
            "warnings": [item.model_dump(mode="json") for item in detail.warnings],
        }

    def _model_step(
        self,
        *,
        state: ReportGraphState,
        node_name: str,
        section_id: str,
        messages: list[dict[str, str]],
        response_model: type,
        semantic_validator: Callable[[dict[str, Any]], list[str]],
        max_tokens: int | None = None,
    ) -> ModelStepResult:
        input_hash = stable_hash(messages)
        fingerprint = stable_hash(
            {
                "report_version_id": state["report_version_id"],
                "node_name": node_name,
                "section_id": section_id,
                "prompt_version": state["prompt_version"],
                "input_hash": input_hash,
                "model": state["model"],
            }
        )
        cached = self.store.get_successful_model_step(fingerprint)
        if cached:
            self.store.record_run_event(
                state["run_id"],
                node_name,
                "model_step_reused",
                {"section_id": section_id, "fingerprint": fingerprint},
            )
            return ModelStepResult(
                output=cached["output"],
                model=cached["model"],
                usage=cached["usage"],
                reused=True,
            )
        try:
            if hasattr(self.model_client, "exchange_context"):
                self.model_client.exchange_context = {
                    "report_version_id": state["report_version_id"],
                    "node_name": node_name,
                    "section_id": section_id,
                }
            result = self.model_client.generate_structured(
                messages=messages,
                response_model=response_model,
                **({"max_tokens": max_tokens} if max_tokens is not None else {}),
            )
            semantic_errors = semantic_validator(result.output)
            if semantic_errors:
                repair_messages = [
                    *messages,
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            self._provider_repair_output(result.output, response_model),
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._provider_repair_message(response_model, semantic_errors),
                    },
                ]
                result = self.model_client.generate_structured(
                    messages=repair_messages,
                    response_model=response_model,
                    **({"max_tokens": max_tokens} if max_tokens is not None else {}),
                )
                semantic_errors = semantic_validator(result.output)
            if semantic_errors:
                raise ReportValidationError(f"{node_name}_semantic", semantic_errors)
        except Exception as exc:
            self.store.save_model_step(
                fingerprint=fingerprint,
                report_version_id=state["report_version_id"],
                node_name=node_name,
                section_id=section_id,
                prompt_version=state["prompt_version"],
                input_hash=input_hash,
                model=state["model"],
                status="failed",
                error=exc,
            )
            raise
        self.store.save_model_step(
            fingerprint=fingerprint,
            report_version_id=state["report_version_id"],
            node_name=node_name,
            section_id=section_id,
            prompt_version=state["prompt_version"],
            input_hash=input_hash,
            model=result.model,
            status="succeeded",
            output=result.output,
            usage=result.usage,
        )
        self.store.record_run_event(
            state["run_id"],
            node_name,
            "model_step_succeeded",
            {"section_id": section_id, "fingerprint": fingerprint},
        )
        return result

    def _provider_repair_output(self, output: dict[str, Any], response_model: type) -> dict[str, Any]:
        """Hook for integrations that must redact private refs from repair input."""
        return output

    def _provider_repair_message(self, response_model: type, errors: list[str]) -> str:
        return (
            "JSON 结构有效，但违反领域约束。请返回完整修正版 JSON，不要解释。"
            "不要补造 Finding、Evidence 或 metric ID。没有来源的事实陈述必须删除。错误："
            + "; ".join(errors)
        )

    def _validate_outline_semantics(
        self, output: dict[str, Any], state: ReportGraphState
    ) -> list[str]:
        allowed_findings = set(state["selected_finding_ids"])
        allowed_metrics = {
            item["metric_key"] for item in state["statistics"]["metrics"]
        }
        errors = []
        section_ids = [item["section_id"] for item in output.get("sections") or []]
        if len(section_ids) != len(set(section_ids)):
            errors.append("section_id values must be unique")
        sections_with_findings = sum(
            bool(item.get("finding_ids")) for item in output.get("sections") or []
        )
        if sections_with_findings < 3:
            errors.append("at least three sections must reference representative Findings")
        if not any(item.get("metric_refs") for item in output.get("sections") or []):
            errors.append("at least one section must reference deterministic metrics")
        section_kinds = [item.get("section_kind") for item in output.get("sections") or []]
        required_kinds = {
            SectionKind.OVERVIEW.value,
            SectionKind.CASE_ANALYSIS.value,
            SectionKind.SYNTHESIS.value,
            SectionKind.CONCLUSION.value,
        }
        missing_kinds = required_kinds - set(section_kinds)
        if missing_kinds:
            errors.append(f"outline is missing required section kinds: {sorted(missing_kinds)}")
        if section_kinds.count(SectionKind.CONCLUSION.value) != 1:
            errors.append("outline must contain exactly one conclusion section")
        if not section_kinds or section_kinds[-1] != SectionKind.CONCLUSION.value:
            errors.append("conclusion must be the final section")
        errors.extend(
            self._reader_text_errors("report title", str(output.get("report_title") or ""))
        )
        for section in output.get("sections") or []:
            invalid_findings = set(section.get("finding_ids") or []) - allowed_findings
            invalid_metrics = set(section.get("metric_refs") or []) - allowed_metrics
            if invalid_findings:
                errors.append(f"section {section['section_id']} has invalid Findings: {sorted(invalid_findings)}")
            if invalid_metrics:
                errors.append(f"section {section['section_id']} has invalid metrics: {sorted(invalid_metrics)}")
            errors.extend(
                self._reader_text_errors(
                    f"section {section['section_id']} title",
                    str(section.get("title") or ""),
                )
            )
            errors.extend(
                self._reader_text_errors(
                    f"section {section['section_id']} purpose",
                    str(section.get("purpose") or ""),
                )
            )
        return errors

    def _validate_section_semantics(
        self,
        output: dict[str, Any],
        section: dict[str, Any],
        allowed_findings: set[str],
        allowed_evidence: set[str],
        evidence_finding_map: dict[str, str],
        allowed_metrics: set[str],
        metric_records: dict[str, dict[str, Any]],
        prior_report_context: dict[str, Any] | None = None,
    ) -> list[str]:
        errors = []
        if output.get("section_id") != section.get("section_id"):
            errors.append("section_id must match the outline")
        if output.get("title") != section.get("title"):
            errors.append("section title must match the outline")
        visible_texts = [("section title", str(output.get("title") or ""))]
        claims = output.get("claims") or []
        claim_ids = [item.get("claim_id") for item in claims]
        if len(claim_ids) != len(set(claim_ids)):
            errors.append("claim_id values must be unique within a section")
        for claim in claims:
            claim_id = claim.get("claim_id")
            visible_texts.append(
                (f"Claim {claim_id} text", str(claim.get("text") or ""))
            )
            invalid_findings = set(claim.get("finding_ids") or []) - allowed_findings
            invalid_evidence = set(claim.get("evidence_ids") or []) - allowed_evidence
            invalid_metrics = set(claim.get("metric_refs") or []) - allowed_metrics
            if invalid_findings:
                errors.append(f"Claim {claim_id} has invalid Findings")
            if invalid_evidence:
                errors.append(f"Claim {claim_id} has invalid Evidence")
            if invalid_metrics:
                errors.append(f"Claim {claim_id} has invalid metrics")
            if claim.get("claim_type") == ClaimType.NUMERIC.value and not claim.get("metric_refs"):
                errors.append(f"numeric Claim {claim_id} requires metric_refs")
            claim_metric_refs = list(claim.get("metric_refs") or [])
            claim_numbers = self._numbers_in_text(str(claim.get("text") or ""))
            if claim_numbers and not claim_metric_refs:
                errors.append(f"Claim {claim_id} contains numbers without metric_refs")
            allowed_number_forms = set()
            for metric_ref in claim_metric_refs:
                metric = metric_records.get(metric_ref)
                if metric:
                    allowed_number_forms.update(self._metric_number_forms(metric["value"]))
                    allowed_number_forms.update(
                        self._metric_number_forms(metric["denominator"])
                    )
            for number in claim_numbers:
                if number.rstrip("%") not in allowed_number_forms:
                    errors.append(
                        f"Claim {claim_id} contains number {number} not backed by metric_refs"
                    )
            if claim.get("claim_type") in (
                ClaimType.DOMAIN_FACT.value,
                ClaimType.SYNTHESIS.value,
            ):
                if not claim.get("finding_ids"):
                    errors.append(f"domain Claim {claim_id} requires Finding")
                if not claim.get("evidence_ids"):
                    errors.append(f"domain Claim {claim_id} requires Evidence")
                for evidence_id in claim.get("evidence_ids") or []:
                    owner = evidence_finding_map.get(evidence_id)
                    if owner and owner not in set(claim.get("finding_ids") or []):
                        errors.append(
                            f"Claim {claim_id} does not cite the Finding that owns {evidence_id}"
                        )
        paragraphs = output.get("paragraphs") or []
        case_blocks = output.get("case_blocks") or []
        section_kind = section.get("section_kind")
        if section_kind == SectionKind.CASE_ANALYSIS.value and not case_blocks:
            errors.append("case_analysis section requires at least one case block")
        if section_kind != SectionKind.CASE_ANALYSIS.value and case_blocks:
            errors.append("only case_analysis sections may contain case blocks")
        claims_by_id = {item.get("claim_id"): item for item in claims}
        body_text = "\n\n".join(
            str(item.get("text") or "").strip() for item in paragraphs
        )
        for paragraph_index, paragraph in enumerate(paragraphs):
            paragraph_label = f"paragraph {paragraph_index + 1}"
            paragraph_text = str(paragraph.get("text") or "")
            visible_texts.append((paragraph_label, paragraph_text))
            paragraph_claim_ids = set(paragraph.get("claim_ids") or [])
            invalid_claim_ids = paragraph_claim_ids - set(claim_ids)
            if invalid_claim_ids:
                errors.append(
                    f"{paragraph_label} references invalid Claim IDs: {sorted(invalid_claim_ids)}"
                )
        for case_index, case in enumerate(case_blocks):
            case_label = f"case block {case_index + 1}"
            visible_texts.extend(
                (
                    (f"{case_label} title", str(case.get("title") or "")),
                    (f"{case_label} text", str(case.get("text") or "")),
                )
            )
            case_claim_ids = list(case.get("claim_ids") or [])
            invalid_claim_ids = set(case_claim_ids) - set(claim_ids)
            if invalid_claim_ids:
                errors.append(
                    f"{case_label} references invalid Claim IDs: {sorted(invalid_claim_ids)}"
                )
            referenced_claims = [
                claims_by_id[item]
                for item in case_claim_ids
                if item in claims_by_id
            ]
            if not any(
                item.get("claim_type")
                in (ClaimType.DOMAIN_FACT.value, ClaimType.SYNTHESIS.value)
                for item in referenced_claims
            ):
                errors.append(f"{case_label} must reference a domain Claim")
            case_number_forms = set()
            for claim in referenced_claims:
                for metric_ref in claim.get("metric_refs") or []:
                    metric = metric_records.get(metric_ref)
                    if metric:
                        case_number_forms.update(
                            self._metric_number_forms(metric["value"])
                        )
                        case_number_forms.update(
                            self._metric_number_forms(metric["denominator"])
                        )
            for number in self._numbers_in_text(str(case.get("text") or "")):
                if number.rstrip("%") not in case_number_forms:
                    errors.append(
                        f"{case_label} contains number {number} not backed by a referenced Claim metric_ref"
                    )
        linked_claim_ids = {
            claim_id
            for paragraph in paragraphs
            for claim_id in paragraph.get("claim_ids") or []
            if claim_id in claims_by_id
        }
        linked_claim_ids.update(
            claim_id
            for case in case_blocks
            for claim_id in case.get("claim_ids") or []
            if claim_id in claims_by_id
        )
        unlinked_claim_ids = set(claim_ids) - linked_claim_ids
        if unlinked_claim_ids:
            errors.append(
                f"Claims are not linked from paragraphs or case blocks: {sorted(unlinked_claim_ids)}"
            )
        if allowed_findings and not any(
            claim.get("claim_type")
            in (ClaimType.DOMAIN_FACT.value, ClaimType.SYNTHESIS.value)
            for claim in claims
        ):
            errors.append("a section with Finding inputs requires at least one domain Claim")
        body_number_forms = set()
        for claim in claims:
            for metric_ref in claim.get("metric_refs") or []:
                metric = metric_records.get(metric_ref)
                if metric:
                    body_number_forms.update(self._metric_number_forms(metric["value"]))
                    body_number_forms.update(
                        self._metric_number_forms(metric["denominator"])
                    )
        for number in self._numbers_in_text(body_text):
            if number.rstrip("%") not in body_number_forms:
                errors.append(
                    f"section body contains number {number} not backed by a Claim metric_ref"
                )
        for label, text in visible_texts:
            errors.extend(self._reader_text_errors(label, text))
            errors.extend(self._human_number_format_errors(label, text))
            if UNEXPLAINED_ENGLISH_PATTERN.search(text):
                errors.append(f"{label} contains unexplained English prose")
            for pattern in UNSUPPORTED_INTENT_PATTERNS:
                if pattern.search(text):
                    errors.append(
                        f"{label} infers publisher intent beyond the supplied evidence"
                    )
                    break
        if section_kind == SectionKind.CONCLUSION.value:
            conclusion_text = body_text
            for pattern in CONCLUSION_ABSENCE_PATTERNS:
                if pattern.search(conclusion_text):
                    errors.append(
                        "conclusion contradicts the supplied findings and prior validated claims"
                    )
                    break
            overview_text = str(
                (prior_report_context or {}).get("overall_findings_summary") or ""
            ).strip()
            if overview_text and self._normalize_prose(conclusion_text) == self._normalize_prose(
                overview_text
            ):
                errors.append("conclusion must synthesize rather than copy the overview")
            if not re.search(r"建议|后续|应当|应重点|需重点|持续关注", conclusion_text):
                errors.append("conclusion must state a concrete follow-up focus")
            if not any(
                claim.get("claim_type")
                in (ClaimType.DOMAIN_FACT.value, ClaimType.SYNTHESIS.value)
                for claim in claims
            ):
                errors.append("conclusion requires at least one domain Claim")
        return errors

    @staticmethod
    def _normalize_prose(value: str) -> str:
        return re.sub(r"\s+", "", value).strip()

    @staticmethod
    def _reader_text_errors(label: str, value: str) -> list[str]:
        return [
            f"{label} exposes an internal report token: {pattern.pattern}"
            for pattern in FORBIDDEN_PRESENTATION_PATTERNS
            if pattern.search(value)
        ]

    @staticmethod
    def _human_number_format_errors(label: str, value: str) -> list[str]:
        errors = []
        for match in re.finditer(r"(?<![A-Za-z0-9_:])\d+\.(\d+)%?", value):
            decimals = match.group(1)
            if len(decimals) > 1 or set(decimals) == {"0"}:
                errors.append(
                    f"{label} contains a non-human numeric format: {match.group(0)}"
                )
        return errors

    @staticmethod
    def _numbers_in_text(value: str) -> list[str]:
        return re.findall(r"(?<![A-Za-z0-9_:])\d+(?:\.\d+)?%?", value)

    @staticmethod
    def _metric_number_forms(value: float | int) -> set[str]:
        numeric = float(value)
        forms = {str(numeric), f"{numeric:.1f}", f"{numeric:.2f}", f"{numeric:.6f}"}
        if numeric.is_integer():
            forms.add(str(int(numeric)))
        normalized = {
            item.rstrip("0").rstrip(".") if "." in item else item for item in forms
        }
        return forms | normalized


    @staticmethod
    def _deduplicate_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        seen = set()
        for value in values:
            key = stable_hash(value)
            if key not in seen:
                seen.add(key)
                output.append(value)
        return output

    def _result_from_state(self, state: ReportGraphState) -> ReportGenerationResult:
        version = self.store.get_version(state["report_version_id"])
        if version is None:
            raise ReportGenerationError("report version disappeared after generation")
        run = self.store.get_run(state["run_id"]) or {}
        return self._result_from_version(run, version)

    @staticmethod
    def _result_from_version(
        run: dict[str, Any], version: dict[str, Any]
    ) -> ReportGenerationResult:
        return ReportGenerationResult(
            run_id=str(run.get("id") or version.get("generation_run_id") or ""),
            report_id=str(version["report_id"]),
            report_version_id=str(version["id"]),
            version_number=int(version["version_number"]),
            task_id=str(run.get("task_id") or ""),
            status=str(version["status"]),
            title=str(version["title"]),
            content_hash=str(version["content_hash"]),
            body_markdown=str(version["body_markdown"]),
            warnings=tuple(run.get("warnings") or []),
        )
