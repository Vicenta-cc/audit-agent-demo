"""Account interruption is a user decision point, not implicit cancellation."""
import json
from types import SimpleNamespace

import pytest

from test_task_admission import stack, make_run, worker_for
from test_investigation_creation_m3 import FakeExecutionAdapter, FakeReportAdapter
from backend.audit_agent.job_state import available_job_actions, failure_metadata
from backend.investigation_creation.contracts import RunStatus
from backend.task_admission.legacy import enqueue_resume
from backend.task_admission.presentation import admission_actions
from backend.task_admission.store import AdmissionError


def blocked_state(completed=1):
    return {
        **FakeExecutionAdapter.completed_state(ingested=completed, completed=completed),
        "status": "failed", "crawl_status": "failed", "analysis_status": "failed",
        "run_crawler": True,
        "error": "crawler_account_verification_required: account needs verification",
        "control": {"failure": failure_metadata("crawler_account_verification_required")},
    }


def test_account_failure_retains_open_admission_and_no_automatic_report(stack):
    creation, _, (owner, _) = stack
    run = make_run(creation, owner, "blocked")
    execution, reports = FakeExecutionAdapter(blocked_state()), FakeReportAdapter()
    worker = worker_for(creation, execution, reports)
    worker.run_once()
    saved = creation.get_run_for_worker(run.id)
    assert saved.status == RunStatus.INTERRUPTED
    assert saved.error_code == "crawler_account_verification_required"
    row = creation.admission.for_task(run.id)
    assert (row["state"], row["decision"], row["queue_state"]) == ("RESERVED", "OPEN", "HELD")
    assert reports.generate_calls == 0
    assert worker.run_once() is None  # Restart/poll does not silently recollect.
    assert execution.pipeline_calls == 1
    actions = admission_actions(available_job_actions(blocked_state(), {"completed_analysis_count": 1}), row)
    assert actions["resume_crawl"] and actions["finish_collection"]


@pytest.mark.parametrize("completed,expected", [(0, False), (1, True)])
def test_finish_requires_successful_audit_and_is_not_cancellation(completed, expected):
    job = blocked_state(completed)
    assert available_job_actions(job, job["task_stats"])["finish_collection"] is expected
    job["control"]["stop_all_requested"] = True
    assert not available_job_actions(job, job["task_stats"])["finish_collection"]


def test_finish_dispatch_drains_without_account_or_crawler_then_publishes(stack, monkeypatch):
    creation, _, (owner, _) = stack
    run = make_run(creation, owner, "partial")
    execution, reports = FakeExecutionAdapter(blocked_state()), FakeReportAdapter()
    worker = worker_for(creation, execution, reports)
    worker.run_once()
    row = creation.admission.for_task(run.id)
    job_id = row["job_id"]
    job = execution.jobs[job_id]
    job.update(id=job_id, owner_user_id=owner)
    execution.job_store.update_control = lambda ident, **values: execution.jobs[ident].setdefault("control", {}).update(values)
    execution.crawler_account_store = SimpleNamespace(available_accounts=lambda _: [])
    drains = []

    class Pipeline:
        def __init__(self, job_id):
            self.ingestion = SimpleNamespace(reset_analyzing_for_task=lambda _: 0)

        def run(self, *args):
            raise AssertionError("Finish must not collect")

        def resume_pending_analysis(self, *args):
            drains.append(job_id)
            assert job["control"]["collection_finalized"] is True
            assert job["crawl_status"] == "stopped"
            job.update(status="completed", analysis_status="completed")

    monkeypatch.setattr("backend.audit_agent.pipeline.AuditPipeline", Pipeline)
    queued = enqueue_resume(creation.admission, job, owner=owner, action="finish_collection", key="finish")
    assert enqueue_resume(creation.admission, job, owner=owner, action="finish_collection", key="finish")["id"] == queued["id"]
    with pytest.raises(AdmissionError):
        enqueue_resume(creation.admission, job, owner=owner, action="resume_crawl", key="race")
    worker.run_once()
    worker.run_once()
    assert drains == [job_id]
    assert execution.pipeline_calls == 1
    assert reports.generate_calls == 1
    assert creation.get_run_for_worker(run.id).status == RunStatus.PUBLISHED
    assert creation.admission.for_task(run.id)["state"] == "SUCCEEDED"
    with pytest.raises(AdmissionError):
        enqueue_resume(creation.admission, job, owner=owner, action="resume_crawl", key="too-late")


def test_collection_coverage_is_visible_without_failed_posts():
    from backend.reporting.pass_graph import partial_coverage_paragraphs
    snapshot = SimpleNamespace(posts=[1], statistics={"source_coverage": {
        "selected_posts": 1, "collection_incomplete": True,
        "excluded_failed_posts": [],
    }})
    text = " ".join(partial_coverage_paragraphs(snapshot))
    assert "采集未完成" in text and "1" in text


@pytest.mark.parametrize("count", [1, 5])
def test_real_report_freezes_partial_collection_notice(tmp_path, count):
    from test_pass_report import seed_audit, R31ReportRuntime
    from backend.audit_agent.job_store import JobStore
    source, store = seed_audit(tmp_path, count=count)
    jobs = JobStore(source.db_path)
    jobs.update_control("new-search-task", collection_finalized=True)
    result = R31ReportRuntime(store).generate("new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3")
    projection = store.get_presentation_projection(result.report_version_id)
    assert "采集未完成" in json.dumps(projection, ensure_ascii=False)
    snapshot = store.get_source_snapshot(result.report_version_id)
    jobs.update_control("new-search-task", collection_finalized=False)
    assert store.get_source_snapshot(result.report_version_id) == snapshot


def test_crash_after_finish_intent_requeues_only_drain_not_collection(stack):
    from backend.task_admission.recovery import reconcile
    creation, _, (owner, _) = stack
    run = make_run(creation, owner, "crash")
    worker = worker_for(creation, FakeExecutionAdapter(blocked_state()))
    worker.run_once()
    row = creation.admission.for_task(run.id)
    job = {"id": row["job_id"], "owner_user_id": owner}
    enqueue_resume(creation.admission, job, owner=owner, action="finish_collection", key="finish")
    creation.admission.start(run.id, "crashed-process")
    reconcile(worker)
    row = creation.admission.for_task(run.id)
    assert row["queue_state"] == "QUEUED"
    assert json.loads(row["payload_json"])["resume_action"] == "finish_collection"
    assert worker.execution_adapter.pipeline_calls == 1
    creation.admission.cancel(run.id, owner)
    reconcile(worker)
    assert creation.admission.for_task(run.id)["state"] == "RELEASED"
    assert worker.report_adapter.generate_calls == 0


def test_two_choices_racing_cannot_both_execute(stack):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    creation, _, (owner, _) = stack
    run = make_run(creation, owner, "race")
    worker = worker_for(creation, FakeExecutionAdapter(blocked_state()))
    worker.run_once()
    row = creation.admission.for_task(run.id)
    job = {"id": row["job_id"], "owner_user_id": owner}
    barrier = Barrier(2)

    def submit(action):
        barrier.wait()
        try:
            enqueue_resume(creation.admission, job, owner=owner, action=action, key=action)
            return action
        except AdmissionError:
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(submit, ["resume_crawl", "finish_collection"]))
    assert len([value for value in outcomes if value]) == 1
    row = creation.admission.for_task(run.id)
    assert json.loads(row["payload_json"])["resume_action"] in outcomes


def test_available_account_resumes_same_job_with_checkpoint(stack, monkeypatch):
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.task_admission.resources import selected_account
    from crawler_auth_fixtures import mark_camoufox_login_verified
    creation, auth, (owner, _) = stack
    run = make_run(creation, owner, "resume", platform="dy")
    execution = FakeExecutionAdapter(blocked_state())
    worker = worker_for(creation, execution)
    worker.run_once()
    row = creation.admission.for_task(run.id)
    job = execution.jobs[row["job_id"]]
    job.update(id=row["job_id"], owner_user_id=owner, platform="dy", keyword="resume", crawl_mode="search", crawl_checkpoint_keyword="resume", crawl_checkpoint_page=3)
    execution.job_store.update_control = lambda ident, **values: job.setdefault("control", {}).update(values)
    accounts = CrawlerAccountStore(creation.db_path)
    execution.crawler_account_store = accounts
    replacement_id = None
    calls = []

    class Pipeline:
        def __init__(self, job_id):
            self.ingestion = SimpleNamespace(reset_analyzing_for_task=lambda _: 0)

        def run(self, request):
            calls.append(job["id"])
            assert selected_account() == replacement_id
            assert job["crawl_checkpoint_page"] == 3
            job.update(status="completed", crawl_status="completed", analysis_status="completed", error="")

    monkeypatch.setattr("backend.audit_agent.pipeline.AuditPipeline", Pipeline)
    enqueue_resume(creation.admission, job, owner=owner, action="resume_crawl", key="resume")
    assert worker.run_once() is None
    assert not calls
    replacement = accounts.create(platform="dy", display_name="replacement")
    replacement_id = replacement["id"]
    accounts.save_auth_state(replacement_id, "fixture-ciphertext")
    mark_camoufox_login_verified(accounts, replacement_id)
    auth.register_crawler_account(replacement_id, owner)
    worker.run_once()
    worker.run_once()
    assert calls == [row["job_id"]]
    assert creation.get_run_for_worker(run.id).status == RunStatus.PUBLISHED
    assert creation.admission.for_task(run.id)["id"] == row["id"]
