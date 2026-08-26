from __future__ import annotations

import itertools
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from backend.domain.contracts import (
    DomainWarning,
    EvidenceType,
    EvidenceView,
    FindingEvidenceLinkValidation,
    FindingFilters,
    FindingView,
    SourceEnvelope,
    SourceRecord,
)
from backend.domain.errors import (
    DomainAccessDeniedError,
    DomainAssociationError,
    DomainObjectNotFoundError,
    DomainQueryParameterError,
    DomainUnsupportedFormatError,
    EvidenceTypeNotFoundError,
    FindingHasNoEvidenceError,
)
from backend.domain.filters import canonical_filter_dict
from backend.domain.identity import make_finding_id, parse_evidence_id, parse_finding_id, stable_hash
from backend.domain.metrics import (
    AggregationDimension,
    AggregationMetricName,
    PercentageBasis,
    make_metric_key,
)
from backend.domain.pagination import Pagination
from backend.domain.query_contracts import (
    AccessContext,
    AggregationResult,
    AggregationValue,
    EvidenceAvailability,
    EvidenceBrief,
    EvidenceContextItem,
    EvidenceContextOptions,
    EvidenceDetail,
    FindingCard,
    FindingDetail,
    FindingSearchResult,
    PostInfo,
    TaskOverview,
    TaskSnapshot,
    TaskSnapshotFindingInput,
)
from backend.domain.repository import DomainRecordNotFoundError, DomainRepository
from backend.domain.warnings import DataQuality


_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "canceled", "stopped"}
_POST_ADAPTATION_FILTERS = ("has_evidence", "data_quality", "rule_id", "evidence_types")


@dataclass(frozen=True)
class _FindingCollection:
    items: tuple[FindingView, ...]
    sources: tuple[SourceRecord, ...]
    warnings: tuple[DomainWarning, ...]


class DomainQueryService:
    """Internal, read-only query facade shared by future reports and investigation chat."""

    def __init__(
        self,
        repository: DomainRepository | None = None,
        *,
        access_context: AccessContext | None = None,
    ):
        self.repository = repository or DomainRepository()
        self.access_context = access_context or AccessContext()

    def get_task_snapshot(self, task_id: str) -> SourceEnvelope[TaskSnapshot]:
        task = self._get_task(task_id)
        sql_stats = self.repository.get_task_sql_statistics(task_id)
        collection = self._collect_findings(task_id, FindingFilters())
        if len(collection.items) != sql_stats["audit_result_count"]:
            raise DomainAssociationError(
                "task audit result count does not match normalized Finding count",
                object_type="task",
                object_id=task_id,
            )

        statistic_inputs = tuple(self._snapshot_input(item) for item in collection.items)
        path_warnings = self.repository.get_task_path_warnings(task_id)
        warnings = self._merge_warnings((collection.warnings, path_warnings))
        quality_summary = self._quality_summary(collection.items, path_warnings)
        finding_ids = tuple(item.finding_id for item in collection.items)
        status = str(task.get("status") or "")
        completed_at = str(task.get("updated_at") or "") if status.lower() in _TERMINAL_TASK_STATUSES else ""
        snapshot_payload = {
            "task": {
                "task_id": task_id,
                "task_name": task.get("display_name") or task_id,
                "task_status": status,
                "source_platform": task.get("platform") or "",
                "created_at": task.get("created_at") or "",
                "completed_at": completed_at,
                "configuration_revision_id": task.get("current_audit_config_revision_id") or "",
            },
            "statistics": sql_stats,
            "statistic_inputs": [item.model_dump(mode="json") for item in statistic_inputs],
            "data_quality_summary": quality_summary,
        }
        source_hash = stable_hash(snapshot_payload)
        snapshot = TaskSnapshot(
            task_id=task_id,
            task_name=str(task.get("display_name") or task_id),
            task_status=status,
            source_platform=str(task.get("platform") or ""),
            created_at=str(task.get("created_at") or ""),
            completed_at=completed_at,
            completed_at_basis="jobs.updated_at_for_terminal_status" if completed_at else "",
            content_count=sql_stats["content_count"],
            audit_result_count=sql_stats["audit_result_count"],
            decision_distribution=sql_stats["decision_distribution"],
            risk_level_distribution=sql_stats["risk_level_distribution"],
            category_distribution=sql_stats["category_distribution"],
            finding_ids=finding_ids,
            configuration_revision_id=str(task.get("current_audit_config_revision_id") or ""),
            data_quality_summary=quality_summary,
            statistic_inputs=statistic_inputs,
            source_hash=source_hash,
        )
        return SourceEnvelope(
            data=snapshot,
            sources=collection.sources,
            warnings=warnings,
            metadata={
                "query_status": "success",
                "source_manifest_count": len(collection.sources),
                "snapshot_mutability": "recalculable_not_published",
            },
        )

    def get_task_overview(self, task_id: str) -> SourceEnvelope[TaskOverview]:
        snapshot_envelope = self.get_task_snapshot(task_id)
        snapshot = snapshot_envelope.data
        primary_risks = Counter(
            item.primary_risk or "unspecified" for item in snapshot.statistic_inputs
        )
        overview_payload = {
            "task_snapshot_source_hash": snapshot.source_hash,
            "primary_risk_distribution": dict(sorted(primary_risks.items())),
        }
        overview = TaskOverview(
            task_id=snapshot.task_id,
            task_name=snapshot.task_name,
            task_status=snapshot.task_status,
            source_platform=snapshot.source_platform,
            audit_result_count=snapshot.audit_result_count,
            pass_count=snapshot.decision_distribution.get("pass", 0),
            review_count=snapshot.decision_distribution.get("review", 0),
            reject_count=snapshot.decision_distribution.get("reject", 0),
            risk_level_distribution=snapshot.risk_level_distribution,
            primary_risk_distribution=dict(sorted(primary_risks.items())),
            findings_with_evidence_count=sum(
                1 for item in snapshot.statistic_inputs if item.evidence_ids
            ),
            data_quality_summary=snapshot.data_quality_summary,
            source_hash=stable_hash(overview_payload),
        )
        return SourceEnvelope(
            data=overview,
            sources=snapshot_envelope.sources,
            warnings=snapshot_envelope.warnings,
            metadata={"query_status": "success", "derived_from": snapshot.source_hash},
        )

    def search_findings(
        self,
        task_id: str,
        filters: FindingFilters | dict[str, Any] | None = None,
        pagination: Pagination | dict[str, Any] | None = None,
    ) -> SourceEnvelope[FindingSearchResult]:
        self._get_task(task_id)
        normalized_filters = self._normalize_filters(filters)
        normalized_pagination = self._normalize_pagination(pagination)
        applied_filters = canonical_filter_dict(normalized_filters)

        if self._requires_post_adaptation_filter(normalized_filters):
            collection = self._collect_findings(
                task_id,
                normalized_filters,
                sort_by=normalized_pagination.sort_by.value,
                sort_order=normalized_pagination.sort_order.value,
            )
            total = len(collection.items)
            selected = collection.items[
                normalized_pagination.offset : normalized_pagination.offset + normalized_pagination.page_size
            ]
            selected_ids = {item.finding_id for item in selected}
            sources = tuple(item for item in collection.sources if item.finding_id in selected_ids)
            warnings = tuple(
                warning
                for warning in collection.warnings
                if warning.audit_result_id is None
                or make_finding_id(warning.audit_result_id) in selected_ids
            )
        else:
            page = self.repository.list_finding_views(
                task_id,
                normalized_filters,
                offset=normalized_pagination.offset,
                limit=normalized_pagination.page_size,
                sort_by=normalized_pagination.sort_by.value,
                sort_order=normalized_pagination.sort_order.value,
            )
            total = page.data.total
            selected = page.data.items
            sources = page.sources
            warnings = page.warnings

        cards = tuple(self._finding_card(item) for item in selected)
        source_hash = stable_hash(
            {
                "task_id": task_id,
                "filters": applied_filters,
                "pagination": normalized_pagination.model_dump(mode="json"),
                "total": total,
                "finding_source_hashes": [item.source_hash for item in selected],
            }
        )
        result = FindingSearchResult(
            items=cards,
            total=total,
            page=normalized_pagination.page,
            page_size=normalized_pagination.page_size,
            applied_filters=applied_filters,
            source_hash=source_hash,
        )
        metadata = {"query_status": "success", "pagination_mode": "sqlite"}
        if not cards:
            metadata["empty_reason"] = "no_matching_findings"
        return SourceEnvelope(data=result, sources=sources, warnings=warnings, metadata=metadata)

    def aggregate_findings(
        self,
        task_id: str,
        filters: FindingFilters | dict[str, Any] | None,
        group_by: list[str] | tuple[str, ...],
        metrics: list[str] | tuple[str, ...],
    ) -> SourceEnvelope[AggregationResult]:
        self._get_task(task_id)
        normalized_filters = self._normalize_filters(filters)
        dimensions = self._normalize_dimensions(group_by)
        metric_names = self._normalize_metrics(metrics)
        collection = self._collect_findings(task_id, normalized_filters)
        applied_filters = canonical_filter_dict(normalized_filters)
        total_findings = len(collection.items)
        total_evidence = sum(len(item.evidence_ids) for item in collection.items)

        finding_ids_by_group: dict[tuple[str, ...], set[str]] = defaultdict(set)
        evidence_count_by_group: Counter[tuple[str, ...]] = Counter()
        for finding in collection.items:
            value_sets = [self._dimension_values(finding, dimension) for dimension in dimensions]
            if any(not values for values in value_sets):
                continue
            for group_key in itertools.product(*value_sets):
                finding_ids_by_group[group_key].add(finding.finding_id)
                if AggregationDimension.EVIDENCE_TYPE.value in dimensions:
                    evidence_index = dimensions.index(AggregationDimension.EVIDENCE_TYPE.value)
                    evidence_count_by_group[group_key] += finding.evidence_type_counts.get(
                        group_key[evidence_index], 0
                    )
                else:
                    evidence_count_by_group[group_key] += len(finding.evidence_ids)

        aggregate_source_hash = stable_hash(
            {
                "task_id": task_id,
                "filters": applied_filters,
                "group_by": dimensions,
                "metrics": metric_names,
                "finding_source_hashes": [item.source_hash for item in collection.items],
            }
        )
        rows: list[AggregationValue] = []
        for group_key in sorted(finding_ids_by_group):
            group = dict(zip(dimensions, group_key))
            finding_count = len(finding_ids_by_group[group_key])
            evidence_count = evidence_count_by_group[group_key]
            for metric in metric_names:
                if metric in (
                    AggregationMetricName.COUNT.value,
                    AggregationMetricName.FINDING_COUNT.value,
                ):
                    value = float(finding_count)
                    denominator = total_findings
                    denominator_name = "current_filtered_finding_count"
                    percentage_basis = ""
                elif metric == AggregationMetricName.EVIDENCE_COUNT.value:
                    value = float(evidence_count)
                    denominator = total_evidence
                    denominator_name = "current_filtered_evidence_count"
                    percentage_basis = ""
                else:
                    value = round((finding_count / total_findings * 100), 6) if total_findings else 0.0
                    denominator = total_findings
                    denominator_name = "current_filtered_finding_count"
                    percentage_basis = PercentageBasis.FINDING_COUNT.value
                metric_source_hash = stable_hash(
                    {
                        "aggregate_source_hash": aggregate_source_hash,
                        "group": group,
                        "metric": metric,
                        "value": value,
                        "denominator": denominator,
                    }
                )
                rows.append(
                    AggregationValue(
                        metric_key=make_metric_key(
                            task_id=task_id,
                            filters=applied_filters,
                            group_by=dimensions,
                            group=group,
                            metric=metric,
                            denominator_name=denominator_name,
                        ),
                        metric=metric,
                        value=value,
                        denominator=denominator,
                        denominator_name=denominator_name,
                        group=group,
                        filters=applied_filters,
                        task_id=task_id,
                        source_hash=metric_source_hash,
                        percentage_basis=percentage_basis,
                    )
                )

        result = AggregationResult(
            task_id=task_id,
            group_by=dimensions,
            metrics=metric_names,
            rows=tuple(rows),
            filtered_finding_count=total_findings,
            filtered_evidence_count=total_evidence,
            filters=applied_filters,
            source_hash=aggregate_source_hash,
        )
        metadata = {"query_status": "success", "percentage_scale": "0_to_100"}
        if not collection.items:
            metadata["empty_reason"] = "no_matching_findings"
        return SourceEnvelope(
            data=result,
            sources=collection.sources,
            warnings=collection.warnings,
            metadata=metadata,
        )

    def get_finding_detail(self, finding_id: str) -> SourceEnvelope[FindingDetail]:
        audit_result_id = self._parse_finding_id(finding_id)
        finding_envelope = self._get_finding(audit_result_id, finding_id)
        self._assert_access(finding_envelope.data.task_id)
        if any(
            warning.code == DataQuality.BROKEN_REFERENCE
            and warning.source_json_path == "/content_id"
            for warning in finding_envelope.warnings
        ):
            raise DomainAssociationError(
                "Finding cannot be traced through task/content",
                object_type="finding",
                object_id=finding_id,
            )
        record = self.repository.get_audit_result_record(audit_result_id)
        result = record["result"]
        if not result and record["result_warnings"]:
            raise DomainUnsupportedFormatError(
                "audit result_json cannot be interpreted as an object",
                object_type="finding",
                object_id=finding_id,
            )
        author = self._load_json_object(record.get("author_json"))
        detail_warnings: list[DomainWarning] = list(finding_envelope.warnings)
        score_breakdown = self._dict_sequence(
            result.get("score_breakdown"), "score_breakdown", finding_envelope.data, detail_warnings
        )
        rule_matches = self._dict_sequence(
            result.get("rule_matches"), "rule_matches", finding_envelope.data, detail_warnings
        )
        detail = FindingDetail(
            finding=finding_envelope.data,
            post=PostInfo(
                content_id=finding_envelope.data.content_id,
                content_key=finding_envelope.data.content_key,
                platform=str(record.get("platform") or ""),
                note_id=str(record.get("note_id") or ""),
                title=str(record.get("title") or result.get("content_title") or ""),
                url=str(record.get("url") or ""),
                author_key=str(record.get("author_key") or ""),
                author=author,
                analyzed_at=str(record.get("analyzed_at") or ""),
            ),
            score_breakdown=score_breakdown,
            rule_matches=rule_matches,
            exclusion_basis=self._exclusion_basis(result),
            risk_basis=self._stringify(result.get("risk_basis")),
            review_status=str(record.get("review_status") or ""),
            review_note=str(record.get("review_note") or ""),
        )
        return SourceEnvelope(
            data=detail,
            sources=finding_envelope.sources,
            warnings=self._merge_warnings((detail_warnings,)),
            metadata={"query_status": "success"},
        )

    def get_finding_evidence(
        self,
        finding_id: str,
        evidence_types: list[str] | tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> SourceEnvelope[tuple[EvidenceBrief, ...]]:
        audit_result_id = self._parse_finding_id(finding_id)
        if limit < 1 or limit > 100:
            raise DomainQueryParameterError("limit must be between 1 and 100")
        finding = self._get_finding(audit_result_id, finding_id)
        self._assert_access(finding.data.task_id)
        evidence = self.repository.get_evidence_views(audit_result_id)
        if not evidence.data:
            if any(warning.code == DataQuality.UNSUPPORTED_FORMAT for warning in evidence.warnings):
                raise DomainUnsupportedFormatError(
                    "Finding Evidence cannot be normalized from the persisted format",
                    object_type="finding",
                    object_id=finding_id,
                )
            raise FindingHasNoEvidenceError(
                "Finding exists but has no normalized Evidence",
                object_type="finding",
                object_id=finding_id,
            )
        requested_types = self._normalize_evidence_types(evidence_types or ())
        selected = tuple(
            item for item in evidence.data if not requested_types or item.evidence_type in requested_types
        )
        if requested_types and not selected:
            raise EvidenceTypeNotFoundError(
                "Finding has Evidence, but none of the requested Evidence types",
                object_type="finding",
                object_id=finding_id,
            )
        briefs = tuple(self._evidence_brief(item) for item in selected[:limit])
        return SourceEnvelope(
            data=briefs,
            sources=evidence.sources,
            warnings=evidence.warnings,
            metadata={
                "query_status": "success",
                "total_before_limit": len(selected),
                "requested_evidence_types": [item.value for item in requested_types],
            },
        )

    def get_evidence_detail(
        self,
        evidence_id: str,
        context_window: int | EvidenceContextOptions = 0,
    ) -> SourceEnvelope[EvidenceDetail]:
        audit_result_id, _ = self._parse_evidence_id(evidence_id)
        evidence_envelope = self._get_evidence(evidence_id)
        evidence = evidence_envelope.data
        self._assert_access(evidence.task_id)
        finding_id = make_finding_id(audit_result_id)
        validation = self.repository.validate_finding_evidence_link(finding_id, evidence_id)
        if any(
            warning.code == DataQuality.BROKEN_REFERENCE
            and warning.source_json_path == "/content_id"
            for warning in validation.warnings
        ):
            raise DomainAssociationError(
                "Evidence cannot be traced through Finding task/content",
                object_type="evidence",
                object_id=evidence_id,
            )
        if not validation.data.valid:
            raise DomainAssociationError(
                validation.data.reason,
                object_type="evidence",
                object_id=evidence_id,
            )
        options, semantics = self._context_options(evidence.evidence_type, context_window)
        record = self.repository.get_audit_result_record(audit_result_id)
        context = self._evidence_context(evidence, record["result"], options)
        asset_available = self._asset_available(evidence)
        structured_available = bool(
            evidence.original_text
            or evidence.translated_text
            or evidence.summary
            or evidence.timestamp_start is not None
        )
        degraded = any(quality != DataQuality.COMPLETE for quality in evidence.data_quality)
        if not structured_available and not asset_available:
            availability = EvidenceAvailability.UNAVAILABLE
        elif degraded or (evidence.asset_path and not asset_available):
            availability = EvidenceAvailability.DEGRADED
        else:
            availability = EvidenceAvailability.AVAILABLE
        detail = EvidenceDetail(
            evidence_id=evidence.evidence_id,
            finding_id=finding_id,
            audit_result_id=audit_result_id,
            task_id=evidence.task_id,
            content_id=evidence.content_id,
            evidence_type=evidence.evidence_type,
            original_text=evidence.original_text,
            translated_text=evidence.translated_text,
            summary=evidence.summary,
            timestamp_start=evidence.timestamp_start,
            timestamp_end=evidence.timestamp_end,
            asset_path=evidence.asset_path,
            asset_available=asset_available,
            structured_content_available=structured_available,
            availability=availability,
            context_semantics=semantics,
            context=context,
            support_type=evidence.support_type,
            source_format=evidence.source_format.value,
            source_hash=evidence.source_hash,
            data_quality=evidence.data_quality,
        )
        return SourceEnvelope(
            data=detail,
            sources=evidence_envelope.sources,
            warnings=evidence_envelope.warnings,
            metadata={
                "query_status": "success",
                "availability": availability.value,
                "external_resource_fallback": (
                    "embedded_structured_content"
                    if availability == EvidenceAvailability.DEGRADED and structured_available
                    else "none"
                ),
            },
        )

    def validate_finding_evidence_link(
        self,
        finding_id: str,
        evidence_id: str,
    ) -> SourceEnvelope[FindingEvidenceLinkValidation]:
        finding_result_id = self._parse_finding_id(finding_id)
        self._parse_evidence_id(evidence_id)
        finding = self._get_finding(finding_result_id, finding_id)
        evidence = self._get_evidence(evidence_id)
        self._assert_access(finding.data.task_id)
        self._assert_access(evidence.data.task_id)
        validation = self.repository.validate_finding_evidence_link(finding_id, evidence_id)
        return SourceEnvelope(
            data=validation.data,
            sources=self._merge_sources((finding.sources, evidence.sources)),
            warnings=self._merge_warnings((finding.warnings, evidence.warnings, validation.warnings)),
            metadata={
                "query_status": "success",
                "validation_outcome": "valid" if validation.data.valid else "invalid_link",
            },
        )

    def _get_task(self, task_id: str) -> dict[str, Any]:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise DomainQueryParameterError("task_id is required")
        self._assert_access(normalized)
        try:
            return self.repository.get_task_record(normalized)
        except DomainRecordNotFoundError as exc:
            raise DomainObjectNotFoundError(
                f"task not found: {normalized}", object_type="task", object_id=normalized
            ) from exc

    def _assert_access(self, task_id: str) -> None:
        allowed = self.access_context.allowed_task_ids
        if allowed and task_id not in allowed:
            raise DomainAccessDeniedError(
                f"task is outside the current access boundary: {task_id}",
                object_type="task",
                object_id=task_id,
            )

    def _get_finding(self, audit_result_id: int, finding_id: str) -> SourceEnvelope[FindingView]:
        try:
            return self.repository.get_finding_view(audit_result_id)
        except DomainRecordNotFoundError as exc:
            raise DomainObjectNotFoundError(
                f"Finding not found: {finding_id}", object_type="finding", object_id=finding_id
            ) from exc

    def _get_evidence(self, evidence_id: str) -> SourceEnvelope[EvidenceView]:
        try:
            return self.repository.get_evidence_view(evidence_id)
        except DomainRecordNotFoundError as exc:
            raise DomainObjectNotFoundError(
                f"Evidence not found: {evidence_id}", object_type="evidence", object_id=evidence_id
            ) from exc

    def _parse_finding_id(self, finding_id: str) -> int:
        try:
            return parse_finding_id(str(finding_id or ""))
        except ValueError as exc:
            raise DomainQueryParameterError(
                "invalid stable Finding ID", object_type="finding", object_id=str(finding_id or "")
            ) from exc

    def _parse_evidence_id(self, evidence_id: str) -> tuple[int, str]:
        try:
            return parse_evidence_id(str(evidence_id or ""))
        except ValueError as exc:
            raise DomainQueryParameterError(
                "invalid stable Evidence ID", object_type="evidence", object_id=str(evidence_id or "")
            ) from exc

    def _normalize_filters(self, filters: FindingFilters | dict[str, Any] | None) -> FindingFilters:
        try:
            normalized = filters if isinstance(filters, FindingFilters) else FindingFilters.model_validate(filters or {})
        except ValidationError as exc:
            raise DomainQueryParameterError(f"invalid FindingFilters: {exc}") from exc
        minimum = normalized.risk_score_min
        if minimum is None:
            minimum = normalized.min_risk_score
        if (
            minimum is not None
            and normalized.risk_score_max is not None
            and minimum > normalized.risk_score_max
        ):
            raise DomainQueryParameterError("risk_score_min cannot exceed risk_score_max")
        return normalized

    def _normalize_pagination(self, pagination: Pagination | dict[str, Any] | None) -> Pagination:
        try:
            return pagination if isinstance(pagination, Pagination) else Pagination.model_validate(pagination or {})
        except ValidationError as exc:
            raise DomainQueryParameterError(f"invalid Pagination: {exc}") from exc

    def _normalize_dimensions(self, values: Iterable[str]) -> tuple[str, ...]:
        output = tuple(dict.fromkeys(str(value) for value in values))
        if not output:
            raise DomainQueryParameterError("group_by must contain at least one dimension")
        supported = {item.value for item in AggregationDimension}
        invalid = sorted(set(output) - supported)
        if invalid:
            raise DomainQueryParameterError(f"unsupported aggregation dimensions: {invalid}")
        return output

    def _normalize_metrics(self, values: Iterable[str]) -> tuple[str, ...]:
        output = tuple(dict.fromkeys(str(value) for value in values))
        if not output:
            raise DomainQueryParameterError("metrics must contain at least one metric")
        supported = {item.value for item in AggregationMetricName}
        invalid = sorted(set(output) - supported)
        if invalid:
            raise DomainQueryParameterError(f"unsupported aggregation metrics: {invalid}")
        return output

    def _normalize_evidence_types(self, values: Iterable[str]) -> tuple[EvidenceType, ...]:
        output = []
        for value in values:
            try:
                evidence_type = value if isinstance(value, EvidenceType) else EvidenceType(str(value))
            except ValueError as exc:
                raise DomainQueryParameterError(f"unsupported Evidence type: {value}") from exc
            if evidence_type not in output:
                output.append(evidence_type)
        return tuple(output)

    def _requires_post_adaptation_filter(self, filters: FindingFilters) -> bool:
        return any(getattr(filters, field) not in (None, "", (), []) for field in _POST_ADAPTATION_FILTERS)

    def _collect_findings(
        self,
        task_id: str,
        filters: FindingFilters,
        *,
        sort_by: str = "audit_result_id",
        sort_order: str = "asc",
    ) -> _FindingCollection:
        offset = 0
        batch_size = 100
        items: list[FindingView] = []
        sources: list[SourceRecord] = []
        warnings: list[DomainWarning] = []
        while True:
            page = self.repository.list_finding_views(
                task_id,
                filters,
                offset=offset,
                limit=batch_size,
                sort_by=sort_by,
                sort_order=sort_order,
            )
            matched_ids = set()
            for finding in page.data.items:
                if self._matches_post_filters(finding, filters):
                    items.append(finding)
                    matched_ids.add(finding.finding_id)
            sources.extend(source for source in page.sources if source.finding_id in matched_ids)
            warnings.extend(
                warning
                for warning in page.warnings
                if warning.audit_result_id is not None
                and make_finding_id(warning.audit_result_id) in matched_ids
            )
            offset += len(page.data.items)
            if not page.data.items or offset >= page.data.total:
                break
        return _FindingCollection(
            items=tuple(items),
            sources=self._merge_sources((sources,)),
            warnings=self._merge_warnings((warnings,)),
        )

    def _matches_post_filters(self, finding: FindingView, filters: FindingFilters) -> bool:
        if filters.has_evidence is not None and bool(finding.evidence_ids) != filters.has_evidence:
            return False
        if filters.data_quality and not all(item in finding.data_quality for item in filters.data_quality):
            return False
        if filters.rule_id and filters.rule_id not in finding.matched_rule_ids:
            return False
        if filters.evidence_types:
            requested = {item.value for item in filters.evidence_types}
            if not requested.intersection(finding.evidence_type_counts):
                return False
        return True

    def _snapshot_input(self, finding: FindingView) -> TaskSnapshotFindingInput:
        return TaskSnapshotFindingInput(
            finding_id=finding.finding_id,
            audit_result_id=finding.audit_result_id,
            task_id=finding.task_id,
            content_id=finding.content_id,
            content_key=finding.content_key,
            decision=finding.decision,
            risk_level=finding.risk_level,
            risk_score=finding.risk_score,
            primary_risk=finding.primary_risk,
            categories=finding.categories,
            matched_rule_ids=finding.matched_rule_ids,
            audit_config_revision_id=finding.audit_config_revision_id,
            evidence_ids=finding.evidence_ids,
            evidence_type_counts=finding.evidence_type_counts,
            source_hash=finding.source_hash,
            data_quality=finding.data_quality,
        )

    def _quality_summary(
        self,
        findings: Iterable[FindingView],
        path_warnings: Iterable[DomainWarning],
    ) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for finding in findings:
            degraded = [item for item in finding.data_quality if item != DataQuality.COMPLETE]
            counter["degraded" if degraded else "complete"] += 1
            for item in degraded:
                counter[item.value] += 1
        for warning in path_warnings:
            if warning.audit_result_id is None:
                counter[f"task_content_{warning.code.value}"] += 1
        return dict(sorted(counter.items()))

    def _finding_card(self, finding: FindingView) -> FindingCard:
        return FindingCard(
            finding_id=finding.finding_id,
            audit_result_id=finding.audit_result_id,
            task_id=finding.task_id,
            content_id=finding.content_id,
            content_title=finding.content_title,
            author=finding.author,
            source_platform=finding.source_platform,
            decision=finding.decision,
            risk_level=finding.risk_level,
            risk_score=finding.risk_score,
            primary_risk=finding.primary_risk,
            summary=finding.summary,
            evidence_count=len(finding.evidence_ids),
            evidence_type_summary=finding.evidence_type_counts,
            data_quality=finding.data_quality,
            source_hash=finding.source_hash,
        )

    def _dimension_values(self, finding: FindingView, dimension: str) -> tuple[str, ...]:
        if dimension == AggregationDimension.DECISION.value:
            return (finding.decision or "unknown",)
        if dimension == AggregationDimension.RISK_LEVEL.value:
            return (finding.risk_level or "unknown",)
        if dimension == AggregationDimension.PRIMARY_RISK.value:
            return (finding.primary_risk or "unspecified",)
        if dimension == AggregationDimension.CATEGORY.value:
            return tuple(dict.fromkeys(finding.categories)) or ("uncategorized",)
        if dimension == AggregationDimension.AUTHOR.value:
            return (finding.author or "unknown",)
        if dimension == AggregationDimension.EVIDENCE_TYPE.value:
            return tuple(sorted(finding.evidence_type_counts))
        raise DomainQueryParameterError(f"unsupported aggregation dimension: {dimension}")

    def _evidence_brief(self, evidence: EvidenceView) -> EvidenceBrief:
        return EvidenceBrief(
            evidence_id=evidence.evidence_id,
            evidence_type=evidence.evidence_type,
            summary=evidence.summary,
            original_text_preview=self._preview(evidence.original_text),
            translated_text_preview=self._preview(evidence.translated_text),
            timestamp_start=evidence.timestamp_start,
            timestamp_end=evidence.timestamp_end,
            asset_available=self._asset_available(evidence),
            support_type=evidence.support_type,
            data_quality=evidence.data_quality,
            source_hash=evidence.source_hash,
        )

    def _asset_available(self, evidence: EvidenceView) -> bool:
        if not evidence.asset_path:
            return False
        task_root = (self.repository.outputs_dir / evidence.task_id).resolve()
        candidate = (task_root / evidence.asset_path).resolve()
        try:
            candidate.relative_to(task_root)
        except ValueError:
            return False
        return candidate.is_file()

    def _context_options(
        self,
        evidence_type: EvidenceType,
        value: int | EvidenceContextOptions,
    ) -> tuple[EvidenceContextOptions, str]:
        if isinstance(value, EvidenceContextOptions):
            options = value
        else:
            try:
                window = int(value)
            except (TypeError, ValueError) as exc:
                raise DomainQueryParameterError("context_window must be a non-negative integer") from exc
            if window < 0:
                raise DomainQueryParameterError("context_window must be a non-negative integer")
            try:
                if evidence_type == EvidenceType.COMMENT:
                    options = EvidenceContextOptions(comment_neighbors=window)
                elif evidence_type in (EvidenceType.OCR, EvidenceType.ASR):
                    options = EvidenceContextOptions(time_window_seconds=window)
                elif evidence_type == EvidenceType.TEXT:
                    options = EvidenceContextOptions(text_characters=window)
                elif evidence_type in (EvidenceType.KEYFRAME, EvidenceType.VISUAL):
                    options = EvidenceContextOptions(
                        time_window_seconds=window,
                        related_resource_limit=min(window, 20) if window else 0,
                    )
                else:
                    options = EvidenceContextOptions()
            except ValidationError as exc:
                raise DomainQueryParameterError(f"invalid context_window: {exc}") from exc
        if evidence_type == EvidenceType.COMMENT:
            semantics = f"adjacent_comment_count_per_side:{options.comment_neighbors}"
        elif evidence_type in (EvidenceType.OCR, EvidenceType.ASR):
            semantics = f"time_seconds_before_and_after:{options.time_window_seconds:g}"
        elif evidence_type == EvidenceType.TEXT:
            semantics = f"text_characters_before_and_after:{options.text_characters}"
        elif evidence_type in (EvidenceType.KEYFRAME, EvidenceType.VISUAL):
            semantics = (
                f"time_seconds_before_and_after:{options.time_window_seconds:g};"
                f"related_resource_limit:{options.related_resource_limit}"
            )
        else:
            semantics = "no_context_for_evidence_type"
        return options, semantics

    def _evidence_context(
        self,
        evidence: EvidenceView,
        result: dict[str, Any],
        options: EvidenceContextOptions,
    ) -> tuple[EvidenceContextItem, ...]:
        if evidence.evidence_type == EvidenceType.COMMENT and options.comment_neighbors:
            return self._comment_context(evidence, result, options.comment_neighbors)
        if evidence.evidence_type in (EvidenceType.OCR, EvidenceType.ASR) and options.time_window_seconds:
            key = "ocr_items" if evidence.evidence_type == EvidenceType.OCR else "asr_segments"
            return self._time_context(evidence, (result.get("evidence_index") or {}).get(key), options)
        if evidence.evidence_type == EvidenceType.TEXT and options.text_characters:
            return self._text_context(evidence, result, options.text_characters)
        if evidence.evidence_type in (EvidenceType.KEYFRAME, EvidenceType.VISUAL) and (
            options.time_window_seconds or options.related_resource_limit
        ):
            index = result.get("evidence_index") or {}
            candidates = [
                *(index.get("timeline_frames") or []),
                *(result.get("risk_frames") or []),
                *(result.get("risk_images") or []),
            ]
            return self._time_context(evidence, candidates, options)
        return ()

    def _comment_context(
        self,
        evidence: EvidenceView,
        result: dict[str, Any],
        neighbor_count: int,
    ) -> tuple[EvidenceContextItem, ...]:
        source_item = self._json_pointer(result, evidence.source_json_path)
        comment_id = ""
        if isinstance(source_item, dict):
            comment_id = str(source_item.get("comment_id") or "")
            if not comment_id:
                source = str(source_item.get("source") or "")
                if source.startswith("comment:"):
                    comment_id = source.partition(":")[2]
        comments = result.get("comments") or []
        if not comment_id or not isinstance(comments, list):
            return ()
        selected_index = next(
            (
                index
                for index, item in enumerate(comments)
                if isinstance(item, dict) and str(item.get("comment_id") or "") == comment_id
            ),
            None,
        )
        if selected_index is None:
            return ()
        start = max(0, selected_index - neighbor_count)
        end = min(len(comments), selected_index + neighbor_count + 1)
        output = []
        for index in range(start, end):
            if index == selected_index or not isinstance(comments[index], dict):
                continue
            item = comments[index]
            output.append(
                EvidenceContextItem(
                    source_kind="comment",
                    source_id=str(item.get("comment_id") or ""),
                    original_text=str(item.get("content") or item.get("text") or ""),
                    translated_text=str(item.get("translation_zh") or item.get("text_zh") or ""),
                )
            )
        return tuple(output)

    def _time_context(
        self,
        evidence: EvidenceView,
        candidates: Any,
        options: EvidenceContextOptions,
    ) -> tuple[EvidenceContextItem, ...]:
        if not isinstance(candidates, list):
            return ()
        center_start = evidence.timestamp_start if evidence.timestamp_start is not None else 0.0
        center_end = evidence.timestamp_end if evidence.timestamp_end is not None else center_start
        lower = center_start - options.time_window_seconds
        upper = center_end + options.time_window_seconds
        output = []
        seen = set()
        limit = options.related_resource_limit or 20
        for item in candidates:
            if not isinstance(item, dict):
                continue
            start = self._as_float(item.get("start"), item.get("timestamp"))
            end = self._as_float(item.get("end"), item.get("timestamp"), start)
            if start is None or end is None or end < lower or start > upper:
                continue
            source_id = str(
                item.get("id")
                or item.get("frame_id")
                or item.get("segment_id")
                or item.get("evidence_id")
                or ""
            )
            text = str(
                item.get("text")
                or item.get("ocr_text")
                or item.get("source_text_dolphin")
                or item.get("evidence")
                or ""
            )
            key = (source_id, text, start, end)
            if key in seen:
                continue
            seen.add(key)
            asset = self._safe_context_asset(
                evidence.task_id,
                str(item.get("frame_asset_rel") or item.get("asset_rel") or ""),
            )
            output.append(
                EvidenceContextItem(
                    source_kind=evidence.evidence_type.value,
                    source_id=source_id,
                    original_text=text,
                    translated_text=str(item.get("translation_zh") or item.get("ocr_text_zh") or ""),
                    timestamp_start=start,
                    timestamp_end=end,
                    asset_path=asset,
                )
            )
            if len(output) >= limit:
                break
        return tuple(output)

    def _text_context(
        self,
        evidence: EvidenceView,
        result: dict[str, Any],
        characters: int,
    ) -> tuple[EvidenceContextItem, ...]:
        for source_name in ("desc", "content_title", "title"):
            source_text = str(result.get(source_name) or "")
            if not source_text:
                continue
            position = source_text.find(evidence.original_text) if evidence.original_text else -1
            if position < 0:
                position = 0
            evidence_end = position + len(evidence.original_text)
            excerpt = source_text[max(0, position - characters) : evidence_end + characters]
            return (
                EvidenceContextItem(
                    source_kind="text",
                    source_id=source_name,
                    original_text=excerpt,
                ),
            )
        return ()

    def _safe_context_asset(self, task_id: str, raw_path: str) -> str:
        if not raw_path or raw_path.startswith(("http://", "https://")):
            return ""
        task_root = (self.repository.outputs_dir / task_id).resolve()
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = task_root / candidate
        try:
            candidate = candidate.resolve()
            relative = candidate.relative_to(task_root)
        except (OSError, ValueError):
            return ""
        return relative.as_posix() if candidate.is_file() else ""

    def _dict_sequence(
        self,
        value: Any,
        field_name: str,
        finding: FindingView,
        warnings: list[DomainWarning],
    ) -> tuple[dict[str, Any], ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            warnings.append(
                DomainWarning(
                    code=DataQuality.UNSUPPORTED_FORMAT,
                    message=f"{field_name} is not a list",
                    task_id=finding.task_id,
                    audit_result_id=finding.audit_result_id,
                    source_json_path=f"/{field_name}",
                )
            )
            return ()
        output = []
        for index, item in enumerate(value):
            if isinstance(item, dict):
                output.append(item)
            else:
                warnings.append(
                    DomainWarning(
                        code=DataQuality.UNSUPPORTED_FORMAT,
                        message=f"{field_name} item is not an object",
                        task_id=finding.task_id,
                        audit_result_id=finding.audit_result_id,
                        source_json_path=f"/{field_name}/{index}",
                    )
                )
        return tuple(output)

    def _exclusion_basis(self, result: dict[str, Any]) -> tuple[str, ...]:
        output = []
        for key in (
            "exclusion_basis",
            "exclusion_reasons",
            "safe_reasons",
            "no_risk_basis",
            "counter_evidence",
        ):
            value = result.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                rendered = self._stringify(item)
                if rendered and rendered not in output:
                    output.append(rendered)
        return tuple(output)

    @staticmethod
    def _load_json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _stringify(value: Any) -> str:
        if value in (None, "", [], {}):
            return ""
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _preview(value: str, limit: int = 180) -> str:
        text = str(value or "")
        return text if len(text) <= limit else f"{text[:limit]}..."

    @staticmethod
    def _json_pointer(document: Any, pointer: str) -> Any:
        current = document
        for raw_part in str(pointer or "").split("/")[1:]:
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                return None
        return current

    @staticmethod
    def _as_float(*values: Any) -> float | None:
        for value in values:
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _merge_warnings(groups: Iterable[Iterable[DomainWarning]]) -> tuple[DomainWarning, ...]:
        output = []
        seen = set()
        for warning in (item for group in groups for item in group):
            key = (
                warning.code,
                warning.message,
                warning.task_id,
                warning.audit_result_id,
                warning.evidence_id,
                warning.source_json_path,
            )
            if key not in seen:
                seen.add(key)
                output.append(warning)
        return tuple(output)

    @staticmethod
    def _merge_sources(groups: Iterable[Iterable[SourceRecord]]) -> tuple[SourceRecord, ...]:
        output = []
        seen = set()
        for source in (item for group in groups for item in group):
            key = (source.finding_id, source.source_hash)
            if key not in seen:
                seen.add(key)
                output.append(source)
        return tuple(output)
