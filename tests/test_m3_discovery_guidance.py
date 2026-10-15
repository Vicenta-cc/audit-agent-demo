"""Guard the shared Hermes registry boundary, including failed M3 turns."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.hermes_runtime.adapter import HermesRuntimeBinding
from backend.investigation_creation.contracts import CollectionConfiguration


@pytest.mark.parametrize("mode", ["creation", "account-activity", "report"])
def test_discovery_guidance_is_scoped_and_restored(tmp_path, mode):
    source = [{"type": "function", "function": {
        "name": "tool_call", "description": "Original", "parameters": {"type": "object"}
    }}]
    original = lambda *a, **kw: source
    original_resolve = lambda args: (args.get('name'), args.get('arguments', {}), None)
    original_validate = lambda name, args: None
    bridge = SimpleNamespace(bridge_tool_schemas=original,
                             validate_deferred_call_args=original_validate,
                             resolve_underlying_call=original_resolve)
    binding = HermesRuntimeBinding()
    with patch.object(HermesRuntimeBinding, "configure_product_home"), \
         patch.object(HermesRuntimeBinding, "discover_plugins"), \
         patch("backend.hermes_runtime.adapter._load_module", return_value=bridge) as loader:
        with pytest.raises(RuntimeError, match="turn failed"):
            with binding.product_mode_execution(tmp_path, product_mode=mode):
                definition = bridge.bridge_tool_schemas(10)[0]["function"]
                assert (definition["description"] != "Original") == (mode == "creation")
                assert definition["parameters"] == {"type": "object"}
                assert (bridge.resolve_underlying_call is not original_resolve) == (mode == 'creation')
                raise RuntimeError("turn failed")
        assert bridge.bridge_tool_schemas is original
        assert bridge.resolve_underlying_call is original_resolve
        assert bridge.validate_deferred_call_args is original_validate
        assert source[0]["function"]["description"] == "Original"
        assert loader.call_count == (1 if mode == "creation" else 0)


def test_collection_default_keeps_explicit_existing_limits():
    assert CollectionConfiguration(keywords=["测试"]).max_comments == 300
    assert CollectionConfiguration(keywords=["测试"], max_comments=1000).max_comments == 1000
    assert CollectionConfiguration(keywords=["测试"], max_comments=0).max_comments == 0


@pytest.mark.parametrize("name,visible_contract", [
    ("read_resource", ("只读", "kind", "必填", "ruleset", "lexicon", "resource_id", "不用版本ID")),
    ("get_resource_edit", ("只读", "仅传 edit_id", "不传 expected_version")),
])
def test_read_contract_survives_deferred_catalog_truncation(name, visible_contract):
    from backend.investigation_creation.tools import _ALL_CREATION_TOOL_SCHEMAS

    definition = next(item for item in _ALL_CREATION_TOOL_SCHEMAS if item["name"] == name)
    # Keep the contract on a short first line so Hermes' 60-char, word-boundary
    # catalog clipping preserves it when whitespace is collapsed.
    first_line = definition["description"].split("\n", 1)[0]
    assert len(first_line) <= 60
    visible = first_line[:60]
    for requirement in visible_contract:
        assert requirement in visible, (name, requirement, visible)


def test_edit_read_description_preserves_edit_and_history_boundaries():
    from backend.investigation_creation.tools import _ALL_CREATION_TOOL_SCHEMAS

    definition = next(item for item in _ALL_CREATION_TOOL_SCHEMAS if item["name"] == "get_resource_edit")
    assert "read_arguments" in definition["description"]
    assert "update_resource_edit" in definition["description"]
    assert "read_session_resource" in definition["description"]
    assert set(definition["parameters"]["properties"]) == {"edit_id"}
