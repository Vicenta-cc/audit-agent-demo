from __future__ import annotations

from typing import Any

from backend.reporting.contracts import (
    HumanReportCaseBlock,
    HumanReportCitationAction,
    HumanReportDTO,
    HumanReportKeyMetric,
    HumanReportSection,
    HumanReportTextBlock,
    SectionKind,
)
from backend.reporting.identity import make_report_claim_id


class HumanReportAssembler:
    def assemble(self, state: dict[str, Any]) -> tuple[HumanReportDTO, str]:
        drafts = state["section_drafts"]
        version_id = state["report_version_id"]
        overview = self._section_by_kind(drafts, SectionKind.OVERVIEW.value) or drafts[0]
        conclusion = self._section_by_kind(drafts, SectionKind.CONCLUSION.value) or drafts[-1]
        summary = self._single_text_block(version_id, overview)
        conclusion_block = self._single_text_block(version_id, conclusion)

        sections = []
        case_blocks = []
        for draft in drafts:
            kind = draft.get("section_kind")
            if kind in {SectionKind.OVERVIEW.value, SectionKind.CONCLUSION.value}:
                continue
            if kind == SectionKind.CASE_ANALYSIS.value:
                case_blocks.extend(self._case_blocks(version_id, draft))
                continue
            sections.append(
                HumanReportSection(
                    section_id=draft["section_id"],
                    title=draft["title"],
                    paragraphs=self._paragraphs(version_id, draft),
                )
            )

        human_report = HumanReportDTO(
            title=state["outline"]["report_title"],
            summary=summary,
            key_metrics=self._key_metrics(state["statistics"]["metrics"]),
            sections=tuple(sections),
            case_blocks=tuple(case_blocks),
            conclusion=conclusion_block,
            data_quality_note=HumanReportTextBlock(
                text=self._data_quality_note(state.get("warnings") or [])
            ),
        )
        return human_report, self._to_markdown(human_report)

    def _single_text_block(
        self, version_id: str, draft: dict[str, Any]
    ) -> HumanReportTextBlock:
        paragraphs = self._paragraphs(version_id, draft)
        return HumanReportTextBlock(
            text="\n\n".join(item.text for item in paragraphs),
            claim_ids=tuple(
                dict.fromkeys(
                    claim_id for item in paragraphs for claim_id in item.claim_ids
                )
            ),
        )

    def _paragraphs(
        self, version_id: str, draft: dict[str, Any]
    ) -> tuple[HumanReportTextBlock, ...]:
        claims = {item["claim_id"] for item in draft.get("claims") or []}
        return tuple(
            HumanReportTextBlock(
                text=str(item.get("text") or "").strip(),
                claim_ids=tuple(
                    make_report_claim_id(version_id, draft["section_id"], claim_id)
                    for claim_id in item.get("claim_ids") or []
                    if claim_id in claims
                ),
            )
            for item in draft.get("paragraphs") or []
            if str(item.get("text") or "").strip()
        )

    def _case_blocks(
        self, version_id: str, draft: dict[str, Any]
    ) -> tuple[HumanReportCaseBlock, ...]:
        claims = {item["claim_id"]: item for item in draft.get("claims") or []}
        output = []
        for case in draft.get("case_blocks") or []:
            report_claim_ids = tuple(
                make_report_claim_id(version_id, draft["section_id"], local_claim_id)
                for local_claim_id in case.get("claim_ids") or []
                if local_claim_id in claims
            )
            actions = tuple(
                HumanReportCitationAction(claim_id=claim_id)
                for claim_id in report_claim_ids
            )
            output.append(
                HumanReportCaseBlock(
                    title=case["title"],
                    text=case["text"],
                    claim_ids=report_claim_ids,
                    citation_actions=actions,
                )
            )
        return tuple(output)

    def _key_metrics(
        self, metrics: list[dict[str, Any]]
    ) -> tuple[HumanReportKeyMetric, ...]:
        def first(*, name: str, group_key: str = "", group_value: str = ""):
            for metric in metrics:
                if metric.get("metric_name") != name:
                    continue
                group = metric.get("group") or {}
                if not group_key or group.get(group_key) == group_value:
                    return metric
            return None

        total = next(
            (
                item
                for item in metrics
                if item.get("metric_name") == "scalar" and item.get("label") == "审核结果总数"
            ),
            None,
        )
        review_count = first(name="count", group_key="decision", group_value="review")
        review_percentage = first(
            name="percentage", group_key="decision", group_value="review"
        )
        medium = first(name="count", group_key="risk_level", group_value="medium")
        high = first(name="count", group_key="risk_level", group_value="high")
        none_count = first(name="count", group_key="risk_level", group_value="none")
        none_percentage = first(
            name="percentage", group_key="risk_level", group_value="none"
        )

        output = []
        if total:
            output.append(
                HumanReportKeyMetric(
                    label="分析内容",
                    value=f"{int(total['value'])}条",
                    metric_refs=(total["metric_key"],),
                )
            )
        if review_count:
            detail = (
                f"约{self._format_percentage(review_percentage['value'])}"
                if review_percentage
                else ""
            )
            refs = [review_count["metric_key"]]
            if review_percentage:
                refs.append(review_percentage["metric_key"])
            output.append(
                HumanReportKeyMetric(
                    label="进入复审",
                    value=f"{int(review_count['value'])}条",
                    detail=detail,
                    metric_refs=tuple(refs),
                )
            )
        if medium and high:
            output.append(
                HumanReportKeyMetric(
                    label="中高风险",
                    value=f"{int(medium['value'] + high['value'])}条",
                    detail=f"中风险{int(medium['value'])}条，高风险{int(high['value'])}条",
                    metric_refs=(medium["metric_key"], high["metric_key"]),
                )
            )
        if none_count:
            detail = (
                f"约{self._format_percentage(none_percentage['value'])}"
                if none_percentage
                else ""
            )
            refs = [none_count["metric_key"]]
            if none_percentage:
                refs.append(none_percentage["metric_key"])
            output.append(
                HumanReportKeyMetric(
                    label="未发现明显风险",
                    value=f"{int(none_count['value'])}条",
                    detail=detail,
                    metric_refs=tuple(refs),
                )
            )
        return tuple(output)

    @staticmethod
    def _format_percentage(value: float | int) -> str:
        rendered = f"{float(value):.1f}"
        return f"{rendered.removesuffix('.0')}%"

    @staticmethod
    def _data_quality_note(warnings: list[dict[str, Any]]) -> str:
        if not warnings:
            return "本次分析数据完整，未发现影响研判的数据缺失。"
        return (
            f"本次分析记录了{len(warnings)}项数据可用性提示；报告已基于可用结构化内容形成，"
            "相关原始材料的可用状态可在证据详情中核查。"
        )

    @staticmethod
    def _section_by_kind(
        drafts: list[dict[str, Any]], kind: str
    ) -> dict[str, Any] | None:
        return next((item for item in drafts if item.get("section_kind") == kind), None)

    @staticmethod
    def _to_markdown(report: HumanReportDTO) -> str:
        lines = [f"# {report.title}", "", report.summary.text, "", "## 调查概况", ""]
        for metric in report.key_metrics:
            detail = f"，{metric.detail}" if metric.detail else ""
            lines.append(f"- **{metric.label}**：{metric.value}{detail}")
        lines.append("")
        for section in report.sections:
            lines.extend((f"## {section.title}", ""))
            for paragraph in section.paragraphs:
                lines.extend((paragraph.text, ""))
        if report.case_blocks:
            lines.extend(("## 重点案例", ""))
            for case in report.case_blocks:
                lines.extend((f"### {case.title}", "", case.text, "", "【查看相关证据】", ""))
        lines.extend(("## 结论", "", report.conclusion.text, "", "---", "", report.data_quality_note.text))
        return "\n".join(lines).strip() + "\n"
