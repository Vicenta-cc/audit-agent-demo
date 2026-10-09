from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from backend.hermes_runtime.adapter import HermesReportToolsUnavailable, HermesRuntimeBinding
from hermes_m0.unified_support import unified_tool_schemas


NAMES = {schema["name"] for schema in unified_tool_schemas()}
BRIDGE = {"tool_search", "tool_describe", "tool_call"}


def definitions(names):
    return [{"type": "function", "function": {"name": name}} for name in sorted(names)]


def construct(visible, catalog=None, deferred=None, valid=None, product_mode="unified-report"):
    agent = SimpleNamespace(
        tools=definitions(visible), valid_tool_names=visible if valid is None else valid,
        _api_max_retries=3, disabled_toolsets=None, close=Mock(), run_conversation=Mock(),
    )
    model_tools = SimpleNamespace(get_tool_definitions=Mock(return_value=definitions(NAMES if catalog is None else catalog)))
    search = SimpleNamespace(scoped_deferrable_names=Mock(return_value=NAMES if deferred is None else deferred))
    modules = {"model_tools": model_tools, "tools.tool_search": search}
    with patch("backend.hermes_runtime.adapter._load_module", side_effect=modules.__getitem__):
        try:
            result = HermesRuntimeBinding().create_agent(
                session_id="report-tool-preflight", product_mode=product_mode,
                agent_factory=lambda **_: agent,
            )
        except HermesReportToolsUnavailable:
            agent.close.assert_called_once()
            agent.run_conversation.assert_not_called()
            raise
    model_tools.get_tool_definitions.assert_called_once_with(
        enabled_toolsets=["investigation"], disabled_toolsets=None,
        quiet_mode=True, skip_tool_search_assembly=True,
    )
    assert result is agent
    agent.close.assert_not_called()
    agent.run_conversation.assert_not_called()
    return agent


@pytest.mark.parametrize("visible", [set(), {"tool_call"}, BRIDGE - {"tool_describe"}, NAMES - {"list_post_comments"}])
def test_report_agent_rejects_missing_entry_points_before_model_call(visible):
    with pytest.raises(HermesReportToolsUnavailable, match="entry points"):
        construct(visible)


def test_visible_schema_must_also_be_in_runtime_validator():
    with pytest.raises(HermesReportToolsUnavailable, match="entry points"):
        construct(BRIDGE, valid=set())


@pytest.mark.parametrize("visible", [NAMES, BRIDGE])
def test_direct_and_native_deferred_entry_points_are_both_legal(visible):
    construct(visible)


@pytest.mark.parametrize("visible", [NAMES, BRIDGE])
def test_bridge_or_stale_schema_cannot_hide_missing_business_catalog(visible):
    with pytest.raises(HermesReportToolsUnavailable, match="catalog"):
        construct(visible, catalog=NAMES - {"list_post_comments"})


def test_bridge_cannot_expose_tools_outside_its_deferred_scope():
    with pytest.raises(HermesReportToolsUnavailable, match="not callable"):
        construct(BRIDGE, deferred=NAMES - {"list_post_comments"})


@pytest.mark.parametrize("case", ["entry", "catalog", "deferred", "valid"])
def test_continuous_creation_requires_original_report_preflight(monkeypatch, case):
    from backend.audit_agent.config import settings

    monkeypatch.setattr(settings, "continuous_resource_session_enabled", True)
    kwargs = {
        "entry": {"visible": set()},
        "catalog": {"visible": BRIDGE, "catalog": NAMES - {"read_report"}},
        "deferred": {"visible": BRIDGE, "deferred": NAMES - {"read_report"}},
        "valid": {"visible": BRIDGE, "valid": set()},
    }[case]
    with pytest.raises(HermesReportToolsUnavailable):
        construct(**kwargs, product_mode="creation")


def test_continuous_creation_accepts_real_deferred_report_catalog(monkeypatch):
    from backend.audit_agent.config import settings

    monkeypatch.setattr(settings, "continuous_resource_session_enabled", True)
    construct(BRIDGE, product_mode="creation")


def test_legacy_creation_does_not_require_report_tools(monkeypatch):
    from backend.audit_agent.config import settings

    monkeypatch.setattr(settings, "continuous_resource_session_enabled", False)
    agent = SimpleNamespace(_api_max_retries=3, close=Mock())
    with patch.object(HermesRuntimeBinding, "validate_report_tools") as validate:
        result = HermesRuntimeBinding().create_agent(
            session_id="legacy-creation", product_mode="creation", agent_factory=lambda **_: agent,
        )
    assert result is agent
    validate.assert_not_called()
    agent.close.assert_not_called()
