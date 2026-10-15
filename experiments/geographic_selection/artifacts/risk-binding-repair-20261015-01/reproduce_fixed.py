"""Fresh guarded risk-binding counterexamples; original acceptance evidence is read-only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

from experiments.geographic_selection.offline_guard import OfflineGuard
from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
from experiments.geographic_selection.bridge import validate_geo_record
from audit_agent.scoped_selection import eligible_tier
from audit_agent.scoped_selection_store import Store
from audit_agent.scoped_execution import ScopedExecution


def main():
    output = {'command': [sys.executable, *sys.argv], 'cases': {}}
    with tempfile.TemporaryDirectory(prefix='p4-risk-binding-repair-') as directory:
        root = Path(directory)
        guard = OfflineGuard(root)
        guard.pytest_sessionstart(None)
        try:
            plan = fixture_plan(queries=('A',), task_limit=1)
            old = fixture_occurrence(plan, 'A', 'x')
            for name, change in (
                ('post', {'desc': '克拉玛依欠薪新原文'}),
                ('comments', {'comments': [{'comment_id': 'c', 'content': '新评论'}]}),
                ('identity', {'enterprise_verify_reason': '公安官方'}),
            ):
                current = fixture_occurrence(plan, 'A', 'x', **change)
                mixed = deepcopy(current)
                mixed['risk'] = deepcopy(old['risk'])
                validate_geo_record(plan, mixed['candidate'], mixed['geo'])
                try:
                    eligible_tier(plan, mixed)
                except ValueError as exc:
                    selector_error = str(exc)
                else:
                    raise AssertionError('stale risk reached selector')
                calls = []
                runner = ScopedExecution(Store(root/(name+'.sqlite')), plan,
                    capture=lambda p, q: {'account_id': 'capture', 'occurrences': [mixed]},
                    risk=lambda p, q, o: o['risk'], geo=lambda p, q, o: o['geo'],
                    validate_geo=validate_geo_record,
                    detail=lambda *a, **kw: calls.append('detail'),
                    review=lambda p, o, c: o['geo'],
                    deliver=lambda *a, **kw: calls.append('deliver'),
                    account=lambda q: 'detail', rotate=lambda *a: None, stop=lambda: False)
                try:
                    runner.run()
                except ValueError as exc:
                    execution_error = str(exc)
                else:
                    raise AssertionError('stale risk reached collection')
                saved = runner.snapshot()
                case = {'same_occurrence_id': old['occurrence_id'] == current['occurrence_id'],
                    'old_band': old['risk']['score']['band'], 'current_band': current['risk']['score']['band'],
                    'selector_error': selector_error, 'execution_error': execution_error,
                    'query_status': saved['queries']['A']['status'],
                    'persisted_evaluations': saved['queries']['A']['evaluations'],
                    'reserved': saved['state']['total_reserved'], 'collected': saved['state']['total_success'],
                    'handed_off': len(saved['deliveries']), 'detail_or_delivery_calls': calls}
                assert selector_error == execution_error == 'risk_capture_evidence_mismatch'
                assert not case['persisted_evaluations'] and not calls
                assert case['reserved'] == case['collected'] == case['handed_off'] == 0
                if name == 'identity':
                    assert case['old_band'] == 'weak' and case['current_band'] == 'discard'
                output['cases'][name] = case
        finally:
            class Session:
                exitstatus = 0
            session = Session()
            guard.pytest_sessionfinish(session, 0)
        output['guard_metrics'] = guard.metrics
        assert not any(guard.metrics.values())
    Path(__file__).with_suffix('.json').write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
