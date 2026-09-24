"""Prompt placement, real deferred discovery and session-isolation fences."""
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.hermes_runtime.adapter import HermesRuntimeBinding
from backend.hermes_runtime.creation_prompt import CREATION_IDENTITY, install_creation_prompt
from backend.investigation_creation.conversation import CREATION_SYSTEM_PROMPT
from backend.investigation_creation.tools import HERMES_M3_TOOL_SCHEMAS
from backend.resource_management.authoring_guidance import (
    LEXICON_DOMAIN_GUIDANCE, RESOURCE_EDIT_GUIDANCE, RULESET_AUTHORING_GUIDANCE,
)
from backend.resource_management.recall_prompt import RECALL_GENERATION_PROMPT
from backend.resource_management.tools import RESOURCE_PROMPT
from backend.resource_management.generation import generation_messages
from backend.resource_management.generation_contracts import ResourceGenerationRequest


def _agent(mode):
    with patch.object(HermesRuntimeBinding, "activate_product_mode"):
        return HermesRuntimeBinding().create_agent(
            session_id="isolation-test", product_mode=mode,
            agent_factory=lambda **kwargs: SimpleNamespace(_api_max_retries=3),
        )


def test_product_builder_is_creation_only_and_not_a_shared_patch():
    report = _agent("account-activity")
    creation = _agent("creation")
    other = _agent("creation")
    assert not hasattr(report, "_creation_product_system_message")
    assert not hasattr(report, "_build_system_prompt")
    product = CREATION_SYSTEM_PROMPT + RESOURCE_PROMPT
    rendered = creation._build_system_prompt(product)
    assert rendered == CREATION_IDENTITY + "\n\n" + product
    assert creation._cached_system_prompt_static == CREATION_IDENTITY
    assert creation._build_system_prompt() == rendered
    assert creation._build_system_prompt_parts()["context"] == product
    assert "Host: macOS" not in rendered
    assert "You are Hermes Agent" not in rendered
    assert "Python toolchain:" not in rendered
    with pytest.raises(ValueError):
        other._build_system_prompt()
    assert creation._api_max_retries == other._api_max_retries == 1


@pytest.mark.parametrize("message", ["", "   ", 42, {}])
def test_missing_product_instructions_fail_closed(message):
    agent = SimpleNamespace()
    install_creation_prompt(agent)
    with pytest.raises(ValueError):
        agent._build_system_prompt(message)


def test_guidance_is_available_through_native_deferred_describe(tmp_path):
    from tools.tool_search import dispatch_tool_describe

    binding = HermesRuntimeBinding()
    described = {}
    with binding.product_mode_execution(tmp_path / "hermes", product_mode="creation"):
        definitions = binding.tool_definitions(
            enabled_toolsets=["investigation"], product_mode="creation",
        )
        wire_schemas = {item["function"]["name"]: item["function"] for item in definitions}
        for schema in HERMES_M3_TOOL_SCHEMAS:
            name = schema["name"]
            result = json.loads(dispatch_tool_describe(
                {"name": name}, current_tool_defs=definitions,
            ))
            assert result["name"] == name, result
            # Hermes normalizes nullable Pydantic fields for the provider.
            # Verify describe returns the exact registered wire schema.
            assert result["parameters"] == wire_schemas[name]["parameters"]
            assert set(result["parameters"]["properties"]) == set(schema["parameters"]["properties"])
            assert result["parameters"].get("required", []) == schema["parameters"].get("required", [])
            assert result["description"] == schema["description"]
            described[name] = result["description"]
    assert len(described) == 17
    assert "generation_request" in described["create_lexicon_edit"]
    assert "generation_request" in described["create_ruleset_proposal"]
    request = ResourceGenerationRequest(objective="调查", platform="dy")
    assert RECALL_GENERATION_PROMPT in generation_messages("lexicon", request)[0]["content"]
    assert LEXICON_DOMAIN_GUIDANCE in generation_messages("lexicon", request)[0]["content"]
    assert RULESET_AUTHORING_GUIDANCE in generation_messages("ruleset", request)[0]["content"]
    for name in ("update_ruleset_proposal", "update_resource_edit"):
        assert RULESET_AUTHORING_GUIDANCE.strip() in described[name]
    for name in ("open_resource_edit", "update_resource_edit"):
        assert RESOURCE_EDIT_GUIDANCE.strip() in described[name]
    for name in ("save_resource", "get_resource_save", "query_investigation_options"):
        assert RULESET_AUTHORING_GUIDANCE not in described[name]
        assert RECALL_GENERATION_PROMPT not in described[name]


def test_global_prompt_keeps_authority_but_not_authoring_payloads():
    product = CREATION_SYSTEM_PROMPT + RESOURCE_PROMPT
    assert len(product) < 21000
    assert RULESET_AUTHORING_GUIDANCE not in product
    assert LEXICON_DOMAIN_GUIDANCE not in product
    assert RESOURCE_EDIT_GUIDANCE not in product
    assert RECALL_GENERATION_PROMPT not in product
    for boundary in (
        "do not repeat their generation",
        "Only use_ruleset_proposal",
        "presentation_id",
        "confirm_and_queue_investigation after an explicit",
        "get_resource_save",
        "保存不是规则采用",
        "不能声称生成、修改、保存、采用或启动完成",
        "版本冲突需核对内容",
    ):
        assert boundary in product
    assert "不强制增加资源工具" not in product
    assert "由本段替代" not in product


def test_original_prompt_lines_are_preserved_or_explicitly_accounted_for():
    """A wording move cannot silently delete an old behavioral/quality constraint."""
    from backend.investigation_creation.tools import M3_TOOL_DESCRIPTIONS
    baseline = json.loads((Path(__file__).parent / "fixtures" /
                           "creation_prompt_baseline_1c909ad.json").read_text())
    normalize = lambda text: re.sub(r"\s+", "", text)
    current = normalize("\n".join([
        CREATION_SYSTEM_PROMPT, RESOURCE_PROMPT, RECALL_GENERATION_PROMPT,
        RULESET_AUTHORING_GUIDANCE, LEXICON_DOMAIN_GUIDANCE, RESOURCE_EDIT_GUIDANCE,
        *M3_TOOL_DESCRIPTIONS.values(),
    ]))
    changes = {(item["source"], item["line"]): item for item in baseline["changed_lines"]}
    for name, original in baseline["prompts"].items():
        for index, line in enumerate(original.splitlines()):
            if not line.strip():
                continue
            change = changes.get((name, index))
            if change:
                assert change["text"] == line and change["reason"]
            else:
                assert normalize(line) in current, (name, index, line)
    # The large identity paragraph changed only its final lifecycle sentence.
    original = baseline["prompts"]["CREATION_SYSTEM_PROMPT"].splitlines()[7]
    assert original.split("临时词库只写入")[0] in CREATION_SYSTEM_PROMPT
