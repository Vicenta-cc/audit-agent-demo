"""Retry provider input rejection without replaying a tool or changing input."""
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from openai import BadRequestError
import pytest

from backend.hermes_runtime.input_retry import (
    install_creation_input_retries, retry_input_request,
)
from backend.resource_management.contracts import ResourceError
from backend.resource_management.generation_contracts import ResourceGenerationRequest
from test_resource_generation import generator, REQUEST


def blocked(message="Input text data may contain inappropriate content."):
    return BadRequestError(
        message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://test.invalid/v1")),
        body={"code": "data_inspection_failed", "message": message},
    )


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr("backend.hermes_runtime.input_retry.time.sleep", lambda _: None)


def test_fifth_attempt_can_succeed_without_mutating_request():
    request = {"messages": [{"role": "tool", "content": "already saved"}]}
    call = Mock(side_effect=[blocked(), blocked(), blocked(), blocked(), "Qwen answer"])
    agent = SimpleNamespace(_interruptible_api_call=call)
    install_creation_input_retries(agent)
    install_creation_input_retries(agent)
    assert agent._interruptible_api_call(request) == "Qwen answer"
    assert call.call_count == 5
    assert all(item.args[0] is request for item in call.call_args_list)
    assert request == {"messages": [{"role": "tool", "content": "already saved"}]}


def test_nested_stream_fallback_has_one_budget_and_returns_original_error():
    error = blocked()
    call = Mock(side_effect=error)
    agent = SimpleNamespace(_interruptible_api_call=call)
    agent._interruptible_streaming_api_call = lambda request, **kw: agent._interruptible_api_call(request)
    install_creation_input_retries(agent)
    with pytest.raises(BadRequestError) as caught:
        agent._interruptible_streaming_api_call({"messages": []}, on_first_delta=None)
    assert caught.value is error
    assert call.call_count == 5
    assert not agent._creation_input_retry_active


@pytest.mark.parametrize("flag,value", [
    ("_interrupt_requested", True), ("_current_streamed_assistant_text", "partial output"),
])
def test_cancelled_or_partially_delivered_request_is_not_replayed(flag, value):
    call = Mock(side_effect=blocked())
    agent = SimpleNamespace(_interruptible_api_call=call, **{flag: value})
    install_creation_input_retries(agent)
    with pytest.raises(BadRequestError):
        agent._interruptible_api_call({})
    assert call.call_count == 1


@pytest.mark.parametrize("error", [
    blocked("Output text data may contain inappropriate content."),
    RuntimeError("data_inspection_failed input"),
    ValueError("version conflict"),
])
def test_other_errors_are_not_retried(error):
    call = Mock(side_effect=error)
    with pytest.raises(type(error)):
        retry_input_request(call)
    assert call.call_count == 1


def test_error_status_and_code_must_match():
    error = blocked()
    error.status_code = 401
    call = Mock(side_effect=error)
    with pytest.raises(BadRequestError):
        retry_input_request(call)
    assert call.call_count == 1


@pytest.mark.parametrize("body", [
    {"error": {"code": "data_inspection_failed", "message": "Input text"}},
    {"code": "DataInspectionFailed", "message": "Input text"},
    'data: {"error":{"code":"data_inspection_failed","message":"Input text"}}',
])
def test_stream_api_error_body_is_recognized(body):
    from openai import APIError
    error = APIError("inspection", httpx.Request("POST", "https://test.invalid/v1"),
                     body=body)
    call = Mock(side_effect=[error, "ok"])
    assert retry_input_request(call) == "ok"
    assert call.call_count == 2


def test_resource_author_retries_then_validates_the_original_request():
    gen, client, _ = generator()
    response = client.chat.completions.create.return_value
    client.chat.completions.create.side_effect = [blocked()] * 4 + [response]
    parsed = gen.generate("lexicon", ResourceGenerationRequest(**REQUEST))
    assert len(parsed.search_terms()) == 6
    calls = client.chat.completions.create.call_args_list
    assert len(calls) == 5
    assert all(call == calls[0] for call in calls)


def test_author_exhaustion_remains_failure_without_resource():
    gen, client, _ = generator(error=blocked())
    with pytest.raises(ResourceError) as caught:
        gen.generate("lexicon", ResourceGenerationRequest(**REQUEST))
    assert client.chat.completions.create.call_count == 5
    assert caught.value.code == "INPUT_CONTENT_BLOCKED"
    assert caught.value.details["resource_created"] is False
