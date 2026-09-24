"""Zero-cost text matching for triage: normalization and lexicon terms."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
# Curly quotes (""'') are U+201C U+201D U+2018 U+2019
_SEPARATORS = re.compile(r"[\s\.\-_*·•/\\|,，。、~～!！?？:：;；'" '"' r"“”‘’''()（）\[\]【】<>《》]+")
_SEARCH_ONLY_MATCH_TYPES = {"平台搜索词", "平台标签", "tag"}


@dataclass(frozen=True)
class TriageTerm:
    keyword: str
    match_type: str
    category_id: str
    risk_level: str
    entry_kind: str = ""
    entry_group: str = ""


@dataclass(frozen=True)
class TriageHit:
    keyword: str
    match_type: str
    category_id: str
    risk_level: str
    field: str
    snippet: str
    entry_group: str = ""


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
                                    str(row.get("entry_kind") or ""), str(row.get("entry_group") or "")))
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
                                      field, _snippet(raw, match.start(), match.end()), term.entry_group))
            continue
        needle = normalize_text(term.keyword)
        index = normalized.find(needle) if needle else -1
        if index >= 0:
            hits.append(TriageHit(term.keyword, term.match_type, term.category_id, term.risk_level,
                                  field, _snippet(normalized, index, index + len(needle)), term.entry_group))
    return hits
