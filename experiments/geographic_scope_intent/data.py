"""Synthetic, human-labelled conversation fixtures; never model answers.

Labels belong exclusively to evaluation. No provider or business service imports.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA_VERSION = 'geo-intent-synthetic-v1'
DEFAULT_CASES_PATH = Path(__file__).with_name('cases.jsonl')
LABELS = frozenset({'set', 'clear', 'keep', 'clarify', 'defer', 'no_task'})
POLICIES = frozenset({'local_only', 'local_first_with_supplement'})
REQUIRED_CATEGORIES = frozenset({
    'explicit_city', 'explicit_province', 'local_only', 'change_region',
    'clear_region', 'resource_or_title_only', 'creator_origin',
    'resource_name_or_example', 'pasted_post', 'ambiguous_place',
    'later_resource_adoption', 'independent_new_round',
})
_REF = re.compile(r'^resource-ref:[0-9a-f]{32}$')


def load_cases(path: str | Path | None = None) -> list[dict]:
    """Load and validate JSONL without executing tools or reading configuration."""
    with Path(path or DEFAULT_CASES_PATH).open(encoding='utf-8') as stream:
        cases = [json.loads(line) for line in stream if line.strip()]
    validate_dataset(cases)
    return cases


def _require_keys(value: object, keys: set[str], context: str) -> dict:
    if not isinstance(value, dict) or not keys <= value.keys():
        raise ValueError(f'{context}: missing required fields {sorted(keys)}')
    return value


def _positive_int(value: object, context: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f'{context}: expected positive integer')


def validate_dataset(cases: list[dict]) -> None:
    """Check fixture integrity and complete-conversation dev/holdout grouping.

    Does not validate intent semantics: a quote's existence is not semantic proof.
    Source-adversarial history may include foreign/future messages, which the
    source validator (not this data loader) must reject as evidence.
    """
    if not cases:
        raise ValueError('dataset is empty')
    seen_ids: set[str] = set()
    conversation_splits: dict[str, str] = {}
    turns: set[tuple[str, int]] = set()
    for case in cases:
        _require_keys(case, {
            'case_id', 'conversation_id', 'principal_id', 'synthetic', 'task_round',
            'application_turn', 'current_user', 'history', 'current_draft',
            'resources_ready', 'resource_state', 'read_fixtures', 'expected',
            'category', 'critical_negative', 'split', 'allowed_mutation_tools',
        }, 'case')
        for key in ('case_id', 'conversation_id', 'principal_id', 'category'):
            if not isinstance(case[key], str) or not case[key].strip():
                raise ValueError(f'{key}: expected nonempty string')
        if case['case_id'] in seen_ids:
            raise ValueError('duplicate case_id')
        seen_ids.add(case['case_id'])
        if case['synthetic'] is not True:
            raise ValueError('this dataset requires explicit synthetic=true')
        for key in ('task_round', 'application_turn'):
            _positive_int(case[key], key)
        if case['split'] not in {'dev', 'holdout'}:
            raise ValueError('split must be dev or holdout')
        conversation = case['conversation_id']
        previous = conversation_splits.setdefault(conversation, case['split'])
        if previous != case['split']:
            raise ValueError('complete conversation crosses dev/holdout split')
        turn = (conversation, case['application_turn'])
        if turn in turns:
            raise ValueError('duplicate conversation/application_turn')
        turns.add(turn)
        for key in ('resources_ready', 'critical_negative'):
            if type(case[key]) is not bool:
                raise ValueError(f'{key}: expected boolean')
        allowed = case['allowed_mutation_tools']
        if (not isinstance(allowed, list)
                or any(not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', name)
                       for name in allowed)
                or len(allowed) != len(set(allowed))):
            raise ValueError('allowed_mutation_tools: expected unique tool names')
        if not isinstance(case['history'], list):
            raise ValueError('history: expected list')
        for message in [*case['history'], case['current_user']]:
            _require_keys(message, {'message_id', 'role', 'content', 'principal_id',
                                   'conversation_id', 'task_round', 'application_turn', 'sequence'}, 'message')
            if message['role'] not in {'user', 'assistant', 'tool'}:
                raise ValueError('message: invalid role')
            for key in ('message_id', 'content', 'principal_id', 'conversation_id'):
                if not isinstance(message[key], str) or not message[key]:
                    raise ValueError(f'message.{key}: expected nonempty string')
            for key in ('task_round', 'application_turn', 'sequence'):
                _positive_int(message[key], f'message.{key}')
        current = case['current_user']
        if current['role'] != 'user' or any(current[key] != case[key] for key in
                ('principal_id', 'conversation_id', 'task_round', 'application_turn')):
            raise ValueError('current_user must match case identity and turn')
        draft = case['current_draft']
        if draft is not None:
            _require_keys(draft, {'draft_id', 'revision', 'scope', 'ruleset_ref', 'lexicon_ref'}, 'draft')
            if not isinstance(draft['draft_id'], str) or not draft['draft_id']:
                raise ValueError('draft_id: expected nonempty string')
            _positive_int(draft['revision'], 'draft.revision')
            for key in ('ruleset_ref', 'lexicon_ref'):
                if not isinstance(draft[key], str) or not _REF.fullmatch(draft[key]):
                    raise ValueError(f'draft.{key}: invalid resource reference')
            if draft['scope'] is not None:
                _require_keys(draft['scope'], {'target_text', 'selection_policy'}, 'draft.scope')
                if not isinstance(draft['scope']['target_text'], str) or not draft['scope']['target_text']:
                    raise ValueError('draft.scope.target_text: expected nonempty string')
                if draft['scope']['selection_policy'] not in POLICIES:
                    raise ValueError('draft.scope.selection_policy: invalid policy')
        _require_keys(case['resource_state'], {'kind', 'shown', 'adoption'}, 'resource_state')
        if not isinstance(case['read_fixtures'], dict) or any(
                not isinstance(value, dict) or value.get('simulated') is not True
                for value in case['read_fixtures'].values()):
            raise ValueError('all read fixtures must be dictionaries marked simulated=true')
        expected = _require_keys(case['expected'], {'label', 'target_text', 'selection_policy',
                                  'source_quote', 'source_evidence_span', 'reason',
                                  'acceptable_response_patterns'}, 'expected')
        label = expected['label']
        if label not in LABELS:
            raise ValueError('expected.label: invalid label')
        if not isinstance(expected['reason'], str) or not expected['reason']:
            raise ValueError('expected.reason: expected nonempty string')
        patterns = expected['acceptable_response_patterns']
        if not isinstance(patterns, list) or any(not isinstance(p, str) or not p for p in patterns):
            raise ValueError('expected.acceptable_response_patterns: invalid patterns')
        for pattern in patterns:
            re.compile(pattern)
        if label == 'set':
            if not isinstance(expected['target_text'], str) or not expected['target_text']:
                raise ValueError('set requires target_text')
            if expected['selection_policy'] not in POLICIES:
                raise ValueError('set requires valid selection_policy')
        elif expected['target_text'] is not None or expected['selection_policy'] is not None:
            raise ValueError('non-set labels must not carry target/policy expectations')
        if label in {'set', 'clear'}:
            if not isinstance(expected['source_quote'], str) or not expected['source_quote']:
                raise ValueError('set/clear requires source_quote')
            span = expected['source_evidence_span']
            if not isinstance(span, str) or not span.strip() or span not in expected['source_quote']:
                raise ValueError('set/clear requires source_evidence_span within source_quote')
        elif expected['source_quote'] is not None or expected['source_evidence_span'] is not None:
            raise ValueError('non-mutation label must not carry source_quote/evidence span')
        if label in {'clarify', 'defer', 'no_task'} and not patterns:
            raise ValueError('response labels require explicit acceptable patterns')
