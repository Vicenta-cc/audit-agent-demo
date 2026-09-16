from fastapi import BackgroundTasks
from types import SimpleNamespace

import pytest

from backend import main
from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.job_state import failure_metadata
from backend.audit_agent.pipeline import crawler_start_page, search_resume_parameters
from backend.investigation_creation.contracts import RunStatus
from backend.investigation_creation.adapters import InvestigationRunProjector


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
    assert search_resume_parameters("dy", 1, "词一", 3) == (1, "词一", 3)
    assert search_resume_parameters("dy", 2, "词一", 0) == (2, "词一", 1)
    assert search_resume_parameters("dy", 2, "", 0) == (2, "", None)


def test_douyin_user_page_is_kept_for_crawler_cli():
    assert crawler_start_page("dy", 0) == 0
    assert crawler_start_page("dy", 1) == 1
    assert crawler_start_page("dy", 2) == 2
    assert crawler_start_page("xhs", 1) == 1


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
    assert background.tasks[0].args[1] == 1
    assert response["control"]["crawl_epoch"] == 1


def test_resume_request_reuses_effective_comment_and_media_switches():
    job = _job_payload()
    job["effective_config"] = {
        "collect_comments": False,
        "collect_media": False,
    }

    request = main.crawl_request_from_job(job)

    assert request.collect_comments is False
    assert request.collect_media is False


def test_recoverable_failed_m3_job_reenters_same_pipeline_and_run(tmp_path, monkeypatch):
    jobs = JobStore(tmp_path / "audit.sqlite3")
    ingestion = IngestionStore(tmp_path / "audit.sqlite3")
    job = jobs.create(job_id="recoverable-failed-dy", **_job_payload())
    jobs.update(
        job["id"],
        status="failed",
        crawl_status="failed",
        analysis_status="failed",
        error="crawler_account_verification_required: synthetic verification",
    )
    jobs.update_control(
        job["id"],
        failure=failure_metadata(
            "crawler_account_verification_required", "synthetic verification"
        ),
    )
    calls = []

    class FakeCreationStore:
        def get_run_for_job(self, job_id):
            assert job_id == job["id"]
            return SimpleNamespace(status=RunStatus.FAILED)

        def begin_recoverable_crawl_for_job(self, job_id, **kwargs):
            calls.append((job_id, kwargs))
            return True

    class FakePipeline:
        def __init__(self, job_id):
            self.job_id = job_id

        def run(self, request, crawl_epoch=None):
            return None

    monkeypatch.setattr(main, "job_store", jobs)
    monkeypatch.setattr(main, "ingestion_store", ingestion)
    monkeypatch.setattr(main, "investigation_creation_store", FakeCreationStore())
    monkeypatch.setattr(main, "AuditPipeline", FakePipeline)

    background = BackgroundTasks()
    response = main.control_job(
        job["id"], main.JobControlRequest(action="resume_crawl"), background
    )

    assert response["id"] == job["id"]
    assert response["status"] == "queued"
    assert calls[0][0] == job["id"]
    assert calls[0][1]["failure_code"] == "crawler_account_verification_required"
    assert len(background.tasks) == 1


def test_nonrecoverable_failed_job_cannot_continue_collection(tmp_path, monkeypatch):
    jobs = JobStore(tmp_path / "audit.sqlite3")
    ingestion = IngestionStore(tmp_path / "audit.sqlite3")
    job = jobs.create(job_id="nonrecoverable-failed-dy", **_job_payload())
    jobs.update(
        job["id"],
        status="failed",
        crawl_status="failed",
        analysis_status="failed",
        error="audit_provider_unavailable: synthetic provider failure",
    )
    jobs.update_control(
        job["id"],
        failure=failure_metadata(
            "audit_provider_unavailable", "synthetic provider failure"
        ),
    )
    monkeypatch.setattr(main, "job_store", jobs)
    monkeypatch.setattr(main, "ingestion_store", ingestion)

    with pytest.raises(main.HTTPException) as exc_info:
        main.control_job(
            job["id"],
            main.JobControlRequest(action="resume_crawl"),
            BackgroundTasks(),
        )

    assert exc_info.value.status_code == 409


def test_continue_collection_projection_waits_for_an_eligible_account(
    tmp_path, monkeypatch
):
    jobs = JobStore(tmp_path / "audit.sqlite3")
    ingestion = IngestionStore(tmp_path / "audit.sqlite3")
    job = jobs.create(
        job_id="cooling-failed-dy",
        **{**_job_payload(), "crawler_account_id": "preferred-account"},
    )
    jobs.update(
        job["id"],
        status="failed",
        crawl_status="failed",
        analysis_status="failed",
        error="crawler_account_verification_required: synthetic verification",
    )
    jobs.update_control(
        job["id"],
        failure=failure_metadata(
            "crawler_account_verification_required", "synthetic verification"
        ),
    )
    monkeypatch.setattr(main, "job_store", jobs)
    monkeypatch.setattr(main, "ingestion_store", ingestion)
    monkeypatch.setattr(
        main,
        "crawler_account_store",
        SimpleNamespace(available_accounts=lambda _platform: []),
    )

    cooling = main.enrich_job(jobs.get(job["id"]))
    assert cooling["available_actions"]["resume_crawl"] is False

    monkeypatch.setattr(
        main,
        "crawler_account_store",
        SimpleNamespace(available_accounts=lambda _platform: [{"id": "fresh"}]),
    )
    eligible = main.enrich_job(jobs.get(job["id"]))
    assert eligible["available_actions"]["resume_crawl"] is True

    projector = InvestigationRunProjector(
        job_store=jobs,
        ingestion_store=ingestion,
        crawler_account_store=SimpleNamespace(
            available_accounts=lambda _platform: []
        ),
    )
    projected = projector.project(
        SimpleNamespace(
            job_id=job["id"],
            status=RunStatus.FAILED,
            report_version_id="",
        )
    )
    assert projected["available_actions"]["resume_crawl"] is False
