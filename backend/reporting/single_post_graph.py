"""One audited risk post: preserve its verdict and evidence without clustering."""

from backend.reporting.errors import ReportValidationError
from backend.reporting.pass_graph import PassReportGraph, SCOPE, partial_coverage_paragraphs

SINGLE_POST_TEMPLATE_VERSION = "report-single-risk-post/v1"
DECISIONS = {"pass": "通过", "review": "复审", "reject": "拒绝"}
RISKS = {"none": "无风险", "low": "低风险", "medium": "中风险", "high": "高风险"}


class SinglePostReportGraph(PassReportGraph):
    template_kind = "single_risk_post"
    template_version = SINGLE_POST_TEMPLATE_VERSION

    @staticmethod
    def _validate_snapshot(snapshot):
        if len(snapshot.posts) != 1 or len(snapshot.findings) != 1:
            raise ReportValidationError("validate_single_post", ["exactly one audited post is required"])
        finding = snapshot.findings[0]
        if finding.post_ref != snapshot.posts[0].ref:
            raise ReportValidationError("validate_single_post", ["audit does not belong to the sample"])
        result = finding.payload
        if result.get("decision") not in DECISIONS or result.get("risk_level") not in RISKS:
            raise ReportValidationError("validate_single_post", ["a complete audit verdict is required"])
        if result["decision"] == "pass" and result["risk_level"] == "none":
            raise ReportValidationError("validate_single_post", ["pass/none belongs to the all-pass template"])
        comments = snapshot.posts[0].payload.get("comments") or []
        completed = [c for c in comments if c.get("audit_status") == "completed" and c.get("risk_level") in RISKS]
        return {
            "total": len(comments), "completed": len(completed),
            "unreviewed": len(comments) - len(completed),
            "risk": sum(c["risk_level"] != "none" for c in completed),
        }

    def _draft_pass_report(self, state):
        if state["source_snapshot"].get("task_status") != "completed":
            raise ReportValidationError("validate_single_post", ["a completed audit task is required"])
        snapshot = self._snapshot(state["report_version_id"])
        comments = self._validate_snapshot(snapshot)
        result = snapshot.findings[0].payload
        verdict = DECISIONS[result["decision"]]
        risk = RISKS[result["risk_level"]]
        summary = f"本次共完成 1 条帖子的审核，审核决定为{verdict}，风险等级为{risk}。以下保留原审核结论及对应依据。"
        coverage = (
            f"本次资料包含 {comments['total']} 条评论，完成独立审核 {comments['completed']} 条，"
            f"其中风险评论 {comments['risk']} 条；其余 {comments['unreviewed']} 条尚无完整审核结果。"
        )
        sections = []

        def add(number, kind, title, paragraphs, parent=None):
            sections.append({
                "section_id": kind + "-" + number,
                "section_ref": "section-" + number.replace(".", "-"),
                "section_number": number, "parent_section_ref": parent,
                "section_type": kind, "section_kind": kind, "title": title,
                "display_ordinal": len(sections) + 1, "sort_order": len(sections),
                "paragraphs": [{"text": text, "claim_ids": []} for text in paragraphs],
                "claims": [], "case_blocks": [], "is_report_category": False,
                "body": "\n\n".join(paragraphs),
            })

        add("1", "overview", "调查概况", [summary])
        add("2", "data_overview", "数据概览", ["以下统计覆盖本次全部已审核帖子。"])
        add("2.1", "deterministic_statistics", "内容与评论规模", [coverage], "section-2")
        add("2.2", "deterministic_statistics", "审核结果与风险等级", [f"审核决定：{verdict}；风险等级：{risk}。"], "section-2")
        add("2.3", "account_activity_overview", "账号活动概览", ["仅统计本次样本中的发布账号，不推断账号全部历史活动。"], "section-2")
        partial_paragraphs = partial_coverage_paragraphs(snapshot)
        if partial_paragraphs:
            add("2.4", "coverage_diagnostics", "失败帖子与统计口径", partial_paragraphs, "section-2")
        add("3", "audit_samples", "本次审核发现与样本", [
            "本次仅审核 1 条帖子，作为独立样本展示；原审核说明及直接依据可在帖子详情中核对。",
            result.get("summary") or "原审核结果未提供文字说明。",
        ])
        add("4", "synthesis", "综合研判", [
            summary, "单条样本不足以推断跨帖子共性或账号整体风险。", SCOPE, coverage,
        ])
        add("5", "conclusion", "调查结论与建议", [
            f"本次样本的原审核决定为{verdict}，风险等级为{risk}。",
            "建议结合原文及审核依据复核该条内容；如需判断风险是否持续或具有共性，应扩大样本后继续审核。",
        ])
        return {
            "section_drafts": sections,
            "outline": {"report_title": snapshot.display_name + "调查报告"},
            "investigation_findings": [], "standalone_risk_posts": [],
        }
