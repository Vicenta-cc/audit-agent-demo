"""Sequential keyword rounds over the existing immutable capture/evaluation store."""
from .scoped_execution import ScopedExecution
from .scoped_selection import select_next_batch


class MentionExecution(ScopedExecution):
    def _evaluation_plan(self):
        # These internal ports only read the verified frozen plan. Keep its
        # shared catalog out of per-candidate copies; raw risk inputs still use
        # the existing independent occurrence/query copies and risk engine.
        return self.plan

    def _skip(self, query, reason):
        qid = query['query_id']
        status = self.read_state(lambda s: s['queries'][qid]['status'])
        if status == 'evaluated':
            return
        if status != 'pending':
            raise ValueError('cannot skip an already started geographic batch')
        self.store.skip_mention_batch(self.execution_id, qid, reason)

    def _ending(self, query):
        qid = query['query_id']
        def read(s):
            saved = s['queries'][qid]
            if saved['closure'] or any(b['query_id'] == qid for b in s['batches'].values()):
                return 'selected'
            capture = saved.get('capture') or {}
            if capture.get('skipped'):
                return capture['end_reason']
            if capture.get('continuation', {}).get('exhausted') is True:
                return 'source_exhausted'
            if query['round_index'] == query['max_rounds']:
                return 'max_rounds'
            return None
        return self.read_state(read)

    def run(self):
        fatal, auth = self.read_state(lambda s: (
            s.get('fatal_error'), any(q.get('auth_pending') for q in s['queries'].values())))
        if fatal or auth:
            return self.report([fatal['kind'] if fatal else 'awaiting_account_resolution'])
        # Complete durable deliveries first. Never replay an uncertain external capture.
        reasons = self._collect_ready()
        if 'awaiting_reconciliation' in reasons or 'paused' in reasons:
            return self.report(reasons)
        ended = {}
        for query in self.plan['queries']:
            if self.stop():
                return self.report(['paused'])
            logical, qid = query['logical_query_id'], query['query_id']
            budget_done = self.read_state(lambda s: s['state']['total_success'] >= self.plan['task_limit'])
            if budget_done or logical in ended:
                self._skip(query, 'task_limit' if budget_done else ended[logical])
                continue
            status = self._capture_query(query)
            if status:
                return self.report([status])
            # Inspect the same selector that reserves results; eligibility includes
            # existing duplicate/occupied exclusions and the original risk outputs.
            decision = self.read_state(lambda s: select_next_batch(self.plan, s['queries'], s['state']))
            selected = decision['status'] == 'ready' and decision['query_id'] == qid
            if decision['status'] == 'blocked':
                return self.report(decision['reasons'])
            reasons = self._collect_ready()
            if 'awaiting_reconciliation' in reasons or 'paused' in reasons:
                return self.report(reasons)
            ending = 'selected' if selected else self._ending(query)
            if ending:
                ended[logical] = ending
        return self.report(self._collect_ready())

    def _report(self, saved, reasons):
        report = super()._report(saved, reasons)
        grouped = {}
        for query in self.plan['queries']:
            qid, logical = query['query_id'], query['logical_query_id']
            queries = saved['queries']
            if hasattr(queries, 'summary'):
                summary = queries.summary(qid)
            else:
                from .geographic_query_summary import make_query_summary
                summary = make_query_summary(self.plan, qid, queries[qid])
            row = grouped.setdefault(logical, dict(logical_query_id=logical, query=query['query'],
                round_index=1, max_rounds=query['max_rounds'], status='pending', end_reason=None))
            if summary.get('skipped'):
                if row['end_reason'] is None:
                    row['end_reason'] = summary.get('end_reason')
                row['status'] = 'evaluated'
            elif summary['status'] != 'pending':
                row.update(round_index=query['round_index'], status=summary['status'])
                if summary.get('source_exhausted'):
                    row['end_reason'] = 'source_exhausted'
                elif query['round_index'] == query['max_rounds'] and summary['status'] == 'evaluated':
                    row['end_reason'] = 'max_rounds'
            if any(b['query_id'] == qid for b in saved['batches'].values()):
                row['end_reason'] = 'selected'
        return report | {'geographic_strategy': self.plan['geographic_strategy'], 'queries': list(grouped.values())}
