from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from pydantic import Field

from backend.investigation.contracts import (
    ClaimSupportQueryDetails,
    EvidenceDetailQueryDetails,
    FindingDetailQueryDetails,
    FindingEvidenceQueryDetails,
    REPORT_VERSION_ID_PATTERN,
    SNAPSHOT_HASH_PATTERN,
    StrictModel,
    ToolQueryReceipt,
)


CapabilityKind = Literal[
    "report_presentation",
    "claim_support",
    "finding_summary",
    "frozen_citation",
    "evidence_collection",
    "evidence_detail",
]
CapabilityStatus = Literal["available", "partial", "unavailable"]


class QueryCapabilityScope(StrictModel):
    session_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)


class QueryCapabilityAssessment(StrictModel):
    capability: CapabilityKind
    status: CapabilityStatus
    session_id: str = Field(min_length=1, max_length=160)
    report_version_id: str = Field(pattern=REPORT_VERSION_ID_PATTERN)
    snapshot_hash: str = Field(pattern=SNAPSHOT_HASH_PATTERN)
    subject_refs: tuple[str, ...] = Field(default=(), max_length=1000)
    evidence_types: tuple[str, ...] = Field(default=(), max_length=20)
    backend_complete: bool = False
    tool_result_model_ref_complete: bool = Field(
        default=False,
        description="All relevant refs were visible in the Tool result at execution time.",
    )
    tool_result_content_complete: bool | None = Field(
        default=None,
        description="Content completeness when the Receipt can prove it.",
    )
    returned_refs: tuple[str, ...] = Field(default=(), max_length=2000)
    model_visible_refs: tuple[str, ...] = Field(default=(), max_length=2000)
    matched_receipt_ids: tuple[str, ...] = Field(default=(), max_length=1000)
    selected_receipt_id: str = Field(default="", max_length=160)
    reasons: tuple[str, ...] = Field(default=(), max_length=20)

    @property
    def available(self) -> bool:
        return self.status == "available"


class QueryReceiptConflictError(ValueError):
    """Raised when one receipt identity describes conflicting query facts."""


@dataclass(frozen=True)
class _Candidate:
    receipt: ToolQueryReceipt
    backend_complete: bool
    tool_result_model_ref_complete: bool
    tool_result_content_complete: bool | None
    returned_refs: tuple[str, ...]
    model_visible_refs: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def status(self) -> CapabilityStatus:
        if not self.backend_complete or not self.tool_result_model_ref_complete:
            return "partial"
        if self.tool_result_content_complete is False:
            return "partial"
        return "available"


class QueryCapabilityResolver:
    """Project historical query facts without deciding current answer sufficiency."""

    def __init__(
        self,
        receipts: Iterable[ToolQueryReceipt],
        *,
        scope: QueryCapabilityScope,
    ) -> None:
        self.scope = scope
        deduplicated = self._deduplicate(receipts)
        self._receipts = tuple(
            receipt
            for receipt in deduplicated
            if receipt.session_id == scope.session_id
            and receipt.report_version_id == scope.report_version_id
            and receipt.snapshot_hash == scope.snapshot_hash
        )

    def assess_report_presentation(
        self, report_version_id: str
    ) -> QueryCapabilityAssessment:
        if report_version_id != self.scope.report_version_id:
            return self._unavailable(
                "report_presentation",
                (report_version_id,),
                reasons=("requested_report_scope_mismatch",),
            )
        candidates = []
        for receipt in self._successful("report_presentation_read"):
            if receipt.subject_refs != (report_version_id,):
                continue
            returned = receipt.returned_refs
            visible = self._visible_subset(returned, receipt.model_visible_refs)
            candidates.append(
                _Candidate(
                    receipt=receipt,
                    backend_complete=True,
                    tool_result_model_ref_complete=self._contains_all(
                        visible, returned
                    ),
                    tool_result_content_complete=None,
                    returned_refs=returned,
                    model_visible_refs=visible,
                    reasons=self._model_ref_reasons(visible, returned),
                )
            )
        return self._select(
            "report_presentation", (report_version_id,), (), candidates
        )

    def assess_claim_support(self, claim_ref: str) -> QueryCapabilityAssessment:
        candidates = []
        for receipt in self._successful("claim_support_read"):
            details = receipt.details
            if not isinstance(details, ClaimSupportQueryDetails):
                continue
            if details.claim_ref != claim_ref:
                continue
            if not self._query_subject_matches(receipt, "claim_id", claim_ref):
                continue
            candidates.append(self._ref_candidate(receipt, claim_ref))
        return self._select("claim_support", (claim_ref,), (), candidates)

    def assess_finding_summary(
        self, finding_ref: str
    ) -> QueryCapabilityAssessment:
        candidates = []
        for receipt in self._successful("claim_support_read"):
            details = receipt.details
            if not isinstance(details, ClaimSupportQueryDetails):
                continue
            if not self._query_subject_matches(
                receipt, "claim_id", details.claim_ref
            ):
                continue
            if finding_ref not in details.finding_summary_refs:
                continue
            candidates.append(self._ref_candidate(receipt, finding_ref))
        for receipt in self._successful("finding_detail_read"):
            details = receipt.details
            if not isinstance(details, FindingDetailQueryDetails):
                continue
            if details.finding_ref != finding_ref:
                continue
            if not self._query_subject_matches(
                receipt, "finding_id", finding_ref
            ):
                continue
            candidates.append(self._ref_candidate(receipt, finding_ref))
        return self._select("finding_summary", (finding_ref,), (), candidates)

    def assess_frozen_citation(
        self, evidence_ref: str, *, claim_ref: str = ""
    ) -> QueryCapabilityAssessment:
        candidates = []
        for receipt in self._successful("claim_support_read"):
            details = receipt.details
            if not isinstance(details, ClaimSupportQueryDetails):
                continue
            if not self._query_subject_matches(
                receipt, "claim_id", details.claim_ref
            ):
                continue
            if claim_ref and details.claim_ref != claim_ref:
                continue
            if evidence_ref not in details.frozen_citation_refs:
                continue
            candidates.append(self._ref_candidate(receipt, evidence_ref))
        subjects = tuple(item for item in (claim_ref, evidence_ref) if item)
        return self._select("frozen_citation", subjects, (), candidates)

    def assess_evidence_collection(
        self,
        finding_ref: str,
        *,
        evidence_types: Iterable[str] | None = None,
    ) -> QueryCapabilityAssessment:
        normalized_types = self._normalize_evidence_types(evidence_types)
        candidates = []
        for receipt in self._successful("finding_evidence_list"):
            details = receipt.details
            if not isinstance(details, FindingEvidenceQueryDetails):
                continue
            if details.finding_ref != finding_ref:
                continue
            if not self._query_subject_matches(
                receipt, "finding_id", finding_ref
            ):
                continue
            if self._normalize_evidence_types(
                receipt.query_params.get("evidence_types") or ()
            ) != self._normalize_evidence_types(details.evidence_types):
                continue
            if self._normalize_evidence_types(details.evidence_types) != normalized_types:
                continue
            candidates.append(self._collection_candidate(receipt, details))
        return self._select(
            "evidence_collection",
            (finding_ref,),
            normalized_types,
            candidates,
        )

    def assess_evidence_detail(
        self, evidence_ref: str, *, finding_ref: str = ""
    ) -> QueryCapabilityAssessment:
        candidates = []
        for receipt in self._successful("evidence_detail_read"):
            details = receipt.details
            if not isinstance(details, EvidenceDetailQueryDetails):
                continue
            if details.evidence_ref != evidence_ref:
                continue
            if not self._query_subject_matches(
                receipt, "evidence_id", evidence_ref
            ):
                continue
            if finding_ref and details.finding_ref != finding_ref:
                continue
            visible = self._visible_subset(
                (evidence_ref,), receipt.model_visible_refs
            )
            ref_complete = self._contains_all(visible, (evidence_ref,))
            content_complete = not receipt.model_output_truncated
            reasons = list(self._model_ref_reasons(visible, (evidence_ref,)))
            if not content_complete:
                reasons.append("tool_result_content_truncated")
            backend_complete = evidence_ref in set(receipt.returned_refs)
            if not backend_complete:
                reasons.append("receipt_returned_refs_mismatch")
            candidates.append(
                _Candidate(
                    receipt=receipt,
                    backend_complete=backend_complete,
                    tool_result_model_ref_complete=ref_complete,
                    tool_result_content_complete=content_complete,
                    returned_refs=(evidence_ref,),
                    model_visible_refs=visible,
                    reasons=tuple(reasons),
                )
            )
        subjects = tuple(item for item in (finding_ref, evidence_ref) if item)
        return self._select("evidence_detail", subjects, (), candidates)

    def has_report_presentation(self, report_version_id: str) -> bool:
        return self.assess_report_presentation(report_version_id).available

    def has_claim_support(self, claim_ref: str) -> bool:
        return self.assess_claim_support(claim_ref).available

    def has_finding_summary(self, finding_ref: str) -> bool:
        return self.assess_finding_summary(finding_ref).available

    def has_frozen_citation(self, evidence_ref: str, *, claim_ref: str = "") -> bool:
        return self.assess_frozen_citation(
            evidence_ref, claim_ref=claim_ref
        ).available

    def has_evidence_collection(
        self,
        finding_ref: str,
        *,
        evidence_types: Iterable[str] | None = None,
    ) -> bool:
        return self.assess_evidence_collection(
            finding_ref, evidence_types=evidence_types
        ).available

    def has_evidence_detail(
        self, evidence_ref: str, *, finding_ref: str = ""
    ) -> bool:
        return self.assess_evidence_detail(
            evidence_ref, finding_ref=finding_ref
        ).available

    def _successful(self, operation: str) -> tuple[ToolQueryReceipt, ...]:
        return tuple(
            receipt
            for receipt in self._receipts
            if receipt.status == "ok" and receipt.operation == operation
        )

    def _ref_candidate(
        self, receipt: ToolQueryReceipt, target_ref: str
    ) -> _Candidate:
        returned = (target_ref,)
        visible = self._visible_subset(returned, receipt.model_visible_refs)
        backend_complete = target_ref in set(receipt.returned_refs)
        reasons = list(self._model_ref_reasons(visible, returned))
        if not backend_complete:
            reasons.append("receipt_returned_refs_mismatch")
        return _Candidate(
            receipt=receipt,
            backend_complete=backend_complete,
            tool_result_model_ref_complete=self._contains_all(visible, returned),
            tool_result_content_complete=None,
            returned_refs=returned,
            model_visible_refs=visible,
            reasons=tuple(reasons),
        )

    def _collection_candidate(
        self,
        receipt: ToolQueryReceipt,
        details: FindingEvidenceQueryDetails,
    ) -> _Candidate:
        returned = tuple(dict.fromkeys(details.returned_evidence_refs))
        visible = self._visible_subset(returned, receipt.model_visible_refs)
        reasons = []
        if len(returned) != len(details.returned_evidence_refs):
            reasons.append("duplicate_returned_refs")
        if tuple(receipt.returned_refs) != returned:
            reasons.append("receipt_returned_refs_mismatch")
        if details.returned_count != len(returned):
            reasons.append("returned_count_mismatch")
        if receipt.result_count != details.returned_count:
            reasons.append("receipt_result_count_mismatch")
        if receipt.total != details.total:
            reasons.append("receipt_total_mismatch")
        if receipt.has_more != details.has_more:
            reasons.append("receipt_has_more_mismatch")
        if details.has_more:
            reasons.append("backend_collection_has_more")
        if details.returned_count != details.total:
            reasons.append("backend_collection_count_incomplete")
        if receipt.cursor:
            reasons.append("backend_collection_did_not_start_at_first_page")
        if receipt.next_cursor:
            reasons.append("backend_collection_has_next_cursor")
        ref_complete = self._contains_all(visible, returned)
        reasons.extend(self._model_ref_reasons(visible, returned))
        backend_complete = not any(
            reason
            for reason in reasons
            if reason
            not in {
                "tool_result_model_refs_incomplete",
            }
        )
        return _Candidate(
            receipt=receipt,
            backend_complete=backend_complete,
            tool_result_model_ref_complete=ref_complete,
            tool_result_content_complete=None,
            returned_refs=returned,
            model_visible_refs=visible,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def _select(
        self,
        capability: CapabilityKind,
        subject_refs: tuple[str, ...],
        evidence_types: tuple[str, ...],
        candidates: list[_Candidate],
    ) -> QueryCapabilityAssessment:
        if not candidates:
            return self._unavailable(capability, subject_refs, evidence_types)
        ranked = sorted(
            candidates,
            key=lambda item: (
                1 if item.status == "available" else 0,
                item.receipt.executed_at,
                item.receipt.receipt_id,
            ),
        )
        selected = ranked[-1]
        return QueryCapabilityAssessment(
            capability=capability,
            status=selected.status,
            session_id=self.scope.session_id,
            report_version_id=self.scope.report_version_id,
            snapshot_hash=self.scope.snapshot_hash,
            subject_refs=subject_refs,
            evidence_types=evidence_types,
            backend_complete=selected.backend_complete,
            tool_result_model_ref_complete=(
                selected.tool_result_model_ref_complete
            ),
            tool_result_content_complete=(
                selected.tool_result_content_complete
            ),
            returned_refs=selected.returned_refs,
            model_visible_refs=selected.model_visible_refs,
            matched_receipt_ids=tuple(
                item.receipt.receipt_id
                for item in sorted(
                    candidates,
                    key=lambda item: (
                        item.receipt.executed_at,
                        item.receipt.receipt_id,
                    ),
                )
            ),
            selected_receipt_id=selected.receipt.receipt_id,
            reasons=selected.reasons,
        )

    def _unavailable(
        self,
        capability: CapabilityKind,
        subject_refs: tuple[str, ...],
        evidence_types: tuple[str, ...] = (),
        *,
        reasons: tuple[str, ...] = ("no_matching_successful_receipt",),
    ) -> QueryCapabilityAssessment:
        return QueryCapabilityAssessment(
            capability=capability,
            status="unavailable",
            session_id=self.scope.session_id,
            report_version_id=self.scope.report_version_id,
            snapshot_hash=self.scope.snapshot_hash,
            subject_refs=subject_refs,
            evidence_types=evidence_types,
            reasons=reasons,
        )

    @staticmethod
    def _deduplicate(
        receipts: Iterable[ToolQueryReceipt],
    ) -> tuple[ToolQueryReceipt, ...]:
        by_id: dict[str, ToolQueryReceipt] = {}
        for receipt in receipts:
            existing = by_id.get(receipt.receipt_id)
            if existing is not None and existing != receipt:
                raise QueryReceiptConflictError(
                    f"conflicting Tool Query Receipt: {receipt.receipt_id}"
                )
            by_id[receipt.receipt_id] = receipt
        return tuple(
            sorted(
                by_id.values(),
                key=lambda item: (item.executed_at, item.receipt_id),
            )
        )

    @staticmethod
    def _normalize_evidence_types(
        evidence_types: Iterable[str] | None,
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    str(item).strip()
                    for item in evidence_types or ()
                    if str(item).strip()
                }
            )
        )

    @staticmethod
    def _query_subject_matches(
        receipt: ToolQueryReceipt, query_key: str, subject_ref: str
    ) -> bool:
        return (
            receipt.subject_refs == (subject_ref,)
            and str(receipt.query_params.get(query_key) or "") == subject_ref
        )

    @staticmethod
    def _visible_subset(
        returned_refs: tuple[str, ...], model_visible_refs: tuple[str, ...]
    ) -> tuple[str, ...]:
        visible = set(model_visible_refs)
        return tuple(item for item in returned_refs if item in visible)

    @staticmethod
    def _contains_all(
        visible_refs: tuple[str, ...], returned_refs: tuple[str, ...]
    ) -> bool:
        return set(returned_refs).issubset(visible_refs)

    @staticmethod
    def _model_ref_reasons(
        visible_refs: tuple[str, ...], returned_refs: tuple[str, ...]
    ) -> tuple[str, ...]:
        if QueryCapabilityResolver._contains_all(visible_refs, returned_refs):
            return ()
        return ("tool_result_model_refs_incomplete",)
