"""Report R3 graph with deterministic current-investigation Account entries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from backend.reporting.account_entries import (
    DEFAULT_ACCOUNT_FIXTURE_PATH,
    ReportAccountProjector,
    public_account_projection,
)
from backend.reporting.contracts import ReportGraphState
from backend.reporting.errors import ReportValidationError
from backend.reporting.r2_graph import RiskFindingReportGraph
from backend.reporting.public_references import public_post_and_finding_refs


R3_NODE_ORDER = (
    "freeze_source_snapshot",
    "build_statistics",
    "build_report_account_entries",
    "prepare_risk_inputs",
    "plan_investigation_findings",
    "plan_outline",
    "draft_sections",
    "validate_numbers",
    "validate_claim_support",
    "validate_citations",
    "assemble_report",
    "publish_report_version",
)


class AccountEntryReportGraph(RiskFindingReportGraph):
    """R2 findings plus a server-computed, report-local Account projection."""

    def __init__(
        self,
        *,
        source,
        store=None,
        model_client=None,
        checkpoint_path: Path | None = None,
        max_input_chars: int | None = None,
        account_fixture_path: Path = DEFAULT_ACCOUNT_FIXTURE_PATH,
    ) -> None:
        self.account_projector = ReportAccountProjector.load(account_fixture_path)
        super().__init__(
            source=source,
            store=store,
            model_client=model_client,
            checkpoint_path=checkpoint_path,
            max_input_chars=max_input_chars,
        )

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(ReportGraphState)
        functions: dict[str, Callable[[ReportGraphState], dict[str, Any]]] = {
            "freeze_source_snapshot": self._freeze_source_snapshot,
            "build_statistics": self._build_statistics,
            "build_report_account_entries": self._build_report_account_entries,
            "prepare_risk_inputs": self._prepare_risk_inputs,
            "plan_investigation_findings": self._plan_investigation_findings,
            "plan_outline": self._plan_outline,
            "draft_sections": self._draft_sections,
            "validate_numbers": self._validate_numbers,
            "validate_claim_support": self._validate_claim_support,
            "validate_citations": self._validate_citations,
            "assemble_report": self._assemble_report,
            "publish_report_version": self._publish_report_version,
        }
        for name in R3_NODE_ORDER:
            graph.add_node(name, self._wrap_node(name, functions[name]))
        graph.add_edge(START, R3_NODE_ORDER[0])
        for current, following in zip(R3_NODE_ORDER, R3_NODE_ORDER[1:]):
            graph.add_edge(current, following)
        graph.add_edge(R3_NODE_ORDER[-1], END)
        return graph

    def _build_report_account_entries(
        self, state: ReportGraphState
    ) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        projection = self.account_projector.build(snapshot)
        self.store.record_run_event(
            state["run_id"],
            "build_report_account_entries",
            "validated",
            {
                "target_account_count": projection["statistics"][
                    "target_account_count"
                ],
                "comment_account_count": projection["statistics"][
                    "comment_account_count"
                ],
                "all_account_count": projection["statistics"]["all_account_count"],
                "projection_hash": projection["projection_hash"],
            },
        )
        return {"report_account_projection": projection}

    def _assemble_report(self, state: ReportGraphState) -> dict[str, Any]:
        result = super()._assemble_report(state)
        assembled = result["assembled_report"]
        projection = state["report_account_projection"]
        public_projection = public_account_projection(projection)
        account_section = self._account_section(public_projection)

        sections = [dict(item) for item in state["section_drafts"]]
        overview_index = next(
            (
                index
                for index, item in enumerate(sections)
                if item.get("section_id") == "overview"
            ),
            0,
        )
        sections.insert(overview_index + 1, account_section)
        for index, section in enumerate(sections):
            section["sort_order"] = index

        body_json = assembled["body_json"]
        body_json["audit_model"]["sections"] = sections
        body_json["account_model"] = public_projection
        body_json["report_document"] = self._public_report_document(
            state=state,
            title=assembled["title"],
            sections=sections,
            account_projection=public_projection,
        )
        assembled["body_json"] = body_json
        assembled["structured_sections"] = sections
        assembled["body_markdown"] = self._with_account_markdown(
            assembled["body_markdown"], public_projection
        )
        return {"assembled_report": assembled}

    def _publish_report_version(self, state: ReportGraphState) -> dict[str, Any]:
        if len(state.get("investigation_findings") or []) == 0:
            raise ReportValidationError(
                "publish_report_version", ["no InvestigationFindings"]
            )
        covered = {
            membership["post_ref"]
            for item in state["investigation_findings"]
            for membership in item["post_memberships"]
        }
        covered.update(
            item["post_ref"] for item in state.get("standalone_risk_posts") or []
        )
        if not state.get("risk_post_coverage_complete") or covered != set(
            state["risk_alias_registry"]["post_alias_to_ref"].values()
        ):
            raise ReportValidationError(
                "publish_report_version", ["not all risk Posts are covered"]
            )
        assembled = state["assembled_report"]
        categories = assembled["body_json"]["audit_model"]["categories"]
        version = self.store.publish_version(
            report_version_id=state["report_version_id"],
            title=assembled["title"],
            body_markdown=assembled["body_markdown"],
            body_json=assembled["body_json"],
            sections=assembled["structured_sections"],
            citation_details=state.get("citation_details") or {},
            categories=categories,
            investigation_findings=state["investigation_findings"],
            standalone_risk_posts=state.get("standalone_risk_posts") or [],
            account_projection=state["report_account_projection"],
        )
        self.store.update_run(
            state["run_id"],
            status="completed",
            current_node="publish_report_version",
            warnings=state.get("warnings") or [],
        )
        self.store.record_run_event(
            state["run_id"],
            "publish_report_version",
            "published",
            {"report_version_id": state["report_version_id"]},
        )
        assembled["content_hash"] = version["content_hash"]
        return {"assembled_report": assembled}

    @staticmethod
    def _account_section(public_projection: dict[str, Any]) -> dict[str, Any]:
        statistics = public_projection["statistics"]
        paragraphs = [
            {
                "text": (
                    f"本报告保留{statistics['target_account_count']}个调查目标/发布账号入口，"
                    f"并默认展示{statistics['default_active_comment_account_count']}个"
                    "当前调查内的活跃评论账号。账号卡片仅使用当前调查统计。"
                ),
                "claim_ids": [],
            },
            {
                "text": public_projection["scope_boundary"],
                "claim_ids": [],
            },
        ]
        return {
            "section_id": "account-entries",
            "section_kind": "account_entries",
            "title": "账号入口",
            "purpose": "提供当前调查账号卡片和进入 Account Overview 的结构化入口。",
            "paragraphs": paragraphs,
            "claims": [],
            "case_blocks": [],
            "is_report_category": False,
            "body": "\n\n".join(item["text"] for item in paragraphs),
        }

    def _public_report_document(
        self,
        *,
        state: ReportGraphState,
        title: str,
        sections: list[dict[str, Any]],
        account_projection: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        post_refs, finding_refs = public_post_and_finding_refs(snapshot)
        evidence_refs = {
            item.ref: f"evidence-{index:03d}"
            for index, item in enumerate(snapshot.evidence, 1)
        }
        investigation_refs = {
            item["finding_ref"]: f"investigation-finding-{index:02d}"
            for index, item in enumerate(state["investigation_findings"], 1)
        }
        evidence_by_finding: dict[str, list[str]] = {}
        for item in snapshot.evidence:
            evidence_by_finding.setdefault(item.finding_ref, []).append(item.ref)

        public_sections = []
        for section_order, section in enumerate(sections, 1):
            public_claims = []
            for claim_order, claim in enumerate(section.get("claims") or [], 1):
                public_claims.append(
                    {
                        "claim_ref": f"claim-{section_order:02d}-{claim_order:02d}",
                        "claim_type": claim.get("claim_type") or "",
                        "text": claim.get("text") or "",
                        "support_type": claim.get("support_type") or "",
                        "audit_finding_refs": [
                            finding_refs[item]
                            for item in claim.get("finding_ids") or []
                            if item in finding_refs
                        ],
                        "evidence_refs": [
                            evidence_refs[item]
                            for item in claim.get("evidence_ids") or []
                            if item in evidence_refs
                        ],
                    }
                )
            public_sections.append(
                {
                    "order": section_order,
                    "section_ref": f"section-{section_order:02d}",
                    "section_type": section.get("section_kind") or "narrative",
                    "title": section["title"],
                    "purpose": section.get("purpose") or "",
                    "paragraphs": [
                        {"text": item.get("text") or ""}
                        for item in section.get("paragraphs") or []
                    ],
                    "claims": public_claims,
                }
            )

        public_posts = []
        for post in snapshot.posts:
            finding = next(item for item in snapshot.findings if item.post_ref == post.ref)
            public_posts.append(
                {
                    "post_ref": post_refs[post.ref],
                    "title": str(post.payload.get("title") or "未命名帖子"),
                    "author_display_name": str(post.payload.get("author") or "未知作者"),
                    "caption": str(post.payload.get("caption") or ""),
                    "audit_finding_ref": finding_refs[finding.ref],
                }
            )

        public_findings = [
            {
                "audit_finding_ref": finding_refs[item.ref],
                "post_ref": post_refs[item.post_ref],
                "decision": item.payload.get("decision") or "",
                "risk_level": item.payload.get("risk_level") or "",
                "categories": list(item.payload.get("categories") or []),
                "primary_risk": item.payload.get("primary_risk") or "",
                "summary": item.payload.get("summary") or "",
                "evidence_refs": [
                    evidence_refs[evidence_ref]
                    for evidence_ref in evidence_by_finding.get(item.ref, [])
                ],
            }
            for item in snapshot.findings
        ]
        public_evidence = [
            {
                "evidence_ref": evidence_refs[item.ref],
                "post_ref": post_refs[item.post_ref],
                "audit_finding_ref": finding_refs[item.finding_ref],
                "support_type": item.support_type,
                "evidence_type": item.payload.get("evidence_type") or "",
                "original_text": item.payload.get("original_text") or "",
                "translated_text": item.payload.get("translated_text") or "",
                "summary": item.payload.get("summary") or "",
            }
            for item in snapshot.evidence
        ]
        investigations = []
        for item in state["investigation_findings"]:
            memberships = [
                {
                    "post_ref": post_refs[membership["post_ref"]],
                    "audit_finding_ref": finding_refs[
                        membership["audit_finding_ref"]
                    ],
                    "is_representative": bool(
                        membership.get("is_representative")
                    ),
                    "membership_evidence_refs": [
                        evidence_refs[evidence_ref]
                        for evidence_ref in membership.get(
                            "membership_evidence_refs"
                        )
                        or []
                    ],
                }
                for membership in item["post_memberships"]
            ]
            investigations.append(
                {
                    "investigation_finding_ref": investigation_refs[
                        item["finding_ref"]
                    ],
                    "order": int(item["display_ordinal"]),
                    "title": item["title"],
                    "statement": item["statement"],
                    "boundary_notes": list(item.get("boundary_notes") or []),
                    "post_memberships": memberships,
                    "representative_post_refs": [
                        membership["post_ref"]
                        for membership in memberships
                        if membership["is_representative"]
                    ],
                }
            )
        standalone = [
            {
                "post_ref": post_refs[item["post_ref"]],
                "audit_finding_ref": finding_refs[item["audit_finding_ref"]],
                "disposition_note": item["disposition_note"],
            }
            for item in state.get("standalone_risk_posts") or []
        ]
        return {
            "schema_version": "structured-report-r3/v1",
            "report_metadata": {
                "title": title,
                "status": "published",
                "source_name": snapshot.display_name,
            },
            "provenance": {
                "snapshot_hash": snapshot.snapshot_hash,
                "source_database_sha256": snapshot.source_db_sha256,
                "source_revision": snapshot.source_revision,
                "relation_hash": snapshot.relation_hash,
                "account_projection_hash": account_projection["projection_hash"],
            },
            "statistics": dict(snapshot.statistics),
            "ordered_sections": public_sections,
            "investigation_findings": investigations,
            "standalone_risk_posts": standalone,
            "posts": public_posts,
            "audit_findings": public_findings,
            "evidence": public_evidence,
            "account_entries": account_projection,
        }

    @staticmethod
    def _with_account_markdown(
        body_markdown: str, public_projection: dict[str, Any]
    ) -> str:
        groups = public_projection["groups"]
        lines = ["## 账号入口", "", public_projection["scope_boundary"], ""]
        lines.extend(("### 调查目标/发布账号", ""))
        for item in groups["target_accounts"]:
            stats = item["current_investigation_statistics"]
            lines.append(
                f"- {item['display_name']}：发布{stats['published_post_count']}篇，"
                f"评论{stats['comment_count']}条"
            )
        lines.extend(("", "### 活跃评论账号", ""))
        for item in groups["active_comment_accounts"]:
            stats = item["current_investigation_statistics"]
            lines.append(
                f"- {item['display_name']}：评论{stats['comment_count']}条，"
                f"涉及{stats['commented_post_count']}篇帖子"
            )
        account_markdown = "\n".join(lines).rstrip() + "\n\n"
        marker = "## 结论\n"
        if marker in body_markdown:
            return body_markdown.replace(marker, account_markdown + marker, 1)
        return body_markdown.rstrip() + "\n\n" + account_markdown
