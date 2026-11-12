from copy import deepcopy

from audit_agent.geographic_query_summary import make_query_summary
from audit_agent.geographic_recovery import snapshot_resume_blocker
from audit_agent.scoped_selection import select_next_batch

from test_geographic_runtime_pipeline import confirmed, runtime
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_scoped_selection import make_plan, occurrence, state, states


class QuerySummaries(dict):
    """Reject accesses to unselected evidence after commit-time validation."""
    def __init__(self, plan, queries):
        super().__init__(queries)
        self.summaries = {key: make_query_summary(plan, key, q) for key, q in queries.items()}

    def summary(self, qid):
        return self.summaries[qid]


def test_summary_selects_same_order_and_never_reads_unselected_evidence():
    plan = make_plan(limit=2)
    first = occurrence(plan, 'A', 'province', 'province_unknown', score=100)
    second = occurrence(plan, 'B', 'local', 'local')
    third = occurrence(plan, 'C', 'ip-only', 'ip_only', band='strong')
    queries = states(plan, first, second, third)
    expected = select_next_batch(plan, queries, state())
    compact = QuerySummaries(plan, queries)
    for hidden in ('A', 'C'):
        # Discard the occurrence list entirely to prove selection only needs it
        # for the winners after tier, ranking and budgets have been applied.
        compact[hidden] = {'status': 'evaluated'}
    assert expected['picks'] == [second]
    assert select_next_batch(plan, compact, state()) == expected


def test_corrupt_evaluated_evidence_preserves_the_original_blocker():
    plan = make_plan()
    candidate = occurrence(plan, 'A', 'x')
    candidate['geo']['request_digest'] = 'mismatch'
    queries = states(plan, candidate)
    expected = select_next_batch(plan, queries, state())
    compact = QuerySummaries(plan, queries)
    assert expected['status'] == 'blocked'
    assert select_next_batch(plan, compact, state()) == expected


def test_pending_capture_keeps_public_row_and_recovery_reason():
    plan = make_plan()
    row = occurrence(plan, 'A', 'partial')
    original = {k: deepcopy(v) for k, v in row.items() if k not in ('geo', 'risk')}
    geo = {'result': {'evaluation_status': 'invalid_response', 'errors': ['missing_location']}}
    q = {'status': 'captured', 'closure': None, 'capture': {'occurrences': [original]},
         'occurrences': [], 'evaluations': {row['occurrence_id']: {'risk': row['risk'], 'geo': geo}}}
    compact = QuerySummaries(plan, {'A': q})
    summary = compact.summary('A')
    assert summary['candidate_count'] == 1
    assert summary['ranked'] == []
    assert summary['selection_rows'] == [{
        'occurrence_id': row['occurrence_id'], 'query_id': 'A', 'content_key': 'partial',
        'rank': row['rank'], 'source_keyword': 'A', 'risk_band': 'weak', 'risk_score': 100,
        'geo_tier': None, 'geo_status': None, 'evaluation_status': 'invalid_response',
        'pending_reason': 'geographic_response_invalid',
    }]
    assert summary['pending_evaluations'][0]['errors'] == ['missing_location']
    compact['A'] = {'status': 'captured', 'closure': None}
    assert snapshot_resume_blocker({'queries': compact, 'attempts': {}}) == 'geographic_evaluation_reconciliation_required'


def test_segmented_worker_completes_and_resumes_without_loading_whole_snapshot(
        confirmed, tmp_path, monkeypatch):
    from backend.audit_agent.geographic_execution_store import GeographicExecutionStore

    def reject_full_load(*args):
        raise AssertionError('unexpected full snapshot load')

    monkeypatch.setattr(GeographicExecutionStore, 'load', reject_full_load)
    runner, events, _ = runtime(confirmed, tmp_path)
    report, output = runner.run()
    assert report['handed_off'] == 1
    assert [item['aweme_id'] for item in output.contents] == ['same']

    before = len(events)
    restarted, _, model = runtime(confirmed, tmp_path, events=events)
    assert restarted.run()[0] == report
    assert len(events) == before and not model.calls
