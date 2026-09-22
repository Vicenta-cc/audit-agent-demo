from backend.audit_agent.triage_matcher import TriageTerm
from scripts.douyin_query_correct_ab import compare_runs


def test_compare_runs_counts_overlap_and_rule_hits():
    terms = [TriageTerm("上分", "模糊", "gambling", "高")]
    on = [{"aweme_id": "1", "desc": "上班日常"}, {"aweme_id": "2", "desc": "普通"}]
    off = [{"aweme_id": "2", "desc": "普通"}, {"aweme_id": "3", "desc": "今晚上分"}]
    report = compare_runs(on, off, terms)
    assert report["on_count"] == 2 and report["off_count"] == 2
    assert report["overlap"] == 1 and report["only_on"] == 1 and report["only_off"] == 1
    assert report["on_rule_hits"] == 0 and report["off_rule_hits"] == 1
