"""Keyword isolation uses confirmed media failures, never arbitrary exceptions."""
import json

import pytest

from test_triaged_search import FakeCrawler, FakeIngestion, FakeEngine, _new_pipeline, _base_request
from test_task_admission import stack, make_run, worker_for
from test_investigation_creation_m3 import FakeExecutionAdapter, FakeReportAdapter
from backend.audit_agent import pipeline as module
from backend.audit_agent.crawler_adapter import (
    CrawlerMediaDownloadError, CrawlerCollectionIncompleteError, CrawlerRateLimitError,
)
from backend.audit_agent.job_state import failure_metadata, available_job_actions
from backend.investigation_creation.contracts import RunStatus


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(module.settings, "triage_mode", "select")
    monkeypatch.setattr(module.job_store, "log", lambda *a, **kw: None)


def run(crawler, root, callback=None, stop=None):
    pipeline = _new_pipeline("media-test", crawler, FakeIngestion(set()), FakeEngine())
    return pipeline._run_triaged_search(
        request=_base_request(keyword="词A,词B"), save_root=root, start_page=1,
        max_total_notes=10, crawler_concurrency=1, account_auth_state=None,
        crawler_account_id="test", content_callback=callback, stream_items=True,
        stop_checker=stop or (lambda: False), started_callback=None, progress_callback=None,
    )


def crawler(errors=None, **kw):
    return FakeCrawler({"词A": [("a1", "上分")], "词B": [("b1", "上分")]},
                       detail_exceptions=errors, **kw)


def test_failed_keyword_then_success_and_resume_only_failed_keyword(tmp_path):
    first = crawler({"词A": CrawlerMediaDownloadError("media")})
    delivered = []
    result = run(first, tmp_path, lambda c, m: delivered.extend(c))
    assert [c["aweme_id"] for c in result.contents] == ["b1"]
    assert [c["aweme_id"] for c in delivered] == ["b1"]
    path = tmp_path / "keyword_failures.json"
    record = json.loads(path.read_text())
    assert list(record) == ["词A"] and record["词A"]["content_id"] == "a1"
    second = crawler(existing_rows=["b1"])
    result = run(second, tmp_path)
    assert [c["keyword"] for c in second.search_calls] == ["词A"]
    assert {c["aweme_id"] for c in result.contents} == {"a1", "b1"}
    assert json.loads(path.read_text()) == {}


def test_all_failed_words_are_attempted_and_remain_recoverable(tmp_path):
    source = crawler({word: CrawlerMediaDownloadError("media") for word in ("词A", "词B")})
    with pytest.raises(CrawlerMediaDownloadError, match="crawler_media_download_failed"):
        run(source, tmp_path)
    assert [c["keyword"] for c in source.search_calls] == ["词A", "词B"]
    assert set(json.loads((tmp_path / "keyword_failures.json").read_text())) == {"词A", "词B"}
    assert failure_metadata("crawler_media_download_failed")["recoverable"] is True


@pytest.mark.parametrize("error", [RuntimeError("database unavailable"),
    CrawlerCollectionIncompleteError("callback failed"), CrawlerRateLimitError("limited")])
def test_unclassified_integrity_and_global_errors_do_not_skip(tmp_path, error):
    source = crawler({"词A": error})
    with pytest.raises(type(error)):
        run(source, tmp_path)
    assert len(source.search_calls) == 1
    assert not (tmp_path / "keyword_failures.json").exists()


def test_callback_failure_does_not_continue_to_next_keyword(tmp_path):
    source = crawler()
    def callback(*_):
        raise RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database"):
        run(source, tmp_path, callback)
    assert len(source.search_calls) == 1


def test_candidate_stage_media_error_is_not_detail_skip(tmp_path):
    source = crawler()
    def search(**_):
        raise CrawlerMediaDownloadError("candidate stage")
    source.run_search = search
    with pytest.raises(CrawlerMediaDownloadError):
        run(source, tmp_path)
    assert not (tmp_path / "keyword_failures.json").exists()


def test_cancel_stops_without_next_keyword(tmp_path):
    source = crawler()
    cancelled = []
    run(source, tmp_path, lambda *_: cancelled.append(True), lambda: bool(cancelled))
    assert len(source.search_calls) == 1


def test_real_report_freezes_keyword_failure_coverage(tmp_path):
    from test_pass_report import seed_audit, R31ReportRuntime
    source, store = seed_audit(tmp_path)
    path = tmp_path / "outputs/new-search-task/crawler/keyword_failures.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"词A": {"content_id": "failed-post"}}))
    result = R31ReportRuntime(store).generate("new-search-task", source=source,
                                           checkpoint_path=tmp_path / "checkpoints.sqlite3")
    projection = store.get_presentation_projection(result.report_version_id)
    text = json.dumps(projection, ensure_ascii=False)
    assert "部分关键词采集失败" in text and "未完成关键词「词A」" in text
    assert "用户已结束后续采集" not in text
    snapshot = store.get_source_snapshot(result.report_version_id)
    path.write_text("{}")
    assert store.get_source_snapshot(result.report_version_id) == snapshot


def test_no_output_media_failure_holds_admission_without_empty_report(stack):
    creation, _, (owner, _) = stack
    task = make_run(creation, owner, "media-blocked")
    state = {**FakeExecutionAdapter.completed_state(ingested=0, completed=0),
             "status": "failed", "crawl_status": "failed", "analysis_status": "idle",
             "run_crawler": True, "error": "crawler_media_download_failed: no output",
             "control": {"failure": failure_metadata("crawler_media_download_failed")}}
    execution, reports = FakeExecutionAdapter(state), FakeReportAdapter()
    worker = worker_for(creation, execution, reports)
    worker.run_once()
    assert creation.get_run_for_worker(task.id).status == RunStatus.INTERRUPTED
    assert creation.admission.for_task(task.id)["state"] == "RESERVED"
    assert reports.generate_calls == 0
    actions = available_job_actions(state, state["task_stats"])
    assert actions["resume_crawl"] and not actions["finish_collection"]
