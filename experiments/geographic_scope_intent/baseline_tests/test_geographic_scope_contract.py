"""Offline candidate-contract checks; none of these measure model semantics."""
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from experiments.geographic_scope_intent.contract import (
    CandidateConfigureInput, OriginalConfigureInput, configure_schema,
    normalize_target, source_metadata, validate_source,
)


def update(**changes):
    return {"operation": "update", "draft_id": "draft-1", "expected_revision": 2, **changes}


def set_change(**overrides):
    return {"action": "set", "target_text": "克拉玛依", "source_quote": "调查克拉玛依",
            "selection_policy": "local_first_with_supplement", **overrides}


def message(message_id="user-now", content="调查克拉玛依", **overrides):
    return {"message_id": message_id, "role": "user", "content": content,
            "principal_id": "principal-1", "conversation_id": "conversation-1",
            "task_round": "round-2", "application_turn": 3, "sequence": 8, **overrides}


def case(history=None, current=None):
    return {"principal_id": "principal-1", "conversation_id": "conversation-1",
            "task_round": "round-2", "application_turn": 3,
            "current_user": current or message(), "history": history or []}


@pytest.mark.parametrize("change", [set_change(), {"action": "clear", "source_quote": "不限定地域"}])
def test_scope_only_update_preserves_revision_and_original_fields(change):
    parsed = CandidateConfigureInput.model_validate(update(relevance_scope_change=change))
    assert parsed.model_dump(mode="json", exclude_unset=True) == update(relevance_scope_change=change)


def test_omission_is_keep_and_original_schema_fields_match_exactly():
    parsed = CandidateConfigureInput.model_validate(update(title="更改标题"))
    assert "relevance_scope_change" not in parsed.model_dump(mode="json")
    original = OriginalConfigureInput.model_json_schema()
    candidate = configure_schema()
    for name, definition in original["properties"].items():
        assert candidate["properties"][name] == definition
    assert candidate["required"] == original["required"]
    assert candidate["additionalProperties"] is False
    scope = candidate["properties"]["relevance_scope_change"]
    assert "default" not in scope
    assert all(option.get("type") != "null" for option in scope["oneOf"])
    assert candidate["$defs"]["ClearScopeChange"]["required"] == ["action", "source_quote"]


@pytest.mark.parametrize("payload", [
    update(), update(title=None), update(relevance_scope_change=None),
    update(expected_revision=0, relevance_scope_change=set_change()),
    update(expected_revision=True, relevance_scope_change=set_change()),
    update(draft_id="", relevance_scope_change=set_change()),
    update(lexicon_ref="guessed", relevance_scope_change=set_change()),
    update(lexicon_ref="resource-ref:" + "a" * 32, creator_url="https://example.com"),
    {"operation": "create", "relevance_scope_change": set_change()},
    update(configuration={}), update(unknown="not allowed"),
])
def test_original_constraints_are_not_relaxed(payload):
    with pytest.raises(ValidationError):
        CandidateConfigureInput.model_validate(payload)


@pytest.mark.parametrize("change", [
    {"action": "set", "target_text": "克拉玛依", "source_quote": "调查克拉玛依"},
    set_change(target_text=" "), set_change(source_quote=""),
    set_change(selection_policy="all_provinces"), set_change(message_id="fake"),
    set_change(administrative_code="650200"), set_change(target_text=42),
    {"action": "clear", "source_quote": "不限定地域", "target_text": "克拉玛依"},
    {"action": "clear", "source_quote": "不限定地域", "selection_policy": "local_only"},
    {"action": "keep"}, {"action": "clear", "source_quote": "  "},
])
def test_scope_actions_strict_no_null_or_model_supplied_trusted_metadata(change):
    with pytest.raises(ValidationError):
        CandidateConfigureInput.model_validate(update(relevance_scope_change=change))


def test_create_still_requires_resources_and_rejects_update_identity():
    payload = {"operation": "create", "title": "地域调查", "objective": "调查负面舆情",
               "platform": "dy", "ruleset_ref": "resource-ref:" + "a" * 32,
               "lexicon_ref": "resource-ref:" + "b" * 32, "relevance_scope_change": set_change()}
    assert CandidateConfigureInput.model_validate(payload).operation == "create"
    for patch in ({"draft_id": "d"}, {"expected_revision": 1}, {"platform": "unknown"}):
        with pytest.raises(ValidationError):
            CandidateConfigureInput.model_validate({**payload, **patch})


def test_model_facing_platform_hides_wb_but_original_validator_stays_unchanged():
    original = OriginalConfigureInput.model_json_schema()
    candidate = configure_schema()
    assert original["$defs"]["Platform"]["enum"] == ["xhs", "dy", "ks", "wb"]
    assert candidate["$defs"]["Platform"]["enum"] == ["xhs", "dy", "ks"]
    assert CandidateConfigureInput.model_validate(update(platform="wb")).platform.value == "wb"


def test_source_includes_current_user_and_preserves_exact_quote():
    result = validate_source(case(), "调查克拉玛依")
    assert result["ok"] and result["message_id"] == "user-now"
    assert result["application_turn"] == 3 and result["sequence"] == 8
    assert not validate_source(case(), "调查克拉玛依市")["ok"]


def test_earlier_same_task_source_works_when_resources_become_ready():
    earlier = message("original-intent", application_turn=1, sequence=1)
    result = validate_source(case([earlier], message(content="采用这套词库")), "调查克拉玛依")
    assert result["ok"] and result["message_id"] == "original-intent"
    assert result["source_application_turn"] == 1


@pytest.mark.parametrize("override", [
    {"role": "assistant"}, {"role": "tool"}, {"principal_id": "other-user"},
    {"conversation_id": "other-conversation"}, {"task_round": "round-1"},
    {"application_turn": 4}, {"sequence": 9}, {"application_turn": "3"},
    {"application_turn": True}, {"sequence": -1}, {"message_id": None},
    {"task_round": None},
])
def test_source_rejects_inaccessible_role_old_round_or_future(override):
    invalid = {**message("excluded-source"), **override}
    result = validate_source(case([invalid], message(content="采用这套词库")), "调查克拉玛依")
    assert result["code"] == "SOURCE_NOT_FOUND"


def test_duplicate_quotes_are_ambiguous_even_if_both_sources_accessible():
    earlier = message("earlier", application_turn=1, sequence=1)
    result = validate_source(case([earlier]), "调查克拉玛依")
    assert result["code"] == "SOURCE_AMBIGUOUS"
    assert set(result["candidate_message_ids"]) == {"earlier", "user-now"}


def test_same_message_repeated_in_history_is_one_source_but_conflicts_fail_closed():
    assert validate_source(case([message()]), "调查克拉玛依")["ok"]
    result = validate_source(case([message(content="伪造其他正文")]), "调查克拉玛依")
    assert result["code"] == "SOURCE_CONTEXT_INVALID"


@pytest.mark.parametrize("quote", [None, 4, "", "   "])
def test_empty_or_invalid_source_quote_fails(quote):
    assert validate_source(case(), quote)["code"] == "SOURCE_EMPTY"


def test_missing_or_mismatched_context_fails_closed():
    fixture = case()
    fixture.pop("principal_id")
    assert validate_source(fixture, "调查克拉玛依")["code"] == "SOURCE_CONTEXT_INVALID"
    assert validate_source(case(current=message(application_turn=4)), "调查克拉玛依")["code"] == "SOURCE_CONTEXT_INVALID"


@pytest.mark.parametrize("target,name,level", [
    ("克拉玛依", "克拉玛依市", "city"), ("大庆市", "大庆市", "city"),
    ("新疆维吾尔自治区", "新疆", "province"), ("成都", "成都市", "city"),
    (" 杭州 ", "杭州市", "city"),
    ("苏州", "苏州市", "city"), ("苏州市", "苏州市", "city"),
    ("四川", "四川", "province"), ("四川省", "四川", "province"),
])
def test_controlled_mapping_preserves_original_target(target, name, level):
    result = normalize_target(target)
    assert result["ok"] and result["fixture"]
    assert result["target_text"] == target and result["name"] == name and result["level"] == level


def test_ambiguous_unknown_targets_never_default_to_city():
    assert normalize_target("朝阳")["code"] == "REGION_AMBIGUOUS"
    assert normalize_target("朝阳区")["code"] == "REGION_AMBIGUOUS"
    for target in ("这里", "成都附近", "调查杭州", "", None):
        assert normalize_target(target)["code"] == "REGION_UNRESOLVED"


def test_isolated_import_does_not_load_business_modules():
    command = (
        "import json,sys; import experiments.geographic_scope_intent.contract as c; "
        "print(json.dumps([n for n in sys.modules if n.startswith('backend.')]))"
    )
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == []
    metadata = source_metadata()
    assert len(metadata["configure_sha256"]) == 64 and len(metadata["contracts_sha256"]) == 64
