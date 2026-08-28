from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.investigation_creation import create_investigation_creation_router
from backend.audit_agent.audit_policy_store import AuditPolicyStore
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.lexicon_store import LexiconStore
from backend.investigation_creation.adapters import InvestigationConfigurationResolver
from backend.investigation_creation.contracts import (
    ConfirmAndQueueCommand,
    CreateDraftCommand,
    QueryInvestigationOptions,
    UpdateDraftCommand,
)
from backend.investigation_creation.errors import (
    ConfigurationValidationError,
    ResourceStaleError,
)
from backend.investigation_creation.principal import LocalPrincipalProvider, Principal
from backend.investigation_creation.resources import InvestigationResourceService
from backend.investigation_creation.service import InvestigationCreationService
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import InvestigationCreationToolService
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
    resolver = InvestigationConfigurationResolver(
        lexicon_store=lexicons,
        policy_store=policies,
        crawler_account_store=CrawlerAccountStore(resource_db),
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
    options = m3_stack["service"].query_investigation_options(
        QueryInvestigationOptions(page_size=2),
        principal=_principal(m3_stack),
    )
    assert _counts(m3_stack["store"]) == before == (0, 0)
    assert [item.id for item in options.audit_policies] == ["policy_gambling"]
    assert len(options.ruleset_revisions) == 1
    serialized = json.dumps(options.model_dump(mode="json"), ensure_ascii=False)
    for forbidden in ("system_template", "source_mappings", "prompt_profile", "categories"):
        assert forbidden not in serialized


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

    mismatched = dict(configuration)
    mismatched["platform"] = "xhs"
    bad_draft = _create_draft(m3_stack, mismatched)
    bad_preview = m3_stack["service"].get_confirmation_preview(
        bad_draft.id, principal=_principal(m3_stack)
    )
    assert [item.code for item in bad_preview.blockers] == ["PLATFORM_MISMATCH"]


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

    resolver = InvestigationConfigurationResolver(
        lexicon_store=lexicons,
        policy_store=policies,
        crawler_account_store=CrawlerAccountStore(resource_db),
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
        QueryInvestigationOptions(domain_hint="gambling"),
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
