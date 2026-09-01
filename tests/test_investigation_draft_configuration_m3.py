from __future__ import annotations

import atexit
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_M3_TEST_ROOT = Path(tempfile.mkdtemp(prefix="m3-contract-tests-", dir="/tmp"))
os.environ["XHS_AUDIT_DATA_DIR"] = str(_M3_TEST_ROOT / "data")
os.environ["XHS_AUDIT_OUTPUTS_DIR"] = str(_M3_TEST_ROOT / "outputs")
os.environ["PYTHONPYCACHEPREFIX"] = str(_M3_TEST_ROOT / "pycache")
sys.pycache_prefix = os.environ["PYTHONPYCACHEPREFIX"]
os.environ["TMPDIR"] = str(_M3_TEST_ROOT / "tmp")
os.environ["HERMES_HOME"] = str(_M3_TEST_ROOT / "hermes")
(_M3_TEST_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
atexit.register(shutil.rmtree, _M3_TEST_ROOT, True)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.investigation_creation import create_investigation_creation_router
from backend.audit_agent.audit_policy_store import AuditPolicyStore
from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_adapter import CrawlOutput, MediaCrawlerAdapter
from backend.audit_agent.ingestion import AuditResultStore, BatchWriter, IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.lexicon_store import LexiconStore
import backend.audit_agent.pipeline as audit_pipeline_module
from backend.audit_agent.pipeline import AuditPipeline
from backend.investigation_creation.adapters import (
    AuditPipelineExecutionAdapter,
    InvestigationConfigurationResolver,
    InvestigationRunProjector,
)
from backend.investigation_creation.contracts import (
    ConfirmAndQueueCommand,
    ConfirmedConfigurationSnapshotV3,
    CreateDraftCommand,
    InvestigationDraftConfiguration,
    QueryInvestigationOptions,
    UpdateDraftCommand,
    confirmed_configuration_hash,
)
from backend.investigation_creation.errors import (
    ConfigurationValidationError,
    ResourceStaleError,
)
from backend.investigation_creation.principal import LocalPrincipalProvider, Principal
from backend.investigation_creation.public_projection import public_run
from backend.investigation_creation.resources import InvestigationResourceService
from backend.investigation_creation.service import InvestigationCreationService
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import InvestigationCreationToolService
from backend.investigation_creation.worker import InvestigationWorker
from backend.hermes_runtime.adapter import HermesRuntimeBinding
from backend.rulesets.service import RuleSetService
from backend.rulesets.store import RuleSetStore
from hermes_m0.plugin import register as register_hermes_plugin
from hermes_m0.schemas import M2_ACCOUNT_ACTIVITY_TOOLS


@pytest.fixture
def m3_stack(tmp_path: Path) -> dict:
    resource_db = tmp_path / "resources.sqlite3"
    lexicons = LexiconStore(resource_db)
    policies = AuditPolicyStore(resource_db)
    rulesets = RuleSetService(RuleSetStore(resource_db))
    principals = LocalPrincipalProvider()
    crawler_accounts = CrawlerAccountStore(resource_db)
    for platform in ("xhs", "dy", "ks"):
        account = crawler_accounts.create(
            platform=platform,
            display_name=f"{platform} fixture account",
        )
        crawler_accounts.save_auth_state(account["id"], "synthetic-fixture-ciphertext")
    resolver = InvestigationConfigurationResolver(
        lexicon_store=lexicons,
        policy_store=policies,
        crawler_account_store=crawler_accounts,
        ruleset_service=rulesets,
        principal_provider=principals,
    )
    resources = InvestigationResourceService(
        lexicon_store=lexicons,
        policy_store=policies,
        ruleset_service=rulesets,
        configuration_resolver=resolver,
    )
    creation_store = InvestigationCreationStore(tmp_path / "creation.sqlite3")
    service = InvestigationCreationService(
        creation_store,
        configuration_resolver=resolver,
        resource_service=resources,
    )
    return {
        "lexicons": lexicons,
        "policies": policies,
        "principals": principals,
        "resources": resources,
        "crawler_accounts": crawler_accounts,
        "store": creation_store,
        "service": service,
    }


def _principal(stack: dict):
    return stack["principals"]()


def _policy(stack: dict):
    options = stack["service"].query_investigation_options(
        QueryInvestigationOptions(domain_hint="gambling"),
        principal=_principal(stack),
    )
    assert len(options.audit_policies) == 1
    return options.audit_policies[0]


def _selection(policy) -> dict:
    return {
        "id": policy.id,
        "expected_published_version": policy.published_version,
        "expected_published_config_hash": policy.published_config_hash,
        "expected_ruleset_revision_id": policy.ruleset_revision_id,
        "expected_ruleset_version": policy.ruleset_version,
        "expected_ruleset_content_hash": policy.ruleset_content_hash,
    }


def _temporary_configuration(stack: dict, terms: list[str]) -> dict:
    return {
        "platform": "xhs",
        "investigation": {
            "mode": "search",
            "recall_plan": {
                "strategy": "temporary_terms",
                "terms": terms,
                "source_lexicon_ids": ["general-reference"],
            },
        },
        "audit_policy": _selection(_policy(stack)),
    }


def _create_draft(stack: dict, configuration: dict):
    return stack["service"].create_draft(
        CreateDraftCommand(
            title="Gambling investigation",
            objective="Collect and audit public gambling promotion content.",
            configuration=configuration,
        ),
        principal=_principal(stack),
    )


def _account_for_platform(stack: dict, platform: str) -> dict:
    return next(
        account
        for account in stack["crawler_accounts"].list(platform=platform)
        if account["status"] == "active" and account["has_auth_state"]
    )


def _douyin_configuration(stack: dict, term: str = "世界杯") -> dict:
    return {
        "platform": "dy",
        "investigation": {
            "mode": "search",
            "recall_plan": {
                "strategy": "temporary_terms",
                "terms": [term],
                "source_lexicon_ids": ["general-reference"],
            },
        },
        "audit_policy": _selection(_policy(stack)),
    }


class _FakeCrawler:
    def __init__(self, contents: list[dict]) -> None:
        self.contents = [dict(item) for item in contents]
        self.calls: list[dict] = []

    def _run(self, mode: str, **kwargs) -> CrawlOutput:
        self.calls.append({"mode": mode, **kwargs})
        if kwargs.get("started_callback"):
            kwargs["started_callback"]()
        callback = kwargs.get("content_callback")
        if callback:
            for item in self.contents:
                callback([dict(item)], [])
            if self.contents:
                callback([dict(self.contents[0])], [])
        return CrawlOutput(
            platform=kwargs["platform"],
            contents=[dict(item) for item in self.contents],
            comments=[],
            output_dir=kwargs["save_root"],
            command=["fake-mediacrawler", mode],
        )

    def run_search(self, **kwargs) -> CrawlOutput:
        return self._run("search", **kwargs)

    def run_creator(self, **kwargs) -> CrawlOutput:
        return self._run("creator", **kwargs)



def test_unmarked_pipeline_preserves_legacy_multiple_item_behavior(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        audit_pipeline_module.settings, "auto_analyze_crawled_content", True
    )
    audit_db = tmp_path / "legacy-multiple.sqlite3"
    jobs = JobStore(audit_db)
    ingestion = IngestionStore(audit_db)
    crawler = _FakeCrawler(
        [
            {"note_id": "legacy-1", "title": "first"},
            {"note_id": "legacy-2", "title": "second"},
        ]
    )
    job_id = "legacy-multiple"
    configuration = {
        "platform": "xhs",
        "display_name": "Legacy multiple",
        "crawl_mode": "search",
        "keyword": "legacy",
        "keyword_source": "keyword",
        "lexicon_category": "soft",
        "library_ids": ["soft"],
        "capabilities": ["text"],
        "scoring_template": "balanced",
        "rule_snapshot": {},
        "lexicon_keywords": [],
        "creator_url": "",
        "creator_id": "",
        "start_page": 1,
        "max_notes": 2,
        "max_comments": 0,
        "max_concurrency": 1,
        "max_items_per_minute": 1,
        "crawler_account_id": None,
        "get_sub_comment": False,
        "analyze_limit": 2,
        "run_crawler": True,
        "source_output_id": None,
        "analysis_batch_size": 1,
        "prompt_profile_snapshot": {},
        "policy_id": "",
    }
    jobs.create(job_id=job_id, **configuration)
    monkeypatch.setattr(audit_pipeline_module, "job_store", jobs)
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = job_id
    pipeline.crawler = crawler
    pipeline.ingestion = ingestion
    pipeline.audit_results = object()
    pipeline.prompt_profile_snapshot = {}
    pipeline.audit_config_revision_id = ""
    pipeline.rule_snapshot = {}
    pipeline._analyze_subject = lambda subject: {
        "note_id": subject.note_id,
        "title": subject.title,
        "decision": "pass",
    }
    pipeline._persist_audit_result = lambda **kwargs: {
        **kwargs["result"],
        "content_key": kwargs["content_key"],
    }

    pipeline.run(SimpleNamespace(**configuration))

    assert jobs.get(job_id)["status"] == "completed"
    assert pipeline.authoritative_m3 is False
    stats = ingestion.stats_for_task(job_id)
    assert stats["ingested_count"] == 2
    assert stats["completed_analysis_count"] == 2
    assert crawler.calls[0]["max_notes"] == 2
    assert "_confirmed_analyze_limit" not in configuration


def test_existing_draft_payload_without_account_remains_readable(m3_stack: dict):
    configuration = _temporary_configuration(m3_stack, ["美食"])
    assert "crawler_account_id" not in configuration
    parsed = InvestigationDraftConfiguration.model_validate(configuration)
    assert "crawler_account_id" not in parsed.model_dump(mode="json")


def test_v3_snapshot_accepts_analyze_limit_greater_than_one(m3_stack: dict):
    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, ["美食"]))
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=1,
            confirmed=True,
            idempotency_key="analyze-limit:v3-two",
        ),
        principal=_principal(m3_stack),
    )
    snapshot = json.loads(json.dumps(run.confirmed_configuration))
    snapshot["execution"]["analyze_limit"] = 2
    snapshot["config_hash"] = confirmed_configuration_hash(
        {
            key: snapshot[key]
            for key in (
                "mode",
                "platform",
                "resolved_search_terms",
                "creator_url",
                "recall_plan",
                "audit_policy",
                "ruleset_revision",
                "execution",
            )
        }
    )

    parsed = ConfirmedConfigurationSnapshotV3.model_validate(snapshot)

    assert parsed.schema_version == "investigation-run-config-v3"
    assert parsed.execution.analyze_limit == 2
    assert parsed.execution.max_notes == 1


def test_m3_hides_auto_selected_account_and_freezes_it_into_job(
    m3_stack: dict,
):
    account = _account_for_platform(m3_stack, "dy")
    draft = _create_draft(
        m3_stack,
        _douyin_configuration(m3_stack),
    )
    assert "crawler_account_id" not in draft.configuration.model_dump(mode="json")

    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert preview.can_confirm
    preview_payload = preview.model_dump(mode="json")
    assert "crawler_account" not in preview_payload
    assert "crawler_accounts" not in preview_payload

    frozen_name = account["display_name"]
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=draft.current_revision,
            confirmed=True,
            idempotency_key="account-freeze:1",
        ),
        principal=_principal(m3_stack),
    )
    execution = run.confirmed_configuration["execution"]
    assert execution["crawler_account_id"] == account["id"]
    assert execution["crawler_account_display_name"] == frozen_name
    assert execution["crawler_account_confirmed_state"] == {
        "status": "active",
        "has_auth_state": True,
        "auth_state_updated_at": account["auth_state_updated_at"],
        "last_validated_at": account["last_validated_at"],
    }
    assert execution["max_notes"] == 1
    assert execution["analyze_limit"] == 1

    m3_stack["crawler_accounts"].update(
        account["id"], display_name="renamed after confirmation"
    )
    resource_db = m3_stack["resources"].resource_db_path
    adapter = AuditPipelineExecutionAdapter(
        job_store=JobStore(resource_db),
        ingestion_store=IngestionStore(resource_db),
        revision_store=TaskAuditConfigRevisionStore(resource_db),
        crawler_account_store=m3_stack["crawler_accounts"],
        test_provider_validator=lambda _: None,
    )
    job_id = adapter.ensure_job(run)
    job = JobStore(resource_db).get(job_id)
    assert job["crawler_account_id"] == account["id"]
    assert job["crawler_account_display_name"] == frozen_name
    assert job["max_notes"] == 1
    assert job["analyze_limit"] == 1


def test_existing_job_freeze_identity_mismatch_fails_closed(m3_stack: dict):
    account = _account_for_platform(m3_stack, "dy")
    draft = _create_draft(m3_stack, _douyin_configuration(m3_stack))
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=draft.current_revision,
            confirmed=True,
            idempotency_key="freeze-mismatch:1",
        ),
        principal=_principal(m3_stack),
    )
    resource_db = m3_stack["resources"].resource_db_path
    adapter = AuditPipelineExecutionAdapter(
        job_store=JobStore(resource_db),
        ingestion_store=IngestionStore(resource_db),
        revision_store=TaskAuditConfigRevisionStore(resource_db),
        crawler_account_store=m3_stack["crawler_accounts"],
        test_provider_validator=lambda _: None,
    )
    job_id = adapter.ensure_job(run)
    JobStore(resource_db).update(job_id, crawler_account_display_name="伪造显示名")

    with pytest.raises(RuntimeError, match="crawler_account_display_name"):
        adapter.ensure_job(run)
    failed_job = JobStore(resource_db).get(job_id)
    assert failed_job["status"] == "failed"
    assert "crawler_account_display_name" in failed_job["error"]


def test_account_invalidated_after_job_creation_invalidates_job_before_worker(
    m3_stack: dict,
):
    account = _account_for_platform(m3_stack, "dy")
    draft = _create_draft(m3_stack, _douyin_configuration(m3_stack))
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=draft.current_revision,
            confirmed=True,
            idempotency_key="account-invalid-after-job:1",
        ),
        principal=_principal(m3_stack),
    )
    resource_db = m3_stack["resources"].resource_db_path
    adapter = AuditPipelineExecutionAdapter(
        job_store=JobStore(resource_db),
        ingestion_store=IngestionStore(resource_db),
        revision_store=TaskAuditConfigRevisionStore(resource_db),
        crawler_account_store=m3_stack["crawler_accounts"],
        test_provider_validator=lambda _: None,
    )
    job_id = adapter.ensure_job(run)
    m3_stack["crawler_accounts"].update(account["id"], status="disabled")

    worker = InvestigationWorker(
        m3_stack["store"],
        execution_adapter=adapter,
        report_adapter=object(),
        session_adapter=object(),
    )
    failed = worker.run_once()
    assert failed is not None
    assert failed.error_code == "crawler_account_login_required"
    assert failed.pipeline_started_at == ""
    assert JobStore(resource_db).get(job_id)["status"] == "failed"


def test_m3_confirmation_without_account_is_blocked_without_side_effects(
    m3_stack: dict,
):
    account = _account_for_platform(m3_stack, "dy")
    m3_stack["crawler_accounts"].delete(account["id"])
    draft = _create_draft(
        m3_stack,
        _douyin_configuration(m3_stack),
    )
    assert "crawler_account_id" not in draft.configuration.model_dump(mode="json")
    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert not preview.can_confirm
    assert [item.code for item in preview.blockers] == [
        "collection_service_unavailable"
    ]
    assert preview.blockers[0].message == "抖音采集服务当前不可用，请稍后重试。"
    assert "crawler_account" not in preview.model_dump(mode="json")
    with pytest.raises(ConfigurationValidationError) as caught:
        m3_stack["service"].confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=draft.current_revision,
                confirmed=True,
                idempotency_key="no-account:1",
            ),
            principal=_principal(m3_stack),
        )
    assert caught.value.code == "collection_service_unavailable"
    assert _confirmation_side_effects(m3_stack) == (0, 0, 0)


def test_m3_discards_legacy_user_account_input_and_selects_stably(m3_stack: dict):
    existing = _account_for_platform(m3_stack, "dy")
    second = m3_stack["crawler_accounts"].create(
        platform="dy", display_name="second active fixture"
    )
    m3_stack["crawler_accounts"].save_auth_state(
        second["id"], "synthetic-second-fixture-ciphertext"
    )
    configuration = _douyin_configuration(m3_stack)
    configuration["crawler_account_id"] = "user-supplied-account-must-be-ignored"
    draft = _create_draft(m3_stack, configuration)
    assert "crawler_account_id" not in draft.configuration.model_dump(mode="json")
    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert preview.can_confirm
    assert "crawler_account" not in preview.model_dump(mode="json")
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=draft.current_revision,
            confirmed=True,
            idempotency_key="stable-auto-account:1",
        ),
        principal=_principal(m3_stack),
    )
    assert run.confirmed_configuration["execution"]["crawler_account_id"] == min(
        existing["id"], second["id"]
    )


def test_m3_account_changed_after_draft_blocks_run_and_worker_never_creates_job(
    m3_stack: dict,
):
    account = _account_for_platform(m3_stack, "dy")
    draft = _create_draft(
        m3_stack,
        _douyin_configuration(m3_stack),
    )
    m3_stack["crawler_accounts"].update(account["id"], status="disabled")
    with pytest.raises(ConfigurationValidationError) as caught:
        m3_stack["service"].confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=draft.current_revision,
                confirmed=True,
                idempotency_key="changed-before-confirm:1",
            ),
            principal=_principal(m3_stack),
        )
    assert caught.value.code == "collection_service_unavailable"
    assert str(caught.value) == "抖音采集服务当前不可用，请稍后重试。"
    assert _confirmation_side_effects(m3_stack) == (0, 0, 0)

    m3_stack["crawler_accounts"].update(account["id"], status="active")
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=draft.current_revision,
            confirmed=True,
            idempotency_key="changed-before-worker:1",
        ),
        principal=_principal(m3_stack),
    )
    m3_stack["crawler_accounts"].update(account["id"], status="disabled")
    resource_db = m3_stack["resources"].resource_db_path
    worker = InvestigationWorker(
        m3_stack["store"],
        execution_adapter=AuditPipelineExecutionAdapter(
            job_store=JobStore(resource_db),
            ingestion_store=IngestionStore(resource_db),
            revision_store=TaskAuditConfigRevisionStore(resource_db),
            crawler_account_store=m3_stack["crawler_accounts"],
            test_provider_validator=lambda _: None,
        ),
        report_adapter=object(),
        session_adapter=object(),
    )
    failed = worker.run_once()
    assert failed is not None
    assert failed.id == run.id
    assert failed.status.value == "FAILED"
    assert failed.error_code == "crawler_account_login_required"
    assert failed.pipeline_started_at == ""
    assert _confirmation_side_effects(m3_stack)[2] == 0


@pytest.mark.parametrize(
    ("decision", "risk_level", "expected_evidence_count"),
    [("review", "high", 1), ("pass", "none", 0)],
)
def test_douyin_jsonl_to_durable_audit_completed_without_report_or_session(
    m3_stack: dict,
    tmp_path: Path,
    decision: str,
    risk_level: str,
    expected_evidence_count: int,
):
    fixture_root = tmp_path / f"douyin-fixture-{decision}"
    fixture_jsonl = fixture_root / "douyin" / "jsonl"
    fixture_jsonl.mkdir(parents=True)
    candidates = [
        {
            "aweme_id": "7590000000000000001",
            "aweme_url": "https://www.douyin.com/video/7590000000000000001",
            "title": "世界杯稳赚交流群",
            "desc": "点击主页加入交流群",
            "nickname": "内容作者甲",
            "sec_uid": "MS4wLjABAAAA-content-author-a",
            "video_download_url": "https://fixture.invalid/video.mp4",
        },
        {
            "aweme_id": "7590000000000000002",
            "aweme_url": "https://www.douyin.com/video/7590000000000000002",
            "title": "第二条候选内容",
            "nickname": "内容作者乙",
            "sec_uid": "MS4wLjABAAAA-content-author-b",
        },
    ]
    (fixture_jsonl / "search_contents_2026-09-01.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in candidates),
        encoding="utf-8",
    )

    resource_db = m3_stack["resources"].resource_db_path
    jobs = JobStore(resource_db)
    ingestion = IngestionStore(resource_db)
    audit_results = AuditResultStore(resource_db)
    fake_calls = {"pipeline": 0, "asr": 0, "qwen": 0}

    class DeterministicAsrTestDouble:
        def transcribe(self, _audio_path: Path) -> dict:
            fake_calls["asr"] += 1
            return {
                "text": "加入世界杯交流，宣称稳赚",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "加入世界杯交流，宣称稳赚"}
                ],
            }

    class DeterministicQwenTestDouble:
        model = "test-only-qwen-audit-v1"

        def audit(self, item: dict, transcript: dict) -> dict:
            fake_calls["qwen"] += 1
            assert transcript["text"] == "加入世界杯交流，宣称稳赚"
            evidence = (
                [
                    {
                        "evidence_id": "ev-asr-1",
                        "source_type": "video_asr",
                        "original_text": transcript["text"],
                    }
                ]
                if expected_evidence_count
                else []
            )
            return {
                "note_id": item["aweme_id"],
                "url": item["aweme_url"],
                "title": item["title"],
                "author": {
                    "nickname": item["nickname"],
                    "sec_uid": item["sec_uid"],
                },
                "decision": decision,
                "risk_level": risk_level,
                "summary": (
                    "视频包含稳赚承诺和站外引流，建议复核。"
                    if decision == "review"
                    else "本次审核未发现明确风险。"
                ),
                "evidence_items": evidence,
                "model_provenance": {
                    "provider": "deterministic-test-double",
                    "model": self.model,
                    "prompt_sha256": "a" * 64,
                    "prompt_version": "m3-audit-test-v1",
                },
                "prompt_version": "m3-audit-test-v1",
            }

    asr = DeterministicAsrTestDouble()
    qwen = DeterministicQwenTestDouble()

    class DeterministicPipelineTestDouble:
        def __init__(self, *, job_id: str) -> None:
            self.job_id = job_id

        def run(self, request) -> None:
            fake_calls["pipeline"] += 1
            assert request.platform == "dy"
            assert request.analyze_limit == 1
            assert request._confirmed_analyze_limit == 1
            assert request._authoritative_m3_contract is True
            jobs.update(self.job_id, status="running")
            output = MediaCrawlerAdapter(
                tmp_path / "unused-mediacrawler"
            ).load_latest_output(fixture_root, "dy")
            assert [item["aweme_id"] for item in output.contents] == [
                "7590000000000000001",
                "7590000000000000002",
            ]
            writer = BatchWriter(
                job_id=self.job_id,
                platform="dy",
                keyword="世界杯",
                root=tmp_path / "pipeline-output" / self.job_id,
                batch_size=1,
            )
            batch_paths = writer.add(output.contents[:1], output.comments)
            assert len(batch_paths) == 1
            queued = ingestion.ingest_batch(
                batch_paths[0], tmp_path / "raw-items" / self.job_id
            )
            assert len(queued) == 1
            selected = queued[0]
            content_key = selected["content_key"]
            ingestion.mark_content_status(
                "dy", content_key, "analyzing", task_id=self.job_id
            )
            transcript = asr.transcribe(tmp_path / "fixture-video.wav")
            result = qwen.audit(selected["item"], transcript)
            result_path = tmp_path / "results" / f"{content_key}.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
            persisted = audit_results.upsert_result(
                job_id=self.job_id,
                platform="dy",
                content_key=content_key,
                result=result,
                result_path=str(result_path),
                content_id=selected["content_id"],
                model_text=qwen.model,
                prompt_version="m3-audit-test-v1",
            )
            ingestion.mark_content_status(
                "dy",
                content_key,
                "completed",
                str(result_path),
                task_id=self.job_id,
                audit_result_id=int(persisted["audit_result_id"]),
            )
            jobs.update(self.job_id, status="completed", items=[persisted])

    class ForbiddenReportOrSessionAdapter:
        def __getattr__(self, name: str):
            raise AssertionError(f"v3 audit completion must not call {name}")

    draft = _create_draft(m3_stack, _douyin_configuration(m3_stack))
    public_draft_payload = draft.model_dump(mode="json")
    assert "crawler_account" not in json.dumps(public_draft_payload)
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=draft.current_revision,
            confirmed=True,
            idempotency_key=f"dy-audit-completed:{decision}",
        ),
        principal=_principal(m3_stack),
    )
    frozen_account_id = run.confirmed_configuration["execution"][
        "crawler_account_id"
    ]
    assert "_authoritative_m3_contract" not in json.dumps(
        run.confirmed_configuration
    )
    assert frozen_account_id == _account_for_platform(m3_stack, "dy")["id"]
    adapter = AuditPipelineExecutionAdapter(
        job_store=jobs,
        ingestion_store=ingestion,
        audit_result_store=audit_results,
        revision_store=TaskAuditConfigRevisionStore(resource_db),
        crawler_account_store=m3_stack["crawler_accounts"],
        pipeline_factory=DeterministicPipelineTestDouble,
        test_provider_validator=lambda _: None,
    )
    worker = InvestigationWorker(
        m3_stack["store"],
        execution_adapter=adapter,
        report_adapter=ForbiddenReportOrSessionAdapter(),
        session_adapter=ForbiddenReportOrSessionAdapter(),
    )

    completed = worker.run_once()

    assert completed is not None
    assert completed.status.value == "AUDIT_COMPLETED"
    assert completed.job_id == adapter.job_id_for_run(run.id)
    assert completed.report_version_id == ""
    assert completed.report_session_id == ""
    assert "_authoritative_m3_contract" not in json.dumps(
        jobs.get(completed.job_id)
    )
    assert fake_calls == {"pipeline": 1, "asr": 1, "qwen": 1}
    assert ingestion.stats_for_task(completed.job_id) == {
        "ingested_count": 1,
        "queued_analysis_count": 0,
        "pending_analysis_count": 0,
        "analyzing_count": 0,
        "completed_analysis_count": 1,
        "failed_analysis_count": 0,
        "analysis_status_counts": {"completed": 1},
        "batch_count": 1,
        "batch_item_count": 1,
        "batch_processed_count": 1,
    }
    memberships, refs = ingestion.validated_selection_for_task(completed.job_id)
    assert [item["content_key"] for item in memberships] == [
        "7590000000000000001"
    ]
    assert refs[0]["item"]["sec_uid"] == "MS4wLjABAAAA-content-author-a"
    stored_page = audit_results.list_results(job_id=completed.job_id)
    assert stored_page["total"] == 1
    stored_result = stored_page["items"][0]
    assert stored_result["decision"] == decision
    assert stored_result["risk_level"] == risk_level
    assert stored_result["model_provenance"] == {
        "provider": "deterministic-test-double",
        "model": "test-only-qwen-audit-v1",
        "prompt_sha256": "a" * 64,
        "prompt_version": "m3-audit-test-v1",
    }
    assert len(stored_result["evidence_items"]) == expected_evidence_count

    m3_stack["service"].run_projector = InvestigationRunProjector(
        job_store=jobs,
        ingestion_store=ingestion,
        audit_result_store=audit_results,
    )
    projection = public_run(
        m3_stack["service"].get_run(
            completed.id, principal=_principal(m3_stack)
        )
    ).model_dump(mode="json")
    assert projection["status"] == "AUDIT_COMPLETED"
    assert projection["crawl_status"] == "completed"
    assert projection["analysis_status"] == "completed"
    assert projection["report_status"] == "pending"
    assert projection["report_version_id"] == ""
    assert projection["audit_results"][0]["author_display_name"] == "内容作者甲"
    assert projection["audit_results"][0]["summary"] == stored_result["summary"]
    assert len(projection["audit_results"][0]["evidence"]) == expected_evidence_count
    serialized_projection = json.dumps(projection, ensure_ascii=False)
    assert "_authoritative_m3_contract" not in serialized_projection
    assert frozen_account_id not in serialized_projection
    assert "fixture account" not in serialized_projection
    assert "crawler_account" not in serialized_projection

    reopened = InvestigationCreationStore(m3_stack["store"].db_path)
    durable = reopened.get_run(completed.id, principal=_principal(m3_stack).id)
    assert durable.status.value == "AUDIT_COMPLETED"
    assert InvestigationWorker(
        reopened,
        execution_adapter=adapter,
        report_adapter=ForbiddenReportOrSessionAdapter(),
        session_adapter=ForbiddenReportOrSessionAdapter(),
    ).run_once() is None
    assert fake_calls == {"pipeline": 1, "asr": 1, "qwen": 1}
    assert audit_results.list_results(job_id=completed.job_id)["total"] == 1
    with sqlite3.connect(m3_stack["store"].db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM investigation_report_generation_bindings"
        ).fetchone()[0] == 0


def test_authoritative_m3_missing_provider_fails_before_job_creation(
    m3_stack: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(audit_pipeline_module.settings, "use_remote_llm", False)
    monkeypatch.setattr(audit_pipeline_module.settings, "dashscope_api_key", "")
    monkeypatch.setattr(
        audit_pipeline_module.settings, "qwen_text_model", "qwen-test-model"
    )
    draft = _create_draft(m3_stack, _douyin_configuration(m3_stack))
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=draft.current_revision,
            confirmed=True,
            idempotency_key="missing-provider:1",
        ),
        principal=_principal(m3_stack),
    )
    resource_db = m3_stack["resources"].resource_db_path
    jobs = JobStore(resource_db)
    adapter = AuditPipelineExecutionAdapter(
        job_store=jobs,
        ingestion_store=IngestionStore(resource_db),
        audit_result_store=AuditResultStore(resource_db),
        revision_store=TaskAuditConfigRevisionStore(resource_db),
        crawler_account_store=m3_stack["crawler_accounts"],
    )
    failed = InvestigationWorker(
        m3_stack["store"],
        execution_adapter=adapter,
        report_adapter=object(),
        session_adapter=object(),
    ).run_once()

    assert failed is not None
    assert failed.status.value == "FAILED"
    assert failed.error_code == "audit_provider_unavailable"
    assert failed.job_id == ""
    assert failed.pipeline_started_at == ""
    assert jobs.get(adapter.job_id_for_run(run.id)) is None


def test_non_authoritative_qwen_keeps_legacy_missing_config_mock_contract(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(audit_pipeline_module.settings, "use_remote_llm", False)
    monkeypatch.setattr(audit_pipeline_module.settings, "dashscope_api_key", "")

    result = audit_pipeline_module.QwenClient().audit_text("legacy fixture prompt")

    assert result["decision"] == "review"
    assert result["risk_level"] == "unknown"


def _counts(store: InvestigationCreationStore) -> tuple[int, int]:
    with sqlite3.connect(store.db_path) as connection:
        drafts = connection.execute(
            "SELECT COUNT(*) FROM investigation_drafts"
        ).fetchone()[0]
        runs = connection.execute(
            "SELECT COUNT(*) FROM investigation_runs"
        ).fetchone()[0]
    return int(drafts), int(runs)


def _confirmation_side_effects(stack: dict) -> tuple[int, int, int]:
    with sqlite3.connect(stack["store"].db_path) as connection:
        runs = connection.execute(
            "SELECT COUNT(*) FROM investigation_runs"
        ).fetchone()[0]
        idempotency = connection.execute(
            "SELECT COUNT(*) FROM investigation_run_idempotency_keys"
        ).fetchone()[0]
    with sqlite3.connect(stack["resources"].resource_db_path) as connection:
        has_jobs = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        jobs = (
            connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            if has_jobs
            else 0
        )
    return int(runs), int(idempotency), int(jobs)


def _existing_lexicon_configuration(
    stack: dict, lexicon_id: str, runtime_hash: str
) -> dict:
    return {
        "platform": "xhs",
        "investigation": {
            "mode": "search",
            "recall_plan": {
                "strategy": "existing_lexicon",
                "lexicon_id": lexicon_id,
                "expected_runtime_content_hash": runtime_hash,
            },
        },
        "audit_policy": _selection(_policy(stack)),
    }


def test_options_are_safe_bounded_and_have_no_m3_side_effects(m3_stack: dict):
    before = _counts(m3_stack["store"])
    confirmation_before = _confirmation_side_effects(m3_stack)
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(page_size=2),
        principal=_principal(m3_stack),
    )
    assert _counts(m3_stack["store"]) == before == (0, 0)
    assert _confirmation_side_effects(m3_stack) == confirmation_before == (0, 0, 0)
    assert [item.id for item in options.audit_policies] == ["policy_gambling"]
    assert len(options.ruleset_revisions) == 1
    serialized = json.dumps(options.model_dump(mode="json"), ensure_ascii=False)
    for forbidden in ("system_template", "source_mappings", "prompt_profile", "categories"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "domain_hint",
    ["世界杯 博彩 引流", "世界杯博彩引流", "gambling"],
)
def test_domain_hint_ranks_gambling_resources_without_filtering_valid_candidates(
    m3_stack: dict, domain_hint: str
):
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(domain_hint=domain_hint),
        principal=_principal(m3_stack),
    )

    assert options.audit_policies[0].id == "policy_gambling"
    assert options.ruleset_revisions[0].id == "ruleset-revision:gambling:v2"
    assert options.recall_lexicons[0].id == "gambling"
    assert "NO_PUBLISHED_AUDIT_POLICY" not in {
        blocker.code for blocker in options.blockers
    }


def test_unrelated_domain_hint_returns_no_policy_blocker_without_fallback(
    m3_stack: dict,
):
    before = _counts(m3_stack["store"])
    confirmation_before = _confirmation_side_effects(m3_stack)
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(
            domain_hint="completely unrelated astronomy",
            page_size=1,
        ),
        principal=_principal(m3_stack),
    )

    assert options.audit_policies == []
    assert len(options.recall_lexicons) == 1
    assert "NO_PUBLISHED_AUDIT_POLICY" in {
        blocker.code for blocker in options.blockers
    }
    assert _counts(m3_stack["store"]) == before == (0, 0)
    assert _confirmation_side_effects(m3_stack) == confirmation_before == (0, 0, 0)


def test_domain_hint_order_and_cursor_are_deterministic(m3_stack: dict):
    source = m3_stack["policies"].get("policy_gambling")
    policy = m3_stack["policies"].create(
        id="policy_alpha",
        name="Alpha candidate",
        description="A deterministic secondary candidate.",
        config=source["published_config"],
    )
    m3_stack["policies"].publish(
        policy["id"],
        expected_draft_hash=m3_stack["policies"].draft_hash(policy),
    )

    query = QueryInvestigationOptions(domain_hint="alpha", page_size=20)
    first = m3_stack["service"].query_investigation_options(
        query, principal=_principal(m3_stack)
    )
    second = m3_stack["service"].query_investigation_options(
        query, principal=_principal(m3_stack)
    )
    first_ids = [item.id for item in first.audit_policies]
    assert first_ids == ["policy_alpha"]
    assert [item.id for item in second.audit_policies] == first_ids

    first_page = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(domain_hint="alpha", page_size=1),
        principal=_principal(m3_stack),
    )
    second_page = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(
            domain_hint="alpha", page_size=1, cursor=first_page.next_cursor
        ),
        principal=_principal(m3_stack),
    )
    assert [item.id for item in first_page.audit_policies] == ["policy_alpha"]
    assert second_page.audit_policies == []


def test_creator_options_never_require_or_return_recall_lexicons(m3_stack: dict):
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(mode="creator"),
        principal=_principal(m3_stack),
    )
    assert options.audit_policies
    assert options.recall_lexicons == []
    assert "NO_PUBLISHED_RECALL_LEXICON" not in {
        blocker.code for blocker in options.blockers
    }


def test_search_options_block_when_no_real_recall_lexicon_exists(m3_stack: dict):
    with sqlite3.connect(m3_stack["resources"].resource_db_path) as connection:
        connection.execute("DELETE FROM lexicon_keywords")
        connection.execute("DELETE FROM lexicon_prompt_profiles")
        connection.execute("DELETE FROM lexicon_categories")
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(mode="search"),
        principal=_principal(m3_stack),
    )
    assert options.recall_lexicons == []
    assert "NO_PUBLISHED_RECALL_LEXICON" in {
        blocker.code for blocker in options.blockers
    }


def test_explicit_policy_request_is_strict_and_not_filtered_by_hint(m3_stack: dict):
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(
            domain_hint="completely unrelated astronomy",
            audit_policy_ids=["policy_gambling"],
        ),
        principal=_principal(m3_stack),
    )
    assert [item.id for item in options.audit_policies] == ["policy_gambling"]
    assert options.blockers == []


def test_no_valid_published_policy_still_returns_blocker(m3_stack: dict):
    with sqlite3.connect(m3_stack["resources"].resource_db_path) as connection:
        connection.execute(
            "UPDATE audit_policies SET status = 'draft' WHERE id = ?",
            ("policy_gambling",),
        )

    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(domain_hint="世界杯 博彩 引流"),
        principal=_principal(m3_stack),
    )
    assert options.audit_policies == []
    assert "NO_PUBLISHED_AUDIT_POLICY" in {
        blocker.code for blocker in options.blockers
    }


@pytest.mark.parametrize("policy_state", ["missing", "draft", "invalid_hash"])
def test_explicit_invalid_policy_request_remains_fail_closed(
    m3_stack: dict, policy_state: str
):
    requested_id = "policy_gambling"
    with sqlite3.connect(m3_stack["resources"].resource_db_path) as connection:
        if policy_state == "missing":
            requested_id = "policy_missing"
        elif policy_state == "draft":
            connection.execute(
                "UPDATE audit_policies SET status = 'draft' WHERE id = ?",
                (requested_id,),
            )
        else:
            connection.execute("DROP TRIGGER immutable_rule_set_revisions_update")
            connection.execute(
                "UPDATE rule_set_revisions SET content_hash = ? WHERE id = ?",
                ("0" * 64, "ruleset-revision:gambling:v2"),
            )

    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(
            domain_hint="世界杯 博彩 引流",
            audit_policy_ids=[requested_id],
        ),
        principal=_principal(m3_stack),
    )
    assert options.audit_policies == []
    codes = {blocker.code for blocker in options.blockers}
    assert "NO_PUBLISHED_AUDIT_POLICY" in codes
    if policy_state == "invalid_hash":
        assert "INVALID_RULESET_REFERENCE" in codes


def test_explicit_missing_lexicon_remains_unavailable(m3_stack: dict):
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(
            domain_hint="世界杯 博彩 引流",
            lexicon_ids=["missing-lexicon"],
        ),
        principal=_principal(m3_stack),
    )
    assert len(options.recall_lexicons) == 1
    assert options.recall_lexicons[0].id == "missing-lexicon"
    assert not options.recall_lexicons[0].available


def test_lexicon_options_only_return_enabled_main_terms(m3_stack: dict):
    category = m3_stack["lexicons"].upsert_category(
        category_id="collection-safe",
        title="Collection safe",
        entries=[
            {
                "main_term": "alpha",
                "variants": ["alpha-variant"],
                "query_type": "keyword",
                "enabled": True,
            },
            {
                "main_term": "tag-only",
                "variants": [],
                "query_type": "tag",
                "enabled": True,
            },
            {
                "main_term": "second-main",
                "variants": [],
                "query_type": "keyword",
                "enabled": True,
            },
            {
                "main_term": "disabled",
                "variants": [],
                "query_type": "keyword",
                "enabled": False,
            },
        ],
    )
    assert category["id"] == "collection-safe"
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(
            lexicon_ids=["collection-safe"],
            include_lexicon_terms_for_ids=["collection-safe"],
            lexicon_term_limit=1,
        ),
        principal=_principal(m3_stack),
    )
    lexicon = options.recall_lexicons[0]
    assert lexicon.enabled_main_terms == ["alpha"]
    assert lexicon.enabled_main_term_count == 2
    assert lexicon.enabled_main_terms_returned == 1
    assert lexicon.terms_truncated
    serialized = json.dumps(lexicon.model_dump(mode="json"), ensure_ascii=False)
    assert "alpha-variant" not in serialized
    assert "tag-only" not in serialized
    assert "disabled" not in serialized


def test_temporary_terms_are_trimmed_deduped_and_reference_only(m3_stack: dict):
    draft = _create_draft(
        m3_stack,
        _temporary_configuration(m3_stack, [" first ", "", "second", "first"]),
    )
    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert preview.resolved_search_terms == ["first", "second"]
    assert preview.recall_plan.source_lexicon_ids == ["general-reference"]
    assert preview.recall_plan.temporary_terms == ["first", "second"]
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=1,
            confirmed=True,
            idempotency_key="temporary:1",
        ),
        principal=_principal(m3_stack),
    )
    snapshot = run.confirmed_configuration
    assert snapshot["resolved_search_terms"] == ["first", "second"]
    assert snapshot["recall_plan"]["temporary_terms"] == ["first", "second"]
    assert snapshot["execution"]["keyword"] == "first,second"
    assert "general-reference" not in snapshot["resolved_search_terms"]


def test_empty_search_terms_block_preview_and_confirmation(m3_stack: dict):
    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, [" ", ""]))
    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert not preview.can_confirm
    assert [item.code for item in preview.blockers] == ["NO_SEARCH_TERMS"]
    with pytest.raises(ConfigurationValidationError) as caught:
        m3_stack["service"].confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key="empty:1",
            ),
            principal=_principal(m3_stack),
        )
    assert caught.value.code == "NO_SEARCH_TERMS"
    assert _counts(m3_stack["store"])[1] == 0


def test_creator_mode_has_no_recall_and_reports_platform_mismatch(m3_stack: dict):
    configuration = {
        "platform": "dy",
        "investigation": {
            "mode": "creator",
            "creator_url": "https://www.douyin.com/user/MS4wLjABAAAA-valid",
        },
        "audit_policy": _selection(_policy(m3_stack)),
    }
    draft = _create_draft(m3_stack, configuration)
    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert preview.can_confirm
    assert preview.creator_url.startswith("https://www.douyin.com/user/")
    assert preview.resolved_search_terms == []
    assert preview.recall_plan.strategy == "none"

    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=1,
            confirmed=True,
            idempotency_key="creator-snapshot:1",
        ),
        principal=_principal(m3_stack),
    )
    snapshot = run.confirmed_configuration
    account = _account_for_platform(m3_stack, "dy")
    assert snapshot["mode"] == "creator"
    assert snapshot["creator_url"] == configuration["investigation"]["creator_url"]
    assert snapshot["resolved_search_terms"] == []
    assert snapshot["recall_plan"] is None
    assert snapshot["execution"]["crawl_mode"] == "creator"
    assert snapshot["execution"]["keyword"] == ""
    assert snapshot["execution"]["crawler_account_id"] == account["id"]
    assert snapshot["execution"]["crawler_account_display_name"] == account["display_name"]
    assert snapshot["execution"]["crawler_account_confirmed_state"]["has_auth_state"] is True
    assert snapshot["execution"]["max_notes"] == 1
    assert snapshot["execution"]["analyze_limit"] == 1

    mismatched = dict(configuration)
    mismatched["platform"] = "xhs"
    bad_draft = _create_draft(m3_stack, mismatched)
    bad_preview = m3_stack["service"].get_confirmation_preview(
        bad_draft.id, principal=_principal(m3_stack)
    )
    assert [item.code for item in bad_preview.blockers] == ["PLATFORM_MISMATCH"]

    post_url_configuration = {
        **configuration,
        "investigation": {
            "mode": "creator",
            "creator_url": "https://www.douyin.com/video/1234567890",
        },
    }
    post_url_draft = _create_draft(m3_stack, post_url_configuration)
    post_url_preview = m3_stack["service"].get_confirmation_preview(
        post_url_draft.id, principal=_principal(m3_stack)
    )
    assert [item.code for item in post_url_preview.blockers] == [
        "INVALID_CREATOR_URL"
    ]
    assert _counts(m3_stack["store"])[1] == 1


@pytest.mark.parametrize(
    "investigation",
    [
        {
            "mode": "search",
            "creator_url": "https://www.douyin.com/user/MS4wLjABAAAA-valid",
            "recall_plan": {
                "strategy": "temporary_terms",
                "terms": ["term"],
                "source_lexicon_ids": [],
            },
        },
        {
            "mode": "creator",
            "creator_url": "https://www.douyin.com/user/MS4wLjABAAAA-valid",
            "recall_plan": {
                "strategy": "temporary_terms",
                "terms": ["term"],
                "source_lexicon_ids": [],
            },
        },
    ],
)
def test_modes_reject_cross_mode_fields(m3_stack: dict, investigation: dict):
    with pytest.raises(ValidationError):
        CreateDraftCommand.model_validate(
            {
                "title": "invalid cross-mode fields",
                "objective": "reject ambiguous mode",
                "configuration": {
                    "platform": "dy",
                    "investigation": investigation,
                    "audit_policy": _selection(_policy(m3_stack)),
                },
            }
        )


def test_draft_cannot_set_internal_authoritative_m3_marker(m3_stack: dict):
    configuration = _temporary_configuration(m3_stack, ["internal marker"])
    configuration["_authoritative_m3_contract"] = True

    with pytest.raises(ValidationError):
        CreateDraftCommand.model_validate(
            {
                "title": "Reject internal marker",
                "objective": "The client cannot select the authoritative contract.",
                "configuration": configuration,
            }
        )


def test_existing_lexicon_snapshot_and_explicit_term_edit_are_revisioned(
    m3_stack: dict,
):
    m3_stack["lexicons"].upsert_category(
        category_id="revisioned-recall",
        title="Revisioned recall",
        entries=[
            {
                "main_term": "enabled-main",
                "variants": ["variant-must-not-crawl"],
                "query_type": "keyword",
                "enabled": True,
            },
            {
                "main_term": "tag-must-not-crawl",
                "variants": [],
                "query_type": "tag",
                "enabled": True,
            },
            {
                "main_term": "disabled-must-not-crawl",
                "variants": [],
                "query_type": "keyword",
                "enabled": False,
            },
        ],
    )
    runtime_hash = m3_stack["lexicons"].runtime_content_hash("revisioned-recall")
    draft = _create_draft(
        m3_stack,
        _existing_lexicon_configuration(
            m3_stack, "revisioned-recall", runtime_hash
        ),
    )
    saved_plan = draft.configuration.investigation.recall_plan
    assert saved_plan.strategy == "existing_lexicon"
    assert saved_plan.expected_runtime_content_hash == runtime_hash
    assert saved_plan.enabled_main_terms == ["enabled-main"]

    temporary_configuration = draft.configuration.model_dump(mode="json")
    temporary_configuration["investigation"]["recall_plan"] = {
        "strategy": "temporary_terms",
        "terms": ["user-edited-main"],
        "source_lexicon_ids": ["revisioned-recall"],
    }
    updated = m3_stack["service"].update_draft(
        UpdateDraftCommand(
            draft_id=draft.id,
            expected_revision=1,
            configuration=temporary_configuration,
        ),
        principal=_principal(m3_stack),
    )
    assert updated.current_revision == 2
    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert preview.resolved_search_terms == ["user-edited-main"]
    assert preview.recall_plan.temporary_terms == ["user-edited-main"]
    assert preview.recall_plan.source_lexicon_ids == ["revisioned-recall"]
    assert _confirmation_side_effects(m3_stack) == (0, 0, 0)

    with sqlite3.connect(m3_stack["store"].db_path) as connection:
        revisions = connection.execute(
            "SELECT revision, configuration_json FROM investigation_draft_revisions "
            "WHERE draft_id = ? ORDER BY revision",
            (draft.id,),
        ).fetchall()
    assert [row[0] for row in revisions] == [1, 2]
    first_plan = json.loads(revisions[0][1])["investigation"]["recall_plan"]
    second_plan = json.loads(revisions[1][1])["investigation"]["recall_plan"]
    assert first_plan["enabled_main_terms"] == ["enabled-main"]
    assert first_plan["expected_runtime_content_hash"] == runtime_hash
    assert second_plan["terms"] == ["user-edited-main"]


def test_missing_policy_keeps_draft_and_returns_management_url(m3_stack: dict):
    configuration = _temporary_configuration(m3_stack, ["term"])
    configuration["audit_policy"] = None
    draft = _create_draft(m3_stack, configuration)
    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert not preview.can_confirm
    blocker = preview.blockers[0]
    assert blocker.code == "NO_PUBLISHED_AUDIT_POLICY"
    assert blocker.management_url == (
        f"/rule-assistant/rulesets?return_to=/investigation&draft_id={draft.id}"
    )
    assert _counts(m3_stack["store"]) == (1, 0)


def test_preview_is_dynamic_and_never_persisted(m3_stack: dict):
    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, ["one"]))
    before = _counts(m3_stack["store"])
    first = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    second = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert first == second
    assert _counts(m3_stack["store"]) == before == (1, 0)
    with sqlite3.connect(m3_stack["store"].db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(investigation_drafts)"
            ).fetchall()
        }
    assert "confirmation_preview" not in columns


def test_existing_lexicon_snapshot_is_clean_frozen_and_server_limited(m3_stack: dict):
    m3_stack["lexicons"].upsert_category(
        category_id="freeze-safe",
        title="Freeze safe",
        entries=[
            {
                "main_term": "first",
                "variants": ["variant"],
                "query_type": "keyword",
                "enabled": True,
            },
            {
                "main_term": "second",
                "variants": [],
                "query_type": "keyword",
                "enabled": True,
            },
        ],
    )
    runtime_hash = m3_stack["lexicons"].runtime_content_hash("freeze-safe")
    configuration = {
        "platform": "xhs",
        "investigation": {
            "mode": "search",
            "recall_plan": {
                "strategy": "existing_lexicon",
                "lexicon_id": "freeze-safe",
                "expected_runtime_content_hash": runtime_hash,
            },
        },
        "audit_policy": _selection(_policy(m3_stack)),
    }
    draft = _create_draft(m3_stack, configuration)
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=1,
            confirmed=True,
            idempotency_key="freeze:1",
        ),
        principal=_principal(m3_stack),
    )
    snapshot = run.confirmed_configuration
    assert snapshot["schema_version"] == "investigation-run-config-v3"
    assert snapshot["resolved_search_terms"] == ["first", "second"]
    assert snapshot["recall_plan"]["enabled_main_terms"] == ["first", "second"]
    assert snapshot["recall_plan"]["runtime_content_hash"] == runtime_hash
    assert snapshot["max_notes"] == 1
    assert snapshot["execution"]["max_notes"] == 1
    assert snapshot["audit_policy"]["published_config_hash"]
    assert snapshot["ruleset_revision"]["content_hash"]
    assert snapshot["confirmed_by"] == _principal(m3_stack).id
    assert snapshot["confirmed_at"]
    assert snapshot["config_hash"]
    assert "variant" not in json.dumps(snapshot, ensure_ascii=False)

    m3_stack["lexicons"].add_keyword(
        category_id="freeze-safe", keyword="later", enabled=True
    )
    stored = m3_stack["store"].get_run(run.id, principal=_principal(m3_stack).id)
    assert stored.confirmed_configuration["resolved_search_terms"] == ["first", "second"]
    replay = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=1,
            confirmed=True,
            idempotency_key="freeze:1",
        ),
        principal=_principal(m3_stack),
    )
    assert replay.id == run.id


def test_pre_confirmation_resource_change_is_stale(m3_stack: dict):
    lexicon = m3_stack["lexicons"].get_category("gambling")
    runtime_hash = m3_stack["lexicons"].runtime_content_hash("gambling")
    draft = _create_draft(
        m3_stack,
        {
            "platform": "xhs",
            "investigation": {
                "mode": "search",
                "recall_plan": {
                    "strategy": "existing_lexicon",
                    "lexicon_id": "gambling",
                    "expected_runtime_content_hash": runtime_hash,
                },
            },
            "audit_policy": _selection(_policy(m3_stack)),
        },
    )
    m3_stack["lexicons"].add_keyword(
        category_id=lexicon["id"], keyword="new confirmed candidate", enabled=True
    )
    preview = m3_stack["service"].get_confirmation_preview(
        draft.id, principal=_principal(m3_stack)
    )
    assert "RESOURCE_STALE" in [item.code for item in preview.blockers]
    with pytest.raises(ResourceStaleError):
        m3_stack["service"].confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key="stale:1",
            ),
            principal=_principal(m3_stack),
        )
    assert _counts(m3_stack["store"])[1] == 0


def test_resource_writer_committed_first_is_stale_without_side_effects(
    m3_stack: dict,
):
    m3_stack["lexicons"].upsert_category(
        category_id="writer-first",
        title="Writer first",
        terms=["before"],
    )
    runtime_hash = m3_stack["lexicons"].runtime_content_hash("writer-first")
    draft = _create_draft(
        m3_stack,
        _existing_lexicon_configuration(m3_stack, "writer-first", runtime_hash),
    )
    writer_committed = threading.Event()
    writer_errors: list[BaseException] = []

    def write_resource() -> None:
        try:
            m3_stack["lexicons"].add_keyword(
                category_id="writer-first", keyword="committed-before-confirm"
            )
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_committed.set()

    writer = threading.Thread(target=write_resource)
    writer.start()
    assert writer_committed.wait(2)
    writer.join(timeout=2)
    assert not writer_errors

    with pytest.raises(ResourceStaleError):
        m3_stack["service"].confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key="writer-first:1",
            ),
            principal=_principal(m3_stack),
        )

    assert _confirmation_side_effects(m3_stack) == (0, 0, 0)
    stored_draft = m3_stack["store"].get_draft(
        draft.id, principal=_principal(m3_stack).id
    )
    assert stored_draft.status.value == "DRAFT"
    updated = m3_stack["service"].update_draft(
        UpdateDraftCommand(
            draft_id=draft.id,
            expected_revision=1,
            title="Still editable after stale resources",
        ),
        principal=_principal(m3_stack),
    )
    assert updated.current_revision == 2


def test_confirmation_fence_blocks_resource_writer_until_run_commit(
    m3_stack: dict, monkeypatch: pytest.MonkeyPatch
):
    m3_stack["lexicons"].upsert_category(
        category_id="confirm-first",
        title="Confirm first",
        terms=["frozen-before-writer"],
    )
    runtime_hash = m3_stack["lexicons"].runtime_content_hash("confirm-first")
    draft = _create_draft(
        m3_stack,
        _existing_lexicon_configuration(m3_stack, "confirm-first", runtime_hash),
    )
    creation_store_entered = threading.Event()
    writer_attempted = threading.Event()
    writer_finished = threading.Event()
    writer_errors: list[BaseException] = []
    original_confirm = m3_stack["store"].confirm_and_queue

    def observe_creation_commit(*args, **kwargs):
        creation_store_entered.set()
        assert writer_attempted.wait(2)
        assert not writer_finished.wait(0.1)
        run = original_confirm(*args, **kwargs)
        assert _counts(m3_stack["store"])[1] == 1
        assert not writer_finished.is_set()
        return run

    monkeypatch.setattr(
        m3_stack["store"], "confirm_and_queue", observe_creation_commit
    )

    def write_resource() -> None:
        try:
            assert creation_store_entered.wait(2)
            writer_attempted.set()
            m3_stack["lexicons"].add_keyword(
                category_id="confirm-first", keyword="written-after-run-commit"
            )
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    writer = threading.Thread(target=write_resource)
    writer.start()
    run = m3_stack["service"].confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=1,
            confirmed=True,
            idempotency_key="confirm-first:1",
        ),
        principal=_principal(m3_stack),
    )
    assert writer_finished.wait(2)
    writer.join(timeout=2)
    assert not writer_errors
    assert run.confirmed_configuration["resolved_search_terms"] == [
        "frozen-before-writer"
    ]
    assert m3_stack["lexicons"].enabled_main_terms("confirm-first") == [
        "frozen-before-writer",
        "written-after-run-commit",
    ]


def test_locked_policy_hash_drift_is_stale_without_confirmation_writes(m3_stack: dict):
    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, ["term"]))
    with sqlite3.connect(m3_stack["resources"].resource_db_path) as connection:
        row = connection.execute(
            "SELECT published_config_json FROM audit_policies WHERE id = ?",
            (draft.configuration.audit_policy.id,),
        ).fetchone()
        changed = json.loads(row[0])
        changed["fencing_probe"] = "changed"
        connection.execute(
            "UPDATE audit_policies SET published_config_json = ? WHERE id = ?",
            (json.dumps(changed), draft.configuration.audit_policy.id),
        )

    with pytest.raises(ResourceStaleError):
        m3_stack["service"].confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key="policy-hash-stale:1",
            ),
            principal=_principal(m3_stack),
        )
    assert _confirmation_side_effects(m3_stack) == (0, 0, 0)


def test_locked_ruleset_hash_validation_is_stale_without_confirmation_writes(
    m3_stack: dict,
):
    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, ["term"]))
    revision_id = draft.configuration.audit_policy.expected_ruleset_revision_id
    with sqlite3.connect(m3_stack["resources"].resource_db_path) as connection:
        connection.execute("DROP TRIGGER immutable_rule_set_revisions_update")
        connection.execute(
            "UPDATE rule_set_revisions SET content_hash = ? WHERE id = ?",
            ("0" * 64, revision_id),
        )

    with pytest.raises(ResourceStaleError):
        m3_stack["service"].confirm_and_queue(
            ConfirmAndQueueCommand(
                draft_id=draft.id,
                expected_revision=1,
                confirmed=True,
                idempotency_key="ruleset-hash-stale:1",
            ),
            principal=_principal(m3_stack),
        )
    assert _confirmation_side_effects(m3_stack) == (0, 0, 0)


def test_confirmation_is_idempotent_and_tool_queries_do_not_start_work(m3_stack: dict):
    tools = InvestigationCreationToolService(m3_stack["service"])
    principal = _principal(m3_stack)
    before = _counts(m3_stack["store"])
    options = tools.execute(
        "query_investigation_options",
        {"domain_hint": "gambling"},
        principal=principal,
    )
    assert options["audit_policies"][0]["id"] == "policy_gambling"
    assert _counts(m3_stack["store"]) == before
    assert "actor_id" not in json.dumps(tools.definitions())

    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, ["term"]))
    arguments = {
        "draft_id": draft.id,
        "expected_revision": 1,
        "confirmed": True,
        "idempotency_key": "tool-confirm:1",
    }
    first = tools.execute(
        "confirm_and_queue_investigation", arguments, principal=principal
    )
    second = tools.execute(
        "confirm_and_queue_investigation", arguments, principal=principal
    )
    assert first["run_id"] == second["run_id"]
    assert _counts(m3_stack["store"])[1] == 1


def test_request_principal_reaches_all_m3_resource_queries(tmp_path: Path):
    resource_db = tmp_path / "principal-resources.sqlite3"
    lexicons = LexiconStore(resource_db)
    policies = AuditPolicyStore(resource_db)
    delegate = RuleSetService(RuleSetStore(resource_db))

    class RecordingRuleSetService:
        def __init__(self) -> None:
            self.store = delegate.store
            self.principal_ids: list[str] = []

        def get_published(self, revision_id: str, *, principal, connection=None):
            self.principal_ids.append(principal.id)
            return delegate.get_published(
                revision_id, principal=principal, connection=connection
            )

        def compile_for_execution(
            self, revision_id: str, *, audit_policy, principal, connection=None
        ):
            self.principal_ids.append(principal.id)
            return delegate.compile_for_execution(
                revision_id,
                audit_policy=audit_policy,
                principal=principal,
                connection=connection,
            )

    rulesets = RecordingRuleSetService()
    provider_calls: list[str] = []

    def resource_provider() -> Principal:
        provider_calls.append("called")
        return Principal("resource-provider")

    crawler_accounts = CrawlerAccountStore(resource_db)
    account = crawler_accounts.create(
        platform="xhs",
        display_name="xhs principal fixture account",
    )
    crawler_accounts.save_auth_state(account["id"], "synthetic-fixture-ciphertext")
    resolver = InvestigationConfigurationResolver(
        lexicon_store=lexicons,
        policy_store=policies,
        crawler_account_store=crawler_accounts,
        ruleset_service=rulesets,
        principal_provider=resource_provider,
    )
    resources = InvestigationResourceService(
        lexicon_store=lexicons,
        policy_store=policies,
        ruleset_service=rulesets,
        configuration_resolver=resolver,
    )
    store = InvestigationCreationStore(tmp_path / "principal-creation.sqlite3")
    service = InvestigationCreationService(
        store,
        configuration_resolver=resolver,
        resource_service=resources,
    )
    request_owner = Principal("request-owner")
    options = service.query_investigation_options(
        QueryInvestigationOptions(domain_hint="世界杯 博彩 引流"),
        principal=request_owner,
    )
    configuration = {
        "platform": "xhs",
        "investigation": {
            "mode": "search",
            "recall_plan": {
                "strategy": "temporary_terms",
                "terms": ["term"],
            },
        },
        "audit_policy": _selection(options.audit_policies[0]),
    }
    draft = service.create_draft(
        CreateDraftCommand(
            title="Principal",
            objective="Use the authenticated request owner.",
            configuration=configuration,
        ),
        principal=request_owner,
    )
    service.get_confirmation_preview(draft.id, principal=request_owner)
    service.confirm_and_queue(
        ConfirmAndQueueCommand(
            draft_id=draft.id,
            expected_revision=1,
            confirmed=True,
            idempotency_key="principal:1",
        ),
        principal=request_owner,
    )

    assert rulesets.principal_ids
    assert set(rulesets.principal_ids) == {"request-owner"}
    assert provider_calls == []


class _HermesStubContext:
    def __init__(self) -> None:
        self.names: list[str] = []

    def register_middleware(self, *_args, **_kwargs) -> None:
        return None

    def register_tool(self, *, name: str, **_kwargs) -> None:
        self.names.append(name)


def test_hermes_creation_product_mode_registers_only_m3_application_tools():
    context = _HermesStubContext()

    class Agent:
        _api_max_retries = 0

    environment = {
        "HERMES_INVESTIGATION_CREATION_MODE": "1",
        "HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE": "1",
        "HERMES_INVESTIGATION_REAL_REPORT_MODE": "1",
        "HERMES_INVESTIGATION_REPORT_TASK_MODE": "1",
        "HERMES_INVESTIGATION_TASK_MODE": "1",
    }
    with patch.dict(os.environ, environment, clear=False):
        HermesRuntimeBinding().create_agent(
            session_id="m3-creation-product",
            product_mode="creation",
            agent_factory=lambda **_options: Agent(),
        )
        assert os.environ["HERMES_INVESTIGATION_CREATION_MODE"] == "1"
        assert os.environ["HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE"] == "0"
        assert os.environ["HERMES_INVESTIGATION_REAL_REPORT_MODE"] == "0"
        assert os.environ["HERMES_INVESTIGATION_REPORT_TASK_MODE"] == "0"
        assert os.environ["HERMES_INVESTIGATION_TASK_MODE"] == "0"
        register_hermes_plugin(context)
    assert context.names == [
        "query_investigation_options",
        "create_investigation_draft",
        "update_investigation_draft",
        "get_investigation_draft",
        "confirm_and_queue_investigation",
        "get_investigation_run",
    ]


def test_hermes_account_activity_product_mode_keeps_m22_catalog():
    context = _HermesStubContext()
    environment = {
        "HERMES_INVESTIGATION_CREATION_MODE": "1",
        "HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE": "0",
        "HERMES_INVESTIGATION_REAL_REPORT_MODE": "0",
        "HERMES_INVESTIGATION_REPORT_TASK_MODE": "0",
        "HERMES_INVESTIGATION_TASK_MODE": "0",
    }
    with patch.dict(os.environ, environment, clear=False):
        HermesRuntimeBinding.activate_product_mode()
        assert os.environ["HERMES_INVESTIGATION_CREATION_MODE"] == "0"
        assert os.environ["HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE"] == "1"
        register_hermes_plugin(context)
    assert context.names == [item["name"] for item in M2_ACCOUNT_ACTIVITY_TOOLS]


@pytest.mark.parametrize("field", ["terms", "source_lexicon_ids"])
@pytest.mark.parametrize("invalid_value", [123, True, {"unexpected": "value"}])
def test_non_string_temporary_term_inputs_return_http_422(
    m3_stack: dict, field: str, invalid_value: object
):
    configuration = _temporary_configuration(m3_stack, ["valid"])
    configuration["investigation"]["recall_plan"][field] = [invalid_value]
    app = FastAPI()
    app.include_router(
        create_investigation_creation_router(
            m3_stack["service"], principal_provider=m3_stack["principals"]
        )
    )
    response = TestClient(app).post(
        "/api/investigation-drafts",
        json={
            "title": "Strict strings",
            "objective": "Reject non-string recall values.",
            "configuration": configuration,
        },
    )
    assert response.status_code == 422
    assert _counts(m3_stack["store"])[0] == 0


def test_other_m3_strict_string_lists_do_not_preconvert_values():
    with pytest.raises(ValidationError):
        QueryInvestigationOptions.model_validate({"lexicon_ids": [123]})


def test_http_resource_stale_error_is_structured(m3_stack: dict):
    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, ["term"]))
    policy = m3_stack["policies"].get("policy_gambling")
    assert policy is not None
    m3_stack["policies"].update("policy_gambling", name="Changed Draft name")

    app = FastAPI()
    app.include_router(
        create_investigation_creation_router(
            m3_stack["service"], principal_provider=m3_stack["principals"]
        )
    )
    response = TestClient(app).post(
        f"/api/investigation-drafts/{draft.id}/confirm-and-queue",
        headers={"Idempotency-Key": "http-stale:1"},
        json={"expected_revision": 1, "confirmed": True},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "RESOURCE_STALE"
    assert detail["details"]["resources"]
    assert _counts(m3_stack["store"])[1] == 0
