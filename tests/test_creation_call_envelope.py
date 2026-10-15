"""Observed bridge envelopes are compatible; business contracts stay strict."""
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


@pytest.mark.parametrize('name', ['create_ruleset_proposal', 'create_lexicon_edit'])
def test_flat_generation_envelope_preserves_request_and_raw_call(name):
    request = {'objective': '调查招聘收费', 'platform': 'dy', 'requirements': '保留原要求'}
    raw = {'arguments': {'name': name, 'generation_request': request}}
    before = deepcopy(raw)
    adapted = unwrap_extra_call_envelope(raw)
    assert adapted == {'name': name, 'arguments': {'generation_request': request}}
    adapted['arguments']['generation_request']['objective'] = 'changed downstream'
    assert raw == before


@pytest.mark.parametrize('inner', [
    {'name': 'unknown_business', 'generation_request': {}},
    {'name': 'tool_call', 'generation_request': {}},
    {'name': 'create_lexicon_edit', 'generation_request': '{}'},
    {'name': 'create_lexicon_edit', 'generation_request': {}, 'arguments': {}},
    {'name': 'create_lexicon_edit', 'generation_request': {}, 'platform': 'dy'},
    {'name': 'read_resource', 'kind': 'lexicon'},
])
def test_flat_envelope_rejects_unobserved_or_ambiguous_shapes(inner):
    raw = {'arguments': inner}
    assert unwrap_extra_call_envelope(raw) is raw


def test_flat_generation_adaptation_still_uses_native_resolution_and_validation():
    from backend.resource_management.contracts import CreateLexiconInput
    raw = {'arguments': {'name': 'create_lexicon_edit', 'generation_request': {
        'objective': '调查招聘收费', 'platform': 'dy', 'unexpected_field': True}}}
    resolver = Mock(side_effect=lambda args: (args['name'], args['arguments'], None))
    bridge = SimpleNamespace(bridge_tool_schemas=lambda: [],
                             validate_deferred_call_args=lambda *a: None,
                             resolve_underlying_call=resolver)
    with creation_discovery_guidance(bridge):
        name, business_args, error = bridge.resolve_underlying_call(raw)
        assert name == 'create_lexicon_edit' and error is None
        with pytest.raises(ValidationError, match='unexpected_field'):
            CreateLexiconInput.model_validate(business_args)
    resolver.assert_called_once_with(unwrap_extra_call_envelope(raw))


@pytest.mark.parametrize('raw', [
    {'name': 'save_resource', 'arguments': {'name': 'create_lexicon_edit', 'generation_request': {}}},
    {'arguments': {'arguments': {'name': 'create_lexicon_edit', 'generation_request': {}}}},
    {'arguments': {'name': 'create_lexicon_edit', 'generation_request': {}}, 'extra': True},
])
def test_flat_generation_does_not_unwrap_conflicting_or_recursive_calls(raw):
    assert unwrap_extra_call_envelope(raw) is raw


def test_flat_generation_keeps_native_authorization_rejection():
    rejection = (None, {}, 'not authorized in this session')
    original_resolve = Mock(return_value=rejection)
    bridge = SimpleNamespace(bridge_tool_schemas=lambda: [],
                             validate_deferred_call_args=lambda *a: None,
                             resolve_underlying_call=original_resolve)
    raw = {'arguments': {'name': 'create_lexicon_edit', 'generation_request': {
        'objective': '调查招聘收费', 'platform': 'dy'}}}
    with creation_discovery_guidance(bridge):
        assert bridge.resolve_underlying_call(raw) is rejection
    original_resolve.assert_called_once_with(unwrap_extra_call_envelope(raw))
