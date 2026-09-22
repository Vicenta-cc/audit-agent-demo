# tests/test_triaged_search.py
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import backend.audit_agent.pipeline as pipeline_module
from backend.audit_agent.crawler_adapter import CrawlOutput
from backend.audit_agent.triage import CandidateScore


class FakeCrawler:
    def __init__(self, candidates_by_word):
        self.candidates_by_word = candidates_by_word
        self.detail_calls = []
        self.search_sorts = []

    def run_search(self, *, platform, keyword, save_root, **kwargs):
        self.search_sorts.append(kwargs.get("search_sort"))
        items = [{"aweme_id": aid, "desc": desc, "source_keyword": keyword, "liked_count": "1"}
                 for aid, desc in self.candidates_by_word.get(keyword, [])]
        return CrawlOutput(platform=platform, contents=items, comments=[], output_dir=Path(save_root))

    def run_detail(self, platform, content_id, *, source_keyword, content_callback=None, save_root=None, **kwargs):
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
    def terms_for(self, category_ids):
        return []

    def score(self, content_key, rank, item, comments, terms):
        score = 300 if "上分" in item.get("desc", "") else 0
        return CandidateScore(content_key, rank, score, "rule" if score else "none", "", [], None, 0)


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
