from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.domain.contracts import FindingFilters
from backend.domain.query_service import DomainQueryService
from backend.domain.repository import DomainRepository
from backend.domain.pagination import Pagination
from backend.domain.identity import stable_hash
from backend.reporting.contracts import ClaimType, FrozenSourceSnapshot, ReportMetric, SectionKind
from backend.reporting.errors import ReportValidationError
from backend.reporting.graph import ReportGenerationGraph
from backend.reporting.integration_source import (
    CanonicalReportSource,
    ImmutableReportSnapshot,
    build_immutable_snapshot,
)
from backend.reporting.store import ReportStore


class IntegrationReportGraph(ReportGenerationGraph):
    """The single production ReportGraph wired to the canonical report contract."""

    def __init__(
        self,
        *,
        source: CanonicalReportSource,
        query_service: DomainQueryService | None = None,
        store: ReportStore | None = None,
        model_client: Any | None = None,
        checkpoint_path: Path | None = None,
        representative_finding_limit: int = 10,
        fail_once_section_index: int | None = None,
    ):
        self.report_source = source
        self._snapshots: dict[str, ImmutableReportSnapshot] = {}
        self._active_version_by_task: dict[str, str] = {}
        repository = DomainRepository(db_path=source.db_path, outputs_dir=source.outputs_dir)
        super().__init__(
            query_service=query_service or DomainQueryService(repository=repository),
            store=store,
            model_client=model_client,
            checkpoint_path=checkpoint_path,
            representative_finding_limit=representative_finding_limit,
            fail_once_section_index=fail_once_section_index,
        )

    def _on_generation_created(self, generation: dict[str, Any]) -> None:
        if generation.get("task_id") and generation.get("report_version_id"):
            self._active_version_by_task[str(generation["task_id"])] = str(
                generation["report_version_id"]
            )
        if not hasattr(self.model_client, "exchange_recorder"):
            return

        def record(request: dict[str, Any], response: dict[str, Any], status: str, metadata: dict[str, Any]) -> None:
            self.store.save_provider_exchange(
                report_version_id=generation["report_version_id"],
                request=request,
                response=response,
                status=status,
                metadata=metadata,
                node_name=str(metadata.get("node_name") or ""),
                section_id=str(metadata.get("section_id") or ""),
            )

        self.model_client.exchange_recorder = record

    def _snapshot(self, version_id: str) -> ImmutableReportSnapshot:
        snapshot = self._snapshots.get(version_id)
        if snapshot is None:
            snapshot = self.store.load_immutable_snapshot(version_id)
            self._snapshots[version_id] = snapshot
            self._active_version_by_task[snapshot.task_id] = version_id
        return snapshot

    def _snapshot_for_task(self, task_id: str) -> ImmutableReportSnapshot:
        version_id = self._active_version_by_task.get(task_id)
        if not version_id:
            raise RuntimeError(f"no frozen report Snapshot is bound to task {task_id}")
        return self._snapshot(version_id)

    def _freeze_source_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        if hasattr(self.report_source, "frozen_snapshot"):
            from dataclasses import replace
            snapshot = replace(
                self.report_source.frozen_snapshot(state["task_id"]),
                snapshot_ref="report-source-snapshot:" + state["report_version_id"].split(":")[-1],
            )
        else:
            snapshot = build_immutable_snapshot(self.report_source, state["task_id"])
        self._snapshots[state["report_version_id"]] = snapshot
        task_status = (
            self.report_source.task_status(state["task_id"])
            if hasattr(self.report_source, "task_status")
            else self.query_service.get_task_snapshot(state["task_id"]).data.task_status
        )
        finding_ids = [item.ref for item in snapshot.findings]
        evidence_ids = [item.ref for item in snapshot.evidence]
        statistic_inputs = [
            {
                "finding_id": finding.ref,
                "post_ref": finding.post_ref,
                "audit_result_id": finding.audit_result_id,
                "decision": finding.payload["decision"],
                "risk_level": finding.payload["risk_level"],
                "categories": finding.payload.get("categories") or [],
                "evidence_ids": [item.ref for item in snapshot.evidence if item.finding_ref == finding.ref],
                "finding_hash": finding.payload_hash,
            }
            for finding in snapshot.findings
        ]
        manifest = FrozenSourceSnapshot(
            snapshot_id=snapshot.snapshot_ref,
            report_version_id=state["report_version_id"],
            task_id=snapshot.task_id,
            task_status=task_status,
            source_hash=snapshot.source_db_sha256,
            configuration_revision_id=snapshot.audit_config_revision_id,
            finding_ids=tuple(finding_ids),
            evidence_ids=tuple(evidence_ids),
            data_quality_warnings=(),
            statistic_inputs=tuple(statistic_inputs),
            generated_at=datetime.now(timezone.utc).isoformat(),
            snapshot_hash=snapshot.snapshot_hash,
            display_name=snapshot.display_name,
            source_revision=snapshot.source_revision,
            relation_hash=snapshot.relation_hash,
            statistics=snapshot.statistics,
        )
        persisted = self.store.save_source_snapshot(manifest.model_dump(mode="json"))
        self.store.save_snapshot_payloads(
            state["report_version_id"],
            posts=[item.__dict__ for item in snapshot.posts],
            findings=[item.__dict__ for item in snapshot.findings],
            evidence=[item.__dict__ for item in snapshot.evidence],
        )
        return {
            "source_snapshot": {
                **persisted,
                "snapshot_id": persisted.get("id", snapshot.snapshot_ref),
                "finding_ids": finding_ids,
                "evidence_ids": evidence_ids,
                "snapshot_hash": snapshot.snapshot_hash,
            },
            "warnings": [],
        }

    def _build_statistics(self, state: dict[str, Any]) -> dict[str, Any]:
        self._snapshot(state["report_version_id"])
        return {"statistics": self._calculate_statistics(state["task_id"])}

    def _task_overview_for_state(self, state: dict[str, Any]) -> Any:
        snapshot = self._snapshot(state["report_version_id"])
        manifest = self.store.get_source_snapshot(state["report_version_id"])
        platform = next(
            (str(item.payload.get("platform") or "") for item in snapshot.posts),
            "",
        )
        decisions = Counter(item.payload.get("decision", "") for item in snapshot.findings)
        risks = Counter(item.payload.get("risk_level", "") for item in snapshot.findings)
        return SimpleNamespace(
            task_id=snapshot.task_id,
            task_name=snapshot.display_name,
            task_status=str((manifest or {}).get("task_status") or ""),
            source_platform=platform,
            audit_result_count=len(snapshot.findings),
            pass_count=decisions.get("pass", 0),
            review_count=decisions.get("review", 0),
            reject_count=decisions.get("reject", 0),
            risk_level_distribution=dict(risks),
            data_quality_summary={},
        )

    def _calculate_statistics(self, task_id: str) -> dict[str, Any]:
        snapshot = self._snapshot_for_task(task_id)
        findings = snapshot.findings
        posts = snapshot.post_by_ref
        evidence = snapshot.evidence
        metrics: list[dict[str, Any]] = []

        def add_metric(
            metric_name: str,
            label: str,
            value: float,
            denominator: int,
            denominator_name: str,
            group: dict[str, str] | None = None,
            percentage_basis: str = "",
        ) -> None:
            group = group or {}
            request = {
                "task_id": task_id,
                "source_snapshot_hash": snapshot.snapshot_hash,
                "metric_name": metric_name,
                "label": label,
                "denominator": denominator,
                "denominator_name": denominator_name,
                "group": group,
                "percentage_basis": percentage_basis,
            }
            metrics.append(
                ReportMetric(
                    metric_key=f"metric:{stable_hash(request)}",
                    metric_name=metric_name,
                    label=label,
                    value=value,
                    denominator=denominator,
                    denominator_name=denominator_name,
                    group=group,
                    filters={},
                    source_hash=stable_hash({**request, "value": value, "denominator": denominator}),
                    validation_kind="immutable_report_snapshot",
                    validation_request=request,
                    percentage_basis=percentage_basis,
                ).model_dump(mode="json")
            )

        add_metric("scalar", "任务内容总数", len(posts), len(posts), "canonical_post_count")
        add_metric("scalar", "审核结果总数", len(findings), len(findings), "canonical_finding_count")
        add_metric(
            "scalar",
            "包含直接 Evidence 的 Finding 数",
            len({item.finding_ref for item in evidence}),
            len(findings),
            "canonical_finding_count",
        )
        add_metric(
            "scalar",
            "直接 Evidence 总数",
            len(evidence),
            len(evidence),
            "direct_evidence_count",
        )
        decision_counts = Counter(item.payload["decision"] for item in findings)
        risk_counts = Counter(item.payload["risk_level"] for item in findings)
        primary_counts = Counter(item.payload.get("primary_risk") or "unspecified" for item in findings)
        evidence_types = Counter(item.payload["evidence_type"] for item in evidence)
        for group_key, values, denominator in (
            ("decision", decision_counts, len(findings)),
            ("risk_level", risk_counts, len(findings)),
            ("primary_risk", primary_counts, len(findings)),
            ("evidence_type", evidence_types, len(evidence)),
        ):
            for value, count in sorted(values.items()):
                group = {group_key: str(value)}
                add_metric("count", f"{group_key}={value} 的数量", count, denominator, f"{group_key}_denominator", group)
                if group_key in {"decision", "risk_level"}:
                    percentage = round((count / denominator * 100), 6) if denominator else 0.0
                    add_metric(
                        "percentage",
                        f"{group_key}={value} 的比例",
                        percentage,
                        denominator,
                        f"{group_key}_denominator",
                        group,
                        "finding_count",
                    )
        # Archived reports can include verified candidate/exclusion counts.
        # Keep their stable metric refs so saved numeric claims can be revalidated.
        coverage = snapshot.statistics.get("source_coverage")
        if coverage and getattr(self.report_source, "account_source", "") == "report_snapshot":
            if coverage["selected_posts"] != len(posts) or coverage["candidate_posts"] < len(posts):
                raise ReportValidationError("build_statistics", ["invalid source coverage counts"])
            for name, label, value in (
                ("candidate_posts", "原始候选帖子数", coverage["candidate_posts"]),
                ("excluded_posts", "未完成帖子数", coverage["candidate_posts"] - len(posts)),
            ):
                metric = dict(metrics[0])
                metric.update(
                    metric_key="metric:" + stable_hash({"snapshot": snapshot.snapshot_hash, "coverage": name}),
                    label=label, value=value, denominator=coverage["candidate_posts"],
                    denominator_name="source_candidate_post_count", group={"source_coverage": name},
                    source_hash=stable_hash({"coverage": coverage, "metric": name}),
                )
                metrics.append(metric)
        return {
            "task_overview": {
                "task_id": task_id,
                "task_name": snapshot.display_name,
                "task_status": self.store.get_source_snapshot(
                    self._active_version_by_task[task_id]
                )["task_status"],
                "source_platform": posts[next(iter(posts))].payload.get("platform", "") if posts else "",
                "audit_result_count": len(findings),
                "pass_count": decision_counts.get("pass", 0),
                "review_count": decision_counts.get("review", 0),
                "reject_count": decision_counts.get("reject", 0),
            },
            "metrics": metrics,
            "source_hash": stable_hash({"snapshot_hash": snapshot.snapshot_hash, "metrics": metrics}),
        }

    def _all_finding_cards(self, task_id: str) -> list[dict[str, Any]]:
        snapshot = self._snapshot_for_task(task_id)
        posts = snapshot.post_by_ref
        evidence_by_finding: dict[str, list[Any]] = {}
        for item in snapshot.evidence:
            evidence_by_finding.setdefault(item.finding_ref, []).append(item)
        cards = []
        for finding in snapshot.findings:
            post = posts[finding.post_ref]
            owned = evidence_by_finding.get(finding.ref, [])
            cards.append(
                {
                    "finding_id": finding.ref,
                    "audit_result_id": finding.audit_result_id,
                    "task_id": task_id,
                    "content_id": finding.payload.get("content_id"),
                    "content_title": post.payload.get("title", ""),
                    "author": post.payload.get("author", ""),
                    "source_platform": post.payload.get("platform", ""),
                    "decision": finding.payload["decision"],
                    "risk_level": finding.payload["risk_level"],
                    "risk_score": finding.payload.get("risk_score"),
                    "primary_risk": finding.payload.get("primary_risk", ""),
                    "summary": finding.payload.get("summary", ""),
                    "evidence_count": len(owned),
                    "evidence_type_summary": dict(Counter(item.payload["evidence_type"] for item in owned)),
                    "data_quality": [],
                    "source_hash": finding.payload_hash,
                }
            )
        return sorted(cards, key=lambda item: (-int(item["evidence_count"]), int(item["audit_result_id"])))

    def _finding_prompt_input(self, finding_id: str) -> dict[str, Any]:
        version_id = next(
            (
                version_id
                for version_id, snapshot in self._snapshots.items()
                if finding_id in snapshot.finding_by_ref
            ),
            None,
        )
        if version_id is None:
            for task_id, candidate_version in self._active_version_by_task.items():
                candidate = self._snapshot(candidate_version)
                if finding_id in candidate.finding_by_ref:
                    version_id = candidate_version
                    break
        if version_id is None:
            raise RuntimeError(f"Finding is not present in a frozen Snapshot: {finding_id}")
        snapshot = self._snapshot(version_id)
        finding = snapshot.finding_by_ref[finding_id]
        post = snapshot.post_by_ref[finding.post_ref]
        evidence = [item for item in snapshot.evidence if item.finding_ref == finding_id]
        return {
            "finding": {
                "finding_id": finding.ref,
                "audit_result_id": finding.audit_result_id,
                "content_key": finding.payload.get("content_key", ""),
                "decision": finding.payload.get("decision", ""),
                "risk_level": finding.payload.get("risk_level", ""),
                "risk_score": finding.payload.get("risk_score"),
                "categories": finding.payload.get("categories") or [],
                "summary": finding.payload.get("summary", ""),
                "risk_basis": finding.payload.get("risk_basis", ""),
                "primary_risk": finding.payload.get("primary_risk", ""),
                "completed_at": finding.payload.get("completed_at", ""),
            },
            "post": {
                "post_ref": post.ref,
                "canonical_key": post.canonical_key,
                "revision": post.revision,
                "platform": post.payload.get("platform", ""),
                "title": post.payload.get("title", ""),
                "caption": post.payload.get("caption", ""),
                "url": post.payload.get("url", ""),
                "author": post.payload.get("author", ""),
                "author_display": post.payload.get("author_display", {}),
                "source_content": post.payload.get("source_content", {}),
            },
            "risk_basis": finding.payload.get("risk_basis", ""),
            "evidence": [
                {
                    "evidence_id": item.ref,
                    "evidence_type": item.payload["evidence_type"],
                    "summary": item.payload.get("summary", ""),
                    "original_text_preview": item.payload.get("original_text", "")[:500],
                    "translated_text_preview": item.payload.get("translated_text", "")[:500],
                    "support_type": item.support_type,
                }
                for item in evidence
            ],
            "evidence_detail": [
                {
                    "evidence_id": item.ref,
                    "evidence_type": item.payload["evidence_type"],
                    "original_text": item.payload.get("original_text", "")[:1000],
                    "translated_text": item.payload.get("translated_text", "")[:1000],
                    "summary": item.payload.get("summary", "")[:1000],
                    "asset_available": bool(item.payload.get("asset_path")),
                }
                for item in evidence[:2]
            ],
            "warnings": [],
        }

    def _finding_evidence_link_is_valid(self, finding_id: str, evidence_id: str) -> bool:
        for snapshot in self._snapshots.values():
            finding = snapshot.finding_by_ref.get(finding_id)
            evidence = snapshot.evidence_by_ref.get(evidence_id)
            if finding is None or evidence is None:
                continue
            return (
                evidence.support_type == "direct"
                and evidence.finding_ref == finding.ref
                and evidence.post_ref == finding.post_ref
            )
        return False

    def _validate_citations(self, state: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        evidence_by_ref = snapshot.evidence_by_ref
        finding_by_ref = snapshot.finding_by_ref
        errors: list[str] = []
        citation_details: dict[str, dict[str, Any]] = {}
        for claim in state["claims"]:
            finding_ids = list(claim.get("finding_ids") or [])
            for finding_id in finding_ids:
                if finding_id not in finding_by_ref:
                    errors.append(f"Claim references Finding outside frozen Snapshot: {finding_id}")
            for evidence_id in claim.get("evidence_ids") or []:
                evidence = evidence_by_ref.get(evidence_id)
                if evidence is None:
                    errors.append(f"Claim references Evidence outside frozen Snapshot: {evidence_id}")
                    continue
                if evidence.support_type != "direct":
                    errors.append(f"Claim references non-direct Evidence: {evidence_id}")
                valid_links = [
                    finding_id
                    for finding_id in finding_ids
                    if finding_id == evidence.finding_ref
                ]
                if not valid_links:
                    errors.append(f"Evidence is not linked to any Claim Finding: {evidence_id}")
                    continue
                payload = evidence.payload
                structured_available = bool(
                    payload.get("original_text")
                    or payload.get("translated_text")
                    or payload.get("summary")
                )
                asset_available = bool(payload.get("asset_path"))
                if not structured_available and not asset_available:
                    errors.append(f"Evidence has no usable frozen content: {evidence_id}")
                    continue
                citation_details[evidence_id] = {
                    "finding_ids": valid_links,
                    "citation_excerpt": (
                        payload.get("original_text")
                        or payload.get("translated_text")
                        or payload.get("summary")
                    )[:1000],
                    "structured_content_available": structured_available,
                    "asset_available": asset_available,
                    "asset_status": "available" if asset_available else "not_applicable",
                    "availability": "available" if (structured_available or asset_available) else "unavailable",
                }
        if errors:
            raise ReportValidationError("validate_citations", errors)
        return {
            "citation_validation_errors": [],
            "warnings": list(state.get("warnings") or []),
            "citation_details": citation_details,
        }

    def _validate_claim_support(self, state: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        finding_refs = set(snapshot.finding_by_ref)
        evidence_refs = set(snapshot.evidence_by_ref)
        errors: list[str] = []
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
                if finding_id not in finding_refs:
                    errors.append(f"Claim references Finding outside frozen Snapshot: {finding_id}")
            for evidence_id in evidence_ids:
                evidence = snapshot.evidence_by_ref.get(evidence_id)
                if evidence_id not in evidence_refs:
                    errors.append(f"Claim references Evidence outside frozen Snapshot: {evidence_id}")
                elif evidence.support_type != "direct":
                    errors.append(f"Claim references non-direct Evidence: {evidence_id}")
                elif evidence.finding_ref not in finding_ids:
                    errors.append(f"Evidence parent is not among Claim Findings: {evidence_id}")
        if errors:
            raise ReportValidationError("validate_claim_support", errors)
        return {"claim_validation_errors": []}

    def _derive_categories(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = self._snapshot(state["report_version_id"])
        finding_map = snapshot.finding_by_ref
        categories: list[dict[str, Any]] = []
        for section in state.get("section_drafts") or []:
            if not section.get("is_report_category", False):
                continue
            ordered_findings: list[str] = []
            claims = {claim.get("claim_id"): claim for claim in section.get("claims") or []}
            for paragraph in section.get("paragraphs") or []:
                for claim_id in paragraph.get("claim_ids") or []:
                    for finding_id in claims.get(claim_id, {}).get("finding_ids") or []:
                        if finding_id in finding_map and finding_id not in ordered_findings:
                            ordered_findings.append(finding_id)
            for case in section.get("case_blocks") or []:
                for claim_id in case.get("claim_ids") or []:
                    for finding_id in claims.get(claim_id, {}).get("finding_ids") or []:
                        if finding_id in finding_map and finding_id not in ordered_findings:
                            ordered_findings.append(finding_id)
            if not ordered_findings:
                continue
            categories.append(
                {
                    "category_ref": f"category:{section['section_id']}",
                    "display_ordinal": len(categories) + 1,
                    "title": section["title"],
                    "membership_scope": "report_displayed_posts",
                    "membership_complete": False,
                    "source_section_id": section["section_id"],
                    "members": [
                        {
                            "display_ordinal": index,
                            "post_ref": finding_map[finding_id].post_ref,
                            "finding_ref": finding_id,
                        }
                        for index, finding_id in enumerate(ordered_findings, 1)
                    ],
                }
            )
        return categories

    def _assemble_report(self, state: dict[str, Any]) -> dict[str, Any]:
        result = super()._assemble_report(state)
        categories = self._derive_categories(state)
        result["assembled_report"]["body_json"]["audit_model"]["categories"] = categories
        return result

    def _publish_report_version(self, state: dict[str, Any]) -> dict[str, Any]:
        validation_errors = (
            state["number_validation_errors"]
            + state["claim_validation_errors"]
            + state["citation_validation_errors"]
        )
        if validation_errors:
            from backend.reporting.errors import ReportValidationError

            raise ReportValidationError("publish_report_version", validation_errors)
        assembled = state["assembled_report"]
        categories = assembled["body_json"]["audit_model"].get("categories") or []
        version = self.store.publish_version(
            report_version_id=state["report_version_id"],
            title=assembled["title"],
            body_markdown=assembled["body_markdown"],
            body_json=assembled["body_json"],
            sections=state["section_drafts"],
            citation_details=state.get("citation_details") or {},
            categories=categories,
        )
        self.store.update_run(
            state["run_id"], status="completed", current_node="publish_report_version", warnings=state["warnings"]
        )
        assembled["content_hash"] = version["content_hash"]
        return {"assembled_report": assembled}
