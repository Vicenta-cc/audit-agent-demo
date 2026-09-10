from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from backend.investigation_creation.contracts import InvestigationDraftConfiguration
from backend.investigation_creation.errors import ProposalNotFoundError, ProposalVersionConflictError
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.service import InvestigationCreationService
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import (
    HERMES_M3_TOOL_SCHEMAS, HermesToolExecutionIdentity, InvestigationCreationToolService,
    configure_hermes_investigation_creation_tools, dispatch_hermes_investigation_creation_tool,
)
from backend.rulesets.compiler import compile_ruleset_content, content_hash
from backend.rulesets.contracts import RuleSetContent
from test_investigation_creation_conversation import (
    creation_stack, _run_scripted_creation_turn, _t1_temporary_arguments,
)


@pytest.fixture
def content():
    return json.loads((Path(__file__).parent / "fixtures/recruitment_fraud_ruleset.json").read_text())


@pytest.fixture
def app(tmp_path):
    return InvestigationCreationService(
        InvestigationCreationStore(tmp_path / "creation.sqlite3"), configuration_resolver=None,
    )


def rows(store, table="ruleset_proposals"):
    with sqlite3.connect(store.db_path) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def call(app, name, arguments, *, session="session-a", turn="turn-1", tool_call="call-1"):
    return InvestigationCreationToolService(app).execute_with_identity(
        name, arguments, principal=Principal("principal-a"),
        identity=HermesToolExecutionIdentity.require(
            session_id=session, turn_id=turn, tool_call_id=tool_call,
        ),
    )


def test_create_canonical_content_compile_and_durable_minimal_row(app, content):
    proposal = app.create_ruleset_proposal(content, session_id="session-a")
    assert proposal.version == 1
    assert proposal.proposal_id.startswith("ruleset-proposal:")
    assert proposal.content == RuleSetContent.model_validate(content)
    assert proposal.content_hash == content_hash(content)
    assert proposal.created_at == proposal.updated_at
    compile_ruleset_content(proposal.content)
    reopened = InvestigationCreationStore(app.store.db_path)
    assert reopened.get_ruleset_proposal(proposal.proposal_id, session_id="session-a") == proposal
    with sqlite3.connect(app.store.db_path) as connection:
        columns = {r[1] for r in connection.execute("PRAGMA table_info(ruleset_proposals)")}
    assert columns == {"proposal_id", "session_id", "version", "content_hash", "content_json",
                       "created_at", "updated_at"}
    second = app.create_ruleset_proposal(dict(reversed(list(content.items()))), session_id="session-a")
    assert second.proposal_id != proposal.proposal_id
    assert second.content_hash == proposal.content_hash
    assert len(rows(app.store)) == 2


def test_update_and_canonical_noop_keep_timestamps(app, content):
    first = app.create_ruleset_proposal(content, session_id="session-a")
    assert app.update_ruleset_proposal(
        first.proposal_id, session_id="session-a", expected_version=1,
        content=first.content.model_dump(mode="json"),
    ) == first
    content["categories"][0]["rules"][0]["hit_condition"] = "Explicitly demand advance payment."
    second = app.update_ruleset_proposal(
        first.proposal_id, session_id="session-a", expected_version=1, content=content,
    )
    assert second.version == 2
    assert second.content_hash == content_hash(content) != first.content_hash
    assert second.created_at == first.created_at
    assert second.updated_at > first.updated_at
    assert len(rows(app.store)) == 1


@pytest.mark.parametrize("same_content", [True, False])
def test_stale_version_never_mutates_even_for_noop(app, content, same_content):
    first = app.create_ruleset_proposal(content, session_id="session-a")
    for version in (1, 2):
        content["name"] += " revised"
        current = app.update_ruleset_proposal(
            first.proposal_id, session_id="session-a", expected_version=version, content=content,
        )
    if not same_content:
        content["name"] += " stale edit"
    before = rows(app.store)
    result = call(app, "update_ruleset_proposal", {
        "proposal_id": first.proposal_id, "expected_version": 2, "content": content,
    })
    assert result["error"]["code"] == "RULESET_PROPOSAL_STALE"
    assert result["error"]["details"]["current_version"] == 3
    assert rows(app.store) == before
    assert app.get_ruleset_proposal(first.proposal_id, session_id="session-a") == current


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["schema", "identity", "budget", "compiler"])
def test_invalid_content_never_mutates(app, content, operation, failure):
    first = app.create_ruleset_proposal(content, session_id="session-a") if operation == "update" else None
    if failure == "schema":
        content["unexpected"] = True
    elif failure == "identity":
        content["categories"].append(deepcopy(content["categories"][0]))
    elif failure == "budget":
        rule = content["categories"][0]["rules"][0]
        content["categories"][0]["rules"] = [
            {**rule, "rule_id": f"long.{i}", "hit_condition": "x" * 4000} for i in range(12)
        ]
    before = rows(app.store)
    arguments = {"content": content}
    if first:
        arguments.update(proposal_id=first.proposal_id, expected_version=1)
    with patch(
        "backend.investigation_creation.service.compile_ruleset_content",
        side_effect=RuntimeError("compiler unavailable") if failure == "compiler" else None,
        wraps=None if failure == "compiler" else compile_ruleset_content,
    ):
        result = call(app, f"{operation}_ruleset_proposal", arguments)
    assert result["status"] == "error"
    assert rows(app.store) == before
    receipts = rows(app.store, "investigation_creation_tool_receipts")
    assert len(receipts) == (0 if failure in {"schema", "identity"} else 1)


@pytest.mark.parametrize("operation", ["get", "update"])
def test_cross_session_fails_closed(app, content, operation):
    first = app.create_ruleset_proposal(content, session_id="session-a")
    arguments = {"proposal_id": first.proposal_id}
    if operation == "update":
        arguments.update(expected_version=1, content=content)
    before = rows(app.store)
    result = call(app, f"{operation}_ruleset_proposal", arguments, session="session-b")
    assert result["error"]["code"] == "RULESET_PROPOSAL_NOT_FOUND"
    assert result["error"]["details"] == {}
    assert rows(app.store) == before


def test_ownership_and_version_checked_before_application_compile(app, content):
    first = app.create_ruleset_proposal(content, session_id="session-a")
    with patch("backend.investigation_creation.service.compile_ruleset_content") as compile_call:
        with pytest.raises(ProposalNotFoundError):
            app.update_ruleset_proposal(first.proposal_id, session_id="other", expected_version=1, content={})
        with pytest.raises(ProposalVersionConflictError):
            app.update_ruleset_proposal(first.proposal_id, session_id="session-a", expected_version=2, content={})
        compile_call.assert_not_called()


def test_mutation_replay_conflict_and_read_without_receipt(app, content):
    arguments = {"content": content}
    first = call(app, "create_ruleset_proposal", arguments)
    assert first["status"] == "ok"
    assert call(app, "create_ruleset_proposal", arguments) == first
    content = deepcopy(content)
    content["name"] += " changed"
    conflict = call(app, "create_ruleset_proposal", {"content": content})
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    update = {"proposal_id": first["data"]["proposal_id"], "expected_version": 1, "content": content}
    second = call(app, "update_ruleset_proposal", update, turn="turn-2")
    assert second["data"]["version"] == 2
    assert call(app, "update_ruleset_proposal", update, turn="turn-2") == second
    assert call(app, "create_ruleset_proposal", arguments) == first
    assert len(rows(app.store)) == 1
    before = rows(app.store, "investigation_creation_tool_receipts")
    read = call(app, "get_ruleset_proposal", {"proposal_id": update["proposal_id"]}, turn="read")
    assert read == second
    assert rows(app.store, "investigation_creation_tool_receipts") == before


def test_concurrent_updates_have_one_winner(app, content):
    first = app.create_ruleset_proposal(content, session_id="session-a")
    barrier = Barrier(2)

    def synchronized_compile(value):
        result = compile_ruleset_content(value)
        barrier.wait(timeout=10)
        return result

    def update(index):
        changed = {**content, "name": f"Candidate {index}"}
        return call(app, "update_ruleset_proposal", {
            "proposal_id": first.proposal_id, "expected_version": 1, "content": changed,
        }, turn=f"concurrent-{index}")

    with patch("backend.investigation_creation.service.compile_ruleset_content", synchronized_compile):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, (1, 2)))
    assert sorted(result["status"] for result in results) == ["error", "ok"]
    loser = next(result for result in results if result["status"] == "error")
    assert loser["error"]["code"] == "RULESET_PROPOSAL_STALE"
    assert app.get_ruleset_proposal(first.proposal_id, session_id="session-a").version == 2


def test_tool_schemas_are_canonical_and_minimal():
    schemas = {item["name"]: item["parameters"] for item in HERMES_M3_TOOL_SCHEMAS}
    canonical = RuleSetContent.model_json_schema()
    canonical.pop("$defs")
    for name, keys in {
        "create_ruleset_proposal": {"content"},
        "update_ruleset_proposal": {"proposal_id", "expected_version", "content"},
        "get_ruleset_proposal": {"proposal_id"},
    }.items():
        schema = schemas[name]
        assert set(schema["properties"]) == set(schema["required"]) == keys
        assert schema["additionalProperties"] is False
        if "content" in keys:
            assert schema["properties"]["content"]["$ref"] == "#/$defs/RuleSetContent"
            assert schema["$defs"]["RuleSetContent"] == canonical


@pytest.mark.parametrize("field", ["session_id", "proposal_id", "version", "content_hash", "created_at", "updated_at", "status", "ruleset_content"])
def test_create_rejects_authoritative_fields_and_aliases(app, content, field):
    result = call(app, "create_ruleset_proposal", {"content": content, field: "model-supplied"})
    assert result["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert rows(app.store) == rows(app.store, "investigation_creation_tool_receipts") == []


@pytest.mark.parametrize("invalid", [{"expected_content_hash": "old"}, {"expected_version": True}, {"expected_version": "1"}])
def test_update_rejects_hash_parameter_and_noninteger_version(app, content, invalid):
    result = call(app, "update_ruleset_proposal", {
        "proposal_id": "P1", "expected_version": 1, "content": content, **invalid,
    })
    assert result["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert rows(app.store) == rows(app.store, "investigation_creation_tool_receipts") == []


def test_dispatch_session_identity_for_get_and_missing_mutation_identity(creation_stack, content):
    conversation = creation_stack["conversation"]
    a = conversation.create_session(principal=Principal("principal-a"), workspace_key="a")
    b = conversation.create_session(principal=Principal("principal-a"), workspace_key="b")
    configure_hermes_investigation_creation_tools(
        creation_stack["tool_service"], principal_provider=conversation.principal_for_session,
    )
    missing = json.loads(dispatch_hermes_investigation_creation_tool(
        "create_ruleset_proposal", {"content": content}, session_id=a.id,
    ))
    assert missing["error"]["code"] == "TOOL_EXECUTION_IDENTITY_REQUIRED"
    first = json.loads(dispatch_hermes_investigation_creation_tool(
        "create_ruleset_proposal", {"content": content}, session_id=a.id, turn_id="t1", tool_call_id="c1",
    ))
    assert first["status"] == "ok"
    for session, status in ((a, "ok"), (b, "error")):
        result = json.loads(dispatch_hermes_investigation_creation_tool(
            "get_ruleset_proposal", {"proposal_id": first["data"]["proposal_id"]}, session_id=session.id,
        ))
        assert result["status"] == status


def test_multiturn_creation_edit_regeneration_with_no_formal_or_draft_effects(creation_stack, content):
    app = creation_stack["app_service"]
    with sqlite3.connect(creation_stack["resource_db"]) as connection:
        before = list(connection.iterdump())
    options = creation_stack["tool_service"].execute("query_investigation_options", {}, principal=Principal("principal-a"))
    first = _run_scripted_creation_turn(
        creation_stack, content="Generate recruitment fraud rules if none fit.",
        actions=[("create_ruleset_proposal", {"content": content})], client_message_id="p1",
    )
    p1 = first["agent"].results[0]
    content["categories"][0]["rules"][0]["hit_condition"] = "Explicitly request advance payment from the applicant."
    second = _run_scripted_creation_turn(
        creation_stack, content="Tighten the advance fee rule.", session_id=first["session_id"],
        actions=[("get_ruleset_proposal", {"proposal_id": p1["proposal_id"]}),
                 ("update_ruleset_proposal", {"proposal_id": p1["proposal_id"], "expected_version": 1, "content": content})],
        client_message_id="edit-p1",
    )
    third = _run_scripted_creation_turn(
        creation_stack, content="Generate another candidate for comparison.", session_id=first["session_id"],
        actions=[("create_ruleset_proposal", {"content": content})], client_message_id="p2",
    )
    assert second["agent"].results[-1]["version"] == 2
    assert third["agent"].results[-1]["version"] == 1
    assert third["agent"].results[-1]["proposal_id"] != p1["proposal_id"]
    assert app.get_ruleset_proposal(p1["proposal_id"], session_id=first["session_id"]).version == 2
    assert all(turn["turn"].status == "completed" and turn["turn"].public_artifact["proposal_presentations"] for turn in (first, second, third))
    assert rows(app.store, "investigation_drafts") == rows(app.store, "investigation_runs") == []
    assert creation_stack["tool_service"].execute("query_investigation_options", {}, principal=Principal("principal-a")) == options
    with sqlite3.connect(creation_stack["resource_db"]) as connection:
        assert list(connection.iterdump()) == before


def test_proposal_tools_do_not_modify_existing_draft_judgement(creation_stack, content):
    app = creation_stack["app_service"]
    arguments = _t1_temporary_arguments(creation_stack)
    assert call(app, "create_investigation_draft", arguments)["status"] == "ok"
    before = rows(app.store, "investigation_drafts")
    first = call(app, "create_ruleset_proposal", {"content": content}, tool_call="proposal-create")["data"]
    content["name"] += " revised"
    updated = call(app, "update_ruleset_proposal", {
        "proposal_id": first["proposal_id"], "expected_version": 1, "content": content,
    }, tool_call="proposal-update")
    assert updated["status"] == "ok"
    assert call(app, "get_ruleset_proposal", {"proposal_id": first["proposal_id"]}) == updated
    assert rows(app.store, "investigation_drafts") == before


def test_same_content_still_passes_compile_validation(app, content):
    first = app.create_ruleset_proposal(content, session_id="session-a")
    with patch("backend.investigation_creation.service.compile_ruleset_content", side_effect=RuntimeError("compiler failure")):
        result = call(app, "update_ruleset_proposal", {
            "proposal_id": first.proposal_id, "expected_version": 1, "content": content,
        })
    assert result["status"] == "error"
    assert app.get_ruleset_proposal(first.proposal_id, session_id="session-a") == first


@pytest.mark.parametrize("proposal_first", [True, False])
def test_same_turn_terms_and_proposal_in_either_order(creation_stack, content, proposal_first):
    arguments = _t1_temporary_arguments(creation_stack)
    actions = [("create_ruleset_proposal", {"content": content}), ("create_investigation_draft", arguments)]
    if not proposal_first:
        actions.reverse()
    result = _run_scripted_creation_turn(
        creation_stack, content="Generate missing terms and a comparison Proposal.",
        actions=actions, client_message_id=f"dual-{proposal_first}",
    )
    assert result["turn"].status == "completed"
    draft = result["turn"].public_artifact["draft"]
    assert draft["configuration"]["judgement"] == arguments["configuration"]["judgement"]
    assert draft["configuration"]["investigation"]["recall_plan"]["strategy"] == "temporary_terms"
    assert len(rows(creation_stack["creation_store"])) == 1
    assert len(rows(creation_stack["creation_store"], "investigation_creation_tool_receipts")) == 2
    assert rows(creation_stack["creation_store"], "investigation_runs") == []
    without_judgement = deepcopy(arguments["configuration"])
    without_judgement.pop("judgement")
    with pytest.raises(ValidationError):
        InvestigationDraftConfiguration.model_validate(without_judgement)
