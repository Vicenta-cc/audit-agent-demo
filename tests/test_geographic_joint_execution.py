"""Real frozen P2 plans drive the unchanged P3/P4 offline execution modules."""
from copy import deepcopy

import pytest

from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_draft_configuration import no_effects
from experiments.geographic_joint_integration.scenarios import make_real_bundle, JointHarness, row
from backend.audit_agent.crawler_adapter import CrawlerVerificationError, CrawlerMediaDownloadError, CrawlerRateLimitError


def harness(stack, content, tmp_path, case, rows, **options):
    bundle, expected, authority = make_real_bundle(stack, content, tmp_path, case, **options)
    return JointHarness(bundle, expected, tmp_path / 'p4.sqlite3', rows), authority


def details(h):
    return [call[1] for call in h.calls if call[0] == 'detail']


@pytest.mark.parametrize('formal_rules,formal_lexicon,ui', [
    (False, False, False), (True, False, True), (False, True, True), (True, True, False)])
def test_real_resource_combinations_late_local_beats_early_province(
        creation_stack, content, tmp_path, formal_rules, formal_lexicon, ui):
    case = f'resources-{int(formal_rules)}-{int(formal_lexicon)}-{int(ui)}'
    h, authority = harness(creation_stack, content, tmp_path, case,
        {0: [row('early', '新疆欠薪', band='strong', place='新疆')], 1: [row('late')]},
        formal_rules=formal_rules, formal_lexicon=formal_lexicon, ui=ui)
    result = h.runner().run()
    assert details(h) == ['late']
    assert result['collected'] == result['handed_off'] == 1
    assert all(q['max_notes'] == 1 for q in h.plan['queries'])
    last_capture = max(i for i, call in enumerate(h.calls) if call[0] == 'capture')
    first_detail = next(i for i, call in enumerate(h.calls) if call[0] == 'detail')
    assert last_capture < first_detail
    assert authority['configuration']['relevance_scope_state']['source_kind'] == ('ui_selection' if ui else 'conversation')
    no_effects(creation_stack)
    h.evidence(case, result, source_kind=authority['configuration']['relevance_scope_state']['source_kind'])


@pytest.mark.parametrize('case,rows,expected', [
    ('same-tier-query-order', {0: [row('early-weak')], 1: [row('late-strong', band='strong')]}, ['early-weak']),
    ('original-rank-order', {0: [row('rank-two', rank=2), row('rank-one', rank=1)]}, ['rank-two']),
    ('province-before-ip', {0: [row('ip-strong', '投诉未解决', band='strong', place=None, author_ip_location='新疆')],
                            1: [row('province-weak', '新疆欠薪', place='新疆')]}, ['province-weak']),
    ('same-entity-later-query', {0: [row('Y', rank=2), row('X', rank=1)], 1: [row('X')]}, ['Y', 'X']),
])
def test_order_and_actual_query_ownership(creation_stack, content, tmp_path, case, rows, expected):
    h, _ = harness(creation_stack, content, tmp_path, case, rows, task_limit=len(expected))
    result = h.runner().run()
    assert details(h) == expected
    assert result['collected'] == result['handed_off'] == len(expected)
    assert h.runner().snapshot()['state']['total_reserved'] == 0
    h.evidence(case, result, expected_selected=expected)


@pytest.mark.parametrize('case,target,policy,entry,count', [
    ('non-xinjiang-ip-strong', '深圳市', 'local_first_with_supplement',
     row('guangdong-ip', '投诉未解决', band='strong', place=None, author_ip_location='广东'), 1),
    ('non-xinjiang-ip-weak', '深圳市', 'local_first_with_supplement',
     row('guangdong-ip', '投诉未解决', place=None, author_ip_location='广东'), 0),
    ('local-only-excludes-province', '克拉玛依', 'local_only', row('province', '新疆欠薪', place='新疆'), 0),
    ('province-target-local', '广东省', 'local_first_with_supplement', row('shenzhen', '深圳市欠薪', place='深圳市'), 1),
    ('municipality-target', '北京市', 'local_first_with_supplement', row('beijing', '北京市欠薪', place='北京市'), 1),
    ('explicit-other-city', '克拉玛依', 'local_first_with_supplement', row('other', '乌鲁木齐市欠薪', band='strong', place='乌鲁木齐市'), 0),
])
def test_frozen_geographic_policy(creation_stack, content, tmp_path, case, target, policy, entry, count):
    h, _ = harness(creation_stack, content, tmp_path, case, {0: [entry]}, target=target, policy=policy)
    result = h.runner().run()
    assert result['collected'] == result['handed_off'] == count
    h.evidence(case, result, expected_success=count)


@pytest.mark.parametrize('stage', ['after_capture', 'after_risk', 'after_geo', 'after_reserve',
                                  'after_receipt', 'after_settle', 'after_delivery'])
def test_recreate_from_frozen_plan_preserves_progress(creation_stack, content, tmp_path, stage):
    case = 'restore-' + stage
    h, _ = harness(creation_stack, content, tmp_path, case, {0: [row('x')]})
    h.crash_at = stage
    with pytest.raises(RuntimeError, match='joint interruption'):
        h.runner().run()
    result = h.runner().run()
    assert result['collected'] == result['handed_off'] == 1
    assert details(h) == ['x']
    assert len([c for c in h.calls if c[0] == 'risk']) == 1
    assert len(h.deliveries) == 1
    h.evidence(case, result, interrupted_stage=stage)


def test_unknown_result_requires_trusted_reconciliation(creation_stack, content, tmp_path):
    case = 'restore-unknown-result'
    h, _ = harness(creation_stack, content, tmp_path, case, {0: [row('x')]})
    h.crash_at = 'after_detail'
    with pytest.raises(RuntimeError):
        h.runner().run()
    waiting = h.runner().run()
    assert waiting['reasons'] == ['awaiting_reconciliation']
    snapshot = h.runner().snapshot()
    assert snapshot['state']['total_reserved'] == 1
    attempt = next(iter(snapshot['attempts'].values()))
    runner = h.runner()
    receipt = runner.collection_receipt(attempt, h.effects[attempt['attempt_id']])
    runner.reconcile(attempt['attempt_id'], receipt, receipt_id='fixture:trusted-receipt')
    result = h.runner().run()
    assert result['collected'] == result['handed_off'] == 1
    assert details(h) == ['x']
    h.evidence(case, result, waiting=waiting, trusted_receipt=receipt)


@pytest.mark.parametrize('mutation', ['post', 'comments', 'identity', 'tier'])
def test_detail_rechecks_geography_retains_original_risk(creation_stack, content, tmp_path, mutation):
    case = 'detail-change-' + mutation
    h, _ = harness(creation_stack, content, tmp_path, case, {0: [row('x')]})
    changes = {'post': {'item': {'desc': '克拉玛依市新的投诉'}},
               'comments': {'comments': [{'comment_id': 'new', 'content': '变化评论'}]},
               'identity': {'item': {'enterprise_verify_reason': '公安官方'}},
               'tier': {'item': {'desc': '新疆新的投诉'}, 'geo_response': {
                   'status': 'located', 'locations': [{'field': 'desc', 'place': '新疆'}]}}}
    h.review_change = changes[mutation]
    result = h.runner().run()
    assert result['collected'] == 1
    assert result['handed_off'] == (0 if mutation == 'tier' else 1)
    assert len([c for c in h.calls if c[0] == 'risk']) == 1
    snapshot = h.runner().snapshot()
    assert snapshot['state']['total_success'] == 1
    assert snapshot['state']['total_reserved'] == 0
    if mutation != 'tier':
        assert next(i for i,c in enumerate(h.calls) if c[0]=='review') < next(i for i,c in enumerate(h.calls) if c[0]=='deliver')
    h.evidence(case, result)


@pytest.mark.parametrize('mutation', ['post', 'comments', 'identity'])
def test_stale_risk_same_occurrence_rejected_before_collection(creation_stack, content, tmp_path, mutation):
    case = 'old-risk-' + mutation
    h, _ = harness(creation_stack, content, tmp_path, case, {0: [row('x')]})
    qid = h.plan['queries'][0]['query_id']
    old = h.occurrences[qid][0]
    stale = h.adapter.risk(old, h.inputs[old['occurrence_id']]['risk_response'])
    item = deepcopy(old['candidate']['raw_source']['post'])
    comments = []
    if mutation == 'post':
        item['desc'] = '克拉玛依市全新原文'
    elif mutation == 'comments':
        comments = [{'comment_id': 'new', 'content': '全新评论'}]
    else:
        item['enterprise_verify_reason'] = '公安官方'
    current = h.adapter.occurrence(qid, 'x', 1, item, comments)
    assert current['occurrence_id'] == old['occurrence_id']
    actual = h.adapter.risk(current, h.inputs[old['occurrence_id']]['risk_response'])
    if mutation == 'identity':
        assert stale['score']['band'] == 'weak' and actual['score']['band'] == 'discard'
    h.occurrences[qid] = [current]
    h.risk_override[current['occurrence_id']] = stale
    with pytest.raises(ValueError, match='risk_capture_evidence_mismatch'):
        h.runner().run()
    snapshot = h.runner().snapshot()
    assert snapshot['queries'][qid]['evaluations'] == {}
    assert snapshot['state']['total_reserved'] == snapshot['state']['total_success'] == 0
    assert not details(h) and not h.deliveries
    from backend.audit_agent.scoped_selection_store import Store
    barrier = Store(tmp_path / 'barrier.sqlite3')
    eid = h.plan['execution_id']
    barrier.create(h.plan)
    barrier.record_query(eid, qid, 'capturing', [], 'barrier-start')
    barrier.record_query(eid, qid, 'captured', [], 'barrier-capture',
        capture={'account_id': 'fixture:captured', 'occurrences': [current]},
        capture_account='fixture:captured')
    mixed = {**current, 'risk': stale,
             'geo': h.adapter.geo(current['candidate'], h.inputs[current['occurrence_id']]['geo_response'])}
    with pytest.raises(ValueError, match='risk_capture_evidence_mismatch'):
        barrier.record_query(eid, qid, 'evaluated', [mixed], 'barrier-complete')
    assert barrier.load(eid, h.plan['plan_digest'])['queries'][qid]['status'] == 'captured'
    h.evidence(case, rejected='risk_capture_evidence_mismatch', current_risk=actual, old_risk=stale,
               query_barrier_rejected=True)


@pytest.mark.parametrize('failure', ['empty', 'media', 'authentication', 'rate_limit'])
def test_failure_types_keep_original_budget_semantics(creation_stack, content, tmp_path, failure):
    case = 'failure-' + failure
    h, _ = harness(creation_stack, content, tmp_path, case,
                   {0: [row('x', rank=2), row('z', rank=1)]})
    h.failure['x'] = {'empty': None, 'media': CrawlerMediaDownloadError('fixture media'),
        'authentication': CrawlerVerificationError('fixture verification'),
        'rate_limit': CrawlerRateLimitError('fixture rate limit')}[failure]
    if failure == 'rate_limit':
        with pytest.raises(CrawlerRateLimitError):
            h.runner().run()
        result = h.runner().run()
        assert result['reasons'] == ['awaiting_reconciliation', 'collection_failed']
        assert h.runner().snapshot()['state']['total_reserved'] == 1
        assert details(h) == ['x']
    else:
        result = h.runner().run()
        assert result['collected'] == (1 if failure == 'authentication' else 0)
        assert details(h) == (['x', 'z'] if failure == 'authentication' else ['x'])
        if failure == 'authentication':
            assert len([c for c in h.calls if c[0] == 'risk']) == 2
            assert len([c for c in h.calls if c[0] == 'rotate']) == 1
            qid = h.plan['queries'][0]['query_id']
            assert h.runner().snapshot()['queries'][qid]['auth_retries'] == 1
            assert h.runner().run()['collected'] == 1
            assert details(h) == ['x', 'z']
            assert len([c for c in h.calls if c[0] == 'rotate']) == 1
    h.evidence(case, result)


def test_pause_empty_and_failed_query_do_not_open_supplement(creation_stack, content, tmp_path):
    case = 'pause-and-empty'
    h, _ = harness(creation_stack, content, tmp_path, case, {})
    h.stop_now = True
    paused = h.runner().run()
    assert 'paused' in paused['reasons'] and len(paused['unfinished_queries']) == 4
    h.stop_now = False
    result = h.runner().run()
    assert not result['unfinished_queries'] and 'no_candidates' in result['reasons']
    assert not details(h)
    h.evidence(case, result, paused=paused)


def test_independent_process_resumes_saved_reservation(creation_stack, content, tmp_path):
    import json
    from pathlib import Path
    import subprocess
    import sys
    case = 'independent-process-reserve'
    h, _ = harness(creation_stack, content, tmp_path, case, {0: [row('x')]})
    h.crash_at = 'after_reserve'
    with pytest.raises(RuntimeError):
        h.runner().run()
    assert not details(h)
    payload = tmp_path / 'case.json'
    external = tmp_path / 'expected.json'
    payload.write_text(json.dumps({'case_id': case, 'bundle': h.bundle, 'rows': h.rows}))
    external.write_text(json.dumps(h.expected))
    command = [sys.executable, '-m', 'experiments.geographic_joint_integration.replay',
               '--case', str(payload), '--expected', str(external), '--state-db', str(h.db_path)]
    proc = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    evidence = json.loads(proc.stdout)
    assert evidence['result']['collected'] == evidence['result']['handed_off'] == 1
    assert [c[1] for c in evidence['calls'] if c[0] == 'detail'] == ['x']
    assert not [c for c in evidence['calls'] if c[0] in {'capture', 'risk', 'geo'}]
    assert evidence['before_state']['total_reserved'] == 1
    h.evidence(case, evidence['result'], child_command=command, child_exit_code=proc.returncode,
               child_calls=evidence['calls'], child_binding=evidence['bundle']['binding'])


def test_restore_ignores_changed_live_resources_and_settings(creation_stack, content, tmp_path, monkeypatch):
    from test_unified_draft_configuration import P
    from test_geographic_draft_configuration import flow
    case = 'restore-no-live-reads'
    h, authority = harness(creation_stack, content, tmp_path, case, {0: [row('x')]})
    oid = h.occurrences[h.plan['queries'][0]['query_id']][0]['occurrence_id']
    occurrence = h.occurrences[h.plan['queries'][0]['query_id']][0]
    baseline_risk = h.adapter.risk(occurrence, h.inputs[oid]['risk_response'])
    baseline_geo = h.adapter.geo(occurrence['candidate'], h.inputs[oid]['geo_response'])
    h.crash_at = 'after_risk'
    with pytest.raises(RuntimeError):
        h.runner().run()
    app = creation_stack['app_service']
    identity = authority['identity']
    recall = authority['configuration']['investigation']['recall_plan']
    with app.store._connect() as conn:
        edit_id = conn.execute('SELECT edit_id FROM lexicon_edit_refs WHERE ref=?',
                               (recall['source_edit_ref'],)).fetchone()[0]
    app.resource_management.update(edit_id, 1, [{'operation': 'upsert_entry',
        'target_id': 'joint-term-0', 'values': {'term': '冻结后全新搜索词'}}],
        principal=P, session_id=identity['session_id'])
    new_rules = deepcopy(content)
    new_rules['categories'][0]['rules'][0]['adjudication_notes'] = '冻结后新的规则判定文字'
    app.update_ruleset_proposal(authority['configuration']['judgement']['proposal_id'],
        session_id=identity['session_id'], expected_version=1, content=new_rules)
    settings = app.resource_service.task_settings
    current = settings.get()
    settings.save({**current['parameters'], 'collect_comments': False, 'max_total_notes': 7},
                  expected_revision=current['revision'])
    flow(creation_stack, identity['session_id'], {'operation': 'update',
         'draft_id': identity['draft_id'], 'expected_revision': identity['revision'],
         'title': '冻结后修改标题'}, '只修改标题')
    def forbidden(*args, **kwargs):
        raise AssertionError('restoration attempted live resolver/settings read')
    monkeypatch.setattr(app.resource_service, 'resolve_confirmation', forbidden)
    monkeypatch.setattr(settings, 'get', forbidden)
    result = h.runner().run()
    assert result['collected'] == result['handed_off'] == 1
    assert h.risks[oid] == baseline_risk
    assert h.geos[oid] == baseline_geo
    assert len([c for c in h.calls if c[0] == 'risk']) == 1
    h.evidence(case, result, live_reads_forbidden=True, changed_live=['rules', 'lexicon', 'settings', 'draft'])


def test_fixed_p3_mixed_province_city_is_consumed_without_new_filter(creation_stack, content, tmp_path):
    case = 'p3-known-mixed-province-city'
    entry = row('mixed', '新疆乌鲁木齐市欠薪', place='新疆')
    entry['geo_response'] = {'status': 'located', 'locations': [
        {'field': 'desc', 'place': '新疆'}, {'field': 'desc', 'place': '乌鲁木齐市'}]}
    h, _ = harness(creation_stack, content, tmp_path, case, {0: [entry]})
    result = h.runner().run()
    assert next(iter(h.geos.values()))['result']['geo_tier'] == 'province_unknown'
    assert result['collected'] == result['handed_off'] == 1
    h.evidence(case, result, known_limitation='fixed P3 mixed province/city aggregation; not semantic acceptance')


def test_capture_failure_cannot_open_supplement(creation_stack, content, tmp_path):
    case = 'capture-failed-barrier'
    h, _ = harness(creation_stack, content, tmp_path, case,
        {0: [row('province', '新疆欠薪', band='strong', place='新疆')], 1: [row('local')]})
    capture = h.capture
    qid = h.plan['queries'][1]['query_id']
    def fail_later_query(plan, query):
        if query['query_id'] == qid:
            raise CrawlerRateLimitError('fixture interrupted second query')
        return capture(plan, query)
    h.capture = fail_later_query
    with pytest.raises(CrawlerRateLimitError):
        h.runner().run()
    result = h.runner().run()
    assert result['collected'] == 0 and qid in result['unfinished_queries']
    assert not details(h)
    assert h.runner().snapshot()['state']['total_reserved'] == 0
    h.evidence(case, result, rejected_failure='CrawlerRateLimitError')
