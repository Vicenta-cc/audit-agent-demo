"""Local bridge candidates: prompt-only guidance and read-only progress diagnostics.

Reuse the frozen B loop, full tools, schema and simulated dispatcher. No forced
tool choice, parameter repair, automatic scope injection or new provider IO.
Real providers require an explicit opt-in and authorization label. Import and
scripted runs perform no provider IO; semantic effects require real evaluation.
"""
from copy import deepcopy
import json
from pathlib import Path

from .contrast_intent import CONTEXT_MARKER, OBSERVER_NAME, request_for_arm
from .contrast_runner import run_arm

VERSION = 'geo-intent-p0-bridge-minimal-v1'
GUIDANCE = Path(__file__).with_name('bridge_minimal_guidance.json')
FACETS = ('confirmation', 'state', 'recording')
VARIANTS = ('baseline', *FACETS, 'combined')


def _guided_request(request, variant):
    if variant not in VARIANTS:
        raise ValueError('unknown minimal bridge variant')
    result = deepcopy(request)
    if variant == 'baseline':
        return result
    guidance = json.loads(GUIDANCE.read_text(encoding='utf-8'))
    selected = FACETS if variant == 'combined' else (variant,)
    before, context = result['messages'][0]['content'].rsplit(CONTEXT_MARKER, 1)
    result['messages'][0]['content'] = (
        before + '\n本轮隔离候选补充指导：\n'
        + '\n'.join(guidance[facet] for facet in selected)
        + '\n' + CONTEXT_MARKER + context
    )
    return result


def build_request(case, variant='combined'):
    return _guided_request(request_for_arm(case, 'bridge'), variant)


def diagnose_progress(case, trace):
    """Observe actual tool receipts, without expected labels or semantic scoring.

    An observer receipt proves capture only, not correct intent. Configuration
    proves a simulated state change only, not correct semantics or real writes.
    Original termination and scorer results stay unchanged.
    """
    calls = trace.get('calls', [])
    records = [c for c in calls if c['name'] == OBSERVER_NAME and c['receipt'].get('ok')]
    configurations = [c for c in calls if c['name'] == 'configure_investigation_draft'
                      and c['receipt'].get('ok') and c['receipt'].get('code') == 'DRAFT_SIMULATED']
    initial = (case.get('current_draft') or {}).get('scope')
    scope = configurations[-1]['receipt']['data'].get('scope') if configurations else initial
    record_status = 'missing' if not records else 'captured' if len(records) == 1 else 'multiple'
    handoff = 'not_observable'
    clear_without_scope = False
    if len(records) == 1:
        intent = records[0]['arguments']
        action = intent['action']
        if action == 'keep':
            handoff = 'preserved' if scope == initial else 'mismatch'
        elif action in ('set', 'clear'):
            clear_without_scope = action == 'clear' and initial is None
            explicit_match = any(c['arguments'].get('relevance_scope_change') == intent
                                 for c in configurations)
            explicit_changes = any('relevance_scope_change' in c['arguments']
                                   for c in configurations)
            matches = scope == intent if action == 'set' else scope is None
            handoff = ('applied' if explicit_match and matches else
                       'mismatch' if explicit_changes or (configurations and not matches)
                       else 'not_applied')
        else:
            handoff = 'unexpected_configuration' if configurations else 'not_applicable'
    return {
        'record_status': record_status,
        'successful_record_count': len(records),
        'draft_configured': bool(configurations),
        'successful_configuration_count': len(configurations),
        'handoff_status': handoff,
        'clear_without_existing_scope': clear_without_scope,
        'final_reply_complete': trace.get('termination') == 'final',
        'pending_final_reply': bool(configurations) and trace.get('termination') == 'loop_limit',
        'original_termination': trace.get('termination'),
        'semantic_correctness': 'not_assessed_by_progress_diagnostic',
    }


def run_candidate(case, provider, variant='combined', *, enable_real=False,
                  authorization_label=''):
    """All decisions come from provider replies; real IO is explicit opt-in."""
    if variant not in VARIANTS:
        raise ValueError('unknown minimal bridge variant')
    if provider.mode not in ('scripted', 'real'):
        raise ValueError('unknown provider mode')
    if provider.mode == 'real' and not (enable_real and authorization_label.strip()):
        raise PermissionError('real evaluation requires enable flag and authorization label')

    class GuidedProvider:
        mode = provider.mode

        def __init__(self):
            self.requests = []

        def complete(self, request):
            actual = _guided_request(request, variant)
            self.requests.append(deepcopy(actual))
            return provider.complete(actual)

    guided = GuidedProvider()
    trace = run_arm(case, guided, 'bridge')
    # run_arm captures its original inputs; persist exactly what the delegate saw.
    trace['requests'] = guided.requests
    trace['candidate_version'] = VERSION
    trace['candidate_variant'] = variant
    trace['progress'] = diagnose_progress(case, trace)
    return trace
