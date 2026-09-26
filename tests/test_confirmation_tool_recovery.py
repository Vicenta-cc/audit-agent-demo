"""Confirmation argument repair must not consume the start idempotency fence."""
from unittest.mock import patch

import pytest

from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import (
    ConfirmAndQueueInvestigationInput,
    HERMES_M3_TOOL_SCHEMAS,
    HermesToolExecutionIdentity,
)
from test_investigation_creation_conversation import (
    creation_stack,
    _mutation_receipt_count,
    _run_count,
    _search_draft_from_options,
)


def prepared_confirmation(stack, *, revision=1):
    principal = Principal("principal-a")
    tools = stack["tool_service"]
    settings = stack["app_service"].resource_service.task_settings
    if revision:
        settings.save({"max_notes": 2}, 0)
    options = tools.execute(
        "query_investigation_options",
        {"domain_hint": "博彩", "mode": "search", "include_lexicon_terms_for_ids": ["gambling"]},
        principal=principal,
    )
    view = tools.execute("create_investigation_draft", _search_draft_from_options([options]), principal=principal)
    return {
        "draft_id": view["draft"]["id"],
        "expected_revision": view["draft"]["current_revision"],
        "expected_task_settings_revision": view["confirmation_preview"]["task_settings_revision"],
        "confirmed": True,
        "idempotency_key": "stable-confirm-key",
    }


def confirm(stack, arguments, call_id, *, principal="principal-a"):
    return stack["tool_service"].execute_with_identity(
        "confirm_and_queue_investigation", arguments,
        principal=Principal(principal),
        identity=HermesToolExecutionIdentity.require(
            session_id="session:confirmation-recovery", turn_id="turn:confirmation-recovery", tool_call_id=call_id
        ),
    )


@pytest.mark.parametrize("invalid_value", ["missing", None, -1, True, "1", 1.5])
def test_missing_or_invalid_settings_version_can_be_repaired_in_same_turn(creation_stack, invalid_value):
    stack = creation_stack
    arguments = prepared_confirmation(stack)
    assert arguments["expected_task_settings_revision"] == 1
    invalid = dict(arguments)
    if invalid_value == "missing":
        invalid.pop("expected_task_settings_revision")
    else:
        invalid["expected_task_settings_revision"] = invalid_value
    with patch.object(stack["app_service"], "confirm_and_queue") as application:
        rejected = confirm(stack, invalid, "call:missing")
    application.assert_not_called()
    assert rejected["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    details = rejected["error"]["details"]
    assert details["receipt_created"] is False
    assert details["mutation_applied"] is False
    assert details["run_created"] is False
    assert details["retryable"] is True
    assert _mutation_receipt_count(stack["creation_store"]) == 0
    assert _run_count(stack["creation_store"]) == 0

    result = confirm(stack, arguments, "call:corrected")
    assert result["status"] == "ok", result
    assert confirm(stack, arguments, "call:corrected") == result
    assert confirm(stack, arguments, "call:duplicate") == result
    assert _run_count(stack["creation_store"]) == 1
    assert _mutation_receipt_count(stack["creation_store"]) == 1
    conflict = confirm(stack, {**arguments, "idempotency_key": "different-key"}, "call:conflict")
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert _run_count(stack["creation_store"]) == 1


def test_schema_requires_settings_version_and_accepts_real_zero(creation_stack):
    schema = ConfirmAndQueueInvestigationInput.model_json_schema()
    assert "expected_task_settings_revision" in schema["required"]
    tool_schema = next(item for item in HERMES_M3_TOOL_SCHEMAS if item["name"] == "confirm_and_queue_investigation")
    assert "expected_task_settings_revision" in tool_schema["parameters"]["required"]
    arguments = prepared_confirmation(creation_stack, revision=0)
    assert arguments["expected_task_settings_revision"] == 0
    assert confirm(creation_stack, arguments, "call:zero")["status"] == "ok"


def test_stale_settings_still_rejected_without_start(creation_stack):
    arguments = prepared_confirmation(creation_stack)
    creation_stack["app_service"].resource_service.task_settings.save({"max_notes": 3}, 1)
    result = confirm(creation_stack, arguments, "call:stale")
    assert result["error"]["code"] == "TASK_SETTINGS_CHANGED"
    assert _run_count(creation_stack["creation_store"]) == 0


def test_confirmation_does_not_allow_another_user_draft(creation_stack):
    arguments = prepared_confirmation(creation_stack)
    result = confirm(creation_stack, arguments, "call:other-user", principal="principal-b")
    assert result["status"] == "error"
    assert _run_count(creation_stack["creation_store"]) == 0


def test_successful_confirmation_replay_survives_later_settings_change(creation_stack):
    arguments = prepared_confirmation(creation_stack)
    result = confirm(creation_stack, arguments, "call:original")
    assert result["status"] == "ok"
    creation_stack["app_service"].resource_service.task_settings.save({"max_notes": 3}, 1)
    assert confirm(creation_stack, arguments, "call:duplicate") == result
    assert _run_count(creation_stack["creation_store"]) == 1


def test_unknown_confirmation_outcome_is_not_retried(creation_stack):
    arguments = prepared_confirmation(creation_stack)
    store = creation_stack["creation_store"]
    store.begin_tool_execution(
        session_id="session:confirmation-recovery",
        turn_id="turn:confirmation-recovery",
        tool_call_id="call:interrupted",
        principal="principal-a",
        tool_name="confirm_and_queue_investigation",
        arguments=arguments,
        is_mutation=True,
    )
    with patch.object(creation_stack["app_service"], "confirm_and_queue") as application:
        result = confirm(creation_stack, arguments, "call:retry")
    application.assert_not_called()
    assert result["error"]["code"] == "MUTATION_RESULT_UNKNOWN"
    assert _run_count(store) == 0
    assert _mutation_receipt_count(store) == 1
