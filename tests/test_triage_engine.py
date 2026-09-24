from __future__ import annotations

import json
from pathlib import Path

from backend.audit_agent.triage import (
    DISCARD_SCORE, OFFICIAL_VERIFY_PATTERNS, CandidateScore, TriageEngine, load_collected_selections,
    mark_candidates_collected, rank_candidates, select_candidate, write_candidates_file,
)


class FakeQwen:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def audit_text(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return dict(self.response) if isinstance(self.response, dict) else self.response


class FakeLexicon:
    def triage_terms(self, category_ids):
        return [{"category_id": "gambling", "keyword": "上分", "match_type": "模糊", "risk_level": "高", "entry_kind": ""},
                {"category_id": "gambling", "keyword": "上分群", "match_type": "模糊", "risk_level": "高", "entry_kind": ""},
                # 引流用语在词库里以正则条目维护，不再由代码内置的导流模板提供
                {"category_id": "gambling", "keyword": r"加\s*微信", "match_type": "正则", "risk_level": "高",
                 "entry_kind": ""}]

    def search_word_groups(self, category_ids):
        return {}


class EntryLexicon:
    """房卡词条：主词 + 两个变体；十三水是另一个词条；彩金与平台搜索词「注册送彩金」没有变体关联。"""

    def triage_terms(self, category_ids):
        def row(keyword, group, match_type="模糊", kind="main"):
            return {"category_id": "gambling", "keyword": keyword, "match_type": match_type, "risk_level": "高",
                    "entry_kind": kind, "entry_group": group}
        return [row("房卡", "房卡"), row("房卡代理", "房卡", kind="variant"), row("批发房卡", "房卡", kind="variant"),
                row("十三水", "十三水"), row("彩金", "彩金"), row("送彩金", "送彩金", match_type="正则")]

    def search_word_groups(self, category_ids):
        return {"房卡": "房卡", "房卡代理": "房卡", "批发房卡": "房卡", "十三水": "十三水", "彩金": "彩金",
                "注册送彩金": "注册送彩金"}


def _entry_engine(response):
    return TriageEngine(EntryLexicon(), FakeQwen(response), model="qwen3.6-flash", max_comments=60,
                        request_timeout=60)


def _engine(response, **limits):
    return TriageEngine(FakeLexicon(), FakeQwen(response), model="qwen3.6-flash", max_comments=60,
                        request_timeout=60, **limits)


class LeveledLexicon:
    """同一类目下高/中/低/未标等级各一个词，用来验证按词条风险等级计分。"""

    def triage_terms(self, category_ids):
        def row(keyword, level):
            return {"category_id": "gambling", "keyword": keyword, "match_type": "模糊", "risk_level": level,
                    "entry_kind": "main", "entry_group": keyword}
        return [row("甲词", "高"), row("乙词", "中"), row("丙词", "低"), row("丁词", "")]

    def search_word_groups(self, category_ids):
        return {}


def _leveled_engine(response, **weights):
    return TriageEngine(LeveledLexicon(), FakeQwen(response), model="qwen3.6-flash", max_comments=60,
                        request_timeout=60, **weights)


def _leveled(engine, text, comments=()):
    return engine.score("a", 1, {"desc": text}, list(comments), engine.terms_for(["gambling"]))


def test_high_hit_plus_strong_model_adds_both_scores():
    engine = _leveled_engine({"suspicion": "strong", "reason": "评论区约私聊"})
    scored = _leveled(engine, "甲词")
    assert scored.score == 500 and scored.band == "strong"
    assert len(engine.qwen.calls) == 1      # 有规则命中也要过模型
    assert scored.reason == "词库命中 甲词(高) +300；模型 strong +200：评论区约私聊"
    assert [hit["keyword"] for hit in scored.hits] == ["甲词"]


def test_medium_hit_weights_combine_with_strong_and_weak():
    assert _leveled(_leveled_engine({"suspicion": "strong"}), "乙词").score == 300
    weak = _leveled(_leveled_engine({"suspicion": "weak"}), "乙词")
    assert weak.score == 200 and weak.band == "weak"


def test_low_and_unlabelled_levels_use_their_weights():
    assert _leveled(_leveled_engine({"suspicion": "strong"}), "丙词").score == 250
    assert _leveled(_leveled_engine({"suspicion": "strong"}), "丁词").score == 300   # 未标等级按中计


def test_model_none_zeroes_the_rule_hits():
    engine = _leveled_engine({"suspicion": "none", "reason": "反诈科普"})
    scored = _leveled(engine, "甲词", [{"comment_id": "c1", "content": "甲词"}])
    assert scored.score == 0 and scored.band == "none"
    assert scored.reason == "模型判 none，词库命中 2 处不计分：反诈科普"
    assert len(scored.hits) == 2
    assert select_candidate(rank_candidates([scored]), exclude_keys=set()) is None


def test_model_failure_keeps_the_rule_score_as_weak():
    engine = _leveled_engine(RuntimeError("down"))
    scored = _leveled(engine, "甲词")
    assert scored.score == 400 and scored.band == "weak"
    assert scored.reason.startswith("词库命中 甲词(高) +300；初筛模型调用失败，按 weak 计")


def test_rule_score_is_capped_before_the_model_score_is_added():
    engine = _leveled_engine({"suspicion": "strong"})
    comments = [{"comment_id": f"c{i}", "content": "甲词"} for i in range(3)]
    scored = _leveled(engine, "甲词", comments)
    assert scored.score == 900 + 200
    assert "甲词(高)×4 +900" in scored.reason


def test_rule_weights_are_configurable():
    engine = _leveled_engine({"suspicion": "weak"}, rule_score_high=500, rule_score_cap=2000)
    assert _leveled(engine, "甲词").score == 600
    capped = _leveled_engine({"suspicion": "weak"}, rule_score_medium=100, rule_score_cap=150)
    assert _leveled(capped, "乙词丁词").score == 150 + 100


def test_prompt_lists_the_lexicon_hits_as_facts():
    engine = _leveled_engine({"suspicion": "strong"})
    _leveled(engine, "甲词", [{"comment_id": "c9", "content": "乙词"}])
    prompt = engine.qwen.calls[0][0]
    assert "词库命中：甲词@desc，乙词@comment:c9" in prompt

    no_hits = _leveled_engine({"suspicion": "none"})
    _leveled(no_hits, "日常")
    assert "词库命中：" not in no_hits.qwen.calls[0][0]


def test_prompt_lists_at_most_five_lexicon_hits():
    engine = _leveled_engine({"suspicion": "strong"})
    comments = [{"comment_id": f"c{i}", "content": "甲词"} for i in range(6)]
    _leveled(engine, "日常", comments)
    line = next(l for l in engine.qwen.calls[0][0].splitlines() if l.startswith("词库命中："))
    assert line.count("@") == 5


def test_rule_hit_is_scored_together_with_the_model():
    engine = _engine({"suspicion": "weak"})
    terms = engine.terms_for(["gambling"])
    score = engine.score("a", 1, {"desc": "今晚上分稳赢", "liked_count": "10"}, [], terms)
    assert score.band == "weak" and score.score == 400 and len(engine.qwen.calls) == 1
    assert score.hits[0]["keyword"] == "上分"


def test_search_keyword_self_hit_is_not_scored_and_falls_through_to_the_model():
    # 搜「上分」搜出来的帖子必然带「上分」，这一条自命中不能当规则分，否则模型判强的候选永远抢不过它
    engine = _engine({"suspicion": "strong", "reason": "评论区约私聊"})
    item = {"title": "今晚上分吗", "liked_count": "10"}
    scored = engine.score("a", 1, item, [], engine.terms_for(["gambling"]), search_keyword="上分")
    assert scored.band == "strong" and scored.score == 200
    assert len(engine.qwen.calls) == 1
    assert scored.reason == "评论区约私聊（搜索词所在词条命中 1 处不计分）"

    # 不带搜索词（别的调用方）时自命中照常计规则分
    baseline = _engine({"suspicion": "strong", "reason": "评论区约私聊"})
    plain = baseline.score("a", 1, item, [], baseline.terms_for(["gambling"]))
    assert plain.band == "strong" and plain.score == 500 and len(baseline.qwen.calls) == 1


def test_other_lexicon_term_still_counts_when_the_search_keyword_is_dropped():
    engine = _engine({"suspicion": "weak"})
    scored = engine.score("b", 1, {"title": "上分群带你上分"}, [], engine.terms_for(["gambling"]), search_keyword="上分")
    assert scored.band == "weak" and scored.score == 400
    assert [hit["keyword"] for hit in scored.hits] == ["上分群"]
    assert "上分群(高) +300" in scored.reason and scored.reason.endswith("（搜索词所在词条命中 1 处不计分）")


def test_regex_lexicon_entry_survives_the_search_keyword_drop():
    # 自命中被扣掉后，词库里的正则条目照样计分——引流用语现在全部来自词库
    engine = _engine({"suspicion": "weak", "reason": "引流"})
    scored = engine.score("c", 1, {"title": "上分", "desc": "加 微信详聊"}, [], engine.terms_for(["gambling"]),
                          search_keyword="上分")
    assert scored.band == "weak" and scored.score == 400
    assert [hit["match_type"] for hit in scored.hits] == ["正则"]
    assert scored.reason == "词库命中 加\\s*微信(高) +300；模型 weak +100：引流（搜索词所在词条命中 1 处不计分）"


def test_searching_a_variant_drops_hits_on_its_main_and_sibling_variants():
    # 搜「房卡代理」：主词「房卡」和兄弟变体「批发房卡」都属于同一个词条，不能算规则分
    engine = _entry_engine({"suspicion": "weak", "reason": "疑似代理"})
    scored = engine.score("a", 1, {"desc": "批发房卡，房卡便宜"}, [], engine.terms_for(["gambling"]),
                          search_keyword="房卡代理")
    assert scored.band == "weak" and len(engine.qwen.calls) == 1
    assert scored.reason == "疑似代理（搜索词所在词条命中 2 处不计分）"


def test_other_entry_still_scores_when_the_searched_entry_is_dropped():
    engine = _entry_engine({"suspicion": "weak"})
    scored = engine.score("b", 1, {"desc": "房卡代理，十三水开桌"}, [], engine.terms_for(["gambling"]),
                          search_keyword="房卡代理")
    assert scored.band == "weak" and scored.score == 400
    assert [hit["keyword"] for hit in scored.hits] == ["十三水"]
    assert "十三水" in scored.reason


def test_term_contained_in_the_search_word_is_dropped_without_a_variant_link():
    # 搜平台搜索词「注册送彩金」，帖子必然带「彩金」；二者没有变体关联，靠包含关系排除
    engine = _entry_engine({"suspicion": "none", "reason": "无交易"})
    scored = engine.score("c", 1, {"title": "新人注册彩金"}, [], engine.terms_for(["gambling"]),
                          search_keyword="注册送彩金")
    assert scored.band == "none" and len(engine.qwen.calls) == 1
    assert scored.reason == "无交易（搜索词所在词条命中 1 处不计分）"


def test_regex_term_is_never_dropped_by_the_containment_rule():
    engine = _entry_engine({"suspicion": "weak"})
    scored = engine.score("d", 1, {"title": "注册送彩金"}, [], engine.terms_for(["gambling"]),
                          search_keyword="注册送彩金")
    assert scored.band == "weak" and scored.score == 400
    assert [(hit["keyword"], hit["match_type"]) for hit in scored.hits] == [("送彩金", "正则")]
    assert scored.hits[0]["entry_group"] == "送彩金"


def test_blank_search_keyword_drops_nothing():
    engine = _entry_engine({"suspicion": "weak"})
    scored = engine.score("e", 1, {"desc": "批发房卡"}, [], engine.terms_for(["gambling"]), search_keyword="  ")
    assert scored.band == "weak" and scored.score == 700
    assert [hit["keyword"] for hit in scored.hits] == ["房卡", "批发房卡"]


def test_model_bands_map_to_scores():
    engine = _engine({"suspicion": "weak", "content_type": "婚恋", "reason": "暗示"})
    weak = engine.score("b", 2, {"desc": "日常", "user_signature": "普通人"}, [{"comment_id": "c1", "content": "有资源吗"}], [])
    assert weak.band == "weak" and weak.score == 100
    assert "有资源吗" in engine.qwen.calls[0][0] and engine.qwen.calls[0][1]["enable_thinking"] is False


def test_content_type_is_descriptive_and_no_longer_overrides_the_band():
    # 放行语境由判定规则的放行条目决定，代码不再按 content_type 强制改判
    engine = _engine({"suspicion": "weak", "content_type": "科普", "reason": "反诈提醒里提到上分"})
    scored = engine.score("a", 1, {"desc": "民警提醒大家注意"}, [], [])
    assert scored.band == "weak" and scored.score == 100
    assert scored.reason == "反诈提醒里提到上分"
    assert scored.model["content_type"] == "科普"

    # note 后缀照旧拼在理由末尾
    noted_engine = _engine({"suspicion": "weak", "content_type": "科普", "reason": "反诈提醒"})
    noted = noted_engine.score("b", 1, {"title": "今晚上分吗"}, [], noted_engine.terms_for(["gambling"]),
                               search_keyword="上分")
    assert noted.reason == "反诈提醒（搜索词所在词条命中 1 处不计分）"


def test_news_context_still_yields_strong_when_the_model_sees_trade_intent():
    engine = _engine({"suspicion": "strong", "content_type": "新闻", "reason": "评论区留了联系方式"})
    scored = engine.score("a", 1, {"desc": "记者报道"}, [], [])
    assert scored.band == "strong" and scored.score == 200
    assert scored.reason == "评论区留了联系方式"


def test_ordinary_content_type_keeps_the_weak_band():
    engine = _engine({"suspicion": "weak", "content_type": "日常", "reason": "有点可疑"})
    scored = engine.score("a", 1, {"desc": "随手拍"}, [], [])
    assert scored.band == "weak" and scored.score == 100


def test_prompt_is_built_from_the_task_judgement_rules():
    engine = _engine({"suspicion": "none"})
    rules = {"audit_goal": "识别赌博、上分提现与代理推广风险",
             "evidence_rules": "1. 高危：出现联系方式或上下分交易。\n2. 放行：反诈宣传与新闻报道。",
             "fusion_rules": "包赢与私域导流同时出现时上调风险。"}

    prompt = engine.build_prompt({"desc": "民警提醒"}, [], engine.terms_for(["gambling"]), rules=rules)

    assert "审核目标：识别赌博、上分提现与代理推广风险" in prompt
    assert "证据规则：\n1. 高危：出现联系方式或上下分交易。\n2. 放行：反诈宣传与新闻报道。" in prompt
    assert "包赢与私域导流同时出现时上调风险。" in prompt
    assert "符合高危定义答 strong，符合中危定义答 weak，属于低危、放行或安全语境答 none" in prompt
    for removed in ("反诈科普与警示", "游戏术语", "科普/新闻语境"):
        assert removed not in prompt
    # 事实输入与输出契约照旧
    assert "lexicon_terms" in prompt and "suspicion" in prompt and "最多列 5 项" in prompt


def test_prompt_without_rules_has_no_rules_section():
    engine = _engine({"suspicion": "none"})
    prompt = engine.build_prompt({"desc": "民警提醒"}, [], [])
    assert "审核目标" not in prompt and "证据规则" not in prompt
    assert "最多列 5 项" in prompt


def test_fusion_rules_identical_to_evidence_rules_are_not_repeated():
    engine = _engine({"suspicion": "none"})
    rules = {"audit_goal": "赌博", "evidence_rules": "1. 高危：上下分交易。", "fusion_rules": "1. 高危：上下分交易。"}
    prompt = engine.build_prompt({"desc": "x"}, [], [], rules=rules)
    assert prompt.count("1. 高危：上下分交易。") == 1


def test_score_sends_the_task_rules_to_the_model():
    engine = _engine({"suspicion": "weak", "reason": "可疑"})
    engine.score("a", 1, {"desc": "日常"}, [], [], rules={"audit_goal": "涉赌识别", "evidence_rules": "1. 高危：约赌。"})
    prompt, _kwargs = engine.qwen.calls[0]
    assert "审核目标：涉赌识别" in prompt and "证据规则：\n1. 高危：约赌。" in prompt


def test_blue_v_is_discarded_before_the_rules_and_the_model():
    # 蓝V的反诈科普必然带黑话和联系方式，两处命中也不该占掉该词唯一的深审名额
    engine = _engine({"suspicion": "strong", "reason": "有暗语"})
    terms = engine.terms_for(["gambling"])
    item = {"desc": "今晚上分群，加微信", "enterprise_verify_reason": "某某日报官方账号", "liked_count": "7"}

    scored = engine.score("a", 1, item, [], terms)

    assert DISCARD_SCORE == -1000
    assert scored.band == "discard" and scored.score == DISCARD_SCORE
    assert scored.hits == [] and scored.model is None and scored.engagement == 7
    assert engine.qwen.calls == []      # 丢弃的候选不调模型
    assert scored.reason == "官方/机构认证账号（某某日报官方账号），不进精审"
    assert select_candidate(rank_candidates([scored]), exclude_keys=set()) is None

    plain = engine.score("b", 1, {"desc": "今晚上分"}, [], terms)
    assert plain.band == "strong" and plain.score == 500 and "认证账号" not in plain.reason
    assert len(engine.qwen.calls) == 1


def test_official_institution_blue_v_is_discarded_by_built_in_patterns():
    engine = _engine({"suspicion": "none"})
    for reason in ("新华通讯社官方账号", "泾源县公安局官方抖音账号", "河南省体育彩票管理中心"):
        scored = engine.score("a", 1, {"desc": "普通内容", "enterprise_verify_reason": reason}, [], [])
        assert scored.band == "discard" and scored.score == DISCARD_SCORE, reason
        assert scored.reason == f"官方/机构认证账号（{reason[:40]}），不进精审"
        assert engine.qwen.calls == []


def test_merchant_blue_v_is_not_discarded_and_proceeds_to_the_model():
    engine = _engine({"suspicion": "none"})
    scored = engine.score("a", 1, {"desc": "普通内容", "enterprise_verify_reason": "商家认证账号"}, [], [])
    assert scored.band != "discard"
    assert len(engine.qwen.calls) == 1      # 没有规则命中，走到模型判断


def test_merchant_blue_v_is_discarded_only_above_the_unverified_follower_limit():
    engine = _engine({"suspicion": "none"}, max_followers_unverified=1000000)
    item = {"desc": "普通内容", "enterprise_verify_reason": "广州展丰智能科技有限公司"}

    big = engine.score("a", 1, {**item, "follower_count": "2000000"}, [], [])
    assert big.band == "discard" and big.score == DISCARD_SCORE
    assert engine.qwen.calls == []

    small = engine.score("b", 1, {**item, "follower_count": "500000"}, [], [])
    assert small.band != "discard"


def test_official_verify_patterns_env_override_narrows_the_match():
    engine = TriageEngine(FakeLexicon(), FakeQwen({"suspicion": "none"}), model="qwen3.6-flash",
                          max_comments=60, request_timeout=60, official_verify_patterns=("测试",))

    discarded = engine.score("a", 1, {"desc": "内容", "enterprise_verify_reason": "测试账号"}, [], [])
    assert discarded.band == "discard" and discarded.score == DISCARD_SCORE

    passthrough = engine.score("b", 1, {"desc": "内容", "enterprise_verify_reason": "新华通讯社官方账号"}, [], [])
    assert passthrough.band != "discard"


def test_official_verify_patterns_default_matches_the_module_constant():
    engine = _engine({"suspicion": "none"})
    scored = engine.score("a", 1, {"desc": "内容", "enterprise_verify_reason": OFFICIAL_VERIFY_PATTERNS[0]}, [], [])
    assert scored.band == "discard"


def test_personal_yellow_v_is_discarded_only_above_the_follower_limit():
    item = {"desc": "今晚上分", "custom_verify": "知名情感博主", "follower_count": "600000"}

    engine = _engine({"suspicion": "none"}, max_followers_personal_verified=500000)
    discarded = engine.score("a", 1, item, [], engine.terms_for(["gambling"]))
    assert discarded.band == "discard" and discarded.score == DISCARD_SCORE
    assert discarded.reason == "个人认证账号（知名情感博主）粉丝 600000 超过 500000，不进精审"
    assert engine.qwen.calls == []

    kept = engine.score("b", 1, {**item, "follower_count": "400000"}, [], engine.terms_for(["gambling"]))
    assert kept.band == "none" and kept.score == 0 and len(engine.qwen.calls) == 1   # 阈值以下照常走规则+模型

    unlimited = _engine({"suspicion": "none"}, max_followers_personal_verified=0)
    assert unlimited.score("c", 1, item, [], []).band == "none"      # 0 = 不限


def test_unverified_account_is_discarded_only_above_its_own_limit():
    engine = _engine({"suspicion": "none"}, max_followers_unverified=1000000)

    big = engine.score("a", 1, {"desc": "日常", "follower_count": "1500000"}, [], [])
    assert big.band == "discard" and big.score == DISCARD_SCORE
    assert big.reason == "粉丝 1500000 超过 1000000，不进精审"
    assert engine.qwen.calls == []

    small = engine.score("b", 1, {"desc": "日常", "follower_count": "900000"}, [], [])
    assert small.band == "none" and len(engine.qwen.calls) == 1

    unlimited = _engine({"suspicion": "none"}, max_followers_unverified=0)
    assert unlimited.score("c", 1, {"desc": "日常", "follower_count": "1500000"}, [], []).band == "none"


def test_follower_count_parses_strings_and_treats_a_missing_key_as_zero():
    engine = _engine({"suspicion": "none"}, max_followers_unverified=1000)
    assert engine.score("a", 1, {"desc": "日常", "follower_count": "1708"}, [], []).band == "discard"
    assert engine.score("b", 1, {"desc": "日常"}, [], []).band == "none"
    assert engine.score("c", 1, {"desc": "日常", "follower_count": ""}, [], []).band == "none"


def test_a_discard_is_never_selected_even_when_it_sorts_first():
    discard = CandidateScore("d1", 1, DISCARD_SCORE, "discard", "蓝V认证账号（官方），不进精审", [], None, 0)
    rule = CandidateScore("d2", 2, 300, "rule", "命中", [], None, 0)
    assert [c.content_key for c in rank_candidates([discard, rule])] == ["d2", "d1"]   # 丢弃排最后
    assert select_candidate([discard, rule], exclude_keys=set()) is None


def test_model_failure_counts_as_weak():
    score = _engine(RuntimeError("down")).score("d", 1, {"desc": "日常"}, [], [])
    assert score.band == "weak" and score.score == 100


def test_non_dict_model_output_degrades_to_weak_instead_of_killing_the_keyword():
    # _parse_json_object 原样返回 json.loads 的结果，模型答一个数组就会得到 list
    score = _engine([{"suspicion": "strong"}]).score("e", 1, {"desc": "日常"}, [], [])
    assert score.band == "weak" and score.score == 100 and "初筛模型输出不合法" in score.reason
    assert score.model is None


def test_non_list_matched_terms_is_not_split_into_characters():
    engine = _engine({"suspicion": "strong", "matched_terms": "上分", "locations": "desc", "reason": "有暗语"})
    score = engine.score("f", 1, {"desc": "日常"}, [], [])
    assert score.band == "strong" and score.score == 200
    assert score.hits == []
    assert score.model["matched_terms"] == [] and score.model["locations"] == []


def test_rank_and_select_prefer_score_then_deeper_rank_then_low_engagement(tmp_path: Path):
    scores = [
        CandidateScore("k1", 1, 100, "weak", "", [], None, engagement=500),
        CandidateScore("k2", 5, 100, "weak", "", [], None, engagement=20),
        CandidateScore("k3", 3, 300, "rule", "", [], None, engagement=999),
        CandidateScore("k4", 9, 100, "weak", "", [], None, engagement=20),
    ]
    ranked = rank_candidates(scores)
    assert [item.content_key for item in ranked] == ["k3", "k4", "k2", "k1"]
    assert select_candidate(ranked, exclude_keys={"k3", "k4"}).content_key == "k2"
    assert select_candidate(ranked, exclude_keys={"k1", "k2", "k3", "k4"}) is None
    all_normal = rank_candidates([CandidateScore("z1", 1, 0, "none", "", [], None, 0),
                                  CandidateScore("z2", 2, DISCARD_SCORE, "discard", "", [], None, 0)])
    assert select_candidate(all_normal, exclude_keys=set()) is None   # 全判正常 → 该词跳过
    path = write_candidates_file(tmp_path, "上分", ranked, ranked[0], "triage")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["selected"] == "k3" and payload["strategy"] == "triage" and len(payload["candidates"]) == 4


def _score(key: str) -> CandidateScore:
    return CandidateScore(key, 1, 300, "rule", "命中", [], None, 0)


def test_collected_marker_is_written_off_and_read_back_across_rotation_dirs(tmp_path: Path):
    crawl_dir = tmp_path / "crawler"
    first = crawl_dir / "candidates" / "01-词A"
    write_candidates_file(first, "词A", [_score("a1")], _score("a1"), "triage")
    assert json.loads((first / "candidates.json").read_text(encoding="utf-8"))["collected"] is False
    assert load_collected_selections(crawl_dir) == {}      # 精采未完成前不算已选定

    mark_candidates_collected(first)
    assert load_collected_selections(crawl_dir) == {"词A": "a1"}

    # 切换账号后的候选目录也要读到
    second = crawl_dir / "rotation-acc2" / "candidates" / "02-词B"
    write_candidates_file(second, "词B", [_score("b1")], _score("b1"), "triage")
    mark_candidates_collected(second)
    assert load_collected_selections(crawl_dir) == {"词A": "a1", "词B": "b1"}

    # 没选中的词即使被标记也不算已采
    third = crawl_dir / "candidates" / "03-词C"
    write_candidates_file(third, "词C", [], None, "triage")
    mark_candidates_collected(third)
    assert "词C" not in load_collected_selections(crawl_dir)


def test_collected_helpers_tolerate_missing_and_broken_files(tmp_path: Path):
    mark_candidates_collected(tmp_path / "does-not-exist")          # 文件缺失 → 静默跳过
    broken = tmp_path / "candidates" / "01-词A"
    broken.mkdir(parents=True)
    (broken / "candidates.json").write_text("{not json", encoding="utf-8")
    mark_candidates_collected(broken)
    assert load_collected_selections(tmp_path) == {}


def test_prompt_bounds_list_lengths_and_model_gets_a_larger_output_budget():
    engine = _engine({"suspicion": "none"})
    prompt = engine.build_prompt({"desc": "x"}, [], [])
    assert "最多列 5 项" in prompt
    engine.score("k", 1, {"desc": "x"}, [], [])
    _prompt, kwargs = engine.qwen.calls[-1]
    assert kwargs.get("max_tokens") == 800
