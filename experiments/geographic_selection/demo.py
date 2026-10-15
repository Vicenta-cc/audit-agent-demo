"""Reproducible three-query manual fixture, with a compact saved execution trace."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from audit_agent.scoped_execution import ScopedExecution
from audit_agent.scoped_selection_store import Store
from .bridge import validate_geo_record
from .fixtures import fixture_plan, fixture_occurrence
from .offline_guard import OfflineGuard


def main():
    trace, sink = [], {}
    with tempfile.TemporaryDirectory(prefix='p4-offline-demo-') as directory:
        guard = OfflineGuard(directory)
        guard.pytest_sessionstart(None)
        try:
            plan = fixture_plan(queries=('A', 'B', 'C'), task_limit=2)
            rows = {}
            for query, text, place, band in [('A', '新疆欠薪', '新疆', 'strong'),
                                             ('B', '克拉玛依欠薪', '克拉玛依市', 'weak'),
                                             ('C', '克拉玛依欠薪', '克拉玛依市', 'strong')]:
                rows[query] = [fixture_occurrence(plan, query, query.lower(), desc=text,
                    raw_geo={'status': 'located', 'locations': [{'field': 'desc', 'place': place}]},
                    risk_response={'suspicion': band, 'reason': '人工固定响应'})]
            store = Store(Path(directory)/'selection.sqlite')
            def capture(plan, query):
                trace.append({'stage': 'capture', 'query_id': query['query_id']})
                return {'account_id': 'capture-fixture', 'occurrences': deepcopy(rows[query['query_id']])}
            def detail(occurrence, attempt, *, account_id, content_callback):
                trace.append({'stage': 'detail', 'query_id': occurrence['query_id'],
                              'content_key': occurrence['content_key'], 'tier': attempt['tier']})
                content_callback({'candidate_only': True})
                return deepcopy(occurrence['candidate'])
            def deliver(content, *, idempotency_key, binding):
                trace.append({'stage': 'deliver', 'query_id': binding['query_id'],
                              'content_key': binding['content_key']})
                sink.setdefault(idempotency_key, deepcopy(content))
                return dict(binding, idempotency_key=idempotency_key, status='accepted')
            execution = ScopedExecution(store, plan, capture=capture,
                risk=lambda plan, query, o: deepcopy(o['risk']), geo=lambda plan, query, o: deepcopy(o['geo']),
                detail=detail, review=lambda plan, o, content: deepcopy(o['geo']), deliver=deliver,
                validate_geo=validate_geo_record, account=lambda query: 'detail-fixture',
                rotate=lambda query, account, error: None, stop=lambda: False)
            result = execution.run()
            assert result['collected'] == result['handed_off'] == 2
            assert [t['query_id'] for t in trace if t['stage'] == 'detail'] == ['B', 'C']
            saved = execution.snapshot()
            evidence = {'validation_origin': 'fixture', 'plan_digest': plan['plan_digest'],
                'input_summary': [{'query_id': q, 'band': o[0]['risk']['score']['band'],
                    'score': o[0]['risk']['score']['score'], 'rank': o[0]['rank'],
                    'geo_tier': o[0]['geo']['result']['geo_tier']} for q, o in rows.items()],
                'trace': trace, 'result': result, 'database_path': str(store.path),
                'database_retained': False, 'event_revisions': [e['revision'] for e in saved['events']],
                'account_mapping': [{'query_id': a['query_id'], 'capture_account': a['capture_account'],
                    'detail_account': a['account_id']} for a in saved['attempts'].values()],
                'guard_metrics': guard.metrics}
        finally:
            session = SimpleNamespace(exitstatus=0)
            guard.pytest_sessionfinish(session, 0)
            if session.exitstatus:
                raise RuntimeError('offline demo attempted a prohibited side effect')
    path = Path(__file__).parent/'artifacts/example.json'
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'example': str(path.resolve()), 'result': result, 'guard_metrics': guard.metrics},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
