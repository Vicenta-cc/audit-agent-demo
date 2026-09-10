from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from pydantic import model_validator

from backend.domain.identity import stable_hash
from backend.investigation.context import InvestigationContextBuilder
from backend.investigation.contracts import (
    AcquisitionRequired,
    BoundEvidenceCollectionRequirement,
    BoundEvidenceDetailRequirement,
    BoundRequirement,
    FindingEvidenceCollectionTurnPlan,
    InvestigationSession,
    PlannerShadowTrace,
    ProjectionTokenAccounting,
    QueryArtifactIndexEntry,
    QueryResultArtifact,
    ReadySourceBundle,
    ResolvedReference,
    SourceArtifact,
    SourceCoverageProof,
    SourceFailure,
    SourceKind,
    SourceLedgerCandidate,
    SourcePreparationDecision,
    SourcePreparationShadowTrace,
    StrictModel,
    TurnPlan,
    UnsupportedCapacity,
    UnsupportedComplete,
)
from backend.investigation.store import InvestigationStore


SUBJECT_BINDER_VERSION = "subject-binder-v1"
SOURCE_ORCHESTRATOR_VERSION = "source-orchestrator-shadow-v1"
PROJECTION_POLICY_VERSION = "evidence-artifact-json-v1"


class SubjectBindingDecision(StrictModel):
    status: Literal["not_applicable", "need_subject", "bound"]
    requirement_kind: str
    bound_requirement: BoundRequirement | None = None
    reason: str

    @model_validator(mode="after")
    def validate_decision(self):
        if (self.status == "bound") != (self.bound_requirement is not None):
            raise ValueError("only a bound decision may contain a requirement")
        return self


class SubjectBinder:
    """Bind Planner requirements using only existing referent and focus state."""

    version = SUBJECT_BINDER_VERSION

    def bind(
        self,
        *,
        plan: TurnPlan,
        session: InvestigationSession,
        resolved_references: Iterable[ResolvedReference],
    ) -> SubjectBindingDecision:
        requirement_kind = plan.requirement_kind
        if requirement_kind not in {
            "finding_evidence_collection",
            "evidence_detail",
        }:
            return SubjectBindingDecision(
                status="not_applicable",
                requirement_kind=requirement_kind,
                reason="requirement_not_managed_by_evidence_orchestrator",
            )

        expected_type = (
            "finding"
            if requirement_kind == "finding_evidence_collection"
            else "evidence"
        )
        references = tuple(resolved_references)
        if any(item.status == "unresolved" for item in references):
            return SubjectBindingDecision(
                status="need_subject",
                requirement_kind=requirement_kind,
                reason="explicit_subject_unresolved",
            )
        all_resolved = tuple(
            dict.fromkeys(
                item.target_id
                for item in references
                if item.status == "resolved" and item.target_id
            )
        )
        resolved = tuple(
            dict.fromkeys(
                item.target_id
                for item in references
                if item.status == "resolved"
                and item.target_type == expected_type
                and item.target_id
            )
        )
        if all_resolved and not resolved:
            return SubjectBindingDecision(
                status="need_subject",
                requirement_kind=requirement_kind,
                reason=f"resolved_subject_is_not_{expected_type}",
            )
        if len(resolved) > 1:
            return SubjectBindingDecision(
                status="need_subject",
                requirement_kind=requirement_kind,
                reason="multiple_resolved_subjects",
            )
        subject_ref = resolved[0] if resolved else ""
        if not subject_ref and session.active_focus.get("type") == expected_type:
            subject_ref = str(session.active_focus.get("target_id") or "")
        if not subject_ref:
            subject_ref = (
                session.last_finding_id
                if expected_type == "finding"
                else session.last_evidence_id
            )
        if not subject_ref:
            return SubjectBindingDecision(
                status="need_subject",
                requirement_kind=requirement_kind,
                reason=f"no_resolved_{expected_type}_subject",
            )

        scope = {
            "session_id": session.id,
            "report_version_id": session.report_version_id,
            "source_snapshot_id": session.source_snapshot_id,
            "snapshot_hash": session.snapshot_hash,
        }
        try:
            if isinstance(plan, FindingEvidenceCollectionTurnPlan):
                bound: BoundRequirement = BoundEvidenceCollectionRequirement(
                    **scope,
                    requirement_kind="finding_evidence_collection",
                    finding_ref=subject_ref,
                    evidence_types=plan.evidence_types,
                    coverage=plan.coverage,
                )
            else:
                bound = BoundEvidenceDetailRequirement(
                    **scope,
                    requirement_kind="evidence_detail",
                    evidence_ref=subject_ref,
                )
        except ValueError:
            return SubjectBindingDecision(
                status="need_subject",
                requirement_kind=requirement_kind,
                reason=f"invalid_{expected_type}_subject",
            )
        return SubjectBindingDecision(
            status="bound",
            requirement_kind=requirement_kind,
            bound_requirement=bound,
            reason="subject_bound_from_existing_focus_state",
        )


@dataclass(frozen=True)
class PreparationObservation:
    decision: SourcePreparationDecision
    matched_query_result_artifact_ids: tuple[str, ...]
    selected_query_result_artifact_id: str | None


class SourceOrchestrator:
    """Prepare Evidence bundles from immutable Artifacts."""

    version = SOURCE_ORCHESTRATOR_VERSION

    def __init__(
        self,
        store: InvestigationStore,
        *,
        subject_binder: SubjectBinder | None = None,
        source_token_budget: int = 12_000,
        projection_policy_version: str = PROJECTION_POLICY_VERSION,
    ) -> None:
        self.store = store
        self.subject_binder = subject_binder or SubjectBinder()
        self.source_token_budget = max(1_000, int(source_token_budget))
        self.projection_policy_version = str(
            projection_policy_version or PROJECTION_POLICY_VERSION
        )

    def prepare(
        self,
        requirement: BoundRequirement,
        *,
        supplemental_requirements: Iterable[
            BoundEvidenceDetailRequirement
        ] = (),
        parent_bundle_fingerprint: str | None = None,
    ) -> SourcePreparationDecision:
        """Return the stable public source-preparation result union."""
        observation = self._prepare_with_observation(requirement)
        supplemental = tuple(supplemental_requirements)
        if not supplemental or not isinstance(
            observation.decision, ReadySourceBundle
        ):
            return observation.decision
        try:
            return self._expand_ready_bundle(
                requirement=requirement,
                base_bundle=observation.decision,
                supplemental_requirements=supplemental,
                parent_bundle_fingerprint=str(parent_bundle_fingerprint or ""),
            )
        except Exception:
            return SourceFailure(
                status="source_failure",
                bound_requirement=requirement,
                error_code="controlled_react_materialization_failure",
                safe_message="受控 Evidence 扩展无法确定性恢复。",
                retryable=False,
            )

    def ledger_candidates(
        self, bundle: ReadySourceBundle
    ) -> tuple[SourceLedgerCandidate, ...]:
        """Restore current-source audit candidates from the exact projected Artifacts."""
        requirement = bundle.bound_requirement
        finding_ref = (
            requirement.finding_ref
            if isinstance(requirement, BoundEvidenceCollectionRequirement)
            else ""
        )
        candidates = []
        for artifact_id in bundle.source_artifact_ids:
            source = self.store.get_source_artifact(artifact_id)
            if source is None:
                raise ValueError("ReadySourceBundle source artifact is missing")
            metadata = source.observation_metadata
            excerpt = json.dumps(
                source.canonical_content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )[:1600]
            candidates.append(
                SourceLedgerCandidate(
                    source_kind=SourceKind.CURRENT_EVIDENCE,
                    finding_id=(
                        finding_ref
                        or str(source.canonical_content.get("finding_id") or "")
                    ),
                    evidence_id=source.stable_source_ref,
                    source_hash=str(
                        metadata.get("source_hash") or source.source_fingerprint
                    ),
                    excerpt=excerpt,
                    asset_status=str(metadata.get("asset_status") or ""),
                    query_fingerprint=str(
                        metadata.get("query_fingerprint")
                        or bundle.query_fingerprints[0]
                    ),
                    tool_call_id=str(metadata.get("tool_call_id") or ""),
                    query_receipt_id=str(metadata.get("receipt_id") or ""),
                    warnings=tuple(
                        str(item) for item in metadata.get("warnings") or ()
                    ),
                    freshness="current_source",
                )
            )
        return tuple(candidates)

    def _prepare_with_observation(
        self, requirement: BoundRequirement
    ) -> PreparationObservation:
        try:
            matches = self._matching_artifacts(requirement)
        except Exception:
            return PreparationObservation(
                decision=SourceFailure(
                    status="source_failure",
                    bound_requirement=requirement,
                    error_code="artifact_index_failure",
                    safe_message="Source Artifact 索引未通过完整性校验。",
                    retryable=False,
                ),
                matched_query_result_artifact_ids=(),
                selected_query_result_artifact_id=None,
            )
        matched_ids = tuple(item.query_result_artifact_id for item in matches)
        if not matches:
            return PreparationObservation(
                decision=self._acquisition_required(requirement),
                matched_query_result_artifact_ids=(),
                selected_query_result_artifact_id=None,
            )
        selected = max(
            matches,
            key=lambda item: (
                item.observed_at,
                item.query_result_artifact_id,
            ),
        )
        try:
            query_result = self.store.get_query_result_artifact(
                selected.query_result_artifact_id
            )
            if query_result is None:
                raise ValueError("indexed query result is missing")
            sources = self._materialize_sources(requirement, query_result)
            decision = self._prepare_selected(requirement, query_result, sources)
        except Exception:
            decision = SourceFailure(
                status="source_failure",
                bound_requirement=requirement,
                error_code="artifact_materialization_failure",
                safe_message="Source Artifact 无法确定性恢复。",
                retryable=False,
            )
        return PreparationObservation(
            decision=decision,
            matched_query_result_artifact_ids=matched_ids,
            selected_query_result_artifact_id=selected.query_result_artifact_id,
        )

    def observe_shadow(
        self,
        *,
        session: InvestigationSession,
        turn_id: str,
        planner_trace: PlannerShadowTrace,
        resolved_references: Iterable[ResolvedReference],
    ) -> SourcePreparationShadowTrace:
        resolved = tuple(resolved_references)
        created_at = datetime.now(timezone.utc).isoformat()
        input_fingerprint = stable_hash(
            {
                "session_id": session.id,
                "turn_id": turn_id,
                "scope": {
                    "report_version_id": session.report_version_id,
                    "source_snapshot_id": session.source_snapshot_id,
                    "snapshot_hash": session.snapshot_hash,
                },
                "planner_trace_id": planner_trace.trace_id,
                "plan": (
                    planner_trace.plan.model_dump(mode="json")
                    if planner_trace.plan
                    else None
                ),
                "active_focus": session.active_focus,
                "last_finding_id": session.last_finding_id,
                "last_evidence_id": session.last_evidence_id,
                "resolved_references": [
                    item.model_dump(mode="json") for item in resolved
                ],
                "subject_binder_version": self.subject_binder.version,
                "orchestrator_version": self.version,
                "projection_policy_version": self.projection_policy_version,
            }
        )
        base = {
            "trace_id": self._trace_id(turn_id),
            "session_id": session.id,
            "turn_id": turn_id,
            "report_version_id": session.report_version_id,
            "source_snapshot_id": session.source_snapshot_id,
            "snapshot_hash": session.snapshot_hash,
            "planner_trace_id": planner_trace.trace_id,
            "planner_prompt_version": planner_trace.planner_prompt_version,
            "subject_binder_version": self.subject_binder.version,
            "orchestrator_version": self.version,
            "input_fingerprint": input_fingerprint,
            "requirement_kind": (
                planner_trace.plan.requirement_kind
                if planner_trace.plan
                else "none"
            ),
            "created_at": created_at,
        }
        if planner_trace.planning_status != "ok" or planner_trace.plan is None:
            return SourcePreparationShadowTrace(
                **base,
                binding_status="not_applicable",
                status="planner_error",
                error_code=planner_trace.error_code or "planner_error",
                error_message=planner_trace.error_message or "Planner 未生成可用计划。",
            )
        try:
            binding = self.subject_binder.bind(
                plan=planner_trace.plan,
                session=session,
                resolved_references=resolved,
            )
            if binding.status == "not_applicable":
                return SourcePreparationShadowTrace(
                    **base,
                    binding_status="not_applicable",
                    status="not_applicable",
                    reason=binding.reason,
                )
            if binding.status == "need_subject":
                return SourcePreparationShadowTrace(
                    **base,
                    binding_status="need_subject",
                    status="need_subject",
                    reason=binding.reason,
                )
            observation = self._prepare_with_observation(binding.bound_requirement)
            decision = observation.decision
            return SourcePreparationShadowTrace(
                **base,
                binding_status="bound",
                status=decision.status,
                bound_requirement=binding.bound_requirement,
                preparation_result=decision,
                matched_query_result_artifact_ids=(
                    observation.matched_query_result_artifact_ids
                ),
                selected_query_result_artifact_id=(
                    observation.selected_query_result_artifact_id
                ),
                reason=self._decision_reason(decision),
            )
        except Exception:
            return SourcePreparationShadowTrace(
                **base,
                binding_status="error",
                status="error",
                error_code="source_orchestrator_unexpected_error",
                error_message="Source Orchestrator shadow 执行失败。",
            )

    def _matching_artifacts(
        self, requirement: BoundRequirement
    ) -> tuple[QueryArtifactIndexEntry, ...]:
        operation = (
            "finding_evidence_list"
            if isinstance(requirement, BoundEvidenceCollectionRequirement)
            else "evidence_detail_read"
        )
        subject_ref = (
            requirement.finding_ref
            if isinstance(requirement, BoundEvidenceCollectionRequirement)
            else requirement.evidence_ref
        )
        expected_types = (
            self._normalized_types(requirement.evidence_types)
            if isinstance(requirement, BoundEvidenceCollectionRequirement)
            else ()
        )
        entries = self.store.list_query_artifact_index(
            session_id=requirement.session_id,
            report_version_id=requirement.report_version_id,
            snapshot_hash=requirement.snapshot_hash,
            operation=operation,
            limit=100,
        )
        output = []
        for entry in entries:
            if (
                entry.source_snapshot_id != requirement.source_snapshot_id
                or entry.subject_ref != subject_ref
            ):
                continue
            if isinstance(requirement, BoundEvidenceCollectionRequirement):
                artifact_types = self._normalized_types(
                    entry.normalized_filters.get("evidence_types") or ()
                )
                query_types = self._normalized_types(
                    entry.normalized_query.get("evidence_types") or ()
                )
                if artifact_types != expected_types or query_types != expected_types:
                    continue
            elif entry.normalized_query != {"evidence_id": subject_ref}:
                continue
            output.append(entry)
        return tuple(output)

    def _prepare_selected(
        self,
        requirement: BoundRequirement,
        query_result: QueryResultArtifact,
        sources: tuple[SourceArtifact, ...],
    ) -> SourcePreparationDecision:
        acquisition = self._acquisition_completeness(query_result)
        if isinstance(requirement, BoundEvidenceCollectionRequirement):
            if requirement.coverage == "complete" and acquisition != "complete":
                return UnsupportedComplete(
                    status="unsupported_complete",
                    bound_requirement=requirement,
                    reason=(
                        "tool_not_pageable"
                        if query_result.has_more is True
                        and not query_result.next_cursor
                        else "acquisition_incomplete"
                    ),
                )
            return self._project_collection(
                requirement, query_result, sources, acquisition
            )
        return self._project_detail(requirement, query_result, sources)

    def _project_collection(
        self,
        requirement: BoundEvidenceCollectionRequirement,
        query_result: QueryResultArtifact,
        sources: tuple[SourceArtifact, ...],
        acquisition: Literal["complete", "partial", "unknown"],
    ) -> SourcePreparationDecision:
        selected: list[SourceArtifact] = []
        for source in sources:
            candidate = [*selected, source]
            if self._projected_tokens(requirement, query_result, candidate) > (
                self.source_token_budget
            ):
                break
            selected = candidate
        if sources and not selected:
            return UnsupportedCapacity(
                status="unsupported_capacity",
                bound_requirement=requirement,
                capacity_kind="context_capacity",
                reason="单个 collection source 超过 Source Bundle token budget。",
            )
        projection_truncated = len(selected) < len(sources)
        if requirement.coverage == "complete" and projection_truncated:
            return UnsupportedComplete(
                status="unsupported_complete",
                bound_requirement=requirement,
                reason="projection_incomplete",
            )
        return self._ready_bundle(
            requirement=requirement,
            query_result=query_result,
            projected_sources=tuple(selected),
            acquisition=acquisition,
            projection_truncated=projection_truncated,
        )

    def _project_detail(
        self,
        requirement: BoundEvidenceDetailRequirement,
        query_result: QueryResultArtifact,
        sources: tuple[SourceArtifact, ...],
    ) -> SourcePreparationDecision:
        if (
            len(sources) != 1
            or sources[0].content_level != "evidence_detail"
            or self._projected_tokens(requirement, query_result, list(sources))
            > self.source_token_budget
        ):
            return UnsupportedCapacity(
                status="unsupported_capacity",
                bound_requirement=requirement,
                capacity_kind="context_capacity",
                reason="Evidence detail 无法完整投影到 Source Bundle。",
            )
        return self._ready_bundle(
            requirement=requirement,
            query_result=query_result,
            projected_sources=sources,
            acquisition="complete",
            projection_truncated=False,
        )

    def _ready_bundle(
        self,
        *,
        requirement: BoundRequirement,
        query_result: QueryResultArtifact,
        projected_sources: tuple[SourceArtifact, ...],
        acquisition: Literal["complete", "partial", "unknown"],
        projection_truncated: bool,
    ) -> ReadySourceBundle:
        payload = self._projection_payload(
            requirement, query_result, list(projected_sources)
        )
        projected_message = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_tokens = InvestigationContextBuilder.estimate_tokens(payload)
        coverage = None
        if isinstance(requirement, BoundEvidenceCollectionRequirement):
            coverage_payload = {
                "requested_coverage": requirement.coverage,
                "returned_count": query_result.returned_count,
                "total": query_result.total,
                "has_more": query_result.has_more,
                "acquisition_completeness": acquisition,
                "query_result_artifact_id": query_result.artifact_id,
            }
            coverage = SourceCoverageProof(
                requested_coverage=requirement.coverage,
                returned_count=query_result.returned_count,
                total=query_result.total,
                has_more=query_result.has_more,
                acquisition_completeness=acquisition,
                proof_fingerprint=stable_hash(coverage_payload),
            )
            answer_scope = (
                "collection_complete"
                if requirement.coverage == "complete"
                else "collection_discovery"
            )
        else:
            answer_scope = "evidence_detail"
        identity = {
            "schema_version": "ready-source-bundle-v1",
            "bound_requirement": requirement.model_dump(mode="json"),
            "query_result_artifact_ids": [query_result.artifact_id],
            "source_artifact_ids": [
                item.artifact_id for item in projected_sources
            ],
            "ordered_projected_refs": [
                item.stable_source_ref for item in projected_sources
            ],
            "projected_source_message": projected_message,
            "coverage_proof": (
                coverage.model_dump(mode="json") if coverage else None
            ),
            "acquisition_completeness": acquisition,
            "answer_scope": answer_scope,
            "projection_truncated": projection_truncated,
            "query_fingerprints": [query_result.query_fingerprint],
            "result_fingerprints": [query_result.result_fingerprint],
            "projection_policy_version": self.projection_policy_version,
            "token_accounting": {
                "source_tokens": source_tokens,
                "source_token_budget": self.source_token_budget,
            },
        }
        return ReadySourceBundle(
            bound_requirement=requirement,
            query_result_artifact_ids=(query_result.artifact_id,),
            source_artifact_ids=tuple(
                item.artifact_id for item in projected_sources
            ),
            ordered_projected_refs=tuple(
                item.stable_source_ref for item in projected_sources
            ),
            projected_source_message=projected_message,
            coverage_proof=coverage,
            acquisition_completeness=acquisition,
            answer_scope=answer_scope,
            projection_truncated=projection_truncated,
            query_fingerprints=(query_result.query_fingerprint,),
            result_fingerprints=(query_result.result_fingerprint,),
            bundle_fingerprint=stable_hash(identity),
            projection_policy_version=self.projection_policy_version,
            token_accounting=ProjectionTokenAccounting(
                source_tokens=source_tokens,
                source_token_budget=self.source_token_budget,
            ),
        )

    def _expand_ready_bundle(
        self,
        *,
        requirement: BoundRequirement,
        base_bundle: ReadySourceBundle,
        supplemental_requirements: tuple[BoundEvidenceDetailRequirement, ...],
        parent_bundle_fingerprint: str,
    ) -> SourcePreparationDecision:
        if (
            not isinstance(requirement, BoundEvidenceCollectionRequirement)
            or base_bundle.schema_version != "ready-source-bundle-v1"
            or not parent_bundle_fingerprint
            or len(supplemental_requirements) > 2
        ):
            raise ValueError("invalid Controlled ReAct expansion request")
        supplemental_refs = tuple(
            item.evidence_ref for item in supplemental_requirements
        )
        if (
            len(supplemental_refs) != len(set(supplemental_refs))
            or not set(supplemental_refs).issubset(
                base_bundle.ordered_projected_refs
            )
        ):
            raise ValueError("supplemental Evidence is outside the base Bundle")
        expected_parent_fingerprint = base_bundle.bundle_fingerprint
        if len(supplemental_requirements) == 2:
            previous = self._expand_ready_bundle(
                requirement=requirement,
                base_bundle=base_bundle,
                supplemental_requirements=supplemental_requirements[:1],
                parent_bundle_fingerprint=base_bundle.bundle_fingerprint,
            )
            if not isinstance(previous, ReadySourceBundle):
                raise ValueError("previous Controlled ReAct Bundle cannot be rebuilt")
            expected_parent_fingerprint = previous.bundle_fingerprint
        if parent_bundle_fingerprint != expected_parent_fingerprint:
            raise ValueError("Controlled ReAct parent Bundle fingerprint mismatch")

        expected_scope = (
            requirement.session_id,
            requirement.report_version_id,
            requirement.source_snapshot_id,
            requirement.snapshot_hash,
        )
        primary_result = self.store.get_query_result_artifact(
            base_bundle.query_result_artifact_ids[0]
        )
        if primary_result is None:
            raise ValueError("base Bundle query result is missing")
        projected_by_ref: dict[str, SourceArtifact] = {}
        for artifact_id in base_bundle.source_artifact_ids:
            source = self.store.get_source_artifact(artifact_id)
            if source is None:
                raise ValueError("base Bundle source artifact is missing")
            projected_by_ref[source.stable_source_ref] = source

        detail_results: list[QueryResultArtifact] = []
        for detail_requirement in supplemental_requirements:
            detail_scope = (
                detail_requirement.session_id,
                detail_requirement.report_version_id,
                detail_requirement.source_snapshot_id,
                detail_requirement.snapshot_hash,
            )
            if detail_scope != expected_scope:
                raise ValueError("supplemental requirement scope mismatch")
            matches = self._matching_artifacts(detail_requirement)
            if not matches:
                raise ValueError("supplemental detail artifact is missing")
            selected = max(
                matches,
                key=lambda item: (
                    item.observed_at,
                    item.query_result_artifact_id,
                ),
            )
            detail_result = self.store.get_query_result_artifact(
                selected.query_result_artifact_id
            )
            if detail_result is None:
                raise ValueError("supplemental query result is missing")
            detail_sources = self._materialize_sources(
                detail_requirement, detail_result
            )
            if (
                len(detail_sources) != 1
                or detail_sources[0].content_level != "evidence_detail"
                or detail_sources[0].stable_source_ref
                != detail_requirement.evidence_ref
            ):
                raise ValueError("supplemental detail source is invalid")
            projected_by_ref[detail_requirement.evidence_ref] = detail_sources[0]
            detail_results.append(detail_result)

        projected_sources = tuple(
            projected_by_ref[ref]
            for ref in base_bundle.ordered_projected_refs
            if ref in projected_by_ref
        )
        if len(projected_sources) != len(base_bundle.ordered_projected_refs):
            raise ValueError("expanded Bundle lost a projected Evidence source")
        query_results = (primary_result, *detail_results)
        payload = self._expanded_projection_payload(
            requirement=requirement,
            query_results=query_results,
            supplemental_requirements=supplemental_requirements,
            sources=projected_sources,
        )
        projected_message = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_tokens = InvestigationContextBuilder.estimate_tokens(payload)
        if source_tokens > self.source_token_budget:
            return UnsupportedCapacity(
                status="unsupported_capacity",
                bound_requirement=requirement,
                capacity_kind="context_capacity",
                reason="扩展后的 Evidence Source Bundle 超过 token budget。",
            )
        identity = {
            "schema_version": "ready-source-bundle-v2",
            "bound_requirement": requirement.model_dump(mode="json"),
            "supplemental_requirements": [
                item.model_dump(mode="json")
                for item in supplemental_requirements
            ],
            "parent_bundle_fingerprint": parent_bundle_fingerprint,
            "react_iteration_count": len(supplemental_requirements),
            "query_result_artifact_ids": [item.artifact_id for item in query_results],
            "source_artifact_ids": [item.artifact_id for item in projected_sources],
            "ordered_projected_refs": list(base_bundle.ordered_projected_refs),
            "projected_source_message": projected_message,
            "coverage_proof": (
                base_bundle.coverage_proof.model_dump(mode="json")
                if base_bundle.coverage_proof
                else None
            ),
            "acquisition_completeness": base_bundle.acquisition_completeness,
            "answer_scope": base_bundle.answer_scope,
            "projection_truncated": base_bundle.projection_truncated,
            "query_fingerprints": [item.query_fingerprint for item in query_results],
            "result_fingerprints": [item.result_fingerprint for item in query_results],
            "projection_policy_version": self.projection_policy_version,
            "token_accounting": {
                "source_tokens": source_tokens,
                "source_token_budget": self.source_token_budget,
            },
        }
        return ReadySourceBundle(
            schema_version="ready-source-bundle-v2",
            bound_requirement=requirement,
            supplemental_requirements=supplemental_requirements,
            parent_bundle_fingerprint=parent_bundle_fingerprint,
            react_iteration_count=len(supplemental_requirements),
            query_result_artifact_ids=tuple(
                item.artifact_id for item in query_results
            ),
            source_artifact_ids=tuple(
                item.artifact_id for item in projected_sources
            ),
            ordered_projected_refs=base_bundle.ordered_projected_refs,
            projected_source_message=projected_message,
            coverage_proof=base_bundle.coverage_proof,
            acquisition_completeness=base_bundle.acquisition_completeness,
            answer_scope=base_bundle.answer_scope,
            projection_truncated=base_bundle.projection_truncated,
            query_fingerprints=tuple(
                item.query_fingerprint for item in query_results
            ),
            result_fingerprints=tuple(
                item.result_fingerprint for item in query_results
            ),
            bundle_fingerprint=stable_hash(identity),
            projection_policy_version=self.projection_policy_version,
            token_accounting=ProjectionTokenAccounting(
                source_tokens=source_tokens,
                source_token_budget=self.source_token_budget,
            ),
        )

    def _projected_tokens(
        self,
        requirement: BoundRequirement,
        query_result: QueryResultArtifact,
        sources: list[SourceArtifact],
    ) -> int:
        return InvestigationContextBuilder.estimate_tokens(
            self._projection_payload(requirement, query_result, sources)
        )

    @staticmethod
    def _projection_payload(
        requirement: BoundRequirement,
        query_result: QueryResultArtifact,
        sources: list[SourceArtifact],
    ) -> dict[str, Any]:
        return {
            "schema_version": "ready-source-projection-v1",
            "requirement": {
                "requirement_kind": requirement.requirement_kind,
                "evidence_types": (
                    list(requirement.evidence_types or ())
                    if isinstance(requirement, BoundEvidenceCollectionRequirement)
                    else None
                ),
                "coverage": (
                    requirement.coverage
                    if isinstance(requirement, BoundEvidenceCollectionRequirement)
                    else None
                ),
            },
            "query_result": {
                "operation": query_result.operation,
                "subject_ref": query_result.subject_ref,
                "normalized_filters": query_result.normalized_filters,
                "returned_count": query_result.returned_count,
                "total": query_result.total,
                "has_more": query_result.has_more,
            },
            "sources": [
                {
                    "stable_source_ref": source.stable_source_ref,
                    "source_type": source.source_type,
                    "content_level": source.content_level,
                    "canonical_content": source.canonical_content,
                }
                for source in sources
            ],
        }

    @staticmethod
    def _expanded_projection_payload(
        *,
        requirement: BoundEvidenceCollectionRequirement,
        query_results: tuple[QueryResultArtifact, ...],
        supplemental_requirements: tuple[BoundEvidenceDetailRequirement, ...],
        sources: tuple[SourceArtifact, ...],
    ) -> dict[str, Any]:
        return {
            "schema_version": "ready-source-projection-v2",
            "requirement": {
                "requirement_kind": requirement.requirement_kind,
                "evidence_types": list(requirement.evidence_types or ()),
                "coverage": requirement.coverage,
            },
            "query_results": [
                {
                    "role": "primary_collection" if index == 0 else "detail_expansion",
                    "operation": query_result.operation,
                    "subject_ref": query_result.subject_ref,
                    "normalized_filters": query_result.normalized_filters,
                    "returned_count": query_result.returned_count,
                    "total": query_result.total,
                    "has_more": query_result.has_more,
                }
                for index, query_result in enumerate(query_results)
            ],
            "controlled_react": {
                "expanded_evidence_refs": [
                    item.evidence_ref for item in supplemental_requirements
                ]
            },
            "sources": [
                {
                    "stable_source_ref": source.stable_source_ref,
                    "source_type": source.source_type,
                    "content_level": source.content_level,
                    "canonical_content": source.canonical_content,
                }
                for source in sources
            ],
        }

    def _materialize_sources(
        self,
        requirement: BoundRequirement,
        query_result: QueryResultArtifact,
    ) -> tuple[SourceArtifact, ...]:
        expected_scope = (
            requirement.session_id,
            requirement.report_version_id,
            requirement.source_snapshot_id,
            requirement.snapshot_hash,
        )
        if (
            query_result.session_id,
            query_result.report_version_id,
            query_result.source_snapshot_id,
            query_result.snapshot_hash,
        ) != expected_scope:
            raise ValueError("query result scope does not match bound requirement")
        sources = []
        for member in query_result.ordered_members:
            source = self.store.get_source_artifact(member.source_artifact_id)
            if source is None:
                raise ValueError("query result source artifact is missing")
            if (
                source.session_id,
                source.report_version_id,
                source.source_snapshot_id,
                source.snapshot_hash,
            ) != expected_scope:
                raise ValueError("source artifact scope does not match bound requirement")
            if (
                source.stable_source_ref != member.stable_source_ref
                or source.content_level != member.content_level
            ):
                raise ValueError("source artifact does not match query result member")
            if (
                isinstance(requirement, BoundEvidenceCollectionRequirement)
                and requirement.evidence_types
                and source.source_type not in requirement.evidence_types
            ):
                raise ValueError("source artifact does not match Evidence type filter")
            sources.append(source)
        return tuple(sources)

    @staticmethod
    def _acquisition_required(requirement: BoundRequirement) -> AcquisitionRequired:
        if isinstance(requirement, BoundEvidenceCollectionRequirement):
            return AcquisitionRequired(
                status="acquisition_required",
                bound_requirement=requirement,
                required_tool="list_finding_evidence",
                required_arguments={
                    "finding_id": requirement.finding_ref,
                    "evidence_types": list(requirement.evidence_types or ()),
                    "limit": 20,
                },
            )
        return AcquisitionRequired(
            status="acquisition_required",
            bound_requirement=requirement,
            required_tool="read_evidence_detail",
            required_arguments={"evidence_id": requirement.evidence_ref},
        )

    @staticmethod
    def _acquisition_completeness(
        query_result: QueryResultArtifact,
    ) -> Literal["complete", "partial", "unknown"]:
        if (
            query_result.has_more is False
            and query_result.total is not None
            and query_result.returned_count == query_result.total
        ):
            return "complete"
        if query_result.has_more is True or (
            query_result.total is not None
            and query_result.returned_count < query_result.total
        ):
            return "partial"
        return "unknown"

    @staticmethod
    def _normalized_types(values: Iterable[str] | None) -> tuple[str, ...]:
        return tuple(sorted(set(str(item) for item in (values or ()) if item)))

    @staticmethod
    def _decision_reason(decision: SourcePreparationDecision) -> str:
        if isinstance(decision, ReadySourceBundle):
            return "reusable_artifact_projected"
        if isinstance(decision, AcquisitionRequired):
            return decision.reason
        if isinstance(decision, UnsupportedComplete):
            return decision.reason
        if isinstance(decision, UnsupportedCapacity):
            return decision.reason
        return decision.error_code

    def _trace_id(self, turn_id: str) -> str:
        identity = stable_hash(
            {"turn_id": turn_id, "orchestrator_version": self.version}
        )
        return f"source-preparation-shadow-trace:{identity[:32]}"
