"""Narrow provider routing for explicit resource-generation turns."""

from __future__ import annotations

import re
from typing import Literal


ResourceKind = Literal["ruleset", "lexicon"]


def resource_generation_kinds(message: str) -> tuple[ResourceKind, ...]:
    """Detect creation requests while excluding edits, saves and reads."""

    text = re.sub(r"\s+", "", str(message or "")).lower()
    if not text:
        return ()
    if re.search(
        r"(?:生成|创建|新建|重新生成|重做|做一套|来一套|给我一套|帮我(?:生成|创建|写|做))",
        text,
    ) is None:
        return ()
    if "重新生成" not in text and re.search(
        r"(?:修改|编辑|调整|保存|发布|采用|启用|停用|删除|读取|查看)",
        text,
    ):
        return ()
    kinds: list[ResourceKind] = []
    if re.search(r"(?:审核规则|规则集|规则方案|研判规则)", text):
        kinds.append("ruleset")
    if re.search(r"(?:黑话库|词库|关键词|搜索词|召回词)", text):
        kinds.append("lexicon")
    return tuple(kinds)
