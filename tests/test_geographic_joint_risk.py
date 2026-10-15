"""Real P1 authority -> persisted P2 -> joint adapter request-level contracts."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.audit_agent import triage
from backend.audit_agent.config import settings
from backend.audit_agent.scoped_selection import eligible_tier
from experiments.geographic_execution_plan.authority import capture_fixture_authority
from experiments.geographic_execution_plan.storage import save_plan, load_plan
from test_geographic_execution_plan import make_authority, build, expected
from test_geographic_draft_configuration import change, flow, no_effects
from test_geographic_execution_plan_risk import ManualClient
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_unified_draft_configuration import P, prepare

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'experiments/geographic_joint_integration/artifacts/joint-20261015-01/INPUT_MANIFEST.json'
WORDS = ['克拉玛依市包过', '包过', '北京路欠薪', '克拉玛依市 包过']
MANUAL = {'suspicion': 'strong', 'risk_score': 85, 'reason': '人工固定响应', 'matched_terms': []}


@pytest.fixture(autouse=True)
def explicit_select_fixture(monkeypatch):
    # This is fixture setup before real P1 capture, never coercion in the adapter.
    monkeypatch.setattr(settings, 'triage_mode', 'select')


def evidence(case_id, **data):
    output = os.environ.get('GEO_JOINT_OUTPUT')
    if not output:
        return
    path = Path(output) / 'risk-contract-cases.json'
    result = json.loads(path.read_text()) if path.exists() else {
        'schema': 'p234-joint-risk-contract-evidence-v1', 'fixture_only': True,
        'model_semantic_quality_evidence': False, 'cases': {}}
    result['cases'][case_id] = data
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def recapture(stack, identity):
    return capture_fixture_authority(stack['app_service'], identity['draft_id'], principal=P,
        session_id=identity['session_id'], fixture_id='fixture:joint-risk-contract')


def bound(authority, tmp_path, name='risk'):
    from experiments.geographic_joint_integration.adapter import project_plan, BoundAdapter
    identity = expected(authority)
    plan = build(authority)
    path = tmp_path / (name + '.sqlite3')
    saved = save_plan(path, plan)
    restored = load_plan(path, expected_identity=identity, expected_digest=saved)
    pins = dict(expected_identity=identity, expected_p2_digest=saved,
        execution_id='fixture:joint-' + name,
        source_manifest_digest=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), manifest_path=MANIFEST)
    bundle = project_plan(restored, plan_path=path, authority=authority, **pins)
    adapter = BoundAdapter(bundle, expected_binding_digest=bundle['binding']['binding_digest'], **pins)
    assert adapter.p2 == plan == restored
    return adapter, bundle, pins


def scored(adapter, *, key='dy:one', rank=1, item=None, comments=(), response=None):
    query = adapter.plan['queries'][0]
    raw_item = deepcopy(item) if item is not None else {'title': '克拉玛依市收费咨询'}
    raw_item.setdefault('post_id', raw_item.pop('id', key.split(':')[-1]))
    raw_item.setdefault('source_keyword', query['query'])
    occurrence = adapter.occurrence(query['query_id'], key, rank, raw_item, comments=comments)
    occurrence['risk'] = adapter.risk(occurrence, MANUAL if response is None else response)
    return occurrence


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
def test_real_resolver_four_resources_request_and_score_parity(creation_stack, content, tmp_path,
        formal_rules, formal_lexicon):
    """Oracle constructs P1 engine from actual live resolver/store, never the adapter."""
    authority = make_authority(creation_stack, content, formal_rules=formal_rules,
        formal_lexicon=formal_lexicon, ui=formal_rules)
    app = creation_stack['app_service']
    draft = app.get_draft(authority['identity']['draft_id'], principal=P)
    with app.resource_service.confirmation_fence() as conn:
        resolution = app.resource_service.resolve_confirmation(draft, principal=P, resource_connection=conn)
    execution = resolution.execution
    categories = [str(v).strip() for v in execution.library_ids if str(v).strip()]
    if not categories and execution.lexicon_category:
        categories = [execution.lexicon_category.strip()]
    rules = dict(execution.prompt_profile_snapshot)
    client = ManualClient()
    baseline = triage.TriageEngine(app.resource_service.lexicon_store, client,
        model=settings.triage_model, max_comments=settings.triage_candidate_comments,
        request_timeout=settings.triage_request_timeout,
        max_followers_personal_verified=settings.triage_max_followers_personal_verified,
        max_followers_unverified=settings.triage_max_followers_unverified,
        official_verify_patterns=settings.triage_official_verify_patterns,
        **{name: getattr(settings, 'triage_' + name) for name in
           ('rule_score_high', 'rule_score_medium', 'rule_score_low', 'rule_score_cap')})
    terms = baseline.terms_for(categories)
    adapter, bundle, _ = bound(authority, tmp_path)
    frozen = adapter.p2.inputs.risk_inputs
    assert frozen['category_ids'] == categories
    assert frozen['rules'] == rules
    assert frozen['terms'] == [asdict(t) for t in terms]
    assert frozen['search_groups'] == baseline._search_groups_for(terms)
    assert bool(terms) == formal_lexicon
    source = adapter.p2.inputs.configuration.relevance_scope_state
    assert source.source_kind == ('ui_selection' if formal_rules else 'conversation')
    if formal_rules:
        assert not source.source_quote and not source.source_turn_id
    query = adapter.plan['queries'][0]['query']
    contexts = []
    for rank, comments, extra in [
        (1, [{'id': 'c', 'content': '维吾尔族文化 文化讨论'}], {}),
        (9, [{'id': 'c', 'content': '维吾尔族文化 文化讨论'}], {}),
        (7, [], {}), (2, [], {'enterprise_verify_reason': '公安官方'}),
        (3, [], {'follower_count': settings.triage_max_followers_unverified + 1}),
        (4, [], {'custom_verify': '认证', 'follower_count': settings.triage_max_followers_personal_verified + 1}),
    ]:
        contexts.append(dict(rank=rank, item={'post_id': 'same', 'title': '克拉玛依市 维吾尔族文化 维族文化 文化讨论 包过 欠薪',
            'source_keyword': query, **extra}, comments=comments))
    originals, records = [], []
    for row in contexts:
        originals.append(baseline.score('dy:same', row['rank'], row['item'], row['comments'], terms,
            search_keyword=query, rules=rules))
        records.append(scored(adapter, key='dy:same', **row)['risk'])
    expected_scores = [asdict(v) for v in originals]
    assert [r['score'] for r in records] == expected_scores
    actual_calls = [call for record in records for call in record['calls']]
    expected_calls = [dict(prompt=p, kwargs=k) for p, k in client.calls]
    assert actual_calls == expected_calls
    assert len(actual_calls) == 3
    assert all(rules['audit_goal'] in call['prompt'] for call in actual_calls)
    assert [v['band'] for v in expected_scores[-3:]] == ['discard'] * 3
    if formal_lexicon:
        assert any('不计分' in row['reason'] for row in expected_scores[:3])
    joint_scores = [triage.CandidateScore(**record['score']) for record in records]
    # Nested hit dictionaries are already serialized: compare ordered content/rank,
    # while full equality of every score/hit above proves the sorting inputs.
    assert [(s.content_key, s.rank) for s in triage.rank_candidates(joint_scores)] == [
        (s.content_key, s.rank) for s in triage.rank_candidates(originals)]
    no_effects(creation_stack)
    evidence(f'resources-rules-{formal_rules}-lexicon-{formal_lexicon}', binding=bundle['binding'],
        source=source.model_dump(mode='json'), categories=categories, frozen_risk=frozen,
        original_rules=rules, contexts=contexts, baseline_calls=expected_calls,
        joint_records=records, baseline_scores=expected_scores,
        original_order=[asdict(s) for s in triage.rank_candidates(originals)],
        assertions=dict(exact_requests=True, exact_results=True, actual_resolver=True,
            actual_terms_groups=True, identity_filters=True, same_rank_order=True))


def exact_authority(stack, body):
    first, ctx, manager, _, _, args = prepare(stack, body)
    lexicon = {'title': '联调原样搜索', 'entries': [
        {'id': 'exact-' + str(i), 'term': word, 'kind': 'main', 'platform': 'dy', 'match_type': '黑话词'}
        for i, word in enumerate(WORDS)] + [
        {'id': 'duplicate-source', 'term': WORDS[0], 'kind': 'main', 'platform': '全平台', 'match_type': '精确'}]}
    edit = manager.create_lexicon(lexicon, **ctx)
    args.update(platform='dy', lexicon_ref=edit['resource_ref'], scope_decision='apply',
        relevance_scope_change=change())
    result, _ = flow(stack, first['session_id'], args)
    return recapture(stack, dict(draft_id=result['data']['draft']['id'], session_id=first['session_id']))


def test_exact_four_queries_sources_and_region_changes_leave_risk_unchanged(creation_stack, content, tmp_path):
    authority = exact_authority(creation_stack, content)
    adapter, bundle, _ = bound(authority, tmp_path, 'original-target')
    assert [q['query'] for q in adapter.plan['queries']] == WORDS
    assert [q.query for q in adapter.p2.execution_queries] == WORDS
    assert {s.entry_id for s in adapter.p2.execution_queries[0].sources} == {'exact-0', 'duplicate-source'}
    first = scored(adapter)
    first['geo'] = adapter.geo(first['candidate'], {'status': 'located', 'locations': [
        {'field': 'title', 'place': '克拉玛依市'}]})
    adapter.validate_geo(adapter.plan, first['candidate'], first['geo'])
    identity = authority['identity']
    flow(creation_stack, identity['session_id'], dict(operation='update', draft_id=identity['draft_id'],
        expected_revision=identity['revision'], relevance_scope_change=change('深圳市', '改查深圳市', 'local_only')),
        '改查深圳市')
    changed = recapture(creation_stack, identity)
    second_adapter, second_bundle, _ = bound(changed, tmp_path, 'second-target')
    second = scored(second_adapter)
    second['geo'] = second_adapter.geo(second['candidate'], {'status': 'located', 'locations': [
        {'field': 'title', 'place': '克拉玛依市'}]})
    second_adapter.validate_geo(second_adapter.plan, second['candidate'], second['geo'])
    assert adapter.p2.execution_queries == second_adapter.p2.execution_queries
    assert first['risk']['input'] == second['risk']['input']
    assert first['risk']['calls'] == second['risk']['calls']
    assert first['risk']['score'] == second['risk']['score']
    assert eligible_tier(adapter.plan, first) == 'local'
    assert eligible_tier(second_adapter.plan, second) is None
    no_effects(creation_stack)
    evidence('exact-query-and-region-invariance', queries=[q.model_dump(mode='json') for q in adapter.p2.execution_queries],
        before_binding=bundle['binding'], after_binding=second_bundle['binding'], before=first, after=second,
        assertions=dict(exact_query_order_and_spaces=True, duplicate_sources=True, identical_risk_input_requests_scores=True,
            geographic_eligibility_changed=True))


@pytest.mark.parametrize('comments,limit', [(False, 75), (True, 0), (True, 75)])
def test_effective_parameter_zero_false_and_revision_zero(creation_stack, content, tmp_path, comments, limit):
    authority = make_authority(creation_stack, content)
    initial, _, _ = bound(authority, tmp_path, 'revision-zero')
    assert initial.p2.inputs.settings.revision == 0
    task_settings = creation_stack['app_service'].resource_service.task_settings
    saved = task_settings.get()
    task_settings.save({**saved['parameters'], 'collect_comments': comments, 'max_comments': limit,
        'max_total_notes': 7}, expected_revision=0)
    current = recapture(creation_stack, authority['identity'])
    adapter, bundle, _ = bound(current, tmp_path, 'saved-settings')
    collection = adapter.p2.collection
    assert collection.effective_parameters.max_notes == 1
    assert collection.candidate_comment_limit == (min(collection.effective_parameters.max_comments,
        adapter.p2.inputs.settings.limits.triage_candidate_comments) if comments else 0)
    assert collection.detail_comment_limit == (min(limit, adapter.p2.inputs.settings.limits.m3_comments_per_post) if comments else 0)
    assert adapter.p2.inputs.settings.saved.max_total_notes == 7
    assert adapter.plan['task_limit'] == collection.max_total_notes == min(
        collection.effective_parameters.max_total_notes,
        sum(q.per_keyword_limit for q in adapter.p2.execution_queries))
    assert all(q['max_notes'] == q2.per_keyword_limit == 1
        for q, q2 in zip(adapter.plan['queries'], adapter.p2.execution_queries))
    capture = adapter.plan['queries'][0]['capture_parameters']
    assert capture['candidate_count'] == collection.candidate_count
    assert capture['candidate_comment_limit'] == collection.candidate_comment_limit
    assert capture['detail_comment_limit'] == collection.detail_comment_limit
    assert capture['collect_comments'] == collection.collect_comments
    no_effects(creation_stack)
    evidence(f'parameters-{comments}-{limit}', binding=bundle['binding'],
        revision_zero=initial.p2.inputs.settings.model_dump(mode='json'),
        collection=collection.model_dump(mode='json'), projection=adapter.plan,
        assertions=dict(no_default_for_zero_false=True, revision_zero_preserved=True, effective_mapping=True))


@pytest.mark.parametrize('limit', [1, 10, 100, 101, 200])
def test_search_terms_cap_supported_range_reaches_projection(creation_stack, content, tmp_path, monkeypatch, limit):
    monkeypatch.setattr(settings, 'search_terms_max', limit)
    authority = make_authority(creation_stack, content)
    adapter, bundle, _ = bound(authority, tmp_path)
    assert adapter.p2.inputs.settings.limits.search_terms_max == limit
    assert [q['query'] for q in adapter.plan['queries']] == authority['resolution']['resolved_search_terms']
    evidence(f'search-max-{limit}', binding=bundle['binding'],
        settings=adapter.p2.inputs.settings.model_dump(mode='json'), queries=adapter.plan['queries'],
        assertions=dict(real_p1_settings=True, cap_preserved=True))


def test_restore_after_live_rules_words_settings_changed_never_reads_live(creation_stack, content, tmp_path, monkeypatch):
    from experiments.geographic_joint_integration.adapter import BoundAdapter
    authority = make_authority(creation_stack, content)
    adapter, bundle, pins = bound(authority, tmp_path)
    before = scored(adapter)
    before['geo'] = adapter.geo(before['candidate'], {'status': 'located', 'locations': [
        {'field': 'title', 'place': '克拉玛依市'}]})
    app = creation_stack['app_service']
    identity = authority['identity']
    manager = app.resource_management
    word_ref = adapter.p2.inputs.configuration.investigation.recall_plan.source_edit_ref
    with app.store._connect() as conn:
        edit_id = conn.execute('SELECT edit_id FROM lexicon_edit_refs WHERE ref=?', (word_ref,)).fetchone()[0]
    manager.update(edit_id, 1, [{'operation': 'upsert_entry', 'target_id': 'variant-1',
        'values': {'term': '冻结后新词'}}], principal=P, session_id=identity['session_id'])
    rule_id = adapter.p2.inputs.configuration.judgement.proposal_id
    manager.update(rule_id, 1, [{'operation': 'set_metadata', 'values': {'name': '冻结后新规则', 'audit_goal': '冻结后风险审核目标改变，不得进入原请求'}}],
        principal=P, session_id=identity['session_id'])
    settings_service = app.resource_service.task_settings
    saved = settings_service.get()
    settings_service.save({**saved['parameters'], 'collect_comments': False, 'max_total_notes': 20}, expected_revision=0)
    def forbidden(*args, **kwargs):
        pytest.fail('restoration attempted to read live resolver/settings/lexicon')
    monkeypatch.setattr(app.resource_service, 'resolve_confirmation', forbidden)
    monkeypatch.setattr(settings_service, 'get', forbidden)
    monkeypatch.setattr(app.resource_service.lexicon_store, 'triage_terms', forbidden)
    monkeypatch.setattr(app.resource_service.lexicon_store, 'search_word_groups', forbidden)
    del adapter
    restored = BoundAdapter(deepcopy(bundle), expected_binding_digest=bundle['binding']['binding_digest'], **pins)
    after = scored(restored)
    after['geo'] = restored.geo(after['candidate'], {'status': 'located', 'locations': [
        {'field': 'title', 'place': '克拉玛依市'}]})
    assert before == after
    assert eligible_tier(restored.plan, after) == 'local'
    no_effects(creation_stack)
    evidence('no-live-after-freeze', binding=bundle['binding'], before=before, after=after,
        assertions=dict(live_resources_really_mutated=True, live_read_failure_probes_installed=True,
            identical_risk_and_geo=True, eligibility_preserved=True))


def test_non_xinjiang_ip_and_comment_text_isolation(creation_stack, content, tmp_path):
    authority = make_authority(creation_stack, content)
    identity = authority['identity']
    flow(creation_stack, identity['session_id'], dict(operation='update', draft_id=identity['draft_id'],
        expected_revision=identity['revision'], relevance_scope_change=change('深圳市', '改查深圳市')), '改查深圳市')
    adapter, bundle, _ = bound(recapture(creation_stack, identity), tmp_path)
    assert adapter.p2.inputs.configuration.relevance_scope_state.province_code == '440000'
    cases = []
    for label, author_ip, comments, expected_tier in [
        ('author-guangdong', '广东', [], 'ip_only'), ('author-xinjiang', '新疆', [], 'unresolved'),
        ('ten-independent', '', [{'id': str(i), 'user_id': str(i), 'content': '评论克拉玛依不定位',
            'ip_location': '广东'} for i in range(10)], 'ip_only'),
        ('nine-independent', '', [{'id': str(i), 'user_id': str(i), 'content': '评论克拉玛依不定位',
            'ip_location': '广东'} for i in range(9)], 'unresolved'),
        ('duplicates', '', [{'id': str(i), 'user_id': 'same-user', 'content': '评论克拉玛依不定位',
            'ip_location': '广东'} for i in range(10)], 'unresolved'),
        ('share-at-sixty', '', [{'id': str(i), 'user_id': str(i), 'content': '评论不定位',
            'ip_location': '广东' if i < 6 else '新疆'} for i in range(10)], 'ip_only'),
        ('share-below-sixty', '', [{'id': str(i), 'user_id': str(i), 'content': '评论不定位',
            'ip_location': '广东' if i < 5 else '新疆'} for i in range(10)], 'unresolved'),
        ('coverage-at-half', '', [{'id': str(i), 'user_id': str(i), 'content': '评论不定位',
            'ip_location': '广东' if i < 10 else ''} for i in range(20)], 'ip_only'),
        ('coverage-below-half', '', [{'id': str(i), 'user_id': str(i), 'content': '评论不定位',
            'ip_location': '广东' if i < 10 else ''} for i in range(21)], 'unresolved'),
    ]:
        occurrence = scored(adapter, item={'id': 'one', 'title': '普通收费咨询', 'author_ip_location': author_ip},
            comments=comments)
        occurrence['geo'] = adapter.geo(occurrence['candidate'], {'status': 'no_location', 'locations': []})
        adapter.validate_geo(adapter.plan, occurrence['candidate'], occurrence['geo'])
        assert occurrence['geo']['result']['geo_tier'] == expected_tier
        assert eligible_tier(adapter.plan, occurrence) == ('ip_only' if expected_tier == 'ip_only' else None)
        assert '评论克拉玛依不定位' not in occurrence['geo']['request']['prompt']
        assert occurrence['geo']['request']['repairs'] == {'place_aliases': True, 'role_prompt': True}
        cases.append(dict(case=label, occurrence=occurrence))
    a = scored(adapter, item={'id': 'one', 'title': '普通收费咨询', 'author_ip_location': '广东'},
        comments=[{'id': 'c', 'user_id': 'u', 'content': '评论克拉玛依不定位', 'ip_location': '广东'}])
    b = scored(adapter, item={'id': 'one', 'title': '普通收费咨询', 'author_ip_location': '广东'},
        comments=[{'id': 'c', 'user_id': 'u', 'content': '评论深圳也不定位', 'ip_location': '广东'}])
    ga = adapter.geo(a['candidate'], {'status': 'no_location', 'locations': []})
    gb = adapter.geo(b['candidate'], {'status': 'no_location', 'locations': []})
    assert ga['request']['prompt'] == gb['request']['prompt']
    assert ga['request']['model_input_digest'] == gb['request']['model_input_digest']
    assert ga['evidence_digest'] != gb['evidence_digest']
    assert ga['result']['geo_tier'] == gb['result']['geo_tier'] == 'ip_only'
    for response in ({'suspicion': 'weak', 'reason': '人工弱'}, {'suspicion': 'none', 'reason': '人工无'},
            RuntimeError('manual risk failure')):
        row = scored(adapter, item={'id': 'one', 'title': '普通收费咨询', 'author_ip_location': '广东'}, response=response)
        row['geo'] = adapter.geo(row['candidate'], {'status': 'no_location', 'locations': []})
        assert eligible_tier(adapter.plan, row) is None
        cases.append(dict(case='ip-risk-' + row['risk']['score']['band'], occurrence=row))
    for label, response in [('invalid', {}), ('conflict', {'status': 'conflict', 'locations': []}),
            ('insufficient', {'status': 'insufficient_context', 'locations': []})]:
        row = scored(adapter, item={'id': 'one', 'title': '普通收费咨询', 'author_ip_location': '广东'})
        row['geo'] = adapter.geo(row['candidate'], response)
        assert row['geo']['result']['geo_tier'] != 'ip_only'
        if row['geo']['result']['evaluation_status'] == 'completed':
            assert eligible_tier(adapter.plan, row) is None
        else:
            with pytest.raises(ValueError, match='geo_evaluation_failed'):
                eligible_tier(adapter.plan, row)
        cases.append(dict(case='geo-' + label, occurrence=row))
    no_effects(creation_stack)
    evidence('non-xinjiang-ip-comment-isolation', binding=bundle['binding'], cases=cases,
        comment_change_before=ga, comment_change_after=gb,
        assertions=dict(frozen_guangdong_chain=True, unique_user_threshold=True, author_ip_without_comments=True,
            comment_text_excluded=True, raw_evidence_retained=True, ip_only_requires_valid_strong=True))


def test_ui_source_survives_real_title_rule_revision_and_rejects_bad_lineage(creation_stack, content, tmp_path):
    from test_geographic_execution_plan_resources import resource_draft
    from test_investigation_creation_conversation import _run_scripted_creation_turn
    session, draft_id, ctx, manager, rule, _ = resource_draft(creation_stack, content, ui=True)
    identity = dict(session_id=session, draft_id=draft_id)
    original = recapture(creation_stack, identity)
    new_content = deepcopy(content)
    new_content['name'] = '合法修订后规则'
    _run_scripted_creation_turn(creation_stack, session_id=session, content='改规则名称',
        client_message_id='joint-source-revision', actions=[('update_ruleset_proposal', {
            'proposal_id': rule['edit_id'], 'expected_version': 1, 'content': new_content})])
    revised = manager.get_edit(rule['edit_id'], **ctx)
    flow(creation_stack, session, dict(operation='update', draft_id=draft_id, expected_revision=3,
        title='合法改标题', ruleset_ref=revised['resource_ref']), '采用展示的新规则并改标题')
    authority = recapture(creation_stack, identity)
    adapter, bundle, _ = bound(authority, tmp_path)
    assert adapter.p2.identity.revision == 4
    assert adapter.p2.inputs.provenance.origin_revision == 3
    assert adapter.p2.inputs.configuration.relevance_scope_state.model_dump(mode='json') == original['configuration']['relevance_scope_state']
    assert adapter.p2.inputs.configuration.judgement.proposal_version == 2
    assert not adapter.p2.inputs.configuration.relevance_scope_state.source_quote
    errors = {}
    for key in ('owner', 'session_id', 'draft_id', 'revision', 'source_operation', 'source_revision', 'missing_rule'):
        bad = deepcopy(authority)
        if key in {'owner', 'session_id', 'draft_id'}:
            bad['identity'][key] = 'foreign'
        elif key == 'revision':
            bad['identity']['revision'] = 99
        elif key == 'source_operation':
            bad['provenance']['selection']['operation_id'] = 'foreign'
        elif key == 'source_revision':
            bad['provenance']['origin_revision'] = 99
        else:
            bad['resources']['ruleset_content'] = None
        with pytest.raises(ValueError) as failure:
            build(bad)
        errors[key] = str(failure.value)
    no_effects(creation_stack)
    evidence('source-preserved-after-title-and-rule-revision', binding=bundle['binding'],
        initial_source=original['configuration']['relevance_scope_state'],
        restored_source=adapter.p2.inputs.configuration.relevance_scope_state.model_dump(mode='json'),
        restored_revision=adapter.p2.identity.revision, rejected_mutations=errors,
        assertions=dict(real_title_and_rule_revision=True, source_preserved=True, ui_has_no_fabricated_quote=True,
            invalid_lineage_and_missing_resource_rejected=True))
