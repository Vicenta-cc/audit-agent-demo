from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .contracts import RuleSetContent
from .errors import RuleSetValidationError

SYSTEM_TEMPLATE_VERSION = "audit-system-template-v2"
COMPILER_VERSION = "ruleset-compiler-v2"

_STAGES = (
    "image_evidence",
    "video_frame_evidence",
    "comment_audit",
    "fusion_audit",
)
_CAPABILITY_ORDER = ("text", "ocr", "asr", "vision", "comment")
FIXED_PROMPT_LIMITS = {
    "image_evidence": 8_000,
    "video_frame_evidence": 8_000,
    "comment_audit": 10_000,
    "fusion_audit": 12_000,
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(content: RuleSetContent | dict) -> str:
    model = content if isinstance(content, RuleSetContent) else RuleSetContent.model_validate(content)
    return hashlib.sha256(canonical_json(model.model_dump(mode="json")).encode("utf-8")).hexdigest()


@dataclass
class _ContentCompileResult:
    prompt_profile_snapshot: dict
    general_exemptions: list[dict]
    decision_rules: list[dict]
    stage_routes: dict[str, list[str]]


def _validate_compile_content(
    content: RuleSetContent | dict,
    system_template_version: str,
    compiler_version: str,
) -> RuleSetContent:
    if system_template_version != SYSTEM_TEMPLATE_VERSION:
        raise RuleSetValidationError(f"unsupported system_template_version: {system_template_version}")
    if compiler_version != COMPILER_VERSION:
        raise RuleSetValidationError(f"unsupported compiler_version: {compiler_version}")
    content = RuleSetContent.model_validate(content)
    if content.domain != "gambling":
        raise RuleSetValidationError("RuleSet Foundation pilot only supports the gambling domain")
    return content


def compile_ruleset_revision(
    revision: dict,
    *,
    audit_policy: dict | None = None,
    system_template_version: str = SYSTEM_TEMPLATE_VERSION,
    compiler_version: str = COMPILER_VERSION,
) -> dict:
    if str(revision.get("status") or "") != "published":
        raise RuleSetValidationError("only a published RuleSetRevision can be compiled")
    # Validate before formal consistency checks to preserve their error precedence.
    content = _validate_compile_content(
        _revision_content(revision), system_template_version, compiler_version
    )
    computed_content_hash = content_hash(content)
    stored_content_hash = str(revision.get("content_hash") or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(stored_content_hash):
        raise RuleSetValidationError("RuleSetRevision content_hash must be a 64-character SHA-256")
    if stored_content_hash != computed_content_hash:
        raise RuleSetValidationError("RuleSetRevision content_hash does not match its immutable content")

    policy = _normalize_policy(audit_policy or {})
    revision_id = str(revision.get("id") or "").strip()
    if policy["ruleset_revision_id"] and policy["ruleset_revision_id"] != revision_id:
        raise RuleSetValidationError(
            "AuditPolicy RuleSetRevision reference does not match the compiled revision"
        )
    compiled = compile_ruleset_content(
        content,
        system_template_version=system_template_version,
        compiler_version=compiler_version,
    )
    ruleset_ref = {
        "ruleset_id": str(revision.get("ruleset_id") or ""),
        "revision_id": revision_id,
        "version": int(revision.get("version") or 0),
        "content_hash": computed_content_hash,
    }
    rule_snapshot = {
        "schema_version": 2,
        "ruleset_ref": ruleset_ref,
        "general_exemptions": compiled.general_exemptions,
        "decision_rules": compiled.decision_rules,
        "scoring_rules": [],
        "thresholds": policy["thresholds"],
        "stage_routes": compiled.stage_routes,
    }
    hash_payload = {
        "schema_version": 2,
        "audit_policy": policy,
        "ruleset_revision": ruleset_ref,
        "rule_snapshot": rule_snapshot,
        "prompt_profile_snapshot": compiled.prompt_profile_snapshot,
        "system_template_version": system_template_version,
        "compiler_version": compiler_version,
    }
    config_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
    return {
        "schema_version": 2,
        "audit_policy_snapshot": policy,
        "prompt_profile_snapshot": compiled.prompt_profile_snapshot,
        "rule_snapshot": rule_snapshot,
        "config_hash": config_hash,
    }


def compile_ruleset_content(
    content: RuleSetContent | dict,
    *,
    system_template_version: str = SYSTEM_TEMPLATE_VERSION,
    compiler_version: str = COMPILER_VERSION,
) -> _ContentCompileResult:
    """Compile content with the existing gambling pilot templates, without revision identity."""
    content = _validate_compile_content(content, system_template_version, compiler_version)
    rules = _enabled_rules(content)
    stage_routes = {
        stage: [rule["rule_id"] for rule in rules if stage in rule["application_stages"]]
        for stage in _STAGES
    }
    general_exemptions = [item.model_dump(mode="json") for item in content.general_exemptions]
    stage_payloads = {
        stage: {
            "ruleset": {
                "name": content.name,
                "domain": content.domain,
                "audit_goal": content.audit_goal,
            },
            "general_exemptions": general_exemptions,
            "rules": [_prompt_rule(rule) for rule in rules if stage in rule["application_stages"]],
        }
        for stage in _STAGES
    }

    prompt_profile_snapshot = {
        "schema_version": 2,
        "category_id": content.domain,
        "audit_goal": content.audit_goal,
        "image_prompt": _image_prompt(stage_payloads["image_evidence"]),
        "frame_prompt": _frame_prompt(stage_payloads["video_frame_evidence"]),
        "comment_prompt_template": _comment_prompt_template(stage_payloads["comment_audit"]),
        "fusion_prompt_template": _fusion_prompt_template(stage_payloads["fusion_audit"]),
        "system_template_version": system_template_version,
        "compiler_version": compiler_version,
        "stage_rule_ids": stage_routes,
        "libraries": [
            {
                "id": content.domain,
                "title": content.name,
                "audit_goal": content.audit_goal,
                "output_labels": [category.name for category in content.categories],
            }
        ],
    }
    _validate_fixed_prompt_budgets(prompt_profile_snapshot)
    prompt_profile_snapshot["fixed_prompt_chars"] = {
        "image_evidence": len(prompt_profile_snapshot["image_prompt"]),
        "video_frame_evidence": len(prompt_profile_snapshot["frame_prompt"]),
        "comment_audit": len(prompt_profile_snapshot["comment_prompt_template"]),
        "fusion_audit": len(prompt_profile_snapshot["fusion_prompt_template"]),
    }
    prompt_digest = hashlib.sha256(canonical_json(prompt_profile_snapshot).encode("utf-8")).hexdigest()[:16]
    prompt_profile_snapshot["prompt_version"] = f"gambling-v1-{prompt_digest}"

    return _ContentCompileResult(
        prompt_profile_snapshot=prompt_profile_snapshot,
        general_exemptions=general_exemptions,
        decision_rules=[_decision_rule(rule) for rule in rules],
        stage_routes=stage_routes,
    )


def _revision_content(revision: dict) -> dict:
    snapshot = revision.get("snapshot")
    if isinstance(snapshot, dict):
        return snapshot
    return {
        key: revision.get(key)
        for key in ("schema_version", "name", "domain", "audit_goal", "general_exemptions", "categories")
    }


def _normalize_policy(policy: dict) -> dict:
    thresholds = {"high": 80, "medium": 60, "review": 40}
    incoming_thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
    for key, fallback in thresholds.items():
        try:
            thresholds[key] = max(0, min(100, int(incoming_thresholds.get(key, fallback))))
        except (TypeError, ValueError):
            thresholds[key] = fallback
    capabilities = []
    incoming_capabilities = policy.get("capabilities") if isinstance(policy.get("capabilities"), list) else []
    for capability in _CAPABILITY_ORDER:
        if capability in incoming_capabilities and capability not in capabilities:
            capabilities.append(capability)
    if not capabilities:
        capabilities = list(_CAPABILITY_ORDER)
    return {
        "policy_id": str(policy.get("policy_id") or policy.get("id") or ""),
        "policy_name": str(policy.get("policy_name") or policy.get("name") or ""),
        "policy_version": str(policy.get("policy_version") or policy.get("version") or ""),
        "ruleset_revision_id": str(policy.get("ruleset_revision_id") or ""),
        "policy_config": policy.get("policy_config") if isinstance(policy.get("policy_config"), dict) else {},
        "library_ids": [
            str(item)
            for item in (policy.get("library_ids") if isinstance(policy.get("library_ids"), list) else [])
            if str(item).strip()
        ],
        "capabilities": capabilities,
        "scoring_template": str(policy.get("scoring_template") or "balanced"),
        "thresholds": thresholds,
    }


def _enabled_rules(content: RuleSetContent) -> list[dict]:
    rules = []
    for category in sorted(content.categories, key=lambda item: (item.order, item.category_id)):
        for rule in sorted(category.rules, key=lambda item: (item.order, item.rule_id)):
            if not rule.enabled:
                continue
            row = rule.model_dump(mode="json")
            row["category_id"] = category.category_id
            row["category_name"] = category.name
            rules.append(row)
    return rules


def _prompt_rule(rule: dict) -> dict:
    return {
        key: rule[key]
        for key in (
            "rule_id",
            "hit_condition",
            "suggested_risk_level",
            "rule_exemptions",
        )
    }


def _decision_rule(rule: dict) -> dict:
    return {
        key: rule[key]
        for key in (
            "rule_id",
            "name",
            "hit_condition",
            "suggested_risk_level",
            "rule_exemptions",
            "application_stages",
            "adjudication_notes",
            "enabled",
            "order",
            "source_mappings",
            "category_id",
            "category_name",
        )
    }


def _image_prompt(payload: dict) -> str:
    return (
        "提取图片赌博风险证据，分析 OCR、二维码、界面及视觉；不作整帖最终处罚。\n\n"
        + _compiled_business_rules(
            payload,
            reference_instruction=(
                "risk_type 只能是赌博交易、投注平台导流、上分提现、代理推广、盘口赔率、资金结算或其子类。"
                "“参考”只用于研判，必须依据当前证据独立输出实际 severity，不得照抄。"
            ),
        )
        + "\n\n逐字提取所有可见文字；看不清写 unreadable，无文字写空字符串。客观描述人物、物品、文字、"
        "二维码、联系方式和场景，不擅自定性。只有明确证据才输出 risk_items；证据不足输出空数组。"
        "每项必须引用图片文字或可见元素并说明原因，不得仅凭关键词、单个物品或无上下文画面判 high。"
        "severity 是该项实际证据风险等级。matched_exemption_ids 仅在真正命中豁免时输出。"
        "硬约束：visual_summary 必须始终存在且必须是 JSON 字符串，不得省略，也不得返回 null、数组或对象。"
        "即使未发现风险，也必须用 visual_summary 客观概括图片主要内容，并将 risk_items 返回空数组。\n\n"
        "只输出合法 JSON，不要输出 Markdown：\n"
        "{\"ocr_text\":\"string\",\"visual_summary\":\"string\",\"benign_context\":\"string\","
        "\"risk_items\":[{\"risk_type\":\"string\",\"evidence\":\"string\",\"reason\":\"string\","
        "\"severity\":\"low|medium|high\",\"rule_id\":\"上述 stable rule_id\","
        "\"matched_exemption_ids\":[\"实际命中的 exemption_id，可选\"]}]}"
    )


def _frame_prompt(payload: dict) -> str:
    return (
        "提取视频分段赌博风险证据。4x4 contact sheet 按真实时间排序；空白格非视频内容。结合标题、正文、"
        "逐帧 OCR 原文/译文及本段 ASR 审核；不复述 OCR/ASR 全文。\n\n"
        + _compiled_business_rules(
            payload,
            reference_instruction=(
                "risk_type 只能是赌博交易、投注平台导流、上分提现、代理推广、盘口赔率、资金结算或其子类。"
                "“参考”仅供研判；实际 risk_level 由当前证据独立判断，不得代填；score 按 "
                "none=0、low=40、medium=60、high=80 输出。"
            ),
        )
        + "\n\nframe_id、ocr_chunk_id、asr_chunk_id 仅可取自输入。无明确风险时对应数组为空。"
        "每个非零风险项必须给出 stable rule_id、与 score 一致的 low|medium|high risk_level 和短原因；"
        "matched_exemption_ids 仅在真正命中豁免时输出。segment_score 和各项 score 均为0-100，"
        "reason 最多35字，segment_summary 客观且最多45字。\n\n只输出合法 JSON，不要输出 Markdown：\n"
        "{\"segment_summary\":\"string\",\"segment_score\":0,\"risk_library_id\":\"string\","
        "\"risk_library_label\":\"string\",\"visual_risks\":[{\"frame_ids\":[\"f0001\"],\"score\":0,"
        "\"risk_type\":\"string\",\"reason\":\"string\",\"rule_id\":\"stable rule_id\","
        "\"risk_level\":\"low|medium|high\",\"matched_exemption_ids\":[\"可选\"]}],"
        "\"ocr_risks\":[{\"ocr_chunk_id\":\"string\",\"frame_ids\":[\"f0001\"],\"score\":0,"
        "\"risk_type\":\"string\",\"reason\":\"string\",\"rule_id\":\"stable rule_id\","
        "\"risk_level\":\"low|medium|high\",\"matched_exemption_ids\":[\"可选\"]}],"
        "\"asr_risks\":[{\"asr_chunk_id\":\"string\",\"score\":0,\"risk_type\":\"string\","
        "\"reason\":\"string\",\"rule_id\":\"stable rule_id\",\"risk_level\":\"low|medium|high\","
        "\"matched_exemption_ids\":[\"可选\"]}]}"
    )


def _comment_prompt_template(payload: dict) -> str:
    return (
        "你是评论区逐条赌博风险审核器。结合帖子标题、正文和媒体摘要，独立判断每条评论；不得聚合多条评论，"
        "一条评论可独立触发召回。评论证据只归属于该评论，不得直接归责主帖作者。\n\n"
        + _compiled_business_rules(
            payload,
            reference_instruction=(
                "t 只能是赌博交易、投注平台导流、上分提现、代理推广、盘口赔率、资金结算或其子类。"
                "“参考”只用于研判；实际 risk_level 仅依据当前评论证据，不得代填；s 按 "
                "none=0、low=40、medium=60、high=80 输出。"
            ),
        )
        + "\n\n只依据当前评论原文或可靠译文判断，不补写原文没有的交易、因果或参与意图。疑问、转述、批判、"
        "否定、举报和正常讨论不自动违规，也不自动豁免；语义或翻译不确定时最多 low。q 必须逐字摘自 "
        "source_text 且保持最短，t/rb 必须受 q 和可靠译文支持。每条 source_text 保持现有最多300字，"
        "已有 translation_zh 保持现有最多300字；不得改写评论正文。每个 comment_id 必须且只能输出一次。\n\n"
        "只输出合法 JSON 稀疏合同：\n{\"comments\":["
        "{\"id\":\"原comment_id\",\"s\":0,\"risk_level\":\"none\",\"zh\":\"仅需要翻译时\"},"
        "{\"id\":\"原comment_id\",\"s\":80,\"lib\":\"主风险库id\",\"sec\":[\"次风险库id，可选\"],"
        "\"t\":\"短类别\",\"rb\":\"违规依据\",\"eb\":\"真实豁免依据，可选\","
        "\"q\":\"source_text中的最短原句\",\"risk_level\":\"low|medium|high\","
        "\"rule_id\":\"stable rule_id\",\"matched_exemption_ids\":[\"实际命中的 exemption_id，可选\"],"
        "\"zh\":\"仅需要翻译时\"}]}\n必须保留 V1 稀疏语义：s=0 时只输出 id、s、risk_level，"
        "需翻译时加 zh；s>0 必须输出 id/s/lib/t/rb/q/risk_level/rule_id，sec、eb、matched_exemption_ids "
        "仅在实际存在时输出。s 为0-100；rb 最多18字，eb 最多14字。需要翻译时 zh 必须准确、自然、完整；"
        "无需翻译时省略。不要输出空字符串、空数组或占位依据。"
    )


def _fusion_prompt_template(payload: dict) -> str:
    return (
        "融合全帖赌博风险。仅校准 evidence_catalog 现有证据的跨分段、跨模态含义；不重审媒体，不创造、"
        "复制或改写证据。\n\n"
        + _compiled_business_rules(
            payload,
            reference_instruction=(
                "categories 仅使用赌博交易、投注平台导流、上分提现、代理推广、盘口赔率、资金结算或其子类。"
                "“参考”仅供研判；实际 evidence_risk_level 和建议等级按已有证据独立判断，不得代填。"
            ),
        )
        + "\n\n优先识别引用、反讽、批判、否定、新闻、科普、举报、反赌、被骗曝光、风险提示。评论独立；"
        "其证据主导时仅建议 review，summary 注明来自评论区，不直接归责主帖作者。title_zh/desc_zh "
        "优先用于理解外文。content_title 须为8-18字中文主题短标题，不写内容ID、审核结论或风险等级。\n\n"
        "只输出合法 JSON：\n"
        "{\"schema_version\":\"audit_fusion_v4\",\"content_title\":\"\",\"summary\":\"\","
        "\"decision_suggestion\":\"pass|review|reject\",\"risk_level_suggestion\":\"none|low|medium|high\","
        "\"primary_risk\":\"\",\"categories\":[],\"evidence_items\":[{\"evidence_id\":\"目录ID\","
        "\"evidence_risk_level\":\"low|medium|high\",\"reason\":\"短原因\"}],"
        "\"rule_matches\":[{\"rule_id\":\"stable rule_id\",\"evidence_ids\":[\"目录ID\"],"
        "\"matched_exemption_ids\":[\"实际命中的 exemption_id，可选\"]}]}\n\n"
        "evidence_items 和 rule_matches 仅选 evidence_catalog 现有 ID。安全或豁免只写 summary，不得输出 "
        "none 证据；pass/none 时两个数组必须为空。summary 最多80字，reason 最多45字。不得输出 "
        "primary_modality、supporting_modalities、source、source_label、text、features 或 confidence。"
    )


def canonical_rule_line(rule: dict) -> str:
    return (
        f"{str(rule.get('rule_id') or '').strip()}｜参考 "
        f"{str(rule.get('suggested_risk_level') or '').strip()}｜"
        f"{str(rule.get('hit_condition') or '').strip()}"
    )


def _compiled_business_rules(
    payload: dict,
    *,
    reference_instruction: str,
) -> str:
    lines = ["筛选后的业务规则："]
    seen_rule_ids: set[str] = set()
    rule_exemptions: list[str] = []
    seen_exemption_ids: set[str] = set()
    for rule in payload.get("rules") or []:
        rule_id = str(rule.get("rule_id") or "").strip()
        if not rule_id or rule_id in seen_rule_ids:
            continue
        seen_rule_ids.add(rule_id)
        lines.append(canonical_rule_line(rule))
        for exemption in rule.get("rule_exemptions") or []:
            exemption_id = str(exemption.get("exemption_id") or "").strip()
            condition = str(exemption.get("condition") or "").strip()
            if not exemption_id or not condition or exemption_id in seen_exemption_ids:
                continue
            seen_exemption_ids.add(exemption_id)
            rule_exemptions.append(f"{exemption_id}｜{condition}")
    lines.append(reference_instruction)

    general_exemptions = []
    seen_general_ids: set[str] = set()
    for exemption in payload.get("general_exemptions") or []:
        exemption_id = str(exemption.get("exemption_id") or "").strip()
        condition = str(exemption.get("condition") or "").strip()
        if not exemption_id or not condition or exemption_id in seen_general_ids:
            continue
        seen_general_ids.add(exemption_id)
        general_exemptions.append(f"{exemption_id}｜{condition}")
    if general_exemptions:
        lines.extend(("", "通用豁免只判断一次：" + "；".join(general_exemptions)))
    if rule_exemptions:
        lines.append("规则级豁免仅在实际满足时使用：" + "；".join(rule_exemptions))
    return "\n".join(lines)


def _validate_fixed_prompt_budgets(profile: dict) -> None:
    keys = {
        "image_evidence": "image_prompt",
        "video_frame_evidence": "frame_prompt",
        "comment_audit": "comment_prompt_template",
        "fusion_audit": "fusion_prompt_template",
    }
    for stage, key in keys.items():
        limit = FIXED_PROMPT_LIMITS[stage]
        size = len(str(profile.get(key) or ""))
        if size > limit:
            raise RuleSetValidationError(
                f"compiled fixed prompt for {stage} exceeds {limit} characters: {size}"
            )
