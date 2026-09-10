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
    bridge = SimpleNamespace(bridge_tool_schemas=original)
    binding = HermesRuntimeBinding()
    with patch.object(HermesRuntimeBinding, "configure_product_home"), \
         patch.object(HermesRuntimeBinding, "discover_plugins"), \
         patch("backend.hermes_runtime.adapter._load_module", return_value=bridge) as loader:
        with pytest.raises(RuntimeError, match="turn failed"):
            with binding.product_mode_execution(tmp_path, product_mode=mode):
                definition = bridge.bridge_tool_schemas(10)[0]["function"]
                assert (definition["description"] != "Original") == (mode == "creation")
                assert definition["parameters"] == {"type": "object"}
                raise RuntimeError("turn failed")
        assert bridge.bridge_tool_schemas is original
        assert source[0]["function"]["description"] == "Original"
        assert loader.call_count == (1 if mode == "creation" else 0)


def test_collection_default_keeps_explicit_existing_limits():
    assert CollectionConfiguration(keywords=["测试"]).max_comments == 300
    assert CollectionConfiguration(keywords=["测试"], max_comments=1000).max_comments == 1000
    assert CollectionConfiguration(keywords=["测试"], max_comments=0).max_comments == 0
