from fastapi import BackgroundTasks

from backend import main
from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.pipeline import search_resume_parameters


def _job_payload():
    return {
        "platform": "dy",
        "display_name": "续抓测试",
        "crawl_mode": "search",
        "keyword": "风景",
        "keyword_source": "keyword",
        "lexicon_category": "",
        "library_ids": [],
        "capabilities": [],
        "scoring_template": "balanced",
        "rule_snapshot": {},
        "lexicon_keywords": [],
        "creator_url": "",
        "creator_id": "",
        "start_page": 1,
        "max_notes": 3,
        "max_comments": 0,
        "max_concurrency": 1,
        "max_items_per_minute": 3,
        "get_sub_comment": False,
        "analyze_limit": 0,
        "run_crawler": True,
        "source_output_id": None,
        "analysis_batch_size": 1,
        "prompt_profile_snapshot": {},
    }


def test_douyin_checkpoint_page_is_scoped_to_checkpoint_keyword():
    assert search_resume_parameters("dy", 0, "词一", 3) == (0, "词一", 3)


def test_failed_job_is_projected_as_failed_crawl_status(tmp_path, monkeypatch):
    jobs = JobStore(tmp_path / "audit.sqlite3")
    ingestion = IngestionStore(tmp_path / "audit.sqlite3")
    job = jobs.create(job_id="failed-dy", **{**_job_payload(), "platform": "dy"})
    jobs.update(job["id"], status="failed", error="采集账号触发平台验证")

    monkeypatch.setattr(main, "job_store", jobs)
    monkeypatch.setattr(main, "ingestion_store", ingestion)

    enriched = main.enrich_job(jobs.get(job["id"]))

    assert enriched["status"] == "failed"
    assert enriched["crawl_status"] == "failed"


def test_resume_action_reuses_frozen_request_and_keeps_checkpoint(tmp_path, monkeypatch):
    jobs = JobStore(tmp_path / "audit.sqlite3")
    ingestion = IngestionStore(tmp_path / "audit.sqlite3")
    job = jobs.create(job_id="resume-dy", **_job_payload())
    jobs.update(
        job["id"], status="crawl_paused", crawl_checkpoint_page=3,
        crawl_checkpoint_keyword="风景",
    )
    jobs.update_control(job["id"], crawl_stop_requested=True)

    scheduled = []

    class FakePipeline:
        def __init__(self, job_id):
            self.job_id = job_id

        def run(self, request):
            scheduled.append((self.job_id, request))

    monkeypatch.setattr(main, "job_store", jobs)
    monkeypatch.setattr(main, "ingestion_store", ingestion)
    monkeypatch.setattr(main, "AuditPipeline", FakePipeline)

    background = BackgroundTasks()
    response = main.control_job(
        job["id"], main.JobControlRequest(action="resume_crawl"), background
    )

    assert response["status"] == "queued"
    assert response["control"]["crawl_stop_requested"] is False
    assert response["crawl_checkpoint_page"] == 3
    assert response["available_actions"]["resume_crawl"] is False
    assert len(background.tasks) == 1
    assert background.tasks[0].func.__self__.job_id == job["id"]
    request = background.tasks[0].args[0]
    assert request.platform == "dy"
    assert request.keyword == "风景"
    assert request.max_notes == 3
