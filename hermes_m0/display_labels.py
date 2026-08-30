"""Deterministic Chinese labels for public Investigation ToolResults."""

from __future__ import annotations

from typing import Mapping


PLATFORM_LABELS = {
    "dy": "抖音",
    "douyin": "抖音",
    "xhs": "小红书",
    "xiaohongshu": "小红书",
}
DECISION_LABELS = {
    "pass": "通过",
    "review": "需复核",
    "reject": "不通过",
}
RISK_LEVEL_LABELS = {
    "none": "无风险",
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}
FINDING_TYPE_LABELS = {
    "audit_finding": "帖子级审核发现",
    "investigation_finding": "主要调查发现",
    "standalone_risk_post": "其他独立风险事项",
}
EVIDENCE_TYPE_LABELS = {
    "comment": "评论证据",
    "keyframe": "画面证据",
    "text": "文字证据",
}


def enum_label(mapping: Mapping[str, str], value: object) -> str | None:
    """Return a known display label without guessing unknown enum values."""

    return mapping.get(value) if isinstance(value, str) else None
