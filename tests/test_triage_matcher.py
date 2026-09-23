from __future__ import annotations

from backend.audit_agent.lexicon_store import LexiconStore
from backend.audit_agent.triage_matcher import (
    TriageTerm, match_terms, normalize_text, terms_from_rows,
)


def test_normalize_strips_zero_width_separators_and_fullwidth():
    assert normalize_text("上​分  Ｖ·Ｘ") == "上分vx"
    assert normalize_text("回 血 ！！") == "回血"


def test_normalize_strips_curly_quotes():
    assert normalize_text("上“分”不用等") == "上分不用等"
    hits = match_terms("上“分”稳赢", "desc", [TriageTerm("上分", "模糊", "gambling", "高")])
    assert len(hits) == 1


def test_fuzzy_and_exact_match_on_normalized_text():
    terms = [TriageTerm("上分", "模糊", "gambling", "高"), TriageTerm("盘口", "精确", "gambling", "高")]
    hits = match_terms("今晚上.分稳赢，盘 口已开", "desc", terms)
    assert {hit.keyword for hit in hits} == {"上分", "盘口"}
    assert hits[0].field == "desc"


def test_regex_term_matches_raw_text_case_insensitive():
    terms = [TriageTerm(r"v[信x]\s*[:：]?\s*[a-z0-9_-]{5,}", "正则", "fraud", "高")]
    hits = match_terms("加 VX: abc_123 私聊", "comment:1", terms)
    assert len(hits) == 1 and hits[0].match_type == "正则"


def test_search_only_terms_are_ignored():
    # 平台搜索词/标签只用来搜，不参与文本命中；判定信号只来自可匹配的词库条目
    assert match_terms("泳装", "desc", [TriageTerm("泳装", "平台搜索词", "soft", "中")]) == []
    assert match_terms("泳装", "desc", [TriageTerm("泳装", "tag", "soft", "中")]) == []
    assert len(match_terms("泳装", "desc", [TriageTerm("泳装", "模糊", "soft", "中")])) == 1


def test_lexicon_store_triage_terms_include_seed_rows(tmp_path):
    store = LexiconStore(tmp_path / "lex.sqlite3")
    rows = store.triage_terms(["gambling", "fraud"])
    assert {"上分", "盘口", "刷流水"} <= {row["keyword"] for row in rows}
    assert all(row["match_type"] in {"精确", "模糊", "正则", "黑话词"} for row in rows)
    assert isinstance(terms_from_rows(rows)[0], TriageTerm)
