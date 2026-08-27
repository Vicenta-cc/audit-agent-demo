from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = _REPOSITORY_ROOT / "data"
_DEFAULT_OUTPUTS_DIR = _REPOSITORY_ROOT / "outputs"


def _directory_fingerprint(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


_DEFAULT_DIRECTORY_STATE = {
    "data": _directory_fingerprint(_DEFAULT_DATA_DIR),
    "outputs": _directory_fingerprint(_DEFAULT_OUTPUTS_DIR),
}
_MODULE_ISOLATION_ROOT = Path(tempfile.mkdtemp(prefix="m3-review-gate-tests-"))
os.environ["XHS_AUDIT_DATA_DIR"] = str(_MODULE_ISOLATION_ROOT / "data")
os.environ["XHS_AUDIT_OUTPUTS_DIR"] = str(_MODULE_ISOLATION_ROOT / "outputs")
atexit.register(shutil.rmtree, _MODULE_ISOLATION_ROOT, True)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.investigation_creation import create_investigation_creation_router
from backend.audit_agent.audit_policy_store import (
    AuditPolicyStore,
    TaskAuditConfigRevisionStore,
)
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.lexicon_store import LexiconStore
from backend.investigation.contracts import PublishedReportContext
from backend.investigation.store import InvestigationStore
from backend.investigation_creation.adapters import (
    AuditPipelineExecutionAdapter,
    InvestigationConfigurationResolver,
    R31ReportAdapter,
)
from backend.investigation_creation.contracts import (
    ConfirmAndQueueCommand,
    CreateDraftCommand,
    InvestigationConfiguration,
    RunStatus,
    UpdateDraftCommand,
)
from backend.investigation_creation.errors import (
    ConfirmationRequiredError,
    IdempotencyConflictError,
    PrincipalAccessDeniedError,
)
from backend.investigation_creation.principal import (
    LOCAL_PRINCIPAL_ID,
    LocalPrincipalProvider,
    Principal,
)
from backend.investigation_creation.service import InvestigationCreationService
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.worker import InvestigationWorker
from backend.reporting.runtime import R31ReportRuntime


LOCAL = Principal(LOCAL_PRINCIPAL_ID)
OTHER = Principal("other-local-profile")


def configuration_payload(*, keyword: str = "subject") -> dict:
    return {
        "platform": "xhs",
        "collection": {
            "crawl_mode": "search",
            "keyword_source": "keyword",
            "keywords": [keyword],
            "run_crawler": True,
        },
        "analysis": {
            "library_ids": ["soft"],
            "capabilities": ["text", "comment"],
            "scoring_template": "balanced",
        },
    }


def creator_configuration_payload(
    *,
    creator_url: str = (
        "https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753"
        "?xsec_token=test-token&xsec_source=pc_user"
    ),
    platform: str = "xhs",
) -> dict:
    return {
        "platform": platform,
        "collection": {
            "crawl_mode": "creator",
            "keyword_source": "keyword",
            "keywords": [],
            "creator_url": creator_url,
            "run_crawler": True,
        },
        "analysis": {
            "library_ids": ["soft"],
            "capabilities": ["text", "comment"],
            "scoring_template": "balanced",
        },
    }


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 27, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeConfigurationResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, configuration: InvestigationConfiguration):
        self.calls += 1
        collection = configuration.collection
        analysis = configuration.analysis
        capabilities = [item.value for item in analysis.capabilities] or ["text"]
        revision = {
            "source_policy_id": analysis.policy_id,
            "source_policy_name": "Test policy",
            "source_policy_version": "v1",
            "audit_config": {},
            "knowledge_package_snapshots": [],
            "rule_snapshot": {},
            "prompt_profile_snapshot": {},
            "config_hash": "test-config-hash",
        }
        return {
            "platform": configuration.platform.value,
            "display_name": collection.display_name,
            "crawl_mode": collection.crawl_mode.value,
            "keyword": ",".join(collection.keywords),
            "keyword_source": collection.keyword_source.value,
            "lexicon_category": analysis.library_ids[0],
            "library_ids": list(analysis.library_ids),
            "capabilities": capabilities,
            "scoring_template": analysis.scoring_template.value,
            "rule_snapshot": {},
            "lexicon_keywords": [],
            "creator_url": collection.creator_url,
            "creator_id": collection.creator_url,
            "start_page": collection.start_page,
            "max_notes": collection.max_notes,
            "max_comments": collection.max_comments,
            "max_concurrency": collection.max_concurrency,
            "max_items_per_minute": collection.max_items_per_minute,
            "crawler_account_id": collection.crawler_account_id,
            "crawler_account_display_name": "",
            "get_sub_comment": collection.get_sub_comment,
            "analyze_limit": analysis.analyze_limit,
            "run_crawler": collection.run_crawler,
            "source_output_id": collection.source_output_id,
            "analysis_batch_size": analysis.analysis_batch_size,
            "prompt_profile_snapshot": {},
            "policy_id": analysis.policy_id,
            "audit_config_revision": revision,
        }


class FakeExecutionAdapter:
    def __init__(self, final_state: dict | None = None) -> None:
        self.final_state = final_state or self.completed_state()
        self.ensure_calls = 0
        self.pipeline_calls = 0
        self.jobs: dict[str, dict] = {}
        self.crash_in_pipeline = False

    @staticmethod
    def completed_state(*, pending: int = 0, analyzing: int = 0) -> dict:
        return {
            "status": "completed",
            "error": "",
            "task_stats": {
                "pending_analysis_count": pending,
                "analyzing_count": analyzing,
                "completed_analysis_count": 1,
            },
        }

    @staticmethod
    def job_id_for_run(run_id: str) -> str:
        return f"job:{run_id[-12:]}"

    def ensure_job(self, run) -> str:
        self.ensure_calls += 1
        job_id = self.job_id_for_run(run.id)
        self.jobs.setdefault(
            job_id,
            {"status": "queued", "error": "", "task_stats": {}},
        )
        return job_id

    def run_pipeline(self, job_id: str, configuration: dict) -> None:
        self.pipeline_calls += 1
        if self.crash_in_pipeline:
            self.jobs[job_id] = {
                "status": "running",
                "error": "",
                "task_stats": {},
            }
            raise SimulatedWorkerCrash()
        self.jobs[job_id] = dict(self.final_state)

    def get_job_state(self, job_id: str):
        state = self.jobs.get(job_id)
        return dict(state) if state is not None else None


class FakeReportAdapter:
    def __init__(self) -> None:
        self.generate_calls = 0
        self.find_calls = 0
        self.verify_calls = 0
        self.published_by_task: dict[str, tuple[str, str]] = {}
        self.after_started = None
        self.after_published = None
        self.error_after_publish: BaseException | None = None

    def find_published(self, task_id: str, *, r31_run_id: str = ""):
        self.find_calls += 1
        existing = self.published_by_task.get(task_id)
        if existing is None:
            return None
        if r31_run_id and existing[0] != r31_run_id:
            raise RuntimeError("generation mismatch")
        return existing[1]

    def generate(self, task_id: str, *, on_generation_started):
        self.generate_calls += 1
        r31_run_id = f"report-run:{self.generate_calls:032x}"
        version_id = f"report-version:{self.generate_calls:032x}"
        on_generation_started(r31_run_id, version_id)
        if self.after_started is not None:
            self.after_started()
        self.published_by_task[task_id] = (r31_run_id, version_id)
        if self.after_published is not None:
            self.after_published()
        if self.error_after_publish is not None:
            raise self.error_after_publish
        return version_id

    def verify_published(self, report_version_id: str, *, task_id: str) -> None:
        self.verify_calls += 1
        existing = self.published_by_task.get(task_id)
        if existing is None or existing[1] != report_version_id:
            raise RuntimeError("not published")


class FakeSessionAdapter:
    def __init__(self, *, failures_remaining: int = 0) -> None:
        self.calls = 0
        self.sessions: dict[str, tuple[str, str]] = {}
        self.failures_remaining = failures_remaining

    def ensure_session(self, run_id: str, report_version_id: str) -> str:
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("temporary Session creation failure")
        existing = self.sessions.get(run_id)
        if existing:
            if existing[1] != report_version_id:
                raise RuntimeError("session rebound")
            return existing[0]
        session_id = f"investigation-session:{len(self.sessions) + 1:032x}"
        self.sessions[run_id] = (session_id, report_version_id)
        return session_id


class SimulatedWorkerCrash(BaseException):
    pass


class M3TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.clock = MutableClock()
        self.store = InvestigationCreationStore(
            Path(self.temp_dir.name) / "m3.sqlite3", clock=self.clock
        )
        self.resolver = FakeConfigurationResolver()
        self.service = InvestigationCreationService(
            self.store, configuration_resolver=self.resolver
        )

    def create_draft(self, *, principal: Principal = LOCAL, keyword: str = "subject"):
        return self.service.create_draft(
            CreateDraftCommand(
                title="Investigate subject",
                objective="Find and assess relevant public content.",
                configuration=configuration_payload(keyword=keyword),
            ),
            principal=principal,
        )

    def queue_run(
        self,
        *,
        principal: Principal = LOCAL,
        key: str = "confirm:1",
        keyword: str = "subject",
    ):
        draft = self.create_draft(principal=principal, keyword=keyword)
        run = self.service.confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key=key,
            ),
            principal=principal,
        )
        return draft, run

    def worker(
        self,
        execution: FakeExecutionAdapter,
        report: FakeReportAdapter | None = None,
        session: FakeSessionAdapter | None = None,
        *,
        worker_id: str = "worker:1",
        hook=None,
        heartbeat_interval_seconds: float = 0.05,
    ) -> InvestigationWorker:
        return InvestigationWorker(
            self.store,
            execution_adapter=execution,
            report_adapter=report or FakeReportAdapter(),
            session_adapter=session or FakeSessionAdapter(),
            worker_id=worker_id,
            lease_timeout_seconds=10,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            lifecycle_hook=hook,
        )


class OwnershipAndConfigurationTest(M3TestCase):
    def test_cross_principal_get_patch_confirm_and_run_get_are_rejected(self):
        draft = self.create_draft()
        with self.assertRaises(PrincipalAccessDeniedError):
            self.service.get_draft(draft.id, principal=OTHER)
        with self.assertRaises(PrincipalAccessDeniedError):
            self.service.update_draft(
                UpdateDraftCommand(
                    draft_id=draft.id,
                    expected_revision=1,
                    objective="Unauthorized update",
                ),
                principal=OTHER,
            )
        with self.assertRaises(PrincipalAccessDeniedError):
            self.service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=draft.id,
                    expected_revision=1,
                    confirmed=True,
                    idempotency_key="other:confirm",
                ),
                principal=OTHER,
            )
        run = self.service.confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key="owner:confirm",
            ),
            principal=LOCAL,
        )
        with self.assertRaises(PrincipalAccessDeniedError):
            self.service.get_run(run.id, principal=OTHER)

    def test_same_idempotency_key_with_different_fingerprint_conflicts(self):
        first_draft, first_run = self.queue_run(key="same-key")
        replay = self.service.confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=first_draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key="same-key",
            ),
            principal=LOCAL,
        )
        self.assertEqual(first_run.id, replay.id)

        second_draft = self.create_draft(keyword="other")
        with self.assertRaises(IdempotencyConflictError):
            self.service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=second_draft.id,
                    expected_revision=1,
                    confirmed=True,
                    idempotency_key="same-key",
                ),
                principal=LOCAL,
            )
        with self.assertRaises(IdempotencyConflictError):
            self.service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=first_draft.id,
                    expected_revision=1,
                    confirmed=False,
                    idempotency_key="same-key",
                ),
                principal=LOCAL,
            )

    def test_same_idempotency_key_cannot_replay_another_principals_run(self):
        self.queue_run(principal=LOCAL, key="principal-scoped-key")
        other_draft = self.create_draft(principal=OTHER, keyword="other-owner")
        with self.assertRaises(IdempotencyConflictError):
            self.service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=other_draft.id,
                    expected_revision=1,
                    confirmed=True,
                    idempotency_key="principal-scoped-key",
                ),
                principal=OTHER,
            )

    def test_confirm_transaction_failure_leaves_no_partial_run_or_draft_state(self):
        class FailingConfirmationStore(InvestigationCreationStore):
            def _record_idempotency_key(self, *args, **kwargs):
                raise RuntimeError("injected idempotency write failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = FailingConfirmationStore(Path(temp_dir) / "m3.sqlite3")
            service = InvestigationCreationService(
                store, configuration_resolver=FakeConfigurationResolver()
            )
            draft = service.create_draft(
                CreateDraftCommand(
                    title="Atomic confirmation",
                    objective="Rollback every confirmation write.",
                    configuration=configuration_payload(),
                ),
                principal=LOCAL,
            )
            with self.assertRaisesRegex(RuntimeError, "injected"):
                service.confirm_and_queue(
                    ConfirmAndQueueCommand(
                        draft_id=draft.id,
                        expected_revision=1,
                        confirmed=True,
                        idempotency_key="atomic-confirm",
                    ),
                    principal=LOCAL,
                )
            persisted = service.get_draft(draft.id, principal=LOCAL)
            self.assertEqual(persisted.status.value, "DRAFT")
            self.assertIsNone(persisted.confirmed_revision)
            with sqlite3.connect(store.db_path) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM investigation_runs").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM investigation_run_idempotency_keys"
                    ).fetchone()[0],
                    0,
                )

    def test_unconfirmed_new_request_does_not_queue(self):
        draft = self.create_draft()
        with self.assertRaises(ConfirmationRequiredError):
            self.service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=draft.id,
                    expected_revision=1,
                    confirmed=False,
                    idempotency_key="not-confirmed",
                ),
                principal=LOCAL,
            )
        self.assertEqual(
            self.service.get_draft(draft.id, principal=LOCAL).status.value,
            "DRAFT",
        )

    def test_api_uses_server_principal_and_never_calls_pipeline(self):
        app = FastAPI()
        app.include_router(
            create_investigation_creation_router(
                self.service,
                principal_provider=LocalPrincipalProvider(),
            )
        )
        with patch(
            "backend.audit_agent.pipeline.AuditPipeline.run",
            side_effect=AssertionError("HTTP must not execute Pipeline"),
        ) as pipeline_run:
            with TestClient(app) as client:
                actor_response = client.post(
                    "/api/investigation-drafts",
                    json={
                        "title": "Rejected actor",
                        "objective": "Actor is server-owned.",
                        "configuration": configuration_payload(),
                        "created_by": "client-controlled",
                    },
                )
                self.assertEqual(actor_response.status_code, 422)
                created = client.post(
                    "/api/investigation-drafts",
                    json={
                        "title": "HTTP draft",
                        "objective": "Queue only.",
                        "configuration": configuration_payload(keyword="http"),
                    },
                )
                self.assertEqual(created.status_code, 201)
                draft_id = created.json()["id"]
                confirmed = client.post(
                    f"/api/investigation-drafts/{draft_id}/confirm-and-queue",
                    headers={"Idempotency-Key": "http:confirm"},
                    json={"expected_revision": 1, "confirmed": True},
                )
        self.assertEqual(confirmed.status_code, 202)
        self.assertEqual(confirmed.json()["status"], "QUEUED")
        pipeline_run.assert_not_called()

    def test_invalid_configuration_is_rejected_by_api_and_service(self):
        invalid_payloads = (
            {
                **configuration_payload(),
                "collection": {
                    **configuration_payload()["collection"],
                    "run_crawler": "false",
                },
            },
            {**configuration_payload(), "unexpected": "field"},
            {
                "platforms": ["xhs", "dy"],
                "collection": configuration_payload()["collection"],
                "analysis": configuration_payload()["analysis"],
            },
        )
        app = FastAPI()
        app.include_router(create_investigation_creation_router(self.service))
        with TestClient(app) as client:
            valid = client.post(
                "/api/investigation-drafts",
                json={
                    "title": "Valid before PATCH",
                    "objective": "The update must remain strict.",
                    "configuration": configuration_payload(keyword="patch"),
                },
            )
            self.assertEqual(valid.status_code, 201)
            for index, invalid in enumerate(invalid_payloads):
                with self.subTest(layer="api", index=index):
                    response = client.post(
                        "/api/investigation-drafts",
                        json={
                            "title": "Invalid",
                            "objective": "Must fail.",
                            "configuration": invalid,
                        },
                    )
                    self.assertEqual(response.status_code, 422)
                with self.subTest(layer="api-patch", index=index):
                    response = client.patch(
                        f"/api/investigation-drafts/{valid.json()['id']}",
                        json={
                            "expected_revision": 1,
                            "configuration": invalid,
                        },
                    )
                    self.assertEqual(response.status_code, 422)
                with self.subTest(layer="service", index=index):
                    with self.assertRaises(ValidationError):
                        self.service.create_draft(
                            CreateDraftCommand.model_construct(
                                title="Invalid",
                                objective="Must fail.",
                                configuration=invalid,
                            ),
                            principal=LOCAL,
                        )

    def test_required_text_is_trimmed_before_length_validation(self):
        with self.assertRaises(ValidationError):
            CreateDraftCommand(
                title="   ",
                objective="Objective",
                configuration=configuration_payload(),
            )
        with self.assertRaises(ValidationError):
            ConfirmAndQueueCommand(
                draft_id="   ",
                expected_revision=1,
                confirmed=True,
                idempotency_key="key",
            )
        with self.assertRaises(ValidationError):
            ConfirmAndQueueCommand(
                draft_id="draft",
                expected_revision=1,
                confirmed=True,
                idempotency_key="   ",
            )
        with self.assertRaises(ValueError):
            self.service.get_draft("   ", principal=LOCAL)

        app = FastAPI()
        app.include_router(create_investigation_creation_router(self.service))
        with TestClient(app) as client:
            response = client.post(
                "/api/investigation-drafts",
                json={
                    "title": "   ",
                    "objective": "Objective",
                    "configuration": configuration_payload(),
                },
            )
        self.assertEqual(response.status_code, 422)

    def test_invalid_creator_urls_are_rejected_by_api_service_and_worker(self):
        invalid_urls = (
            "ordinary creator text",
            "https://www.xiaohongshu.com/explore/123?xsec_token=t&xsec_source=s",
            "https://www.douyin.com/user/MS4wLjABAAAA_other",
        )
        app = FastAPI()
        app.include_router(create_investigation_creation_router(self.service))
        with TestClient(app) as client:
            valid = client.post(
                "/api/investigation-drafts",
                json={
                    "title": "Valid creator",
                    "objective": "Exercise creator PATCH validation.",
                    "configuration": creator_configuration_payload(),
                },
            )
            self.assertEqual(valid.status_code, 201)
            for invalid_url in invalid_urls:
                with self.subTest(layer="api", creator_url=invalid_url):
                    response = client.post(
                        "/api/investigation-drafts",
                        json={
                            "title": "Invalid creator",
                            "objective": "Reject the wrong creator identity.",
                            "configuration": creator_configuration_payload(
                                creator_url=invalid_url
                            ),
                        },
                    )
                    self.assertEqual(response.status_code, 422)
                with self.subTest(layer="api-patch", creator_url=invalid_url):
                    response = client.patch(
                        f"/api/investigation-drafts/{valid.json()['id']}",
                        json={
                            "expected_revision": 1,
                            "configuration": creator_configuration_payload(
                                creator_url=invalid_url
                            ),
                        },
                    )
                    self.assertEqual(response.status_code, 422)

        with self.assertRaises(ValidationError):
            self.service.create_draft(
                CreateDraftCommand.model_construct(
                    title="Invalid creator",
                    objective="Service must revalidate.",
                    configuration=creator_configuration_payload(
                        creator_url=invalid_urls[0]
                    ),
                ),
                principal=LOCAL,
            )

        confirm_draft = self.service.create_draft(
            CreateDraftCommand(
                title="Confirm creator",
                objective="Revalidate persisted configuration before resolution.",
                configuration=creator_configuration_payload(),
            ),
            principal=LOCAL,
        )
        with sqlite3.connect(self.store.db_path) as connection:
            configuration = json.loads(
                connection.execute(
                    "SELECT configuration_json FROM investigation_drafts WHERE id = ?",
                    (confirm_draft.id,),
                ).fetchone()[0]
            )
            configuration["collection"]["creator_url"] = invalid_urls[1]
            connection.execute(
                "UPDATE investigation_drafts SET configuration_json = ? WHERE id = ?",
                (json.dumps(configuration), confirm_draft.id),
            )
        resolver_calls = self.resolver.calls
        with self.assertRaises(ValidationError):
            self.service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=confirm_draft.id,
                    expected_revision=1,
                    confirmed=True,
                    idempotency_key="invalid-creator-confirm",
                ),
                principal=LOCAL,
            )
        self.assertEqual(self.resolver.calls, resolver_calls)

        draft = self.service.create_draft(
            CreateDraftCommand(
                title="Valid creator",
                objective="Corrupt only the frozen worker snapshot.",
                configuration=creator_configuration_payload(),
            ),
            principal=LOCAL,
        )
        run = self.service.confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key="creator-worker-validation",
            ),
            principal=LOCAL,
        )
        with sqlite3.connect(self.store.db_path) as connection:
            snapshot = json.loads(
                connection.execute(
                    "SELECT confirmed_configuration_json FROM investigation_runs WHERE id = ?",
                    (run.id,),
                ).fetchone()[0]
            )
            snapshot["draft_configuration"]["collection"]["creator_url"] = invalid_urls[2]
            snapshot["execution"]["creator_url"] = invalid_urls[2]
            snapshot["execution"]["creator_id"] = invalid_urls[2]
            connection.execute(
                "UPDATE investigation_runs SET confirmed_configuration_json = ? WHERE id = ?",
                (json.dumps(snapshot), run.id),
            )
        execution = FakeExecutionAdapter()
        result = self.worker(execution).run_once()
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.error_code, "invalid_confirmed_configuration")
        self.assertEqual(execution.ensure_calls, 0)
        self.assertEqual(execution.pipeline_calls, 0)

    def test_worker_revalidates_frozen_snapshot_before_any_job(self):
        invalid_mutations = (
            lambda snapshot: snapshot["execution"].__setitem__(
                "run_crawler", "false"
            ),
            lambda snapshot: snapshot["execution"].__setitem__(
                "unexpected", "field"
            ),
            lambda snapshot: snapshot["draft_configuration"].update(
                {"platforms": ["xhs", "dy"]}
            ),
        )
        for index, mutate in enumerate(invalid_mutations):
            with self.subTest(index=index):
                with tempfile.TemporaryDirectory() as temp_dir:
                    store = InvestigationCreationStore(Path(temp_dir) / "m3.sqlite3")
                    service = InvestigationCreationService(
                        store, configuration_resolver=FakeConfigurationResolver()
                    )
                    draft = service.create_draft(
                        CreateDraftCommand(
                            title="Worker validation",
                            objective="Reject corrupted snapshot.",
                            configuration=configuration_payload(),
                        ),
                        principal=LOCAL,
                    )
                    run = service.confirm_and_queue(
                        ConfirmAndQueueCommand(
                            draft_id=draft.id,
                            expected_revision=1,
                            confirmed=True,
                            idempotency_key=f"invalid-worker:{index}",
                        ),
                        principal=LOCAL,
                    )
                    with sqlite3.connect(store.db_path) as connection:
                        snapshot = json.loads(
                            connection.execute(
                                """
                                SELECT confirmed_configuration_json
                                FROM investigation_runs WHERE id = ?
                                """,
                                (run.id,),
                            ).fetchone()[0]
                        )
                        mutate(snapshot)
                        connection.execute(
                            """
                            UPDATE investigation_runs
                            SET confirmed_configuration_json = ? WHERE id = ?
                            """,
                            (json.dumps(snapshot), run.id),
                        )
                    execution = FakeExecutionAdapter()
                    result = InvestigationWorker(
                        store,
                        execution_adapter=execution,
                        report_adapter=FakeReportAdapter(),
                        session_adapter=FakeSessionAdapter(),
                    ).run_once()
                    self.assertEqual(result.status, RunStatus.FAILED)
                    self.assertEqual(result.error_code, "invalid_confirmed_configuration")
                    self.assertEqual(execution.ensure_calls, 0)
                    self.assertEqual(execution.pipeline_calls, 0)


class WorkerRecoveryAndFencingTest(M3TestCase):
    def test_two_workers_only_one_claims(self):
        self.queue_run()
        barrier = Barrier(2)

        def claim(worker_id: str):
            barrier.wait()
            claimed = self.store.claim_next(worker_id)
            return claimed.id if claimed else None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("worker:a", "worker:b")))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_job_created_before_binding_is_found_and_safely_executed(self):
        _, run = self.queue_run()
        execution = FakeExecutionAdapter()

        def crash(event, _run, _detail):
            if event == "after_job_created":
                raise SimulatedWorkerCrash()

        with self.assertRaises(SimulatedWorkerCrash):
            self.worker(execution, hook=crash).run_once()
        self.assertFalse(self.store.get_run_for_worker(run.id).job_id)
        self.assertIn(execution.job_id_for_run(run.id), execution.jobs)

        self.clock.advance(11)
        recovered = self.worker(
            execution, worker_id="worker:recovery"
        ).run_once()
        self.assertEqual(recovered.status, RunStatus.PUBLISHED)
        self.assertEqual(recovered.job_id, execution.job_id_for_run(run.id))
        self.assertEqual(execution.pipeline_calls, 1)

    def test_pipeline_running_after_crash_is_interrupted_without_restart(self):
        _, run = self.queue_run()
        execution = FakeExecutionAdapter()
        execution.crash_in_pipeline = True
        with self.assertRaises(SimulatedWorkerCrash):
            self.worker(execution).run_once()
        started = self.store.get_run_for_worker(run.id)
        self.assertTrue(started.pipeline_started_at)
        self.clock.advance(11)
        recovered = self.worker(
            execution, worker_id="worker:recovery"
        ).run_once()
        self.assertEqual(recovered.status, RunStatus.INTERRUPTED)
        self.assertEqual(recovered.error_code, "collection_result_unknown")
        self.assertEqual(execution.pipeline_calls, 1)

    def test_started_pipeline_with_missing_or_queued_job_is_never_restarted(self):
        for job_exists in (False, True):
            with self.subTest(job_exists=job_exists):
                with tempfile.TemporaryDirectory() as temp_dir:
                    clock = MutableClock()
                    store = InvestigationCreationStore(
                        Path(temp_dir) / "m3.sqlite3", clock=clock
                    )
                    service = InvestigationCreationService(
                        store, configuration_resolver=FakeConfigurationResolver()
                    )
                    draft = service.create_draft(
                        CreateDraftCommand(
                            title="Started marker",
                            objective="Do not infer an unstarted Pipeline.",
                            configuration=configuration_payload(),
                        ),
                        principal=LOCAL,
                    )
                    run = service.confirm_and_queue(
                        ConfirmAndQueueCommand(
                            draft_id=draft.id,
                            expected_revision=1,
                            confirmed=True,
                            idempotency_key=f"started:{job_exists}",
                        ),
                        principal=LOCAL,
                    )
                    execution = FakeExecutionAdapter()
                    claimed = store.claim_next("worker:lost", lease_timeout_seconds=10)
                    job_id = execution.job_id_for_run(run.id)
                    store.bind_job(run.id, claimed.claim_token, job_id)
                    store.mark_pipeline_started(run.id, claimed.claim_token)
                    if job_exists:
                        execution.jobs[job_id] = {
                            "status": "queued",
                            "error": "",
                            "task_stats": {},
                        }
                    clock.advance(11)
                    recovered = InvestigationWorker(
                        store,
                        execution_adapter=execution,
                        report_adapter=FakeReportAdapter(),
                        session_adapter=FakeSessionAdapter(),
                        worker_id="worker:recovery",
                        lease_timeout_seconds=10,
                    ).run_once()
                    self.assertEqual(recovered.status, RunStatus.INTERRUPTED)
                    self.assertEqual(execution.pipeline_calls, 0)

    def test_completed_job_recovery_enters_report_without_pipeline_replay(self):
        _, run = self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()

        def crash(event, _run, _detail):
            if event == "after_pipeline_returned":
                raise SimulatedWorkerCrash()

        with self.assertRaises(SimulatedWorkerCrash):
            self.worker(execution, report, hook=crash).run_once()
        self.clock.advance(11)
        recovered = self.worker(
            execution,
            report,
            worker_id="worker:recovery",
        ).run_once()
        self.assertEqual(recovered.status, RunStatus.PUBLISHED)
        self.assertEqual(execution.pipeline_calls, 1)
        self.assertEqual(report.generate_calls, 1)

    def test_failed_or_undrained_job_never_generates_report(self):
        for state, expected_code in (
            ({"status": "failed", "error": "failed", "task_stats": {}}, "audit_job_failed"),
            (FakeExecutionAdapter.completed_state(pending=1), "analysis_not_drained"),
            (FakeExecutionAdapter.completed_state(analyzing=1), "analysis_not_drained"),
        ):
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as temp_dir:
                    store = InvestigationCreationStore(Path(temp_dir) / "m3.sqlite3")
                    service = InvestigationCreationService(
                        store, configuration_resolver=FakeConfigurationResolver()
                    )
                    draft = service.create_draft(
                        CreateDraftCommand(
                            title="Gate",
                            objective="Do not report.",
                            configuration=configuration_payload(),
                        ),
                        principal=LOCAL,
                    )
                    service.confirm_and_queue(
                        ConfirmAndQueueCommand(
                            draft_id=draft.id,
                            expected_revision=1,
                            confirmed=True,
                            idempotency_key=f"gate:{expected_code}:{len(temp_dir)}",
                        ),
                        principal=LOCAL,
                    )
                    report = FakeReportAdapter()
                    result = InvestigationWorker(
                        store,
                        execution_adapter=FakeExecutionAdapter(state),
                        report_adapter=report,
                        session_adapter=FakeSessionAdapter(),
                    ).run_once()
                    self.assertEqual(result.status, RunStatus.FAILED)
                    self.assertEqual(result.error_code, expected_code)
                    self.assertEqual(report.generate_calls, 0)

    def test_completed_and_drained_generates_one_report(self):
        self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        session = FakeSessionAdapter()
        worker = self.worker(execution, report, session)
        result = worker.run_once()
        replay = worker.run_once()
        self.assertEqual(result.status, RunStatus.PUBLISHED)
        self.assertIsNone(replay)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(len(session.sessions), 1)

    def test_publish_committed_then_generate_exception_reconciles_without_replay(self):
        self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        report.error_after_publish = RuntimeError("generate failed after publish commit")
        session = FakeSessionAdapter()

        result = self.worker(execution, report, session).run_once()

        self.assertEqual(result.status, RunStatus.PUBLISHED)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(len(report.published_by_task), 1)
        self.assertEqual(len(session.sessions), 1)

    def test_binding_update_failure_is_reclaimed_and_finalized_by_new_worker(self):
        self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        session = FakeSessionAdapter()
        original = self.store.mark_report_binding_published
        calls = 0

        def fail_twice(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                raise RuntimeError("temporary binding update failure")
            return original(*args, **kwargs)

        with patch.object(
            self.store, "mark_report_binding_published", side_effect=fail_twice
        ):
            deferred = self.worker(
                execution, report, session, worker_id="worker:first"
            ).run_once()
            recovered = self.worker(
                execution, report, session, worker_id="worker:new"
            ).run_once()

        self.assertEqual(deferred.status, RunStatus.REPORT_GENERATING)
        self.assertFalse(deferred.claim_token)
        self.assertEqual(recovered.status, RunStatus.PUBLISHED)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(len(report.published_by_task), 1)
        self.assertEqual(len(session.sessions), 1)

    def test_session_creation_failure_is_reclaimed_and_uses_one_anchor(self):
        self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        session = FakeSessionAdapter(failures_remaining=2)

        deferred = self.worker(
            execution, report, session, worker_id="worker:first"
        ).run_once()
        recovered = self.worker(
            execution, report, session, worker_id="worker:new"
        ).run_once()

        self.assertEqual(deferred.status, RunStatus.REPORT_GENERATING)
        self.assertFalse(deferred.claim_token)
        self.assertEqual(recovered.status, RunStatus.PUBLISHED)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(len(report.published_by_task), 1)
        self.assertEqual(len(session.sessions), 1)

    def test_run_publish_write_failure_is_reclaimed_without_duplicate_side_effects(self):
        self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        session = FakeSessionAdapter()
        original = self.store.mark_published
        calls = 0

        def fail_twice(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                raise RuntimeError("temporary InvestigationRun write failure")
            return original(*args, **kwargs)

        with patch.object(self.store, "mark_published", side_effect=fail_twice):
            deferred = self.worker(
                execution, report, session, worker_id="worker:first"
            ).run_once()
            recovered = self.worker(
                execution, report, session, worker_id="worker:new"
            ).run_once()

        self.assertEqual(deferred.status, RunStatus.REPORT_GENERATING)
        self.assertFalse(deferred.claim_token)
        self.assertEqual(recovered.status, RunStatus.PUBLISHED)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(len(report.published_by_task), 1)
        self.assertEqual(len(session.sessions), 1)

    def test_crash_after_publish_inside_generate_is_recovered_by_new_worker(self):
        self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        report.error_after_publish = SimulatedWorkerCrash()
        session = FakeSessionAdapter()

        with self.assertRaises(SimulatedWorkerCrash):
            self.worker(
                execution, report, session, worker_id="worker:crashed"
            ).run_once()
        self.clock.advance(11)
        recovered = self.worker(
            execution, report, session, worker_id="worker:new"
        ).run_once()

        self.assertEqual(recovered.status, RunStatus.PUBLISHED)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(len(report.published_by_task), 1)
        self.assertEqual(len(session.sessions), 1)

    def test_lost_report_lease_fences_old_worker_and_new_worker_does_not_generate(self):
        _, run = self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        session = FakeSessionAdapter()
        recovery_results = []

        def steal_lease_after_generation_started():
            self.clock.advance(11)
            recovery_results.append(
                self.worker(
                    execution,
                    report,
                    session,
                    worker_id="worker:new",
                    heartbeat_interval_seconds=60,
                ).run_once()
            )

        report.after_started = steal_lease_after_generation_started
        old_result = self.worker(
            execution,
            report,
            session,
            worker_id="worker:old",
            heartbeat_interval_seconds=60,
        ).run_once()
        self.assertEqual(recovery_results[0].status, RunStatus.INTERRUPTED)
        self.assertEqual(old_result.status, RunStatus.INTERRUPTED)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(session.calls, 0)
        binding = self.store.get_report_binding(run.id)
        self.assertEqual(binding.state, "STARTED")

    def test_new_worker_reuses_published_result_after_old_worker_loses_lease(self):
        self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        session = FakeSessionAdapter()
        recovered_runs = []

        def steal_lease_after_publish():
            self.clock.advance(11)
            recovered_runs.append(
                self.worker(
                    execution,
                    report,
                    session,
                    worker_id="worker:new",
                    heartbeat_interval_seconds=60,
                ).run_once()
            )

        report.after_published = steal_lease_after_publish
        old_result = self.worker(
            execution,
            report,
            session,
            worker_id="worker:old",
            heartbeat_interval_seconds=60,
        ).run_once()

        self.assertEqual(recovered_runs[0].status, RunStatus.PUBLISHED)
        self.assertEqual(old_result.status, RunStatus.PUBLISHED)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(len(report.published_by_task), 1)
        self.assertEqual(len(session.sessions), 1)

    def test_crash_after_publish_reuses_same_version_and_session_anchor(self):
        _, run = self.queue_run()
        execution = FakeExecutionAdapter()
        report = FakeReportAdapter()
        session = FakeSessionAdapter()

        def crash(event, _run, _detail):
            if event == "after_session_created":
                raise SimulatedWorkerCrash()

        with self.assertRaises(SimulatedWorkerCrash):
            self.worker(execution, report, session, hook=crash).run_once()
        self.clock.advance(11)
        recovered = self.worker(
            execution,
            report,
            session,
            worker_id="worker:recovery",
        ).run_once()
        self.assertEqual(recovered.status, RunStatus.PUBLISHED)
        self.assertEqual(report.generate_calls, 1)
        self.assertEqual(len(session.sessions), 1)
        self.assertEqual(
            recovered.report_version_id,
            report.published_by_task[run.job_id or recovered.job_id][1],
        )


class PersistenceContractTest(unittest.TestCase):
    def test_revision_create_or_get_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            stores = (
                TaskAuditConfigRevisionStore(db_path),
                TaskAuditConfigRevisionStore(db_path),
            )
            payload = {
                "source_policy_name": "Test",
                "audit_config": {},
                "knowledge_package_snapshots": [],
                "rule_snapshot": {},
                "prompt_profile_snapshot": {},
                "config_hash": "same-hash",
            }
            barrier = Barrier(2)

            def create(index: int):
                barrier.wait()
                return stores[index].create_or_get(
                    job_id="job:one", created_by="test", **payload
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                rows = list(
                    pool.map(create, range(2))
                )
            self.assertEqual(rows[0]["id"], rows[1]["id"])
            self.assertEqual(len(stores[0].list_for_job("job:one")), 1)

    def test_r31_runtime_binds_generation_before_graph_execution(self):
        events = []

        class FakeGraph:
            def __init__(self, **_kwargs):
                self._on_generation_created = lambda generation: events.append(
                    ("graph", generation["run_id"])
                )
                self.closed = False

            def generate(self, _task_id):
                generation = {
                    "run_id": "report-run:bound",
                    "report_version_id": "report-version:bound",
                }
                self._on_generation_created(generation)
                events.append(("execute", generation["run_id"]))
                return type("Result", (), {"report_version_id": generation["report_version_id"]})()

            def close(self):
                self.closed = True

        runtime = R31ReportRuntime(object(), graph_factory=FakeGraph)
        runtime.generate(
            "job:one",
            source=object(),
            on_generation_created=lambda generation: events.append(
                ("binding", generation["run_id"])
            ),
        )
        self.assertEqual(
            events,
            [
                ("graph", "report-run:bound"),
                ("binding", "report-run:bound"),
                ("execute", "report-run:bound"),
            ],
        )

    def test_unpublished_existing_r31_generation_is_not_automatically_resumed(self):
        class FakeStore:
            def get_latest_run_for_task(self, task_id):
                return {"id": "report-run:unknown", "task_id": task_id}

        class FakeRuntime:
            def generate(self, *_args, **_kwargs):
                raise AssertionError("Provider generation must not run")

            def resume(self, *_args, **_kwargs):
                raise AssertionError("automatic checkpoint resume must not run")

        adapter = R31ReportAdapter(store=FakeStore(), runtime=FakeRuntime())
        with self.assertRaises(RuntimeError):
            adapter.generate(
                "job:one",
                on_generation_started=lambda _run_id, _version_id: None,
            )

    def test_production_adapter_reuses_job_and_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audit_path = root / "audit.sqlite3"
            resolver = InvestigationConfigurationResolver(
                lexicon_store=LexiconStore(audit_path),
                policy_store=AuditPolicyStore(audit_path),
                crawler_account_store=CrawlerAccountStore(audit_path),
            )
            creation_store = InvestigationCreationStore(root / "m3.sqlite3")
            service = InvestigationCreationService(
                creation_store, configuration_resolver=resolver
            )
            draft = service.create_draft(
                CreateDraftCommand(
                    title="Adapter",
                    objective="One Job and revision.",
                    configuration=configuration_payload(keyword="adapter"),
                ),
                principal=LOCAL,
            )
            run = service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=draft.id,
                    expected_revision=1,
                    confirmed=True,
                    idempotency_key="adapter:confirm",
                ),
                principal=LOCAL,
            )
            job_store = JobStore(audit_path)
            revisions = TaskAuditConfigRevisionStore(audit_path)
            adapter = AuditPipelineExecutionAdapter(
                job_store=job_store,
                ingestion_store=IngestionStore(audit_path),
                revision_store=revisions,
                pipeline_factory=lambda **_kwargs: None,
            )
            first = adapter.ensure_job(run)
            second = adapter.ensure_job(run)
            self.assertEqual(first, second)
            self.assertEqual(len(job_store.list(include_archived=True)), 1)
            self.assertEqual(len(revisions.list_for_job(first)), 1)

    def test_revision_created_before_job_binding_recovers_without_duplicate(self):
        class CrashBeforeRevisionBindingJobStore(JobStore):
            def __init__(self, db_path):
                super().__init__(db_path)
                self.crash_once = True

            def update(self, job_id: str, **kwargs) -> None:
                if self.crash_once and "current_audit_config_revision_id" in kwargs:
                    self.crash_once = False
                    raise SimulatedWorkerCrash()
                super().update(job_id, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audit_path = root / "audit.sqlite3"
            creation_store = InvestigationCreationStore(root / "m3.sqlite3")
            service = InvestigationCreationService(
                creation_store, configuration_resolver=FakeConfigurationResolver()
            )
            draft = service.create_draft(
                CreateDraftCommand(
                    title="Revision crash",
                    objective="Recover one revision.",
                    configuration=configuration_payload(),
                ),
                principal=LOCAL,
            )
            run = service.confirm_and_queue(
                ConfirmAndQueueCommand(
                    draft_id=draft.id,
                    expected_revision=1,
                    confirmed=True,
                    idempotency_key="revision:crash",
                ),
                principal=LOCAL,
            )
            revisions = TaskAuditConfigRevisionStore(audit_path)
            crashing_jobs = CrashBeforeRevisionBindingJobStore(audit_path)
            first_adapter = AuditPipelineExecutionAdapter(
                job_store=crashing_jobs,
                ingestion_store=IngestionStore(audit_path),
                revision_store=revisions,
                pipeline_factory=lambda **_kwargs: None,
            )
            with self.assertRaises(SimulatedWorkerCrash):
                first_adapter.ensure_job(run)
            job_id = first_adapter.job_id_for_run(run.id)
            self.assertEqual(len(revisions.list_for_job(job_id)), 1)
            self.assertFalse(
                JobStore(audit_path).get(job_id)["current_audit_config_revision_id"]
            )

            recovered = AuditPipelineExecutionAdapter(
                job_store=JobStore(audit_path),
                ingestion_store=IngestionStore(audit_path),
                revision_store=revisions,
                pipeline_factory=lambda **_kwargs: None,
            ).ensure_job(run)
            self.assertEqual(recovered, job_id)
            self.assertEqual(len(revisions.list_for_job(job_id)), 1)
            self.assertTrue(
                JobStore(audit_path).get(job_id)["current_audit_config_revision_id"]
            )

    def test_two_connections_can_concurrently_migrate_session_anchor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "investigation.sqlite3"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE investigation_sessions (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        report_id TEXT NOT NULL,
                        report_version_id TEXT NOT NULL,
                        source_snapshot_id TEXT NOT NULL,
                        snapshot_hash TEXT NOT NULL,
                        status TEXT NOT NULL,
                        summary_text TEXT NOT NULL DEFAULT '',
                        active_focus_json TEXT NOT NULL DEFAULT '{}',
                        ordered_referents_json TEXT NOT NULL DEFAULT '[]',
                        last_claim_id TEXT NOT NULL DEFAULT '',
                        last_finding_id TEXT NOT NULL DEFAULT '',
                        last_evidence_id TEXT NOT NULL DEFAULT '',
                        last_answer_message_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            barrier = Barrier(2)

            def initialize(_index: int):
                barrier.wait()
                return InvestigationStore(db_path)

            with ThreadPoolExecutor(max_workers=2) as pool:
                stores = list(pool.map(initialize, range(2)))
            with sqlite3.connect(db_path) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(investigation_sessions)"
                    )
                }
            self.assertEqual(len(stores), 2)
            self.assertIn("anchor_key", columns)

    @staticmethod
    def context(token: str) -> PublishedReportContext:
        return PublishedReportContext(
            task_id=f"task:{token}",
            report_id="report:" + token * 32,
            report_version_id="report-version:" + token * 32,
            version_number=1,
            source_snapshot_id=f"source-snapshot:{token}",
            snapshot_hash=token * 64,
            source_hash=f"source:{token}",
            title=f"Report {token}",
            content_hash=f"content:{token}",
            published_at="2026-08-27T00:00:00+00:00",
            finding_ids=(),
            evidence_ids=(),
        )

    def test_session_anchor_remains_idempotent_and_immutable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = InvestigationStore(Path(temp_dir) / "investigation.sqlite3")
            first = store.create_session(self.context("a"), anchor_key="m3-run:one")
            replay = store.create_session(self.context("a"), anchor_key="m3-run:one")
            self.assertEqual(first.id, replay.id)
            with self.assertRaises(ValueError):
                store.create_session(self.context("b"), anchor_key="m3-run:one")

    def test_two_connections_create_one_fixed_session_anchor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "investigation.sqlite3"
            stores = (InvestigationStore(db_path), InvestigationStore(db_path))
            context = self.context("c")
            barrier = Barrier(2)

            def create(index: int):
                barrier.wait()
                return stores[index].create_session(
                    context, anchor_key="m3-run:concurrent"
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                sessions = list(pool.map(create, range(2)))
            self.assertEqual(sessions[0].id, sessions[1].id)
            with sqlite3.connect(db_path) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM investigation_sessions WHERE anchor_key = ?",
                    ("m3-run:concurrent",),
                ).fetchone()[0]
            self.assertEqual(count, 1)


class LegacyApiCompatibilityTest(unittest.TestCase):
    def test_old_post_jobs_accepts_existing_creator_url_and_legacy_id(self):
        from backend import main as backend_main

        full_url = creator_configuration_payload()["collection"]["creator_url"]
        requests = (
            {"creator_url": full_url, "creator_id": ""},
            {"creator_url": "", "creator_id": "5f58bd990000000001003753"},
        )
        with patch(
            "subprocess.Popen",
            side_effect=AssertionError("legacy API test must not launch subprocess"),
        ), patch.object(
            backend_main.AuditPipeline,
            "run",
            autospec=True,
        ) as pipeline_run:
            with TestClient(backend_main.app) as client:
                responses = [
                    client.post(
                        "/api/jobs",
                        json={
                            "platform": "xhs",
                            "crawl_mode": "creator",
                            "keyword_source": "keyword",
                            "run_crawler": True,
                            "max_notes": 1,
                            **creator_identity,
                        },
                    )
                    for creator_identity in requests
                ]
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(pipeline_run.call_count, 2)
        self.assertEqual(responses[0].json()["creator_url"], full_url)
        self.assertEqual(
            responses[1].json()["creator_url"], "5f58bd990000000001003753"
        )


class ZZZTestIsolationContract(unittest.TestCase):
    def test_default_data_and_outputs_are_unchanged(self):
        from backend.audit_agent.config import settings

        self.assertNotEqual(settings.data_dir.resolve(), _DEFAULT_DATA_DIR.resolve())
        self.assertNotEqual(
            settings.outputs_dir.resolve(), _DEFAULT_OUTPUTS_DIR.resolve()
        )
        self.assertEqual(
            _directory_fingerprint(_DEFAULT_DATA_DIR),
            _DEFAULT_DIRECTORY_STATE["data"],
        )
        self.assertEqual(
            _directory_fingerprint(_DEFAULT_OUTPUTS_DIR),
            _DEFAULT_DIRECTORY_STATE["outputs"],
        )


if __name__ == "__main__":
    unittest.main()
