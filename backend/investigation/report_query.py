from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.audit_agent.config import settings
from backend.domain.errors import DomainQueryError
from backend.domain.identity import make_finding_id, parse_evidence_id, stable_hash
from backend.domain.query_service import DomainQueryService
from backend.investigation.contracts import FocusedCaseContext, PublishedReportContext
from backend.investigation.errors import ReportNotFoundError, ReportScopeError
from backend.reporting.prompts import metric_semantic_definition


class ReportQueryFacade:
    """Narrow, read-only view over one published report and its domain sources."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        query_service: DomainQueryService | None = None,
    ):
        self.db_path = (db_path or (settings.data_dir / "audit_index.sqlite3")).resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        self.query_service = query_service or DomainQueryService()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def get_published_report_context(self, report_version_id: str) -> PublishedReportContext:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    r.task_id, r.id AS report_id,
                    v.id AS report_version_id, v.version_number, v.status, v.title,
                    v.body_json, v.content_hash, v.published_at,
                    s.id AS source_snapshot_id, s.snapshot_hash, s.source_hash,
                    s.finding_ids_json, s.evidence_ids_json
                FROM report_versions v
                JOIN reports r ON r.id = v.report_id
                JOIN report_source_snapshots s ON s.report_version_id = v.id
                WHERE v.id = ?
                """,
                (report_version_id,),
            ).fetchone()
        if row is None or str(row["status"]) != "published":
            raise ReportNotFoundError(report_version_id)
        body = self._object(row["body_json"], "report body")
        audit_model = body.get("audit_model")
        human_report = body.get("human_report")
        if not isinstance(audit_model, dict) or not isinstance(human_report, dict):
            raise ReportScopeError("published report does not contain phase 2.5 body layers")
        snapshot_hash = str(row["snapshot_hash"] or "")
        if str(audit_model.get("source_snapshot_hash") or "") != snapshot_hash:
            raise ReportScopeError("report body and source snapshot hashes do not match")
        if str(audit_model.get("task_id") or "") != str(row["task_id"]):
            raise ReportScopeError("report body task does not match report owner")
        return PublishedReportContext(
            task_id=str(row["task_id"]),
            report_id=str(row["report_id"]),
            report_version_id=str(row["report_version_id"]),
            version_number=int(row["version_number"]),
            source_snapshot_id=str(row["source_snapshot_id"]),
            snapshot_hash=snapshot_hash,
            source_hash=str(row["source_hash"]),
            title=str(row["title"] or human_report.get("title") or ""),
            content_hash=str(row["content_hash"]),
            published_at=str(row["published_at"] or ""),
            finding_ids=tuple(self._string_list(row["finding_ids_json"], "snapshot finding IDs")),
            evidence_ids=tuple(self._string_list(row["evidence_ids_json"], "snapshot evidence IDs")),
        )

    def get_report_presentation(self, report_version_id: str) -> dict[str, Any]:
        self.get_published_report_context(report_version_id)
        body = self._get_body(report_version_id)
        presentation = body.get("human_report")
        if not isinstance(presentation, dict):
            raise ReportScopeError("human report is missing")
        return presentation

    def get_case_catalog(self, report_version_id: str) -> list[dict[str, Any]]:
        return [
            {
                "case_ref": item.case_ref,
                "title": item.title,
                "summary": item.case_text[:500],
                "related_claim_refs": list(item.related_claim_refs),
            }
            for item in self._focused_case_contexts(report_version_id)
        ]

    def get_focused_case_context(
        self, report_version_id: str, selected_case_ref: str
    ) -> FocusedCaseContext:
        for item in self._focused_case_contexts(report_version_id):
            if item.case_ref == selected_case_ref:
                return item
        raise ReportScopeError("selected case ref is not a case in this published version")

    def find_focused_case_context(
        self, report_version_id: str, member_ref: str
    ) -> FocusedCaseContext | None:
        matches = []
        for item in self._focused_case_contexts(report_version_id):
            allowed = {
                item.case_ref,
                *item.related_claim_refs,
                *item.related_finding_refs,
                *item.related_evidence_refs,
            }
            if member_ref in allowed:
                matches.append(item)
        if len(matches) > 1:
            raise ReportScopeError("focused object belongs to multiple report cases")
        return matches[0] if matches else None

    def lookup_metric(self, report_version_id: str, metric_key: str) -> dict[str, Any]:
        self.get_published_report_context(report_version_id)
        audit_model = self._get_body(report_version_id).get("audit_model")
        metrics = audit_model.get("metrics") if isinstance(audit_model, dict) else None
        if not isinstance(metrics, list):
            raise ReportScopeError("frozen metrics are missing")
        for metric in metrics:
            if isinstance(metric, dict) and str(metric.get("metric_key") or "") == metric_key:
                output = dict(metric)
                output["percentage"] = (
                    output.get("value")
                    if str(output.get("metric_name") or "") == "percentage"
                    else None
                )
                output["dimension"] = list((output.get("group") or {}).keys())
                output["semantic_definition"] = str(
                    output.get("semantic_definition")
                    or metric_semantic_definition(output)
                )
                return output
        raise ReportNotFoundError(f"metric not found in published version: {metric_key}")

    def get_claim_metric_refs(
        self, report_version_id: str, claim_ids: list[str]
    ) -> dict[str, list[str]]:
        """Resolve metric references without loading Claim support relations."""
        self.get_published_report_context(report_version_id)
        unique_ids = list(dict.fromkeys(str(item) for item in claim_ids if item))
        if not unique_ids:
            return {}
        if len(unique_ids) > 100:
            raise ReportScopeError("too many presentation claims")
        placeholders = ", ".join("?" for _ in unique_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, metric_refs_json
                FROM report_claims
                WHERE report_version_id = ? AND id IN ({placeholders})
                """,
                (report_version_id, *unique_ids),
            ).fetchall()
        by_id = {
            str(row["id"]): self._string_list(
                row["metric_refs_json"], "claim metric refs"
            )
            for row in rows
        }
        if set(unique_ids) - set(by_id):
            raise ReportScopeError("presentation references a Claim outside this version")
        return by_id

    def get_claim_support(self, report_version_id: str, claim_id: str) -> dict[str, Any]:
        context = self.get_published_report_context(report_version_id)
        with self._connect() as connection:
            claim = connection.execute(
                """
                SELECT c.*, s.section_id, s.title AS section_title
                FROM report_claims c
                JOIN report_sections s ON s.id = c.report_section_id
                WHERE c.id = ? AND c.report_version_id = ?
                """,
                (claim_id, report_version_id),
            ).fetchone()
            if claim is None:
                raise ReportNotFoundError(f"claim not found in published version: {claim_id}")
            finding_rows = connection.execute(
                "SELECT finding_id FROM report_claim_findings WHERE claim_id = ? ORDER BY finding_id",
                (claim_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT evidence_id, citation_excerpt, asset_status
                FROM report_claim_evidence WHERE claim_id = ? ORDER BY evidence_id
                """,
                (claim_id,),
            ).fetchall()
        finding_ids = [str(row["finding_id"]) for row in finding_rows]
        evidence = [dict(row) for row in evidence_rows]
        snapshot_findings = set(context.finding_ids)
        snapshot_evidence = set(context.evidence_ids)
        for finding_id in finding_ids:
            self._assert_finding_scope(context, finding_id)
        for item in evidence:
            evidence_id = str(item["evidence_id"])
            if evidence_id not in snapshot_evidence:
                raise ReportScopeError("claim evidence is outside the frozen source snapshot")
            try:
                audit_result_id, _ = parse_evidence_id(evidence_id)
            except ValueError as exc:
                raise ReportScopeError("claim evidence ID is malformed") from exc
            linked_finding_id = make_finding_id(audit_result_id)
            if linked_finding_id not in snapshot_findings:
                raise ReportScopeError("claim evidence Finding is outside the frozen snapshot")
            if finding_ids and linked_finding_id not in finding_ids:
                raise ReportScopeError("claim evidence is not linked to a claim Finding")
            try:
                validation = self.query_service.validate_finding_evidence_link(
                    linked_finding_id, evidence_id
                )
            except DomainQueryError as exc:
                raise ReportScopeError("claim evidence association cannot be verified") from exc
            if not validation.data.valid:
                raise ReportScopeError("claim evidence association is invalid")
        metric_refs = self._string_list(claim["metric_refs_json"], "claim metric refs")
        if str(claim["claim_type"]) == "numeric" and not metric_refs:
            raise ReportScopeError("numeric claim has no frozen metric reference")
        return {
            "claim": {
                "claim_id": str(claim["id"]),
                "claim_type": str(claim["claim_type"]),
                "text": str(claim["text"]),
                "support_type": str(claim["support_type"]),
                "metric_refs": metric_refs,
                "content_hash": str(claim["content_hash"]),
            },
            "section": {
                "section_id": str(claim["section_id"]),
                "title": str(claim["section_title"]),
            },
            "finding_ids": finding_ids,
            "evidence": evidence,
        }

    def list_report_findings(
        self,
        report_version_id: str,
        *,
        risk_level: str = "",
        decision: str = "",
        primary_risk: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        context = self.get_published_report_context(report_version_id)
        selected: list[dict[str, Any]] = []
        for finding_id in context.finding_ids:
            detail = self.query_service.get_finding_detail(finding_id).data
            finding = detail.finding
            if risk_level and finding.risk_level != risk_level:
                continue
            if decision and finding.decision != decision:
                continue
            if primary_risk and finding.primary_risk != primary_risk:
                continue
            selected.append(self._finding_projection(detail.model_dump(mode="json")))
        rank = {"high": 0, "medium": 1, "low": 2, "none": 3, "": 4}
        selected.sort(
            key=lambda item: (
                rank.get(str(item.get("risk_level") or ""), 5),
                -float(item.get("risk_score") or 0),
                context.finding_ids.index(str(item["finding_id"])),
            )
        )
        items = selected[:limit]
        return {
            "items": items,
            "total": len(selected),
            "returned": len(items),
            "has_more": len(items) < len(selected),
        }

    def get_finding_detail(self, report_version_id: str, finding_id: str) -> dict[str, Any]:
        context = self.get_published_report_context(report_version_id)
        self._assert_finding_scope(context, finding_id)
        detail = self.query_service.get_finding_detail(finding_id)
        return detail.model_dump(mode="json")

    def list_finding_evidence(
        self,
        report_version_id: str,
        finding_id: str,
        *,
        evidence_types: tuple[str, ...] = (),
        limit: int = 10,
    ) -> dict[str, Any]:
        context = self.get_published_report_context(report_version_id)
        self._assert_finding_scope(context, finding_id)
        envelope = self.query_service.get_finding_evidence(
            finding_id, evidence_types=evidence_types, limit=100
        )
        allowed = set(context.evidence_ids)
        available = [
            item.model_dump(mode="json")
            for item in envelope.data
            if item.evidence_id in allowed
        ]
        items = available[:limit]
        return {
            "items": items,
            "total": len(available),
            "returned": len(items),
            "has_more": len(items) < len(available),
        }

    def get_evidence_detail(self, report_version_id: str, evidence_id: str) -> dict[str, Any]:
        context = self.get_published_report_context(report_version_id)
        if evidence_id not in set(context.evidence_ids):
            raise ReportScopeError("evidence is outside the frozen source snapshot")
        detail = self.query_service.get_evidence_detail(evidence_id)
        if detail.data.task_id != context.task_id:
            raise ReportScopeError("evidence belongs to another task")
        output = detail.model_dump(mode="json")
        output["metadata"] = {
            **output.get("metadata", {}),
            "freshness": "current_source",
            "frozen_snapshot_membership": True,
            "semantic_note": "当前读取结果，不表示报告发布时冻结的完整 Evidence。",
        }
        return output

    def _assert_finding_scope(
        self, context: PublishedReportContext, finding_id: str
    ) -> None:
        if finding_id not in set(context.finding_ids):
            raise ReportScopeError("finding is outside the frozen source snapshot")
        try:
            detail = self.query_service.get_finding_detail(finding_id)
        except DomainQueryError as exc:
            raise ReportScopeError("frozen Finding is no longer readable") from exc
        if detail.data.finding.task_id != context.task_id:
            raise ReportScopeError("finding belongs to another task")

    def _get_body(self, report_version_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT body_json FROM report_versions WHERE id = ? AND status = 'published'",
                (report_version_id,),
            ).fetchone()
        if row is None:
            raise ReportNotFoundError(report_version_id)
        return self._object(row["body_json"], "report body")

    def _focused_case_contexts(
        self, report_version_id: str
    ) -> list[FocusedCaseContext]:
        report_context = self.get_published_report_context(report_version_id)
        presentation = self.get_report_presentation(report_version_id)
        raw_cases = presentation.get("case_blocks") or []
        if not isinstance(raw_cases, list):
            raise ReportScopeError("human report case blocks are invalid")

        case_claim_ids = list(
            dict.fromkeys(
                str(claim_id)
                for block in raw_cases
                if isinstance(block, dict)
                for claim_id in block.get("claim_ids") or []
                if claim_id
            )
        )
        support_by_claim = {
            claim_id: self.get_claim_support(report_version_id, claim_id)
            for claim_id in case_claim_ids
        }
        global_facts = self._validated_report_wide_facts(
            report_version_id,
            presentation,
            excluded_claim_ids=set(case_claim_ids),
        )

        contexts = []
        for index, block in enumerate(raw_cases, start=1):
            if not isinstance(block, dict):
                raise ReportScopeError("human report case block is invalid")
            claim_ids = list(
                dict.fromkeys(str(item) for item in block.get("claim_ids") or [] if item)
            )
            if not claim_ids:
                continue
            if any(claim_id not in support_by_claim for claim_id in claim_ids):
                raise ReportScopeError("case block references a Claim outside this version")
            supports = [support_by_claim[claim_id] for claim_id in claim_ids]
            finding_ids = list(
                dict.fromkeys(
                    finding_id
                    for support in supports
                    for finding_id in support.get("finding_ids") or []
                )
            )
            evidence_ids = list(
                dict.fromkeys(
                    str(evidence["evidence_id"])
                    for support in supports
                    for evidence in support.get("evidence") or []
                )
            )
            finding_set = set(finding_ids)
            evidence_ids = list(
                dict.fromkeys(
                    [
                        *evidence_ids,
                        *(
                            evidence_id
                            for evidence_id in report_context.evidence_ids
                            if self._evidence_finding_id(evidence_id) in finding_set
                        ),
                    ]
                )
            )
            metric_refs = list(
                dict.fromkeys(
                    metric_ref
                    for support in supports
                    for metric_ref in support["claim"].get("metric_refs") or []
                )
            )
            title = str(block.get("title") or f"案例{index}").strip()
            case_text = str(block.get("text") or "").strip()
            if not case_text:
                raise ReportScopeError("human report case block has no case-specific text")
            contexts.append(
                FocusedCaseContext(
                    case_ref=claim_ids[0],
                    case_index=index,
                    case_section_id=f"case-{index}",
                    title=title,
                    case_text=case_text,
                    related_claim_refs=tuple(claim_ids),
                    related_finding_refs=tuple(finding_ids),
                    related_evidence_refs=tuple(evidence_ids),
                    related_metric_refs=tuple(metric_refs),
                    validated_report_wide_facts=tuple(global_facts),
                )
            )
        return contexts

    @staticmethod
    def _evidence_finding_id(evidence_id: str) -> str:
        try:
            audit_result_id, _ = parse_evidence_id(evidence_id)
        except ValueError as exc:
            raise ReportScopeError("snapshot Evidence ID is malformed") from exc
        return make_finding_id(audit_result_id)

    def _validated_report_wide_facts(
        self,
        report_version_id: str,
        presentation: dict[str, Any],
        *,
        excluded_claim_ids: set[str],
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[str, str]] = []
        for source_block in ("summary", "conclusion"):
            block = presentation.get(source_block)
            if not isinstance(block, dict):
                continue
            candidates.extend(
                (str(claim_id), source_block)
                for claim_id in block.get("claim_ids") or []
                if claim_id and str(claim_id) not in excluded_claim_ids
            )
        claim_ids = list(dict.fromkeys(claim_id for claim_id, _ in candidates))
        if not claim_ids:
            return []
        placeholders = ", ".join("?" for _ in claim_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, claim_type, text
                FROM report_claims
                WHERE report_version_id = ? AND id IN ({placeholders})
                """,
                (report_version_id, *claim_ids),
            ).fetchall()
        by_id = {str(row["id"]): row for row in rows}
        if set(claim_ids) - set(by_id):
            raise ReportScopeError("report-wide block references a Claim outside this version")
        source_by_id = dict(candidates)
        return [
            {
                "claim_id": claim_id,
                "claim_type": str(by_id[claim_id]["claim_type"]),
                "text": str(by_id[claim_id]["text"]),
                "source_block": source_by_id[claim_id],
            }
            for claim_id in claim_ids
            # Methodology is the only current Claim type that structurally denotes
            # a report-level interpretation instead of a local or numeric fact.
            if str(by_id[claim_id]["claim_type"]) == "methodology"
        ]

    @staticmethod
    def _finding_projection(detail: dict[str, Any]) -> dict[str, Any]:
        finding = detail["finding"]
        post = detail["post"]
        return {
            "finding_id": finding["finding_id"],
            "decision": finding.get("decision", ""),
            "risk_level": finding.get("risk_level", ""),
            "risk_score": finding.get("risk_score"),
            "primary_risk": finding.get("primary_risk", ""),
            "summary": finding.get("summary", ""),
            "categories": finding.get("categories", []),
            "evidence_type_counts": finding.get("evidence_type_counts", {}),
            "source_hash": finding.get("source_hash", ""),
            "title": post.get("title", ""),
            "author": post.get("author", {}),
        }

    @staticmethod
    def _object(raw: Any, label: str) -> dict[str, Any]:
        try:
            value = json.loads(str(raw or "{}"))
        except json.JSONDecodeError as exc:
            raise ReportScopeError(f"{label} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ReportScopeError(f"{label} is not an object")
        return value

    @staticmethod
    def _string_list(raw: Any, label: str) -> list[str]:
        try:
            value = json.loads(str(raw or "[]"))
        except json.JSONDecodeError as exc:
            raise ReportScopeError(f"{label} is not valid JSON") from exc
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ReportScopeError(f"{label} is not a string list")
        return list(dict.fromkeys(value))

    @staticmethod
    def query_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
        return stable_hash({"tool_name": tool_name, "arguments": arguments})
