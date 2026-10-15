"""Offline paired diagnostic scores; intent and draft landing are separate axes.

Full-arm speech needs separate review. Absence of structured evidence never
licenses a claim about the model's internal understanding.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import re

from .contract import normalize_target, validate_source
from .scoring import CONFIGURE, FORBIDDEN_EXECUTION, COMPLETED_TASK_PATTERN, score_case

OBSERVER = 'record_geographic_intent'
FIELDS = {'set': {'action', 'target_text', 'selection_policy', 'source_quote'},
          'clear': {'action', 'source_quote'}, 'keep': {'action'},
          'no_task': {'action'}, 'clarify': {'action', 'question'}}


def _arguments(call):
    value = call.get('arguments')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) else None


def _source(case, change, label):
    quote = change.get('source_quote')
    actual = validate_source(case, quote)
    expected_quote = label.get('source_quote')
    span = label.get('source_evidence_span')
    if not span:
        return quote == expected_quote and actual.get('ok') is True
    expected = validate_source(case, expected_quote)
    return (isinstance(quote, str) and isinstance(expected_quote, str)
            and isinstance(span, str) and span in quote and span in expected_quote
            and actual.get('ok') is True and expected.get('ok') is True
            and actual.get('message_id') == expected.get('message_id'))


def _change_score(case, change, label, receipt):
    dimensions = dict.fromkeys(('action', 'target', 'policy', 'source', 'schema', 'receipt'), True)
    if not isinstance(change, dict):
        return {**dict.fromkeys(dimensions, False), 'pass': False,
                'errors': ['missing_or_invalid_record'], 'predicted_action': None}
    action = change.get('action')
    dimensions['action'] = action == label['action']
    allowed = FIELDS.get(action, set())
    dimensions['schema'] = bool(allowed) and set(change) == allowed
    if action == 'set':
        target = change.get('target_text')
        expected_target = label.get('target_text')
        actual_region = normalize_target(target)
        expected_region = normalize_target(expected_target)
        dimensions['target'] = (target in [expected_target, *label.get('target_aliases', [])]
                                or actual_region.get('ok') is True
                                and expected_region.get('ok') is True
                                and actual_region['name'] == expected_region['name'])
        dimensions['policy'] = change.get('selection_policy') == label.get('selection_policy')
        dimensions['source'] = _source(case, change, label)
        dimensions['schema'] &= all(isinstance(change.get(k), str) and bool(change[k].strip())
                                    for k in ('target_text', 'source_quote', 'selection_policy'))
    elif action == 'clear':
        dimensions['target'] = 'target_text' not in change
        dimensions['policy'] = 'selection_policy' not in change
        dimensions['source'] = _source(case, change, label)
    elif action == 'clarify':
        dimensions['schema'] &= isinstance(change.get('question'), str) and bool(change['question'].strip())
    dimensions['receipt'] = (isinstance(receipt, dict) and receipt.get('ok') is True
                             and receipt.get('simulated') is True)
    return {**dimensions, 'pass': all(dimensions.values()),
            'errors': [key + '_failed' for key, value in dimensions.items() if not value],
            'predicted_action': action}


def score_contrast(case: dict, trace: dict, label: dict) -> dict:
    """No gold data enters requests; callers supply labels only for offline scoring."""
    arm = trace.get('arm', 'full')
    if arm not in {'full', 'bridge', 'simple'}:
        raise ValueError('unknown contrast arm')
    calls = trace.get('calls', [])
    observers = [call for call in calls if call.get('name') == OBSERVER]
    business_trace = deepcopy(trace)
    business_trace['calls'] = [call for call in business_trace.get('calls', [])
                               if call.get('name') != OBSERVER]
    changes = [(call, arguments['relevance_scope_change']) for call in business_trace['calls']
               if call.get('name') == CONFIGURE and (arguments := _arguments(call)) is not None
               and 'relevance_scope_change' in arguments]
    record = None
    receipt = None
    if arm == 'full' and len(changes) == 1:
        record, receipt = changes[0][1], changes[0][0].get('receipt')
    elif arm != 'full' and len(observers) == 1:
        record, receipt = _arguments(observers[0]), observers[0].get('receipt')
    intent = _change_score(case, record, label, receipt)
    if arm != 'full' and record is not None:
        from .contrast_intent import observer_tool
        from .sandbox import validate_json_schema
        try:
            validate_json_schema(record, observer_tool()['parameters'])
        except (ValueError, TypeError, KeyError):
            intent['schema'] = intent['pass'] = False
            intent['errors'].append('observer_schema_invalid')
    intent['status'] = ('not_observable' if arm == 'full' and not changes else
                        'missing_record' if arm != 'full' and not observers else 'observed')
    intent['record_count'] = len(observers)
    if arm != 'full' and len(observers) != 1:
        intent['pass'] = False
        intent['errors'].append('expected_one_intent_record')
    if trace.get('case_id') != case['case_id']:
        intent['pass'] = False
        intent['errors'].append('case_id_mismatch')
    complete = None if arm == 'simple' else score_case(case, trace if arm == 'full' else business_trace)
    boundary_errors = []
    for call in calls:
        name = call.get('name')
        if name in FORBIDDEN_EXECUTION:
            boundary_errors.append('execution_tool_attempt:' + name)
        if arm == 'simple' and name != OBSERVER:
            boundary_errors.append('simple_non_observer_tool:' + str(name))
        if arm == 'full' and name == OBSERVER:
            boundary_errors.append('full_unavailable_observer')
    if arm != 'full' and any(not _arguments(call) for call in observers):
        boundary_errors.append('invalid_observer_arguments')
    if arm != 'full' and observers and not intent['schema']:
        boundary_errors.append('invalid_observer_schema')
    text_claims = [index for index, message in enumerate(trace.get('messages', []))
                   if message.get('role', 'assistant') == 'assistant'
                   and isinstance(message.get('content'), str)
                   and re.search(COMPLETED_TASK_PATTERN, message['content'])]
    landing = None
    if arm != 'simple':
        old_label = case['expected']['label']
        eligible = old_label in {'set', 'clear'}
        if eligible:
            expected = {**case['expected'], 'action': old_label}
            change = changes[0][1] if len(changes) == 1 else None
            change_receipt = changes[0][0].get('receipt') if len(changes) == 1 else None
            landing = _change_score(case, change, expected, change_receipt)
            snapshot = change_receipt.get('data') if isinstance(change_receipt, dict) else None
            if isinstance(snapshot, dict):
                wanted_scope = None if old_label == 'clear' else change
                landing['snapshot'] = 'scope' in snapshot and snapshot['scope'] == wanted_scope
                if not landing['snapshot']:
                    landing['pass'] = False
                    landing['errors'].append('receipt_scope_snapshot_mismatch')
        else:
            preservation = (trace.get('termination') != 'missing' and not changes
                            and not complete['observed']['scope_changes']
                            and not any('scope_recreation' in e for e in complete['errors']))
            landing = {'pass': preservation, 'errors': [] if preservation else ['scope_not_preserved']}
        landing.update(eligible_set=old_label == 'set', eligible_clear=old_label == 'clear',
                       scope_preserved=not changes if not eligible else None,
                       observed_scope_changes=[change for _, change in changes])
    return {'case_id': case['case_id'], 'arm': arm, 'label': label['action'],
            'intent': intent, 'landing': landing, 'original_complete': complete,
            'boundary': {'pass': not boundary_errors and not text_claims,
                         'errors': boundary_errors,
                         'completion_claim_heuristic': {'flagged': bool(text_claims),
                            'message_indexes': text_claims,
                            'requires_offline_review': bool(text_claims)},
                         'original_boundary': complete['boundary'] if complete else None},
            'recorded_but_missed_landing': bool(arm == 'bridge' and intent['pass']
                and label['action'] == 'set' and landing['eligible_set'] and not landing['pass'])}


def _ratio(n, d):
    return {'numerator': n, 'denominator': d, 'value': n / d if d else None}


def aggregate_contrast(cases: list[dict], traces: list[dict], labels: dict, *, arm=None) -> dict:
    """One arm only; missing runs remain in the fixed case denominator."""
    ids = {case['case_id'] for case in cases}
    if len(ids) != len(cases) or set(labels) != ids:
        raise ValueError('case/label identities must be unique and match exactly')
    by_id = {}
    for trace in traces:
        if trace['case_id'] not in ids or trace['case_id'] in by_id:
            raise ValueError('unknown or duplicate contrast trace')
        by_id[trace['case_id']] = trace
    arms = {trace.get('arm', 'full') for trace in traces}
    if len(arms) > 1:
        raise ValueError('aggregate only one arm')
    if arm is not None and (arm not in {'full', 'bridge', 'simple'} or arms and arms != {arm}):
        raise ValueError('aggregate arm does not match traces')
    arm = arm if arm is not None else next(iter(arms), 'full')
    rows = []
    all_traces = []
    missing = []
    for case in cases:
        identity = case['case_id']
        if identity not in by_id:
            missing.append(identity)
        trace = by_id.get(identity, {'case_id': identity, 'arm': arm, 'mode': 'real',
                                    'termination': 'missing', 'calls': [], 'messages': []})
        all_traces.append(trace)
        rows.append(score_contrast(case, trace, labels[identity]))
    intents = [row['intent'] for row in rows]
    expected_sets = sum(label['action'] == 'set' for label in labels.values())
    predicted_sets = sum(row['intent']['predicted_action'] == 'set' for row in rows)
    correct_sets = sum(row['label'] == 'set' and row['intent']['pass'] for row in rows)
    usage = Counter()
    for trace in all_traces:
        for key, value in (trace.get('usage') or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[key] += value
    landings = [row['landing'] for row in rows if row['landing'] is not None]
    draft_expected = sum(value['eligible_set'] for value in landings)
    draft_correct = sum(value['eligible_set'] and value['pass'] for value in landings)
    draft_predictions = sum(any(isinstance(change, dict) and change.get('action') == 'set'
                                for change in value['observed_scope_changes']) for value in landings)
    return {'arm': arm, 'evaluated': len(cases), 'rows': rows, 'missing_case_ids': missing,
            'intent': {'passed': sum(value['pass'] for value in intents),
                       'accuracy': _ratio(sum(value['pass'] for value in intents), len(cases)),
                       'set_precision': _ratio(correct_sets, predicted_sets),
                       'set_recall': _ratio(correct_sets, expected_sets),
                       'not_observable': sum(value['status'] == 'not_observable' for value in intents),
                       'false_sets': sum(row['label'] != 'set' and row['intent']['predicted_action'] == 'set' for row in rows),
                       'false_clears': sum(row['label'] != 'clear' and row['intent']['predicted_action'] == 'clear' for row in rows),
                       'source_errors': sum(not value['source'] for value in intents),
                       'policy_errors': sum(not value['policy'] for value in intents)},
            'landing': None if arm == 'simple' else {
                'set_precision': _ratio(draft_correct, draft_predictions),
                'set_recall': _ratio(draft_correct, draft_expected),
                'complete_passed': sum(row['original_complete']['pass'] for row in rows),
                'recorded_but_missed_landing': sum(row['recorded_but_missed_landing'] for row in rows)},
            'boundary': {'failures': sum(not row['boundary']['pass'] for row in rows),
                         'errors': dict(Counter(error for row in rows for error in row['boundary']['errors'])),
                         'completion_claim_heuristic_flags': sum(row['boundary']['completion_claim_heuristic']['flagged'] for row in rows),
                         'original_complete_failures': sum(not row['original_complete']['boundary']
                             for row in rows if row['original_complete'] is not None)},
            'requests': sum(len(trace.get('requests', [])) for trace in all_traces),
            'responses': sum(len(trace.get('responses', [])) for trace in all_traces),
            'tool_requests': sum(len(trace.get('calls', [])) for trace in all_traces),
            'termination_counts': dict(Counter(trace.get('termination', 'missing') for trace in all_traces)),
            'usage': dict(usage), 'usage_available': sum(bool(trace.get('usage')) for trace in all_traces),
            'elapsed_seconds': sum(trace.get('elapsed_seconds', 0) for trace in all_traces),
            'cost': 'unknown'}
