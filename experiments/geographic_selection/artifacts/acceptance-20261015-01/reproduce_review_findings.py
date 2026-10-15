"""Acceptance counterexamples; no source changes, real calls, or production DBs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from experiments.geographic_selection.offline_guard import OfflineGuard
from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
from experiments.geographic_selection.bridge import validate_geo_record
from audit_agent.scoped_selection import eligible_tier
from audit_agent.scoped_selection_store import Store
from audit_agent.scoped_execution import ScopedExecution


def main():
    with tempfile.TemporaryDirectory(prefix='p4-acceptance-counterexample-') as directory:
        root = Path(directory)
        guard = OfflineGuard(root)
        guard.pytest_sessionstart(None)
        output = {}
        try:
            plan = fixture_plan(queries=('A',), task_limit=1)
            ordinary = fixture_occurrence(plan, 'A', 'x')
            official = fixture_occurrence(plan, 'A', 'x', enterprise_verify_reason='公安官方')
            swapped = deepcopy(official)
            swapped['risk'] = deepcopy(ordinary['risk'])
            validate_geo_record(plan, swapped['candidate'], swapped['geo'])

            def runner(row, name, rejected=False):
                return ScopedExecution(
                    Store(root / name), plan,
                    capture=lambda p, q: {'account_id': 'capture', 'occurrences': [row]},
                    risk=lambda p, q, o: o['risk'], geo=lambda p, q, o: o['geo'],
                    validate_geo=validate_geo_record,
                    detail=lambda o, a, **k: deepcopy(o['candidate']),
                    review=lambda p, o, c: o['geo'],
                    deliver=lambda c, **k: dict(k['binding'], idempotency_key=k['idempotency_key'],
                                               **({'status': 'rejected'} if rejected else {})),
                    account=lambda q: 'detail', rotate=lambda *a: None, stop=lambda: False)

            mixed = runner(swapped, 'mixed.sqlite')
            output['risk_binding'] = {
                'ordinary_band': ordinary['risk']['score']['band'],
                'current_official_band': official['risk']['score']['band'],
                'swapped_tier': eligible_tier(plan, swapped),
                'report': mixed.run(),
            }
            rejected = runner(ordinary, 'rejected.sqlite', rejected=True)
            output['rejected_delivery'] = {
                'report': rejected.run(),
                'saved_statuses': [d.get('status') for d in rejected.snapshot()['deliveries'].values()],
            }
        finally:
            class Session:
                exitstatus = 0
            session = Session()
            guard.pytest_sessionfinish(session, 0)
        output['offline_guard'] = guard.metrics
        assert not any(guard.metrics.values()), output
        assert output['risk_binding']['current_official_band'] == 'discard', output
        assert output['risk_binding']['report']['handed_off'] == 1, output
        assert output['rejected_delivery']['report']['handed_off'] == 1, output
        target = Path(__file__).with_suffix('.json')
        target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
