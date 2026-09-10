"""Deterministic reports for completed, pass/none audit samples."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph

from backend.reporting.contracts import ReportGraphState
from backend.reporting.errors import ReportValidationError
from backend.reporting.integration_graph import IntegrationReportGraph
from backend.reporting.r3_graph import AccountEntryReportGraph
from backend.reporting.structured_contract import (
    STRUCTURED_REPORT_SCHEMA_VERSION,
    validate_structured_report_document,
)

PASS_TEMPLATE_VERSION = "report-all-pass/v1"
SAMPLE_LIMIT = 3
SCOPE = "结论仅适用于本次实际完成审核的帖子、已审核评论及所用审核规则，不代表账号全部内容或未来内容均无风险。"


def safe_public_url(value):
    value = str(value or "").strip()
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username and not parsed.password else ""


def public_transcripts(videos):
    """Expose readable audit input, never provider metadata or local paths."""
    texts = []
    for video in videos or []:
        if not isinstance(video, dict):
            continue
        transcript = video.get("transcript") or video.get("asr_text")
        if isinstance(transcript, str) and transcript.strip():
            texts.append(transcript.strip())
        elif isinstance(transcript, dict):
            original = transcript.get("text")
            translation = transcript.get("text_zh")
            if isinstance(original, str) and original.strip():
                texts.append("语音转写原文：" + original.strip())
            if isinstance(translation, str) and translation.strip() and translation != original:
                texts.append("已有中文译文：" + translation.strip())
    return texts


def validate_pass_snapshot(snapshot):
    if not snapshot.posts or len(snapshot.findings) != len(snapshot.posts):
        raise ReportValidationError("validate_pass_snapshot", ["a nonempty, fully audited sample is required"])
    if {item.post_ref for item in snapshot.findings} != set(snapshot.post_by_ref):
        raise ReportValidationError("validate_pass_snapshot", ["audit results must cover every sample post exactly once"])
    if any(item.payload.get("decision") != "pass" or item.payload.get("risk_level") != "none" for item in snapshot.findings):
        raise ReportValidationError("validate_pass_snapshot", ["all sample posts must be pass/none"])
    comments = [comment for post in snapshot.posts for comment in post.payload.get("comments") or []]
    if any(comment.get("risk_level") in {"low", "medium", "high"} for comment in comments):
        raise ReportValidationError("validate_pass_snapshot", ["risk comments cannot be presented as an all-pass report"])
    # Direct risk evidence contradicts a pass/none conclusion. Do not silently
    # relabel it as a safe example or discard it to make the template fit.
    if snapshot.evidence:
        raise ReportValidationError("validate_pass_snapshot", ["pass/none samples must not contain direct risk evidence"])
    completed = [comment for comment in comments if comment.get("audit_status") == "completed" and comment.get("risk_level") == "none"]
    return {"total": len(comments), "completed": len(completed), "unreviewed": len(comments) - len(completed)}


class PassReportGraph(IntegrationReportGraph):
    template_kind = "all_pass"
    template_version = PASS_TEMPLATE_VERSION
    _validate_snapshot = staticmethod(validate_pass_snapshot)

    def __init__(self, *, source, store=None, model_client=None, checkpoint_path=None):
        # No provider is constructed or invoked. Generation metadata records the
        # template rather than attributing these deterministic sections to Qwen.
        super().__init__(source=source, store=store, checkpoint_path=checkpoint_path,
                         model_client=SimpleNamespace(model="deterministic", prompt_version=self.template_version))

    def _build_graph(self):
        graph = StateGraph(ReportGraphState)
        steps = {
            "freeze_source_snapshot": self._freeze_source_snapshot,
            "build_statistics": self._build_statistics,
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

    def _draft_pass_report(self, state):
        if state["source_snapshot"].get("task_status") != "completed":
            raise ReportValidationError(
                "validate_pass_snapshot", ["all-pass reporting requires a completed audit task"]
            )
        snapshot = self._snapshot(state["report_version_id"])
        comments = self._validate_snapshot(snapshot)
        count = len(snapshot.posts)
        sample_count = min(count, SAMPLE_LIMIT)
        summary = f"本次共完成 {count} 条帖子的审核，全部判定为通过，风险等级为无风险。未在本次已审核样本中检出所用审核规则覆盖的风险。"
        comment_note = f"本次资料包含 {comments['total']} 条评论，其中 {comments['completed']} 条完成独立审核并判定为无风险。"
        if comments["unreviewed"]:
            comment_note += f"其余 {comments['unreviewed']} 条评论尚无完整审核结果，不纳入无风险结论。"
        sections = []

        def section(number, kind, title, paragraphs, parent=None):
            sections.append({
                "section_id": kind + "-" + number, "section_ref": "section-" + number.replace(".", "-"),
                "section_number": number, "parent_section_ref": parent,
                "section_type": kind, "section_kind": kind, "title": title,
                "display_ordinal": len(sections) + 1, "sort_order": len(sections),
                "paragraphs": [{"text": text, "claim_ids": []} for text in paragraphs],
                "claims": [], "case_blocks": [], "is_report_category": False,
                "body": "\n\n".join(paragraphs),
            })

        section("1", "overview", "调查概况", [summary])
        section("2", "data_overview", "数据概览", ["以下统计覆盖本次全部已审核帖子，样本展示不改变统计范围。"])
        section("2.1", "deterministic_statistics", "内容与评论规模", [f"纳入报告帖子 {count} 条。" + comment_note], "section-2")
        section("2.2", "deterministic_statistics", "审核结果与风险等级", [f"通过 {count} 条，复审 0 条，拒绝 0 条；无风险 {count} 条，低、中、高风险均为 0 条。"], "section-2")
        section("2.3", "account_activity_overview", "账号活动概览", ["仅展示本次样本中记录的发布账号与发布数量，不据此推断账号整体立场或历史活动。"], "section-2")
        sample_text = "本次仅审核 1 条帖子，以下完整展示该样本及已有审核结论。" if count == 1 else f"按本次资料冻结顺序展示前 {sample_count} 条已审核样本，全部 {count} 条帖子可在附录查阅。"
        section("3", "audit_samples", "审核结果与样本展示", [sample_text])
        # The same frozen fields also remain readable in the chat's text report.
        findings = {finding.post_ref: finding for finding in snapshot.findings}
        for post in snapshot.posts[:SAMPLE_LIMIT]:
            result = findings[post.ref].payload
            text = f"{post.payload.get('title') or '未命名帖子'}：审核通过。{result.get('summary') or '原审核结果未提供文字说明。'}"
            sections[-1]["paragraphs"].append({"text": text, "claim_ids": []})
            sections[-1]["body"] += "\n\n" + text
        section("4", "synthesis", "综合研判", [summary, SCOPE, comment_note])
        section("5", "conclusion", "调查结论与建议", [f"本次 {count} 条已审核帖子均通过审核，未形成需要展开的风险事项。", "本报告保留原有审核结论与原文入口供核对；如需扩大结论范围，应补充样本并完成对应审核。"])
        return {"section_drafts": sections, "outline": {"report_title": snapshot.display_name + "调查报告"},
                "investigation_findings": [], "standalone_risk_posts": []}

    def _assemble_pass_report(self, state):
        snapshot = self._snapshot(state["report_version_id"])
        comments = self._validate_snapshot(snapshot)
        result = super()._assemble_report(state)
        account_model = {
            "projection_hash": snapshot.snapshot_hash,
            "account_coverage_statistics": {}, "default_active_comment_entries": [],
            "full_account_index": {"entries": [], "total_count": 0},
            "scope_boundary": SCOPE, "target_account_entries": [],
        }
        document = AccountEntryReportGraph._public_report_document(
            self, state=state, title=state["outline"]["report_title"],
            sections=state["section_drafts"], account_projection=account_model,
        )
        document["schema_version"] = STRUCTURED_REPORT_SCHEMA_VERSION
        document["template_kind"] = self.template_kind
        document["template_version"] = self.template_version
        document["report_metadata"]["investigation_scope"] = SCOPE
        document.pop("provenance", None)
        document.pop("account_entries", None)
        for key in ("account_coverage_statistics", "default_active_comment_entries", "full_account_index", "target_account_entries"):
            document[key] = account_model[key]
        document["account_scope_boundary"] = SCOPE
        document["comment_audit_coverage"] = comments
        # Account summaries use stable source identity when present. Missing IDs
        # remain separate occurrences; identical nicknames are never merged.
        authors = {}
        for post in snapshot.posts:
            raw = post.payload.get("raw_content_payload") or {}
            author = raw.get("author") or {}
            key = (post.payload.get("platform"), author.get("sec_uid") or author.get("user_id") or post.ref)
            row = authors.setdefault(key, {"display_name": post.payload.get("author") or "未记录作者", "published_post_count": 0})
            row["published_post_count"] += 1
        document["snapshot_account_summary"] = {"entries": list(authors.values()), "scope": "仅统计本次已审核样本中的发布记录；缺少稳定账号标识时不合并同名账号。"}
        for public, post in zip(document["posts"], snapshot.posts):
            public["published_at"] = post.payload.get("published_at") or ""
            public["source_url"] = safe_public_url(post.payload.get("url"))
            content = post.payload.get("source_content") or {}
            public["original_text"] = str(content.get("desc") or content.get("title") or post.payload.get("source_title") or "")
            public["transcripts"] = public_transcripts(content.get("video_results"))
        for public, section in zip(document["ordered_sections"], state["section_drafts"]):
            public.update({key: section[key] for key in ("section_ref", "section_number", "parent_section_ref", "section_type", "display_ordinal")})
            if section["section_type"] == "audit_samples":
                public["sample_post_refs"] = [post["post_ref"] for post in document["posts"][:SAMPLE_LIMIT]]
                public["paragraphs"] = public["paragraphs"][:1]
        validate_structured_report_document(document, account_model=account_model)
        result["assembled_report"]["body_json"].update({"report_document": document, "account_model": account_model})
        return result

    def _publish_report_version(self, state):
        # Recheck the frozen source on resume, before making a report public.
        self._validate_snapshot(self._snapshot(state["report_version_id"]))
        body = state["assembled_report"]["body_json"]
        validate_structured_report_document(body["report_document"], account_model=body["account_model"])
        return super()._publish_report_version(state)
