"""Report R3.1 with deterministic Account activity coverage and sections."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.reporting.account_entries import DEFAULT_ACCOUNT_FIXTURE_PATH
from backend.reporting.account_overview import (
    ReportAccountOverviewProjector,
    public_account_overview_projection,
)
from backend.reporting.errors import ReportValidationError
from backend.reporting.r2_graph import RiskFindingReportGraph
from backend.reporting.r3_graph import AccountEntryReportGraph
from backend.reporting.structured_contract import STRUCTURED_REPORT_SCHEMA_VERSION


class AccountOverviewReportGraph(AccountEntryReportGraph):
    """Add R3.1 presentation while retaining the R2 semantic model flow."""

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
        self.account_projector = None if getattr(source, "account_source", "") == "report_snapshot" else ReportAccountOverviewProjector.load(
            account_fixture_path
        )
        RiskFindingReportGraph.__init__(
            self,
            source=source,
            store=store,
            model_client=model_client,
            checkpoint_path=checkpoint_path,
            max_input_chars=max_input_chars,
        )

    def _build_report_account_entries(self, state) -> dict[str, Any]:
        snapshot = self._snapshot(state["report_version_id"])
        projector = self.account_projector or ReportAccountOverviewProjector.from_snapshot(snapshot)
        projection = projector.build(
            snapshot,
            target_account_identity=self.report_source.creator_account_identity(
                snapshot.task_id
            ),
            require_target=self.report_source.has_explicit_creator_target(snapshot.task_id),
        )
        self.store.record_run_event(
            state["run_id"],
            "build_report_account_entries",
            "validated",
            {
                **projection["statistics"],
                "completed_comment_count": sum(
                    item["current_investigation_statistics"]["comment_count"]
                    for item in projection["entries"]
                ),
                "risk_comment_count": sum(
                    item["current_investigation_statistics"]["risk_comment_count"]
                    for item in projection["entries"]
                ),
                "projection_hash": projection["projection_hash"],
            },
        )
        return {"report_account_projection": projection}

    def _assemble_report(self, state) -> dict[str, Any]:
        # Skip R3's Markdown insertion; the R3.1 ordered section tree is the
        # single presentation source for both Markdown and frontend JSON.
        result = RiskFindingReportGraph._assemble_report(self, state)
        assembled = result["assembled_report"]
        projection = state["report_account_projection"]
        public_projection = public_account_overview_projection(projection)
        sections = self._ordered_sections(state, public_projection)

        body_json = assembled["body_json"]
        body_json["audit_model"]["sections"] = sections
        body_json["account_model"] = public_projection
        document = AccountEntryReportGraph._public_report_document(
            self,
            state=state,
            title=assembled["title"],
            sections=sections,
            account_projection=public_projection,
        )
        document["schema_version"] = STRUCTURED_REPORT_SCHEMA_VERSION
        document.pop("provenance", None)
        for private_key in ("task_id", "dataset_id", "snapshot_ref"):
            document.get("report_metadata", {}).pop(private_key, None)
        document["ordered_sections"] = [
            {
                **public,
                "section_ref": section["section_ref"],
                "section_number": section["section_number"],
                "parent_section_ref": section["parent_section_ref"],
                "section_type": section["section_type"],
                "display_ordinal": section["display_ordinal"],
            }
            for public, section in zip(document["ordered_sections"], sections)
        ]
        document.pop("account_entries", None)
        document["account_coverage_statistics"] = public_projection[
            "account_coverage_statistics"
        ]
        document["target_account_entries"] = public_projection[
            "target_account_entries"
        ]
        document["default_active_comment_entries"] = public_projection[
            "default_active_comment_entries"
        ]
        document["full_account_index"] = public_projection[
            "full_account_index"
        ]
        document["account_scope_boundary"] = public_projection["scope_boundary"]
        if projection.get("account_corpus_schema_version") == "report-snapshot-accounts/v1":
            document["account_source"] = "report_snapshot"
        if "source_coverage" in self._snapshot(state["report_version_id"]).statistics:
            coverage = deepcopy(self._snapshot(state["report_version_id"]).statistics["source_coverage"])
            coverage["excluded_failed_posts"] = [
                {"post_id": item.get("note_id"), "status": item.get("analyze_status")}
                for item in coverage.get("excluded_failed_posts", [])
            ]
            document["statistics"] = deepcopy(document["statistics"])
            document["statistics"].pop("source_configuration", None)
            document["statistics"]["source_coverage"] = coverage
            document["source_coverage"] = coverage
        from backend.reporting.structured_contract import validate_structured_report_document
        validate_structured_report_document(document, account_model=public_projection)

        body_json["report_document"] = document
        assembled["body_json"] = body_json
        assembled["structured_sections"] = sections
        assembled["body_markdown"] = self._render_markdown(
            assembled["title"], sections, public_projection
        )
        return {"assembled_report": assembled}

    def _ordered_sections(
        self, state, account_projection: dict[str, Any]
    ) -> list[dict[str, Any]]:
        source_sections = {
            item["section_id"]: dict(item) for item in state["section_drafts"]
        }
        overview = source_sections.get("overview")
        finding_sections = sorted(
            (
                item
                for item in source_sections.values()
                if str(item.get("section_id") or "").startswith("investigation-")
                and item["section_id"] != "investigation-findings"
            ),
            key=lambda item: str(item["section_id"]),
        )
        standalone = source_sections.get("standalone-risk-posts")
        if standalone is None and not state.get("standalone_risk_posts"):
            risk_post_count = len(
                {
                    membership["post_ref"]
                    for finding in state.get("investigation_findings") or []
                    for membership in finding.get("post_memberships") or []
                }
            )
            standalone = {
                "section_id": "standalone-risk-posts",
                "title": "其他独立风险事项",
                "purpose": "说明当前报告没有未归入共性调查发现的风险帖子",
                "paragraphs": [
                    {
                        "text": (
                            f"当前{risk_post_count}篇风险帖子均已纳入主要调查发现，"
                            "本次没有未归入共性发现的独立风险事项。"
                        ),
                        "claim_ids": [],
                    }
                ],
                "claims": [],
                "case_blocks": [],
                "is_report_category": False,
            }
        synthesis = source_sections.get("synthesis")
        conclusion = source_sections.get("conclusion")
        missing = [
            name
            for name, value in (
                ("overview", overview),
                ("standalone-risk-posts", standalone),
                ("synthesis", synthesis),
                ("conclusion", conclusion),
            )
            if value is None
        ]
        if missing or not finding_sections:
            raise ReportValidationError(
                "assemble_report",
                ["the semantic draft does not contain the required R3.1 sections"],
            )

        snapshot = self._snapshot(state["report_version_id"])
        account_stats = account_projection["account_coverage_statistics"]
        completed_comments = sum(
            item["current_investigation_statistics"]["comment_count"]
            for item in state["report_account_projection"]["entries"]
        )
        risk_comments = sum(
            item["current_investigation_statistics"]["risk_comment_count"]
            for item in state["report_account_projection"]["entries"]
        )

        sections: list[dict[str, Any]] = []

        def add(
            *,
            section_id: str,
            section_number: str,
            title: str,
            section_type: str,
            parent_section_ref: str | None = None,
            source: dict[str, Any] | None = None,
            paragraphs: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            item = dict(source or {})
            item["section_id"] = section_id
            item["section_ref"] = f"section-{section_number.replace('.', '-')}"
            item["section_number"] = section_number
            item["parent_section_ref"] = parent_section_ref
            item["section_type"] = section_type
            item["section_kind"] = section_type
            item["display_ordinal"] = len(sections) + 1
            item["sort_order"] = len(sections)
            item["title"] = title
            item.setdefault("purpose", "")
            if paragraphs is not None:
                item["paragraphs"] = paragraphs
                item["claims"] = []
                item["case_blocks"] = []
                item["is_report_category"] = False
            item["body"] = "\n\n".join(
                str(paragraph.get("text") or "")
                for paragraph in item.get("paragraphs") or []
            )
            sections.append(item)
            return item

        add(
            section_id="overview",
            section_number="1",
            title="调查概况",
            section_type="overview",
            source=overview,
        )
        data = add(
            section_id="data-overview",
            section_number="2",
            title="数据概览",
            section_type="data_overview",
            paragraphs=[
                {
                    "text": "以下数据均来自本次调查的 canonical FrozenSnapshot。",
                    "claim_ids": [],
                }
            ],
        )
        add(
            section_id="content-comment-scale",
            section_number="2.1",
            title="内容与评论规模",
            section_type="deterministic_statistics",
            parent_section_ref=data["section_ref"],
            paragraphs=[
                {
                    "text": (
                        f"本报告纳入 {snapshot.statistics['canonical_posts']} 篇已完成审核的帖子，"
                        f"以及这些帖子冻结载荷中的 {completed_comments} 条已完成独立审核评论。"
                    ),
                    "claim_ids": [],
                }
            ],
        )
        decision = snapshot.statistics["decision"]
        risk_level = snapshot.statistics["risk_level"]
        add(
            section_id="review-risk-levels",
            section_number="2.2",
            title="审核结果与风险等级",
            section_type="deterministic_statistics",
            parent_section_ref=data["section_ref"],
            paragraphs=[
                {
                    "text": (
                        f"帖子审核结果为通过 {decision.get('pass', 0)} 篇、"
                        f"复审 {decision.get('review', 0)} 篇、拒绝 {decision.get('reject', 0)} 篇；"
                        f"帖子风险等级为高 {risk_level.get('high', 0)} 篇、中 {risk_level.get('medium', 0)} 篇、"
                        f"低 {risk_level.get('low', 0)} 篇、无 {risk_level.get('none', 0)} 篇。"
                    ),
                    "claim_ids": [],
                },
                {
                    "text": (
                        f"纳入报告的 {completed_comments} 条已审核评论中，"
                        f"评论自身风险等级为低、中或高的共有 {risk_comments} 条。"
                    ),
                    "claim_ids": [],
                },
            ],
        )
        add(
            section_id="account-activity-overview",
            section_number="2.3",
            title="账号活动概览",
            section_type="account_activity_overview",
            parent_section_ref=data["section_ref"],
            paragraphs=[
                {
                    "text": (
                        f"发布账号 {account_stats['post_author_account_count']}；"
                        f"评论账号 {account_stats['comment_author_account_count']}。"
                    ),
                    "claim_ids": [],
                },
                {"text": account_projection["scope_boundary"], "claim_ids": []},
            ],
        )
        # Reassembly retains the archived coverage prose alongside standard accounts.
        retained = state.get("retained_data_sections") or {}
        for section in sections:
            original = retained.get(section["section_id"])
            if original:
                for field in ("paragraphs", "body", "claims", "case_blocks"):
                    section[field] = original.get(field, [] if field != "body" else "")
        if "coverage-boundaries" in retained:
            add(
                section_id="coverage-boundaries", section_number="2.4",
                title=retained["coverage-boundaries"]["title"], section_type="data_quality",
                parent_section_ref=data["section_ref"], source=retained["coverage-boundaries"],
            )
        findings_parent = add(
            section_id="investigation-findings",
            section_number="3",
            title="主要调查发现",
            section_type="investigation_findings",
            paragraphs=[
                {
                    "text": (
                        f"本报告形成 {len(finding_sections)} 项主要调查发现；"
                        "各项关联帖子与 Evidence 关系保存在结构化报告中。"
                    ),
                    "claim_ids": [],
                }
            ],
        )
        if "investigation-findings" in retained:
            findings_parent["paragraphs"] = retained["investigation-findings"]["paragraphs"]
            findings_parent["body"] = retained["investigation-findings"]["body"]
        for index, source in enumerate(finding_sections, 1):
            add(
                section_id=source["section_id"],
                section_number=f"3.{index}",
                title=source["title"],
                section_type="investigation_finding",
                parent_section_ref=findings_parent["section_ref"],
                source=source,
            )
        add(
            section_id="standalone-risk-posts",
            section_number="4",
            title="其他独立风险事项",
            section_type="standalone_risk_posts",
            source=standalone,
        )
        add(
            section_id="synthesis",
            section_number="5",
            title="综合研判",
            section_type="synthesis",
            source=synthesis,
        )
        add(
            section_id="conclusion",
            section_number="6",
            title="调查结论与建议",
            section_type="conclusion",
            source=conclusion,
        )
        return sections

    @staticmethod
    def _render_markdown(
        title: str,
        sections: list[dict[str, Any]],
        account_projection: dict[str, Any],
    ) -> str:
        lines = [f"# {title}", ""]
        for section in sections:
            level = 3 if section["parent_section_ref"] else 2
            lines.extend(
                [
                    f"{'#' * level} {section['section_number']} {section['title']}",
                    "",
                ]
            )
            for paragraph in section.get("paragraphs") or []:
                text = str(paragraph.get("text") or "").strip()
                if text:
                    lines.extend([text, ""])
            if section["section_id"] == "account-activity-overview":
                targets = account_projection["target_account_entries"]
                if targets:
                    lines.extend(["#### 调查目标账号", ""])
                for entry in targets:
                    lines.append(f"- {entry['display_name']}")
                    lines.extend(
                        f"  - {text}" for text in entry["presentation_lines"]
                    )
                lines.extend(["", "#### 活跃评论账号", ""])
                for entry in account_projection[
                    "default_active_comment_entries"
                ]:
                    lines.append(f"- {entry['display_name']}")
                    lines.extend(
                        f"  - {text}" for text in entry["presentation_lines"]
                    )
                lines.append("")
            for case in section.get("case_blocks") or []:
                lines.extend(
                    [
                        f"#### {case.get('title') or '案例'}",
                        "",
                        str(case.get("text") or ""),
                        "",
                    ]
                )
        return "\n".join(lines).rstrip() + "\n"
