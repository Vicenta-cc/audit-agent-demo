from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from backend.investigation_creation.principal import Principal
from backend.investigation_creation.presentation import message_presentations, render_proposal
from backend.rulesets.contracts import RuleSetContent
from backend.investigation_creation.tools import HermesToolExecutionIdentity
from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn
from test_ruleset_proposals import rows


@pytest.fixture
def content():
    return json.loads((Path(__file__).parent / "fixtures/recruitment_fraud_ruleset.json").read_text())


def run(stack, content, **kwargs):
    return _run_scripted_creation_turn(
        stack, content="生成招聘诈骗规则给我看看", actions=[("create_ruleset_proposal", {"content": content})],
        **kwargs,
    )


def evidence(result):
    return result["turn"].public_artifact["proposal_presentations"][0]


def test_create_exact_snapshot_in_real_message_and_public_api(creation_stack, content):
    stack = creation_stack
    # Values that the previous UI sanitizer or Markdown renderer would hide.
    content["categories"][0]["rules"][0]["adjudication_notes"] = "保留 <tag>、https://example.test、```、draft_id 字样"
    result = run(stack, content, final_response="只有一条规则，全部直接判高危。")
    record = evidence(result)
    store = stack["conversation"].store
    proposal = stack["app_service"].get_ruleset_proposal(record["proposal_id"], session_id=result["session_id"])
    assert record["snapshot"] == proposal.model_dump(mode="json")
    assert record["content_hash"] == proposal.content_hash
    assert record["proposal_version"] == proposal.version == 1
    assert record["session_id"] == result["session_id"]
    assert record["source_user_turn_id"] == result["turn"].id
    assert record["source_user_message_id"] == store.get_user_message_for_turn(result["turn"].id).id
    assert record["assistant_message_id"] == result["result"].message_id
    message = store.get_message(record["assistant_message_id"])
    assert record["presented_at"] == message.created_at
    assert record["text"] == render_proposal(proposal)
    assert result["result"].answer == message.content
    assert message.content.endswith(record["text"])
    assert "只有一条规则" not in record["text"]
    for category in content["categories"]:
        for rule in category["rules"]:
            for field in ("name", "hit_condition", "adjudication_notes"):
                assert rule[field] in record["text"]
    public = stack["client"].get(f"/api/investigation-workspaces/{result['session_id']}/state")
    assert public.status_code == 200, public.text
    shown = next(m for m in public.json()["messages"] if m["message_id"] == message.id)
    assert shown["content"] == message.content
    assert shown["artifact"]["proposal_presentations"][0] == record
    assert store.turn_result(result["turn"].id).answer == message.content
    terminal = stack["client"].get(f"/api/investigation-workspace-turns/{result['turn'].id}")
    assert terminal.status_code == 200
    assert terminal.json()["answer"] == message.content
    assert terminal.json()["artifact"]["proposal_presentations"][0] == record
    stack["principals"].current = Principal("principal-b")
    assert stack["client"].get(f"/api/investigation-workspaces/{result['session_id']}/state").status_code == 404


def test_update_preserves_history_and_displays_new_version(creation_stack, content):
    first = run(creation_stack, content)
    previous = deepcopy(evidence(first))
    content["categories"][0]["rules"][1]["hit_condition"] += " 必须有明确证据。"
    second = _run_scripted_creation_turn(
        creation_stack, content="第二条严格一点", session_id=first["session_id"], client_message_id="edit",
        actions=[("update_ruleset_proposal", {"proposal_id": previous["proposal_id"],
                  "expected_version": 1, "content": content})],
    )
    current = evidence(second)
    assert current["proposal_version"] == 2
    assert current["content_hash"] != previous["content_hash"]
    assert current["assistant_message_id"] != previous["assistant_message_id"]
    assert current["snapshot"]["content"] == RuleSetContent.model_validate(content).model_dump(mode="json")
    assert creation_stack["conversation"].store.get_turn(first["turn"].id).public_artifact["proposal_presentations"] == [previous]


def test_tool_success_without_public_message_has_no_evidence(creation_stack, content):
    stack = creation_stack
    session = stack["conversation"].create_session(principal=Principal("principal-a"), workspace_key="unshown")
    proposal = stack["app_service"].create_ruleset_proposal(content, session_id=session.id)
    assert proposal.version == 1
    assert stack["conversation"].store.list_messages(session.id) == ()
    assert stack["creation_store"].proposal_presentation_snapshots(session_id=session.id, turn_id="absent") == []


def test_failed_message_transaction_rolls_back_presentation(creation_stack, content):
    store = creation_stack["conversation"].store
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("""CREATE TRIGGER reject_completion BEFORE UPDATE ON investigation_turns
            WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'simulated persistence failure'); END""")
    with pytest.raises(RuntimeError):
        run(creation_stack, content)
    assert len(rows(creation_stack["creation_store"])) == 1
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM investigation_messages WHERE role='assistant'").fetchone()[0] == 0
        assert all(json.loads(row[0]) == {} for row in connection.execute("SELECT public_artifact_json FROM investigation_turns"))


def test_prose_claim_without_application_mutation_cannot_present(creation_stack):
    result = _run_scripted_creation_turn(
        creation_stack, content="看看规则", actions=[],
        final_response="已展示 proposal_id=P1 version=9 content_hash=abc，用户已看过。",
    )
    assert result["turn"].public_artifact == {}


def test_cross_session_snapshot_rejected_and_receipts_isolated(creation_stack, content):
    result = run(creation_stack, content)
    record = evidence(result)
    assert creation_stack["creation_store"].proposal_presentation_snapshots(session_id="other", turn_id=result["turn"].id) == []
    with pytest.raises(ValueError, match="identity mismatch"):
        message_presentations([record["snapshot"]], session_id="other", turn_id="t", user_message_id="u",
                              assistant_message_id="a", presented_at="now")
    corrupted = deepcopy(record["snapshot"])
    corrupted["content"]["name"] += " changed"
    with pytest.raises(ValueError, match="identity mismatch"):
        message_presentations([corrupted], session_id=result["session_id"], turn_id="t", user_message_id="u",
                              assistant_message_id="a", presented_at="now")


def test_concurrent_change_does_not_substitute_latest(creation_stack, content):
    store = creation_stack["creation_store"]
    original = store.proposal_presentation_snapshots

    def changed(**kwargs):
        selected = original(**kwargs)[0]
        new = deepcopy(content)
        new["name"] += " unseen"
        creation_stack["app_service"].update_ruleset_proposal(
            selected["proposal_id"], session_id=kwargs["session_id"], expected_version=1, content=new,
        )
        return original(**kwargs)

    with patch.object(store, "proposal_presentation_snapshots", changed), pytest.raises(RuntimeError):
        run(creation_stack, content)
    with sqlite3.connect(creation_stack["conversation"].store.db_path) as connection:
        assert all(json.loads(row[0]) == {} for row in connection.execute("SELECT public_artifact_json FROM investigation_turns"))


def test_no_draft_execution_or_formal_side_effects(creation_stack, content):
    stack = creation_stack
    with sqlite3.connect(stack["resource_db"]) as connection:
        before = list(connection.iterdump())
    with patch.object(stack["app_service"], "confirm_and_queue", side_effect=AssertionError("execution forbidden")):
        run(stack, content)
    assert rows(stack["creation_store"], "investigation_drafts") == []
    assert rows(stack["creation_store"], "investigation_runs") == []
    with sqlite3.connect(stack["resource_db"]) as connection:
        assert list(connection.iterdump()) == before


def test_runtime_identity_is_explicitly_bound_not_parsed(creation_stack, content):
    service = creation_stack["tool_service"]
    original = service.execute_with_identity

    def runtime_call(name, arguments, **kwargs):
        original_identity = kwargs["identity"]
        kwargs["identity"] = HermesToolExecutionIdentity(
            session_id=original_identity.session_id, turn_id="opaque-runtime-turn-with-no-prefix",
            tool_call_id=original_identity.tool_call_id,
        )
        return original(name, arguments, **kwargs)

    with patch.object(service, "execute_with_identity", runtime_call):
        result = run(creation_stack, content)
    assert evidence(result)["source_user_turn_id"] == result["turn"].id
    with sqlite3.connect(creation_stack["creation_store"].db_path) as connection:
        row = connection.execute("""SELECT r.turn_id, b.application_turn_id
            FROM investigation_creation_tool_receipts r
            JOIN ruleset_proposal_conversation_bindings b USING (receipt_id)""").fetchone()
    assert row == ("opaque-runtime-turn-with-no-prefix", result["turn"].id)


def test_terminal_replay_does_not_duplicate_or_change_presentation(creation_stack, content):
    result = run(creation_stack, content)
    before = deepcopy(result["turn"].public_artifact)
    replay = creation_stack["conversation"].execute_turn(result["turn"].id)
    assert replay.message_id == result["result"].message_id
    assert replay.answer == result["result"].answer
    assert creation_stack["conversation"].store.get_turn(result["turn"].id).public_artifact == before
    assert len(rows(creation_stack["creation_store"])) == 1


def test_same_turn_create_then_update_presents_only_final_snapshot(creation_stack, content):
    updated = deepcopy(content)
    updated["name"] += " refined"
    result = _run_scripted_creation_turn(
        creation_stack, content="生成并完善规则", actions=[
            ("create_ruleset_proposal", {"content": content}),
            ("update_ruleset_proposal", lambda results: {
                "proposal_id": results[0]["proposal_id"], "expected_version": 1, "content": updated,
            }),
        ],
    )
    assert len(result["turn"].public_artifact["proposal_presentations"]) == 1
    assert evidence(result)["proposal_version"] == 2
