"""Zero-cost text matching for triage: normalization, lexicon terms, diversion patterns."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")
# Curly quotes (""'') are U+201C U+201D U+2018 U+2019
_SEPARATORS = re.compile("[\s\\.\\-_*·•/\\\\|,，。、~～!！?？:：;；'\"" + "“”‘’" + "''()（）\\[\\]【】<>《》]+")
_SEARCH_ONLY_MATCH_TYPES = {"平台搜索词", "平台标签", "tag"}

DIVERSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("微信/vx", re.compile(r"(微信|vx|v信|wx|威信|薇信|加v|\+v|v\s*[:：])", re.IGNORECASE)),
    ("QQ", re.compile(r"(qq|扣扣|企鹅|球球)\s*[:：]?\s*\d{5,}", re.IGNORECASE)),
    ("telegram", re.compile(r"(telegram|tg|电报|飞机|纸飞机)", re.IGNORECASE)),
    ("私聊导流", re.compile(r"(私聊|私我|私信我|滴滴我|扣1|懂的来|想看更多)")),
    ("主页导流", re.compile(r"(主页有|看主页|看简介|简介有|头像有|评论区第一条)")),
]


@dataclass(frozen=True)
class TriageTerm:
    keyword: str
    match_type: str
    category_id: str
    risk_level: str
    entry_kind: str = ""


@dataclass(frozen=True)
class TriageHit:
    keyword: str
    match_type: str
    category_id: str
    risk_level: str
    field: str
    snippet: str


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = _ZERO_WIDTH.sub("", value)
    value = _SEPARATORS.sub("", value)
    return value.lower()


def terms_from_rows(rows: list[dict]) -> list[TriageTerm]:
    terms: list[TriageTerm] = []
    for row in rows:
        keyword = str(row.get("keyword") or "").strip()
        if keyword:
            terms.append(TriageTerm(keyword, str(row.get("match_type") or "模糊"),
                                    str(row.get("category_id") or ""), str(row.get("risk_level") or ""),
                                    str(row.get("entry_kind") or "")))
    return terms


def _snippet(text: str, start: int, end: int, radius: int = 12) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)]


def match_terms(text: str, field: str, terms: list[TriageTerm]) -> list[TriageHit]:
    raw = str(text or "")
    if not raw:
        return []
    normalized = normalize_text(raw)
    hits: list[TriageHit] = []
    for term in terms:
        if term.match_type in _SEARCH_ONLY_MATCH_TYPES:
            continue
        if term.match_type == "正则":
            try:
                match = re.search(term.keyword, raw, re.IGNORECASE)
            except re.error:
                continue
            if match:
                hits.append(TriageHit(term.keyword, term.match_type, term.category_id, term.risk_level,
                                      field, _snippet(raw, match.start(), match.end())))
            continue
        needle = normalize_text(term.keyword)
        index = normalized.find(needle) if needle else -1
        if index >= 0:
            hits.append(TriageHit(term.keyword, term.match_type, term.category_id, term.risk_level,
                                  field, _snippet(normalized, index, index + len(needle))))
    return hits


def diversion_hits(text: str, field: str) -> list[TriageHit]:
    raw = str(text or "")
    if not raw:
        return []
    hits: list[TriageHit] = []
    for label, pattern in DIVERSION_PATTERNS:
        match = pattern.search(raw)
        if match:
            hits.append(TriageHit(label, "正则", "diversion", "高", field, _snippet(raw, match.start(), match.end())))
    return hits
