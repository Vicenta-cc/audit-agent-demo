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


class ScopedExecution:
    def __init__(self, store, plan, *, capture, risk, geo, detail, review, deliver,
                 validate_geo, account, rotate, stop, checkpoint=lambda stage, payload: None):
        validate_plan(plan)
        self.store = store
        self.plan = deepcopy(plan)
        self.execution_id = plan['execution_id']
        self.capture, self.risk, self.geo = capture, risk, geo
        self.detail, self.review, self.deliver = detail, review, deliver
        self.validate_geo = validate_geo
        self.account, self.rotate, self.stop = account, rotate, stop
        self.checkpoint = checkpoint
        self.store.create(self.plan)

    def snapshot(self):
        return self.store.load(self.execution_id, self.plan['plan_digest'])

    def _query(self, query_id):
        return next(q for q in self.plan['queries'] if q['query_id'] == query_id)

    def _capture_query(self, query):
        qid = query['query_id']
        saved = self.snapshot()['queries'][qid]
        if saved['status'] == 'evaluated':
            return None
        if self.stop():
            if saved['status'] in {'pending', 'captured'}:
                self.store.record_query(self.execution_id, qid, 'paused', [], 'pause:' + uuid4().hex,
                                        error={'stage': 'capture_or_evaluation', 'type': 'Stopped'})
            return 'paused'
        if saved['status'] in {'capturing', 'failed'}:
            return 'capture_requires_reconciliation' if saved['status'] == 'capturing' else 'query_failed'
        if saved['status'] == 'paused' and saved['capture'] is not None:
            self.store.record_query(self.execution_id, qid, 'captured', [], 'resume:' + uuid4().hex)
            saved = self.snapshot()['queries'][qid]
        if saved['status'] in {'pending', 'paused'}:
            # A claim uses a fresh operation ID; replayed idempotent transitions are never external-call claims.
            self.store.record_query(self.execution_id, qid, 'capturing', [], 'capture-start:' + uuid4().hex)
            try:
                captured = self.capture(deepcopy(self.plan), deepcopy(query))
                if (not isinstance(captured, dict) or not isinstance(captured.get('occurrences'), list)
                        or not isinstance(captured.get('account_id'), str) or not captured['account_id']):
                    raise ValueError('complete capture with account identity required')
                self.store.record_query(self.execution_id, qid, 'captured', [], 'capture-end:' + qid,
                                        capture=captured, capture_account=captured['account_id'])
            except Exception as exc:
                # A failed/incomplete capture is never a successful zero-candidate result.
                self.store.record_query(self.execution_id, qid, 'failed', [], 'capture-failed:' + qid,
                                        error={'type': type(exc).__name__, 'stage': 'capture'})
                raise
            self.checkpoint('after_capture', {'query_id': qid})
            saved = self.snapshot()['queries'][qid]
        occurrences = []
        for raw in saved['capture']['occurrences']:
            if self.stop():
                return 'paused'
            occurrence = deepcopy(raw)
            oid = occurrence['occurrence_id']
            for part, evaluate in (('risk', self.risk), ('geo', self.geo)):
                cache = self.snapshot()['queries'][qid]['evaluations'].get(oid, {})
                if part in cache:
                    occurrence[part] = deepcopy(cache[part])
                else:
                    try:
                        value = evaluate(deepcopy(self.plan), deepcopy(query), deepcopy(occurrence))
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
            try:
                eligible_tier(self.plan, occurrence)  # legitimate exclusions are still completed judgments
            except Exception as exc:
                self.store.record_query(self.execution_id, qid, 'failed', [], 'contract-failed:' + qid,
                                        error={'type': type(exc).__name__, 'stage': 'evaluation_contract'})
                raise
            occurrences.append(occurrence)
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
        original = self.snapshot()['reservations'][attempt['occurrence_id']]['occurrence']['candidate']
        if (not isinstance(content, dict) or not content or
                content.get('platform') != attempt['platform'] or
                content.get('content_key') != attempt['content_key'] or
                content.get('post_id') != original['post_id'] or
                not isinstance(content.get('post'), dict) or
                content['post'].get('post_id') != original['post_id']):
            raise ValueError('complete detail identity required')
        return {k: attempt[k] for k in ('execution_id', 'plan_digest', 'query_id', 'platform',
                                      'content_key', 'occurrence_id', 'attempt_id')} | {
            'content': deepcopy(content), 'content_digest': digest(content)}

    def reconcile(self, attempt_id, receipt, *, receipt_id):
        """Explicit operator-supplied trusted receipt; no old candidates.json recovery."""
        attempt = self.snapshot()['attempts'][attempt_id]
        expected = self.collection_receipt(attempt, receipt.get('content'))
        if receipt != expected:
            raise ValueError('reconciliation receipt mismatch')
        return self.store.record_receipt(self.execution_id, attempt_id, receipt_id, receipt)

    def _finish_collection(self, attempt):
        aid = attempt['attempt_id']
        saved = self.snapshot()
        receipt = saved['receipts'][attempt['receipt_id']]
        occurrence = saved['reservations'][attempt['occurrence_id']]['occurrence']
        if aid not in saved['reviews']:
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
        saved = self.snapshot()
        if (not saved['reviews'][aid]['allowed'] or
                any(d['attempt_id'] == aid for d in saved['deliveries'].values()) or self.stop()):
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
        for attempt in self.snapshot()['attempts'].values():
            if attempt['status'] in {'collected', 'settled'}:
                self._finish_collection(attempt)

    def _collect_ready(self):
        self._pending_receipts()
        while not self.stop():
            saved = self.snapshot()
            if any(a['status'] in {'collecting', 'unknown', 'infrastructure'} for a in saved['attempts'].values()):
                return ['awaiting_reconciliation']
            active = next((b for b in saved['batches'].values() if b['status'] == 'active'), None)
            if active is None:
                result = self.store.reserve_next(self.execution_id, 'reserve:' + str(saved['revision']))
                if result['status'] != 'ready':
                    return result.get('reasons', [])
                self.checkpoint('after_reserve', result)
                active = self.snapshot()['batches'][result['batch_id']]
            for occurrence in active['picks']:
                if self.stop():
                    return self.report(['paused'])
                saved = self.snapshot()
                reservation = saved['reservations'][occurrence['occurrence_id']]
                if reservation['status'] != 'reserved' or reservation['attempt_id']:
                    continue
                query = self._query(occurrence['query_id'])
                account_id = saved['queries'][query['query_id']].get('detail_account') or self.account(deepcopy(query))
                attempt = self.store.begin_attempt(self.execution_id, occurrence['occurrence_id'],
                                                   'begin:' + uuid4().hex, account_id)
                self.checkpoint('before_detail', attempt)
                try:
                    # Early callbacks stay in this local collector. Only a complete returned receipt can deliver.
                    content = self.detail(deepcopy(occurrence), deepcopy(attempt), account_id=account_id,
                                          content_callback=lambda *args, **kwargs: None)
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
        saved = self.snapshot()
        if saved.get('fatal_error'):
            return self.report([saved['fatal_error']['kind']])
        if any(q.get('auth_pending') for q in saved['queries'].values()):
            return self.report(['awaiting_account_resolution'])

        # Resume durable receipts/batches before starting any later keyword. Each
        # evaluated keyword then completes selection/detail/delivery before the
        # next keyword capture, matching the legacy per-keyword streaming order.
        reasons = self._collect_ready()
        if 'awaiting_reconciliation' in reasons or 'paused' in reasons:
            return self.report(reasons)
        for query in self.plan['queries']:
            status = self._capture_query(query)
            if status:
                return self.report([status])
            reasons = self._collect_ready()
            if 'awaiting_reconciliation' in reasons or 'paused' in reasons:
                return self.report(reasons)
        return self.report(reasons)

    def report(self, reasons=()):
        saved = self.snapshot()
        rows = [o for q in saved['queries'].values() for o in q['occurrences']]
        unfinished = [q for q, v in saved['queries'].items() if v['status'] != 'evaluated']
        collected = saved['state']['total_success']
        handed_off = len(saved['deliveries'])
        reasons = list(reasons)
        eligible = []
        for occurrence in rows:
            try:
                eligible.append(eligible_tier(self.plan, occurrence))
            except ValueError:
                eligible.append(None)
        if not unfinished and not rows:
            reasons.append('no_candidates')
        if any(a['status'] in {'empty', 'media', 'auth', 'infrastructure', 'unknown'} for a in saved['attempts'].values()):
            reasons.append('collection_failed')
        if collected > handed_off:
            reasons.append('collected_not_delivered')
        if rows and not any(o['risk']['score']['score'] > 0 for o in rows):
            reasons.append('no_eligible_risk')
        if rows and not any(eligible):
            reasons.append('no_eligible_geographic_content')
        if any(o['geo']['result']['geo_tier'] in {'unresolved', 'conflict'} for o in rows):
            reasons.append('geographic_pending_or_conflict')
        return {'execution_id': self.execution_id, 'plan_digest': self.plan['plan_digest'],
                'validation_origin': self.plan['validation_origin'], 'candidate_occurrences': len(rows),
                'excluded_occurrences': len(saved['excluded_occurrences']),
                'ineligible_occurrences': sum(tier is None for tier in eligible),
                'geographic_pending_or_conflict': sum(o['geo']['result']['geo_tier'] in {'unresolved', 'conflict'}
                                                       for o in rows),
                'reserved': saved['state']['total_reserved'], 'collected': collected,
                'handed_off': handed_off, 'collected_not_delivered': collected - handed_off,
                'unfinished_queries': unfinished, 'reasons': list(dict.fromkeys(reasons))}
