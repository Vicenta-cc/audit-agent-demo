"""Dedicated generation: routing, unchanged quality prompts, contracts and receipts."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from backend.resource_management.contracts import CreateLexiconInput, LexiconContent, ResourceError
from backend.resource_management.generation import ResourceGenerator, generation_messages
from backend.resource_management.generation_contracts import ResourceGenerationRequest
from backend.rulesets.contracts import RuleSetContent
from backend.investigation_creation.tools import CreateRuleSetProposalInput, HermesToolExecutionIdentity
from backend.investigation_creation.principal import Principal
from test_investigation_creation_conversation import creation_stack, _draft_count, _run_count
from test_ruleset_proposal_presentation import content

REQUEST = {"objective": "招聘押金风险研究", "platform": "dy"}
PRINCIPAL = Principal("principal-a", role="admin")


def lexicon(count=6):
    return {"title": "招聘风险", "entries": [
        {"id": "m", "term": "招聘风险", "kind": "main"},
        *[{"id": f"v{i}", "term": f"招聘押金{i}", "kind": "variant", "parent_id": "m"} for i in range(count)],
    ]}


def authoring_lexicon(count=6):
    return {"title": "招聘风险", "themes": [
        {"term": "招聘风险", "variants": [{"term": f"招聘押金{i}"} for i in range(count)]},
    ]}


def generator(payload=None, *, finish="stop", refusal=None, error=None):
    client = MagicMock()
    client.__enter__.return_value = client
    client.chat.completions.create.return_value = SimpleNamespace(
        _request_id="provider-request",
        choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(
            content=json.dumps(payload if payload is not None else authoring_lexicon(), ensure_ascii=False), refusal=refusal))],
    )
    if error:
        client.chat.completions.create.side_effect = error
    factory = MagicMock(return_value=client)
    config = SimpleNamespace(resource_generation_api_key="test-private",
        resource_generation_base_url="https://resource.example/v1", resource_generation_model="qwen-test")
    return ResourceGenerator(config=config, client_factory=factory), client, factory


@pytest.mark.parametrize("model", [CreateLexiconInput, CreateRuleSetProposalInput])
def test_exactly_one_input(model, content):
    body = lexicon() if model is CreateLexiconInput else content
    for args in ({}, {"content": body, "generation_request": REQUEST}):
        with pytest.raises(ValidationError):
            model.model_validate(args)
    assert model.model_validate({"content": body}).generation_request is None
    assert model.model_validate({"generation_request": REQUEST}).content is None


def test_dedicated_request_has_two_messages_no_tools_no_retry():
    gen, client, factory = generator()
    parsed = gen.generate("lexicon", ResourceGenerationRequest(**REQUEST))
    assert len(parsed.search_terms()) == 6
    args = client.chat.completions.create.call_args.kwargs
    assert len(args["messages"]) == 2
    assert [m["role"] for m in args["messages"]] == ["system", "user"]
    assert "tools" not in args and not args["stream"]
    assert args["extra_body"] == {"enable_thinking": False}
    assert "completed_public_presentations" not in args["messages"][0]["content"]
    assert factory.call_args.kwargs["max_retries"] == 0
    assert factory.call_args.kwargs["base_url"] == "https://resource.example/v1"
    client.close.assert_not_called()  # lifetime is owned by the context manager
    client.__exit__.assert_called_once()


@pytest.mark.parametrize("finish,refusal,code", [
    ("length", None, "OUTPUT_LENGTH_TRUNCATED"),
    ("content_filter", None, "OUTPUT_CONTENT_BLOCKED"),
    ("stop", "refused", "OUTPUT_CONTENT_BLOCKED"),
    ("tool_calls", None, "RESOURCE_GENERATION_INCOMPLETE"),
])
def test_incomplete_or_blocked_output_never_becomes_content(finish, refusal, code):
    gen, client, _ = generator(finish=finish, refusal=refusal)
    with pytest.raises(ResourceError) as caught:
        gen.generate("lexicon", ResourceGenerationRequest(**REQUEST))
    assert caught.value.code == code
    assert not caught.value.details["mutation_applied"]
    assert not caught.value.details["retryable"]
    assert client.chat.completions.create.call_count == 1


@pytest.mark.parametrize("text,code", [
    ("400 data_inspection_failed Input text", "INPUT_CONTENT_BLOCKED"),
    ("400 DataInspectionFailed Output data", "OUTPUT_CONTENT_BLOCKED"),
    ("400 data_inspection_failed", "PROVIDER_CONTENT_BLOCKED"),
    ("timeout test-private", "RESOURCE_GENERATION_PROVIDER_ERROR"),
])
def test_provider_error_is_classified_without_replay_or_secret_echo(text, code, caplog):
    gen, client, _ = generator(error=RuntimeError(text))
    with pytest.raises(ResourceError) as caught:
        gen.generate("lexicon", ResourceGenerationRequest(**REQUEST))
    assert caught.value.code == code
    assert "test-private" not in str(caught.value)
    assert "test-private" not in caplog.text
    assert client.chat.completions.create.call_count == 1


def test_count_policy_applies_to_generation_not_import():
    gen, _, _ = generator(authoring_lexicon(11))
    with pytest.raises(ResourceError):
        gen.generate("lexicon", ResourceGenerationRequest(**REQUEST))
    parsed = gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, requested_count=11))
    assert len(parsed.search_terms()) == 11
    assert len(CreateLexiconInput(content=lexicon(11)).content.search_terms()) == 11
    for count in (1, 4, 5, 10):
        gen, _, _ = generator(authoring_lexicon(count))
        assert len(gen.generate("lexicon", ResourceGenerationRequest(**REQUEST)).search_terms()) == count


def test_exact_terms_preserved_or_rejected_no_silent_trimming():
    terms = LexiconContent.model_validate(lexicon()).search_terms()
    gen, _, _ = generator()
    assert gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, exact_terms=terms)).search_terms() == terms
    with pytest.raises(ResourceError):
        gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, exact_terms=["用户原文"]))
    with pytest.raises(ValidationError):
        ResourceGenerationRequest(**REQUEST, exact_terms=terms, requested_count=2)


def test_rules_are_schema_validated_and_count_preserved(content):
    gen, _, _ = generator(content)
    count = sum(len(c["rules"]) for c in content["categories"])
    result = gen.generate("ruleset", ResourceGenerationRequest(**REQUEST, requested_count=count))
    assert result == RuleSetContent.model_validate(content)
    with pytest.raises(ResourceError):
        gen.generate("ruleset", ResourceGenerationRequest(**REQUEST, requested_count=count + 1))


def test_generation_receipt_replay_save_and_import_do_not_regenerate(creation_stack):
    stack = creation_stack
    tools = stack["tool_service"]
    gen, client, _ = generator()
    tools.resource_generator = gen
    identity = HermesToolExecutionIdentity("session-generation", "turn-generate", "call-generate")
    args = {"generation_request": REQUEST}
    result = tools.execute_with_identity("create_lexicon_edit", args, principal=PRINCIPAL, identity=identity)
    assert result["status"] == "ok", result
    assert tools.execute_with_identity("create_lexicon_edit", args, principal=PRINCIPAL, identity=identity) == result
    edit = result["data"]
    assert not edit["saved"]
    saved = tools.execute("save_resource", {"edit_id": edit["edit_id"], "expected_version": edit["version"],
                          "operation_id": "save-generated"}, session_id=identity.session_id, principal=PRINCIPAL)
    assert saved["resource_id"]
    imported = tools.execute("create_lexicon_edit", {"content": edit["content"]},
                             session_id=identity.session_id, principal=PRINCIPAL)
    assert imported["content"] == edit["content"]
    assert client.chat.completions.create.call_count == 1
    assert _draft_count(stack["creation_store"]) == _run_count(stack["creation_store"]) == 0


def test_blocked_generation_leaves_no_edit_and_replayed_call_does_not_retry(creation_stack):
    tools = creation_stack["tool_service"]
    _ = tools.application_service.resource_management  # lazily create the empty resource schema
    gen, client, _ = generator(error=RuntimeError("400 data_inspection_failed Input data"))
    tools.resource_generator = gen
    identity = HermesToolExecutionIdentity("session-generation", "turn-block", "call-block")
    args = {"generation_request": REQUEST}
    first = tools.execute_with_identity("create_lexicon_edit", args, principal=PRINCIPAL, identity=identity)
    assert first["status"] == "error" and first["error"]["code"] == "INPUT_CONTENT_BLOCKED"
    second = tools.execute_with_identity("create_lexicon_edit", args, principal=PRINCIPAL, identity=identity)
    assert second == first
    assert client.chat.completions.create.call_count == 1
    with creation_stack["creation_store"]._connect() as db:
        assert db.execute("select count(*) from lexicon_edits").fetchone()[0] == 0
