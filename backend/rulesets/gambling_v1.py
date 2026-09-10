from __future__ import annotations

from .contracts import RuleSetContent

GAMBLING_RULESET_ID = "ruleset.gambling"
GAMBLING_RULESET_V1_REVISION_ID = "ruleset-revision:gambling:v1"
GAMBLING_RULESET_REVISION_ID = "ruleset-revision:gambling:v2"


def _source(source_file: str, source_locator: str, migrated_semantics: str) -> dict:
    return {
        "source_file": source_file,
        "source_locator": source_locator,
        "migrated_semantics": migrated_semantics,
    }


def _exemption(
    exemption_id: str,
    name: str,
    condition: str,
    *source_mappings: dict,
) -> dict:
    return {
        "exemption_id": exemption_id,
        "name": name,
        "condition": condition,
        "source_mappings": list(source_mappings),
    }


def gambling_ruleset_v1() -> RuleSetContent:
    return RuleSetContent.model_validate(
        {
            "schema_version": 0,
            "name": "赌博博彩风险规则集",
            "domain": "gambling",
            "audit_goal": (
                "识别赌博博彩平台与群入口、投注及资金操作、代理带单和收益承诺、"
                "站外导流、评论区组织参与，以及跨模态证据形成的赌博交易闭环。"
            ),
            "general_exemptions": [
                _exemption(
                    "gambling.exemption.sports_and_games",
                    "普通赛事、游戏与概率讨论",
                    "普通体育赛事、棋牌桌游、游戏抽卡或概率数学讨论，且不存在投注、平台、群入口、资金结算、代理推广或组织参与证据。",
                    _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-2-and-6", "普通赛事、游戏娱乐和概率讨论不得仅凭元素升级。"),
                    _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-risk-definition-excluded", "普通棋牌、体育赛事、概率与游戏讨论的排除边界。"),
                ),
                _exemption(
                    "gambling.exemption.news_and_education",
                    "反赌新闻、普法与被骗曝光",
                    "内容主旨为反赌新闻、普法科普、风险提示、被骗经历曝光或举报，且未向用户提供可执行的赌博入口或资金路径。",
                    _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-2-and-5", "新闻反赌、风险提示和避雷语境的放行或降级。"),
                    _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-exemption-rules", "反赌新闻、被骗曝光和风险提示豁免。"),
                ),
                _exemption(
                    "gambling.exemption.warning_qr",
                    "警示材料中的二维码",
                    "二维码、平台截图或聊天截图仅用于新闻取证、反赌警示、举报或被骗曝光，未被作者作为参与入口传播。",
                    _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-2-3-and-6", "二维码必须结合参与导流和资金语境，警示展示不构成入口。"),
                    _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-modality-guidance-ocr", "二维码和平台截图是候选证据，须结合真实路径及整体语境。"),
                ),
            ],
            "categories": [
                {
                    "category_id": "gambling.access_and_funds",
                    "name": "入口与资金闭环",
                    "description": "识别平台、群入口、二维码以及下注、充值、提现和结算链路。",
                    "order": 10,
                    "rules": [
                        {
                            "rule_id": "gambling.platform_entry_and_funding",
                            "name": "博彩平台或群入口与资金路径",
                            "hit_condition": (
                                "同一内容或可归并的多模态证据中，出现投注平台、开户链接、App、群入口、二维码、代理入口之一，"
                                "并出现下注、充值、上分、提现、结算、赔率、返水或赌资支付之一。"
                            ),
                            "suggested_risk_level": "high",
                            "rule_exemptions": [
                                _exemption(
                                    "gambling.rule.platform.warning_context",
                                    "警示取证语境",
                                    "平台或二维码只作为反赌新闻、普法、被骗曝光或举报证据展示，未提供参与指令。",
                                    _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-2-3-and-6", "新闻、警示或被骗曝光中的平台入口不等于参与导流。"),
                                )
                            ],
                            "application_stages": ["image_evidence", "video_frame_evidence", "comment_audit", "fusion_audit"],
                            "adjudication_notes": "入口证据与资金/投注证据可来自不同模态，但必须指向同一赌博活动；单独二维码或单独平台截图不直接构成高风险。",
                            "enabled": True,
                            "order": 10,
                            "source_mappings": [
                                _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-1-3-and-6", "平台、链接、二维码、联系方式与交易闭环边界。"),
                                _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-risk-patterns-high", "平台或群入口与投注资金路径的 AND 组合。"),
                            ],
                        },
                        {
                            "rule_id": "gambling.betting_and_settlement",
                            "name": "盘口下注与充值提现操作",
                            "hit_condition": (
                                "明确展示、讲解或组织盘口、赔率、下注、上分、充值、提现、返水、结算、刷流水等赌博操作，"
                                "并存在真实参与、支付、招募或执行意图。"
                            ),
                            "suggested_risk_level": "high",
                            "rule_exemptions": [
                                _exemption(
                                    "gambling.rule.operations.discussion",
                                    "赛事或概率讨论",
                                    "仅讨论比赛结果、赛事数据或概率，不存在下注和资金执行意图。",
                                    _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-2-and-6", "赛事或概率讨论缺少投注资金意图时不按赌博操作处理。"),
                                )
                            ],
                            "application_stages": ["image_evidence", "video_frame_evidence", "comment_audit", "fusion_audit"],
                            "adjudication_notes": "盘口、赔率等词只有在真实投注或资金路径中才可达到高风险；普通赛事分析不得按赌博操作处理。",
                            "enabled": True,
                            "order": 20,
                            "source_mappings": [
                                _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-1-3-and-5", "盘口、赔率、上分、提现和资金组织。"),
                                _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-risk-definition", "投注及资金操作的纳入与排除边界。"),
                            ],
                        },
                    ],
                },
                {
                    "category_id": "gambling.promotion_and_diversion",
                    "name": "推广承诺与站外导流",
                    "description": "识别代理、带单、包赢稳赚和私域联系方式。",
                    "order": 20,
                    "rules": [
                        {
                            "rule_id": "gambling.agent_guaranteed_win_promotion",
                            "name": "代理带单与包赢稳赚承诺",
                            "hit_condition": (
                                "以代理、代投、带单、带飞、包赢、稳赚、回血、返水或返佣等方式招募参与赌博，"
                                "并伴随收益承诺、参与安排、会员发展或资金操作。"
                            ),
                            "suggested_risk_level": "high",
                            "rule_exemptions": [],
                            "application_stages": ["image_evidence", "video_frame_evidence", "comment_audit", "fusion_audit"],
                            "adjudication_notes": "诈骗语境中的投资带单需按实际主风险归类；本规则只处理明确指向赌博参与或投注平台的推广。",
                            "enabled": True,
                            "order": 10,
                            "source_mappings": [
                                _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-3-and-4", "代投、包赢、稳赚、返水及带飞话术。"),
                                _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-risk-definition-included", "代理推广、会员发展、返佣和参与话术。"),
                            ],
                        },
                        {
                            "rule_id": "gambling.off_platform_diversion",
                            "name": "赌博语境下私信群聊主页导流",
                            "hit_condition": (
                                "赌博平台、盘口、下注、上分、代理或带单语境明确，并引导用户私信、加群、进入群聊、"
                                "查看主页联系方式、扫码或点击站外链接。"
                            ),
                            "suggested_risk_level": "medium",
                            "rule_exemptions": [],
                            "application_stages": ["image_evidence", "video_frame_evidence", "comment_audit", "fusion_audit"],
                            "adjudication_notes": "联系方式或私信动作不能脱离赌博语境单独命中本规则；若同时形成资金路径，优先按高风险闭环规则处理。",
                            "enabled": True,
                            "order": 20,
                            "source_mappings": [
                                _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-3-and-4", "联系方式、群入口和私信导流。"),
                                _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-modality-guidance", "各模态的平台、群聊、二维码和私聊引导证据。"),
                            ],
                        },
                    ],
                },
                {
                    "category_id": "gambling.comments",
                    "name": "评论参与与归责",
                    "description": "区分评论区组织参与、作者迎合和孤立第三方评论。",
                    "order": 30,
                    "rules": [
                        {
                            "rule_id": "gambling.comment_organized_participation",
                            "name": "评论区组织参与或作者迎合",
                            "hit_condition": (
                                "评论明确求带、上车、询问群号、赔率、返水或提现，或组织他人下注、充值、进群；"
                                "作者回复、置顶、点赞迎合或主帖提供入口时，形成作者相关闭环。"
                            ),
                            "suggested_risk_level": "medium",
                            "rule_exemptions": [],
                            "application_stages": ["comment_audit", "fusion_audit"],
                            "adjudication_notes": "单条第三方评论可触发评论风险召回，但不得直接归责为作者主帖高风险；作者迎合或主帖证据闭环后才可升级。",
                            "enabled": True,
                            "order": 10,
                            "source_mappings": [
                                _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-modality-guidance-comment", "评论求带、上车、群号及作者回复导流。"),
                                _source("backend/audit_agent/pipeline.py", "_render_compact_fusion_prompt#third-party-comment-attribution", "评论是独立证据，第三方评论不得直接归责作者。"),
                            ],
                        }
                    ],
                },
                {
                    "category_id": "gambling.adjudication",
                    "name": "证据强度与融合",
                    "description": "约束弱关键词、多模态闭环和可追踪输出。",
                    "order": 40,
                    "rules": [
                        {
                            "rule_id": "gambling.weak_keyword_context",
                            "name": "弱关键词需结合上下文",
                            "hit_condition": (
                                "仅出现赌博、博彩、盘口、赔率、上分、回血、庄、车队、房卡、带飞等单个或少量词，"
                                "但缺少平台、资金、参与组织、收益承诺或导流链路。"
                            ),
                            "suggested_risk_level": "low",
                            "rule_exemptions": [
                                _exemption(
                                    "gambling.rule.weak_keyword.benign",
                                    "明确正常语境",
                                    "词语处于普通赛事、游戏娱乐、概率科普、新闻反赌、普法或被骗曝光语境。",
                                    _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-2-5-and-6", "正常赛事、娱乐、新闻和警示上下文不由弱词触发风险。"),
                                )
                            ],
                            "application_stages": ["image_evidence", "video_frame_evidence", "comment_audit", "fusion_audit"],
                            "adjudication_notes": "单个关键词不得直接构成高风险，也不得把不同语境中的弱词机械拼接成闭环。",
                            "enabled": True,
                            "order": 10,
                            "source_mappings": [
                                _source("backend/audit_agent/prompts.py", "GAMBLING_RULES#clauses-4-through-6", "弱黑话、普通元素和低风险边界。"),
                                _source("backend/audit_agent/rule_compiler.py", "_fusion_prompt#principles-4-and-7", "弱证据不机械相加，关键词仅作为特征。"),
                            ],
                        },
                        {
                            "rule_id": "gambling.multimodal_closed_loop",
                            "name": "多模态赌博参与闭环",
                            "hit_condition": (
                                "标题、正文、OCR、画面、ASR或评论中的至少两个互相指向同一活动的证据，共同形成"
                                "赌博对象或入口、参与动作以及资金/收益路径的闭环。"
                            ),
                            "suggested_risk_level": "high",
                            "rule_exemptions": [
                                _exemption(
                                    "gambling.rule.multimodal.warning",
                                    "警示内容跨模态豁免",
                                    "多个模态共同呈现的是反赌、普法、新闻取证或被骗曝光，且没有参与号召。",
                                    _source("backend/audit_agent/rule_compiler.py", "_fusion_prompt#principles-2-4-and-5", "跨模态候选仍须按新闻、批判和警示整体语境重新校准。"),
                                )
                            ],
                            "application_stages": ["fusion_audit"],
                            "adjudication_notes": "不同模态证据必须引用当前输入 evidence_id，且语义指向一致；候选帧和 OCR 线索需在整体上下文中重新校准。",
                            "enabled": True,
                            "order": 20,
                            "source_mappings": [
                                _source("backend/audit_agent/knowledge_packages.py", "DEFAULT_KNOWLEDGE_PACKAGES#gambling-evidence-rules", "平台/群入口与投注资金证据的闭环组合。"),
                                _source("backend/audit_agent/rule_compiler.py", "_fusion_prompt#principles-5-through-9", "候选证据重校准、来源引用和跨模态边界。"),
                                _source("backend/audit_agent/pipeline.py", "_normalize_evidence_items#catalog-reference-validation", "最终证据必须存在于当前 evidence catalog。"),
                            ],
                        },
                    ],
                },
            ],
        }
    )


def gambling_ruleset_v2() -> RuleSetContent:
    """Return the corrected Pilot revision without mutating published v1."""
    content = gambling_ruleset_v1().model_dump(mode="json")
    exemption_conditions = {
        "gambling.exemption.sports_and_games": (
            "无金钱交易、平台、群入口、结算或代理推广的棋牌、桌游、游戏、赛事和概率讨论。"
        ),
        "gambling.exemption.news_and_education": "新闻反赌、科普、被骗曝光和风险提示。",
        "gambling.exemption.warning_qr": "警示语境中的二维码且没有可执行平台或资金路径。",
    }
    for exemption in content["general_exemptions"]:
        exemption["condition"] = exemption_conditions[exemption["exemption_id"]]

    rule_updates = {
        "gambling.platform_entry_and_funding": {
            "hit_condition": (
                "投注平台、App、开户链接、二维码、群入口、代理或联系方式，与下注、充值、上分、"
                "提现、赔率、返水或结算路径同时出现。"
            ),
            "adjudication_notes": (
                "入口与资金或投注证据必须指向同一赌博活动；单独二维码或平台截图不直接构成高风险。"
            ),
            "exemptions": {
                "gambling.rule.platform.warning_context": (
                    "投注平台、二维码或界面只用于反赌新闻、普法、被骗曝光或举报，且没有参与指令。"
                )
            },
        },
        "gambling.betting_and_settlement": {
            "hit_condition": (
                "投注、盘口、赔率、下注、上分、提现、结算、刷流水、赌资或资金盘等明确参与或资金操作。"
            ),
            "adjudication_notes": (
                "盘口、赔率等词只有在真实投注或资金路径中才可达到高风险；普通赛事分析不得按赌博操作处理。"
            ),
            "exemptions": {
                "gambling.rule.operations.discussion": (
                    "只讨论比赛结果、赛事数据或概率，且没有下注或资金执行意图。"
                )
            },
        },
        "gambling.agent_guaranteed_win_promotion": {
            "hit_condition": (
                "代投、包赢、稳赚、回血、返水、带飞、房卡、车队、会员发展或返佣等引导参与。"
            ),
        },
        "gambling.off_platform_diversion": {
            "hit_condition": "赌博黑话与私信、加群、看主页、联系方式、开户链接或交易暗示结合。",
        },
        "gambling.comment_organized_participation": {
            "name": "评论本身组织参与",
            "hit_condition": "评论本身组织或邀请他人下注、加群、私聊、充值、上分或提现。",
            "adjudication_notes": (
                "单条评论独立判断自身风险，不直接归责主帖作者；评论证据主导时的最终建议由融合阶段处理。"
            ),
        },
        "gambling.weak_keyword_context": {
            "hit_condition": (
                "只有赌博黑话、棋牌、体育、筹码、抽卡等元素，缺少投注、资金、平台、代理、导流或"
                "组织参与证据。"
            ),
            "exemptions": {
                "gambling.rule.weak_keyword.benign": (
                    "只属于普通赛事、游戏娱乐、概率科普、新闻反赌、普法或被骗曝光语境。"
                )
            },
        },
        "gambling.multimodal_closed_loop": {
            "hit_condition": (
                "已有跨模态证据共同形成投注平台、资金、代理、导流或组织参与链路；不得把不同类别的"
                "弱证据机械相加。"
            ),
            "exemptions": {
                "gambling.rule.multimodal.warning": (
                    "跨模态内容整体是反赌、普法、新闻取证或被骗曝光，且没有参与号召。"
                )
            },
        },
    }
    for category in content["categories"]:
        if category["category_id"] == "gambling.comments":
            category["name"] = "评论自身组织参与"
            category["description"] = "识别评论本身组织或邀请赌博参与，并保持评论证据独立归责。"
        for rule in category["rules"]:
            update = rule_updates[rule["rule_id"]]
            for key in ("name", "hit_condition", "adjudication_notes"):
                if key in update:
                    rule[key] = update[key]
            exemption_updates = update.get("exemptions") or {}
            for exemption in rule["rule_exemptions"]:
                exemption["condition"] = exemption_updates[exemption["exemption_id"]]
            if rule["rule_id"] == "gambling.comment_organized_participation":
                for mapping in rule["source_mappings"]:
                    mapping["migrated_semantics"] = (
                        "评论本身组织参与可独立召回，但不得直接归责主帖作者。"
                    )
    return RuleSetContent.model_validate(content)
