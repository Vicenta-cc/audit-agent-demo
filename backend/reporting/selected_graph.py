"""Formal R3.1 evidence report for a selection of existing audits.

Uses the same model, claim, number, citation and publication validators, with
coverage disclosures instead of account activity/person tracking sections.
"""
import json
from copy import deepcopy
from backend.domain.identity import stable_hash
from backend.reporting.r2_graph import RiskFindingReportGraph
from backend.reporting.r31_graph import AccountOverviewReportGraph


class SelectedEvidenceReportGraph(AccountOverviewReportGraph):
    def _validate_claim_support(self, state):
        updated = deepcopy(state)
        evidence = self._snapshot(state["report_version_id"]).evidence_by_ref
        bindings = []
        for section in updated["section_drafts"]:
            if section["section_id"].startswith("investigation-"):
                group = next(group for group in state["investigation_findings"] if section["section_id"] == "investigation-" + group["alias"].lower())
                allowed = {ref for member in group["post_memberships"] for ref in member["membership_evidence_refs"]}
            else:
                allowed = {ref for group in state["investigation_findings"] for member in group["post_memberships"] for ref in member["membership_evidence_refs"]}
            for claim in section.get("claims", []):
                for ref in claim.get("evidence_ids", []):
                    if ref in allowed and ref in evidence and evidence[ref].finding_ref not in claim["finding_ids"]:
                        claim["finding_ids"].append(evidence[ref].finding_ref)
                        bindings.append({"claim_id": claim["claim_id"], "evidence_id": ref, "parent_finding_id": evidence[ref].finding_ref})
        updated["claims"] = [claim for section in updated["section_drafts"] for claim in section.get("claims", [])]
        result = super()._validate_claim_support(updated)
        self.store.record_run_event(state["run_id"], "validate_claim_support", "evidence_parent_bindings", {"bindings": bindings})
        return {**result, "claims": updated["claims"], "section_drafts": updated["section_drafts"]}

    def _calculate_statistics(self, task_id):
        result = super()._calculate_statistics(task_id)
        coverage = self._snapshot_for_task(task_id).statistics["source_coverage"]
        for name, label, value in (("candidate_posts", "原始候选帖子数", coverage["candidate_posts"]), ("excluded_posts", "未完成帖子数", coverage["candidate_posts"] - coverage["selected_posts"])):
            metric = deepcopy(result["metrics"][0])
            metric.update(metric_key="metric:" + stable_hash({"snapshot": self._snapshot_for_task(task_id).snapshot_hash, "coverage": name}), label=label, value=value, denominator=coverage["candidate_posts"], denominator_name="source_candidate_post_count", group={"source_coverage": name})
            metric["source_hash"] = stable_hash({"coverage": coverage, "metric": name})
            result["metrics"].append(metric)
        return result

    def _validate_numbers(self, state):
        updated = deepcopy(state)
        updated["statistics"] = self._calculate_statistics(state["task_id"])
        # Bind model-authored coverage methodology to independently recomputed
        # source metrics, then run the unchanged number validator.
        coverage_claim = "本报告仅覆盖219个已完成帖子，原224个候选中有5个未完成，不能推断其安全或违规状态；评论样本不可外推至民族群体或平台总体，且未以新Prompt重审，仅汇总既有审核资料。"
        refs = [m["metric_key"] for m in updated["statistics"]["metrics"] if m["label"] in {"任务内容总数", "原始候选帖子数", "未完成帖子数"}]
        for claim in updated["claims"] + [c for section in updated["section_drafts"] for c in section.get("claims", [])]:
            if claim.get("claim_type") == "methodology" and claim.get("text") == coverage_claim:
                claim["metric_refs"] = refs
        result = super()._validate_numbers(updated)
        return {**result, "statistics": updated["statistics"], "claims": updated["claims"], "section_drafts": updated["section_drafts"]}

    def _publish_report_version(self, state):
        # The R2 publisher shares all evidence/coverage checks and does not
        # require or persist an account activity projection.
        return RiskFindingReportGraph._publish_report_version(self, {**state, "section_drafts": state["assembled_report"]["structured_sections"]})

    def __init__(self, *, source, store=None, model_client=None, checkpoint_path=None, **kwargs):
        RiskFindingReportGraph.__init__(self, source=source, store=store, model_client=model_client, checkpoint_path=checkpoint_path, **kwargs)

    def _build_report_account_entries(self, state):
        # Deliberately do not load an account corpus or project identities.
        projection = {"schema_version": "report-account-entry-r3.1/v1", "entries": [], "default_active_comment_limit": 0, "statistics": {"target_account_count": 0, "post_author_account_count": 0, "comment_author_account_count": 0, "distinct_account_count": 0, "default_active_comment_account_count": 0, "full_account_index_available": False}, "scope": "disabled_content_evidence_report"}
        projection["projection_hash"] = stable_hash(projection)
        return {"report_account_projection": projection}

    def _model_step(self, **kwargs):
        if kwargs["node_name"] == "draft_synthesis_conclusion":
            kwargs["max_tokens"] = 6000
        kwargs["messages"] = [{"role": "system", "content": (
            "本次是维汉婚姻及家庭讨论中内容与评论的证据分析。普通婚姻选择、民族自豪、文化差异和自愿家庭安排本身不等于攻击；"
            "沿用已有审核结果，不重新审核，不做民族身份画像或人员追踪。低关注不等于明确歧视或暴力。"
            "报告只覆盖219个已完成帖（181通过、38复核）；原224候选中5帖未完成，不能推断安全或违规。"
            "已存47044条评论，47043条完成，1条失败；需翻译689条，688完成、1失败，该失败来自旧任务且原帖仍保留。"
            "这是两次既有审核资料的汇总，两组Prompt来源不同，未以新Prompt重审。评论数不是平台总评论数，样本不可外推民族群体或平台总体。"
            "这些覆盖事实仅用于说明方法和局限，不得把它们编造成某项风险发现的直接证据。"
        )}, *kwargs["messages"]]
        failed_sections = {
            item["section_id"] for item in self.store.list_provider_exchanges(kwargs["state"]["report_version_id"])
            if "data_inspection_failed" in json.dumps(item.get("response") or item.get("response_json") or {}, ensure_ascii=False).lower()
        }
        if kwargs.get("section_id") and kwargs["section_id"] in failed_sections:
            messages = []
            for message in kwargs["messages"]:
                if "\n输入：\n" not in message["content"]:
                    messages.append(message)
                    continue
                prefix, raw = message["content"].split("\n输入：\n", 1)
                payload = json.loads(raw)
                for post in payload.get("posts", []):
                    post.pop("source_content", None)
                    post.pop("caption", None)
                    for evidence in post.get("direct_evidence", []):
                        evidence.pop("original_text", None)
                        evidence.pop("translated_text", None)
                messages.append({**message, "content": prefix + "\n本章写作输入仅含既有审核摘要和证据说明，未提供大段原文；不得生成逐字引语。完整原文保存在引用快照中。\n输入：\n" + json.dumps(payload, ensure_ascii=False)})
            kwargs["messages"] = messages
            self.store.record_run_event(kwargs["state"]["run_id"], kwargs["node_name"], "summary_only_writing_input", {"section_id": kwargs["section_id"], "original_evidence_preserved": True})
        return super()._model_step(**kwargs)

    def _ordered_sections(self, state, account_projection):
        sections = super()._ordered_sections(state, account_projection)
        coverage = self._snapshot(state["report_version_id"]).statistics["source_coverage"]
        comments = coverage["comments"]
        risk_comments = sum(int(post.payload["raw_content_payload"]["comment_audit_stats"].get("review_count", 0)) for post in self._snapshot(state["report_version_id"]).posts)
        for section in sections:
            if section["section_id"] == "data-overview":
                texts = ["以下统计来自本次保留的帖子、评论及既有审核结果。"]
            elif section["section_id"] == "content-comment-scale":
                texts = [f"报告覆盖219个唯一已完成帖：旧资料23帖、新资料196帖。原224个候选中另有5帖未形成完整结果，未纳入风险比例分母。", f"共保留{comments['total']}条已存评论，其中{comments['completed']}条审核完成、{comments['failed']}条失败；需翻译{comments['translation_required']}条，翻译完成{comments['translation_completed']}条、失败{comments['translation_failed']}条。失败评论位于旧资料，其父帖与原文仍保留。这里的评论数是已存一级评论数，不是平台总评论数。"]
            elif section["section_id"] == "review-risk-levels":
                texts = [section["paragraphs"][0]["text"], f"已完成审核评论中，按原评论审核统计有{risk_comments}条需复核；不把帖子复核结论自动赋予帖下每条评论。"]
            elif section["section_id"] == "account-activity-overview":
                section.update(section_id="coverage-boundaries", title="来源范围与未完成内容", section_type="data_quality", section_kind="data_quality")
                texts = ["实际来源词：维汉通婚18帖、维汉婚姻83帖、维族家里不同意39帖、维汉夫妻79帖。旧任务配置17个词，新增任务使用3个保存资料来源词；配置过的词不等于实际有返回结果的词。", "5个未完成帖中4帖因模型输入检查未通过、1帖因视频资源404未完成。未完成帖子不作安全或违规判断；正在另行采集的“维汉一家亲”不属于本报告。", "两轮采用相同的13条业务规则，但评论翻译实现的Prompt来源不同；本次未重爬、未重审，也没有补造缺失译文。普通婚姻选择、民族自豪、文化差异不自动视为攻击，内容风险不能外推民族群体、平台总体或作者现实身份。"]
            else:
                continue
            section["paragraphs"] = [{"text": text, "claim_ids": []} for text in texts]
            section["body"] = "\n\n".join(texts)
        return sections

    def _assemble_report(self, state):
        result = super()._assemble_report(state)
        body = result["assembled_report"]["body_json"]
        body.pop("account_model", None)
        document = body["report_document"]
        document["template_kind"] = "selected_existing_audits"
        for key in ("account_coverage_statistics", "target_account_entries", "default_active_comment_entries", "full_account_index", "account_scope_boundary"):
            document.pop(key, None)
        document["source_coverage"] = self._snapshot(state["report_version_id"]).statistics["source_coverage"]
        return result
