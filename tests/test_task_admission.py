import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace

import pytest
from test_investigation_creation_m3 import (
    FakeConfigurationResolver,
    FakeExecutionAdapter,
    FakeReportAdapter,
    FakeSessionAdapter,
    configuration_payload,
)

from backend import main
from backend.application_auth.store import AuthStore
from backend.audit_agent.config import settings
from backend.investigation_creation.contracts import (
    InvestigationConfiguration,
    RunStatus,
)
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.worker import InvestigationWorker
from backend.task_admission.execution import crawler_fds, execution_lock
from backend.task_admission.legacy import enqueue_resume
from backend.task_admission.store import AdmissionError


@pytest.fixture
def stack(tmp_path, monkeypatch):
    path = tmp_path / "control.sqlite3"
    monkeypatch.setattr(settings, "app_auth_db", path)
    monkeypatch.setattr(settings, "app_auth_mode", "required")
    auth = AuthStore(path)
    users = []
    for name in ("alice", "bravo"):
        user = auth.create_user(username=name, password="correct horse battery staple")
        auth.login(name, "correct horse battery staple")
        users.append(user["id"])
    creation = InvestigationCreationStore(path)
    return creation, auth, users


def enqueue(store, owner, key="one", task=None, **kwargs):
    return store.enqueue(
        owner=owner,
        task_id=task or "task:" + key,
        kind="legacy",
        key=key,
        payload=kwargs.get("payload", {}),
        job_id="job:" + key,
    )


def make_run(creation, owner, key):
    config = InvestigationConfiguration.model_validate(
        configuration_payload(keyword=key)
    )
    draft = creation.create_draft(
        principal=owner,
        title=key,
        objective="Review the requested subject",
        configuration=config,
    )
    return creation.confirm_and_queue(
        draft.id,
        principal=owner,
        expected_revision=1,
        confirmed=True,
        idempotency_key=key,
        request_fingerprint=key,
        resolved_configuration=FakeConfigurationResolver().resolve(config),
    )


def worker_for(creation, execution=None, reports=None):
    execution = execution or FakeExecutionAdapter()
    if isinstance(execution, FakeExecutionAdapter):
        execution.job_store = SimpleNamespace(
            get=execution.get_job_state,
            update=lambda ident, **values: execution.jobs.setdefault(ident, {}).update(
                values
            ),
        )
    return InvestigationWorker(
        creation,
        execution_adapter=execution,
        report_adapter=reports or FakeReportAdapter(),
        session_adapter=FakeSessionAdapter(),
    )


def test_racing_windows_share_one_slot_and_atomic_queue(stack):
    creation, _, (a, b) = stack
    barrier = Barrier(8)

    def submit(i):
        barrier.wait()
        try:
            return enqueue(creation.admission, a, str(i))["id"]
        except AdmissionError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert results.count("USER_TASK_LIMIT") == 7
    assert creation.admission.summary(a)["reserved"] == 1
    enqueue(creation.admission, b, "different-user")
    assert creation.admission.summary(b)["reserved"] == 1


def test_duplicate_requests_and_payload_conflict(stack):
    creation, _, (a, _) = stack
    store = creation.admission
    barrier = Barrier(5)

    def submit(_):
        barrier.wait()
        return enqueue(store, a)["id"]

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert len(set(pool.map(submit, range(5)))) == 1
    with pytest.raises(AdmissionError, match="不同内容"):
        enqueue(store, a, payload={"changed": True})
    assert store.summary(a)["remaining"] == 2


def test_three_successes_delete_independent_and_cross_day(stack):
    creation, _, (a, _) = stack
    store = creation.admission
    now = datetime(2026, 9, 20, 15, 59, tzinfo=timezone.utc)
    store.clock = lambda: now
    for i in range(3):
        row = enqueue(store, a, str(i))
        store.settle(row["task_id"], report_id="report:" + str(i))
        store.settle(row["task_id"], report_id="report:" + str(i))
    assert store.summary(a)["completed"] == 3
    with pytest.raises(AdmissionError) as error:
        enqueue(store, a, "fourth")
    assert error.value.code == "DAILY_REPORT_LIMIT"
    now += timedelta(minutes=2)
    assert store.summary(a)["remaining"] == 3
    row = enqueue(store, a, "tomorrow")
    now += timedelta(days=1)
    assert store.summary(a)["remaining"] == 3
    assert store.summary(a)["active_task"]["day"] == "2026-09-21"
    with pytest.raises(AdmissionError):
        enqueue(store, a, "blocked-by-yesterday")
    store.settle(row["task_id"], report_id="cross-day-report")
    assert store.summary(a)["completed"] == 0
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM task_admissions WHERE state='SUCCEEDED'"
            ).fetchone()[0]
            == 4
        )


def test_cancel_pause_do_not_release_until_execution_stopped(stack):
    creation, _, (a, b) = stack
    store = creation.admission
    row = enqueue(store, a)
    store.hold(row["task_id"], "paused")
    assert store.summary(a)["reserved"] == 1
    with pytest.raises(AdmissionError):
        store.cancel(row["task_id"], b)
    store.cancel(row["task_id"], a)
    assert store.summary(a)["reserved"] == 1
    with pytest.raises(AdmissionError):
        store.settle(row["task_id"])
    store.settle(row["task_id"], stopped=True)
    store.settle(row["task_id"], stopped=True)
    assert store.summary(a)["remaining"] == 2
    with pytest.raises(AdmissionError):
        with store.publication("job:one", "late-report", "old-worker"):
            pytest.fail("cancelled publisher ran")


def test_publication_wins_cancel_and_crash_intent_is_not_refunded(stack):
    creation, _, (a, _) = stack
    store = creation.admission
    row = enqueue(store, a)
    store.start(row["task_id"], "lease")
    with pytest.raises(RuntimeError):
        with store.publication("job:one", "report-1", "lease"):
            assert store.cancel(row["task_id"], a)["decision"] == "PUBLISHING"
            raise RuntimeError("process died after durable publication intent")
    with pytest.raises(AdmissionError):
        store.settle(row["task_id"], stopped=True)
    assert store.summary(a)["reserved"] == 1
    store.settle(row["task_id"], report_id="report-1")
    assert store.summary(a)["completed"] == 1
    assert store.cancel(row["task_id"], a)["state"] == "SUCCEEDED"


def test_cancel_wins_publication_and_replaced_token_is_fenced(stack):
    creation, _, (a, _) = stack
    store = creation.admission
    row = enqueue(store, a)
    store.start(row["task_id"], "first")
    store.start(row["task_id"], "replacement")
    with pytest.raises(AdmissionError):
        with store.publication("job:one", "report-1", "first"):
            pytest.fail("stale worker published")
    store.cancel(row["task_id"], a)
    with pytest.raises(AdmissionError):
        with store.publication("job:one", "report-1", "replacement"):
            pytest.fail("cancelled worker published")


def test_expired_or_unauthorized_admission_never_creates_queue(stack):
    creation, auth, (a, _) = stack
    with pytest.raises(AdmissionError):
        enqueue(creation.admission, a, payload={"crawler_account_id": "not-granted"})
    assert creation.admission.summary(a)["reserved"] == 0
    auth.update_user(a, actor_user_id="admin", status="disabled")
    with pytest.raises(AdmissionError):
        enqueue(creation.admission, a)
    with creation.admission.connect() as db:
        assert db.execute("SELECT count(*) FROM task_admissions").fetchone()[0] == 0


def test_run_and_quota_commit_or_rollback_together(stack, monkeypatch):
    creation, _, (a, _) = stack
    original = creation._record_idempotency_key
    monkeypatch.setattr(
        creation,
        "_record_idempotency_key",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("transaction rollback")
        ),
    )
    with pytest.raises(RuntimeError):
        make_run(creation, a, "rollback")
    assert creation.admission.summary(a)["reserved"] == 0
    assert creation.admission.summary(a)["used"] == 0
    assert creation.admission.summary(a)["remaining"] == 3
    with creation._connect() as db:
        assert db.execute("SELECT count(*) FROM investigation_runs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM task_admissions").fetchone()[0] == 0
    monkeypatch.setattr(creation, "_record_idempotency_key", original)
    run = make_run(creation, a, "accepted")
    assert creation.admission.for_task(run.id)["queue_state"] == "QUEUED"
    with pytest.raises(AdmissionError):
        enqueue(creation.admission, a, "legacy-bypass")


def test_worker_success_failure_pause_and_queued_cancel(stack):
    creation, _, (a, _) = stack
    run = make_run(creation, a, "success")
    worker = worker_for(creation)
    result = worker.run_once()
    assert result.status == RunStatus.PUBLISHED, result
    assert creation.admission.summary(a)["completed"] == 1
    run = make_run(creation, a, "failed")
    worker = worker_for(
        creation, FakeExecutionAdapter({"status": "failed", "error": "failure"})
    )
    assert worker.run_once().status == RunStatus.FAILED
    assert creation.admission.summary(a)["remaining"] == 1
    run = make_run(creation, a, "paused")
    worker = worker_for(creation, FakeExecutionAdapter({"status": "crawl_paused"}))
    assert worker.run_once().status == RunStatus.INTERRUPTED
    assert creation.admission.summary(a)["reserved"] == 1
    creation.admission.cancel(run.id, a)
    # A test adapter intentionally does not have a JobStore; use a no-op projection.
    worker.execution_adapter.job_store = SimpleNamespace(
        update=lambda *args, **kwargs: None
    )
    worker.run_once()
    assert creation.admission.summary(a)["reserved"] == 0
    assert creation.admission.summary(a)["remaining"] == 0
    with pytest.raises(AdmissionError):
        make_run(creation, a, "fourth")
    b = stack[2][1]
    run = make_run(creation, b, "queued-cancel")
    creation.admission.cancel(run.id, b)
    worker = worker_for(creation)
    assert worker.run_once() is None
    assert worker.execution_adapter.pipeline_calls == 0
    assert creation.admission.summary(b)["remaining"] == 2


def test_resume_retains_charge_and_terminal_task_cannot_reopen(stack):
    creation, _, (a, _) = stack
    store = creation.admission
    row = enqueue(store, a)
    job = {"id": "job:one", "owner_user_id": a, "analyze_limit": 1}
    store.hold(row["task_id"], "paused")
    resumed = enqueue_resume(store, job, owner=a, action="resume_crawl", key="resume")
    assert resumed["id"] == row["id"]
    assert (
        enqueue_resume(store, job, owner=a, action="resume_crawl", key="resume")["id"]
        == row["id"]
    )
    with pytest.raises(AdmissionError):
        enqueue_resume(store, job, owner=a, action="resume_analysis", key="resume")
    store.settle(row["task_id"], stopped=True)
    with pytest.raises(AdmissionError) as error:
        enqueue_resume(store, job, owner=a, action="resume_crawl", key="retry")
    assert error.value.code == "TASK_ALREADY_ENDED"
    assert store.summary(a)["reserved"] == 0
    assert store.summary(a)["used"] == 1
    with pytest.raises(AdmissionError):
        enqueue_resume(
            store, job, owner=a, action="resume_analysis", key="expand", analyze_limit=3
        )


@pytest.mark.parametrize("completed", [0, 1])
def test_natural_post_failures_release_slot_without_user_end(stack, completed):
    creation, _, (owner, _) = stack
    run = make_run(creation, owner, "natural-result")
    final = FakeExecutionAdapter.completed_state(ingested=2, pending=2-completed, completed=completed)
    final["task_stats"]["failed_analysis_count"] = 2-completed
    if not completed:
        final["audit_results"] = []
    reports = FakeReportAdapter()
    worker = worker_for(creation, FakeExecutionAdapter(final_state=final), reports)
    result = worker.run_once()
    assert result.status == (RunStatus.PUBLISHED if completed else RunStatus.AUDIT_COMPLETED)
    from backend.task_admission.recovery import reconcile
    reconcile(worker)
    row = creation.admission.for_task(run.id)
    assert row["state"] == ("SUCCEEDED" if completed else "RELEASED")
    assert not row["end_requested_at"]
    assert creation.admission.summary(owner)["reserved"] == 0
    assert creation.admission.summary(owner)["used"] == 1
    assert reports.generate_calls == int(bool(completed))
    make_run(creation, owner, "next-investigation")


def test_child_process_keeps_execution_fence_after_worker_exits(tmp_path):
    path = tmp_path / "control.sqlite3"
    with execution_lock(path) as acquired:
        assert acquired
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
            pass_fds=crawler_fds(),
        )
    try:
        with execution_lock(path) as acquired:
            assert not acquired
    finally:
        child.communicate(b"", timeout=10)
    with execution_lock(path) as acquired:
        assert acquired


def test_pause_reconciliation_cannot_overwrite_concurrent_resume(stack):
    creation, _, (a, _) = stack
    store = creation.admission
    row = enqueue(store, a)
    store.hold(row["task_id"], "paused")
    stale = store.for_task(row["task_id"])
    job = {"id": "job:one", "owner_user_id": a, "analyze_limit": 1}
    enqueue_resume(store, job, owner=a, action="resume_crawl", key="resume")
    store.hold(
        row["task_id"], "stale-observation", expected_updated_at=stale["updated_at"]
    )
    assert not store.settle(
        row["task_id"], stopped=True, expected_updated_at=stale["updated_at"]
    )
    assert store.for_task(row["task_id"])["queue_state"] == "QUEUED"


@pytest.mark.parametrize("account_state", ["active", "disabled", "expired"])
def test_crash_after_report_publish_reconciles_once_without_recrawling(
    stack, account_state
):
    creation, auth, (a, _) = stack
    run = make_run(creation, a, "publish-crash")
    reports = FakeReportAdapter()
    execution = FakeExecutionAdapter()
    worker = worker_for(creation, execution, reports)

    class Crash(BaseException):
        pass

    worker.lifecycle_hook = lambda event, *args: (
        (_ for _ in ()).throw(Crash()) if event == "after_report_published" else None
    )
    with pytest.raises(Crash):
        worker.run_once()
    assert creation.admission.summary(a)["reserved"] == 1
    with creation._connect() as db:
        db.execute(
            "UPDATE investigation_runs SET heartbeat_at='' WHERE id=?", (run.id,)
        )
    if account_state == "disabled":
        auth.update_user(a, actor_user_id="admin", status="disabled")
    elif account_state == "expired":
        with creation.admission.connect() as db:
            db.execute(
                "UPDATE app_users SET expires_at='2020-01-01T00:00:00+00:00' WHERE id=?",
                (a,),
            )
    worker.lifecycle_hook = None
    worker.run_once()
    assert creation.get_run_for_worker(run.id).status == RunStatus.PUBLISHED
    assert execution.pipeline_calls == 1
    assert reports.generate_calls == 1
    worker.run_once()
    assert creation.admission.summary(a)["completed"] == 1


def test_report_generation_failure_releases_after_worker_stops(stack):
    creation, _, (a, _) = stack
    run = make_run(creation, a, "report-failure")
    reports = FakeReportAdapter()
    reports.after_started = lambda: (_ for _ in ()).throw(
        RuntimeError("provider failed")
    )
    worker = worker_for(creation, reports=reports)
    worker.run_once()
    row = creation.admission.for_task(run.id)
    assert row["state"] == "RELEASED"
    assert row["reason"] == "report_generation_failed"
    assert creation.get_run_for_worker(run.id).status == RunStatus.FAILED


def test_legacy_http_entry_persists_intent_before_job_and_worker_dispatch(
    stack, tmp_path, monkeypatch
):
    from fastapi import BackgroundTasks

    from backend.api.reporting import can_read_m3_report
    from backend.application_auth.service import ApplicationAuthService
    from backend.audit_agent import pipeline as pipeline_module
    from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
    from backend.audit_agent.job_store import JobStore
    from backend.investigation_creation.principal import Principal
    from backend.task_admission.execution import assert_execution

    creation, auth, (a, b) = stack
    jobs = JobStore(tmp_path / "jobs.sqlite3")
    revisions = TaskAuditConfigRevisionStore(jobs.db_path)
    auth.register_crawler_account("account", a)
    monkeypatch.setattr(main, "job_store", jobs)
    monkeypatch.setattr(main, "audit_config_revision_store", revisions)
    monkeypatch.setattr(main, "investigation_creation_store", creation)
    monkeypatch.setattr(main, "enrich_job", lambda value: value)
    monkeypatch.setattr(
        main,
        "_authorized_crawler_accounts",
        lambda principal: [
            {
                "id": "account",
                "platform": "dy",
                "status": "active",
                "has_auth_state": True,
            }
        ],
    )
    monkeypatch.setattr(
        main,
        "validate_crawler_account_for_job",
        lambda *args, **kwargs: {"id": "account", "display_name": "test"},
    )
    request = lambda: main.CrawlRequest(
        platform="dy",
        keyword="subject",
        crawler_account_id="account",
        library_ids=["soft"],
    )
    background = BackgroundTasks()
    first = main.create_job(
        request(), background, Principal(a), idempotency_key="http-one"
    )
    assert not background.tasks
    assert creation.admission.summary(a)["reserved"] == 1
    assert (
        main.create_job(
            request(), background, Principal(a), idempotency_key="http-one"
        )["id"]
        == first["id"]
    )
    with pytest.raises(AdmissionError):
        main.create_job(request(), background, Principal(a), idempotency_key="http-two")
    calls = []

    class Pipeline:
        def __init__(self, job_id):
            self.job_id = job_id

        def run(self, request):
            assert_execution(self.job_id)
            calls.append(self.job_id)
            jobs.update(self.job_id, status="completed")

    monkeypatch.setattr(pipeline_module, "AuditPipeline", Pipeline)
    execution = SimpleNamespace(
        job_store=jobs,
        revision_store=revisions,
        get_job_state=lambda job_id: dict(
            status=jobs.get(job_id)["status"],
            task_stats={"completed_analysis_count": 1},
        ),
    )
    reports = FakeReportAdapter()
    # Fresh worker/store models an API process exit after acceptance.
    fresh = InvestigationCreationStore(creation.db_path)
    worker = worker_for(fresh, execution, reports)
    worker.run_once()
    assert calls == [first["id"]]
    assert fresh.admission.summary(a)["completed"] == 1
    published = reports.find_published(first["id"])
    assert can_read_m3_report(
        fresh,
        Principal(a),
        report_version_id=published,
        task_id=first["id"],
        auth_service=ApplicationAuthService(auth),
    )
    assert not can_read_m3_report(
        fresh,
        Principal(b),
        report_version_id=published,
        task_id=first["id"],
        auth_service=ApplicationAuthService(auth),
    )
    jobs.archive(first["id"])
    assert fresh.admission.summary(a)["completed"] == 1


def test_worker_does_not_release_failed_task_while_crawler_child_survives(stack):
    creation, _, (a, _) = stack
    make_run(creation, a, "orphan-child")
    execution = FakeExecutionAdapter(
        {"status": "failed", "error": "cleanup incomplete"}
    )
    original = execution.run_pipeline
    children = []

    def pipeline(job_id, configuration):
        children.append(
            subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
                stdin=subprocess.PIPE,
                pass_fds=crawler_fds(),
            )
        )
        original(job_id, configuration)

    execution.run_pipeline = pipeline
    worker = worker_for(creation, execution)
    try:
        worker.run_once()
        assert creation.admission.summary(a)["reserved"] == 1
        assert worker.run_once() is None
        assert creation.admission.summary(a)["reserved"] == 1
    finally:
        for child in children:
            child.communicate(b"", timeout=10)
    worker.run_once()
    assert creation.admission.summary(a)["reserved"] == 0


def test_report_store_publication_gate_precedes_any_report_write(stack, monkeypatch):
    from backend.reporting.store import ReportStore
    from backend.task_admission.execution import execution_context

    creation, _, (a, _) = stack
    admission = creation.admission
    row = enqueue(admission, a)
    admission.start(row["task_id"], "worker")
    report_store = ReportStore.__new__(ReportStore)
    report_store.get_version = lambda ident: {
        "id": ident,
        "status": "draft",
        "report_id": "report",
    }
    report_store.get_report = lambda ident: {"task_id": row["job_id"]}
    writes = []
    report_store._publish_version = lambda **kwargs: (
        writes.append(kwargs) or {"status": "published"}
    )
    with pytest.raises(AdmissionError):
        report_store.publish_version(report_version_id="version")
    assert not writes
    admission.cancel(row["task_id"], a)
    with execution_context(admission, row["task_id"], "worker"):
        with pytest.raises(AdmissionError):
            report_store.publish_version(report_version_id="version")
    assert not writes


def test_legacy_report_v1_retry_remains_versioned_and_reuses_data(stack):
    creation, _, (a, _) = stack
    run = make_run(creation, a, "retry-report")
    with creation.admission.connect() as db:
        db.execute(
            "UPDATE task_admissions SET quota_policy='report_v1',charge_state='LEGACY',collection_phase='UNKNOWN'"
        )
    reports = FakeReportAdapter()
    reports.after_started = lambda: (_ for _ in ()).throw(
        RuntimeError("provider failed")
    )
    execution = FakeExecutionAdapter()
    worker = worker_for(creation, execution, reports)
    worker.run_once()
    row = creation.admission.for_task(run.id)
    assert row["state"] == "RELEASED"
    job = {"id": row["job_id"], "owner_user_id": a, "analyze_limit": 1}
    execution.job_store = SimpleNamespace(get=lambda ident: job)
    retried = enqueue_resume(
        creation.admission, job, owner=a, action="resume_report", key="report-retry"
    )
    assert retried["id"] != row["id"]
    binding = creation.get_report_binding(run.id)

    def resume(task_id, *, on_generation_started):
        on_generation_started(binding.r31_run_id, binding.report_version_id)
        reports.published_by_task[task_id] = (
            binding.r31_run_id,
            binding.report_version_id,
        )
        return binding.report_version_id

    reports.resume = resume
    worker.run_once()
    assert creation.get_run_for_worker(run.id).status == RunStatus.PUBLISHED
    assert creation.admission.summary(a)["completed"] == 1
    assert creation.admission.summary(a)["reserved"] == 0
    assert execution.pipeline_calls == 1
    assert reports.generate_calls == 1
    assert (
        enqueue_resume(
            creation.admission, job, owner=a, action="resume_report", key="report-retry"
        )["id"]
        == retried["id"]
    )


def test_user_expiry_blocks_queued_dispatch_without_collecting(stack):
    creation, auth, (a, _) = stack
    run = make_run(creation, a, "expired-in-queue")
    auth.update_user(a, actor_user_id="admin", status="disabled")
    worker = worker_for(creation)
    assert worker.run_once() is None
    assert worker.execution_adapter.pipeline_calls == 0
    assert creation.admission.for_task(run.id)["state"] == "RELEASED"


def test_real_report_publish_intent_recovery_completes_or_releases(stack):
    from backend.task_admission.recovery import reconcile

    creation, _, (a, _) = stack
    row = enqueue(creation.admission, a)
    creation.admission.start(row["task_id"], "worker")
    with pytest.raises(RuntimeError):
        with creation.admission.publication(row["job_id"], "version-1", "worker"):
            raise RuntimeError("lost after commit")
    reports = FakeReportAdapter()
    reports.published_by_task[row["job_id"]] = ("generation", "version-1")
    worker = worker_for(creation, reports=reports)
    with execution_lock(creation.db_path) as locked:
        assert locked
        reconcile(worker)
        reconcile(worker)
    assert creation.admission.summary(a)["completed"] == 1
    row = enqueue(creation.admission, a, "failed-before-commit")
    creation.admission.start(row["task_id"], "worker-2")
    with pytest.raises(RuntimeError):
        with creation.admission.publication(row["job_id"], "version-2", "worker-2"):
            raise RuntimeError("lost before commit")
    with execution_lock(creation.db_path) as locked:
        assert locked
        reconcile(worker)
    assert creation.admission.summary(a)["completed"] == 1
    assert creation.admission.summary(a)["remaining"] == 1


def test_recovery_release_cannot_overwrite_a_resume(stack):
    from backend.task_admission.recovery import release_stopped

    creation, _, (a, _) = stack
    row = enqueue(creation.admission, a)
    creation.admission.hold(row["task_id"], "interrupted")
    stale = creation.admission.for_task(row["task_id"])
    job = {"id": row["job_id"], "owner_user_id": a, "analyze_limit": 1}
    enqueue_resume(
        creation.admission, job, owner=a, action="resume_analysis", key="new-resume"
    )
    worker = worker_for(creation)
    release_stopped(worker, stale, "stale-failure")
    assert creation.admission.for_task(row["task_id"])["queue_state"] == "QUEUED"
    assert not worker.execution_adapter.jobs


def test_crashed_legacy_job_is_recoverable_without_automatic_collection(stack):
    from backend.task_admission.recovery import reconcile
    from backend.audit_agent.job_state import available_job_actions

    creation, _, (a, _) = stack
    row = enqueue(creation.admission, a)
    creation.admission.start(row["task_id"], "old-worker")
    worker = worker_for(creation)
    worker.execution_adapter.jobs[row["job_id"]] = dict(
        status="running",
        crawl_status="running",
        analysis_status="running",
        run_crawler=True,
    )
    with execution_lock(creation.db_path) as locked:
        assert locked
        reconcile(worker)
    job = worker.execution_adapter.jobs[row["job_id"]]
    assert available_job_actions(job, {})["resume_crawl"]
    assert creation.admission.for_task(row["task_id"])["queue_state"] == "HELD"
    assert creation.admission.summary(a)["reserved"] == 1
    assert worker.execution_adapter.pipeline_calls == 0


def test_cancellation_projects_stop_before_releasing_transaction(stack):
    creation, _, (a, _) = stack
    admission = creation.admission
    row = enqueue(admission, a)
    observations = []

    def signal(snapshot):
        observations.append((snapshot["state"], snapshot["decision"]))
        with admission.connect() as reader:
            # Cancellation is not externally committed before its stop signal.
            assert (
                reader.execute(
                    "SELECT decision FROM task_admissions WHERE id=?", (row["id"],)
                ).fetchone()[0]
                == "OPEN"
            )

    result = admission.cancel(row["task_id"], a, on_cancel=signal)
    assert observations == [("RESERVED", "OPEN")]
    assert result["decision"] == "CANCELLED"
    assert result["state"] == "RESERVED"
    assert admission.summary(a)["remaining"] == 2


@pytest.mark.parametrize(
    "started,cancelled,system_fault,refunded",
    [
        (False, False, True, True),
        (False, True, True, False),
        (True, False, True, False),
        (False, False, False, False),
    ],
)
def test_charge_refund_requires_positive_system_fault_and_stop_proof(
    stack, started, cancelled, system_fault, refunded
):
    from backend.task_admission.recovery import reconcile

    creation, _, (a, b) = stack
    store = creation.admission
    row = enqueue(store, a)
    other = enqueue(store, b, "other")
    store.start(row["task_id"], "worker")
    if started:
        store.collection_launch(row["task_id"], "worker")
    if system_fault:
        store.system_fault(row["task_id"], "worker", "fixture_system_fault")
    if cancelled:
        store.cancel(row["task_id"], a)
    else:
        store.hold(row["task_id"], "failed_pending_stop")
    worker = worker_for(creation)
    with execution_lock(creation.db_path, row["task_id"]):
        reconcile(worker)
        assert store.summary(a)["used"] == 1
        assert store.summary(a)["active_task"] is not None
    reconcile(worker)
    reconcile(worker)
    assert store.summary(a)["used"] == (0 if refunded else 1)
    assert store.summary(a)["refunded"] == int(refunded)
    assert store.summary(a)["active_task"] is None
    assert store.for_task(other["task_id"])["state"] == "RESERVED"


def test_synchronous_first_spawn_failure_refunds_but_later_spawn_failure_does_not(
    stack, monkeypatch
):
    from backend.task_admission.execution import execution_context, launch_crawler

    creation, _, (a, b) = stack
    store = creation.admission

    def reject(*args, **kwargs):
        raise OSError("fixture: no process created")

    monkeypatch.setattr(subprocess, "Popen", reject)
    for owner, previous_launch in ((a, False), (b, True)):
        row = enqueue(store, owner, owner)
        store.start(row["task_id"], "worker")
        if previous_launch:
            store.collection_launch(row["task_id"], "worker")
        with execution_context(store, row["task_id"], "worker"):
            with pytest.raises(OSError):
                launch_crawler(["fixture-command"])
        assert store.summary(owner)["used"] == 1  # not refunded before stopped
        store.settle(row["task_id"], stopped=True)
        assert store.summary(owner)["used"] == int(previous_launch)


def test_cross_day_refund_belongs_to_original_charge_date(stack):
    creation, _, (a, _) = stack
    store = creation.admission
    now = datetime(2026, 9, 20, 15, 59, tzinfo=timezone.utc)
    store.clock = lambda: now
    row = enqueue(store, a)
    store.start(row["task_id"], "worker")
    store.system_fault(row["task_id"], "worker", "job_creation_failed")
    now += timedelta(minutes=2)
    store.settle(row["task_id"], stopped=True)
    assert store.summary(a)["remaining"] == 3
    assert store.summary(a)["refunded"] == 0
    refunded = store.for_task(row["task_id"])
    assert refunded["day"] == "2026-09-20"
    assert refunded["charge_state"] == "REFUNDED"


def test_manual_report_recovery_cannot_reopen_new_policy_terminal_task(stack):
    creation, _, (a, _) = stack
    run = make_run(creation, a, "report-failure-v2")
    reports = FakeReportAdapter()
    reports.after_started = lambda: (_ for _ in ()).throw(
        RuntimeError("provider failed")
    )
    worker_for(creation, reports=reports).run_once()
    row = creation.admission.for_task(run.id)
    assert row["state"] == "RELEASED"
    assert row["charge_state"] == "CHARGED"
    job = {"id": row["job_id"], "owner_user_id": a, "analyze_limit": 1}
    for action in (
        "resume_crawl",
        "resume_analysis",
        "backfill_analysis",
        "resume_report",
    ):
        with pytest.raises(AdmissionError) as exc:
            enqueue_resume(creation.admission, job, owner=a, action=action, key=action)
        assert exc.value.code == "TASK_ALREADY_ENDED"
    assert creation.admission.summary(a)["used"] == 1


def test_job_creation_system_failure_refunds_after_reconciliation(stack):
    creation, _, (a, _) = stack
    run = make_run(creation, a, "materialize-failure")
    execution = FakeExecutionAdapter()
    execution.ensure_job = lambda run: (_ for _ in ()).throw(
        OSError("fixture disk full")
    )
    worker_for(creation, execution).run_once()
    row = creation.admission.for_task(run.id)
    assert row["state"] == "RELEASED"
    assert row["charge_state"] == "REFUNDED"
    assert execution.pipeline_calls == 0


def test_quota_migration_keeps_old_records_explicitly_versioned(stack):
    from backend.task_admission.store import AdmissionStore

    creation, _, (a, b) = stack
    store = creation.admission
    row = enqueue(store, a)
    store.settle(row["task_id"], stopped=True)
    # Recreate the exact pre-v2 schema by dropping only the additive fields.
    with store.connect() as db:
        for name in (
            "quota_policy",
            "charge_state",
            "collection_phase",
            "system_failure",
            "end_requested_at",
        ):
            db.execute(f"ALTER TABLE task_admissions DROP COLUMN {name}")
    upgraded = AdmissionStore(store.db_path, auth_db=store.auth_db, enabled=True)
    old = upgraded.for_task(row["task_id"])
    assert (old["quota_policy"], old["charge_state"], old["collection_phase"]) == (
        "report_v1",
        "LEGACY",
        "UNKNOWN",
    )
    assert upgraded.summary(a)["remaining"] == 3
    assert upgraded.summary(a)["legacy_record_count"] == 1
    new = enqueue(upgraded, b, "new-policy")
    assert (new["quota_policy"], new["charge_state"]) == ("accepted_v2", "CHARGED")


def test_paused_cross_day_resume_reuses_original_charge(stack):
    creation, _, (a, _) = stack
    admission = creation.admission
    now = datetime(2026, 9, 20, 15, 59, tzinfo=timezone.utc)
    admission.clock = lambda: now
    row = enqueue(admission, a)
    admission.hold(row["task_id"], "paused")
    now += timedelta(minutes=2)
    resumed = enqueue_resume(admission, {"id": row["job_id"], "owner_user_id": a}, owner=a,
                             action="resume_analysis", key="next-day-resume")
    assert resumed["id"] == row["id"]
    assert resumed["day"] == "2026-09-20"
    assert admission.summary(a)["used"] == 0
    assert admission.summary(a)["active_task"] is not None
    with admission.connect() as db:
        assert db.execute("SELECT count(*) FROM task_admissions WHERE charge_state='CHARGED'").fetchone()[0] == 1


def test_end_api_waits_for_execution_and_disables_resume_before_delete(stack, tmp_path, monkeypatch):
    from backend.application_auth.service import ApplicationAuthService
    from backend.audit_agent.job_store import JobStore
    from backend.audit_agent.ingestion import IngestionStore
    from backend.investigation_creation.principal import Principal
    from backend.task_admission.recovery import reconcile
    from fastapi import BackgroundTasks, HTTPException

    creation, auth, (a, _) = stack
    jobs = JobStore(tmp_path / "jobs.sqlite3")
    ingestion = IngestionStore(tmp_path / "jobs.sqlite3")
    row = enqueue(creation.admission, a)
    jobs.create(job_id=row["job_id"], owner_user_id=a, platform="dy", status="analysis_paused",
                crawl_status="completed", analysis_status="paused")
    monkeypatch.setattr(main, "job_store", jobs)
    monkeypatch.setattr(main, "ingestion_store", ingestion)
    monkeypatch.setattr(main, "investigation_creation_store", creation)
    monkeypatch.setattr(main, "auth_store", auth)
    monkeypatch.setattr(main, "auth_service", ApplicationAuthService(auth))
    monkeypatch.setattr(main, "audit_result_store", SimpleNamespace(delete_for_job=lambda _: 0))
    principal = Principal(a)
    assert main.enrich_job(jobs.get(row["job_id"]))["available_actions"]["end_task"]
    with pytest.raises(HTTPException):
        main.delete_job(row["job_id"], principal)
    assert main.cancel_admitted_task(row["task_id"], principal)["state"] == "RESERVED"
    assert main.enrich_job(jobs.get(row["job_id"]))["available_actions"]["ending"]
    worker = worker_for(creation, SimpleNamespace(job_store=jobs))
    with execution_lock(creation.db_path, row["task_id"]):
        reconcile(worker)
        with pytest.raises(HTTPException):
            main.delete_job(row["job_id"], principal)
    reconcile(worker)
    ended = main.enrich_job(jobs.get(row["job_id"]))
    assert ended["available_actions"]["ended"]
    assert ended["analysis_status"] == "stopped"
    assert not ended["available_actions"].get("resume_analysis")
    with pytest.raises(AdmissionError):
        main.control_job(row["job_id"], main.JobControlRequest(action="resume_analysis"), BackgroundTasks(), principal, idempotency_key="late")
    assert main.delete_job(row["job_id"], principal)["ok"]
    assert creation.admission.summary(a)["used"] == 1


def test_crawler_preparation_io_fault_refunds_only_before_any_spawn(stack, tmp_path, monkeypatch):
    from backend.audit_agent.crawler_adapter import MediaCrawlerAdapter
    from backend.task_admission.execution import execution_context
    creation, _, (a, b) = stack
    store = creation.admission
    monkeypatch.setattr(settings, "crawler_browser_profile_root", tmp_path / "profiles")
    monkeypatch.setattr(settings, "task_resource_lock_dir", tmp_path / "locks")
    # A file where a directory is required causes a real pre-spawn OS failure.
    blocked_output = tmp_path / "output-is-file"
    blocked_output.write_text("fixture")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    adapter = MediaCrawlerAdapter(tmp_path)
    for owner, previous in ((a, False), (b, True)):
        row = enqueue(store, owner, owner)
        store.start(row["task_id"], "worker")
        if previous:
            store.collection_launch(row["task_id"], "worker")
        with execution_context(store, row["task_id"], "worker"):
            with pytest.raises(OSError):
                adapter._run_command(["fixture"], blocked_output, "dy", 1, None, None, account_id="fixture-account")
        store.settle(row["task_id"], stopped=True)
        assert store.summary(owner)["used"] == int(previous)


def test_last_daily_charge_cannot_be_reused_by_racing_failed_tasks(stack):
    creation, _, (owner, _) = stack
    store = creation.admission
    for i in range(2):
        row = enqueue(store, owner, f"earlier-{i}")
        store.start(row["task_id"], f"worker-{i}")
        store.collection_launch(row["task_id"], f"worker-{i}")
        store.settle(row["task_id"], stopped=True, reason="failed")
    assert store.summary(owner)["used"] == 2
    barrier = Barrier(8)

    def accept_then_fail(i):
        barrier.wait()
        try:
            row = enqueue(store, owner, f"racing-{i}")
        except AdmissionError as exc:
            return exc.code
        store.start(row["task_id"], f"racing-worker-{i}")
        store.collection_launch(row["task_id"], f"racing-worker-{i}")
        # Even releasing the unfinished position immediately must not let the
        # next concurrent submitter reuse this last charge.
        store.settle(row["task_id"], stopped=True, reason="failed")
        return "accepted"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(accept_then_fail, range(8)))
    assert outcomes.count("accepted") == 1
    assert set(outcomes) <= {"accepted", "DAILY_REPORT_LIMIT", "USER_TASK_LIMIT"}
    assert store.summary(owner)["used"] == 3
    assert store.summary(owner)["remaining"] == 0
    assert store.summary(owner)["active_task"] is None
    with pytest.raises(AdmissionError) as exc:
        enqueue(store, owner, "fourth-after-all-failed")
    assert exc.value.code == "DAILY_REPORT_LIMIT"


@pytest.mark.parametrize("outcome", ["published", "failed", "cancelled", "refunded"])
def test_lost_response_replay_after_restart_and_next_day_never_recharges(stack, outcome):
    from backend.task_admission.store import AdmissionStore

    creation, _, (owner, _) = stack
    store = creation.admission
    now = datetime(2026, 9, 20, 15, 59, tzinfo=timezone.utc)
    store.clock = lambda: now
    original = enqueue(store, owner, "lost-response")
    # Reopening the store models replay after a committed acceptance whose
    # response was lost, without a worker having started yet.
    reopened = AdmissionStore(store.db_path, auth_db=store.auth_db, enabled=True, clock=lambda: now)
    assert enqueue(reopened, owner, "lost-response")["id"] == original["id"]
    assert reopened.summary(owner)["used"] == 1
    if outcome == "published":
        reopened.settle(original["task_id"], report_id="fixture-report")
    else:
        reopened.start(original["task_id"], "worker")
        if outcome == "cancelled":
            reopened.cancel(original["task_id"], owner)
        elif outcome == "refunded":
            reopened.system_fault(original["task_id"], "worker", "job_creation_failed")
        else:
            reopened.collection_launch(original["task_id"], "worker")
        reopened.settle(original["task_id"], stopped=True, reason=outcome)
    finished = reopened.for_task(original["task_id"])
    now += timedelta(minutes=2)
    replay = enqueue(reopened, owner, "lost-response")
    assert replay["id"] == original["id"]
    assert replay["state"] == finished["state"]
    assert replay["charge_state"] == ("REFUNDED" if outcome == "refunded" else "CHARGED")
    assert reopened.summary(owner)["used"] == 0
    assert reopened.summary(owner)["active_task"] is None
    with reopened.connect() as db:
        assert db.execute("SELECT count(*) FROM task_admissions").fetchone()[0] == 1
