from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from backend.domain.errors import DomainQueryError
from backend.domain.identity import make_finding_id, parse_evidence_id, stable_hash
from backend.investigation.artifacts import EvidenceCanonicalCapture
from backend.investigation.contracts import (
    CLAIM_ID_PATTERN,
    EVIDENCE_ID_PATTERN,
    FINDING_ID_PATTERN,
    METRIC_KEY_PATTERN,
    REPORT_VERSION_ID_PATTERN,
    ClaimSupportQueryDetails,
    EvidenceDetailQueryDetails,
    FailedToolQueryDetails,
    FindingDetailQueryDetails,
    FindingEvidenceQueryDetails,
    ReportFindingsQueryDetails,
    ReportMetricQueryDetails,
    ReportPresentationQueryDetails,
    SourceKind,
    SourceLedgerCandidate,
    StrictModel,
    ToolCall,
    ToolErrorDetail,
    ToolQueryDetails,
    ToolQueryReceipt,
    ToolResultEnvelope,
)
from backend.investigation.errors import InvestigationError
from backend.investigation.report_query import ReportQueryFacade


class ReadReportPresentationInput(StrictModel):
    pass


MetricKey = Annotated[str, Field(pattern=METRIC_KEY_PATTERN)]


class LookupReportMetricInput(StrictModel):
    metric_keys: tuple[MetricKey, ...] = Field(min_length=1, max_length=8)


class ReadClaimSupportInput(StrictModel):
    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)


class ListReportFindingsInput(StrictModel):
    risk_level: Literal["", "high", "medium", "low", "none"] = ""
    decision: Literal["", "pass", "review", "reject"] = ""
    primary_risk: str = Field(default="", max_length=80)
    limit: int = Field(default=5, ge=1, le=10)


class ReadFindingDetailInput(StrictModel):
    finding_id: str = Field(pattern=FINDING_ID_PATTERN)


class ListFindingEvidenceInput(StrictModel):
    finding_id: str = Field(pattern=FINDING_ID_PATTERN)
    evidence_types: tuple[
        Literal["text", "comment", "ocr", "asr", "visual", "keyframe"], ...
    ] = Field(default=(), max_length=6)
    limit: int = Field(default=10, ge=1, le=20)


class ReadEvidenceDetailInput(StrictModel):
    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)


TOOL_SCHEMAS: dict[str, type[StrictModel]] = {
    "read_report_presentation": ReadReportPresentationInput,
    "lookup_report_metric": LookupReportMetricInput,
    "read_claim_support": ReadClaimSupportInput,
    "list_report_findings": ListReportFindingsInput,
    "read_finding_detail": ReadFindingDetailInput,
    "list_finding_evidence": ListFindingEvidenceInput,
    "read_evidence_detail": ReadEvidenceDetailInput,
}


TOOL_DESCRIPTIONS = {
    "read_report_presentation": (
        "读取当前会话锁定 Published ReportVersion 的真实用户阅读版。报告原文不做改写。"
        "用于概括报告、读取章节或案例。"
        "其中数字若要解释，必须再调用 lookup_report_metric。"
    ),
    "lookup_report_metric": (
        "按 metric_keys 批量读取锁定报告中的冻结统计，单个 Metric 也使用单元素列表。"
        "一次最多读取 8 个 Metric。当一次回答确实需要多个已知 metric refs 时，"
        "优先在一个调用中批量取得，不要连续逐个调用。若认为需要超过 8 个 Metric，"
        "先重新评估当前问题真正需要哪些统计值；除非用户明确要求完整统计，"
        "不要为了普通概览拆成多个 batch。报告级数字、比例及统计口径必须使用此工具；"
        "普通概览若不需要具体数字则不要调用。"
    ),
    "read_claim_support": (
        "读取锁定报告内一个 Claim 的紧凑支持关系：Finding 摘要、Evidence 概览、"
        "少量冻结引用预览和 metric_refs。支持关系不等于 Evidence 完整证明 Claim。"
    ),
    "list_report_findings": (
        "只在锁定报告的冻结 Finding 集合内筛选案例，可按风险等级、判定或主要风险过滤。"
    ),
    "read_finding_detail": "读取锁定快照内一个 Finding 的当前只读详情。",
    "list_finding_evidence": "列出锁定快照内一个 Finding 的当前 Evidence 摘要。",
    "read_evidence_detail": (
        "读取锁定快照成员 Evidence 的当前原文、译文或 OCR/ASR 内容。"
        "结果是 current_source，不代表报告发布时冻结的完整原文。"
    ),
}


class InvestigationToolService:
    def __init__(self, facade: ReportQueryFacade, *, max_result_size: int = 24_000):
        self.facade = facade
        self.max_result_size = max(4_000, int(max_result_size))

    @property
    def allowed_tool_names(self) -> frozenset[str]:
        return frozenset(TOOL_SCHEMAS)

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "parameters": schema.model_json_schema(),
                    "strict": True,
                },
            }
            for name, schema in TOOL_SCHEMAS.items()
        ]

    def validate_call(self, call: ToolCall) -> StrictModel:
        schema = TOOL_SCHEMAS.get(call.name)
        if schema is None:
            raise ValueError("tool name is not allowed")
        arguments = dict(call.arguments)
        # Older server-side callers may still send metric_key. The model schema only
        # exposes metric_keys, so Qwen never has to choose between two shapes.
        if call.name == "lookup_report_metric" and set(arguments) == {"metric_key"}:
            arguments = {"metric_keys": [arguments["metric_key"]]}
        return schema.model_validate(arguments)

    def canonical_arguments(self, call: ToolCall) -> dict[str, Any]:
        arguments = self.validate_call(call).model_dump(mode="json")
        if call.name == "lookup_report_metric":
            keys = list(dict.fromkeys(str(item) for item in arguments["metric_keys"]))
            if not all(re.fullmatch(METRIC_KEY_PATTERN, item) for item in keys):
                raise ValueError("invalid metric key")
            arguments["metric_keys"] = keys
        return arguments

    def query_fingerprint(self, call: ToolCall) -> str:
        return self.facade.query_fingerprint(
            call.name, self.canonical_arguments(call)
        )

    def execute(self, *, report_version_id: str, call: ToolCall) -> ToolResultEnvelope:
        result, _ = self.execute_with_canonical(
            report_version_id=report_version_id, call=call
        )
        return result

    def execute_with_canonical(
        self, *, report_version_id: str, call: ToolCall
    ) -> tuple[ToolResultEnvelope, EvidenceCanonicalCapture | None]:
        try:
            query_arguments = self.canonical_arguments(call)
        except (ValidationError, ValueError):
            supplied_metrics = call.arguments.get("metric_keys") or [
                call.arguments.get("metric_key")
            ]
            if call.name == "lookup_report_metric" and any(
                str(item or "").startswith("report-claim:")
                for item in supplied_metrics
            ):
                return (
                    self._error(
                        call,
                        "claim_id_used_as_metric_key",
                        "传入的是 Claim ID。请先调用 read_claim_support 获取该 Claim 的 metric_refs，"
                        "再把完整 metric_key 放入 metric_keys 传给 lookup_report_metric。",
                    ),
                    None,
                )
            return (
                self._error(
                    call,
                    "invalid_tool_arguments",
                    "工具参数不符合约束，请只使用工具 Schema 中允许的字段和值。",
                ),
                None,
            )
        query_fingerprint = self.facade.query_fingerprint(call.name, query_arguments)
        try:
            data, provenance = self._dispatch(
                report_version_id, call.name, query_arguments, query_fingerprint
            )
        except InvestigationError as exc:
            return (
                self._error(
                    call, exc.code, exc.safe_message, retryable=exc.retryable
                ),
                None,
            )
        except DomainQueryError as exc:
            return (
                self._error(
                    call,
                    exc.code.value,
                    "当前报告范围内没有可用的对应领域数据。",
                ),
                None,
            )
        except (KeyError, ValueError, TypeError):
            return (
                self._error(
                    call,
                    "source_validation_failed",
                    "工具结果未通过来源范围校验。",
                ),
                None,
            )
        full_fingerprint = stable_hash(
            {
                "tool_name": call.name,
                "arguments": query_arguments,
                "data": data,
                "sources": [item.model_dump(mode="json") for item in provenance],
            }
        )
        details = self._query_details(call.name, query_arguments, data, provenance)
        capture = None
        if call.name in {"list_finding_evidence", "read_evidence_detail"}:
            try:
                if not isinstance(data, dict):
                    raise ValueError("Evidence canonical result must be an object")
                capture = EvidenceCanonicalCapture(
                    tool_name=call.name,
                    normalized_query=query_arguments,
                    canonical_payload=data,
                    provenance=tuple(provenance),
                    query_fingerprint=query_fingerprint,
                    result_fingerprint=full_fingerprint,
                )
            except (ValidationError, ValueError, TypeError):
                capture = None
        bounded, truncated = self._bounded(
            data, max_size=max(1_000, self.max_result_size - 2_000)
        )
        result = ToolResultEnvelope(
            status="ok",
            data=bounded,
            provenance=tuple(provenance),
            result_fingerprint=full_fingerprint,
            truncated=truncated,
            query_details=details,
        )
        return result, capture

    def model_payload(self, result: ToolResultEnvelope) -> dict[str, Any]:
        """Return a compact, valid JSON envelope while keeping full provenance server-side."""
        sources = [
            {
                "source_kind": item.source_kind.value,
                "stable_ref": (
                    item.metric_key
                    or item.evidence_id
                    or item.finding_id
                    or item.claim_id
                    or item.section_id
                ),
                "metric_key": item.metric_key,
                "section_id": item.section_id,
                "claim_id": item.claim_id,
                "finding_id": item.finding_id,
                "evidence_id": item.evidence_id,
                "asset_status": item.asset_status,
                "warnings": list(item.warnings),
                "freshness": item.freshness,
            }
            for item in result.provenance[:8]
        ]
        payload = {
            "status": result.status,
            "data": result.data,
            "error": result.error.model_dump(mode="json") if result.error else None,
            "provenance": {
                "items": sources,
                "total": len(result.provenance),
                "returned": len(sources),
                "has_more": len(sources) < len(result.provenance),
            },
            "result_fingerprint": result.result_fingerprint,
            "truncated": result.truncated,
        }
        if self._size(payload) <= self.max_result_size:
            return payload
        data, _ = self._bounded(
            result.data or {}, max_size=max(1_000, self.max_result_size - 2_500)
        )
        payload["data"] = data
        payload["truncated"] = True
        return payload

    def error_result(
        self,
        call: ToolCall,
        error_code: str,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> ToolResultEnvelope:
        return self._error(
            call, error_code, safe_message, retryable=retryable
        )

    def query_receipt(
        self,
        *,
        session_id: str,
        turn_id: str,
        report_version_id: str,
        snapshot_hash: str,
        call: ToolCall,
        result: ToolResultEnvelope,
        model_payload: dict[str, Any],
    ) -> ToolQueryReceipt:
        try:
            query_params = self.canonical_arguments(call)
            fingerprint_arguments = query_params
        except (ValidationError, ValueError):
            fingerprint_arguments = call.arguments
            bounded, _ = self._bounded(call.arguments, max_size=4_000)
            query_params = bounded if isinstance(bounded, dict) else {}
        query_fingerprint = self.facade.query_fingerprint(
            call.name, fingerprint_arguments
        )
        arguments_fingerprint = stable_hash(
            {"tool_name": call.name, "arguments": fingerprint_arguments}
        )
        details = result.query_details or FailedToolQueryDetails(
            operation="failed_tool_query", requested_tool_name=call.name
        )
        operation = self._operation_for(call.name, details)
        subject_refs = self._subject_refs(
            report_version_id, call.name, query_params, details
        )
        returned_refs = self._returned_refs(details)
        visible_candidates = set(self._stable_refs(model_payload))
        model_visible_refs = tuple(
            item for item in returned_refs if item in visible_candidates
        )
        result_count, total, has_more = self._result_cardinality(details)
        provenance = model_payload.get("provenance") or {}
        receipt_identity = stable_hash(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "tool_call_id": call.id,
            }
        )
        receipt_id = f"tool-query-receipt:{receipt_identity[:32]}"
        return ToolQueryReceipt(
            receipt_id=receipt_id,
            session_id=session_id,
            turn_id=turn_id,
            tool_call_id=call.id,
            report_version_id=report_version_id,
            snapshot_hash=snapshot_hash,
            tool_name=call.name,
            operation=operation,
            subject_refs=subject_refs,
            query_params=query_params,
            arguments_fingerprint=arguments_fingerprint,
            query_fingerprint=query_fingerprint,
            status=result.status,
            error_code=result.error.error_code if result.error else "",
            returned_refs=returned_refs,
            model_visible_refs=model_visible_refs,
            result_count=result_count,
            total=total,
            has_more=has_more,
            model_output_truncated=bool(
                model_payload.get("truncated") or provenance.get("has_more")
            ),
            result_fingerprint=result.result_fingerprint,
            details=details,
            executed_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def _query_details(
        cls,
        tool_name: str,
        arguments: dict[str, Any],
        data: dict[str, Any] | list[Any],
        provenance: list[SourceLedgerCandidate],
    ) -> ToolQueryDetails:
        payload = data if isinstance(data, dict) else {}
        if tool_name == "read_report_presentation":
            return ReportPresentationQueryDetails(
                operation="report_presentation_read",
                section_refs=cls._values_for_keys(payload, {"section_id"}),
                claim_refs=cls._values_for_keys(payload, {"claim_id", "claim_ids"}),
                finding_refs=cls._values_for_keys(payload, {"finding_id", "finding_ids"}),
                evidence_refs=cls._values_for_keys(payload, {"evidence_id", "evidence_ids"}),
                metric_refs=cls._values_for_keys(payload, {"metric_key", "metric_refs"}),
            )
        if tool_name == "lookup_report_metric":
            returned = tuple(
                str(item.get("metric_key") or "")
                for item in payload.get("metrics") or []
                if isinstance(item, dict) and item.get("metric_key")
            )
            return ReportMetricQueryDetails(
                operation="report_metric_lookup",
                requested_metric_refs=tuple(arguments.get("metric_keys") or []),
                returned_metric_refs=returned,
                returned_count=int(payload.get("returned") or len(returned)),
            )
        if tool_name == "read_claim_support":
            claim = payload.get("claim") or {}
            frozen_refs = tuple(
                item.evidence_id
                for item in provenance
                if item.source_kind == SourceKind.FROZEN_CITATION_EXCERPT
                and item.evidence_id
            )
            finding_refs = tuple(
                str(item.get("finding_id") or "")
                for item in payload.get("finding_summaries") or []
                if isinstance(item, dict) and item.get("finding_id")
            )
            return ClaimSupportQueryDetails(
                operation="claim_support_read",
                claim_ref=str(claim.get("claim_id") or ""),
                finding_summary_refs=finding_refs,
                frozen_citation_refs=frozen_refs,
                metric_refs=tuple(str(item) for item in claim.get("metric_refs") or []),
            )
        if tool_name == "list_report_findings":
            refs = tuple(
                str(item.get("finding_id") or "")
                for item in payload.get("items") or []
                if isinstance(item, dict) and item.get("finding_id")
            )
            return ReportFindingsQueryDetails(
                operation="report_findings_list",
                returned_finding_refs=refs,
                returned_count=int(payload.get("returned") or len(refs)),
                total=int(payload.get("total") or 0),
                has_more=bool(payload.get("has_more")),
            )
        if tool_name == "read_finding_detail":
            finding = payload.get("finding") or {}
            overview = payload.get("evidence_overview") or {}
            return FindingDetailQueryDetails(
                operation="finding_detail_read",
                finding_ref=str(finding.get("finding_id") or ""),
                representative_evidence_refs=tuple(
                    str(item) for item in overview.get("representative_refs") or []
                ),
            )
        if tool_name == "list_finding_evidence":
            refs = tuple(
                str(item.get("evidence_id") or "")
                for item in payload.get("items") or []
                if isinstance(item, dict) and item.get("evidence_id")
            )
            return FindingEvidenceQueryDetails(
                operation="finding_evidence_list",
                finding_ref=str(arguments.get("finding_id") or ""),
                evidence_types=tuple(str(item) for item in arguments.get("evidence_types") or []),
                returned_evidence_refs=refs,
                returned_count=int(payload.get("returned") or len(refs)),
                total=int(payload.get("total") or 0),
                has_more=bool(payload.get("has_more")),
            )
        if tool_name == "read_evidence_detail":
            return EvidenceDetailQueryDetails(
                operation="evidence_detail_read",
                evidence_ref=str(payload.get("evidence_id") or ""),
                finding_ref=str(payload.get("finding_id") or ""),
                evidence_type=str(payload.get("evidence_type") or ""),
            )
        return FailedToolQueryDetails(
            operation="failed_tool_query", requested_tool_name=tool_name
        )

    @staticmethod
    def _operation_for(tool_name: str, details: ToolQueryDetails) -> str:
        operations = {
            "read_report_presentation": "report_presentation_read",
            "lookup_report_metric": "report_metric_lookup",
            "read_claim_support": "claim_support_read",
            "list_report_findings": "report_findings_list",
            "read_finding_detail": "finding_detail_read",
            "list_finding_evidence": "finding_evidence_list",
            "read_evidence_detail": "evidence_detail_read",
        }
        return operations.get(tool_name, str(details.operation))

    @classmethod
    def _subject_refs(
        cls,
        report_version_id: str,
        tool_name: str,
        query_params: dict[str, Any],
        details: ToolQueryDetails,
    ) -> tuple[str, ...]:
        if tool_name in {"read_report_presentation", "list_report_findings"}:
            return (report_version_id,)
        if tool_name == "lookup_report_metric":
            return tuple(str(item) for item in query_params.get("metric_keys") or [])
        for key in ("claim_id", "finding_id", "evidence_id"):
            value = str(query_params.get(key) or "")
            if value:
                return (value,)
        return cls._stable_refs(query_params)

    @staticmethod
    def _returned_refs(details: ToolQueryDetails) -> tuple[str, ...]:
        refs: list[str] = []
        if isinstance(details, ReportPresentationQueryDetails):
            refs.extend(details.claim_refs)
            refs.extend(details.finding_refs)
            refs.extend(details.evidence_refs)
            refs.extend(details.metric_refs)
        elif isinstance(details, ReportMetricQueryDetails):
            refs.extend(details.returned_metric_refs)
        elif isinstance(details, ClaimSupportQueryDetails):
            if details.claim_ref:
                refs.append(details.claim_ref)
            refs.extend(details.finding_summary_refs)
            refs.extend(details.frozen_citation_refs)
            refs.extend(details.metric_refs)
        elif isinstance(details, ReportFindingsQueryDetails):
            refs.extend(details.returned_finding_refs)
        elif isinstance(details, FindingDetailQueryDetails):
            if details.finding_ref:
                refs.append(details.finding_ref)
            refs.extend(details.representative_evidence_refs)
        elif isinstance(details, FindingEvidenceQueryDetails):
            refs.extend(details.returned_evidence_refs)
        elif isinstance(details, EvidenceDetailQueryDetails):
            if details.evidence_ref:
                refs.append(details.evidence_ref)
            if details.finding_ref:
                refs.append(details.finding_ref)
        return tuple(dict.fromkeys(item for item in refs if item))

    @staticmethod
    def _result_cardinality(
        details: ToolQueryDetails,
    ) -> tuple[int | None, int | None, bool | None]:
        if isinstance(details, ReportMetricQueryDetails):
            return details.returned_count, None, None
        if isinstance(details, ReportFindingsQueryDetails):
            return details.returned_count, details.total, details.has_more
        if isinstance(details, FindingEvidenceQueryDetails):
            return details.returned_count, details.total, details.has_more
        if isinstance(details, FailedToolQueryDetails):
            return None, None, None
        return 1, 1, None

    @staticmethod
    def _values_for_keys(value: Any, keys: set[str]) -> tuple[str, ...]:
        output: list[str] = []

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    if key in keys:
                        if isinstance(child, str) and child:
                            output.append(child)
                        elif isinstance(child, (list, tuple)):
                            output.extend(str(value) for value in child if value)
                    if isinstance(child, (dict, list, tuple)):
                        visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)

        visit(value)
        return tuple(dict.fromkeys(output))

    @staticmethod
    def _stable_refs(value: Any) -> tuple[str, ...]:
        patterns = (
            REPORT_VERSION_ID_PATTERN,
            CLAIM_ID_PATTERN,
            FINDING_ID_PATTERN,
            EVIDENCE_ID_PATTERN,
            METRIC_KEY_PATTERN,
        )
        output: list[str] = []

        def visit(item: Any) -> None:
            if isinstance(item, str):
                if any(re.fullmatch(pattern, item) for pattern in patterns):
                    output.append(item)
            elif isinstance(item, dict):
                for child in item.values():
                    visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)

        visit(value)
        return tuple(dict.fromkeys(output))

    def _dispatch(
        self,
        report_version_id: str,
        name: str,
        arguments: dict[str, Any],
        query_fingerprint: str,
    ) -> tuple[dict[str, Any] | list[Any], list[SourceLedgerCandidate]]:
        if name == "read_report_presentation":
            presentation = self.facade.get_report_presentation(report_version_id)
            claim_ids = self._presentation_claim_ids(presentation)
            metric_refs = self.facade.get_claim_metric_refs(
                report_version_id, claim_ids
            )
            data = self._presentation_projection(presentation, metric_refs)
            return data, self._presentation_sources(presentation, query_fingerprint)
        if name == "lookup_report_metric":
            metrics = [
                self.facade.lookup_metric(report_version_id, metric_key)
                for metric_key in arguments["metric_keys"]
            ]
            projected = [self._metric_projection(metric) for metric in metrics]
            data = {
                "metric_keys": list(arguments["metric_keys"]),
                "metrics": projected,
                "returned": len(projected),
            }
            return data, [
                self._source(
                    SourceKind.FROZEN_METRIC,
                    metric.get("source_hash") or stable_hash(metric),
                    query_fingerprint,
                    metric_key=str(metric["metric_key"]),
                    excerpt=self._metric_excerpt(item),
                    freshness="frozen_snapshot",
                )
                for metric, item in zip(metrics, projected)
            ]
        if name == "read_claim_support":
            support = self.facade.get_claim_support(report_version_id, arguments["claim_id"])
            return self._claim_support_projection(report_version_id, support), self._claim_sources(
                support, query_fingerprint
            )
        if name == "list_report_findings":
            page = self.facade.list_report_findings(report_version_id, **arguments)
            data = {
                **page,
                "items": [self._finding_card_projection(item) for item in page["items"]],
            }
            return data, [
                self._source(
                    SourceKind.CURRENT_FINDING,
                    str(item["source_hash"]),
                    query_fingerprint,
                    finding_id=str(item["finding_id"]),
                    excerpt=str(item.get("summary") or item.get("title") or "")[:1200],
                    freshness="current_source",
                )
                for item in page["items"]
            ]
        if name == "read_finding_detail":
            envelope = self.facade.get_finding_detail(
                report_version_id, arguments["finding_id"]
            )
            detail = envelope["data"]
            raw_finding = detail["finding"]
            evidence_ids = list(raw_finding.get("evidence_ids", []) or [])
            finding = {
                key: raw_finding.get(key)
                for key in (
                    "finding_id",
                    "decision",
                    "risk_level",
                    "risk_score",
                    "primary_risk",
                    "categories",
                    "matched_rule_ids",
                    "summary",
                    "evidence_type_counts",
                    "source_platform",
                    "content_title",
                    "author",
                    "analyzed_at",
                    "data_quality",
                )
            }
            data = {
                "finding": finding,
                "post": detail["post"],
                "score_breakdown": detail.get("score_breakdown", []),
                "rule_matches": detail.get("rule_matches", []),
                "exclusion_basis": detail.get("exclusion_basis", []),
                "risk_basis": detail.get("risk_basis", ""),
                "review_status": detail.get("review_status", ""),
                "review_note": detail.get("review_note", ""),
                "evidence_overview": {
                    "total": len(evidence_ids),
                    "by_type": raw_finding.get("evidence_type_counts", {}),
                    "representative_refs": evidence_ids[:5],
                    "returned": min(len(evidence_ids), 5),
                    "has_more": len(evidence_ids) > 5,
                },
                "warning_codes": self._warning_codes(envelope),
            }
            return data, [
                self._source(
                    SourceKind.CURRENT_FINDING,
                    str(raw_finding["source_hash"]),
                    query_fingerprint,
                    finding_id=str(finding["finding_id"]),
                    excerpt=str(finding.get("summary") or detail.get("risk_basis") or "")[:1200],
                    warnings=tuple(data["warning_codes"]),
                    freshness="current_source",
                )
            ]
        if name == "list_finding_evidence":
            page = self.facade.list_finding_evidence(
                report_version_id,
                arguments["finding_id"],
                evidence_types=tuple(arguments["evidence_types"]),
                limit=arguments["limit"],
            )
            items = [self._evidence_brief_projection(item) for item in page["items"]]
            data = {**page, "items": items}
            return data, [
                self._source(
                    SourceKind.CURRENT_EVIDENCE,
                    str(item["source_hash"]),
                    query_fingerprint,
                    finding_id=arguments["finding_id"],
                    evidence_id=str(item["evidence_id"]),
                    excerpt=str(
                        item.get("original_text_preview")
                        or item.get("translated_text_preview")
                        or item.get("summary")
                        or ""
                    )[:1200],
                    freshness="current_source",
                )
                for item in page["items"]
            ]
        if name == "read_evidence_detail":
            envelope = self.facade.get_evidence_detail(
                report_version_id, arguments["evidence_id"]
            )
            detail = envelope["data"]
            contexts = [
                {
                    key: value
                    for key, value in item.items()
                    if key != "asset_path"
                }
                for item in detail.get("context", [])
            ]
            data = {
                "evidence_id": detail["evidence_id"],
                "finding_id": detail["finding_id"],
                "evidence_type": detail["evidence_type"],
                "original_text": detail.get("original_text", ""),
                "translated_text": detail.get("translated_text", ""),
                "summary": detail.get("summary", ""),
                "timestamp_start": detail.get("timestamp_start"),
                "timestamp_end": detail.get("timestamp_end"),
                "asset_available": detail.get("asset_available", False),
                "structured_content_available": detail.get(
                    "structured_content_available", False
                ),
                "availability": detail.get("availability", ""),
                "context_semantics": detail.get("context_semantics", ""),
                "context": contexts,
                "support_type": detail.get("support_type", ""),
                "source_format": detail.get("source_format", ""),
                "data_quality": detail.get("data_quality", []),
                "freshness": "current_source",
                "semantic_note": "当前读取结果，不表示报告发布时冻结的完整 Evidence。",
                "warning_codes": self._warning_codes(envelope),
            }
            excerpt = str(
                detail.get("original_text")
                or detail.get("translated_text")
                or detail.get("summary")
                or ""
            )[:1200]
            return data, [
                self._source(
                    SourceKind.CURRENT_EVIDENCE,
                    str(detail["source_hash"]),
                    query_fingerprint,
                    finding_id=str(detail["finding_id"]),
                    evidence_id=str(detail["evidence_id"]),
                    excerpt=excerpt,
                    asset_status=str(detail.get("availability") or ""),
                    warnings=tuple(data["warning_codes"]),
                    freshness="current_source",
                )
            ]
        raise ValueError("tool name is not allowed")

    @classmethod
    def _presentation_projection(
        cls,
        presentation: dict[str, Any],
        claim_metric_refs: dict[str, list[str]],
    ) -> dict[str, Any]:
        projected = {
            "presentation_version": presentation.get("presentation_version", ""),
            "title": presentation.get("title", ""),
            "summary": cls._decorate_presentation_block(
                presentation.get("summary", {}), claim_metric_refs
            ),
            "key_metrics": presentation.get("key_metrics", []),
            "sections": [
                {
                    **section,
                    "paragraphs": [
                        cls._decorate_presentation_block(item, claim_metric_refs)
                        for item in section.get("paragraphs") or []
                    ],
                }
                for section in presentation.get("sections") or []
            ],
            "case_blocks": [
                cls._decorate_presentation_block(item, claim_metric_refs)
                for item in presentation.get("case_blocks") or []
            ],
            "conclusion": cls._decorate_presentation_block(
                presentation.get("conclusion", {}), claim_metric_refs
            ),
            "data_quality_note": cls._decorate_presentation_block(
                presentation.get("data_quality_note", {}), claim_metric_refs
            ),
            "source_annotation": {
                "source_kind": "report_text",
                "authority": "published_report_expression",
                "numeric_authority": "frozen_metric",
            },
        }
        return projected

    @staticmethod
    def _presentation_claim_ids(presentation: dict[str, Any]) -> list[str]:
        claim_ids: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                raw_ids = value.get("claim_ids")
                if isinstance(raw_ids, list):
                    claim_ids.extend(str(item) for item in raw_ids if item)
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(presentation)
        return list(dict.fromkeys(claim_ids))

    @staticmethod
    def _decorate_presentation_block(
        block: Any, claim_metric_refs: dict[str, list[str]]
    ) -> Any:
        if not isinstance(block, dict):
            return block
        claim_ids = [str(item) for item in block.get("claim_ids") or []]
        metric_refs = list(
            dict.fromkeys(
                metric_ref
                for claim_id in claim_ids
                for metric_ref in claim_metric_refs.get(claim_id, [])
            )
        )
        return {**block, "metric_refs": metric_refs}

    def _claim_support_projection(
        self, report_version_id: str, support: dict[str, Any]
    ) -> dict[str, Any]:
        finding_ids = list(support.get("finding_ids") or [])
        finding_summaries = []
        for finding_id in finding_ids[:5]:
            detail = self.facade.get_finding_detail(report_version_id, finding_id)["data"]
            finding = detail["finding"]
            finding_summaries.append(
                {
                    "finding_id": finding["finding_id"],
                    "label": detail.get("post", {}).get("title", ""),
                    "risk_level": finding.get("risk_level", ""),
                    "decision": finding.get("decision", ""),
                    "summary": finding.get("summary", ""),
                }
            )
        evidence = list(support.get("evidence") or [])
        previews = []
        for item in evidence[:3]:
            evidence_id = str(item["evidence_id"])
            audit_result_id, _ = parse_evidence_id(evidence_id)
            previews.append(
                {
                    "evidence_id": evidence_id,
                    "finding_id": make_finding_id(audit_result_id),
                    "citation_excerpt_preview": str(item.get("citation_excerpt") or "")[:500],
                    "asset_status": str(item.get("asset_status") or ""),
                }
            )
        claim = support["claim"]
        return {
            "claim": {
                "claim_id": claim["claim_id"],
                "claim_type": claim["claim_type"],
                "text": claim["text"],
                "support_type": claim["support_type"],
                "metric_refs": list(claim.get("metric_refs") or []),
            },
            "section": support["section"],
            "finding_ids": finding_ids[:20],
            "finding_summaries": finding_summaries,
            "finding_overview": {
                "total": len(finding_ids),
                "returned": len(finding_summaries),
                "has_more": len(finding_summaries) < len(finding_ids),
            },
            "evidence_overview": {
                "total": len(evidence),
                "returned": len(previews),
                "has_more": len(previews) < len(evidence),
                "representative_previews": previews,
            },
            "support_boundary": (
                "这些关系记录报告生成时使用的支持来源，不表示来源在逻辑上完整证明 Claim。"
            ),
        }

    @staticmethod
    def _metric_projection(metric: dict[str, Any]) -> dict[str, Any]:
        return {
            "metric_key": metric["metric_key"],
            "metric_name": metric.get("metric_name", ""),
            "label": metric.get("label", ""),
            "value": metric.get("value"),
            "denominator": metric.get("denominator"),
            "denominator_name": metric.get("denominator_name", ""),
            "percentage": metric.get("percentage"),
            "percentage_basis": metric.get("percentage_basis", ""),
            "semantic_definition": metric.get("semantic_definition", ""),
            "dimension": metric.get("dimension", []),
            "group": metric.get("group", {}),
        }

    @staticmethod
    def _evidence_brief_projection(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "evidence_id": item["evidence_id"],
            "type": item.get("evidence_type", ""),
            "preview": (
                item.get("original_text_preview")
                or item.get("translated_text_preview")
                or item.get("summary")
                or ""
            ),
            "label": item.get("summary", ""),
            "asset_status": "available" if item.get("asset_available") else "unavailable",
        }

    @staticmethod
    def _finding_card_projection(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "finding_id": item["finding_id"],
            "label": item.get("title", ""),
            "risk_level": item.get("risk_level", ""),
            "decision": item.get("decision", ""),
            "short_summary": item.get("summary", ""),
        }

    def _presentation_sources(
        self, presentation: dict[str, Any], query_fingerprint: str
    ) -> list[SourceLedgerCandidate]:
        sources = []
        blocks: list[tuple[str, dict[str, Any]]] = [
            ("summary", presentation.get("summary") or {}),
            ("conclusion", presentation.get("conclusion") or {}),
            ("data_quality_note", presentation.get("data_quality_note") or {}),
        ]
        blocks.extend(
            (str(item.get("section_id") or "section"), paragraph)
            for item in presentation.get("sections") or []
            for paragraph in item.get("paragraphs") or []
        )
        blocks.extend(
            (f"case-{index}", item)
            for index, item in enumerate(presentation.get("case_blocks") or [], start=1)
        )
        for section_id, block in blocks:
            text = str(block.get("text") or "")
            if not text:
                continue
            sources.append(
                self._source(
                    SourceKind.REPORT_TEXT,
                    stable_hash({"section_id": section_id, "block": block}),
                    query_fingerprint,
                    section_id=section_id,
                    excerpt=text[:1200],
                    freshness="published_version",
                )
            )
        return sources

    def _claim_sources(
        self, support: dict[str, Any], query_fingerprint: str
    ) -> list[SourceLedgerCandidate]:
        claim = support["claim"]
        sources = [
            self._source(
                SourceKind.REPORT_CLAIM,
                str(claim["content_hash"]),
                query_fingerprint,
                section_id=str(support["section"]["section_id"]),
                claim_id=str(claim["claim_id"]),
                excerpt=str(claim["text"])[:1200],
                freshness="published_version",
            )
        ]
        finding_ids = set(support.get("finding_ids") or [])
        for item in support.get("evidence") or []:
            evidence_id = str(item["evidence_id"])
            audit_result_id, _ = parse_evidence_id(evidence_id)
            finding_id = make_finding_id(audit_result_id)
            if finding_ids and finding_id not in finding_ids:
                continue
            excerpt = str(item.get("citation_excerpt") or "")
            sources.append(
                self._source(
                    SourceKind.FROZEN_CITATION_EXCERPT,
                    stable_hash(
                        {
                            "claim_id": claim["claim_id"],
                            "evidence_id": evidence_id,
                            "excerpt": excerpt,
                            "asset_status": item.get("asset_status", ""),
                        }
                    ),
                    query_fingerprint,
                    section_id=str(support["section"]["section_id"]),
                    claim_id=str(claim["claim_id"]),
                    finding_id=finding_id,
                    evidence_id=evidence_id,
                    excerpt=excerpt[:1200],
                    asset_status=str(item.get("asset_status") or ""),
                    freshness="frozen_snapshot",
                )
            )
        return sources

    @staticmethod
    def _metric_excerpt(metric: dict[str, Any]) -> str:
        return (
            f"{metric.get('label', metric.get('metric_key'))}: {metric.get('value')}; "
            f"denominator={metric.get('denominator')} ({metric.get('denominator_name')}); "
            f"percentage={metric.get('percentage')}; basis={metric.get('percentage_basis')}; "
            f"semantic_definition={metric.get('semantic_definition', '')}"
        )[:1200]

    @staticmethod
    def _warning_codes(envelope: dict[str, Any]) -> list[str]:
        return list(
            dict.fromkeys(
                str(item.get("code") or "")
                for item in envelope.get("warnings") or []
                if isinstance(item, dict) and item.get("code")
            )
        )

    @staticmethod
    def _source(
        source_kind: SourceKind,
        source_hash: str,
        query_fingerprint: str,
        *,
        metric_key: str = "",
        section_id: str = "",
        claim_id: str = "",
        finding_id: str = "",
        evidence_id: str = "",
        excerpt: str = "",
        asset_status: str = "",
        warnings: tuple[str, ...] = (),
        freshness: Literal["published_version", "frozen_snapshot", "current_source"],
    ) -> SourceLedgerCandidate:
        return SourceLedgerCandidate(
            source_kind=source_kind,
            metric_key=metric_key,
            section_id=section_id,
            claim_id=claim_id,
            finding_id=finding_id,
            evidence_id=evidence_id,
            source_hash=source_hash,
            excerpt=excerpt,
            asset_status=asset_status,
            query_fingerprint=query_fingerprint,
            warnings=warnings,
            freshness=freshness,
        )

    def _bounded(
        self,
        data: dict[str, Any] | list[Any],
        *,
        max_size: int | None = None,
    ) -> tuple[dict[str, Any] | list[Any], bool]:
        size_limit = max(1_000, int(max_size or self.max_result_size))
        if self._size(data) <= size_limit:
            return data, False
        bounded = self._truncate(data, string_limit=800, list_limit=20)
        if self._size(bounded) <= size_limit:
            return bounded, True
        bounded = self._truncate(data, string_limit=300, list_limit=8)
        if self._size(bounded) <= size_limit:
            return bounded, True
        if isinstance(data, list):
            summary: dict[str, Any] | list[Any] = {
                "item_count": len(data),
                "summary": "结果超过单次工具输出上限，请缩小筛选范围。",
            }
        else:
            summary = {
                "available_keys": list(data)[:20],
                "summary": "结果超过单次工具输出上限，请使用更窄的读取工具。",
            }
        return summary, True

    @classmethod
    def _truncate(cls, value: Any, *, string_limit: int, list_limit: int) -> Any:
        if isinstance(value, str):
            return value if len(value) <= string_limit else value[:string_limit] + "..."
        if isinstance(value, list):
            return [
                cls._truncate(item, string_limit=string_limit, list_limit=list_limit)
                for item in value[:list_limit]
            ]
        if isinstance(value, tuple):
            return [
                cls._truncate(item, string_limit=string_limit, list_limit=list_limit)
                for item in value[:list_limit]
            ]
        if isinstance(value, dict):
            return {
                str(key): cls._truncate(item, string_limit=string_limit, list_limit=list_limit)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))

    @staticmethod
    def _error(
        call: ToolCall,
        error_code: str,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> ToolResultEnvelope:
        detail = ToolErrorDetail(
            tool_call_id=call.id,
            tool_name=call.name,
            error_code=error_code,
            safe_message=safe_message,
            retryable=retryable,
        )
        return ToolResultEnvelope(
            status="error",
            error=detail,
            result_fingerprint=stable_hash(detail.model_dump(mode="json")),
            query_details=FailedToolQueryDetails(
                operation="failed_tool_query", requested_tool_name=call.name
            ),
        )
