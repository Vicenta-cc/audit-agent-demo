from __future__ import annotations

import json
from typing import Any


def outline_messages(
    *,
    task: dict[str, Any],
    statistics: dict[str, Any],
    finding_cards: list[dict[str, Any]],
) -> list[dict[str, str]]:
    payload = {
        "task": task,
        "statistics": _metric_prompt_view(statistics),
        "representative_findings": finding_cards,
    }
    return [
        {
            "role": "system",
            "content": (
                "你是面向业务人员的内容调查报告规划员。输入中的 metric_key、Finding ID 仅用于结构化"
                "引用，不得出现在章节标题、purpose 或任何给读者看的文字中。不得重新审核帖子，不得"
                "发明 Finding、Evidence 或数字。"
            ),
        },
        {
            "role": "user",
            "content": (
                "为一份正常给人阅读的调查报告生成结构化大纲，设置 4 至 6 个动态章节，不要照抄工程"
                "验收清单。要求：\n"
                "1. section_id 使用稳定 ASCII slug；section_kind 只能使用 overview、risk_analysis、"
                "case_analysis、synthesis、conclusion。\n"
                "1a. 每个章节必须显式填写 is_report_category；只有真正的报告类别填写 true，普通分析、重点案例、"
                "风险材料归纳等章节填写 false。不要把 case block 或普通章节自动当作报告类别。\n"
                "2. 必须有 overview、case_analysis、synthesis 和 conclusion；conclusion 只能有一个且"
                "必须是最后一章。\n"
                "3. 不要单设“典型证据说明”“来源与数据质量”“数据质量/覆盖说明”等工程章节。证据融入"
                "案例分析，数据质量由程序在报告尾部生成一句简短说明。\n"
                "4. 章节优先围绕调查对象、风险现象、行为模式、典型案例和综合研判组织，始终使用调查报告"
                "视角。除非任务本身明确是审核效果评估，否则不要规划“审核难点”“模型能力”“系统识别"
                "难点”“算法效果”“审核流程”等系统自我分析章节。若材料体现主体内容与评论区风险错位，"
                "应将其规划为调查对象的风险特征，而不是审核系统为什么难以判断。\n"
                "5. 标题围绕该任务的真实风险特征组织，使用普通业务语言，不出现 Finding、Evidence、"
                "metric_ref、快照、哈希或任何内部 ID。\n"
                "6. finding_ids 和 metric_refs 只填写在结构字段中，且只能引用输入已有 ID；至少三个章节"
                "引用 Finding，结论章节必须引用代表性 Finding。不要输出正文。\n输入：\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]


def section_messages(
    *,
    task: dict[str, Any],
    section: dict[str, Any],
    metrics: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    prior_report_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    payload = {
        "task": task,
        "section": section,
        "allowed_metrics": _metric_records_for_prompt(metrics),
        "allowed_findings_and_evidence": findings,
        "prior_report_context": prior_report_context or {},
    }
    return [
        {
            "role": "system",
            "content": (
                "你是面向调查人员和业务决策者的正式调查报告撰写员。事实只能来自输入，所有领域事实"
                "必须输出结构化 Claim。不得计算或推测统计数字，不得创建新的 ID。结构字段供程序校验，"
                "正文必须像自然、克制、易读的调查报告。"
            ),
        },
        {
            "role": "user",
            "content": (
                "撰写当前章节。不要写审核结果流水账或系统说明。规则：\n"
                "1. 任何包含数字、数量、比例或排序的 Claim 必须使用 claim_type=numeric，并绑定输入中的 metric_refs；"
                "正文只使用 metric 的 display_value/denominator_display。百分比最多保留一位小数，整数不得写成 24.0。\n"
                "2. domain_fact 和 synthesis Claim 必须引用 allowed finding_ids；除纯统计或方法说明外，还必须引用至少一个"
                "与该 Finding 关联的 evidence_id。\n"
                "3. methodology Claim 可不引用来源，但不得陈述任务事实。没有 Finding 输入时，非数字 Claim 只能写"
                "流程、范围或口径说明，不得补造来源 ID。\n"
                "4. Claim text 应是可以独立核验的后台陈述。阅读正文放在 paragraphs 中，每个 paragraph 必须通过"
                " claim_ids 关联本节至少一个 Claim；每个 Claim 也必须被至少一个 paragraph 或 case block 引用。"
                "paragraph、Claim text、case block 的 title/text 均严禁出现"
                " finding:audit_result、evidence:audit_result、metric:、metric_ref、Claim ID、来源快照、来源哈希，"
                "也不要使用 Finding/Evidence 这类系统术语。用“一则视频”“相关评论”“画面文字”“语音内容”等自然描述。\n"
                "   paragraph 可以自然综合或改写其关联 Claim，不需要机械复制 Claim text；有 Finding 输入的章节至少输出一个领域事实 Claim。\n"
                "5. 只能使用输入列出的 finding_id、evidence_id、metric_key，且这些 ID 只能放在结构字段中。\n"
                "6. section_kind=case_analysis 时，为主要案例输出 case_blocks；每个 case block 用自然标题和简洁分析，"
                "claim_ids 只引用本节 Claim 的本地 claim_id。其他章节 case_blocks 置空。\n"
                "7. section_kind=conclusion 时，必须综合 prior_report_context 中的 overall_findings_summary 和"
                " validated_prior_claims，形成明确调查结论与后续关注方向。不得照抄概览，不得声称没有提供发现、"
                "Finding 或证据。\n"
                "8. 只描述来源能够支持的客观现象。除非来源明确说明，不得推测发布者的主观意图、故意规避审核或"
                "借合规身份实施引流；可改写为“评论区客观上形成了风险信息聚集”。正文使用完整、自然的中文，"
                "来源原话中的简短黑话除外，不混入英文叙述。\n"
                "9. 不要在正文解释校验、快照、数据契约或报告生成过程。\n"
                "10. evidence_type 指标必须严格按照 semantic_definition 解读。finding_count 和以 finding_count"
                "为分母的 percentage 只表示“至少包含该类证据的去重内容数/覆盖率”，不得改写成该类证据在"
                "全部证据中的占比，更不得写成判定依据占比、决策贡献度、重要权重或主要决定因素。"
                "evidence_count 只表示该类型的规范化证据单元数量。\n输入：\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]


def _metric_prompt_view(statistics: dict[str, Any]) -> list[dict[str, Any]]:
    return _metric_records_for_prompt(statistics.get("metrics") or [])


def _metric_records_for_prompt(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "metric_key": item["metric_key"],
            "metric_name": item["metric_name"],
            "label": _human_metric_label(item),
            "display_value": _display_metric_value(item),
            "denominator_display": str(item["denominator"]),
            "denominator_name": item["denominator_name"],
            "denominator_label": _human_denominator_label(item),
            "group": item.get("group") or {},
            "percentage_basis": item.get("percentage_basis") or "",
            "semantic_definition": metric_semantic_definition(item),
            "forbidden_interpretations": _metric_forbidden_interpretations(item),
        }
        for item in metrics
    ]


def _display_metric_value(metric: dict[str, Any]) -> str:
    value = float(metric["value"])
    if metric.get("metric_name") == "percentage":
        rendered = f"{value:.1f}"
        return f"{rendered.removesuffix('.0')}%"
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _human_metric_label(metric: dict[str, Any]) -> str:
    label = str(metric["label"])
    group = metric.get("group") or {}
    metric_name = str(metric.get("metric_name") or "")
    if len(group) == 1:
        group_key, group_value = next(iter(group.items()))
        value_labels = {
            "pass": "直接通过",
            "review": "进入复审",
            "reject": "拒绝",
            "none": "无明显风险",
            "low": "低风险",
            "medium": "中风险",
            "high": "高风险",
            "text": "正文",
            "comment": "评论",
            "ocr": "画面文字",
            "asr": "语音",
            "keyframe": "关键帧",
            "visual": "视觉",
            "profile": "账号资料",
            "rule_reference": "规则引用",
            "other": "其他",
        }
        group_label = value_labels.get(str(group_value), str(group_value))
        suffixes = {
            "count": "内容数",
            "percentage": "占比",
            "finding_count": "覆盖内容数",
            "evidence_count": "证据条数",
        }
        suffix = suffixes.get(metric_name, metric_name)
        if group_key == "author":
            return f"作者 {group_label} 的{suffix}"
        if group_key == "evidence_type":
            if metric_name == "percentage":
                return f"包含{group_label}证据的内容覆盖率"
            if metric_name == "finding_count":
                return f"包含{group_label}证据的内容数"
            if metric_name == "evidence_count":
                return f"{group_label}证据条数"
            return f"{group_label}证据{suffix}"
        return f"{group_label}{suffix}"
    return (
        label.replace("规范化 Evidence", "证据")
        .replace("Evidence", "证据")
        .replace("Finding", "内容")
    )


def _human_denominator_label(metric: dict[str, Any]) -> str:
    labels = {
        "current_filtered_finding_count": "当前过滤范围内的内容总数",
        "current_filtered_evidence_count": "当前过滤范围内的规范化证据总数",
        "content_count": "任务内容总数",
        "audit_result_count": "审核结果总数",
    }
    name = str(metric.get("denominator_name") or "")
    return labels.get(name, name)


def metric_semantic_definition(metric: dict[str, Any]) -> str:
    group = metric.get("group") or {}
    metric_name = str(metric.get("metric_name") or "")
    evidence_type = group.get("evidence_type")
    if evidence_type:
        evidence_label = {
            "text": "正文",
            "comment": "评论",
            "ocr": "画面文字",
            "asr": "语音",
            "keyframe": "关键帧",
            "visual": "视觉",
            "profile": "账号资料",
            "rule_reference": "规则引用",
            "other": "其他",
        }.get(str(evidence_type), str(evidence_type))
        if metric_name == "finding_count":
            return (
                f"当前过滤范围内，至少包含一条{evidence_label}证据的去重内容数量；"
                "同一内容即使含多条同类型证据也只计一次。"
            )
        if metric_name == "evidence_count":
            return f"当前过滤范围内，{evidence_label}类型的规范化证据单元数量。"
        if metric_name == "percentage":
            return (
                f"至少包含一条{evidence_label}证据的去重内容数，除以当前过滤范围内的"
                "内容总数；这是内容覆盖率。"
            )
    if metric_name == "percentage":
        return "该分组的去重内容数，除以当前过滤范围内的内容总数。"
    if metric_name == "count":
        return "当前过滤范围内属于该分组的去重内容数量。"
    return "由普通程序从冻结来源中确定性计算的统计值。"


def _metric_forbidden_interpretations(metric: dict[str, Any]) -> list[str]:
    if (metric.get("group") or {}).get("evidence_type"):
        return [
            "该类证据占全部证据的比例",
            "判定依据来自该类证据的比例",
            "该类证据的决策贡献度或权重",
            "该类证据是主要决定因素",
        ]
    return []
