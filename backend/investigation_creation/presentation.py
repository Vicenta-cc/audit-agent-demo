"""Deterministic Proposal text and evidence for the existing conversation message."""

from typing import Any

from backend.rulesets.compiler import content_hash
from .contracts import TemporaryRuleSetProposal


def render_proposal(proposal: TemporaryRuleSetProposal) -> str:
    content = proposal.content
    lines = [
        "临时研判规则 · 完整规则快照",
        f"名称：{content.name}", f"领域：{content.domain}",
        f"版本：{proposal.version}", f"方案标识：{proposal.proposal_id}",
        f"内容指纹：{proposal.content_hash}", f"研判目标：{content.audit_goal}",
        f"分类数：{len(content.categories)}；规则数：{sum(len(c.rules) for c in content.categories)}",
    ]

    def exemptions(items, label):
        lines.append(label + ("：" if items else "：无"))
        for item in items:
            lines.extend([f"- {item.name}（{item.exemption_id}）", f"  条件：{item.condition}"])
            mappings(item.source_mappings)

    def mappings(items):
        for item in items:
            lines.append(f"  来源：{item.source_file} / {item.source_locator}；语义：{item.migrated_semantics}")

    exemptions(content.general_exemptions, "通用豁免")
    number = 0
    for category in content.categories:
        lines.extend(["", f"分类：{category.name}（{category.category_id}，排序 {category.order}）",
                      f"说明：{category.description}"])
        for rule in category.rules:
            number += 1
            lines.extend(["", f"规则 {number}：{rule.name}（{rule.rule_id}）",
                          f"启用：{'是' if rule.enabled else '否'}；排序：{rule.order}",
                          f"命中条件：{rule.hit_condition}",
                          f"建议风险：{rule.suggested_risk_level}",
                          f"应用阶段：{', '.join(rule.application_stages)}",
                          f"裁决说明：{rule.adjudication_notes}"])
            exemptions(rule.rule_exemptions, "规则豁免")
            mappings(rule.source_mappings)
    return "\n".join(lines)


def message_presentations(snapshots: list[dict[str, Any]], *, session_id: str,
                          turn_id: str, user_message_id: str, assistant_message_id: str,
                          presented_at: str) -> list[dict[str, Any]]:
    records = []
    for snapshot in snapshots:
        proposal = TemporaryRuleSetProposal.model_validate(snapshot)
        if proposal.session_id != session_id or content_hash(proposal.content) != proposal.content_hash:
            raise ValueError("Proposal presentation identity mismatch")
        records.append({
            "session_id": session_id, "source_user_turn_id": turn_id,
            "source_user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id, "presented_at": presented_at,
            "proposal_id": proposal.proposal_id, "proposal_version": proposal.version,
            "content_hash": proposal.content_hash,
            "snapshot": proposal.model_dump(mode="json"),
            "presentation_format": "ruleset-proposal-text-v1",
            "text": render_proposal(proposal),
            "boundary": "durable_public_assistant_message",
        })
    return records
