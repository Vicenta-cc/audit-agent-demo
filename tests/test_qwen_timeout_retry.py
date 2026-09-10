from unittest.mock import Mock

import pytest
import requests

from backend.audit_agent.config import settings
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.pipeline import AuditPipeline, AuditProviderCallError
from backend.audit_agent.qwen_client import QwenClient, QwenProviderError, QwenTimeoutError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "use_remote_llm", False)
    monkeypatch.setattr("backend.audit_agent.qwen_client.time.sleep", Mock())
    value = QwenClient()
    value.api_key = "test-only-key"
    value.chat_url = "https://example.invalid/chat/completions"
    return value


def response():
    value = Mock()
    value.json.return_value = {
        "choices": [{"message": {"content": '{"comments":[]}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    }
    return value


@pytest.mark.parametrize("timeout_type", [requests.ReadTimeout, requests.ConnectTimeout])
def test_timeout_retries_once_with_identical_request(client, monkeypatch, caplog, timeout_type):
    post = Mock(side_effect=[timeout_type("sensitive-message"), response()])
    monkeypatch.setattr("backend.audit_agent.qwen_client.requests.post", post)
    result = client.audit_text("private-comment", max_tokens=6000, model="test-model", enable_thinking=True, request_timeout=120)
    assert result["comments"] == []
    assert post.call_count == 2
    assert post.call_args_list[0] == post.call_args_list[1]
    assert post.call_args.kwargs["timeout"] == 120
    assert not client.provider_failure
    assert "retrying once" in caplog.text
    assert "private-comment" not in caplog.text
    assert "test-only-key" not in caplog.text
    assert "sensitive-message" not in caplog.text


def test_second_timeout_propagates_and_never_calls_a_third_time(client, monkeypatch):
    post = Mock(side_effect=requests.ReadTimeout())
    monkeypatch.setattr("backend.audit_agent.qwen_client.requests.post", post)
    with pytest.raises(QwenTimeoutError, match="after one retry") as caught:
        client.audit_text("comment")
    assert post.call_count == 2
    assert isinstance(caught.value.__cause__, requests.ReadTimeout)
    assert client.provider_failure


@pytest.mark.parametrize("failure", [requests.ConnectionError(), requests.HTTPError(), ValueError()])
def test_non_timeout_errors_are_not_retried(client, monkeypatch, failure):
    post = Mock(side_effect=failure)
    monkeypatch.setattr("backend.audit_agent.qwen_client.requests.post", post)
    with pytest.raises(QwenProviderError):
        client.audit_text("comment")
    assert post.call_count == 1


def test_success_and_invalid_json_are_not_timeout_retried(client, monkeypatch):
    reply = response()
    reply.json.return_value["choices"][0]["message"]["content"] = "not-json"
    post = Mock(return_value=reply)
    monkeypatch.setattr("backend.audit_agent.qwen_client.requests.post", post)
    assert client.audit_text("comment")["raw_response"] == "not-json"
    assert post.call_count == 1


@pytest.mark.parametrize("recover", [True, False])
def test_remote_text_has_the_same_timeout_bound(client, monkeypatch, recover):
    monkeypatch.setattr(settings, "use_remote_llm", True)
    client.remote = Mock(enabled=True)
    client.remote.audit_text.side_effect = [requests.ReadTimeout(), {"comments": []}] if recover else requests.ReadTimeout()
    if recover:
        assert client.audit_text("comment", request_timeout=120) == {"comments": []}
    else:
        with pytest.raises(QwenTimeoutError):
            client.audit_text("comment", request_timeout=120)
    assert client.remote.audit_text.call_count == 2
    assert client.remote.audit_text.call_args_list[0] == client.remote.audit_text.call_args_list[1]


@pytest.mark.parametrize("stage", ["comments", "fusion"])
def test_pipeline_does_not_stack_more_retries_after_client_exhaustion(client, monkeypatch, stage):
    post = Mock(side_effect=requests.ReadTimeout())
    monkeypatch.setattr("backend.audit_agent.qwen_client.requests.post", post)
    monkeypatch.setattr("backend.audit_agent.pipeline.job_store.log", Mock())
    monkeypatch.setattr(settings, "fusion_timeout_retries", 5)
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.qwen = client
    pipeline.job_id = "isolated-timeout-test"
    pipeline.authoritative_m3 = True
    pipeline._render_comment_audit_prompt = Mock(return_value="comment batch")
    with pytest.raises(AuditProviderCallError, match="timed out"):
        if stage == "comments":
            pipeline._audit_comment_batch_with_fallback(
                AuditSubject(platform="dy", note_id="note", url="", title="", desc="",
                             author={}, image_urls=[], video_urls=[], comments=[]),
                "context", [{"comment_id": str(i), "source_text": "comment"} for i in range(20)],
            )
        else:
            pipeline._run_fusion_audit("note", "fusion prompt")
    assert post.call_count == 2
