from copy import deepcopy
import json
import sqlite3
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from pydantic import ValidationError

from backend.investigation_creation.approval import UseRuleSetProposalInput
from backend.investigation_creation.contracts import (
    CreateDraftCommand, UpdateDraftCommand, ConfirmAndQueueCommand, TemporaryRuleSetJudgement,
)
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import HermesToolExecutionIdentity
from test_investigation_creation_conversation import (
    creation_stack, _run_scripted_creation_turn, _t1_temporary_arguments,
)
from test_ruleset_proposal_presentation import content, run, evidence
from test_ruleset_proposals import rows


PRINCIPAL = Principal("principal-a")


def creation():
    return {"title": "招聘诈骗调查", "objective": "调查招聘收费风险", "configuration": {
        "platform": "xhs", "investigation": {"mode": "search", "recall_plan": {
            "strategy": "temporary_terms", "terms": ["招聘收费"], "source_lexicon_ids": [],
        }},
    }}


def approve(stack, first, *, message="可以，就按现在这套规则来。", arguments=None, principal=PRINCIPAL):
    conversation = stack["conversation"]
    turn, _ = conversation.accept_message(first["session_id"], client_message_id=f"approve-{len(conversation.store.list_messages(first['session_id']))}",
                                          content=message, principal=PRINCIPAL)
    identity = HermesToolExecutionIdentity(first["session_id"], turn.id, "use-1")
    arguments = arguments if arguments is not None else {"create_draft": creation()}
    arguments.setdefault("presentation_id", first["turn"].public_artifact.get("proposal_presentations", [{}])[0].get("presentation_id", "missing"))
    service = stack["tool_service"]
    service.begin_conversation_turn(first["session_id"], turn.id)
    try:
        result = service.execute_with_identity("use_ruleset_proposal", arguments, principal=principal, identity=identity)
    finally:
        service.end_conversation_turn(first["session_id"])
    return result, identity, turn


def test_binding_snapshot_preview_confirm_audit_and_replay(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    assert not rows(stack["creation_store"], "investigation_drafts")
    result, identity, turn = approve(stack, first)
    assert result["status"] == "ok", result
    data = result["data"]
    draft = data["draft"]
    judgement = draft["configuration"]["judgement"]
    assert judgement["strategy"] == "temporary_ruleset"
    assert judgement["content"] == evidence(first)["snapshot"]["content"]
    assert judgement["content_hash"] == evidence(first)["content_hash"]
    assert data["confirmation_preview"]["can_confirm"]
    assert data["confirmation_preview"]["temporary_ruleset"] == judgement
    app = stack["app_service"]
    # Adoption and readiness do not themselves confirm or create a Run (T5).
    assert not rows(stack["creation_store"], "investigation_runs")
    with sqlite3.connect(stack["resource_db"]) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM report_versions").fetchone()[0] == 0
    with sqlite3.connect(stack["creation_store"].db_path) as db:
        db.row_factory = sqlite3.Row
        audit = dict(db.execute("SELECT * FROM ruleset_proposal_approvals").fetchone())
    assert audit["session_id"] == first["session_id"]
    assert audit["approving_user_turn_id"] == turn.id
    assert audit["approving_user_message_id"] == turn.user_message_id
    assert audit["presentation_assistant_message_id"] == evidence(first)["assistant_message_id"]
    assert audit["presentation_id"] == evidence(first)["presentation_id"]
    assert audit["tool_call_id"] == identity.tool_call_id
    assert audit["runtime_turn_id"] == identity.turn_id
    assert audit["proposal_version"] == 1 and audit["draft_revision"] == 1
    assert audit["draft_id"] == draft["id"] and audit["approved_at"]
    replay = stack["tool_service"].execute_with_identity("use_ruleset_proposal", {"presentation_id": evidence(first)["presentation_id"], "create_draft": creation()}, principal=PRINCIPAL, identity=identity)
    assert replay == result
    conflict = stack["tool_service"].execute_with_identity("use_ruleset_proposal", {"presentation_id": evidence(first)["presentation_id"], "create_draft": {**creation(), "title": "other"}}, principal=PRINCIPAL, identity=identity)
    assert conflict["status"] == "error"
    content["name"] += " changed"
    app.update_ruleset_proposal(judgement["proposal_id"], session_id=first["session_id"], expected_version=1, content=content)
    assert app.get_draft(draft["id"], principal=PRINCIPAL).configuration.judgement.model_dump(mode="json") == judgement
    assert len(rows(stack["creation_store"], "investigation_draft_revisions")) == 1


def test_proposal_created_draft_preserves_explicit_task_parameters(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    create_draft = creation()
    create_draft["configuration"]["task_parameters"] = {
        "max_notes": 3,
        "max_comments": 10,
        "collect_media": False,
    }

    result, _, _ = approve(
        stack,
        first,
        arguments={"create_draft": create_draft},
    )

    assert result["status"] == "ok", result
    parameters = result["data"]["draft"]["configuration"]["task_parameters"]
    assert parameters["max_notes"] == 3
    assert parameters["max_comments"] == 10
    assert parameters["collect_media"] is False


def test_temporary_lexicon_edit_reference_can_be_corrected_before_receipt(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    valid = {"presentation_id": evidence(first)["presentation_id"], "create_draft": creation()}
    invalid = deepcopy(valid)
    invalid["create_draft"]["configuration"]["investigation"]["recall_plan"]["source_lexicon_ids"] = ["lexicon-edit:temporary"]
    failure, identity, turn = approve(stack, first, arguments=invalid)
    assert failure["status"] == "error"
    assert failure["error"]["details"]["receipt_created"] is False
    assert not rows(stack["creation_store"], "investigation_drafts")
    service = stack["tool_service"]
    service.begin_conversation_turn(first["session_id"], turn.id)
    try:
        result = service.execute_with_identity("use_ruleset_proposal", valid, principal=PRINCIPAL, identity=identity)
        assert result["status"] == "ok", result
        assert service.execute_with_identity("use_ruleset_proposal", valid, principal=PRINCIPAL, identity=identity) == result
    finally:
        service.end_conversation_turn(first["session_id"])
    assert len(rows(stack["creation_store"], "investigation_drafts")) == 1
    assert not rows(stack["creation_store"], "investigation_runs")


@pytest.mark.parametrize("corruption", ["update", "hash", "presentation_session"])
def test_stale_or_invalid_evidence(creation_stack, content, corruption):
    stack = creation_stack
    first = run(stack, content)
    record = evidence(first)
    if corruption == "update":
        content["name"] += " unseen"
        stack["app_service"].update_ruleset_proposal(record["proposal_id"], session_id=first["session_id"], expected_version=1, content=content)
    elif corruption == "hash":
        with sqlite3.connect(stack["creation_store"].db_path) as db:
            db.execute("UPDATE ruleset_proposals SET content_hash=?", ("0" * 64,))
    else:
        artifact = deepcopy(first["turn"].public_artifact)
        artifact["proposal_presentations"][0]["session_id"] = "other"
        with sqlite3.connect(stack["conversation"].store.db_path) as db:
            db.execute("UPDATE investigation_turns SET public_artifact_json=? WHERE id=?", (json.dumps(artifact), first["turn"].id))
    result, _, _ = approve(stack, first)
    assert result["status"] == "error", result
    assert not rows(stack["creation_store"], "investigation_drafts")
    assert not rows(stack["creation_store"], "ruleset_proposal_approvals")


def test_before_presentation_and_same_turn(creation_stack, content):
    first = _run_scripted_creation_turn(creation_stack, content="生成就采用", actions=[])
    result, identity, turn = approve(creation_stack, first)
    assert result["error"]["code"] == "PROPOSAL_NOT_PRESENTED"
    app = creation_stack["app_service"]
    app.create_ruleset_proposal(content, session_id=first["session_id"])
    with pytest.raises(Exception):
        app.use_ruleset_proposal(UseRuleSetProposalInput(presentation_id="missing", create_draft=creation()), session_id=first["session_id"], turn_id=turn.id, principal=PRINCIPAL, tool_call_id="test-direct")
    assert not rows(creation_stack["creation_store"], "investigation_drafts")


@pytest.mark.parametrize("selected", [0, 1])
def test_explicit_presentation_selection(creation_stack, content, selected):
    first = run(creation_stack, content)
    content["name"] += " second"
    second = run(creation_stack, content, session_id=first["session_id"], client_message_id="second")
    records = [evidence(first), evidence(second)]
    result, _, _ = approve(creation_stack, first, arguments={"presentation_id": records[selected]["presentation_id"], "create_draft": creation()})
    assert result["status"] == "ok", result
    assert result["data"]["draft"]["configuration"]["judgement"]["proposal_id"] == records[selected]["proposal_id"]


@pytest.mark.parametrize("failure", [None, "principal", "revision", "session"])
def test_existing_draft_and_fences(creation_stack, content, failure):
    stack = creation_stack
    app = stack["app_service"]
    draft = app.create_draft(CreateDraftCommand.model_validate(_t1_temporary_arguments(stack)), principal=PRINCIPAL)
    first = run(stack, content)
    arguments = {"draft_id": draft.id, "expected_revision": 99 if failure == "revision" else 1}
    principal = Principal("other") if failure == "principal" else PRINCIPAL
    if failure == "session":
        with sqlite3.connect(stack["creation_store"].db_path) as db:
            db.execute("UPDATE ruleset_proposals SET session_id='other'")
    result, _, turn = approve(stack, first, arguments=arguments, principal=principal)
    if failure:
        assert result["status"] == "error"
        assert app.get_draft(draft.id, principal=PRINCIPAL) == draft
    else:
        assert result["status"] == "ok", result
        bound = app.get_draft(draft.id, principal=PRINCIPAL)
        assert bound.current_revision == 2
        repeated = app.use_ruleset_proposal(UseRuleSetProposalInput(presentation_id=evidence(first)["presentation_id"], draft_id=draft.id, expected_revision=2), session_id=first["session_id"], turn_id=turn.id, principal=PRINCIPAL, tool_call_id="test-direct")
        assert repeated == bound
        edited = app.update_draft(UpdateDraftCommand(draft_id=draft.id, expected_revision=2, title="edited"), principal=PRINCIPAL)
        assert edited.configuration == bound.configuration and edited.current_revision == 3
        changed = bound.configuration.model_dump(mode="json")
        changed["judgement"]["proposal_version"] = 2
        with pytest.raises(Exception):
            app.update_draft(UpdateDraftCommand(draft_id=draft.id, expected_revision=3, configuration=changed), principal=PRINCIPAL)
        with pytest.raises(Exception):
            app.create_draft(CreateDraftCommand(title="bad", objective="bad", configuration=bound.configuration), principal=PRINCIPAL)


@pytest.mark.parametrize("missing", ["platform", "investigation", "recall_plan", "terms"])
def test_incomplete_no_placeholder(creation_stack, content, missing):
    config = creation()
    if missing in {"platform", "investigation"}:
        config["configuration"].pop(missing)
    elif missing == "recall_plan":
        config["configuration"]["investigation"].pop(missing)
    else:
        config["configuration"]["investigation"]["recall_plan"]["terms"] = []
    result, _, _ = approve(creation_stack, run(creation_stack, content), arguments={"create_draft": config})
    assert result["status"] == "error"
    assert not rows(creation_stack["creation_store"], "investigation_drafts")


def test_strict_temporary_schema(creation_stack, content):
    first = run(creation_stack, content)
    record = evidence(first)
    valid = {"strategy": "temporary_ruleset", "proposal_id": record["proposal_id"], "proposal_version": 1,
             "content_hash": record["content_hash"], "content": record["snapshot"]["content"]}
    for field, value in [("proposal_version", True), ("proposal_version", "1"), ("content_hash", "0"*64), ("approved", True), ("content", {})]:
        with pytest.raises(ValidationError):
            TemporaryRuleSetJudgement.model_validate({**valid, field: value})


def test_conversation_artifact_recovery_and_http_protection(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    adopted = _run_scripted_creation_turn(stack, session_id=first["session_id"], client_message_id="adopt",
        content="就用这套", actions=[("use_ruleset_proposal", {"presentation_id": evidence(first)["presentation_id"], "create_draft": creation()})])
    artifact = adopted["turn"].public_artifact
    assert artifact["artifact_type"] == "investigation_draft"
    assert artifact["suggestion"]["temporary_ruleset"]["proposal_version"] == 1
    state = stack["client"].get(f"/api/investigation-workspaces/{first['session_id']}/state").json()
    assert state["draft_artifact"]["draft_id"] == artifact["draft_id"]
    config = artifact["draft"]["configuration"]
    client = stack["client"]
    assert client.post("/api/investigation-drafts", json={**creation(), "configuration": config}).status_code == 400
    ordinary = stack["app_service"].create_draft(CreateDraftCommand.model_validate(_t1_temporary_arguments(stack)), principal=PRINCIPAL)
    assert client.patch(f"/api/investigation-drafts/{ordinary.id}", json={"expected_revision": 1, "configuration": config}).status_code == 400
    modified = deepcopy(config)
    modified["judgement"]["proposal_id"] = "fake"
    assert client.patch(f"/api/investigation-drafts/{artifact['draft_id']}", json={"expected_revision": 1, "configuration": modified}).status_code == 400
    config["platform"] = "dy"
    edited = client.patch(f"/api/investigation-drafts/{artifact['draft_id']}", json={"expected_revision": 1, "configuration": config})
    assert edited.status_code == 200, edited.text
    assert edited.json()["configuration"]["judgement"] == artifact["draft"]["configuration"]["judgement"]


def test_atomic_binding_precedes_concurrent_proposal_change(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    app = stack["app_service"]
    started = Event()
    original = app.resource_service.resolve_authoritative_draft
    changed = deepcopy(content)
    changed["name"] += " concurrent"
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = []
        def normalize(*args, **kwargs):
            if not futures:
                def update():
                    started.set()
                    return app.update_ruleset_proposal(evidence(first)["proposal_id"], session_id=first["session_id"], expected_version=1, content=changed)
                futures.append(pool.submit(update))
                assert started.wait(5)
            return original(*args, **kwargs)
        with patch.object(app.resource_service, "resolve_authoritative_draft", normalize):
            result, _, _ = approve(stack, first)
        assert result["status"] == "ok", result
        assert futures[0].result(timeout=10).version == 2
    assert result["data"]["draft"]["configuration"]["judgement"]["proposal_version"] == 1
    assert result["data"]["draft"]["configuration"]["judgement"]["content"] == evidence(first)["snapshot"]["content"]


def test_audit_failure_rolls_back_draft(creation_stack, content):
    first = run(creation_stack, content)
    with sqlite3.connect(creation_stack["creation_store"].db_path) as db:
        db.execute("CREATE TRIGGER reject_approval BEFORE INSERT ON ruleset_proposal_approvals BEGIN SELECT RAISE(ABORT, 'audit failed'); END")
    result, _, _ = approve(creation_stack, first)
    assert result["status"] == "error"
    assert not rows(creation_stack["creation_store"], "investigation_drafts")
    assert not rows(creation_stack["creation_store"], "investigation_draft_revisions")


def test_existing_default_strategy_backwards_compatible(creation_stack):
    args = _t1_temporary_arguments(creation_stack)
    args["configuration"]["judgement"].pop("strategy")
    draft = creation_stack["app_service"].create_draft(CreateDraftCommand.model_validate(args), principal=PRINCIPAL)
    assert draft.configuration.judgement.strategy == "existing_ruleset"


def test_other_session_presentation_cannot_authorize(creation_stack, content):
    shown = run(creation_stack, content)
    other = _run_scripted_creation_turn(creation_stack, content="other", actions=[], client_message_id="other-session")
    result, _, _ = approve(creation_stack, other, arguments={"presentation_id": evidence(shown)["presentation_id"], "create_draft": creation()})
    assert result["error"]["code"] == "PROPOSAL_NOT_PRESENTED"
    assert not rows(creation_stack["creation_store"], "investigation_drafts")


def test_tool_claim_without_current_application_turn_rejected(creation_stack, content):
    first = run(creation_stack, content)
    result = creation_stack["tool_service"].execute_with_identity("use_ruleset_proposal", {"presentation_id": evidence(first)["presentation_id"], "create_draft": creation()},
        principal=PRINCIPAL, identity=HermesToolExecutionIdentity(first["session_id"], "model-claims-approved", "claim"))
    assert result["error"]["code"] == "PROPOSAL_APPROVAL_REQUIRED"


def test_same_turn_generation_with_supposed_approval_rejected(creation_stack, content):
    conversation = creation_stack["conversation"]
    session = conversation.create_session(principal=PRINCIPAL, workspace_key="same-turn")
    turn, _ = conversation.accept_message(session.id, client_message_id="same-turn", content="就用这套", principal=PRINCIPAL)
    tool = creation_stack["tool_service"]
    tool.begin_conversation_turn(session.id, turn.id)
    try:
        generated = tool.execute_with_identity("create_ruleset_proposal", {"content": content}, principal=PRINCIPAL,
            identity=HermesToolExecutionIdentity(session.id, turn.id, "generate"))
        assert generated["status"] == "ok"
        result = tool.execute_with_identity("use_ruleset_proposal", {"presentation_id": "not-yet-published", "create_draft": creation()},
            principal=PRINCIPAL, identity=HermesToolExecutionIdentity(session.id, turn.id, "use"))
        assert result["error"]["code"] == "PROPOSAL_NOT_PRESENTED"
    finally:
        tool.end_conversation_turn(session.id)
    assert not rows(creation_stack["creation_store"], "investigation_drafts")


def test_no_draft_args_cannot_duplicate_current_draft(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    adopted = _run_scripted_creation_turn(stack, session_id=first["session_id"], client_message_id="bind",
        content="就用这套", actions=[("use_ruleset_proposal", {"presentation_id": evidence(first)["presentation_id"], "create_draft": creation()})])
    result, _, _ = approve(stack, first)
    assert result["error"]["code"] == "DRAFT_TARGET_REQUIRED"
    assert result["error"]["details"]["draft_id"] == adopted["turn"].public_artifact["draft_id"]
    assert len(rows(stack["creation_store"], "investigation_drafts")) == 1


def test_stale_existing_draft_no_mutation_and_replay(creation_stack, content):
    stack = creation_stack
    draft = stack["app_service"].create_draft(CreateDraftCommand.model_validate(_t1_temporary_arguments(stack)), principal=PRINCIPAL)
    first = run(stack, content)
    content["name"] += " unseen"
    stack["app_service"].update_ruleset_proposal(evidence(first)["proposal_id"], session_id=first["session_id"], expected_version=1, content=content)
    args = {"draft_id": draft.id, "expected_revision": 1}
    result, identity, _ = approve(stack, first, arguments=args)
    assert result["error"]["code"] == "PROPOSAL_PRESENTATION_STALE"
    assert stack["app_service"].get_draft(draft.id, principal=PRINCIPAL) == draft
    assert stack["tool_service"].execute_with_identity("use_ruleset_proposal", args, principal=PRINCIPAL, identity=identity) == result


def test_draft_revision_reread_at_write_boundary(creation_stack, content):
    stack = creation_stack
    app = stack["app_service"]
    draft = app.create_draft(CreateDraftCommand.model_validate(_t1_temporary_arguments(stack)), principal=PRINCIPAL)
    first = run(stack, content)
    original = stack["creation_store"].use_ruleset_proposal
    def changed_before_write(*args, **kwargs):
        stack["creation_store"].update_draft(draft.id, principal=PRINCIPAL.id, expected_revision=1, title="concurrent edit")
        return original(*args, **kwargs)
    with patch.object(stack["creation_store"], "use_ruleset_proposal", changed_before_write):
        result, _, _ = approve(stack, first, arguments={"draft_id": draft.id, "expected_revision": 1})
    assert result["status"] == "error"
    current = app.get_draft(draft.id, principal=PRINCIPAL)
    assert current.current_revision == 2 and current.configuration.judgement.strategy == "existing_ruleset"
    assert not rows(stack["creation_store"], "ruleset_proposal_approvals")


def test_receipt_returns_bound_revision_despite_later_draft_edit(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    app = stack["app_service"]
    original = app.use_ruleset_proposal
    def edited_after_binding(*args, **kwargs):
        bound = original(*args, **kwargs)
        stack["creation_store"].update_draft(bound.id, principal=PRINCIPAL.id, expected_revision=1, title="later edit")
        return bound
    with patch.object(app, "use_ruleset_proposal", edited_after_binding):
        result, identity, _ = approve(stack, first)
    assert result["status"] == "ok"
    assert result["data"]["draft"]["current_revision"] == 1
    assert result["data"]["confirmation_preview"]["draft_revision"] == 1
    assert app.get_draft(result["data"]["draft"]["id"], principal=PRINCIPAL).current_revision == 2
    assert stack["tool_service"].execute_with_identity("use_ruleset_proposal", {"presentation_id": evidence(first)["presentation_id"], "create_draft": creation()}, principal=PRINCIPAL, identity=identity) == result


@pytest.mark.parametrize("invalid", ["missing", "unpublished", "legacy", "assistant_missing", "text_missing", "identity", "snapshot_content", "canonical_content"])
def test_presentation_authority_failures(creation_stack, content, invalid):
    stack = creation_stack
    first = run(stack, content)
    artifact = deepcopy(first["turn"].public_artifact)
    record = artifact["proposal_presentations"][0]
    requested = record["presentation_id"]
    with sqlite3.connect(stack["conversation"].store.db_path) as db:
        if invalid == "missing":
            requested = "invented-presentation"
        elif invalid == "unpublished":
            db.execute("UPDATE investigation_turns SET status='error' WHERE id=?", (first["turn"].id,))
        elif invalid == "assistant_missing":
            db.execute("UPDATE investigation_turns SET assistant_message_id='missing' WHERE id=?", (first["turn"].id,))
        elif invalid == "text_missing":
            db.execute("UPDATE investigation_messages SET content='only prose' WHERE id=?", (record["assistant_message_id"],))
        elif invalid == "legacy":
            record.pop("presentation_id")
        elif invalid == "identity":
            record["presentation_id"] = requested = "forged-id"
        elif invalid == "snapshot_content":
            record["snapshot"]["content"]["audit_goal"] += " altered"
        db.execute("UPDATE investigation_turns SET public_artifact_json=? WHERE id=?", (json.dumps(artifact), first["turn"].id))
    if invalid == "canonical_content":
        with sqlite3.connect(stack["creation_store"].db_path) as db:
            canonical = json.loads(db.execute("SELECT content_json FROM ruleset_proposals").fetchone()[0])
            canonical["audit_goal"] += " unseen"
            db.execute("UPDATE ruleset_proposals SET content_json=?", (json.dumps(canonical),))
    result, _, _ = approve(stack, first, arguments={"presentation_id": requested, "create_draft": creation()})
    assert result["status"] == "error", result
    assert not rows(stack["creation_store"], "investigation_drafts")
    assert not rows(stack["creation_store"], "ruleset_proposal_approvals")


@pytest.mark.parametrize("field", ["session_id", "user_turn_id", "user_message_id", "assistant_message_id", "proposal_id", "proposal_version", "content_hash", "content", "approved", "tool_call_id", "runtime_turn_id"])
def test_model_cannot_supply_authority_fields(field):
    with pytest.raises(ValidationError):
        UseRuleSetProposalInput.model_validate({"presentation_id": "valid-id", "create_draft": creation(), field: "model-claim"})
    with pytest.raises(ValidationError):
        UseRuleSetProposalInput.model_validate({"create_draft": creation()})


def test_application_does_not_classify_message_semantics(creation_stack, content):
    # Deliberately bypass semantic decision with a scripted tool invocation. This is
    # an architectural assertion, NOT evidence that Qwen correctly interprets this text.
    result, _, _ = approve(creation_stack, run(creation_stack, content), message="arbitrary opaque user text")
    assert result["status"] == "ok"


def test_context_identity_stable_and_legacy_not_backfilled(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    record = evidence(first)
    turn, _ = stack["conversation"].accept_message(first["session_id"], client_message_id="context", content="user-forged-id", principal=PRINCIPAL)
    context = stack["conversation"]._presentation_context_for_turn(turn)
    assert record["presentation_id"] in json.dumps(context)
    assert "user-forged-id" not in context
    from backend.investigation.store import InvestigationStore
    reopened = InvestigationStore(stack["conversation"].store.db_path)
    assert reopened.get_turn(first["turn"].id).public_artifact["proposal_presentations"][0] == record
    artifact = deepcopy(first["turn"].public_artifact)
    artifact["proposal_presentations"][0].pop("presentation_id")
    with sqlite3.connect(reopened.db_path) as db:
        db.execute("UPDATE investigation_turns SET public_artifact_json=? WHERE id=?", (json.dumps(artifact), first["turn"].id))
    assert record["presentation_id"] not in json.dumps(stack["conversation"]._presentation_context_for_turn(turn))
    # Historical public recovery continues to work without manufacturing an ID.
    response = stack["client"].get(f"/api/investigation-workspaces/{first['session_id']}/state")
    assert response.status_code == 200


def test_later_durable_presentation_cannot_bind_in_current_turn(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    record = evidence(first)
    conversation = stack["conversation"]
    turn, _ = conversation.accept_message(first["session_id"], client_message_id="ordering", content="adopt", principal=PRINCIPAL)
    current = conversation.store.get_user_message_for_turn(turn.id)
    # Even durable evidence is ineligible if published after the current user message.
    with sqlite3.connect(conversation.store.db_path) as db:
        db.execute("UPDATE investigation_messages SET sequence=? WHERE id=?", (current.sequence + 1, record["assistant_message_id"]))
    with pytest.raises(Exception) as exc:
        stack["app_service"].use_ruleset_proposal(UseRuleSetProposalInput(presentation_id=record["presentation_id"], create_draft=creation()),
            session_id=first["session_id"], turn_id=turn.id, tool_call_id="runtime-call", principal=PRINCIPAL)
    assert exc.value.code == "PROPOSAL_NOT_PRESENTED"
    assert not rows(stack["creation_store"], "investigation_drafts")
    assert not rows(stack["creation_store"], "ruleset_proposal_approvals")


def test_stale_recovery_requires_later_user_and_new_presentation(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    old = evidence(first)
    content["audit_goal"] += " updated"
    stack["app_service"].update_ruleset_proposal(old["proposal_id"], session_id=first["session_id"], expected_version=1, content=content)
    failed, identity, turn = approve(stack, first)
    assert failed["error"]["code"] == "PROPOSAL_PRESENTATION_STALE"
    # Prepare a valid-looking new record, but do not publish it. Rendering grants no authority.
    from backend.investigation_creation.presentation import message_presentations
    proposal = stack["app_service"].get_ruleset_proposal(old["proposal_id"], session_id=first["session_id"])
    prepared = message_presentations([proposal.model_dump(mode="json")], session_id=first["session_id"], turn_id=turn.id,
        user_message_id=turn.user_message_id, assistant_message_id="prepared-only", presented_at="now")[0]
    with pytest.raises(Exception) as exc:
        stack["app_service"].use_ruleset_proposal(UseRuleSetProposalInput(presentation_id=prepared["presentation_id"], create_draft=creation()),
            session_id=first["session_id"], turn_id=turn.id, tool_call_id="retry", principal=PRINCIPAL)
    assert exc.value.code == "PROPOSAL_NOT_PRESENTED"
    assert not rows(stack["creation_store"], "investigation_drafts")
    # Finish this turn by publishing v2; only the NEXT real user turn can bind v2.
    conversation = stack["conversation"]
    tool = stack["tool_service"]
    tool.begin_conversation_turn(first["session_id"], turn.id)
    try:
        updated = tool.execute_with_identity("update_ruleset_proposal", {"proposal_id": old["proposal_id"], "expected_version": 2, "content": content},
            principal=PRINCIPAL, identity=HermesToolExecutionIdentity(first["session_id"], turn.id, "represent"))
        assert updated["status"] == "ok"
    finally:
        tool.end_conversation_turn(first["session_id"])
    transcript = (conversation.store.latest_completed_hermes_transcript(turn.session_id) or []) + [
        {"role": "user", "content": conversation.store.get_user_message_for_turn(turn.id).content},
        {"role": "assistant", "content": ""}]
    result = conversation._persist_result(turn, {"final_response": "", "completed": True}, transcript, {})
    completed = conversation.store.get_turn(turn.id)
    new = completed.public_artifact["proposal_presentations"][0]
    assert new["proposal_version"] == 2 and new["presentation_id"] != old["presentation_id"]
    assert "本次规则采用操作未成功" in result.answer and "发生了变化" in result.answer
    adopted, _, _ = approve(stack, {"session_id": first["session_id"], "turn": completed})
    assert adopted["status"] == "ok", adopted
    assert adopted["data"]["draft"]["configuration"]["judgement"]["proposal_version"] == 2


def test_mutation_error_fallback_survives_empty_model_response(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    failed, _, turn = approve(stack, first, arguments={"presentation_id": "unknown", "create_draft": creation()})
    assert failed["error"]["details"]["recovery"]
    conversation = stack["conversation"]
    transcript = (conversation.store.latest_completed_hermes_transcript(turn.session_id) or []) + [
        {"role": "user", "content": conversation.store.get_user_message_for_turn(turn.id).content},
        {"role": "assistant", "content": ""}]
    result = conversation._persist_result(turn, {"final_response": ""}, transcript, {})
    assert "本次规则采用操作未成功" in result.answer
    assert "展示记录" in result.answer
    state = stack["client"].get(f"/api/investigation-workspaces/{first['session_id']}/state").json()
    assert any("本次规则采用操作未成功" in m["content"] for m in state["messages"])


def test_draft_owned_by_other_principal_cannot_be_bound(creation_stack, content):
    stack = creation_stack
    draft = stack["app_service"].create_draft(CreateDraftCommand.model_validate(_t1_temporary_arguments(stack)), principal=Principal("other"))
    first = run(stack, content)
    result, _, _ = approve(stack, first, arguments={"draft_id": draft.id, "expected_revision": 1})
    assert result["error"]["code"] == "DRAFT_NOT_AUTHORIZED"
    assert stack["app_service"].get_draft(draft.id, principal=Principal("other")) == draft
    assert not rows(stack["creation_store"], "ruleset_proposal_approvals")


def test_old_presentation_id_never_selects_later_presented_version(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    old = evidence(first)
    content["audit_goal"] += " v2"
    second = _run_scripted_creation_turn(stack, session_id=first["session_id"], client_message_id="v2", content="edit",
        actions=[("update_ruleset_proposal", {"proposal_id": old["proposal_id"], "expected_version": 1, "content": content})])
    assert evidence(second)["presentation_id"] != old["presentation_id"]
    result, _, _ = approve(stack, first)
    assert result["error"]["code"] == "PROPOSAL_PRESENTATION_STALE"
    assert not rows(stack["creation_store"], "investigation_drafts")
    assert not rows(stack["creation_store"], "ruleset_proposal_approvals")


def test_trusted_id_reaches_actual_agent_history_argument(creation_stack, content):
    from test_investigation_creation_conversation import ScriptedCreationHermesAgent
    stack = creation_stack
    first = run(stack, content)
    captured = []
    original = ScriptedCreationHermesAgent.run_conversation
    def observed(agent, *args, **kwargs):
        captured.append(deepcopy(kwargs.get("conversation_history")))
        return original(agent, *args, **kwargs)
    with patch.object(ScriptedCreationHermesAgent, "run_conversation", observed):
        _run_scripted_creation_turn(stack, session_id=first["session_id"], client_message_id="context-input",
            content="explain", actions=[])
    projected = [json.loads(m["content"]) for m in captured[0] if m["role"] == "tool"]
    metadata = [r for p in projected for r in p.get("completed_public_presentations", [])]
    assert [r["presentation_id"] for r in metadata] == [evidence(first)["presentation_id"]]
    assert all(evidence(first)["presentation_id"] not in m["content"] for m in captured[0] if m["role"] == "assistant")


def test_invalid_arguments_failure_feedback_without_mutation_receipt(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    conversation = stack["conversation"]
    turn, _ = conversation.accept_message(first["session_id"], client_message_id="invalid-model-call", content="adopt", principal=PRINCIPAL)
    identity = HermesToolExecutionIdentity(first["session_id"], turn.id, "invalid-args")
    failed = stack["tool_service"].execute_with_identity("use_ruleset_proposal", {}, principal=PRINCIPAL, identity=identity)
    assert failed["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    messages = (conversation.store.latest_completed_hermes_transcript(turn.session_id) or []) + [
        {"role": "user", "content": "adopt"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": identity.tool_call_id, "type": "function", "function": {"name": "use_ruleset_proposal", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": identity.tool_call_id, "name": "use_ruleset_proposal", "content": json.dumps(failed)},
        {"role": "assistant", "content": ""}]
    result = conversation._persist_result(turn, {"final_response": ""}, messages, {})
    assert "本次规则采用操作未成功" in result.answer and "配置或操作条件未满足" in result.answer
    assert not rows(stack["creation_store"], "investigation_drafts")


def test_invalid_context_produces_terminal_safe_failure(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    artifact = deepcopy(first["turn"].public_artifact)
    artifact["proposal_presentations"][0]["content_hash"] = "0" * 64
    with sqlite3.connect(stack["conversation"].store.db_path) as db:
        db.execute("UPDATE investigation_turns SET public_artifact_json=? WHERE id=?", (json.dumps(artifact), first["turn"].id))
    conversation = stack["conversation"]
    turn, _ = conversation.accept_message(first["session_id"], client_message_id="invalid-context", content="adopt", principal=PRINCIPAL)
    with patch.object(conversation, "_agent", side_effect=AssertionError("invalid context must not reach model")):
        result = conversation.execute_turn(turn.id)
    assert conversation.store.get_turn(turn.id).status == "error"
    assert "完整性校验" in result.answer
    assert not rows(stack["creation_store"], "investigation_drafts")
