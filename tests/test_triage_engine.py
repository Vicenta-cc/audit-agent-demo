from __future__ import annotations

import json
from pathlib import Path

from backend.audit_agent.triage import (
    CandidateScore, TriageEngine, rank_candidates, select_candidate, write_candidates_file,
)


class FakeQwen:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def audit_text(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return dict(self.response)


class FakeLexicon:
    def triage_terms(self, category_ids):
        return [{"category_id": "gambling", "keyword": "上分", "match_type": "模糊", "risk_level": "高", "entry_kind": ""}]


def _engine(response):
    return TriageEngine(FakeLexicon(), FakeQwen(response), model="qwen3.6-flash", max_comments=60, request_timeout=60)


def test_rule_hit_scores_high_without_model_call():
    engine = _engine({"suspicion": "none"})
    terms = engine.terms_for(["gambling"])
    score = engine.score("a", 1, {"desc": "今晚上分稳赢", "liked_count": "10"}, [], terms)
    assert score.band == "rule" and score.score >= 300 and engine.qwen.calls == []
    assert score.hits[0]["keyword"] == "上分"


def test_trusted_verified_is_penalized_and_model_bands_map_to_scores():
    engine = _engine({"suspicion": "weak", "content_type": "婚恋", "reason": "暗示"})
    weak = engine.score("b", 2, {"desc": "日常", "user_signature": "普通人"}, [{"comment_id": "c1", "content": "有资源吗"}], [])
    assert weak.band == "weak" and weak.score == 100
    assert "有资源吗" in engine.qwen.calls[0][0] and engine.qwen.calls[0][1]["enable_thinking"] is False
    trusted = _engine({"suspicion": "none"}).score("c", 3, {"desc": "新闻", "enterprise_verify_reason": "官方"}, [], [])
    assert trusted.band == "trusted" and trusted.score == -500


def test_model_failure_counts_as_weak():
    score = _engine(RuntimeError("down")).score("d", 1, {"desc": "日常"}, [], [])
    assert score.band == "weak" and score.score == 100


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
                                  CandidateScore("z2", 2, -500, "trusted", "", [], None, 0)])
    assert select_candidate(all_normal, exclude_keys=set()) is None   # 全判正常 → 该词跳过
    path = write_candidates_file(tmp_path, "上分", ranked, ranked[0], "triage")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["selected"] == "k3" and payload["strategy"] == "triage" and len(payload["candidates"]) == 4
