"""SQLite state for explicitly isolated geographic-selection experiments.

No JobStore, product settings, network, or expiry-based reconciliation is used.
"""
from __future__ import annotations

from contextlib import closing, nullcontext
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import tempfile
from uuid import uuid4

from .scoped_selection import digest, select_next_batch, validate_plan, validate_risk_record


class Store:
    _table = "scoped_selection_executions"

    def _fence(self, db):
        pass

    def _validate_plan(self, plan):
        validate_plan(plan)
        if plan.get("validation_origin") != "fixture":
            raise ValueError("experiment store requires fixture plan")

    def _state_guard(self):
        lock = getattr(self, '_state_lock', None)
        return lock if lock is not None else nullcontext()

    def __init__(self, path):
        self.path = Path(path).resolve()
        self._state_lock = None
        temporary = self.path.is_relative_to(Path(tempfile.gettempdir()).resolve())
        # macOS temporary directories reside under /private/var/folders.
        temporary = temporary or self.path.is_relative_to(Path('/private/var/folders'))
        if not temporary and 'geographic_selection' not in self.path.parts:
            raise ValueError('explicit temporary or geographic_selection experiment path required')
        if self.path.name.lower() in {'jobs.db', 'audit.db', 'audit_agent.db', 'state.db'}:
            raise ValueError('production database basename rejected')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_guard():
            with closing(self._connection()) as db:
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if any(not name[0].startswith('scoped_selection_') for name in tables):
                    raise ValueError('database contains non-experiment tables')
                db.execute('CREATE TABLE IF NOT EXISTS scoped_selection_executions '
                           '(execution_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)')

    def _connection(self):
        return sqlite3.connect(self.path, timeout=30, isolation_level=None)

    def _commit(self, db):
        """Small fault-injection seam: failure must roll back state and event together."""
        db.commit()

    def _transaction(self, execution_id, operation_id, payload, action):
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError('nonempty operation ID required')
        with self._state_guard():
            with closing(self._connection()) as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    self._fence(db)
                    row = db.execute(f'SELECT snapshot FROM {self._table} WHERE execution_id=?',
                                     (execution_id,)).fetchone()
                    if not row:
                        raise ValueError('unknown execution')
                    snap = json.loads(row[0])
                    self._validate_snapshot(snap, execution_id)
                    previous = snap['operations'].get(operation_id)
                    fingerprint = digest(payload)
                    if previous:
                        if previous['digest'] != fingerprint:
                            raise ValueError('operation ID payload conflict')
                        db.rollback()
                        return deepcopy(previous['result'])
                    result = action(snap)
                    self._refresh(snap)
                    snap['revision'] += 1
                    snap['events'].append({'revision': snap['revision'], 'operation_id': operation_id,
                                           'payload': deepcopy(payload)})
                    snap['operations'][operation_id] = {'digest': fingerprint, 'result': deepcopy(result)}
                    snap['snapshot_digest'] = digest({k: v for k, v in snap.items() if k != 'snapshot_digest'})
                    db.execute(f'UPDATE {self._table} SET snapshot=? WHERE execution_id=?',
                               (json.dumps(snap, ensure_ascii=False), execution_id))
                    self._fence(db)
                    self._commit(db)
                    return deepcopy(result)
                except BaseException:
                    db.rollback()
                    raise

    def _validate_snapshot(self, snap, execution_id):
        if (snap['plan']['execution_id'] != execution_id or
                snap.get('snapshot_digest') != digest({k: v for k, v in snap.items() if k != 'snapshot_digest'})):
            raise ValueError('snapshot binding or integrity mismatch')
        self._validate_plan(snap['plan'])
        for qid, query in snap['queries'].items():
            for oid, attempts in query.get('geo_attempts', {}).items():
                if oid not in Store._capture_index(query):
                    raise ValueError('geographic attempt outside captured occurrences')
                self._validate_geo_attempts(attempts)
            if query['evaluations']:
                captured = Store._capture_index(query)
                for oid, parts in query['evaluations'].items():
                    if oid not in captured:
                        raise ValueError('evaluation outside captured occurrences')
                    if 'risk' in parts:
                        validate_risk_record(snap['plan'], captured[oid], parts['risk'])
            if query['status'] == 'evaluated':
                Store._validate_query_evidence(snap['plan'], query, query['occurrences'])

    @staticmethod
    def _capture_index(query):
        capture = query['capture']
        if not isinstance(capture, dict) or not isinstance(capture.get('occurrences'), list):
            raise ValueError('immutable capture required')
        rows = capture['occurrences']
        index = {o['occurrence_id']: o for o in rows}
        if len(index) != len(rows):
            raise ValueError('duplicate captured occurrence ID')
        return index

    @staticmethod
    def _validate_query_evidence(plan, query, occurrences):
        captured = Store._capture_index(query)
        if set(captured) != {o['occurrence_id'] for o in occurrences}:
            raise ValueError('captured_occurrence_mismatch')
        for occurrence in occurrences:
            oid = occurrence['occurrence_id']
            # Only evaluation results and ordering may change after capture.
            evidence = lambda row: {k: v for k, v in row.items() if k not in {'risk', 'geo'}}
            if evidence(occurrence) != evidence(captured[oid]):
                raise ValueError('captured_occurrence_mismatch')
            validate_risk_record(plan, captured[oid], occurrence.get('risk'))
            cached = query['evaluations'].get(oid, {})
            if 'risk' in cached and cached['risk'] != occurrence['risk']:
                raise ValueError('evaluation input is immutable')

    @staticmethod
    def _refresh(s):
        active = [r for r in s['reservations'].values() if r['status'] == 'reserved']
        successes = [r for r in s['reservations'].values() if r['status'] == 'success']
        for batch in s['batches'].values():
            if batch['status'] == 'active' and not any(
                    r['batch_id'] == batch['batch_id'] for r in active):
                batch['status'] = 'complete'
        s['state'] = dict(busy=any(b['status'] == 'active' for b in s['batches'].values()),
                          occupied=[r['entity_key'] for r in active + successes],
                          query_success={q: sum(r['query_id'] == q for r in successes) for q in s['queries']},
                          total_success=len(successes),
                          query_reserved={q: sum(r['query_id'] == q for r in active) for q in s['queries']},
                          total_reserved=len(active),
                          closed_queries={q: v['closure'] for q, v in s['queries'].items() if v['closure']},
                          excluded_occurrences=s['excluded_occurrences'])

    def create(self, plan):
        self._validate_plan(plan)
        plan = deepcopy(plan)
        snap = dict(plan=plan, queries={q['query_id']: dict(status='pending', occurrences=[], closure=None,
                    auth_retries=0, auth_pending=False, capture_account=None, detail_account=None, capture=None, error=None, evaluations={}) for q in plan['queries']}, revision=0,
                    reservations={}, batches={}, attempts={}, receipts={}, deliveries={}, reviews={}, fatal_error=None,
                    operations={}, events=[{'revision': 0, 'operation_id': 'create',
                    'payload': {'kind': 'create', 'plan_digest': plan['plan_digest']}}], excluded_occurrences=[])
        self._refresh(snap)
        snap['snapshot_digest'] = digest(snap)
        with self._state_guard():
            with closing(self._connection()) as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    self._fence(db)
                    old = db.execute(f'SELECT snapshot FROM {self._table} WHERE execution_id=?',
                                     (plan['execution_id'],)).fetchone()
                    if old:
                        self._validate_snapshot(json.loads(old[0]), plan['execution_id'])
                        if json.loads(old[0])['plan'] != plan:
                            raise ValueError('immutable execution plan conflict')
                    else:
                        db.execute(f'INSERT INTO {self._table} VALUES (?, ?)',
                                   (plan['execution_id'], json.dumps(snap, ensure_ascii=False)))
                    self._fence(db)
                    self._commit(db)
                except BaseException:
                    db.rollback()
                    raise
        return self.load(plan['execution_id'], plan['plan_digest'])

    def load(self, execution_id, expected_plan_digest):
        with self._state_guard():
            with closing(self._connection()) as db:
                db.execute('BEGIN')
                try:
                    self._fence(db)
                    row = db.execute(f'SELECT snapshot FROM {self._table} WHERE execution_id=?',
                                     (execution_id,)).fetchone()
                    if not row:
                        raise ValueError('unknown execution')
                    snap = json.loads(row[0])
                    self._validate_snapshot(snap, execution_id)
                    if snap['plan']['plan_digest'] != expected_plan_digest:
                        raise ValueError('plan digest mismatch')
                    self._fence(db)
                    db.commit()
                    return snap
                except BaseException:
                    db.rollback()
                    raise

    def record_query(self, execution_id, query_id, status, occurrences, operation_id, capture_account=None, capture=None, error=None):
        payload = dict(kind='query', query_id=query_id, status=status, occurrences=occurrences,
                       capture_account=capture_account, capture=capture, error=error)
        def action(s):
            if query_id not in s['queries']:
                raise ValueError('unknown query')
            q = s['queries'][query_id]
            allowed = {'pending': {'capturing', 'evaluated', 'failed', 'paused'},
                       'capturing': {'captured', 'failed', 'paused'},
                       'captured': {'captured', 'evaluated', 'failed', 'paused'},
                       'paused': {'capturing', 'captured', 'evaluated', 'failed'},
                       'failed': set(), 'evaluated': set()}
            if status not in allowed[q['status']]:
                raise ValueError('invalid query transition')
            identifiers = set()
            for occurrence in occurrences:
                if occurrence['execution_id'] != execution_id or occurrence['query_id'] != query_id:
                    raise ValueError('occurrence binding mismatch')
                oid = occurrence['occurrence_id']
                if oid in identifiers or any(oid == o['occurrence_id'] for k, other in s['queries'].items()
                                             if k != query_id for o in other['occurrences']):
                    raise ValueError('duplicate occurrence ID')
                identifiers.add(oid)
            if q['capture'] is not None and capture is not None and q['capture'] != capture:
                raise ValueError('captured input is immutable')
            if q['capture_account'] is not None and capture_account is not None and q['capture_account'] != capture_account:
                raise ValueError('capture identity is immutable')
            if status == 'evaluated':
                self._validate_query_evidence(s['plan'], q, occurrences)
            q.update(status=status, occurrences=deepcopy(occurrences), error=error)
            if capture is not None:
                q['capture'] = deepcopy(capture)
            if capture_account is not None:
                q['capture_account'] = capture_account
            return deepcopy(q)
        return self._transaction(execution_id, operation_id, payload, action)

    def record_evaluation(self, execution_id, query_id, occurrence_id, part, value, operation_id):
        def action(s):
            q = s['queries'][query_id]
            if q['status'] != 'captured' or part not in {'risk', 'geo'}:
                raise ValueError('captured query and risk/geo part required')
            captured = self._capture_index(q)
            if occurrence_id not in captured:
                raise ValueError('evaluation outside captured occurrences')
            if (captured[occurrence_id].get('execution_id') != execution_id or
                    captured[occurrence_id].get('query_id') != query_id):
                raise ValueError('occurrence binding mismatch')
            if part == 'risk':
                validate_risk_record(s['plan'], captured[occurrence_id], value)
            if part == 'geo' and occurrence_id in q.get('geo_attempts', {}):
                if value.get('attempts') != q['geo_attempts'][occurrence_id]:
                    raise ValueError('geographic attempt ledger mismatch')
            parts = q['evaluations'].setdefault(occurrence_id, {})
            if part in parts and parts[part] != value:
                raise ValueError('evaluation input is immutable')
            parts[part] = deepcopy(value)
            return deepcopy(value)
        return self._transaction(execution_id, operation_id,
                                 dict(kind='evaluation', query_id=query_id, occurrence_id=occurrence_id,
                                      part=part, value=value), action)

    @staticmethod
    def _validate_geo_attempts(attempts):
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= 3:
            raise ValueError('invalid geographic correction budget')
        for index, attempt in enumerate(attempts):
            if (not isinstance(attempt, dict) or attempt.get('state') not in {'started', 'completed', 'interrupted'}
                    or (index < len(attempts) - 1 and attempt['state'] == 'started')):
                raise ValueError('invalid geographic attempt state')

    def record_geo_attempts(self, execution_id, query_id, occurrence_id, attempts, operation_id):
        """Spend before invoking a model; append-only progress survives worker loss."""
        self._validate_geo_attempts(attempts)
        def action(s):
            query = s['queries'][query_id]
            captured = self._capture_index(query)
            if query['status'] != 'captured' or occurrence_id not in captured:
                raise ValueError('captured occurrence required for geographic attempt')
            if 'geo' in query['evaluations'].get(occurrence_id, {}):
                raise ValueError('geographic evaluation already sealed')
            previous = query.setdefault('geo_attempts', {}).get(occurrence_id, [])
            if attempts == previous:
                return deepcopy(attempts)
            appended = len(attempts) == len(previous) + 1
            settled = len(attempts) == len(previous) and bool(previous)
            if appended:
                if attempts[:-1] != previous or attempts[-1]['state'] != 'started' or (
                        previous and previous[-1]['state'] == 'started'):
                    raise ValueError('geographic attempt must be claimed before call')
            elif settled:
                old, new = previous[-1], attempts[-1]
                if (attempts[:-1] != previous[:-1] or old['state'] != 'started'
                        or new['state'] not in {'completed', 'interrupted'}
                        or any(new.get(k) != v for k, v in old.items() if k != 'state')):
                    raise ValueError('geographic attempt history is immutable')
            else:
                raise ValueError('geographic correction budget cannot reset')
            query['geo_attempts'][occurrence_id] = deepcopy(attempts)
            return deepcopy(attempts)
        return self._transaction(execution_id, operation_id,
            dict(kind='geo_attempts', query_id=query_id, occurrence_id=occurrence_id, attempts=attempts), action)

    def reserve_next(self, execution_id, operation_id):
        def action(s):
            self._refresh(s)
            if s.get('fatal_error'):
                return dict(status='blocked', picks=[], reasons=[s['fatal_error']['kind']])
            if any(q['auth_pending'] for q in s['queries'].values()):
                return dict(status='blocked', picks=[], reasons=['auth_retry_requires_account'])
            if s['state']['busy']:
                return dict(status='waiting', picks=[], reasons=['active_batch'])
            result = select_next_batch(s['plan'], s['queries'], s['state'])
            if result['status'] != 'ready' or not result['picks']:
                return result
            picks = result['picks']
            qid = result['query_id']
            if qid not in s['queries'] or s['queries'][qid]['closure']:
                raise ValueError('closed or unknown query')
            if s['queries'][qid]['status'] != 'evaluated':
                raise ValueError('selected query is not evaluated')
            cap = next(q['max_notes'] for q in s['plan']['queries'] if q['query_id'] == qid)
            if len(picks) + s['state']['total_success'] + s['state']['total_reserved'] > s['plan']['task_limit']:
                raise ValueError('task quota exceeded')
            if len(picks) + s['state']['query_success'][qid] + s['state']['query_reserved'][qid] > cap:
                raise ValueError('query quota exceeded')
            batch_id = uuid4().hex
            batch = dict(batch_id=batch_id, query_id=qid, tier=result['tier'], picks=deepcopy(picks), status='active')
            entities = set(s['state']['occupied'])
            for pick in picks:
                entity = pick['platform'] + ':' + pick['content_key']
                if pick['query_id'] != qid or entity in entities:
                    raise ValueError('duplicate entity or mixed-query batch')
                entities.add(entity)
                s['reservations'][pick['occurrence_id']] = dict(occurrence=deepcopy(pick), query_id=qid,
                    entity_key=entity, status='reserved', batch_id=batch_id, attempt_id=None)
            s['batches'][batch_id] = batch
            return result | {'batch_id': batch_id}
        return self._transaction(execution_id, operation_id, dict(kind='reserve'), action)

    def begin_attempt(self, execution_id, occurrence_id, operation_id, account_id):
        def action(s):
            if s.get('fatal_error'):
                raise ValueError('execution has fatal failure')
            r = s['reservations'].get(occurrence_id)
            if r and s['queries'][r['query_id']]['auth_pending']:
                raise ValueError('authentication retry account requires reconciliation')
            if r and s['queries'][r['query_id']]['closure'] in {'closed_auth_failure', 'closed_media_failure'}:
                raise ValueError('query is closed')
            if not r or r['status'] != 'reserved' or r['attempt_id']:
                raise ValueError('unavailable or in-flight occurrence; reconcile receipt')
            # Sequential detail: unknown and collecting attempts block any next attempt.
            if any(a['status'] in {'collecting', 'unknown', 'infrastructure'} for a in s['attempts'].values()):
                raise ValueError('previous detail requires reconciliation')
            batch = s['batches'][r['batch_id']]
            remaining = [p['occurrence_id'] for p in batch['picks']
                         if s['reservations'][p['occurrence_id']]['status'] == 'reserved'
                         and not s['reservations'][p['occurrence_id']]['attempt_id']]
            if not remaining or remaining[0] != occurrence_id:
                raise ValueError('batch order mismatch')
            a = {k: r['occurrence'][k] for k in ('query_id', 'platform', 'content_key', 'occurrence_id')}
            a.update(execution_id=execution_id, plan_digest=s['plan']['plan_digest'], attempt_id=uuid4().hex,
                     account_id=account_id, capture_account=s['queries'][r['query_id']]['capture_account'],
                     batch_id=r['batch_id'], tier=batch['tier'], status='collecting')
            r['attempt_id'] = a['attempt_id']
            s['attempts'][a['attempt_id']] = a
            return deepcopy(a)
        return self._transaction(execution_id, operation_id,
                                 dict(kind='begin', occurrence_id=occurrence_id, account_id=account_id), action)

    def record_receipt(self, execution_id, attempt_id, receipt_id, receipt):
        def action(s):
            a = s['attempts'][attempt_id]
            for key in ('execution_id', 'plan_digest', 'query_id', 'platform', 'content_key', 'occurrence_id', 'attempt_id'):
                if receipt.get(key) != a[key]:
                    raise ValueError('receipt binding mismatch: ' + key)
            content = receipt.get('content')
            if (not isinstance(content, dict) or content.get('platform') != a['platform'] or
                    content.get('content_key') != a['content_key'] or
                    not isinstance(content.get('post'), dict) or not content['post'].get('post_id')):
                raise ValueError('complete content identity required')
            candidate = s['reservations'][a['occurrence_id']]['occurrence']['candidate']
            expected_post_id = candidate.get('post', {}).get('post_id')
            if (not expected_post_id or candidate.get('post_id', expected_post_id) != expected_post_id or
                    content['post']['post_id'] != expected_post_id or
                    content.get('post_id', expected_post_id) != expected_post_id):
                raise ValueError('receipt post identity mismatch')
            if receipt.get('content_digest') != digest(receipt['content']):
                raise ValueError('receipt content digest mismatch')
            if a['status'] not in {'collecting', 'unknown', 'infrastructure'}:
                raise ValueError('attempt cannot accept receipt')
            if any(r['attempt_id'] == attempt_id for r in s['receipts'].values()):
                raise ValueError('attempt already has receipt')
            s['receipts'][receipt_id] = deepcopy(receipt)
            a.update(status='collected', receipt_id=receipt_id)
            return deepcopy(receipt)
        return self._transaction(execution_id, 'receipt:' + receipt_id,
                                 dict(kind='receipt', attempt_id=attempt_id, receipt=receipt), action)

    def settle(self, execution_id, attempt_id, operation_id):
        def action(s):
            a = s['attempts'][attempt_id]
            if a['status'] == 'settled':
                return deepcopy(a)
            if a['status'] != 'collected' or a.get('receipt_id') not in s['receipts']:
                raise ValueError('trusted full receipt required')
            s['reservations'][a['occurrence_id']]['status'] = 'success'
            a['status'] = 'settled'
            return deepcopy(a)
        return self._transaction(execution_id, operation_id, dict(kind='settle', attempt_id=attempt_id), action)

    def defer_attempt(self, execution_id, attempt_id, operation_id, code):
        """Retain the reservation when route acquisition failed before collection."""
        def action(s):
            if code not in {'PROXY_BUSY', 'SUPPLEMENT_DEFERRED'}:
                raise ValueError('not a preflight deferral')
            attempt = s['attempts'][attempt_id]
            reservation = s['reservations'][attempt['occurrence_id']]
            if (attempt['status'] != 'collecting' or reservation['attempt_id'] != attempt_id
                    or any(r['attempt_id'] == attempt_id for r in s['receipts'].values())):
                raise ValueError('cannot defer an attempt with external results')
            attempt.update(status='deferred', error={'stage': 'before_detail', 'code': code})
            reservation['attempt_id'] = None
            return deepcopy(attempt)
        return self._transaction(execution_id, operation_id,
                                 dict(kind='defer', attempt_id=attempt_id, code=code), action)

    def fail_attempt(self, execution_id, attempt_id, kind, operation_id):
        def action(s):
            if kind not in {'empty', 'media', 'auth', 'unknown', 'infrastructure'}:
                raise ValueError('unknown failure type')
            a = s['attempts'][attempt_id]
            if a['status'] != 'collecting':
                raise ValueError('attempt no longer collecting')
            a['status'] = kind
            q = s['queries'][a['query_id']]
            retry = False
            if kind in {'unknown', 'infrastructure'}:
                return dict(retry_allowed=False, status=kind)
            s['reservations'][a['occurrence_id']]['status'] = 'released'
            s['excluded_occurrences'].append(a['occurrence_id'])
            if kind == 'empty':
                q['closure'] = 'no_refill'
            elif kind == 'media':
                q['closure'] = 'closed_media_failure'
                for r in s['reservations'].values():
                    if r['batch_id'] == a['batch_id'] and r['status'] == 'reserved' and not r['attempt_id']:
                        r['status'] = 'cancelled'
            elif kind == 'auth':
                for r in s['reservations'].values():
                    if r['batch_id'] == a['batch_id'] and r['status'] == 'reserved' and not r['attempt_id']:
                        r['status'] = 'cancelled'
                retry = q['auth_retries'] == 0
                if retry:
                    q['auth_retries'] += 1
                    q['auth_pending'] = True
                else:
                    q['closure'] = 'closed_auth_failure'
                    s['fatal_error'] = dict(kind='auth_exhausted', query_id=a['query_id'])
            return dict(retry_allowed=retry, status=kind)
        return self._transaction(execution_id, operation_id,
                                 dict(kind='failure', attempt_id=attempt_id, failure=kind), action)

    def record_account(self, execution_id, query_id, account_id, operation_id):
        def action(s):
            q = s['queries'][query_id]
            if q['auth_retries'] != 1:
                raise ValueError('exactly one authorized auth retry required')
            if account_id is None:
                q['closure'] = 'closed_auth_failure'
                if s['fatal_error'] is None:
                    s['fatal_error'] = dict(kind='auth_no_replacement', query_id=query_id)
            else:
                if not isinstance(account_id, str) or not account_id.strip():
                    raise ValueError('nonempty account identity required')
                if s['fatal_error'] or q['closure'] == 'closed_auth_failure':
                    raise ValueError('authentication failure already terminal')
                if q['detail_account'] is not None and q['detail_account'] != account_id:
                    raise ValueError('retry account identity is immutable')
                failed_accounts = {a['account_id'] for a in s['attempts'].values()
                                   if a['query_id'] == query_id and a['status'] == 'auth'}
                if account_id in failed_accounts:
                    raise ValueError('replacement must differ from failed account')
                q['detail_account'] = account_id
            q['auth_pending'] = False
            return dict(detail_account=q['detail_account'], fatal_error=s['fatal_error'])
        return self._transaction(execution_id, operation_id,
                                 dict(kind='account', query_id=query_id, account_id=account_id), action)

    def record_review(self, execution_id, attempt_id, operation_id, review):
        def action(s):
            a = s['attempts'][attempt_id]
            if a['status'] not in {'collected', 'settled'} or not isinstance(review.get('allowed'), bool):
                raise ValueError('complete collected attempt and explicit review required')
            if attempt_id in s['reviews']:
                raise ValueError('review is immutable')
            s['reviews'][attempt_id] = deepcopy(review)
            return deepcopy(review)
        return self._transaction(execution_id, operation_id,
                                 dict(kind='review', attempt_id=attempt_id, review=review), action)

    def mark_delivery(self, execution_id, attempt_id, idempotency_key, receipt):
        def action(s):
            a = s['attempts'][attempt_id]
            if a['status'] != 'settled' or not s['reviews'].get(attempt_id, {}).get('allowed'):
                raise ValueError('settled and approved review required before delivery')
            expected = dict(execution_id=execution_id, plan_digest=s['plan']['plan_digest'],
                            attempt_id=attempt_id, idempotency_key=idempotency_key,
                            content_digest=s['receipts'][a['receipt_id']]['content_digest'])
            if any(receipt.get(k) != v for k, v in expected.items()):
                raise ValueError('delivery receipt binding mismatch')
            if any(d['attempt_id'] == attempt_id for d in s['deliveries'].values()):
                raise ValueError('attempt already delivered')
            s['deliveries'][idempotency_key] = deepcopy(receipt)
            return deepcopy(receipt)
        return self._transaction(execution_id, 'delivery:' + idempotency_key,
                                 dict(kind='delivery', attempt_id=attempt_id, receipt=receipt), action)
