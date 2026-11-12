"""Injected P4 coordinator shared by offline and confirmed-run adapters.

Collection receipts and delivery receipts are separate. An in-flight attempt
without a full receipt requires explicit reconciliation, never a timed retry.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .crawler_adapter import (CrawlerAuthenticationError, CrawlerVerificationError,
                              CrawlerMediaDownloadError)
from .scoped_selection import digest, eligible_tier, validate_plan
from .triage import CandidateScore, rank_candidates
from .geographic_recovery import GeographicCaptureDeferred, has_pending_evaluations, snapshot_resume_blocker
from .geographic_query_summary import make_query_summary


class ScopedExecution:
    def __init__(self, store, plan, *, capture, risk, geo, detail, review, deliver,
                 validate_geo, account, rotate, stop, checkpoint=lambda stage, payload: None,
                 durable_geo_attempts=False):
        validate_plan(plan)
        self.store = store
        self.plan = deepcopy(plan)
        self.execution_id = plan['execution_id']
        self.capture, self.risk, self.geo = capture, risk, geo
        self.detail, self.review, self.deliver = detail, review, deliver
        self.validate_geo = validate_geo
        self.account, self.rotate, self.stop = account, rotate, stop
        self.checkpoint = checkpoint
        self.durable_geo_attempts = durable_geo_attempts
        self.store.open_execution(self.plan)

    def snapshot(self):
        return self.store.load(self.execution_id, self.plan['plan_digest'])

    def read_state(self, reader):
        return self.store.read_state(self.execution_id, self.plan['plan_digest'], reader)

    def _query(self, query_id):
        return next(q for q in self.plan['queries'] if q['query_id'] == query_id)

    def _capture_query(self, query):
        qid = query['query_id']
        status, has_capture = self.read_state(
            lambda s: (s['queries'][qid]['status'], s['queries'][qid]['capture'] is not None))
        if status == 'evaluated':
            return None
        if self.stop():
            if status in {'pending', 'captured'}:
                self.store.record_query(self.execution_id, qid, 'paused', [], 'pause:' + uuid4().hex,
                                        error={'stage': 'capture_or_evaluation', 'type': 'Stopped'})
            return 'paused'
        if status in {'capturing', 'failed'}:
            return 'capture_requires_reconciliation' if status == 'capturing' else 'query_failed'
        if status == 'paused' and has_capture:
            self.store.record_query(self.execution_id, qid, 'captured', [], 'resume:' + uuid4().hex)
            status = 'captured'
        if status in {'pending', 'paused'}:
            # A claim uses a fresh operation ID; replayed idempotent transitions are never external-call claims.
            self.store.record_query(self.execution_id, qid, 'capturing', [], 'capture-start:' + uuid4().hex)
            try:
                captured = self.capture(deepcopy(self.plan), deepcopy(query))
                if (not isinstance(captured, dict) or not isinstance(captured.get('occurrences'), list)
                        or not isinstance(captured.get('account_id'), str) or not captured['account_id']):
                    raise ValueError('complete capture with account identity required')
                self.store.record_query(self.execution_id, qid, 'captured', [], 'capture-end:' + qid,
                                        capture=captured, capture_account=captured['account_id'])
                del captured
            except GeographicCaptureDeferred as exc:
                self.store.record_query(self.execution_id, qid, 'paused', [], 'capture-deferred:' + uuid4().hex,
                                        error={'type': type(exc).__name__, 'stage': 'before_capture', 'code': exc.code})
                raise
            except Exception as exc:
                # A failed/incomplete capture is never a successful zero-candidate result.
                self.store.record_query(self.execution_id, qid, 'failed', [], 'capture-failed:' + qid,
                                        error={'type': type(exc).__name__, 'stage': 'capture'})
                raise
            self.checkpoint('after_capture', {'query_id': qid})
        captured_occurrences = self.read_state(lambda s: s['queries'][qid]['capture']['occurrences'])
        occurrences = []
        for occurrence in captured_occurrences:
            if self.stop():
                return 'paused'
            oid = occurrence['occurrence_id']
            for part, evaluate in (('risk', self.risk), ('geo', self.geo)):
                cached = self.read_state(lambda s: s['queries'][qid]['evaluations'].get(oid, {}).get(part))
                if cached is not None:
                    occurrence[part] = cached
                    if part == 'geo':
                        self.validate_geo(self.plan, occurrence['candidate'], occurrence[part])
                else:
                    try:
                        kwargs = {}
                        if part == 'geo' and self.durable_geo_attempts:
                            kwargs = {'attempt_state': self.read_state(
                                lambda s: s['queries'][qid].get('geo_attempts', {}).get(oid, [])),
                                'on_attempt': lambda attempts, oid=oid: self.store.record_geo_attempts(
                                    self.execution_id, qid, oid, attempts, 'geo-attempt:' + uuid4().hex)}
                        value = evaluate(deepcopy(self.plan), deepcopy(query), deepcopy(occurrence), **kwargs)
                        if part == 'geo':
                            self.validate_geo(self.plan, occurrence['candidate'], value)
                        self.store.record_evaluation(self.execution_id, qid, oid, part, value,
                                                     part + ':' + oid)
                    except Exception as exc:
                        self.store.record_query(self.execution_id, qid, 'failed', [], 'evaluation-failed:' + qid,
                                                error={'type': type(exc).__name__, 'stage': part})
                        raise
                    occurrence[part] = deepcopy(value)
                    self.checkpoint('after_' + part, {'occurrence_id': oid})
            if occurrence['geo']['result'].get('evaluation_status') == 'invalid_response':
                # A protocol failure is neither a geographic exclusion nor a failed capture.
                # Its immutable evidence and spent correction budget remain in the cache.
                continue
            try:
                eligible_tier(self.plan, occurrence)  # legitimate exclusions are still completed judgments
            except Exception as exc:
                self.store.record_query(self.execution_id, qid, 'failed', [], 'contract-failed:' + qid,
                                        error={'type': type(exc).__name__, 'stage': 'evaluation_contract'})
                raise
            occurrences.append(occurrence)
        if len(occurrences) != len(captured_occurrences):
            return 'geographic_evaluation_pending'
        # Use exactly the original engine's order, preserving original rank and every score field.
        ordered_scores = rank_candidates([CandidateScore(**o['risk']['score']) for o in occurrences])
        # CandidateScore objects carry no occurrence ID; content/rank identify a per-query occurrence.
        indexed = {(o['content_key'], o['rank']): o for o in occurrences}
        if len(indexed) != len(occurrences):
            raise ValueError('duplicate per-query content/rank occurrence')
        ranked = [indexed[(s.content_key, s.rank)] for s in ordered_scores]
        self.store.record_query(self.execution_id, qid, 'evaluated', ranked, 'evaluated:' + qid)
        return None

    def _capture_all(self):
        for query in self.plan['queries']:
            status = self._capture_query(query)
            if status:
                return status
        return None

    def collection_receipt(self, attempt, content):
        post_id = self.read_state(lambda s: s['reservations'][attempt['occurrence_id']]['occurrence']['candidate']['post_id'])
        if (not isinstance(content, dict) or not content or
                content.get('platform') != attempt['platform'] or
                content.get('content_key') != attempt['content_key'] or
                content.get('post_id') != post_id or
                not isinstance(content.get('post'), dict) or
                content['post'].get('post_id') != post_id):
            raise ValueError('complete detail identity required')
        return {k: attempt[k] for k in ('execution_id', 'plan_digest', 'query_id', 'platform',
                                      'content_key', 'occurrence_id', 'attempt_id')} | {
            'content': deepcopy(content), 'content_digest': digest(content)}

    def reconcile(self, attempt_id, receipt, *, receipt_id):
        """Explicit operator-supplied trusted receipt; no old candidates.json recovery."""
        attempt = self.read_state(lambda s: s['attempts'][attempt_id])
        expected = self.collection_receipt(attempt, receipt.get('content'))
        if receipt != expected:
            raise ValueError('reconciliation receipt mismatch')
        return self.store.record_receipt(self.execution_id, attempt_id, receipt_id, receipt)

    def _finish_collection(self, attempt):
        aid = attempt['attempt_id']

        def collection_state(s):
            return (s['receipts'][attempt['receipt_id']],
                    s['reservations'][attempt['occurrence_id']]['occurrence'] if aid not in s['reviews'] else None)

        receipt, occurrence = self.read_state(collection_state)
        if occurrence is not None:
            content = receipt['content']
            changed = digest(content) != occurrence['evidence_digest']
            geo = None
            try:
                geo = (self.review(deepcopy(self.plan), deepcopy(occurrence), deepcopy(content)) if changed
                       else deepcopy(occurrence['geo']))
                if changed:
                    self.validate_geo(self.plan, content, geo)
                updated = deepcopy(occurrence)
                updated.update(candidate=deepcopy(content), evidence_digest=digest(content), geo=geo)
                tier = eligible_tier(self.plan, updated, risk_candidate=occurrence['candidate'])
                review = dict(allowed=tier == attempt['tier'], changed=changed,
                              before_tier=attempt['tier'], after_tier=tier,
                              before_geo=occurrence['geo'], after_geo=geo,
                              reason='same_tier_eligible' if tier == attempt['tier'] else 'tier_or_eligibility_changed')
            except Exception as exc:
                # Collection already succeeded. Preserve its budget even if revalidation fails.
                review = dict(allowed=False, changed=changed, before_tier=attempt['tier'],
                              after_tier=None, before_geo=occurrence['geo'],
                              after_geo=geo, content_digest=receipt['content_digest'],
                              reason='review_failed', error_type=type(exc).__name__)
                self.store.record_review(self.execution_id, aid, 'review:' + aid, review)
                self.store.settle(self.execution_id, aid, 'settle:' + aid)
                raise
            self.store.record_review(self.execution_id, aid, 'review:' + aid, review)
        if attempt['status'] == 'collected':
            self.store.settle(self.execution_id, aid, 'settle:' + aid)
            self.checkpoint('after_settle', {'attempt_id': aid})
        deliverable = self.read_state(lambda s: s['reviews'][aid]['allowed']
            and all(d['attempt_id'] != aid for d in s['deliveries'].values()))
        if not deliverable or self.stop():
            return
        key = digest({'execution_id': self.execution_id, 'plan_digest': self.plan['plan_digest'],
                      'attempt_id': aid, 'content_digest': receipt['content_digest']})
        binding = {k: attempt[k] for k in ('execution_id', 'plan_digest', 'query_id', 'platform',
                                         'content_key', 'occurrence_id', 'attempt_id')}
        binding['content_digest'] = receipt['content_digest']
        delivery = self.deliver(deepcopy(receipt['content']), idempotency_key=key, binding=deepcopy(binding))
        self.checkpoint('after_delivery', {'attempt_id': aid, 'idempotency_key': key})
        self.store.mark_delivery(self.execution_id, aid, key, delivery)

    def _pending_receipts(self):
        def pending(s):
            delivered = {d['attempt_id'] for d in s['deliveries'].values()}
            result = []
            for attempt in s['attempts'].values():
                aid = attempt['attempt_id']
                if attempt['status'] == 'collected':
                    result.append(attempt)
                elif attempt['status'] == 'settled':
                    review = s['reviews'].get(aid)
                    if review is None or (review['allowed'] and aid not in delivered):
                        result.append(attempt)
            return result

        for attempt in self.read_state(pending):
            self._finish_collection(attempt)

    def _collect_ready(self):
        self._pending_receipts()

        def ready_state(s):
            return (s['revision'],
                    any(a['status'] in {'collecting', 'unknown', 'infrastructure'} for a in s['attempts'].values()),
                    next((b for b in s['batches'].values() if b['status'] == 'active'), None))

        while not self.stop():
            revision, uncertain, active = self.read_state(ready_state)
            if uncertain:
                return ['awaiting_reconciliation']
            if active is None:
                result = self.store.reserve_next(self.execution_id, 'reserve:' + str(revision))
                if result['status'] != 'ready':
                    return result.get('reasons', [])
                self.checkpoint('after_reserve', result)
                active = self.read_state(lambda s: s['batches'][result['batch_id']])
            for occurrence in active['picks']:
                if self.stop():
                    return self.report(['paused'])
                query = self._query(occurrence['query_id'])

                def reserved_account(s):
                    reservation = s['reservations'][occurrence['occurrence_id']]
                    return (reservation['status'] == 'reserved' and not reservation['attempt_id'],
                            s['queries'][query['query_id']].get('detail_account'))

                ready, account_id = self.read_state(reserved_account)
                if not ready:
                    continue
                account_id = account_id or self.account(deepcopy(query))
                attempt = self.store.begin_attempt(self.execution_id, occurrence['occurrence_id'],
                                                   'begin:' + uuid4().hex, account_id)
                self.checkpoint('before_detail', attempt)
                try:
                    # Early callbacks stay in this local collector. Only a complete returned receipt can deliver.
                    content = self.detail(deepcopy(occurrence), deepcopy(attempt), account_id=account_id,
                                          content_callback=lambda *args, **kwargs: None)
                except GeographicCaptureDeferred as exc:
                    self.store.defer_attempt(self.execution_id, attempt['attempt_id'],
                                             'defer:' + attempt['attempt_id'], exc.code)
                    raise
                except (CrawlerAuthenticationError, CrawlerVerificationError) as exc:
                    result = self.store.fail_attempt(self.execution_id, attempt['attempt_id'], 'auth',
                                                     'fail:' + attempt['attempt_id'])
                    if not result['retry_allowed']:
                        raise
                    replacement = self.rotate(deepcopy(query), account_id, exc)
                    # Persist injected account identity; never create an account scheduler here.
                    self.store.record_account(self.execution_id, query['query_id'], replacement,
                                              'account:' + attempt['attempt_id'])
                    if not replacement:
                        raise
                    continue
                except CrawlerMediaDownloadError:
                    self.store.fail_attempt(self.execution_id, attempt['attempt_id'], 'media',
                                            'fail:' + attempt['attempt_id'])
                    break
                except Exception:
                    self.store.fail_attempt(self.execution_id, attempt['attempt_id'], 'infrastructure',
                                            'fail:' + attempt['attempt_id'])
                    raise
                self.checkpoint('after_detail', {'attempt_id': attempt['attempt_id']})
                if content is None:
                    self.store.fail_attempt(self.execution_id, attempt['attempt_id'], 'empty',
                                            'fail:' + attempt['attempt_id'])
                    continue
                receipt = self.collection_receipt(attempt, content)
                self.store.record_receipt(self.execution_id, attempt['attempt_id'],
                                           'collection:' + attempt['attempt_id'], receipt)
                self.checkpoint('after_receipt', {'attempt_id': attempt['attempt_id']})
                self._pending_receipts()
        return ['paused']

    def run(self):
        def initial_status(s):
            queries = s['queries']
            return (s.get('fatal_error'), any(q.get('auth_pending') for q in queries.values()),
                    has_pending_evaluations(queries))

        fatal_error, auth_pending, evaluation_pending = self.read_state(initial_status)
        if fatal_error:
            return self.report([fatal_error['kind']])
        if auth_pending:
            return self.report(['awaiting_account_resolution'])

        # Resume durable receipts/batches before starting any later keyword. Each
        # evaluated keyword then completes selection/detail/delivery before the
        # next keyword capture, matching the legacy per-keyword streaming order.
        reasons = ['geographic_evaluation_pending'] if evaluation_pending else self._collect_ready()
        if 'awaiting_reconciliation' in reasons or 'paused' in reasons:
            return self.report(reasons)
        for query in self.plan['queries']:
            status = self._capture_query(query)
            if status and status != 'geographic_evaluation_pending':
                return self.report([status])
            evaluation_pending = evaluation_pending or status == 'geographic_evaluation_pending'
            # Continue analysis, but do not let a later query overtake an unresolved
            # earlier one or silently change the existing tier/rank ordering.
            reasons = ['geographic_evaluation_pending'] if evaluation_pending else self._collect_ready()
            if 'awaiting_reconciliation' in reasons or 'paused' in reasons:
                return self.report(reasons)
        return self.report(reasons)

    def report(self, reasons=()):
        return self.read_state(lambda saved: self._report(saved, reasons))

    def _report(self, saved, reasons):
        queries = saved['queries']
        if hasattr(queries, 'summary'):
            summaries = {qid: queries.summary(qid) for qid in queries}
        else:
            summaries = {qid: make_query_summary(self.plan, qid, query) for qid, query in queries.items()}
        pending = [item for summary in summaries.values() for item in summary['pending_evaluations']]
        ranked = [entry for summary in summaries.values() for entry in summary['ranked']]
        unfinished = [qid for qid, summary in summaries.items() if summary['status'] != 'evaluated']
        collected = saved['state']['total_success']
        handed_off = len(saved['deliveries'])
        reasons = list(reasons)
        if pending:
            reasons.append('geographic_evaluation_pending')
        if not unfinished and not ranked:
            reasons.append('no_candidates')
        if any(a['status'] in {'empty', 'media', 'auth', 'infrastructure', 'unknown'} for a in saved['attempts'].values()):
            reasons.append('collection_failed')
        if collected > handed_off:
            reasons.append('collected_not_delivered')
        if not unfinished and ranked and not any(s['positive_risk'] for s in summaries.values()):
            reasons.append('no_eligible_risk')
        if not unfinished and ranked and not any(entry['tier'] for entry in ranked):
            reasons.append('no_eligible_geographic_content')
        conflicts = sum(s['conflict_count'] for s in summaries.values())
        if conflicts:
            reasons.append('geographic_pending_or_conflict')
        return {'execution_id': self.execution_id, 'plan_digest': self.plan['plan_digest'],
                'resume_blocked_reason': snapshot_resume_blocker(saved),
                'validation_origin': self.plan['validation_origin'],
                'candidate_occurrences': sum(s['candidate_count'] for s in summaries.values()),
                'geographic_evaluation_pending': len(pending), 'pending_evaluations': pending,
                'excluded_occurrences': len(saved['excluded_occurrences']),
                'ineligible_occurrences': sum(entry['tier'] is None for entry in ranked),
                'geographic_pending_or_conflict': conflicts,
                'reserved': saved['state']['total_reserved'], 'collected': collected,
                'handed_off': handed_off, 'collected_not_delivered': collected - handed_off,
                'unfinished_queries': unfinished, 'reasons': list(dict.fromkeys(reasons))}
