from __future__ import annotations

import json
from typing import Any


R2_PROMPT_VERSION = "report-r2-risk-findings-v2-r1"

SERVER_BOUNDARY_NOTES = (
    "评论Evidence只能证明评论者发表了相应内容，不能证明帖子作者持有相同立场。",
    "报告结论仅适用于当前冻结调查资料。",
    "indirect和counter-evidence未作为本报告风险依据。",
    "未发现相关证据不等于证明现实中不存在相关行为。",
)


def investigation_finding_messages(
    *, task_name: str, statistics: dict[str, Any], risk_inputs: list[dict[str, Any]]
) -> list[dict[str, str]]:
    payload = {
        "task_name": task_name,
        "statistics": statistics,
        "risk_posts": risk_inputs,
    }
    return [
        {
            "role": "system",
            "content": (
                "你负责把当前调查任务中已经存在的审核结论归纳为 InvestigationFinding。"
                "AuditFinding 是既有审核记录，只能读取，不能改判、打分、升级或合并成新的单帖结论。"
                "只能使用输入中的帖子配文、已有语音转写、AuditFinding 和 direct Evidence。"
                "不得把 indirect 或 counter-evidence 当作风险依据。不要输出 canonical ID、数据库 ID、"
                "Snapshot、Handle 或任何系统术语。输入中的 P/F/E/M 是本轮结构字段别名，"
                "只能出现在对应引用字段中，不得出现在 title、statement、boundary_notes 或 disposition_note。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请把主题相近的帖子归纳为少量高层 InvestigationFinding，避免按单帖、措辞差异或风险等级机械拆分。"
                "无法合理形成共性发现的帖子放入 standalone_risk_posts，并给出已研判但未归并的边界说明。"
                "post_memberships 是唯一的成员事实源；is_representative=true 的成员用于报告正文展示。"
                "每个 membership 必须同时引用正确的 P、F，并且 membership_evidence_refs 只能选择该 F/P 下的 direct Evidence；"
                "没有可用 direct Evidence 时允许为空。不要输出 risk_post_coverage_complete，它由服务器计算。"
                "同一帖子可以属于多个 Finding，但 standalone 不能与任何 membership 重叠。"
                "metric_refs 只能引用输入中存在的确定性指标。"
                "如果材料不足以支持更强的因果、身份或现实关系判断，请在 boundary_notes 中明确边界，"
                "不要使用持续、规模化、严重威胁、协同传播或升级趋势等未被资料支持的表达。只返回符合 schema 的 JSON 对象。\n输入：\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]


def finding_section_messages(
    *,
    task_name: str,
    section: dict[str, Any],
    investigation_finding: dict[str, Any],
    posts: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
) -> list[dict[str, str]]:
    payload = {
        "task_name": task_name,
        "section": section,
        "investigation_finding": investigation_finding,
        "posts": posts,
        "metrics": metrics,
    }
    return [
        {
            "role": "system",
            "content": (
                "你撰写调查报告中的一个主要调查发现章节。事实只能来自输入。"
                "AuditFinding 是已有审核结论，不得重新判定；direct Evidence 只能支撑其所属帖子和 Finding。"
                "当前 InvestigationFinding 的 statement 和 boundary_notes 只是本节归纳草案，不是已验证的事实来源，"
                "不能替代 AuditFinding、Evidence 或 Metric。"
                "不要把评论区风险依据扩写成主帖安全、合规或没有风险。不要发明评分、证据叠加或升级规则。"
                "P、F、E、IF、M 别名只能放在结构字段，不能出现在任何自然语言。无法确认的人物关系、动机、"
                "事件顺序或因果必须省略或使用可能、从当前材料看等有边界表达。"
                "除非AuditFinding、direct Evidence或Metric明确支持，不得使用‘大量、普遍、持续、升级、系统性、规模化’，"
                "不得写作者引导、作者认同、账号整体立场、主帖正常、合规或没有风险，"
                "不得把低俗性暗示或性骚扰扩大成性暴力威胁，不得把当前资料未显示改写为不存在。"
            ),
        },
        {
            "role": "user",
            "content": (
                "输出一个完整章节 JSON。每个 paragraph 必须引用本节 Claim，每个 Claim 必须被引用。"
                "domain_fact 或 synthesis Claim 必须引用本节 AuditFinding，并且只能使用当前 membership 允许的 Evidence；"
                "数字只能引用 metric_refs。正文使用自然中文，不出现内部 ID。\n输入：\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]


def synthesis_conclusion_messages(
    *,
    task_name: str,
    verified_section_claims: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    boundary_notes: list[str],
) -> list[dict[str, str]]:
    """Prompt the one bounded model step for synthesis and conclusion."""

    payload = {
        "task_name": task_name,
        "verified_section_claims": verified_section_claims,
        "metrics": metrics,
        "boundary_notes": boundary_notes,
    }
    return [
        {
            "role": "system",
            "content": (
                "你负责生成报告的‘综合研判’和‘调查结论与建议’两个章节。"
                "输入中的章节Claims已经通过服务器验证，只能作为事实整理上下文；"
                "InvestigationFinding的statement、Qwen生成的boundary_notes和其他Claim文本本身不是新的来源。"
                "只能引用输入中已经被已验证章节实际使用的AuditFinding、direct Evidence或确定性Metric；"
                "不得引用另一个Claim、方法说明、indirect/counter-evidence或未被章节使用的资料。"
                "不得把评论区风险依据扩写为主帖安全、合规、没有风险，不得发明评分、证据叠加、升级、身份、"
                "现实关系或因果规则。除非正式来源明确支持，不得使用‘大量、普遍、持续、升级、系统性、规模化’，"
                "不得写作者引导、作者认同、账号整体立场，不得把低俗性暗示或性骚扰扩大成性暴力威胁，"
                "不得把当前资料未显示改写为不存在。"
                "以下边界说明由服务器确定性提供，只能作为表达边界，不能作为来源："
                + "；".join(boundary_notes)
                + "只能使用输入中的局部F/E/M别名，且别名只放在对应引用字段。自然语言不得出现任何别名、"
                "canonical ID、数据库ID、Snapshot、Handle或JSON路径。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请只返回一个JSON对象，字段严格为synthesis_paragraphs和conclusion_paragraphs。"
                "synthesis_paragraphs必须有1至3个对象，conclusion_paragraphs必须有1至2个对象。"
                "每个对象严格只有text、audit_finding_refs、evidence_refs、metric_refs；不要输出claim_id、"
                "claim_type、support_type、claims、paragraphs或section_id。每段至少引用一个允许的F/E/M来源；"
                "引用Evidence时同时引用其所属AuditFinding。数字只能使用并引用对应Metric；不要补充输入没有的事实。\n输入：\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]
