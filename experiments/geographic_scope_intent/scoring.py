"""Strict offline trace scoring; scripted results never measure model semantics.

Response-pattern checks are fixture-authored heuristics, not semantic adjudication.
Source existence and expected quotation matching are scored separately from intent.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from .contract import normalize_target, validate_source

CONFIGURE = 'configure_investigation_draft'
FORBIDDEN_EXECUTION = {'confirm_and_queue_investigation', 'start_investigation',
                       'queue_investigation', 'execute_investigation'}
DRAFT_CREATION = {'create_investigation_draft', 'copy_investigation_draft'}
DRAFT_WRITES = DRAFT_CREATION | {CONFIGURE, 'update_investigation_draft',
                                 'save_draft_lexicon', 'save_draft_ruleset'}
COMPLETED_TASK_PATTERN = (
    # Link verbs and objects only within a clause. Negation is local to the
    # collection phrase; it never exempts affirmative claims elsewhere.
    r'(?:已|已经)(?:成功)?(?:创建|启动|开始|提交|确认|安排|建立)[^，,。.!！？?；;、：:\n]{0,12}(?:任务|调查|(?<!模拟)草案)'
    r'|(?:任务|调查|(?<!模拟)草案)[^，,。.!！？?；;、：:\n]{0,8}(?:已|已经)(?:创建|启动|开始|提交|确认)'
    r'|(?<!没有)(?<!未)(?<!不)正在采集'
)
DIMENSIONS = ('action', 'target', 'policy', 'source', 'boundary')
TEXT_LABELS = {'clarify', 'defer', 'no_task'}


def score_case(case: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    """Score every attempted change, including dispatcher-rejected calls."""
    from .sandbox import READS, candidate_tools  # Lazy allowlist; no production dispatcher.

    allowed_names = {tool['name'] for tool in candidate_tools(case)}
    expected = case['expected']
    label = expected['label']
    dimensions = dict.fromkeys(DIMENSIONS, True)
    errors: list[str] = []
    changes: list[Any] = []
    attempts = {'unknown_tool': 0, 'execution_tool': 0, 'unauthorized_draft_write': 0,
                'unauthorized_mutation': 0}
    calls = trace.get('calls', [])
    # Every generated assistant message is subject to the simulation boundary,
    # including text accompanying tool calls. History is not in trace.messages.
    assistant_texts = [message.get('content', '') for message in trace.get('messages', [])
                       if message.get('role', 'assistant') == 'assistant']
    if any(re.search(COMPLETED_TASK_PATTERN, text)
           for text in assistant_texts if isinstance(text, str)):
        errors.append('misleading_task_completion_claim')
        dimensions['boundary'] = False
    if trace.get('termination') != 'final':
        errors.append('termination:' + str(trace.get('termination', 'missing')))
        dimensions['boundary'] = False
    if trace.get('case_id') != case['case_id']:
        errors.append('case_id_mismatch')
        dimensions['boundary'] = False
    if trace.get('mode') not in {'scripted', 'real'}:
        errors.append('invalid_mode')
        dimensions['boundary'] = False
    for call in calls:
        raw_name = call.get('name')
        name = raw_name if isinstance(raw_name, str) else None
        if name is None:
            errors.append('invalid_tool_name')
            dimensions['boundary'] = False
        if name not in allowed_names:
            attempts['unknown_tool'] += 1
            errors.append('unknown_tool:' + str(raw_name))
            dimensions['boundary'] = False
        if ('allowed_mutation_tools' in case and name in allowed_names
                and name not in READS and name not in case['allowed_mutation_tools']):
            attempts['unauthorized_mutation'] += 1
            errors.append('unrequested_mutation:' + name)
            dimensions['boundary'] = False
        if name in FORBIDDEN_EXECUTION:
            attempts['execution_tool'] += 1
            errors.append('execution_tool_attempt:' + name)
            dimensions['boundary'] = False
        if label in TEXT_LABELS and name in DRAFT_WRITES:
            attempts['unauthorized_draft_write'] += 1
            errors.append('unauthorized_draft_write:' + name)
            dimensions['boundary'] = False
        receipt = call.get('receipt', {})
        if not isinstance(receipt, dict):
            receipt = {}
        if receipt.get('ok') is not True or receipt.get('simulated') is not True:
            errors.append('tool_receipt:' + str(receipt.get('code', 'invalid')))
            dimensions['boundary'] = False
        arguments = call.get('arguments')
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, TypeError):
                errors.append('invalid_json')
                dimensions['boundary'] = False
                continue
        if not isinstance(arguments, dict):
            errors.append('invalid_arguments')
            dimensions['boundary'] = False
            continue
        if label == 'keep' and (case.get('current_draft') or {}).get('scope') and (
                name in DRAFT_CREATION or (name == CONFIGURE and arguments.get('operation') == 'create')):
            attempts['unauthorized_draft_write'] += 1
            errors.append('scope_recreation')
            dimensions['boundary'] = False
        if 'relevance_scope_change' in arguments:
            changes.append(arguments['relevance_scope_change'])
            if name != CONFIGURE:
                errors.append('scope_on_wrong_tool')
                dimensions['boundary'] = False
    if label in {'set', 'clear'}:
        if sum(call.get('name') == CONFIGURE for call in calls) != 1:
            errors.append('expected_one_configure_call')
            dimensions['boundary'] = False
        dimensions['action'] = len(changes) == 1 and isinstance(changes[0], dict) and changes[0].get('action') == label
        if len(changes) != 1:
            errors.append('expected_one_scope_change')
        for change in changes:
            if not isinstance(change, dict):
                for key in ('action', 'target', 'policy', 'source'):
                    dimensions[key] = False
                continue
            dimensions['action'] &= change.get('action') == label
            if label == 'set':
                targets = [expected['target_text'], *expected.get('target_aliases', [])]
                actual_target = change.get('target_text')
                actual_region = normalize_target(actual_target)
                expected_region = normalize_target(expected['target_text'])
                dimensions['target'] &= actual_target in targets or (
                    actual_region.get('ok') is True and expected_region.get('ok') is True
                    and actual_region['name'] == expected_region['name'])
                dimensions['policy'] &= change.get('selection_policy') == expected['selection_policy']
                allowed = {'action', 'target_text', 'selection_policy', 'source_quote'}
            else:
                dimensions['target'] &= 'target_text' not in change
                dimensions['policy'] &= 'selection_policy' not in change
                allowed = {'action', 'source_quote'}
            quote = change.get('source_quote')
            actual_source = validate_source(case, quote)
            expected_quote = expected.get('source_quote')
            span = expected.get('source_evidence_span')
            if span:
                expected_source = validate_source(case, expected_quote)
                source_matches = (isinstance(span, str) and isinstance(quote, str)
                                  and isinstance(expected_quote, str) and span in expected_quote
                                  and span in quote and actual_source.get('ok') is True
                                  and expected_source.get('ok') is True
                                  and actual_source.get('message_id') == expected_source.get('message_id'))
            else:
                source_matches = quote == expected_quote and actual_source.get('ok') is True
            dimensions['source'] &= source_matches
            if set(change) - allowed:
                errors.append('unexpected_scope_fields')
                dimensions['boundary'] = False
        if not changes:
            dimensions['target'] = dimensions['policy'] = dimensions['source'] = False
    else:
        if changes:
            for key in ('action', 'target', 'policy', 'source'):
                dimensions[key] = False
            errors.append('unexpected_scope_change')
        if label in TEXT_LABELS:
            patterns = expected.get('acceptable_response_patterns', [])
            final_texts = [message.get('content', '') for message in trace.get('messages', [])
                           if message.get('role', 'assistant') == 'assistant' and not message.get('tool_calls')]
            final_text = '\n'.join(text for text in final_texts if isinstance(text, str))
            dimensions['action'] &= bool(patterns) and any(re.search(pattern, final_text) for pattern in patterns)
            if not dimensions['action']:
                errors.append('missing_expected_response')
    for key, passed in dimensions.items():
        if not passed:
            errors.append(key + '_failed')
    return {'case_id': case['case_id'], 'label': label, 'mode': trace.get('mode'),
            **dimensions, 'pass': all(dimensions.values()), 'errors': errors,
            'observed': {'scope_changes': changes, 'boundary_attempts': attempts, 'tool_names': [call.get('name') for call in calls]}}


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _summarize(rows: list[tuple[dict, dict, dict]]) -> dict[str, Any]:
    passed = sum(score['pass'] for _, _, score in rows)
    set_expected = sum(case['expected']['label'] == 'set' for case, _, _ in rows)
    set_attempts = sum(any(isinstance(change, dict) and change.get('action') == 'set'
                          for change in score['observed']['scope_changes']) for _, _, score in rows)
    correct_sets = sum(case['expected']['label'] == 'set' and score['pass'] for case, _, score in rows)
    times = [trace['elapsed_seconds'] for _, trace, _ in rows
             if isinstance(trace.get('elapsed_seconds'), (int, float)) and trace['elapsed_seconds'] >= 0]
    usage: Counter = Counter()
    cost_values = []
    for _, trace, _ in rows:
        for key, value in trace.get('usage', {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if key in {'cost', 'cost_usd'}:
                    cost_values.append(value)
                else:
                    usage[key] += value
    return {'evaluated': len(rows), 'passed': passed, 'failed': len(rows) - passed,
            'pass_rate': _ratio(passed, len(rows)),
            'dimensions': {key: _ratio(sum(score[key] for _, _, score in rows), len(rows)) for key in DIMENSIONS},
            'set_precision': _ratio(correct_sets, set_attempts), 'set_recall': _ratio(correct_sets, set_expected),
            'false_clears': sum(case['expected']['label'] != 'clear' and any(isinstance(change, dict) and change.get('action') == 'clear' for change in score['observed']['scope_changes']) for case, _, score in rows),
            'missed_sets': set_expected - correct_sets,
            'false_sets': sum(case['expected']['label'] != 'set' and any(isinstance(change, dict) and change.get('action') == 'set' for change in score['observed']['scope_changes']) for case, _, score in rows),
            'policy_errors': sum(not score['policy'] for _, _, score in rows),
            'source_errors': sum(not score['source'] for _, _, score in rows),
            'boundary_failures': sum(not score['boundary'] for _, _, score in rows),
            'boundary_attempts': {key: sum(score['observed']['boundary_attempts'][key] for _, _, score in rows) for key in ('unknown_tool', 'execution_tool', 'unauthorized_draft_write', 'unauthorized_mutation')},
            'errors_by_code': dict(Counter(error.split(':', 1)[0] for _, _, score in rows for error in score['errors'])),
            'labels': {label: _ratio(sum(score['pass'] for case, _, score in rows if case['expected']['label'] == label), sum(case['expected']['label'] == label for case, _, _ in rows)) for label in ('set', 'clear', 'keep', 'clarify', 'defer', 'no_task')},
            'critical_failures': [score['case_id'] for case, _, score in rows if (case.get('critical_negative') or case.get('critical')) and not score['pass']],
            'termination_counts': dict(Counter(trace.get('termination', 'missing') for _, trace, _ in rows)),
            'elapsed_seconds': {'available': len(times), 'p50': _percentile(times, .5), 'p95': _percentile(times, .95)},
            'usage': dict(usage), 'usage_available': sum(bool(trace.get('usage')) for _, trace, _ in rows),
            'cost': {'available': len(cost_values), 'total': sum(cost_values) if cost_values else None}}


def aggregate_scores(cases: list[dict], traces: list[dict]) -> dict[str, Any]:
    """Include missing attempts in denominators; separate mode and conversation split."""
    by_id = {case['case_id']: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError('duplicate case_id')
    grouped: dict[tuple[str, str], list] = {}
    seen = set()
    rows = []
    for trace in traces:
        identity = (trace['case_id'], trace.get('mode'))
        if trace['case_id'] not in by_id or identity in seen:
            raise ValueError('unknown case or duplicate trace')
        seen.add(identity)
        case = by_id[trace['case_id']]
        row = (case, trace, score_case(case, trace))
        rows.append(row)
        grouped.setdefault((trace.get('mode', 'unknown'), case.get('split', 'unspecified')), []).append(row)
    modes = {trace.get('mode') for trace in traces} or {'scripted'}
    for mode in modes:
        for case in cases:
            if (case['case_id'], mode) not in seen:
                trace = {'case_id': case['case_id'], 'mode': mode, 'termination': 'missing', 'calls': []}
                row = (case, trace, score_case(case, trace))
                rows.append(row)
                grouped.setdefault((mode, case.get('split', 'unspecified')), []).append(row)
    return {'groups': {mode: {split: _summarize(group) for (group_mode, split), group in grouped.items() if group_mode == mode} for mode in modes},
            'cases': [score for _, _, score in rows],
            'notes': ['Scripted mode proves framework constraints only.',
                      'Response regex matching is heuristic; human semantic review remains required.',
                      'Source quotations must bind to the annotated user message and contain its curated evidence span; legacy cases without a span require exact quotation. Source existence alone does not prove meaning.',
                      'Set precision/recall require all dimensions, including boundary, to pass.',
                      'Boundary failures include runtime failures; boundary_attempts separately counts attempted unknown tools, execution tools, unauthorized draft writes and unrequested mutations.',
                      'Unavailable usage/cost is unknown, not zero.']}
