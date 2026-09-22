# tests/test_triaged_search.py
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.audit_agent.pipeline as pipeline_module
from backend.audit_agent.crawler_adapter import CrawlerVerificationError, CrawlOutput
from backend.audit_agent.triage import CandidateScore


class FakeCrawler:
    def __init__(self, candidates_by_word, detail_exceptions=None, comments_by_word=None):
        self.candidates_by_word = candidates_by_word
        self.detail_exceptions = detail_exceptions or {}
        self.comments_by_word = comments_by_word or {}
        self.detail_calls = []
        self.search_sorts = []
        self.search_calls = []

    def run_search(self, *, platform, keyword, save_root, **kwargs):
        self.search_sorts.append(kwargs.get("search_sort"))
        self.search_calls.append(kwargs)
        items = [{"aweme_id": aid, "desc": desc, "source_keyword": keyword, "liked_count": "1"}
                 for aid, desc in self.candidates_by_word.get(keyword, [])]
        comments = self.comments_by_word.get(keyword, [])
        return CrawlOutput(platform=platform, contents=items, comments=comments, output_dir=Path(save_root))

    def run_detail(self, platform, content_id, *, source_keyword, content_callback=None, save_root=None, **kwargs):
        if source_keyword in self.detail_exceptions:
            raise self.detail_exceptions[source_keyword]
        self.detail_calls.append((source_keyword, content_id))
        item = {"aweme_id": content_id, "desc": "full", "source_keyword": source_keyword}
        if content_callback:
            content_callback([item], [])
        return CrawlOutput(platform=platform, contents=[item], comments=[], output_dir=Path(save_root))

    def _load_platform_output(self, save_root, platform):
        items = [{"aweme_id": cid, "source_keyword": kw} for kw, cid in self.detail_calls]
        return CrawlOutput(platform=platform, contents=items, comments=[], output_dir=Path(save_root))


class FakeIngestion:
    def __init__(self, analyzed):
        self.analyzed = set(analyzed)
        self.db_path = Path("/tmp/unused.sqlite3")

    def analyzed_content_keys(self, platform, keys):
        return {k for k in keys if k in self.analyzed}


class FakeEngine:
    def __init__(self):
        self.score_calls = []

    def terms_for(self, category_ids):
        return []

    def score(self, content_key, rank, item, comments, terms):
        self.score_calls.append((content_key, comments))
        score = 300 if "上分" in item.get("desc", "") else 0
        return CandidateScore(content_key, rank, score, "rule" if score else "none", "", [], None, 0)


def _base_request(**overrides):
    defaults = dict(platform="dy", keyword="词A", lexicon_category="", keyword_source="keyword",
                     max_comments=300, get_sub_comment=False, collect_comments=True, collect_media=True,
                     max_items_per_minute=5, search_sort="latest")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _new_pipeline(job_id, crawler, ingestion, engine):
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = job_id
    pipeline.crawler = crawler
    pipeline.ingestion = ingestion
    pipeline._triage_engine = engine
    return pipeline


def test_selects_one_per_keyword_skipping_duplicates_and_analyzed(tmp_path: Path, monkeypatch):
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = "job-t"
    pipeline.crawler = FakeCrawler({
        "词A": [("a1", "普通"), ("a2", "今晚上分"), ("a3", "普通")],
        "词B": [("a2", "今晚上分"), ("b1", "上分群")],        # a2 与词A重复 → 取次优且可疑的 b1
        "词C": [("c1", "上分已审")],                          # c1 别的任务审过 → 该词跳过
        "词D": [],                                            # 搜不到 → 跳过
        "词E": [("e1", "普通"), ("e2", "普通")],               # 全判正常 → 换下一个词
        "词F": [("a2", "今晚上分"), ("f1", "普通")],           # 重复后只剩正常候选 → 跳过（R9）
    })
    pipeline.ingestion = FakeIngestion(analyzed={"c1"})
    pipeline._triage_engine = FakeEngine()
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    request = SimpleNamespace(platform="dy", keyword="词A,词B,词C,词D,词E,词F", lexicon_category="gambling", keyword_source="lexicon",
                              max_comments=300, get_sub_comment=False, collect_comments=True, collect_media=True,
                              max_items_per_minute=5, search_sort="latest")
    streamed = []
    output = pipeline._run_triaged_search(
        request=request, save_root=tmp_path, start_page=1, crawler_concurrency=1, account_auth_state=None,
        crawler_account_id="acc", content_callback=lambda c, m: streamed.extend(c), stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert pipeline.crawler.detail_calls == [("词A", "a2"), ("词B", "b1")]
    assert ("词F", "f1") not in pipeline.crawler.detail_calls        # 重复后只剩正常候选 → 跳过（R9）
    assert set(pipeline.crawler.search_sorts) == {"latest"}   # 沿用同事的排序设置（R10）
    assert [c["aweme_id"] for c in output.contents] == ["a2", "b1"]
    assert [c["aweme_id"] for c in streamed] == ["a2", "b1"]
    payload = json.loads((tmp_path / "candidates" / "02-词B" / "candidates.json").read_text(encoding="utf-8"))
    assert payload["selected"] == "b1"
    assert {c["content_key"] for c in payload["candidates"]} == {"a2", "b1"}


def test_compare_mode_rank1_arm_bypasses_score_gate_but_triage_arm_prefers_score(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "compare")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    request = _base_request(keyword="词G")
    kwargs = dict(
        request=request, start_page=1, crawler_concurrency=1, account_auth_state=None,
        crawler_account_id="acc", content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )

    # random.random() < 0.5 forces the rank1 arm: must take the top-search-ranked
    # candidate (g1, score 0) regardless of the score gate.
    monkeypatch.setattr(pipeline_module.random, "random", lambda: 0.1)
    crawler_rank1 = FakeCrawler({"词G": [("g1", "普通"), ("g2", "今晚上分")]})
    pipeline_rank1 = _new_pipeline("job-rank1", crawler_rank1, FakeIngestion(analyzed=set()), FakeEngine())
    root_rank1 = tmp_path / "rank1"
    pipeline_rank1._run_triaged_search(save_root=root_rank1, **kwargs)
    assert crawler_rank1.detail_calls == [("词G", "g1")]
    payload_rank1 = json.loads((root_rank1 / "candidates" / "01-词G" / "candidates.json").read_text(encoding="utf-8"))
    assert payload_rank1["strategy"] == "rank1" and payload_rank1["selected"] == "g1"

    # random.random() >= 0.5 keeps the triage arm: must take the higher-scoring
    # candidate (g2, score 300) via the normal score gate.
    monkeypatch.setattr(pipeline_module.random, "random", lambda: 0.9)
    crawler_triage = FakeCrawler({"词G": [("g1", "普通"), ("g2", "今晚上分")]})
    pipeline_triage = _new_pipeline("job-triage", crawler_triage, FakeIngestion(analyzed=set()), FakeEngine())
    root_triage = tmp_path / "triage"
    pipeline_triage._run_triaged_search(save_root=root_triage, **kwargs)
    assert crawler_triage.detail_calls == [("词G", "g2")]
    payload_triage = json.loads((root_triage / "candidates" / "01-词G" / "candidates.json").read_text(encoding="utf-8"))
    assert payload_triage["strategy"] == "triage" and payload_triage["selected"] == "g2"


def test_per_keyword_exception_is_isolated_but_account_errors_propagate(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)

    # A generic exception on 词A must not abort the sweep; 词B still gets processed.
    crawler = FakeCrawler(
        {"词A": [("a1", "今晚上分")], "词B": [("b1", "今晚上分")]},
        detail_exceptions={"词A": RuntimeError("boom")},
    )
    pipeline = _new_pipeline("job-isolate", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    output = pipeline._run_triaged_search(
        request=_base_request(keyword="词A,词B"), save_root=tmp_path, start_page=1, crawler_concurrency=1,
        account_auth_state=None, crawler_account_id="acc", content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert crawler.detail_calls == [("词B", "b1")]
    assert [c["aweme_id"] for c in output.contents] == ["b1"]

    # An account-level error must propagate out, not be swallowed.
    crawler_verify = FakeCrawler(
        {"词A": [("a1", "今晚上分")]},
        detail_exceptions={"词A": CrawlerVerificationError("verify")},
    )
    pipeline_verify = _new_pipeline("job-verify", crawler_verify, FakeIngestion(analyzed=set()), FakeEngine())
    with pytest.raises(CrawlerVerificationError):
        pipeline_verify._run_triaged_search(
            request=_base_request(keyword="词A"), save_root=tmp_path / "verify", start_page=1,
            crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
            content_callback=lambda c, m: None, stream_items=True,
            stop_checker=lambda: False, started_callback=None, progress_callback=None,
        )


def test_comments_grouped_by_platform_aware_content_identity(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    crawler = FakeCrawler(
        {"词A": [("a1", "今晚上分")]},
        comments_by_word={"词A": [
            {"aweme_id": "a1", "content": "有暗号"},
            {"aweme_id": "other", "content": "无关"},
        ]},
    )
    engine = FakeEngine()
    pipeline = _new_pipeline("job-comments", crawler, FakeIngestion(analyzed=set()), engine)
    pipeline._run_triaged_search(
        request=_base_request(keyword="词A"), save_root=tmp_path, start_page=1, crawler_concurrency=1,
        account_auth_state=None, crawler_account_id="acc", content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    [(key, comments)] = engine.score_calls
    assert key == "a1"
    assert [c["content"] for c in comments] == ["有暗号"]


def test_run_search_contract_for_candidate_sweep(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    crawler = FakeCrawler({"词A": [("a1", "今晚上分")]})
    pipeline = _new_pipeline("job-contract", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    pipeline._run_triaged_search(
        request=_base_request(keyword="词A"), save_root=tmp_path, start_page=1, crawler_concurrency=1,
        account_auth_state=None, crawler_account_id="acc", content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    [call_kwargs] = crawler.search_calls
    assert call_kwargs["collect_media"] is False
    assert call_kwargs["collect_comments"] is True
    assert call_kwargs["stream_items"] is False
    assert call_kwargs["max_notes"] == call_kwargs["max_total_notes"] == 10
    assert "content_callback" not in call_kwargs
