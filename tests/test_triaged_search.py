# tests/test_triaged_search.py
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.audit_agent.pipeline as pipeline_module
from backend.audit_agent.crawler_adapter import (CrawlerCollectionIncompleteError,
                                                 CrawlerVerificationError, CrawlOutput,
                                                 MediaCrawlerAdapter)
from backend.audit_agent.triage import CandidateScore, mark_candidates_collected, write_candidates_file


class FakeCrawler:
    def __init__(self, candidates_by_word, detail_exceptions=None, comments_by_word=None,
                 detail_fail_once=None, detail_empty_once=None, existing_rows=()):
        self.candidates_by_word = candidates_by_word
        self.existing_rows = list(existing_rows)                   # 之前的采集已经落盘的 content_id
        self.detail_exceptions = detail_exceptions or {}
        self.comments_by_word = comments_by_word or {}
        self.detail_fail_once = dict(detail_fail_once or {})       # content_id -> exception, raised once
        self.detail_empty_once = set(detail_empty_once or set())   # content_id -> empty CrawlOutput once
        self.detail_calls = []
        self.search_sorts = []
        self.search_calls = []

    def run_search(self, *, platform, keyword, save_root, **kwargs):
        self.search_sorts.append(kwargs.get("search_sort"))
        self.search_calls.append({**kwargs, "keyword": keyword})
        items = [{"aweme_id": aid, "desc": desc, "source_keyword": keyword, "liked_count": "1"}
                 for aid, desc in self.candidates_by_word.get(keyword, [])]
        comments = self.comments_by_word.get(keyword, [])
        return CrawlOutput(platform=platform, contents=items, comments=comments, output_dir=Path(save_root))

    def run_detail(self, platform, content_id, *, source_keyword, content_callback=None, save_root=None,
                   progress_callback=None, **kwargs):
        if source_keyword in self.detail_exceptions:
            raise self.detail_exceptions[source_keyword]
        if content_id in self.detail_fail_once:
            exc = self.detail_fail_once.pop(content_id)
            raise exc
        if content_id in self.detail_empty_once:
            self.detail_empty_once.discard(content_id)
            return CrawlOutput(platform=platform, contents=[], comments=[], output_dir=Path(save_root))
        self.detail_calls.append((source_keyword, content_id))
        if progress_callback:
            progress_callback(1, 1)                 # 真实 run_detail 只会报 1/1
        item = {"aweme_id": content_id, "desc": "full", "source_keyword": source_keyword}
        if content_callback:
            content_callback([item], [])
        return CrawlOutput(platform=platform, contents=[item], comments=[], output_dir=Path(save_root),
                           command=["python", "main.py", "--specified_id", content_id])

    def _load_platform_output(self, save_root, platform):
        # 抖音 detail 模式写盘时 source_keyword 为空，重新读盘拿不到词：这里保持同样的诚实行为
        items = [{"aweme_id": cid, "source_keyword": ""} for cid in self.existing_rows]
        items += [{"aweme_id": cid, "source_keyword": ""} for _kw, cid in self.detail_calls]
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

    def score(self, content_key, rank, item, comments, terms, *, search_keyword=""):
        self.score_calls.append((content_key, comments, search_keyword))
        desc = item.get("desc", "")
        if "蓝V" in desc:       # 身份丢弃：真引擎在规则和模型之前就判掉
            return CandidateScore(content_key, rank, -1000, "discard", "蓝V认证账号（官方），不进精审", [], None, 0)
        score = 300 if "上分" in desc else 0
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
        request=request, save_root=tmp_path, start_page=1, max_total_notes=10, crawler_concurrency=1, account_auth_state=None,
        crawler_account_id="acc", content_callback=lambda c, m: streamed.extend(c), stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert pipeline.crawler.detail_calls == [("词A", "a2"), ("词B", "b1")]
    assert ("词F", "f1") not in pipeline.crawler.detail_calls        # 重复后只剩正常候选 → 跳过（R9）
    assert set(pipeline.crawler.search_sorts) == {"latest"}   # 沿用同事的排序设置（R10）
    assert [c["aweme_id"] for c in output.contents] == ["a2", "b1"]
    # 重新读盘的行没有 source_keyword，应用侧必须按选中它的词补回（R7/§5）
    assert [(c["aweme_id"], c["source_keyword"]) for c in output.contents] == [("a2", "词A"), ("b1", "词B")]
    assert [c["aweme_id"] for c in streamed] == ["a2", "b1"]
    payload = json.loads((tmp_path / "candidates" / "02-词B" / "candidates.json").read_text(encoding="utf-8"))
    assert payload["selected"] == "b1"
    assert {c["content_key"] for c in payload["candidates"]} == {"a2", "b1"}


def test_task_content_budget_stops_the_sweep_and_names_the_unsearched_keywords(tmp_path: Path, monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda job_id, message, *a, **k: logs.append(message))
    crawler = FakeCrawler({
        "词A": [("a1", "今晚上分")],
        "词B": [("b1", "今晚上分")],
        "词C": [("c1", "今晚上分")],
    })
    pipeline = _new_pipeline("job-budget", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    output = pipeline._run_triaged_search(
        request=_base_request(keyword="词A,词B,词C"), save_root=tmp_path, start_page=1, max_total_notes=2,
        crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    # 本任务上限 2 条：第三个词连候选搜索都不该发生（R8）
    assert len(crawler.search_calls) == 2
    assert crawler.detail_calls == [("词A", "a1"), ("词B", "b1")]
    assert [c["aweme_id"] for c in output.contents] == ["a1", "b1"]
    budget_logs = [message for message in logs if "采集上限" in message]
    assert len(budget_logs) == 1 and "词C" in budget_logs[0]
    assert not (tmp_path / "candidates" / "03-词C").exists()


def test_skip_log_counts_the_candidates_dropped_by_identity(tmp_path: Path, monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda job_id, message, *a, **k: logs.append(message))
    crawler = FakeCrawler({"词H": [("h1", "蓝V反诈科普"), ("h2", "蓝V辟谣"), ("h3", "普通")]})
    pipeline = _new_pipeline("job-discard", crawler, FakeIngestion(analyzed=set()), FakeEngine())

    pipeline._run_triaged_search(
        request=_base_request(keyword="词H"), save_root=tmp_path, start_page=1, max_total_notes=10,
        crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )

    assert crawler.detail_calls == []       # 两条蓝V被丢弃，剩下的判正常 → 该词跳过
    skipped = [message for message in logs if "跳过" in message]
    assert len(skipped) == 1
    assert "候选 3 条，可疑 0 条，身份丢弃 2 条，排除重复或已审 0 条" in skipped[0]
    payload = json.loads((tmp_path / "candidates" / "01-词H" / "candidates.json").read_text(encoding="utf-8"))
    assert [c["band"] for c in payload["candidates"]] == ["none", "discard", "discard"]


def test_compare_mode_rank1_arm_bypasses_score_gate_but_triage_arm_prefers_score(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "compare")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    request = _base_request(keyword="词G")
    kwargs = dict(
        request=request, start_page=1, max_total_notes=10, crawler_concurrency=1, account_auth_state=None,
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
        request=_base_request(keyword="词A,词B"), save_root=tmp_path, start_page=1, max_total_notes=10, crawler_concurrency=1,
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
            max_total_notes=10, crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
            content_callback=lambda c, m: None, stream_items=True,
            stop_checker=lambda: False, started_callback=None, progress_callback=None,
        )

    # 精采不完整与 TRIAGE_MODE=off 一致：整个任务失败，而不是把已入库的半条内容当作"本词无产出"
    crawler_incomplete = FakeCrawler(
        {"词A": [("a1", "今晚上分")], "词B": [("b1", "今晚上分")]},
        detail_exceptions={"词A": CrawlerCollectionIncompleteError("douyin/collection_status")},
    )
    pipeline_incomplete = _new_pipeline("job-incomplete", crawler_incomplete, FakeIngestion(analyzed=set()), FakeEngine())
    with pytest.raises(CrawlerCollectionIncompleteError):
        pipeline_incomplete._run_triaged_search(
            request=_base_request(keyword="词A,词B"), save_root=tmp_path / "incomplete", start_page=1,
            max_total_notes=10, crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
            content_callback=lambda c, m: None, stream_items=True,
            stop_checker=lambda: False, started_callback=None, progress_callback=None,
        )
    assert crawler_incomplete.detail_calls == []        # 后面的词不再继续


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
        request=_base_request(keyword="词A"), save_root=tmp_path, start_page=1, max_total_notes=10, crawler_concurrency=1,
        account_auth_state=None, crawler_account_id="acc", content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    [(key, comments, search_keyword)] = engine.score_calls
    assert key == "a1"
    assert [c["content"] for c in comments] == ["有暗号"]
    assert search_keyword == "词A"      # 规则层要认得本次搜索词，才能不给自命中计分


def test_run_search_contract_for_candidate_sweep(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    crawler = FakeCrawler({"词A": [("a1", "今晚上分")]})
    pipeline = _new_pipeline("job-contract", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    pipeline._run_triaged_search(
        request=_base_request(keyword="词A"), save_root=tmp_path, start_page=1, max_total_notes=10, crawler_concurrency=1,
        account_auth_state=None, crawler_account_id="acc", content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    [call_kwargs] = crawler.search_calls
    assert call_kwargs["collect_media"] is False
    assert call_kwargs["collect_comments"] is True
    assert call_kwargs["stream_items"] is False
    assert call_kwargs["max_notes"] == call_kwargs["max_total_notes"] == 10
    assert "content_callback" not in call_kwargs


def test_selected_keys_only_recorded_after_successful_precise_collection(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)

    # run_detail raises for 词A's pick (shared1); since the exception happens before
    # selected_keys is populated, 词B can still pick and successfully collect shared1.
    crawler_fail = FakeCrawler(
        {"词A": [("shared1", "今晚上分")], "词B": [("shared1", "今晚上分")]},
        detail_fail_once={"shared1": RuntimeError("boom")},
    )
    pipeline_fail = _new_pipeline("job-fail-once", crawler_fail, FakeIngestion(analyzed=set()), FakeEngine())
    output_fail = pipeline_fail._run_triaged_search(
        request=_base_request(keyword="词A,词B"), save_root=tmp_path / "fail", start_page=1,
        max_total_notes=10, crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert crawler_fail.detail_calls == [("词B", "shared1")]
    assert [c["aweme_id"] for c in output_fail.contents] == ["shared1"]
    payload_b = json.loads((tmp_path / "fail" / "candidates" / "02-词B" / "candidates.json").read_text(encoding="utf-8"))
    assert payload_b["selected"] == "shared1"

    # run_detail returns an empty CrawlOutput for 词A's pick (shared2); since contents
    # is empty, selected_keys is not populated and 词B can still pick and collect it.
    crawler_empty = FakeCrawler(
        {"词A": [("shared2", "今晚上分")], "词B": [("shared2", "今晚上分")]},
        detail_empty_once={"shared2"},
    )
    pipeline_empty = _new_pipeline("job-empty-once", crawler_empty, FakeIngestion(analyzed=set()), FakeEngine())
    output_empty = pipeline_empty._run_triaged_search(
        request=_base_request(keyword="词A,词B"), save_root=tmp_path / "empty", start_page=1,
        max_total_notes=10, crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert crawler_empty.detail_calls == [("词B", "shared2")]        # 词A的空结果不计入成功采集
    assert [c["aweme_id"] for c in output_empty.contents] == ["shared2"]


def _collected_candidates(directory: Path, keyword: str, content_key: str) -> None:
    """之前的采集留下的证据：选中并已精采成功。"""
    write_candidates_file(directory, keyword, [CandidateScore(content_key, 1, 300, "rule", "命中", [], None, 0)],
                          CandidateScore(content_key, 1, 300, "rule", "命中", [], None, 0), "triage")
    mark_candidates_collected(directory)


def test_resumed_sweep_skips_collected_words_and_counts_them_against_the_budget(tmp_path: Path, monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda job_id, message, *a, **k: logs.append(message))
    crawl_dir = tmp_path / "crawler"
    _collected_candidates(crawl_dir / "candidates" / "01-词A", "词A", "a1")
    crawler = FakeCrawler(
        {"词A": [("a1", "今晚上分")], "词B": [("a1", "今晚上分"), ("b1", "今晚上分")], "词C": [("c1", "今晚上分")]},
        existing_rows=["a1"],
    )
    pipeline = _new_pipeline("job-resume", crawler, FakeIngestion(analyzed={"a1"}), FakeEngine())
    output = pipeline._run_triaged_search(
        request=_base_request(keyword="词A,词B,词C"), save_root=crawl_dir, start_page=1, max_total_notes=2,
        crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert [call["keyword"] for call in crawler.search_calls] == ["词B"]    # 词A 已采，词C 超预算
    assert crawler.detail_calls == [("词B", "b1")]                          # a1 仍然被当作重复排除
    assert [(c["aweme_id"], c["source_keyword"]) for c in output.contents] == [("a1", "词A"), ("b1", "词B")]
    assert any("恢复采集：1 个词" in message for message in logs)
    assert any("词「词A」已选定 a1" in message for message in logs)
    assert [message for message in logs if "采集上限" in message] == [
        "已达本任务采集上限 2 条，以下词未搜索：词C"]


def test_rotated_sweep_reads_collected_markers_from_both_account_directories(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    crawl_dir = tmp_path / "crawler"
    _collected_candidates(crawl_dir / "candidates" / "01-词A", "词A", "a1")                      # 上一个账号采的
    _collected_candidates(crawl_dir / "rotation-x" / "candidates" / "02-词B", "词B", "b1")        # 本轮换目录里的
    crawler = FakeCrawler({"词A": [("a1", "今晚上分")], "词B": [("b1", "今晚上分")], "词C": [("c1", "今晚上分")]})
    pipeline = _new_pipeline("job-rotate", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    output = pipeline._run_triaged_search(
        request=_base_request(keyword="词A,词B,词C"), save_root=crawl_dir / "rotation-x", start_page=1,
        max_total_notes=10, crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc2",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert [call["keyword"] for call in crawler.search_calls] == ["词C"]
    assert crawler.detail_calls == [("词C", "c1")]
    assert [(c["aweme_id"], c["source_keyword"]) for c in output.contents] == [("c1", "词C")]


def test_sweep_reports_keyword_progress_command_and_visited_count(tmp_path: Path, monkeypatch):
    logs: list[str] = []
    progress: list[tuple[int, int]] = []
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda job_id, message, *a, **k: logs.append(message))
    crawler = FakeCrawler({"词A": [("a1", "今晚上分")], "词B": [("b1", "今晚上分")], "词C": [("c1", "今晚上分")]})
    pipeline = _new_pipeline("job-progress", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    output = pipeline._run_triaged_search(
        request=_base_request(keyword="词A,词B,词C"), save_root=tmp_path, start_page=1, max_total_notes=10,
        crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: len(crawler.detail_calls) >= 2,      # 采到两条后要求停止
        started_callback=None, progress_callback=lambda done, total: progress.append((done, total)),
    )
    assert progress == [(1, 3), (2, 3)]                            # 外层看到的是第几个词，不是 run_detail 的 1/1
    assert output.command == ["python", "main.py", "--specified_id", "b1"]
    assert any("初筛完成：2 个词，选中 2 条，本次精采 2 条进入精审" in message for message in logs)   # 停止后的词不计入


def test_sweep_without_any_detail_run_still_reports_a_command(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    crawler = FakeCrawler({"词A": []})
    pipeline = _new_pipeline("job-nocmd", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    output = pipeline._run_triaged_search(
        request=_base_request(keyword="词A"), save_root=tmp_path, start_page=1, max_total_notes=10,
        crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert output.command == ["triage-select", "1", "keywords"]


def test_zero_keywords_fails_like_run_search(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    crawler = FakeCrawler({})
    pipeline = _new_pipeline("job-empty-keyword", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    with pytest.raises(ValueError, match="at least one search keyword is required"):
        pipeline._run_triaged_search(
            request=_base_request(keyword=" , "), save_root=tmp_path, start_page=1, max_total_notes=10,
            crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
            content_callback=lambda c, m: None, stream_items=True,
            stop_checker=lambda: False, started_callback=None, progress_callback=None,
        )
    assert crawler.search_calls == []


class RealOutputCrawler(FakeCrawler):
    """搜索与精采仍然是假的，最后一步读盘用真的 _load_platform_output。"""

    def _load_platform_output(self, save_root, platform):
        adapter = MediaCrawlerAdapter.__new__(MediaCrawlerAdapter)
        return adapter._load_platform_output(Path(save_root), platform)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_candidate_rows_never_reach_the_returned_output(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_candidates_per_keyword", 10)
    monkeypatch.setattr(pipeline_module.settings, "triage_candidate_comments", 60)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    # 精采落在正常目录，9 条未选中的候选落在 candidates 子树里（R6）
    _write_jsonl(tmp_path / "douyin" / "jsonl" / "search_contents_2026-09-22.jsonl",
                 [{"aweme_id": "top1", "desc": "今晚上分", "source_keyword": ""}])
    _write_jsonl(tmp_path / "candidates" / "01-词A" / "douyin" / "jsonl" / "search_contents_2026-09-22.jsonl",
                 [{"aweme_id": "cand9", "desc": "候选", "source_keyword": "词A"}])
    crawler = RealOutputCrawler({"词A": [("top1", "今晚上分"), ("cand9", "候选")]})
    pipeline = _new_pipeline("job-r6", crawler, FakeIngestion(analyzed=set()), FakeEngine())
    output = pipeline._run_triaged_search(
        request=_base_request(keyword="词A"), save_root=tmp_path, start_page=1, max_total_notes=10,
        crawler_concurrency=1, account_auth_state=None, crawler_account_id="acc",
        content_callback=lambda c, m: None, stream_items=True,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )
    assert crawler.detail_calls == [("词A", "top1")]
    assert [c["aweme_id"] for c in output.contents] == ["top1"]          # 候选子树对读盘不可见
    assert output.contents[0]["source_keyword"] == "词A"                 # 读盘行由应用侧补回词
    # 候选行确实是一条能被读出来的行，只是 save_root 那一层读不到它
    candidates_output = MediaCrawlerAdapter.__new__(MediaCrawlerAdapter)._load_platform_output(
        tmp_path / "candidates" / "01-词A", "dy")
    assert [c["aweme_id"] for c in candidates_output.contents] == ["cand9"]


def _drive_crawl_dispatch(tmp_path: Path, monkeypatch, mode: str) -> list[str]:
    """跑真正的 run()，在采集调用处用哨兵异常停下来，只看它走了哪个分支。"""
    from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
    from backend.audit_agent.job_store import JobStore
    from backend.audit_agent.requests import CrawlRequest

    calls: list[str] = []
    job_id = f"job-dispatch-{mode}"
    store = JobStore(tmp_path / f"jobs-{mode}.sqlite3")
    store.create(job_id=job_id, platform="dy", start_page=0, max_notes=1)
    monkeypatch.setattr(pipeline_module, "job_store", store)
    monkeypatch.setattr(pipeline_module, "crawler_account_store",
                        SimpleNamespace(available_accounts=lambda platform: []))
    monkeypatch.setattr(pipeline_module.settings, "app_auth_mode", "off")
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", tmp_path / "outputs")
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", mode)

    class DispatchCrawler:
        def run_search(self, **kwargs):
            calls.append("run_search")
            raise RuntimeError("stop-after-dispatch")

        def run_creator(self, **kwargs):
            calls.append("run_creator")
            raise RuntimeError("stop-after-dispatch")

    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = job_id
    pipeline.crawler = DispatchCrawler()
    pipeline.ingestion = IngestionStore(tmp_path / f"audit-{mode}.sqlite3")
    pipeline.audit_results = AuditResultStore(tmp_path / f"audit-{mode}.sqlite3")
    pipeline.prompt_set = None
    pipeline.prompt_profile_snapshot = {}
    pipeline.rule_snapshot = {}
    pipeline.audit_config_revision_id = ""
    pipeline.authoritative_m3 = False
    pipeline._m3_snapshot_validator = lambda: None

    def fake_triaged(**kwargs):
        calls.append("_run_triaged_search")
        raise RuntimeError("stop-after-dispatch")

    pipeline._run_triaged_search = fake_triaged
    pipeline.run(CrawlRequest(platform="dy", keyword="词A,词B", crawl_mode="search", max_notes=1,
                              max_total_notes=2, max_comments=10, collect_media=False,
                              auto_analyze=False, analyze_limit=0, run_crawler=True, search_sort="latest"))
    failure = (store.get(job_id) or {}).get("control", {}).get("failure") or {}
    assert failure.get("code") == "stop-after-dispatch"      # 确实跑到了采集分支才停
    return calls


def test_off_mode_still_uses_run_search_and_select_mode_uses_the_sweep(tmp_path: Path, monkeypatch):
    assert _drive_crawl_dispatch(tmp_path, monkeypatch, "off") == ["run_search"]
    assert _drive_crawl_dispatch(tmp_path, monkeypatch, "select") == ["_run_triaged_search"]
    assert _drive_crawl_dispatch(tmp_path, monkeypatch, "compare") == ["_run_triaged_search"]
