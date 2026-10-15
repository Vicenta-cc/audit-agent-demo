"""Experiment-only SQLite guarantees; independent connections exercise DB serialization."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from audit_agent.triage import CandidateScore
import sqlite3

import pytest

from audit_agent.scoped_selection import digest
from audit_agent.scoped_selection_store import Store


def plan():
    value = dict(schema_version='p4-fixture-input-v1', validation_origin='fixture', execution_id='e', platform='dy', mode='search',
                 binding=dict(owner='o', session='s', draft='d', revision=1), scope_digest=digest('scope'),
                 policy_digest=digest('policy'), selection_policy='local_first_with_supplement', target_level='city',
                 p3_version='2120c53379d1b79c45e3cb8232b9c9b96657b183',
                 versions={key: digest(key) for key in ('admin_catalog', 'prompt', 'model', 'risk')}, triage_mode='select',
                 queries=[dict(query_id='q', query='word', max_notes=2, capture_parameters={}, risk_input_ref='r')],
                 task_limit=2)
    value['versions']['risk'] = digest({'frozen': 'risk'})
    value['plan_digest'] = digest(value)
    return value


def stored_occurrence(p, oid, *, content_key=None):
    key = oid if content_key is None else content_key
    candidate = dict(platform='dy', content_key=key, post_id=key, post={'post_id': key}, comments=[])
    risk_input = dict(content_key=key, rank=1, search_keyword='word', config={'frozen': 'risk'},
                      item=deepcopy(candidate['post']), comments=[])
    return dict(occurrence_id=oid, execution_id='e', query_id='q', platform='dy', content_key=key,
                candidate=candidate, evidence_digest=digest(candidate), rank=1, source_keyword='word',
                risk=dict(occurrence_id=oid, input=risk_input, input_digest=digest(risk_input),
                          input_ref='fixture:risk', config_digest=p['versions']['risk'], model_valid=False,
                          score=asdict(CandidateScore(key, 1, 100, 'weak', 'fixture'))))


def complete_query(store, occurrences):
    store.record_query('e', 'q', 'capturing', [], 'start')
    store.record_query('e', 'q', 'captured', [], 'capture',
                       capture={'account_id': 'account', 'occurrences': occurrences})
    store.record_query('e', 'q', 'evaluated', occurrences, 'query')


def prepared(tmp_path, monkeypatch, picks=2):
    p = plan()
    occurrences = [stored_occurrence(p, str(i)) for i in range(picks)]
    monkeypatch.setattr('audit_agent.scoped_selection_store.select_next_batch',
                        lambda p, q, s: dict(status='ready', picks=occurrences, query_id='q', tier='local'))
    store = Store(tmp_path / 'state.sqlite')
    store.create(p)
    complete_query(store, occurrences)
    return store, p, occurrences


def receipt(p, a):
    content = {'platform': a['platform'], 'content_key': a['content_key'], 'post': {'post_id': a['content_key'], 'desc': 'complete'}}
    return {key: a[key] for key in ('execution_id', 'plan_digest', 'query_id', 'platform', 'content_key',
                                   'occurrence_id', 'attempt_id')} | {'content': content, 'content_digest': digest(content)}


def test_replay_conflict_success_and_restart(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch)
    b = s.reserve_next('e', 'reserve')
    assert s.reserve_next('e', 'reserve') == b
    a = s.begin_attempt('e', '0', 'attempt', 'account')
    with pytest.raises(ValueError):
        s.begin_attempt('e', '0', 'attempt', 'other-account')
    r = receipt(p, a)
    s.record_receipt('e', a['attempt_id'], 'receipt', r)
    s.settle('e', a['attempt_id'], 'settle')
    s.settle('e', a['attempt_id'], 'settle')
    loaded = Store(tmp_path / 'state.sqlite').load('e', p['plan_digest'])
    assert loaded['state']['total_success'] == 1
    assert loaded['state']['total_reserved'] == 1
    assert len(loaded['receipts']) == 1
    with pytest.raises(ValueError):
        s.record_receipt('e', a['attempt_id'], 'receipt', r | {'content_digest': 'wrong'})
    with pytest.raises(ValueError):
        s.load('e', 'wrong')


def test_two_connections_only_one_active_batch(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch)
    def reserve(n):
        return Store(tmp_path / 'state.sqlite').reserve_next('e', str(n))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, range(2)))
    assert sum(v['status'] == 'ready' for v in outcomes) == 1
    state = s.load('e', p['plan_digest'])['state']
    assert state['total_reserved'] == 2


def test_empty_closes_without_canceling_original_batch(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch)
    s.reserve_next('e', 'reserve')
    a = s.begin_attempt('e', '0', 'a', 'account')
    s.fail_attempt('e', a['attempt_id'], 'empty', 'fail')
    state = Store(tmp_path / 'state.sqlite').load('e', p['plan_digest'])['state']
    assert state['closed_queries']['q'] == 'no_refill'
    assert state['total_reserved'] == 1
    s.begin_attempt('e', '1', 'b', 'account')


def test_media_cancels_unstarted_and_auth_budget_survives_restart(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch)
    s.reserve_next('e', 'reserve')
    a = s.begin_attempt('e', '0', 'a', 'account')
    assert s.fail_attempt('e', a['attempt_id'], 'auth', 'auth')['retry_allowed']
    s = Store(tmp_path / 'state.sqlite')
    s.record_account('e', 'q', 'account2', 'account2')
    monkeypatch.setattr('audit_agent.scoped_selection_store.select_next_batch',
                        lambda p, q, state: dict(status='ready', picks=[q['q']['occurrences'][1]], query_id='q', tier='local'))
    s.reserve_next('e', 'reserve2')
    a = s.begin_attempt('e', '1', 'b', 'account2')
    assert not s.fail_attempt('e', a['attempt_id'], 'auth', 'auth2')['retry_allowed']
    assert s.load('e', p['plan_digest'])['queries']['q']['auth_retries'] == 1
    assert s.load('e', p['plan_digest'])['fatal_error']['kind'] == 'auth_exhausted'
    assert s.reserve_next('e', 'after-auth-exhausted')['status'] == 'blocked'


def test_unknown_keeps_reservation_and_receipt_can_reconcile(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch, 1)
    s.reserve_next('e', 'r')
    a = s.begin_attempt('e', '0', 'a', 'account')
    s.fail_attempt('e', a['attempt_id'], 'unknown', 'unknown')
    assert s.load('e', p['plan_digest'])['state']['total_reserved'] == 1
    s.record_receipt('e', a['attempt_id'], 'receipt', receipt(p, a))
    s.settle('e', a['attempt_id'], 'settle')
    assert s.load('e', p['plan_digest'])['state']['total_success'] == 1


def test_reject_existing_production_schema(tmp_path):
    path = tmp_path / 'foreign.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE jobs (id TEXT)')
    with pytest.raises(ValueError):
        Store(path)


def test_commit_failure_rolls_back_reservation_and_event(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch)
    before = s.load('e', p['plan_digest'])
    def failed_commit(db):
        raise sqlite3.OperationalError('injected commit failure')
    monkeypatch.setattr(s, '_commit', failed_commit)
    with pytest.raises(sqlite3.OperationalError):
        s.reserve_next('e', 'reserve')
    assert s.load('e', p['plan_digest']) == before


def test_media_closure_cancels_only_unstarted_picks(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch)
    s.reserve_next('e', 'reserve')
    a = s.begin_attempt('e', '0', 'a', 'account')
    s.fail_attempt('e', a['attempt_id'], 'media', 'media')
    snap = Store(tmp_path / 'state.sqlite').load('e', p['plan_digest'])
    assert snap['state']['closed_queries'] == {'q': 'closed_media_failure'}
    assert snap['state']['total_reserved'] == 0
    assert snap['reservations']['1']['status'] == 'cancelled'
    with pytest.raises(ValueError):
        s.begin_attempt('e', '1', 'b', 'account')


def test_receipt_must_match_full_binding_and_delivery_needs_review(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch, 1)
    s.reserve_next('e', 'reserve')
    a = s.begin_attempt('e', '0', 'a', 'account')
    r = receipt(p, a)
    for field in ('execution_id', 'plan_digest', 'query_id', 'platform', 'content_key', 'occurrence_id', 'attempt_id'):
        with pytest.raises(ValueError):
            s.record_receipt('e', a['attempt_id'], 'bad-' + field, r | {field: 'foreign'})
    wrong_content = r['content'] | {'post': {'post_id': 'foreign'}}
    with pytest.raises(ValueError, match='post identity'):
        s.record_receipt('e', a['attempt_id'], 'wrong-post',
                         r | {'content': wrong_content, 'content_digest': digest(wrong_content)})
    wrong_content = r['content'] | {'post_id': 'foreign'}
    with pytest.raises(ValueError, match='post identity'):
        s.record_receipt('e', a['attempt_id'], 'wrong-top-post',
                         r | {'content': wrong_content, 'content_digest': digest(wrong_content)})
    s.record_receipt('e', a['attempt_id'], 'receipt', r)
    s.settle('e', a['attempt_id'], 'settle')
    d = dict(execution_id='e', plan_digest=p['plan_digest'], attempt_id=a['attempt_id'],
             idempotency_key='delivery', content_digest=r['content_digest'])
    with pytest.raises(ValueError):
        s.mark_delivery('e', a['attempt_id'], 'delivery', d)
    s.record_review('e', a['attempt_id'], 'review', {'allowed': True, 'reason': 'same tier'})
    assert s.mark_delivery('e', a['attempt_id'], 'delivery', d) == d
    assert s.mark_delivery('e', a['attempt_id'], 'delivery', d) == d
    assert len(s.load('e', p['plan_digest'])['deliveries']) == 1


def test_query_readiness_quota_and_entity_checked_in_transaction(tmp_path, monkeypatch):
    p = plan()
    s = Store(tmp_path / 'state.sqlite')
    s.create(p)
    picks = [stored_occurrence(p, str(i), content_key='same') for i in range(2)]
    monkeypatch.setattr('audit_agent.scoped_selection_store.select_next_batch',
                        lambda *args: dict(status='ready', picks=picks, query_id='q', tier='local'))
    with pytest.raises(ValueError, match='not evaluated'):
        s.reserve_next('e', 'early')
    complete_query(s, picks)
    with pytest.raises(ValueError, match='duplicate entity'):
        s.reserve_next('e', 'duplicate')
    picks.append(picks[0] | {'occurrence_id': 'third', 'content_key': 'other'})
    with pytest.raises(ValueError, match='quota'):
        s.reserve_next('e', 'quota')
    assert s.load('e', p['plan_digest'])['state']['total_reserved'] == 0


def test_capture_and_partial_evaluation_resume_are_immutable(tmp_path):
    p = plan()
    s = Store(tmp_path / 'state.sqlite')
    s.create(p)
    s.record_query('e', 'q', 'capturing', [], 'capturing', capture_account='account')
    occurrence = stored_occurrence(p, 'one')
    raw = {'account_id': 'account', 'occurrences': [occurrence]}
    s.record_query('e', 'q', 'captured', [], 'captured', capture=raw)
    s.record_evaluation('e', 'q', 'one', 'risk', occurrence['risk'], 'risk')
    with pytest.raises(ValueError):
        s.record_evaluation('e', 'q', 'one', 'risk', occurrence['risk'] | {'input_ref': 'changed'}, 'different-risk')
    loaded = Store(tmp_path / 'state.sqlite').load('e', p['plan_digest'])
    assert loaded['queries']['q']['capture'] == raw
    assert loaded['queries']['q']['capture_account'] == 'account'
    assert loaded['queries']['q']['evaluations']['one']['risk'] == occurrence['risk']


def test_auth_replacement_and_no_account_terminal_state_survive_restart(tmp_path, monkeypatch):
    s, p, _ = prepared(tmp_path, monkeypatch)
    s.reserve_next('e', 'r')
    a = s.begin_attempt('e', '0', 'a', 'failed-account')
    s.fail_attempt('e', a['attempt_id'], 'auth', 'failure')
    assert s.load('e', p['plan_digest'])['reservations']['1']['status'] == 'cancelled'
    assert Store(tmp_path / 'state.sqlite').reserve_next('e', 'missing-rotation')['reasons'] == ['auth_retry_requires_account']
    with pytest.raises(ValueError):
        s.record_account('e', 'q', 'failed-account', 'bad-account')
    s.record_account('e', 'q', 'replacement', 'account')
    restarted = Store(tmp_path / 'state.sqlite')
    assert restarted.load('e', p['plan_digest'])['queries']['q']['detail_account'] == 'replacement'
    restarted.record_account('e', 'q', None, 'no-account')
    assert restarted.reserve_next('e', 'new-reserve')['status'] == 'blocked'
    assert restarted.load('e', p['plan_digest'])['fatal_error']['kind'] == 'auth_no_replacement'


def test_snapshot_digest_rejects_modified_cache(tmp_path, monkeypatch):
    import json
    s, p, _ = prepared(tmp_path, monkeypatch)
    with sqlite3.connect(s.path) as db:
        snap = json.loads(db.execute('SELECT snapshot FROM scoped_selection_executions').fetchone()[0])
        snap['queries']['q']['occurrences'][0]['content_key'] = 'tampered'
        db.execute('UPDATE scoped_selection_executions SET snapshot=?', (json.dumps(snap),))
    with pytest.raises(ValueError, match='integrity'):
        s.load('e', p['plan_digest'])
    with pytest.raises(ValueError, match='integrity'):
        s.reserve_next('e', 'after-tamper')


@pytest.mark.parametrize('changed', ['post', 'comments', 'identity'])
def test_risk_write_and_query_barrier_bind_immutable_capture(tmp_path, changed):
    from copy import deepcopy
    from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
    p = fixture_plan(queries=('A',), task_limit=1)
    old = fixture_occurrence(p, 'A', 'x')
    changes = {'post': {'desc': '克拉玛依欠薪新原文'},
               'comments': {'comments': [{'comment_id': 'c', 'text': '新评论'}]},
               'identity': {'enterprise_verify_reason': '公安官方'}}
    current = fixture_occurrence(p, 'A', 'x', **changes[changed])
    s = Store(tmp_path/'bound.sqlite')
    s.create(p)
    s.record_query(p['execution_id'], 'A', 'capturing', [], 'start')
    captured = {'account_id': 'capture', 'occurrences': [current]}
    s.record_query(p['execution_id'], 'A', 'captured', [], 'capture', capture=captured)
    with pytest.raises(ValueError, match='risk_capture_evidence_mismatch'):
        s.record_evaluation(p['execution_id'], 'A', current['occurrence_id'], 'risk', old['risk'], 'stale')
    assert s.load(p['execution_id'], p['plan_digest'])['queries']['A']['evaluations'] == {}
    s.record_evaluation(p['execution_id'], 'A', current['occurrence_id'], 'risk', current['risk'], 'valid')
    # Even a self-consistent old candidate/risk/geo tuple must not replace the saved capture at completion.
    with pytest.raises(ValueError, match='captured_occurrence_mismatch'):
        s.record_query(p['execution_id'], 'A', 'evaluated', [deepcopy(old)], 'stale-barrier')
    swapped = deepcopy(current)
    swapped['risk'] = deepcopy(old['risk'])
    with pytest.raises(ValueError, match='risk_capture_evidence_mismatch'):
        s.record_query(p['execution_id'], 'A', 'evaluated', [swapped], 'stale-risk-barrier')
    assert s.load(p['execution_id'], p['plan_digest'])['queries']['A']['status'] == 'captured'
    assert s.reserve_next(p['execution_id'], 'not-complete')['status'] == 'waiting'
    s.record_query(p['execution_id'], 'A', 'evaluated', [current], 'valid-barrier')
    assert s.reserve_next(p['execution_id'], 'complete')['status'] != 'blocked'


@pytest.mark.parametrize('stage', ['captured', 'evaluated'])
def test_recomputed_snapshot_digest_does_not_authorize_old_risk_cache(tmp_path, stage):
    import json
    from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
    p = fixture_plan(queries=('A',), task_limit=1)
    old = fixture_occurrence(p, 'A', 'x')
    current = fixture_occurrence(p, 'A', 'x', enterprise_verify_reason='公安官方')
    s = Store(tmp_path/'cache.sqlite')
    s.create(p)
    eid, oid = p['execution_id'], current['occurrence_id']
    s.record_query(eid, 'A', 'capturing', [], 'start')
    s.record_query(eid, 'A', 'captured', [], 'capture',
                   capture={'account_id': 'capture', 'occurrences': [current]})
    s.record_evaluation(eid, 'A', oid, 'risk', current['risk'], 'valid')
    if stage == 'evaluated':
        s.record_query(eid, 'A', 'evaluated', [current], 'complete')
    snapshot = s.load(eid, p['plan_digest'])
    snapshot['queries']['A']['evaluations'][oid]['risk'] = old['risk']
    if stage == 'evaluated':
        snapshot['queries']['A']['occurrences'][0]['risk'] = old['risk']
    snapshot['snapshot_digest'] = digest({k: v for k, v in snapshot.items() if k != 'snapshot_digest'})
    with sqlite3.connect(s.path) as db:
        db.execute('UPDATE scoped_selection_executions SET snapshot=? WHERE execution_id=?',
                   (json.dumps(snapshot), eid))
    with pytest.raises(ValueError, match='risk_capture_evidence_mismatch'):
        Store(s.path).load(eid, p['plan_digest'])


def test_query_completion_without_immutable_capture_is_rejected(tmp_path):
    from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
    p = fixture_plan(queries=('A',))
    s = Store(tmp_path/'uncaptured.sqlite')
    s.create(p)
    with pytest.raises(ValueError, match='immutable capture required'):
        s.record_query(p['execution_id'], 'A', 'evaluated', [fixture_occurrence(p, 'A', 'x')], 'bypass')
