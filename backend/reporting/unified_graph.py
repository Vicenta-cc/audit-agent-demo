"""Deterministic report template for every newly created 1..5 post task."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from langgraph.graph import END, START, StateGraph

from backend.reporting.account_overview import (
    ReportAccountOverviewProjector,
    public_account_overview_projection,
)
from backend.reporting.comment_statistics import snapshot_comment_coverage
from backend.reporting.contracts import ReportGraphState
from backend.reporting.errors import ReportValidationError
from backend.reporting.pass_graph import PassReportGraph, partial_coverage_paragraphs
from backend.reporting.structured_contract import validate_structured_report_document


UNIFIED_REPORT_TEMPLATE_VERSION = "report-unified-audit/v1"
UNIFIED_REPORT_TEMPLATE_KIND = "unified_audit"
DECISION_LABELS = {"pass": "通过", "review": "复审", "reject": "拒绝"}
RISK_LABELS = {"none": "无风险", "low": "低风险", "medium": "中风险", "high": "高风险"}


def _finding_value(finding: Any, key: str) -> str:
    if isinstance(finding, dict):
        if key in finding:
            return str(finding.get(key) or "")
        payload = finding.get("payload") or {}
        return str(payload.get(key) or "")
    payload = getattr(finding, "payload", None)
    if isinstance(payload, dict) and key in payload:
        return str(payload.get(key) or "")
    return str(getattr(finding, key, "") or "")


def classify_unified_findings(findings: Iterable[Any]) -> dict[str, list[str]]:
    """Assign every finding to exactly one deterministic presentation group."""

    groups: dict[str, list[str]] = {
        "reject": [],
        "review": [],
        "safe": [],
        "pending": [],
    }
    for finding in findings:
        ref = _finding_value(finding, "ref")
        decision = _finding_value(finding, "decision")
        risk = _finding_value(finding, "risk_level")
        if decision == "reject":
            group = "reject"
        elif decision == "review":
            group = "review"
        elif decision == "pass" and risk == "none":
            group = "safe"
        else:
            # A pass verdict carrying a non-none risk level is internally
            # inconsistent. Preserve it for review instead of calling it safe.
            group = "pending"
        groups[group].append(ref)
    return groups


class UnifiedAuditReportGraph(PassReportGraph):
    """Publish one stable report shape without asking a model to format it."""

    template_kind = UNIFIED_REPORT_TEMPLATE_KIND
    template_version = UNIFIED_REPORT_TEMPLATE_VERSION

    def _build_graph(self):
        graph = StateGraph(ReportGraphState)
        steps = {
            "freeze_source_snapshot": self._freeze_source_snapshot,
            "build_statistics": self._build_statistics,
            "build_unified_account_overview": (
                self._build_unified_account_overview
            ),
            "draft_pass_report": self._draft_pass_report,
            "assemble_pass_report": self._assemble_pass_report,
            "publish_report_version": self._publish_report_version,
        }
        previous = START
        for name, method in steps.items():
            graph.add_node(name, self._wrap_node(name, method))
            graph.add_edge(previous, name)
            previous = name
        graph.add_edge(previous, END)
        return graph

    def _build_unified_account_overview(self, state):
        snapshot = self._snapshot(state["report_version_id"])
        projection = ReportAccountOverviewProjector.from_snapshot(
            snapshot
        ).build(
            snapshot,
            target_account_identity=self.report_source.creator_account_identity(
                snapshot.task_id
            ),
            require_target=self.report_source.has_explicit_creator_target(
                snapshot.task_id
            ),
            allow_unresolved_comment_accounts=True,
        )
        self.store.record_run_event(
            state["run_id"],
            "build_unified_account_overview",
            "validated",
            {
                **projection["statistics"],
                "projection_hash": projection["projection_hash"],
            },
        )
        return {"report_account_projection": projection}

    @staticmethod
    def _validate_snapshot(snapshot):
        if not 1 <= len(snapshot.posts) <= 5:
            raise ReportValidationError(
                "validate_unified_snapshot", ["1 to 5 audited posts are required"]
            )
        if len(snapshot.findings) != len(snapshot.posts):
            raise ReportValidationError(
                "validate_unified_snapshot",
                ["every frozen post must have exactly one audit finding"],
            )
        post_refs = [post.ref for post in snapshot.posts]
        finding_post_refs = [finding.post_ref for finding in snapshot.findings]
        if len(set(finding_post_refs)) != len(finding_post_refs) or set(
            finding_post_refs
        ) != set(post_refs):
            raise ReportValidationError(
                "validate_unified_snapshot",
                ["audit findings must cover every frozen post exactly once"],
            )
        invalid = [
            finding.ref
            for finding in snapshot.findings
            if finding.payload.get("decision") not in DECISION_LABELS
            or finding.payload.get("risk_level") not in RISK_LABELS
        ]
        if invalid:
            raise ReportValidationError(
                "validate_unified_snapshot",
                ["every post must have a supported decision and risk level"],
            )

        findings = {finding.post_ref: finding for finding in snapshot.findings}
        for post in snapshot.posts:
            finding = findings[post.ref]
            if not (
                finding.payload.get("decision") == "pass"
                and finding.payload.get("risk_level") == "none"
            ):
                continue
            comments = post.payload.get("comments") or []
            if any(
                comment.get("audit_status") == "completed"
                and comment.get("risk_level") in {"low", "medium", "high"}
                for comment in comments
            ):
                raise ReportValidationError(
                    "validate_unified_snapshot",
                    ["a safe post cannot contain a completed risk comment"],
                )
        return snapshot_comment_coverage(post.payload for post in snapshot.posts)

    @staticmethod
    def _section(
        sections: list[dict[str, Any]],
        *,
        number: str,
        kind: str,
        title: str,
        paragraphs: list[str],
        claims: list[dict[str, Any]] | None = None,
    ) -> None:
        sections.append(
            {
                "section_id": kind + "-" + number,
                "section_ref": "section-" + number.replace(".", "-"),
                "section_number": number,
                "parent_section_ref": None,
                "section_type": kind,
                "section_kind": kind,
                "title": title,
                "purpose": "",
                "display_ordinal": len(sections) + 1,
                "sort_order": len(sections),
                "paragraphs": [
                    {"text": paragraph, "claim_ids": []}
                    for paragraph in paragraphs
                ],
                "claims": claims or [],
                "case_blocks": [],
                "is_report_category": False,
                "body": "\n\n".join(paragraphs),
            }
        )

    @staticmethod
    def _post_claim(snapshot, finding) -> tuple[str, dict[str, Any]]:
        post = snapshot.post_by_ref[finding.post_ref]
        decision = str(finding.payload.get("decision") or "")
        risk = str(finding.payload.get("risk_level") or "")
        summary = str(
            finding.payload.get("summary") or "原审核结果未提供文字说明。"
        )
        related_evidence = [
            evidence
            for evidence in snapshot.evidence
            if evidence.finding_ref == finding.ref
        ]
        evidence_notes = []
        for evidence in related_evidence[:3]:
            payload = evidence.payload
            excerpt = str(
                payload.get("summary")
                or payload.get("reason")
                or payload.get("translated_text")
                or payload.get("original_text")
                or ""
            ).strip()
            if excerpt:
                evidence_notes.append(excerpt[:240])
        basis = (
            "证据依据：" + "；".join(evidence_notes) + "。"
            if evidence_notes
            else "当前冻结快照未提供独立证据条目，结论依据为原审核说明。"
        )
        text = (
            f"{post.payload.get('title') or '未命名帖子'}："
            f"{DECISION_LABELS[decision]}，{RISK_LABELS[risk]}。{summary}{basis}"
        )
        evidence_ids = [evidence.ref for evidence in related_evidence]
        claim = {
            "claim_id": "claim-" + finding.ref.rsplit(":", 1)[-1],
            "claim_type": "domain_fact",
            "text": text,
            "support_type": "audit_result",
            "finding_ids": [finding.ref],
            "evidence_ids": evidence_ids,
            "metric_refs": [],
        }
        return text, claim

    def _draft_pass_report(self, state):
        if state["source_snapshot"].get("task_status") != "completed":
            raise ReportValidationError(
                "validate_unified_snapshot",
                ["unified reporting requires a completed audit task"],
            )
        snapshot = self._snapshot(state["report_version_id"])
        comment_coverage = self._validate_snapshot(snapshot)
        groups = classify_unified_findings(snapshot.findings)
        findings = {finding.ref: finding for finding in snapshot.findings}
        counts = Counter(
            finding.payload.get("decision") for finding in snapshot.findings
        )
        safe_count = len(groups["safe"])
        pending_count = len(groups["pending"])
        sections: list[dict[str, Any]] = []

        platform = (
            "抖音"
            if snapshot.posts[0].payload.get("platform") in {"dy", "douyin"}
            else (snapshot.posts[0].payload.get("platform") or "未记录平台")
        )
        outcome_parts = []
        for count, label in (
            (safe_count, "条审核通过"),
            (counts["review"], "条建议复审"),
            (counts["reject"], "条建议拒绝"),
            (pending_count, "条待确认"),
        ):
            if count:
                outcome_parts.append(f"{count} {label}")

        self._section(
            sections,
            number="1",
            kind="overview",
            title="报告概览",
            paragraphs=[
                f"本报告汇总任务“{snapshot.display_name}”在{platform}平台完成的 "
                f"{len(snapshot.posts)} 条帖子审核结果：{'、'.join(outcome_parts)}。",
            ],
        )

        risk_refs = groups["reject"] + groups["review"]
        if risk_refs:
            paragraphs = [
                f"本次共有 {len(risk_refs)} 条风险帖子，以下按处置优先级展示。"
            ]
            claims = []
            for label, key in (("建议拒绝", "reject"), ("建议复审", "review")):
                if not groups[key]:
                    continue
                paragraphs.append(f"{label}（{len(groups[key])} 条）")
                for ref in groups[key]:
                    text, claim = self._post_claim(snapshot, findings[ref])
                    paragraphs.append(text)
                    claims.append(claim)
            self._section(
                sections,
                number="2",
                kind="risk_post_analysis",
                title="风险帖子分析",
                paragraphs=paragraphs,
                claims=claims,
            )

        if groups["safe"]:
            paragraphs = [
                f"本次共有 {len(groups['safe'])} 条帖子审核通过。"
            ]
            claims = []
            for ref in groups["safe"]:
                text, claim = self._post_claim(snapshot, findings[ref])
                paragraphs.append(text)
                claims.append(claim)
            self._section(
                sections,
                number="3",
                kind="safe_post_analysis",
                title="安全帖子分析",
                paragraphs=paragraphs,
                claims=claims,
            )

        if groups["pending"]:
            paragraphs = [
                "以下帖子存在审核决定与风险等级不一致的情况，不能直接归为安全。"
            ]
            claims = []
            for ref in groups["pending"]:
                text, claim = self._post_claim(snapshot, findings[ref])
                paragraphs.append(text)
                claims.append(claim)
            self._section(
                sections,
                number="4",
                kind="pending_post_analysis",
                title="待确认帖子分析",
                paragraphs=paragraphs,
                claims=claims,
            )

        account_statistics = state["report_account_projection"]["statistics"]
        self._section(
            sections,
            number="5",
            kind="account_activity_overview",
            title="账号关联分析",
            paragraphs=[
                f"本次冻结样本记录到具有稳定标识的发布账号 "
                f"{account_statistics['post_author_account_count']} 个、评论账号 "
                f"{account_statistics['comment_author_account_count']} 个，去重后共 "
                f"{account_statistics['distinct_account_count']} 个账号。"
                "报告正文展示当前报告内的发布与评论活动；同昵称不会被强行合并。"
            ],
        )
        recommendations = []
        if counts["reject"]:
            recommendations.append("优先复核并处置建议拒绝的帖子及其直接证据。")
        if counts["review"]:
            recommendations.append("对建议复审的帖子补充上下文后再作最终处置。")
        if groups["pending"]:
            recommendations.append("先解决审核决定与风险等级不一致的问题。")
        if not recommendations:
            recommendations.append("保留本次审核记录；扩大结论范围前应补充样本并重新审核。")
        self._section(
            sections,
            number="6",
            kind="conclusion",
            title="审核建议",
            paragraphs=recommendations,
        )
        if comment_coverage.get("available"):
            comment_total = comment_coverage.get("total") or 0
            comment_completed = comment_coverage.get("completed") or 0
            if comment_total == 0:
                comment_scope = "本次没有保存可供审核的评论。"
            elif comment_total == comment_completed:
                comment_scope = f"本次保存的 {comment_total} 条评论均已完成审核。"
            else:
                comment_scope = (
                    f"本次共保存 {comment_total} 条评论，其中 {comment_completed} 条已完成审核；"
                    "未完成审核的评论不作为安全依据。"
                )
        else:
            comment_scope = (
                "部分帖子的评论列表没有完整保存，因此不能据此判断“没有评论风险”。"
            )
        methodology_paragraphs = [
            f"本报告只覆盖当前列出的 {len(snapshot.posts)} 条帖子和已经完成审核的评论，"
            "不代表相关账号的其他内容或后续内容。",
            "报告由系统根据生成时保存的审核结果自动整理；"
            "每条结论都可以回到对应帖子和原审核记录核对。",
            comment_scope,
            *partial_coverage_paragraphs(snapshot),
        ]
        self._section(
            sections,
            number="7",
            kind="methodology",
            title="方法、范围与限制",
            paragraphs=methodology_paragraphs,
        )
        self._section(
            sections,
            number="8",
            kind="appendix",
            title="附录",
            paragraphs=[
                (
                    f"附录收录本报告全部 {len(snapshot.posts)} 条帖子、审核结论和相关依据，"
                    "可用于逐条核对。"
                    if snapshot.evidence
                    else f"附录收录本报告全部 {len(snapshot.posts)} 条帖子及其审核结论，"
                    "可用于逐条核对。"
                )
            ],
        )

        standalone = [
            {
                "post_ref": findings[ref].post_ref,
                "audit_finding_ref": ref,
                "disposition_note": "该帖保留原审核结论与依据，不进行模型聚类。",
            }
            for ref in risk_refs
        ]
        return {
            "section_drafts": sections,
            "outline": {"report_title": snapshot.display_name + "审核报告"},
            "investigation_findings": [],
            "standalone_risk_posts": standalone,
            "risk_post_coverage_complete": True,
        }

    def _assemble_pass_report(self, state):
        result = super()._assemble_pass_report(state)
        body = result["assembled_report"]["body_json"]
        audit_model = body["audit_model"]
        audit_model["investigation_findings"] = []
        audit_model["standalone_risk_posts"] = list(
            state.get("standalone_risk_posts") or []
        )
        audit_model["risk_post_coverage_complete"] = True
        account_projection = state["report_account_projection"]
        public_projection = public_account_overview_projection(
            account_projection
        )
        document = body["report_document"]
        body["account_model"] = public_projection
        for key in (
            "account_coverage_statistics",
            "default_active_comment_entries",
            "full_account_index",
            "target_account_entries",
        ):
            document[key] = public_projection[key]
        document["account_scope_boundary"] = public_projection[
            "scope_boundary"
        ]
        document["account_source"] = "report_snapshot"
        document.pop("snapshot_account_summary", None)
        validate_structured_report_document(
            document,
            account_model=public_projection,
        )
        return result

    def _publish_report_version(self, state):
        self._validate_snapshot(self._snapshot(state["report_version_id"]))
        assembled = state["assembled_report"]
        body = assembled["body_json"]
        validate_structured_report_document(
            body["report_document"], account_model=body["account_model"]
        )
        version = self.store.publish_version(
            report_version_id=state["report_version_id"],
            title=assembled["title"],
            body_markdown=assembled["body_markdown"],
            body_json=body,
            sections=state["section_drafts"],
            citation_details=state.get("citation_details") or {},
            categories=body["audit_model"].get("categories") or [],
            investigation_findings=[],
            standalone_risk_posts=state.get("standalone_risk_posts") or [],
            account_projection=state["report_account_projection"],
        )
        self.store.update_run(
            state["run_id"],
            status="completed",
            current_node="publish_report_version",
            warnings=state.get("warnings") or [],
        )
        assembled["content_hash"] = version["content_hash"]
        return {"assembled_report": assembled}
