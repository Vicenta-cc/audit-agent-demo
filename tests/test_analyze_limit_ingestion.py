from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="analyze-limit-contract-", dir="/tmp"))
os.environ["XHS_AUDIT_DATA_DIR"] = str(_RUNTIME_ROOT / "data")
os.environ["XHS_AUDIT_OUTPUTS_DIR"] = str(_RUNTIME_ROOT / "outputs")
os.environ["PYTHONPYCACHEPREFIX"] = str(_RUNTIME_ROOT / "pycache")
os.environ["TMPDIR"] = str(_RUNTIME_ROOT / "tmp")
os.environ["HERMES_HOME"] = str(_RUNTIME_ROOT / "hermes")
(_RUNTIME_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
atexit.register(shutil.rmtree, _RUNTIME_ROOT, True)

import backend.audit_agent.pipeline as pipeline_module
from backend.audit_agent.crawler_adapter import CrawlOutput, MediaCrawlerAdapter
from backend.audit_agent.ingestion import (
    AuditResultStore,
    IngestionStore,
    SelectedContentPayloadUnavailableError,
)
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.pipeline import AuditPipeline
from backend.reporting.integration_source import CanonicalReportSource


class CandidateCrawler:
    def __init__(
        self,
        callback_contents: list[dict],
        final_contents: list[dict] | None = None,
    ) -> None:
        self.callback_contents = [dict(item) for item in callback_contents]
        self.final_contents = [
            dict(item) for item in (final_contents or callback_contents)
        ]
        self.calls: list[dict] = []

    def _run(self, mode: str, **kwargs) -> CrawlOutput:
        self.calls.append({"mode": mode, **kwargs})
        if kwargs.get("started_callback"):
            kwargs["started_callback"]()
        callback = kwargs.get("content_callback")
        if callback:
            for item in self.callback_contents:
                callback([dict(item)], [])
        return CrawlOutput(
            platform=kwargs["platform"],
            contents=[dict(item) for item in self.final_contents],
            comments=[],
            output_dir=kwargs["save_root"],
            command=["fake-crawler", mode],
        )

    def run_search(self, **kwargs) -> CrawlOutput:
        return self._run("search", **kwargs)

    def run_creator(self, **kwargs) -> CrawlOutput:
        return self._run("creator", **kwargs)


def test_mediacrawler_jsonl_keeps_multiple_candidates_in_file_order(
    tmp_path: Path,
) -> None:
    jsonl_dir = tmp_path / "crawler" / "xhs" / "jsonl"
    jsonl_dir.mkdir(parents=True)
    candidates = [
        {"note_id": f"raw-note-{index}", "title": f"candidate {index}"}
        for index in range(1, 11)
    ]
    (jsonl_dir / "search_contents_2026-09-01.jsonl").write_text(
        "\n".join(["not-json", *(json.dumps(item) for item in candidates)]),
        encoding="utf-8",
    )

    output = MediaCrawlerAdapter(tmp_path / "unused-mediacrawler").load_latest_output(
        tmp_path / "crawler", "xhs"
    )

    assert [item["note_id"] for item in output.contents] == [
        f"raw-note-{index}" for index in range(1, 11)
    ]


def _configuration(*, mode: str, analyze_limit: int) -> dict:
    creator_url = (
        "https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753"
        if mode == "creator"
        else ""
    )
    return {
        "platform": "xhs",
        "display_name": "Analyze limit contract",
        "crawl_mode": mode,
        "keyword": "candidate" if mode == "search" else "",
        "keyword_source": "keyword",
        "lexicon_category": "soft",
        "library_ids": ["soft"],
        "capabilities": ["text"],
        "scoring_template": "balanced",
        "rule_snapshot": {},
        "lexicon_keywords": [],
        "creator_url": creator_url,
        "creator_id": creator_url,
        "start_page": 1,
        "max_notes": 1,
        "max_comments": 0,
        "max_concurrency": 1,
        "max_items_per_minute": 1,
        "crawler_account_id": None,
        "get_sub_comment": False,
        "analyze_limit": analyze_limit,
        "run_crawler": True,
        "source_output_id": None,
        "analysis_batch_size": 1,
        "prompt_profile_snapshot": {},
        "policy_id": "",
        "_confirmed_analyze_limit": analyze_limit,
    }


def _run_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    job_id: str,
    crawler: CandidateCrawler,
    mode: str = "search",
    analyze_limit: int = 1,
) -> tuple[JobStore, IngestionStore, list[str]]:
    audit_db = tmp_path / "audit.sqlite3"
    outputs_dir = tmp_path / "outputs"
    jobs = JobStore(audit_db)
    ingestion = IngestionStore(audit_db)
    configuration = _configuration(mode=mode, analyze_limit=analyze_limit)
    if jobs.get(job_id) is None:
        jobs.create(
            job_id=job_id,
            **{
                key: value
                for key, value in configuration.items()
                if not key.startswith("_")
            },
        )

    analyzed: list[str] = []
    monkeypatch.setattr(pipeline_module, "job_store", jobs)
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", outputs_dir)
    monkeypatch.setattr(
        pipeline_module.settings, "auto_analyze_crawled_content", True
    )

    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = job_id
    pipeline.crawler = crawler
    pipeline.ingestion = ingestion
    pipeline.audit_results = object()
    pipeline.prompt_profile_snapshot = {}
    pipeline.audit_config_revision_id = ""
    pipeline.rule_snapshot = {}

    def analyze(subject):
        analyzed.append(subject.note_id)
        return {
            "note_id": subject.note_id,
            "url": subject.url,
            "title": subject.title,
            "decision": "pass",
            "evidence_items": [{"source_identity": subject.note_id}],
        }

    pipeline._analyze_subject = analyze
    pipeline._persist_audit_result = lambda **kwargs: {
        **kwargs["result"],
        "content_key": kwargs["content_key"],
    }
    pipeline.run(SimpleNamespace(**configuration))
    assert pipeline.authoritative_m3 is False
    return jobs, ingestion, analyzed


@pytest.mark.parametrize("mode", ["search", "creator"])
def test_ten_raw_candidates_select_only_first_for_sql_analysis_and_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    candidates = [
        {"note_id": f"note-{index}", "title": f"candidate {index}"}
        for index in range(1, 11)
    ]
    crawler = CandidateCrawler(candidates, list(reversed(candidates)))

    jobs, ingestion, analyzed = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id=f"limit-one-{mode}",
        crawler=crawler,
        mode=mode,
        analyze_limit=1,
    )

    stats = ingestion.stats_for_task(f"limit-one-{mode}")
    refs = ingestion.refs_for_task(f"limit-one-{mode}")
    items = jobs.get(f"limit-one-{mode}")["items"]
    assert stats["ingested_count"] == 1
    assert stats["pending_analysis_count"] == 0
    assert stats["completed_analysis_count"] == 1
    assert [ref["content_key"] for ref in refs] == ["note-1"]
    assert analyzed == ["note-1"]
    assert [item["content_key"] for item in items] == ["note-1"]
    assert items[0]["evidence_items"] == [{"source_identity": "note-1"}]
    assert crawler.calls[0]["mode"] == mode


def test_analyze_limit_two_skips_invalid_and_duplicate_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callback_contents = [
        {"title": "missing identity"},
        {"note_id": " "},
        {"note_id": "note-2", "title": "second"},
        {"note_id": "note-2", "title": "duplicate"},
        {"note_id": "note-3", "title": "third"},
        {"note_id": "note-4", "title": "fourth"},
    ]

    jobs, ingestion, analyzed = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id="limit-two",
        crawler=CandidateCrawler(callback_contents),
        analyze_limit=2,
    )

    assert ingestion.stats_for_task("limit-two")["ingested_count"] == 2
    assert [ref["content_key"] for ref in ingestion.refs_for_task("limit-two")] == [
        "note-2",
        "note-3",
    ]
    assert analyzed == ["note-2", "note-3"]
    assert len(jobs.get("limit-two")["items"]) == 2


def test_report_source_and_evidence_exclude_unselected_audit_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [
        {"note_id": f"report-note-{index}", "title": f"candidate {index}"}
        for index in range(1, 11)
    ]
    _, ingestion, _ = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id="report-scope",
        crawler=CandidateCrawler(candidates),
        analyze_limit=1,
    )
    selected_ref = ingestion.refs_for_task("report-scope")[0]
    results = AuditResultStore(ingestion.db_path)
    selected_result = results.upsert_result(
        job_id="report-scope",
        platform="xhs",
        content_key="report-note-1",
        content_id=selected_ref["content_id"],
        result={
            "note_id": "report-note-1",
            "decision": "pass",
            "risk_level": "none",
            "evidence_items": [
                {
                    "evidence_id": "text:desc",
                    "primary_modality": "text",
                    "source": "desc",
                    "text": "selected evidence",
                }
            ],
        },
    )
    ingestion.mark_content_status(
        "xhs",
        "report-note-1",
        "completed",
        task_id="report-scope",
        audit_result_id=int(selected_result["id"]),
    )
    results.upsert_result(
        job_id="report-scope",
        platform="xhs",
        content_key="report-note-10",
        result={
            "note_id": "report-note-10",
            "decision": "review",
            "risk_level": "high",
            "evidence_items": [
                {
                    "evidence_id": "text:desc",
                    "primary_modality": "text",
                    "source": "desc",
                    "text": "unselected evidence",
                }
            ],
        },
    )

    source = CanonicalReportSource(ingestion.db_path, tmp_path / "outputs")
    rows = source.canonical_rows("report-scope")
    evidence = source.adapt_evidence(rows[0])

    assert [row["content_key"] for row in rows] == ["report-note-1"]
    assert [item.original_text for item in evidence] == ["selected evidence"]


def test_callback_reorder_and_replay_do_not_expand_frozen_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = CandidateCrawler(
        [{"note_id": "note-a"}, {"note_id": "note-b"}],
        [{"note_id": "note-b"}, {"note_id": "note-a"}],
    )
    jobs, ingestion, analyzed_first = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id="replay",
        crawler=first,
        analyze_limit=1,
    )

    replay = CandidateCrawler(
        [{"note_id": "note-b"}, {"note_id": "note-a"}],
        [{"note_id": "note-b"}, {"note_id": "note-a"}],
    )
    jobs, ingestion, analyzed_replay = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id="replay",
        crawler=replay,
        analyze_limit=1,
    )

    assert analyzed_first == ["note-a"]
    assert analyzed_replay == []
    assert ingestion.stats_for_task("replay")["ingested_count"] == 1
    assert [ref["content_key"] for ref in ingestion.refs_for_task("replay")] == [
        "note-a"
    ]
    assert [item["content_key"] for item in jobs.get("replay")["items"]] == [
        "note-a"
    ]


def test_missing_selected_payload_keeps_sql_membership_and_blocks_replay_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ingestion, analyzed_first = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id="missing-selected-payload",
        crawler=CandidateCrawler([{"note_id": "note-a"}]),
        analyze_limit=1,
    )
    membership = ingestion.selection_membership_for_task(
        "missing-selected-payload"
    )
    assert [row["content_key"] for row in membership] == ["note-a"]
    Path(membership[0]["raw_item_path"]).unlink()

    replay = CandidateCrawler([{"note_id": "note-b"}])
    jobs, ingestion, analyzed_replay = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id="missing-selected-payload",
        crawler=replay,
        analyze_limit=1,
    )

    job = jobs.get("missing-selected-payload")
    membership = ingestion.selection_membership_for_task(
        "missing-selected-payload"
    )
    assert analyzed_first == ["note-a"]
    assert analyzed_replay == []
    assert replay.calls == []
    assert job["status"] == "failed"
    assert str(job["error"]).startswith("selected_content_payload_unavailable:")
    assert [row["content_key"] for row in membership] == ["note-a"]
    assert ingestion.stats_for_task("missing-selected-payload")["ingested_count"] == 1


@pytest.mark.parametrize(
    "replacement",
    [
        "{broken-json",
        json.dumps([]),
        json.dumps({"comments": []}),
        json.dumps({"item": [], "comments": []}),
        json.dumps({"item": {"note_id": "note-b"}, "comments": []}),
    ],
    ids=[
        "invalid-json",
        "non-object-root",
        "missing-item",
        "non-object-item",
        "identity-mismatch",
    ],
)
def test_selected_payload_integrity_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    _, ingestion, _ = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id="payload-integrity",
        crawler=CandidateCrawler([{"note_id": "note-a"}]),
        analyze_limit=1,
    )
    membership = ingestion.selection_membership_for_task("payload-integrity")
    raw_path = Path(membership[0]["raw_item_path"])
    raw_path.write_text(replacement, encoding="utf-8")

    with pytest.raises(
        SelectedContentPayloadUnavailableError,
        match=r"^selected_content_payload_unavailable:",
    ):
        ingestion.validated_refs_for_task("payload-integrity")

    remaining = ingestion.selection_membership_for_task("payload-integrity")
    assert [row["content_key"] for row in remaining] == ["note-a"]
    assert ingestion.stats_for_task("payload-integrity")["ingested_count"] == 1


def test_all_invalid_candidates_fail_without_business_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, ingestion, analyzed = _run_pipeline(
        tmp_path,
        monkeypatch,
        job_id="all-invalid",
        crawler=CandidateCrawler(
            [{"title": "missing"}, {"note_id": "null"}, {"note_id": []}]
        ),
        analyze_limit=1,
    )

    job = jobs.get("all-invalid")
    assert job["status"] == "failed"
    assert str(job["error"]).startswith("no_valid_content_selected:")
    assert ingestion.stats_for_task("all-invalid")["ingested_count"] == 0
    assert analyzed == []
    assert job["items"] == []


def test_authoritative_provider_failure_prevents_completed_result_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_db = tmp_path / "provider-failure.sqlite3"
    jobs = JobStore(audit_db)
    ingestion = IngestionStore(audit_db)
    configuration = _configuration(mode="search", analyze_limit=1)
    configuration["_authoritative_m3_contract"] = True
    job_id = "authoritative-provider-failure"
    jobs.create(
        job_id=job_id,
        **{
            key: value
            for key, value in configuration.items()
            if not key.startswith("_")
        },
    )
    monkeypatch.setattr(pipeline_module, "job_store", jobs)
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", tmp_path / "outputs")
    monkeypatch.setattr(
        pipeline_module.settings, "auto_analyze_crawled_content", True
    )

    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = job_id
    pipeline.crawler = CandidateCrawler([{"note_id": "provider-item", "title": "item"}])
    pipeline.ingestion = ingestion
    pipeline.audit_results = object()
    pipeline.prompt_profile_snapshot = {}
    pipeline.audit_config_revision_id = ""
    pipeline.rule_snapshot = {}
    pipeline.qwen = SimpleNamespace(provider_failure="")
    persist_calls: list[str] = []

    def fail_provider_after_analysis(subject):
        pipeline.qwen.provider_failure = "text Provider request failed"
        return {
            "note_id": subject.note_id,
            "decision": "review",
            "risk_level": "medium",
        }

    pipeline._analyze_subject = fail_provider_after_analysis
    pipeline._persist_audit_result = lambda **kwargs: persist_calls.append(
        kwargs["content_key"]
    )

    pipeline.run(SimpleNamespace(**configuration))

    job = jobs.get(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "audit_provider_failed: 审核服务调用失败"
    assert persist_calls == []
    assert ingestion.stats_for_task(job_id)["completed_analysis_count"] == 0
    assert ingestion.stats_for_task(job_id)["failed_analysis_count"] == 1


def test_authoritative_pipeline_does_not_switch_an_invalid_frozen_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_db = tmp_path / "invalid-frozen-account.sqlite3"
    jobs = JobStore(audit_db)
    ingestion = IngestionStore(audit_db)
    configuration = _configuration(mode="search", analyze_limit=1)
    configuration["_authoritative_m3_contract"] = True
    configuration["platform"] = "dy"
    configuration["crawler_account_id"] = "frozen-account-that-became-invalid"
    job_id = "authoritative-invalid-frozen-account"
    jobs.create(
        job_id=job_id,
        **{
            key: value
            for key, value in configuration.items()
            if not key.startswith("_")
        },
    )
    crawler = CandidateCrawler([{"aweme_id": "must-not-be-crawled"}])
    monkeypatch.setattr(pipeline_module, "job_store", jobs)
    monkeypatch.setattr(
        pipeline_module,
        "crawler_account_store",
        SimpleNamespace(get=lambda _account_id: None),
    )
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", tmp_path / "outputs")
    monkeypatch.setattr(
        pipeline_module.settings, "auto_analyze_crawled_content", True
    )
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = job_id
    pipeline.crawler = crawler
    pipeline.ingestion = ingestion
    pipeline.audit_results = object()
    pipeline.prompt_profile_snapshot = {}
    pipeline.audit_config_revision_id = ""
    pipeline.rule_snapshot = {}

    pipeline.run(SimpleNamespace(**configuration))

    job = jobs.get(job_id)
    assert job["status"] == "failed"
    assert job["error"] == (
        "crawler_account_login_required: 抖音采集服务当前不可用，请稍后重试。"
    )
    assert crawler.calls == []
    assert ingestion.stats_for_task(job_id)["ingested_count"] == 0
