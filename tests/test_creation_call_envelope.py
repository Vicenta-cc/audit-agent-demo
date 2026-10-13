"""Only the observed redundant envelope is compatible; business contracts stay strict."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from backend.hermes_runtime.creation_discovery import (
    creation_discovery_guidance, unwrap_extra_call_envelope,
)
from backend.report_collection.tools import DeliveryQuery, Content


@pytest.mark.parametrize('business_args', [
    {'report_refs': ['report-b']},
    {'name_contains': '小明', 'kind': 'accounts', 'scope': 'authorized_history', 'limit': 50},
])
def test_observed_envelope_preserves_exact_query_and_raw_call(business_args):
    raw = {'arguments': {'name': 'query_conversation_reports', 'arguments': business_args}}
    before = deepcopy(raw)
    adapted = unwrap_extra_call_envelope(raw)
    assert adapted == raw['arguments']
    DeliveryQuery.model_validate(adapted['arguments'])
    adapted['arguments']['kind'] = 'changed downstream'
    assert raw == before


@pytest.mark.parametrize('raw', [
    {}, None, [],
    {'arguments': {}},
    {'arguments': {'name': 'read_resource'}},
    {'arguments': {'name': '', 'arguments': {}}},
    {'arguments': {'name': 123, 'arguments': {}}},
    {'arguments': {'name': 'read_resource', 'arguments': '{}'}},
    {'arguments': {'name': 'read_resource', 'arguments': {}, 'extra': True}},
    {'arguments': {'arguments': {'name': 'read_resource', 'arguments': {}}}},
    {'name': 'read_resource', 'arguments': {'kind': 'ruleset'}},
    {'name': 'save_resource', 'arguments': {'name': 'read_resource', 'arguments': {}}},
    {'name': '', 'arguments': {'name': 'read_resource', 'arguments': {}}},
    {'arguments': {'name': 'read_resource', 'arguments': {}}, 'extra': True},
])
def test_other_shapes_are_not_reinterpreted(raw):
    assert unwrap_extra_call_envelope(raw) is raw


def test_observed_wrong_business_field_is_still_rejected():
    raw = {'arguments': {'name': 'query_report_content', 'arguments': {
        'report_ref': 'report-one', 'content_type': 'comments'}}}
    with pytest.raises(ValidationError, match='content_type'):
        Content.model_validate(unwrap_extra_call_envelope(raw)['arguments'])


def test_native_resolver_checks_names_and_preserves_its_rejection():
    rejection = (None, {}, 'not a deferrable tool')
    original_resolve = Mock(return_value=rejection)
    bridge = SimpleNamespace(bridge_tool_schemas=lambda: [],
                             validate_deferred_call_args=lambda *a: None,
                             resolve_underlying_call=original_resolve)
    raw = {'arguments': {'name': 'tool_describe', 'arguments': {'name': 'read_resource'}}}
    with creation_discovery_guidance(bridge):
        assert bridge.resolve_underlying_call(raw) is rejection
    original_resolve.assert_called_once_with(raw['arguments'])
    assert bridge.resolve_underlying_call is original_resolve
