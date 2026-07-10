from __future__ import annotations

import hashlib
import json
from copy import deepcopy


DEFAULT_CAPABILITIES = ["text", "ocr", "asr", "vision", "comment"]
DEFAULT_THRESHOLDS = {"high": 80, "medium": 60, "review": 40}

IMPORTANCE_SCORES = {
    "off": 0,
    "low": 10,
    "medium": 15,
    "high": 20,
    "very_high": 30,
}

CAPABILITY_LABELS = {
    "text": "文本语义",
    "ocr": "OCR 命中",
    "asr": "ASR 命中",
    "vision": "视觉特征",
    "comment": "评论聚集",
    "keyword": "关键词/黑话命中",
}

TEMPLATE_IMPORTANCE = {
    "balanced": {
        "keyword": "high",
        "text": "high",
        "ocr": "high",
        "asr": "medium",
        "vision": "high",
        "comment": "low",
    },
    "strict": {
        "keyword": "very_high",
        "text": "high",
        "ocr": "high",
        "asr": "high",
        "vision": "very_high",
        "comment": "medium",
    },
    "ocr_first": {
        "keyword": "high",
        "text": "medium",
        "ocr": "very_high",
        "asr": "medium",
        "vision": "medium",
        "comment": "low",
    },
    "vision_first": {
        "keyword": "high",
        "text": "medium",
        "ocr": "medium",
        "asr": "medium",
        "vision": "very_high",
        "comment": "low",
    },
}


def normalize_library_ids(library_ids: list[str] | None, fallback: str = "soft") -> list[str]:
    values = _dedupe(library_ids or [])
    if values:
        return values
    return [fallback.strip() or "soft"]


def normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    values = [value for value in _dedupe(capabilities or []) if value in DEFAULT_CAPABILITIES]
    return values or list(DEFAULT_CAPABILITIES)


def compile_rule_profile(
    *,
    libraries: list[dict],
    capabilities: list[str] | None = None,
    scoring_template: str = "balanced",
    rule_snapshot: dict | None = None,
) -> dict:
    normalized_capabilities = normalize_capabilities(capabilities)
    normalized_template = scoring_template if scoring_template in TEMPLATE_IMPORTANCE else "balanced"
    normalized_snapshot = normalize_rule_snapshot(
        rule_snapshot or {},
        libraries=libraries,
        capabilities=normalized_capabilities,
        scoring_template=normalized_template,
    )
    compiler_payload = {
        "schema_version": "1.0",
        "libraries": [_compact_library(library) for library in libraries],
        "capabilities": normalized_capabilities,
        "scoring_template": normalized_template,
        "rule_snapshot": normalized_snapshot,
    }
    digest = hashlib.sha256(
        json.dumps(compiler_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    audit_goal = "、".join(str(library.get("title") or library.get("id")) for library in libraries)
    knowledge_text = _format_libraries(libraries, normalized_capabilities)
    scoring_text = _format_scoring_rules(normalized_snapshot)
    thresholds_text = _format_thresholds(normalized_snapshot["thresholds"])
    output_labels = _format_output_labels(libraries)

    return {
        "category_id": "composite",
        "audit_goal": audit_goal,
        "evidence_rules": knowledge_text,
        "fusion_rules": scoring_text,
        "image_prompt": _image_prompt(audit_goal, knowledge_text, output_labels),
        "frame_prompt": _frame_prompt(audit_goal, knowledge_text, output_labels),
        "fusion_prompt_template": _fusion_prompt(
            audit_goal,
            knowledge_text,
            scoring_text,
            thresholds_text,
            output_labels,
        ),
        "version": 1,
        "prompt_version": f"composite-v1-{digest}",
        "rule_snapshot": normalized_snapshot,
        "capabilities": normalized_capabilities,
        "library_ids": [str(library.get("id") or "") for library in libraries if library.get("id")],
    }


def normalize_rule_snapshot(
    snapshot: dict,
    *,
    libraries: list[dict],
    capabilities: list[str],
    scoring_template: str,
) -> dict:
    thresholds = dict(DEFAULT_THRESHOLDS)
    incoming_thresholds = snapshot.get("thresholds") if isinstance(snapshot, dict) else {}
    if isinstance(incoming_thresholds, dict):
        for key in ("high", "medium", "review"):
            try:
                value = int(incoming_thresholds.get(key, thresholds[key]))
            except (TypeError, ValueError):
                value = thresholds[key]
            thresholds[key] = max(0, min(100, value))

    incoming_rules = snapshot.get("scoring_rules") if isinstance(snapshot, dict) else None
    if isinstance(incoming_rules, list) and incoming_rules:
        scoring_rules = [
            _normalize_scoring_rule(rule, libraries, scoring_template)
            for rule in incoming_rules
            if isinstance(rule, dict)
        ]
        scoring_rules = [rule for rule in scoring_rules if rule["score"] > 0 and rule.get("source") != "keyword"]
    else:
        scoring_rules = _default_scoring_rules(libraries, capabilities, scoring_template)

    return {
        "schema_version": "1.0",
        "thresholds": thresholds,
        "scoring_rules": scoring_rules,
    }


def _default_scoring_rules(libraries: list[dict], capabilities: list[str], template: str) -> list[dict]:
    rules: list[dict] = []
    for library in libraries:
        library_id = str(library.get("id") or "").strip()
        title = str(library.get("title") or library_id).replace("词库", "").replace("知识包", "").strip()
        if not library_id:
            continue
        sources = [capability for capability in capabilities if capability != "text"]
        if "text" in capabilities:
            sources.insert(0, "text")
        for source in sources:
            importance = TEMPLATE_IMPORTANCE[template].get(source, "medium")
            score = IMPORTANCE_SCORES[importance]
            if score <= 0:
                continue
            rules.append({
                "id": f"{source}_{library_id}",
                "label": f"{title}{CAPABILITY_LABELS.get(source, source)}",
                "source": source,
                "library_id": library_id,
                "category": title,
                "importance": importance,
                "score": score,
            })
    return rules


def _normalize_scoring_rule(rule: dict, libraries: list[dict], template: str) -> dict:
    library_lookup = {str(item.get("id")): item for item in libraries}
    source = str(rule.get("source") or "keyword").strip() or "keyword"
    library_id = str(rule.get("library_id") or rule.get("category_id") or "").strip()
    if not library_id and len(libraries) == 1:
        library_id = str(libraries[0].get("id") or "")
    library = library_lookup.get(library_id) or {}
    title = str(rule.get("category") or library.get("title") or library_id).replace("词库", "").replace("知识包", "").strip()
    importance = str(rule.get("importance") or TEMPLATE_IMPORTANCE[template].get(source) or "medium")
    if importance not in IMPORTANCE_SCORES:
        importance = "medium"
    try:
        score = int(rule.get("score") if rule.get("score") is not None else IMPORTANCE_SCORES[importance])
    except (TypeError, ValueError):
        score = IMPORTANCE_SCORES[importance]
    score = max(0, min(100, score))
    rule_id = str(rule.get("id") or rule.get("rule_id") or f"{source}_{library_id}").strip()
    return {
        "id": rule_id,
        "label": str(rule.get("label") or rule.get("rule") or f"{title}{CAPABILITY_LABELS.get(source, source)}").strip(),
        "source": source,
        "library_id": library_id,
        "category": title,
        "importance": importance,
        "score": score,
    }


def _format_libraries(libraries: list[dict], capabilities: list[str]) -> str:
    sections = []
    enabled_labels = "、".join(CAPABILITY_LABELS.get(item, item) for item in capabilities)
    for index, library in enumerate(libraries, start=1):
        keywords = library.get("keywords") or {}
        exact = "、".join((keywords.get("exact") or [])[:30]) or "无"
        fuzzy = "、".join((keywords.get("fuzzy") or [])[:30]) or "无"
        negative = "、".join((keywords.get("negative_context") or [])[:20]) or "无"
        guidance = library.get("modality_guidance") or {}
        source_refs = _source_refs_to_text(library.get("source_refs"))
        sections.append(f"""
{index}. {library.get("title")}（id={library.get("id")}，version={library.get("version")}）
- 规则依据：{source_refs}
- 审核目标：{library.get("audit_goal") or library.get("title")}
- 输出标签：{"、".join(library.get("output_labels") or [library.get("title", "")])}
- 风险定义：{json.dumps(library.get("risk_definition") or {}, ensure_ascii=False)}
- 风险模式：{json.dumps(library.get("risk_patterns") or {}, ensure_ascii=False)}
- 模态指引：
  - 文本：{guidance.get("text", "")}
  - OCR：{guidance.get("ocr", "")}
  - ASR：{guidance.get("asr", "")}
  - 视觉：{guidance.get("vision", "")}
  - 评论：{guidance.get("comment", "")}
- 关键词：
  - 精确词：{exact}
  - 变体/模糊词：{fuzzy}
  - 负向语境词：{negative}
- 证据规则：{_rules_to_text(library.get("evidence_rules"))}
- 融合/豁免规则：{_rules_to_text(library.get("fusion_rules"))}
- 豁免规则：{_rules_to_text(library.get("exemption_rules"))}
""".strip())
    return f"启用检测能力：{enabled_labels}\n\n" + "\n\n".join(sections)


def _format_scoring_rules(rule_snapshot: dict) -> str:
    lines = []
    for rule in rule_snapshot.get("scoring_rules") or []:
        lines.append(
            f"- rule_id={rule.get('id')}，{rule.get('label')}：category={rule.get('category')}，"
            f"library_id={rule.get('library_id')}，source={rule.get('source')}，"
            f"importance={rule.get('importance')}，score={rule.get('score')}"
        )
    return "\n".join(lines) or "- 无启用评分规则"


def _format_thresholds(thresholds: dict) -> str:
    return (
        f"高危 high >= {thresholds.get('high', 80)}；"
        f"中危 medium >= {thresholds.get('medium', 60)}；"
        f"待复核 low/review >= {thresholds.get('review', 40)}；"
        "低于待复核阈值为 none/pass。"
    )


def _format_output_labels(libraries: list[dict]) -> str:
    labels = []
    for library in libraries:
        for label in library.get("output_labels") or [library.get("title", "")]:
            label = str(label or "").strip()
            if label and label not in labels:
                labels.append(label)
    return "、".join(labels) or "风险类别"


def _image_prompt(audit_goal: str, knowledge_text: str, output_labels: str) -> str:
    return f"""
你是内容审核系统中的图片证据提取器。请分析这张图片，审核方向是“{audit_goal}”。

请只输出 JSON，不要输出 Markdown。字段如下：
{{
  "ocr_text": "逐字提取图片里所有可见文字；看不清写 unreadable；没有文字写空字符串",
  "visual_summary": "客观描述图片内容、人物、物品、文字、二维码、联系方式、场景等，不要擅自定性",
  "benign_context": "如果图片属于豁免、新闻科普、正常生活、影视游戏或合法交易语境，请说明；如果不适用写空字符串",
  "risk_items": [
    {{
      "risk_type": "只能使用这些类别或其子类：{output_labels}",
      "evidence": "必须引用图片文字或可见画面元素",
      "reason": "为什么可能违规",
      "severity": "low|medium|high"
    }}
  ]
}}

风险知识包：
{knowledge_text}

只有出现明确证据时才列入 risk_items。若证据不足，risk_items 输出空数组 []。
不要仅凭关键词、单个物品或无上下文画面直接判 high；需要结合知识包里的证据组合和豁免规则。
"""


def _frame_prompt(audit_goal: str, knowledge_text: str, output_labels: str) -> str:
    return f"""
你是视频审核系统中的关键帧证据提取器。请分析这个视频关键帧，审核方向是“{audit_goal}”。

注意：调用方会在本 prompt 末尾提供 external_ocr JSON。理解画面文字时优先使用 external_ocr 的 text_zh，其次使用 text 原文；没有外部 OCR 时不要臆测小字。

请只输出 JSON，不要输出 Markdown。字段如下：
{{
  "visual_summary": "客观描述关键帧画面、人物、物品、文字、二维码、联系方式、场景等",
  "benign_context": "如果画面属于豁免、新闻科普、正常生活、影视游戏或合法交易语境，请说明；如果不适用写空字符串",
  "risk_items": [
    {{
      "risk_type": "只能使用这些类别或其子类：{output_labels}",
      "evidence": "必须引用画面文字、external_ocr 文字或可见画面元素",
      "reason": "一句话说明为什么可能违规",
      "severity": "low|medium|high"
    }}
  ]
}}

风险知识包：
{knowledge_text}

只有出现明确证据时才列入 risk_items。若证据不足，risk_items 输出空数组 []。
不要仅凭关键词、单个物品或无上下文画面直接判 high；需要结合知识包里的证据组合和豁免规则。
"""


def _fusion_prompt(
    audit_goal: str,
    knowledge_text: str,
    scoring_text: str,
    thresholds_text: str,
    output_labels: str,
) -> str:
    return f"""
你是一个内容审核 agent。请审核一条社交平台内容是否存在“{audit_goal}”。

重要原则：
1. Prompt 里的知识包决定“检测什么”，系统策略会按最高证据风险生成最终结论；不要扩展到知识包之外的风险。
2. 必须区分“提及/讨论/批判/科普/新闻/影视游戏”与“宣扬/诱导/攻击/交易/组织”。
3. 你只负责输出原始证据 evidence_items，并判断每条证据自身的 evidence_risk_level；不要自己计算分数。
4. 不允许把不同风险类别的弱证据机械相加成高危。
5. 图片、关键帧和 OCR/ASR risk_items 只是候选线索，融合阶段必须重新校准。
6. 每个风险结论必须引用证据来源：标题/正文/评论/图片/视频语音/视频关键帧。
7. 关键词/黑话只写入 evidence_items.features，不作为独立计分规则。
8. OCR 与视觉边界：风险来自画面文字时 primary_modality=ocr；风险来自二维码、界面、物品、动作、场景等非文字画面元素时 primary_modality=vision；两者都有时只保留一个主证据，另一个放 supporting_modalities。
9. evidence_risk_level 表示“这条证据自身的危险程度”，只能是 none、low、medium、high；最终帖子风险由系统取所有证据的最高等级。

风险知识包：
{knowledge_text}

识别能力与旧版评分规则（仅用于理解能力来源，不要求输出 rule_matches）：
{scoring_text}

风险等级阈值：
{thresholds_text}

输出类别只能来自或归并到：{output_labels}

请只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释性文字或多余前后缀。
所有字段都是必填字段；没有内容时输出空数组 [] 或空字符串 ""，不要省略字段。
decision_suggestion 只能是 "pass"、"review"、"reject"；risk_level_suggestion 只能是 "none"、"low"、"medium"、"high"。
格式：
{{
  "schema_version": "audit_fusion_v3",
  "content_title": "内容短标题，8-18字，概括帖子主题，不写审核结论或风险等级",
  "summary": "内容总结，100字以内",
  "decision_suggestion": "pass|review|reject",
  "risk_level_suggestion": "none|low|medium|high",
  "primary_risk": "主风险类别，没有则空字符串",
  "categories": ["风险类别"],
  "evidence_items": [
    {{
      "evidence_id": "ev_text_001",
      "primary_modality": "text|ocr|asr|vision|comment",
      "supporting_modalities": ["vision"],
      "source": "title|desc|comment:<id>|image:<index>|video_audio|video_frame:<time>",
      "source_label": "标题|正文|评论|图片 1|视频关键帧",
      "risk_library_id": "风险库 id",
      "risk_library_label": "风险库名称",
      "text": "原始命中文字、评论原文、OCR文字、ASR转写或可见元素；没有则空字符串",
      "ocr_text": "仅 OCR 证据填写画面文字原文",
      "visual_elements": ["仅视觉证据填写二维码、投注平台界面、转账截图等非文字元素"],
      "features": ["黑话、私域联系暗示等命中特征"],
      "evidence_risk_level": "none|low|medium|high",
      "reason": "命中解释，只解释为什么命中，不要替代原始证据",
      "confidence": "low|medium|high"
    }}
  ]
}}

待审核内容：
标题：
{{title}}

正文：
{{desc}}

评论文本：
{{comments}}

图片分析：
{{image_analyses}}

视频语音转录：
{{video_transcripts}}

视频 OCR 文字轨道（兼容字段；新流程已合并到关键帧分析）：
{{video_ocr_tracks}}

视频关键帧分析：
{{frame_analyses}}
"""


def _rules_to_text(value) -> str:
    if value in (None, "", [], {}):
        return "无"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _source_refs_to_text(value) -> str:
    if not isinstance(value, list) or not value:
        return "内部规则"
    out = []
    for item in value[:3]:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            notes = str(item.get("notes") or "").strip()
            if title and notes:
                out.append(f"{title}（{notes}）")
            elif title:
                out.append(title)
        elif item:
            out.append(str(item))
    return "；".join(out) or "内部规则"


def _compact_library(library: dict) -> dict:
    copy = deepcopy(library)
    copy.pop("preview", None)
    return copy


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out
