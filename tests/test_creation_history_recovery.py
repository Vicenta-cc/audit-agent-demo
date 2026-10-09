"""Recovery continuation with real Hermes normalization and isolated SQLite."""
from copy import deepcopy
import hashlib
from unittest.mock import patch

import pytest

from backend.hermes_runtime.adapter import prepare_creation_history
from backend.investigation.errors import ToolProtocolError
from backend.investigation.store import InvestigationStore
from backend.investigation_creation.conversation import InvestigationCreationConversationService
from backend.investigation_creation.principal import Principal
from test_investigation_creation_conversation import (
    creation_stack, ScriptedCreationHermesAgent, _explicit_create_actions,
    _run_scripted_creation_turn, _draft_count, _run_count,
)


@pytest.fixture
def hermes_repair():
    return pytest.importorskip("agent.agent_runtime_helpers").repair_message_sequence


def test_legacy_projection_uses_hermes_without_touching_source_or_tool_pairs(hermes_repair):
    call = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read", "arguments": {}}}]}
    result = {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "{}"}
    history = [{"role": "user", "content": "创建"}, call, result,
               {"role": "assistant", "content": "恢复说明"},
               {"role": "assistant", "content": "草案已保存"}]
    before = deepcopy(history)
    expected = deepcopy(history)
    assert hermes_repair(None, expected) == 1
    projected = prepare_creation_history(history)
    assert projected == expected
    assert history == before
    assert projected[:3] == before[:3]
    assert hermes_repair(None, projected) == 0
    projected[1]["tool_calls"][0]["function"]["name"] = "mutated"
    assert history == before


def test_projection_never_repairs_orphan_tools():
    with pytest.raises(ToolProtocolError):
        prepare_creation_history([{"role": "tool", "tool_call_id": "orphan", "content": "{}"}])


def test_projection_preserves_user_boundaries_and_assistant_metadata():
    history = [{"role": "user", "content": "first"}, {"role": "user", "content": "second"},
               {"role": "assistant", "content": "candidate", "finish_reason": "verification_required"},
               {"role": "assistant", "content": "final"}]
    assert prepare_creation_history(history) == history


@pytest.mark.parametrize("changed_role", ["user", "tool"])
def test_runtime_mutation_cannot_change_validation_reference(creation_stack, changed_role):
    initial = _run_scripted_creation_turn(
        creation_stack, content="创建调查草案", actions=_explicit_create_actions(), client_message_id="create")
    store = creation_stack["conversation"].store
    before = store.latest_completed_hermes_transcript(initial["session_id"])
    original_run = ScriptedCreationHermesAgent.run_conversation
    def corrupt_run(agent, message, **kwargs):
        target = next(m for m in kwargs["conversation_history"] if m["role"] == changed_role)
        target["content"] = "changed by runtime"
        return original_run(agent, message, **kwargs)
    with patch.object(ScriptedCreationHermesAgent, "run_conversation", corrupt_run):
        with pytest.raises(RuntimeError, match="unknown outcome") as failure:
            _run_scripted_creation_turn(
                creation_stack, session_id=initial["session_id"], content="追问", actions=[],
                client_message_id="followup")
    assert "preserve its input history" in str(failure.value.__cause__)
    assert store.latest_completed_hermes_transcript(initial["session_id"]) == before
    assert _draft_count(creation_stack["creation_store"]) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_checkpoint_followup_after_reopening_database(creation_stack, hermes_repair, legacy):
    stack = creation_stack
    initial = _run_scripted_creation_turn(
        stack, content="创建调查草案，先不启动。", actions=_explicit_create_actions(),
        completed=False, failed=True, final_response="provider failure",
        client_message_id="create")
    assert initial["turn"].stop_reason == "recovered_successful_application_checkpoint"
    session = initial["session_id"]
    store = stack["conversation"].store
    history = store.latest_completed_hermes_transcript(session)
    assert hermes_repair(None, deepcopy(history)) == 0
    assert history[-2]["role"] == "tool"
    if legacy:
        # Seed precisely the pre-fix durable shape into this disposable fixture.
        history.insert(-1, {"role": "assistant", "content": "系统已从持久化检查点恢复以上成功操作。"})
        serialized = store._hermes_transcript_json(history)
        with store._connect() as db:
            db.execute("UPDATE investigation_hermes_transcripts SET messages_json=?, messages_sha256=? WHERE turn_id=?",
                       (serialized, hashlib.sha256(serialized.encode()).hexdigest(), initial["turn"].id))
    with store._connect() as db:
        old_row = tuple(db.execute("SELECT * FROM investigation_hermes_transcripts WHERE turn_id=?",
                                   (initial["turn"].id,)).fetchone())
    with stack["creation_store"]._connect() as db:
        original_business = {table: [tuple(r) for r in db.execute("SELECT * FROM " + table)] for table in (
            "investigation_drafts", "investigation_draft_revisions", "investigation_creation_tool_receipts")}
    stack["conversation"] = InvestigationCreationConversationService(
        tool_service=stack["tool_service"], store=InvestigationStore(store.db_path),
        fake_runtime=True, hermes_state_dir=stack["conversation"].hermes_state_dir)
    original_run = ScriptedCreationHermesAgent.run_conversation
    def normalized_run(agent, message, **kwargs):
        # Real dependency behavior, not a fake array-merging implementation.
        assert hermes_repair(None, kwargs["conversation_history"]) == 0
        return original_run(agent, message, **kwargs)
    with patch.object(ScriptedCreationHermesAgent, "run_conversation", normalized_run):
        followup = _run_scripted_creation_turn(
            stack, session_id=session, content="刚才草案是否已经保存？", actions=[],
            client_message_id="followup", final_response="已保存，尚未启动。")
        replay, was_replayed = stack["conversation"].accept_message(
            session, client_message_id="followup", content="刚才草案是否已经保存？", principal=Principal("principal-a"))
        assert was_replayed and replay.id == followup["turn"].id
    assert followup["turn"].status == "completed"
    assert _draft_count(stack["creation_store"]) == 1
    assert _run_count(stack["creation_store"]) == 0
    with store._connect() as db:
        assert tuple(db.execute("SELECT * FROM investigation_hermes_transcripts WHERE turn_id=?",
                                (initial["turn"].id,)).fetchone()) == old_row
    with stack["creation_store"]._connect() as db:
        for table, before in original_business.items():
            assert [tuple(r) for r in db.execute("SELECT * FROM " + table)] == before
    trace = store.turn_result(followup["turn"].id)
    assert trace.answer == "已保存，尚未启动。"
    assert not trace.tool_calls  # Old recovered calls must not become this turn's calls.
    transcript = store.latest_completed_hermes_transcript(session)
    assert [m["content"] for m in transcript if m["role"] == "user"] == [
        "创建调查草案，先不启动。", "刚才草案是否已经保存？"]
    assert transcript[-1]["content"] == followup["result"].answer
