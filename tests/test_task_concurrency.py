"""Real process overlap and isolation, without platform requests or credentials."""

import subprocess
import sys
import time
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.audit_agent.config import settings
from backend.audit_agent.crawler_adapter import MediaCrawlerAdapter
from backend.investigation_creation.contracts import (
    InvestigationConfiguration,
    RunStatus,
)
from backend.investigation_creation.store import InvestigationCreationStore
from backend.task_admission.execution import crawler_fds, execution_lock
from backend.task_admission.resources import (
    account_lease,
    acquire_account_handle,
    analysis_capacity,
    capacity_lease,
    selected_account,
)
from test_task_admission import stack, worker_for
from test_investigation_creation_m3 import (
    FakeConfigurationResolver,
    FakeExecutionAdapter,
    FakeReportAdapter,
    configuration_payload,
)


@pytest.fixture(autouse=True)
def resources(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "task_resource_lock_dir", tmp_path / "locks")
    monkeypatch.setattr(settings, "crawler_browser_profile_root", tmp_path / "profiles")
    monkeypatch.setattr(settings, "task_execution_capacity", 2)


def create_run(creation, auth, owner, key, account):
    auth.grant_resource(
        user_id=owner,
        resource_type="crawler-account",
        resource_id=account,
        permission="use",
        actor_user_id="admin",
    )
    payload = configuration_payload(keyword=key)
    payload["platform"] = "dy"
    config = InvestigationConfiguration.model_validate(payload)
    draft = creation.create_draft(
        principal=owner,
        title=key,
        objective="Inspect this subject",
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


def account_pool():
    return SimpleNamespace(
        available_accounts=lambda platform: [
            {"id": "account-a", "platform": "dy"},
            {"id": "account-b", "platform": "dy"},
        ]
    )


def wait_file(path, futures=()):
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        for future in futures:
            if future.done():
                future.result()
        time.sleep(0.02)
    assert path.exists(), f"process did not enter collection: {path}"


def test_two_users_have_overlapping_crawler_processes_and_cancel_is_scoped(
    stack, tmp_path
):
    creation, auth, (a, b) = stack
    run_a = create_run(creation, auth, a, "A", "account-a")
    run_b = create_run(creation, auth, b, "B", "account-b")
    script = tmp_path / "crawl.py"
    script.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "root=Path(sys.argv[1]); account=sys.argv[2]\n"
        "(root/(account+'.started')).write_text(str(time.monotonic()))\n"
        "deadline=time.monotonic()+15\n"
        "while not (root/(account+'.release')).exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        "(root/(account+'.ended')).write_text(str(time.monotonic()))\n"
    )

    class Execution(FakeExecutionAdapter):
        def __init__(self):
            super().__init__()
            self.crawler_account_store = account_pool()

        def run_pipeline(self, job_id, configuration):
            account = selected_account()
            assert account in {"account-a", "account-b"}
            adapter = MediaCrawlerAdapter(tmp_path)
            adapter._run_command(
                [sys.executable, str(script), str(tmp_path), account],
                tmp_path / job_id,
                "dy",
                1,
                None,
                None,
                account_id=account,
                stop_checker=lambda: (
                    creation.admission.for_task(job_id)["decision"] == "CANCELLED"
                ),
            )
            super().run_pipeline(job_id, configuration)

    workers = [
        worker_for(InvestigationCreationStore(creation.db_path), Execution())
        for _ in range(2)
    ]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker.run_once) for worker in workers]
            wait_file(tmp_path / "account-a.started", futures)
            wait_file(tmp_path / "account-b.started", futures)
            before_b = creation.admission.for_task(run_b.id)
            assert before_b["queue_state"] == "RUNNING"
            # Expired heartbeats cannot reclaim either live task lock.
            with creation._connect() as db:
                db.execute("UPDATE investigation_runs SET heartbeat_at=''")
            observer = worker_for(creation)
            assert observer.run_once() is None
            assert (
                creation.admission.for_task(run_b.id)["execution_token"]
                == before_b["execution_token"]
            )
            creation.admission.cancel(run_a.id, a)
            deadline = time.monotonic() + 10
            while (
                creation.admission.summary(a)["reserved"]
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert creation.admission.summary(a)["reserved"] == 0
            assert creation.admission.summary(b)["reserved"] == 1
            assert not (tmp_path / "account-b.ended").exists()
            (tmp_path / "account-b.release").touch()
            [future.result(timeout=10) for future in futures]
    finally:
        (tmp_path / "account-a.release").touch()
        (tmp_path / "account-b.release").touch()
    assert creation.get_run_for_worker(run_b.id).status == RunStatus.PUBLISHED
    assert creation.admission.summary(b)["completed"] == 1
    assert creation.admission.summary(a)["completed"] == 0
    assert float((tmp_path / "account-a.started").read_text()) < float(
        (tmp_path / "account-b.ended").read_text()
    )


def test_busy_account_queues_without_consuming_execution_or_refunding(stack):
    creation, auth, (a, _) = stack
    run = create_run(creation, auth, a, "busy", "account-a")
    execution = FakeExecutionAdapter()
    execution.crawler_account_store = account_pool()
    worker = worker_for(creation, execution)
    # Raw handle models another process; not reentrant in this context.
    handle = acquire_account_handle("dy", "account-a")
    try:
        assert worker.run_once() is None
        row = creation.admission.for_task(run.id)
        assert row["waiting_reason"] == "account_busy"
        assert row["queue_state"] == "QUEUED"
        assert creation.admission.summary(a)["reserved"] == 1
        assert execution.pipeline_calls == 0
    finally:
        handle.close()
    assert worker.run_once().status == RunStatus.PUBLISHED


def test_capacity_queue_is_separate_from_account_availability(stack, monkeypatch):
    creation, auth, (a, _) = stack
    run = create_run(creation, auth, a, "capacity", "account-a")
    execution = FakeExecutionAdapter()
    execution.crawler_account_store = account_pool()
    worker = worker_for(creation, execution)
    worker.execution_capacity = 1
    with capacity_lease("execution", 1) as locked:
        assert locked
        assert worker.run_once() is None
    assert creation.admission.for_task(run.id)["waiting_reason"] == "system_capacity"
    assert worker.run_once().status == RunStatus.PUBLISHED


def test_orphan_child_retains_only_its_task_account_and_capacity(stack, tmp_path):
    creation, auth, (a, b) = stack
    run_a = create_run(creation, auth, a, "orphan-a", "account-a")
    run_b = create_run(creation, auth, b, "independent-b", "account-b")
    with (
        execution_lock(creation.db_path, run_a.id) as locked,
        account_lease("dy", "account-a") as account,
        capacity_lease("execution", 2) as capacity,
    ):
        assert locked and account and capacity
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
            pass_fds=crawler_fds(),
        )
    try:
        creation.admission.cancel(run_a.id, a)
        worker = worker_for(creation)
        assert worker.run_once().id == run_b.id
        assert creation.admission.summary(a)["reserved"] == 1
        assert creation.admission.summary(b)["completed"] == 1
        assert acquire_account_handle("dy", "account-a") is None
        handle = acquire_account_handle("dy", "account-b")
        assert handle is not None
        handle.close()
    finally:
        child.communicate(b"", timeout=5)
    worker.run_once()
    assert creation.admission.summary(a)["reserved"] == 0


def test_analysis_capacity_can_cancel_while_other_task_runs(monkeypatch):
    from backend.task_admission.resources import ResourceBusy

    monkeypatch.setattr(settings, "task_analysis_capacity", 1)
    with capacity_lease("analysis", 1) as acquired:
        assert acquired
        with pytest.raises(ResourceBusy):
            with analysis_capacity(lambda: True):
                pytest.fail("cancelled analysis should not run")
    with analysis_capacity():
        with capacity_lease("analysis", 1) as second:
            assert not second


def test_login_and_maintenance_cannot_enter_an_account_used_by_collection(
    stack, tmp_path, monkeypatch
):
    from backend import main
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_login_manager import CrawlerAccountLoginManager
    from backend.investigation_creation.principal import Principal
    from fastapi import HTTPException

    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    account = store.create(platform="dy", display_name="test")
    helper = tmp_path / "login.py"
    helper.write_text("raise AssertionError('must not open login while collecting')")
    manager = CrawlerAccountLoginManager(
        store=store,
        cipher=AuthStateCipher(key_file=tmp_path / "key"),
        python_path=Path(sys.executable),
        helper_path=helper,
    )
    handle = acquire_account_handle("dy", account["id"])
    monkeypatch.setattr(
        main, "_require_crawler_account", lambda *args, **kwargs: account
    )
    monkeypatch.setattr(main, "crawler_account_store", store)
    monkeypatch.setattr(settings, "crawler_login_interactive", False)
    try:
        with pytest.raises(ValueError, match="正在采集或登录"):
            manager.start(account)
        with pytest.raises(HTTPException) as blocked:
            main.delete_crawler_account(account["id"], Principal("admin", role="admin"))
        assert blocked.value.status_code == 409
        assert store.get(account["id"]) is not None
    finally:
        handle.close()


def test_queued_task_loses_permission_without_borrowing_another_users_account(stack):
    creation, auth, (a, _) = stack
    run = create_run(creation, auth, a, "no-grant", "account-a")
    with creation.admission.connect() as db:
        db.execute(
            "UPDATE resource_grants SET revoked_at='2026-09-20' WHERE user_id=?", (a,)
        )
    execution = FakeExecutionAdapter()
    execution.crawler_account_store = account_pool()
    worker = worker_for(creation, execution)
    assert worker.run_once() is None
    assert execution.pipeline_calls == 0
    assert (
        creation.admission.for_task(run.id)["waiting_reason"]
        == "no_available_authorized_account"
    )
    assert creation.admission.summary(a)["reserved"] == 1


@pytest.mark.parametrize("status", ["REPORT_GENERATING", "RUNNING"])
def test_report_recovery_does_not_wait_for_a_crawler_account(stack, status):
    from backend.task_admission.scheduler import select_account

    creation, auth, (a, _) = stack
    run = create_run(creation, auth, a, "report-only", "account-a")
    with creation._connect() as db:
        db.execute(
            "UPDATE investigation_runs SET status=?,job_id='recovery-job' WHERE id=?",
            (status, run.id),
        )
    execution = FakeExecutionAdapter()
    execution.jobs["recovery-job"] = {"status": "completed"}
    execution.crawler_account_store = SimpleNamespace(available_accounts=lambda _: [])
    with select_account(
        worker_for(creation, execution), creation.admission.for_task(run.id)
    ) as ready:
        assert ready


def test_running_task_rechecks_its_allocated_account_grant(stack):
    from backend.task_admission.execution import execution_context, assert_execution
    from backend.task_admission.store import AdmissionError

    creation, auth, (a, _) = stack
    run = create_run(creation, auth, a, "revoked-running", "account-a")
    creation.admission.bind_job(run.id, "job-running")
    with creation.admission.connect() as db:
        db.execute(
            "UPDATE task_admissions SET resource_account_id='account-a' WHERE task_id=?",
            (run.id,),
        )
    creation.admission.start(run.id, "token")
    with execution_context(creation.admission, run.id, "token"):
        assert_execution("job-running")
        with creation.admission.connect() as db:
            db.execute(
                "UPDATE resource_grants SET revoked_at='2026-09-20' WHERE user_id=?",
                (a,),
            )
        with pytest.raises(AdmissionError) as error:
            assert_execution("job-running")
        assert error.value.code == "CRAWLER_ACCOUNT_FORBIDDEN"


def test_worker_supervisor_replaces_crash_and_drains_group_signal(tmp_path):
    """Real spawn and group signals catch Event-lock deadlocks in handlers."""
    env = dict(os.environ)
    for name, suffix in (
        ("XHS_AUDIT_DATA_DIR", "data"),
        ("XHS_AUDIT_OUTPUTS_DIR", "outputs"),
        ("APP_AUTH_DB", "data/investigation_creation.sqlite3"),
        ("CRAWLER_BROWSER_PROFILE_ROOT", "profiles"),
        ("TASK_RESOURCE_LOCK_DIR", "locks"),
        ("HERMES_HOME", "hermes"),
    ):
        env[name] = str(tmp_path / suffix)
    env["APP_AUTH_MODE"] = "required"
    env["CORS_ALLOW_ORIGINS"] = ""

    def children(pid):
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            check=True,
        )
        return {
            int(parts[0])
            for line in result.stdout.splitlines()
            if len(parts := line.strip().split(None, 2)) == 3
            and int(parts[1]) == pid
            and "spawn_main" in parts[2]
        }

    def wait_children(parent, excluded=None):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = children(parent)
            if len(current) == 2 and excluded not in current:
                return current
            time.sleep(0.1)
        pytest.fail("supervisor did not start two workers")

    with (tmp_path / "supervisor.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "backend.investigation_creation.worker",
                "--workers",
                "2",
                "--poll-seconds",
                ".1",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            initial = wait_children(process.pid)
            time.sleep(2)
            dead = next(iter(initial))
            os.kill(dead, signal.SIGKILL)
            replaced = wait_children(process.pid, dead)
            assert len(replaced - initial) == 1
            time.sleep(2)
            os.killpg(process.pid, signal.SIGTERM)
            assert process.wait(timeout=15) == 0
            assert not children(process.pid)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
