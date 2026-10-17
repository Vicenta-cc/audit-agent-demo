"""Controlled collection/delivery only; no production callbacks or services."""
from copy import deepcopy

import pytest

from audit_agent.scoped_execution import ScopedExecution
from audit_agent.scoped_selection_store import Store
from audit_agent.crawler_adapter import (CrawlerMediaDownloadError, CrawlerVerificationError,
                                       CrawlerRateLimitError)
from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
from experiments.geographic_selection.bridge import validate_geo_record


class Harness:
    def __init__(self, tmp_path, *, rows=None, max_notes=1, task_limit=2):
        self.plan = fixture_plan(queries=('A', 'B'), max_notes=max_notes, task_limit=task_limit)
        self.rows = rows or {'A': [('x', '克拉玛依欠薪')], 'B': [('y', '克拉玛依欠薪')]}
        self.items = {q: [fixture_occurrence(self.plan, q, key, rank=len(entries) - i, desc=text,
                            raw_geo={'status': 'located', 'locations': [{'field': 'desc',
                                      'place': '新疆' if text == '新疆欠薪' else '克拉玛依市'}]})
                          for i, (key, text) in enumerate(entries)] for q, entries in self.rows.items()}
        self.store = Store(tmp_path / 'p4.sqlite')
        self.calls = []
        self.effects = {}
        self.deliveries = {}
        self.failure = {}
        self.stop_now = False
        self.crash_at = None
        self.review_change = None

    def capture(self, plan, query):
        self.calls.append(('capture', query['query_id']))
        return {'account_id': 'capture-account', 'occurrences': deepcopy(self.items[query['query_id']])}

    def risk(self, plan, query, occurrence):
        self.calls.append(('risk', occurrence['occurrence_id']))
        return deepcopy(occurrence['risk'])

    def geo(self, plan, query, occurrence):
        self.calls.append(('geo', occurrence['occurrence_id']))
        return deepcopy(occurrence['geo'])

    def detail(self, occurrence, attempt, *, account_id, content_callback):
        key = occurrence['content_key']
        self.calls.append(('detail', key))
        content_callback({'premature': key})
        assert not self.deliveries.get(key)
        if key in self.failure:
            result = self.failure.pop(key)
            if isinstance(result, BaseException):
                raise result
            return result
        content = deepcopy(occurrence['candidate'])
        if self.review_change:
            content = deepcopy(self.review_change['candidate'])
        self.effects[attempt['attempt_id']] = content
        return content

    def review(self, plan, occurrence, content):
        self.calls.append(('review', occurrence['content_key']))
        return deepcopy(self.review_change['geo'] if self.review_change else occurrence['geo'])

    def deliver(self, content, *, idempotency_key, binding):
        self.calls.append(('deliver', binding['content_key']))
        self.deliveries.setdefault(idempotency_key, deepcopy(content))
        return dict(binding, idempotency_key=idempotency_key, status='accepted')

    def checkpoint(self, stage, payload):
        if stage == self.crash_at:
            self.crash_at = None
            raise RuntimeError('controlled interruption:' + stage)

    def runner(self):
        return ScopedExecution(self.store, self.plan, capture=self.capture, risk=self.risk, geo=self.geo,
                               detail=self.detail, review=self.review, deliver=self.deliver,
                               validate_geo=validate_geo_record,
                               account=lambda query: 'detail-account',
                               rotate=lambda query, account, error: 'second-account',
                               stop=lambda: self.stop_now, checkpoint=self.checkpoint)


def test_local_delivery_streams_before_next_query_capture_and_callback_stays_isolated(tmp_path):
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪')], 'B': [('y', '克拉玛依欠薪')]})
    result = h.runner().run()
    assert h.calls.index(('deliver', 'x')) < h.calls.index(('capture', 'B'))
    assert [c for c in h.calls if c[0] == 'detail'] == [('detail', 'x'), ('detail', 'y')]
    assert result['collected'] == result['handed_off'] == 2


def test_invalid_geo_is_pending_and_other_evaluations_survive_restart(tmp_path):
    from experiments.geographic_selection.bridge import prepare_geo
    from audit_agent.geographic_status import classify_geographic_status, geographic_completion_failure
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪'), ('z', '克拉玛依欠薪')],
                               'B': [('y', '克拉玛依欠薪')]})
    invalid = h.items['A'][0]
    invalid['geo'] = prepare_geo(invalid['geo']['request']['context'], invalid['candidate'],
        {'status': 'located', 'locations': [{'field': 'desc', 'place': '北京市'}]})
    result = h.runner().run()
    saved = h.store.load(h.plan['execution_id'], h.plan['plan_digest'])
    assert saved['queries']['A']['status'] == 'captured'
    assert saved['queries']['B']['status'] == 'evaluated'
    assert len(saved['queries']['A']['evaluations']) == 2
    assert result['candidate_occurrences'] == 3
    assert result['geographic_evaluation_pending'] == 1
    assert result['pending_evaluations'][0]['occurrence_id'] == invalid['occurrence_id']
    assert result['resume_blocked_reason'] == 'geographic_evaluation_reconciliation_required'
    assert 'no_eligible_geographic_content' not in result['reasons']
    assert 'no_eligible_risk' not in result['reasons']
    assert not [c for c in h.calls if c[0] == 'detail']
    assert classify_geographic_status(result) == {'status': 'incomplete', 'reason': 'geographic_evaluation_pending'}
    assert geographic_completion_failure(result)[0] == 'geographic_evaluation_pending'
    calls = list(h.calls)
    again = h.runner().run()
    assert again['geographic_evaluation_pending'] == 1
    assert h.calls == calls  # no recapture, model retry, or implicit budget reset


def test_legitimate_insufficient_context_is_a_completed_judgment(tmp_path):
    from experiments.geographic_selection.bridge import prepare_geo
    h = Harness(tmp_path)
    row = h.items['A'][0]
    row['geo'] = prepare_geo(row['geo']['request']['context'], row['candidate'],
                            {'status': 'insufficient_context', 'locations': []})
    result = h.runner().run()
    assert result['geographic_evaluation_pending'] == 0
    assert not result['unfinished_queries']
    assert result['handed_off'] == 1


def test_geo_attempt_claim_survives_interruption_and_cannot_reset(tmp_path):
    h = Harness(tmp_path)
    seen = []
    class WorkerLost(BaseException):
        pass
    def geo(plan, query, occurrence, *, attempt_state, on_attempt):
        seen.append(deepcopy(attempt_state))
        if not attempt_state:
            on_attempt([{'state': 'started', 'prompt': 'fixture'}])
            raise WorkerLost()
        on_attempt([{'state': 'interrupted', 'prompt': 'fixture'}])
        record = h.geo(plan, query, occurrence)
        record['attempts'] = [{'state': 'interrupted', 'prompt': 'fixture'}]
        return record
    runner = h.runner()
    runner.geo, runner.durable_geo_attempts = geo, True
    with pytest.raises(WorkerLost):
        runner.run()
    assert runner.snapshot()['queries']['A']['status'] == 'captured'
    attempts = runner.snapshot()['queries']['A']['geo_attempts'][h.items['A'][0]['occurrence_id']]
    assert attempts == [{'state': 'started', 'prompt': 'fixture'}]
    with pytest.raises(ValueError, match='budget'):
        h.store.record_geo_attempts(h.plan['execution_id'], 'A', h.items['A'][0]['occurrence_id'], [], 'reset')
    def resume_geo(plan, query, occurrence, **kwargs):
        return geo(plan, query, occurrence, **kwargs) if query['query_id'] == 'A' else h.geo(plan, query, occurrence)
    resumed = h.runner()
    resumed.geo, resumed.durable_geo_attempts = resume_geo, True
    assert resumed.run()['handed_off'] == 2
    assert seen == [[], [{'state': 'started', 'prompt': 'fixture'}]]
    assert len([c for c in h.calls if c == ('capture', 'A')]) == 1


def test_supplement_is_selected_inside_its_query_before_next_query_capture(tmp_path):
    h = Harness(tmp_path, rows={'A': [('x', '新疆欠薪')], 'B': [('y', '克拉玛依欠薪')]}, task_limit=2)
    result = h.runner().run()
    assert [c for c in h.calls if c[0] == 'detail'] == [('detail', 'x'), ('detail', 'y')]
    assert h.calls.index(('deliver', 'x')) < h.calls.index(('capture', 'B'))
    assert result['collected'] == result['handed_off'] == 2
    assert not result['unfinished_queries']


def test_empty_no_refill_even_lower_tier_but_other_queries_continue(tmp_path):
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪'), ('z', '新疆欠薪')],
                               'B': [('x', '克拉玛依欠薪')]}, max_notes=2)
    h.failure['x'] = None
    result = h.runner().run()
    assert [c for c in h.calls if c[0] == 'detail'] == [('detail', 'x'), ('detail', 'x')]
    assert result['collected'] == 1
    assert h.store.load(h.plan['execution_id'], h.plan['plan_digest'])['state']['closed_queries']['A'] == 'no_refill'


def test_empty_continues_original_same_tier_picks_and_media_cancels(tmp_path):
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪'), ('z', '克拉玛依欠薪')], 'B': []}, max_notes=2)
    h.failure['x'] = None
    assert h.runner().run()['collected'] == 1
    assert [c for c in h.calls if c[0] == 'detail'] == [('detail', 'x'), ('detail', 'z')]
    h2 = Harness(tmp_path / 'media', rows=h.rows, max_notes=2)
    h2.failure['x'] = CrawlerMediaDownloadError('controlled typed media failure')
    result = h2.runner().run()
    assert [c for c in h2.calls if c[0] == 'detail'] == [('detail', 'x')]
    assert 'collection_failed' in result['reasons']


def test_rate_limit_propagates_and_preserves_unknown_reservation(tmp_path):
    h = Harness(tmp_path)
    h.failure['x'] = CrawlerRateLimitError('rate limited')
    with pytest.raises(CrawlerRateLimitError):
        h.runner().run()
    s = h.store.load(h.plan['execution_id'], h.plan['plan_digest'])
    assert s['state']['total_reserved'] == 1
    assert 'awaiting_reconciliation' in h.runner().run()['reasons']


def test_one_auth_rotation_without_rescoring(tmp_path):
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪'), ('z', '克拉玛依欠薪')], 'B': []}, max_notes=1)
    h.failure['x'] = CrawlerVerificationError('verification')
    assert h.runner().run()['collected'] == 1
    assert [c for c in h.calls if c[0] == 'detail'] == [('detail', 'x'), ('detail', 'z')]
    assert len([c for c in h.calls if c[0] == 'risk']) == 2


@pytest.mark.parametrize('stage', ['after_reserve', 'after_receipt', 'after_settle', 'after_delivery'])
def test_restart_does_not_recollect_completed_or_reserved_batch(tmp_path, stage):
    h = Harness(tmp_path, task_limit=1)
    h.crash_at = stage
    with pytest.raises(RuntimeError, match='controlled interruption'):
        h.runner().run()
    result = h.runner().run()
    assert result['collected'] == result['handed_off'] == 1
    assert len([c for c in h.calls if c[0] == 'detail']) == 1
    assert len(h.deliveries) == 1


def test_unknown_result_is_not_retried_without_complete_trusted_receipt(tmp_path):
    h = Harness(tmp_path, task_limit=1)
    h.crash_at = 'after_detail'
    with pytest.raises(RuntimeError):
        h.runner().run()
    result = h.runner().run()
    assert result['reasons'] == ['awaiting_reconciliation']
    assert len([c for c in h.calls if c[0] == 'detail']) == 1
    snapshot = h.store.load(h.plan['execution_id'], h.plan['plan_digest'])
    attempt = next(iter(snapshot['attempts'].values()))
    content = h.effects[attempt['attempt_id']]
    receipt = h.runner().collection_receipt(attempt, content)
    h.runner().reconcile(attempt['attempt_id'], receipt, receipt_id='trusted-receipt')
    assert h.runner().run()['handed_off'] == 1
    assert len([c for c in h.calls if c[0] == 'detail']) == 1


def test_changed_tier_is_collected_not_delivered_and_budget_stays_used(tmp_path):
    h = Harness(tmp_path, task_limit=1)
    h.review_change = fixture_occurrence(h.plan, 'A', 'x', desc='新疆欠薪',
                                        raw_geo={'status': 'located', 'locations': [{'field': 'desc', 'place': '新疆'}]})
    result = h.runner().run()
    assert result['collected'] == 1 and result['handed_off'] == 0
    assert result['collected_not_delivered'] == 1
    assert 'collected_not_delivered' in result['reasons']
    assert len([c for c in h.calls if c[0] == 'detail']) == 1


def test_pause_and_zero_candidates_have_distinct_outcomes(tmp_path):
    h = Harness(tmp_path)
    h.stop_now = True
    result = h.runner().run()
    assert 'paused' in result['reasons'] and result['unfinished_queries'] == ['A', 'B']
    assert not h.calls
    h.stop_now = False
    assert h.runner().run()['handed_off'] == 2
    z = Harness(tmp_path / 'zero', rows={'A': [], 'B': []})
    result = z.runner().run()
    assert 'no_candidates' in result['reasons'] and not result['unfinished_queries']


def test_capture_failure_never_opens_supplement(tmp_path):
    h = Harness(tmp_path)
    def fail(plan, query):
        raise CrawlerRateLimitError('capture failure')
    h.capture = fail
    with pytest.raises(CrawlerRateLimitError):
        h.runner().run()
    assert not [c for c in h.calls if c[0] == 'detail']


@pytest.mark.parametrize('stage', ['after_capture', 'after_risk', 'after_geo'])
def test_partial_evaluations_reuse_completed_inputs_after_restart(tmp_path, stage):
    h = Harness(tmp_path, task_limit=1)
    h.crash_at = stage
    with pytest.raises(RuntimeError):
        h.runner().run()
    assert h.runner().run()['handed_off'] == 1
    assert [c for c in h.calls if c[0] == 'capture'] == [('capture', 'A'), ('capture', 'B')]
    assert len([c for c in h.calls if c[0] == 'risk']) == 2
    assert len([c for c in h.calls if c[0] == 'geo']) == 2


def test_delivery_failure_retries_same_key_and_never_recollects(tmp_path):
    h = Harness(tmp_path, task_limit=1)
    original = h.deliver
    keys = []
    def fail_once(content, *, idempotency_key, binding):
        keys.append(idempotency_key)
        result = original(content, idempotency_key=idempotency_key, binding=binding)
        if len(keys) == 1:
            raise RuntimeError('delivery callback failed after accepting')
        return result
    h.deliver = fail_once
    with pytest.raises(RuntimeError):
        h.runner().run()
    assert h.runner().run()['handed_off'] == 1
    assert keys[0] == keys[1]
    assert len([c for c in h.calls if c[0] == 'detail']) == 1


def test_auth_missing_or_interrupted_rotation_is_not_silently_resumed(tmp_path):
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪'), ('z', '克拉玛依欠薪')], 'B': []})
    h.failure['x'] = CrawlerVerificationError('verification')
    runner = h.runner()
    runner.rotate = lambda query, account, error: None
    with pytest.raises(CrawlerVerificationError):
        runner.run()
    assert 'auth_no_replacement' in h.runner().run()['reasons']
    assert [c for c in h.calls if c[0] == 'detail'] == [('detail', 'x')]


def test_changed_text_same_tier_is_revalidated_before_delivery(tmp_path):
    h = Harness(tmp_path, task_limit=1)
    h.review_change = fixture_occurrence(h.plan, 'A', 'x', desc='克拉玛依市新的欠薪文字')
    assert h.runner().run()['handed_off'] == 1
    assert h.calls.index(('review', 'x')) < h.calls.index(('deliver', 'x'))


def test_concurrent_capture_claim_does_not_repeat_external_capture(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    h = Harness(tmp_path, task_limit=1)
    barrier = Barrier(2)
    original = h.store.record_query
    def synchronized(*args, **kwargs):
        if args[1] == 'A' and args[2] == 'capturing':
            barrier.wait(timeout=10)
        return original(*args, **kwargs)
    h.store.record_query = synchronized
    def run(_):
        try:
            return h.runner().run()
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, range(2)))
    assert [c for c in h.calls if c == ('capture', 'A')] == [('capture', 'A')]
    assert any(isinstance(o, dict) and o['handed_off'] == 1 for o in outcomes)


def test_concurrent_detail_claim_does_not_repeat_external_detail(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    h = Harness(tmp_path, task_limit=1)
    seed = h.runner()
    assert seed._capture_all() is None
    h.store.reserve_next(h.plan['execution_id'], 'test-reserve')
    barrier = Barrier(2)
    original = h.store.begin_attempt
    def synchronized(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)
    h.store.begin_attempt = synchronized
    def run(_):
        try:
            return h.runner().run()
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, range(2)))
    assert [c for c in h.calls if c == ('detail', 'x')] == [('detail', 'x')]
    assert any(isinstance(o, dict) and o['handed_off'] == 1 for o in outcomes)


def test_begin_commit_failure_never_starts_detail(tmp_path):
    import json
    h = Harness(tmp_path, task_limit=1)
    original = h.store._commit
    def reject_collecting(db):
        row = db.execute('SELECT snapshot FROM scoped_selection_executions').fetchone()
        if row and json.loads(row[0])['attempts']:
            raise RuntimeError('controlled commit failure')
        return original(db)
    h.store._commit = reject_collecting
    with pytest.raises(RuntimeError, match='controlled commit failure'):
        h.runner().run()
    assert not [c for c in h.calls if c[0] == 'detail']
    snapshot = h.store.load(h.plan['execution_id'], h.plan['plan_digest'])
    assert not snapshot['attempts'] and snapshot['state']['total_success'] == 0


@pytest.mark.parametrize('changed', ['post', 'comments', 'identity'])
def test_first_risk_rejects_old_record_for_same_identity_before_persistence(tmp_path, changed):
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪')], 'B': []}, task_limit=1)
    old = deepcopy(h.items['A'][0])
    changes = {'post': {'desc': '克拉玛依欠薪新原文'},
               'comments': {'comments': [{'comment_id': 'c', 'content': '新评论欠薪'}]},
               'identity': {'enterprise_verify_reason': '公安官方'}}
    current = fixture_occurrence(h.plan, 'A', 'x', **changes[changed])
    assert current['occurrence_id'] == old['occurrence_id']
    if changed == 'identity':
        assert current['risk']['score']['band'] == 'discard'
        assert old['risk']['score']['band'] == 'weak'
    h.items['A'] = [current]
    h.risk = lambda p, q, o: deepcopy(old['risk'])
    with pytest.raises(ValueError, match='risk_capture_evidence_mismatch'):
        h.runner().run()
    saved = h.store.load(h.plan['execution_id'], h.plan['plan_digest'])
    assert saved['queries']['A']['status'] == 'failed'
    assert saved['queries']['A']['evaluations'] == {}
    assert saved['state']['total_reserved'] == saved['state']['total_success'] == 0
    assert not [c for c in h.calls if c[0] in {'geo', 'detail', 'deliver'}]


@pytest.mark.parametrize('changes', [
    {'desc': '克拉玛依市新的欠薪文字'},
    {'comments': [{'comment_id': 'c', 'content': '新评论', 'text': '与原风险别名不同'}]},
    {'enterprise_verify_reason': '公安官方'},
])
def test_detail_same_tier_keeps_first_risk_and_capture_after_evidence_change(tmp_path, changes):
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪')], 'B': []}, task_limit=1)
    original = deepcopy(h.items['A'][0])
    h.review_change = fixture_occurrence(h.plan, 'A', 'x', **changes)
    assert h.runner().run()['handed_off'] == 1
    saved = h.runner().snapshot()
    assert saved['queries']['A']['capture']['occurrences'] == [original]
    assert saved['queries']['A']['evaluations'][original['occurrence_id']]['risk'] == original['risk']
    assert len([c for c in h.calls if c[0] == 'risk']) == 1
    assert saved['state']['total_success'] == 1 and saved['state']['total_reserved'] == 0
    assert all(review['allowed'] for review in saved['reviews'].values())


def test_first_risk_uses_raw_capture_not_lossy_p3_fields(tmp_path):
    h = Harness(tmp_path, rows={'A': [('x', '克拉玛依欠薪')], 'B': []}, task_limit=1)
    current = fixture_occurrence(h.plan, 'A', 'x', enterprise_verify_reason='普通商家',
        custom_verify='个人认证', follower_count=10, user_signature='原签名', liked_count=3,
        comments=[{'comment_id': 'c', 'content': '原风险引擎优先内容', 'text': 'P3优先文本'}])
    assert 'enterprise_verify_reason' not in current['candidate']['post']
    assert current['candidate']['comments'][0]['text'] != current['risk']['input']['comments'][0]['content']
    h.items['A'] = [current]
    assert h.runner().run()['handed_off'] == 1
    assert h.runner().snapshot()['queries']['A']['evaluations'][current['occurrence_id']]['risk'] == current['risk']
