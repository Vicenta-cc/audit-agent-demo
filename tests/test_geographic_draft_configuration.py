"""P1 real SQLite tool/service checks; scripted actions are not AI evaluation."""
from copy import deepcopy
import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.investigation_creation.configure import NAME, ConfigureDraftInput
from backend.investigation_creation.contracts import UpdateDraftCommand
from backend.investigation_creation.errors import ConfigurationValidationError
from backend.investigation_creation.public_projection import public_draft
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import HermesToolExecutionIdentity
from test_geographic_scope_intent import catalog
from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn
from test_ruleset_proposal_presentation import content
from test_resource_lifecycle import lexicon
from test_unified_draft_configuration import P, prepare, call


QUOTE = '调查克拉玛依'


def test_geographic_prompt_uses_actual_readiness_and_execution_receipts():
    from backend.investigation_creation.configure import DESCRIPTION
    from backend.investigation_creation.conversation import CREATION_SYSTEM_PROMPT

    for prompt in (DESCRIPTION, CREATION_SYSTEM_PROMPT):
        assert '真实确认预览和工具预检回执为准' in prompt
        assert '未接通地域执行能力时如实报告阻断' in prompt
        assert '只有确认工具成功回执后才可声称任务已排队' in prompt
        assert '实际执行状态以运行回执为准' in prompt
        assert '保存地域本身不表示已开始限定采集' in prompt
        assert '地域执行尚未接通，不能启动地域调查' not in prompt
        assert '地域仅保存和展示，执行尚未接通' not in prompt


def change(target='克拉玛依', quote=QUOTE, policy='local_first_with_supplement'):
    return {'action': 'set', 'target_text': target, 'source_quote': quote, 'selection_policy': policy}


def flow(stack, session, args, message=QUOTE):
    result = _run_scripted_creation_turn(stack, session_id=session, content=message,
        client_message_id=uuid4().hex, actions=[(NAME, args)])
    assert result['agent'].results, result
    # The synchronous scripted helper bypasses executor delivery. Complete that
    # real production stage so GET events observes its durable terminal event.
    stack['executor']._append_terminal_event(result['turn'].id)
    return {'status': 'ok', 'data': result['agent'].results[0]}, result


def setup_geo(stack, content, **options):
    first, ctx, manager, rule, word, args = prepare(stack, content, **options)
    args['scope_decision'] = 'apply'
    args['relevance_scope_change'] = change()
    result, turn = flow(stack, first['session_id'], args)
    assert result['status'] == 'ok', result
    return first, ctx, manager, rule, word, args, result, turn


def stored(stack, draft_id):
    return stack['app_service'].get_draft(draft_id, principal=P)


def no_effects(stack):
    with stack['creation_store']._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_runs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM task_admissions').fetchone()[0] == 0
    import sqlite3
    with sqlite3.connect(stack['resource_db']) as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
def test_four_resource_combinations_set_preserve_and_resource_update(creation_stack, content, catalog, formal_rules, formal_lexicon):
    stack = creation_stack
    first, ctx, manager, rule, word, args, result, _ = setup_geo(stack, content,
        formal_rules=formal_rules, formal_lexicon=formal_lexicon)
    draft = result['data']['draft']
    original = stored(stack, draft['id'])
    assert original.configuration.relevance_scope.target.name == '克拉玛依市'
    assert original.configuration.relevance_scope_state.draft_id == draft['id']
    assert original.configuration.judgement.strategy == ('existing_ruleset' if formal_rules else 'temporary_ruleset')
    updated, _ = flow(stack, first['session_id'], {'operation': 'update', 'draft_id': draft['id'],
        'expected_revision': 1, 'title': '只改标题'}, '仅修改标题')
    assert updated['status'] == 'ok', updated
    assert stored(stack, draft['id']).configuration == original.configuration
    other = manager.create_lexicon({**lexicon(), 'title': '换词库'}, **ctx)
    updated, _ = flow(stack, first['session_id'], {'operation': 'update', 'draft_id': draft['id'],
        'expected_revision': 2, 'lexicon_ref': other['resource_ref']}, '换词库')
    assert updated['status'] == 'ok', updated
    after = stored(stack, draft['id'])
    assert after.configuration.relevance_scope == original.configuration.relevance_scope
    assert after.configuration.relevance_scope_state == original.configuration.relevance_scope_state
    assert after.current_revision == 3
    no_effects(stack)


def test_pending_clarification_clear_and_restart(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    previous = stored(stack, draft_id).configuration.relevance_scope
    for revision, geo_change, message, status in [
        (1, change('朝阳区', '改查朝阳区'), '改查朝阳区', 'pending'),
        (2, change('北京', '改查北京', 'local_only'), '改查北京', 'resolved'),
        (3, {'action': 'clear', 'source_quote': '不限制地域'}, '不限制地域', 'cleared'),
    ]:
        result, _ = flow(stack, first['session_id'], {'operation': 'update', 'draft_id': draft_id,
            'expected_revision': revision, 'relevance_scope_change': geo_change}, message)
        assert result['status'] == 'ok', result
        draft = stored(stack, draft_id)
        assert draft.configuration.relevance_scope_state.status == status
        if status == 'pending':
            assert draft.configuration.relevance_scope == previous
            assert not result['data']['confirmation_preview']['can_confirm']
        elif status == 'resolved':
            assert draft.configuration.relevance_scope.target.name == '北京市'
            assert draft.configuration.relevance_scope.selection_policy == 'local_only'
        else:
            assert draft.configuration.relevance_scope is None
        restarted = InvestigationCreationStore(stack['creation_store'].db_path)
        assert restarted.get_draft(draft_id, principal=P.id).configuration == draft.configuration
    assert not any(b['code'].startswith('RELEVANCE_SCOPE') for b in result['data']['confirmation_preview']['blockers'])


def test_province_city_ambiguity_and_explicit_choices_persist(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    for revision, text, expected, canonical in [
        (1, '新疆', 'resolved', '新疆维吾尔自治区'),
        (2, '吉林', 'pending', '新疆维吾尔自治区'),
        (3, '吉林省', 'resolved', '吉林省'),
        (4, '吉林市', 'resolved', '吉林市'),
        (5, '朝阳', 'resolved', '朝阳市'),
        (6, '朝阳区', 'pending', '朝阳市'),
    ]:
        message = '调查' + text
        result, _ = flow(stack, first['session_id'], {'operation': 'update', 'draft_id': draft_id,
            'expected_revision': revision, 'relevance_scope_change': change(text, message)}, message)
        draft = stored(stack, draft_id)
        assert draft.current_revision == revision + 1
        assert draft.configuration.relevance_scope_state.status == expected
        assert draft.configuration.relevance_scope.target.name == canonical
        assert not result['data']['confirmation_preview']['can_confirm']
        if text == '吉林':
            assert {c.name for c in draft.configuration.relevance_scope_state.candidates} == {'吉林省', '吉林市'}
        if text in {'新疆', '吉林省'}:
            assert draft.configuration.relevance_scope_state.target_level == 'province'
            assert draft.configuration.relevance_scope.target == draft.configuration.relevance_scope.province
        restarted = InvestigationCreationStore(stack['creation_store'].db_path)
        assert restarted.get_draft(draft_id, principal=P.id).configuration == draft.configuration
        no_effects(stack)


@pytest.mark.parametrize('bad_field', ['source', 'revision', 'unshown', 'stale_ref'])
def test_failed_combined_change_is_atomic(creation_stack, content, catalog, bad_field):
    stack = creation_stack
    first, ctx, manager, _, word, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    before = stored(stack, draft_id)
    args = {'operation': 'update', 'draft_id': draft_id, 'expected_revision': 1,
        'title': '不应提交', 'relevance_scope_change': change('朝阳区', '改查朝阳区')}
    if bad_field == 'source':
        args['relevance_scope_change']['source_quote'] = '其他用户未说过的原话'
    elif bad_field == 'revision':
        args['expected_revision'] = 99
    elif bad_field == 'unshown':
        proposal = stack['app_service'].create_ruleset_proposal(content, session_id=first['session_id'])
        args['ruleset_ref'] = manager.get_edit(proposal.proposal_id, **ctx)['resource_ref']
    else:
        args['lexicon_ref'] = word['resource_ref']
        manager.update(word['edit_id'], 1, [{'operation': 'set_metadata', 'values': {'title': '新版'}}], **ctx)
    result, _, _ = call(stack, first['session_id'], args, message='改查朝阳区')
    assert result['status'] == 'error', result
    assert stored(stack, draft_id) == before
    no_effects(stack)


def test_first_request_can_be_bound_at_later_resource_adoption(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content)
    earlier = _run_scripted_creation_turn(stack, session_id=first['session_id'], content=QUOTE,
        client_message_id='early-geo', actions=[])
    args['scope_decision'] = 'apply'
    args['relevance_scope_change'] = change()
    result, _ = flow(stack, first['session_id'], args, '采用刚才展示的规则和词库')
    assert result['status'] == 'ok', result
    state = stored(stack, result['data']['draft']['id']).configuration.relevance_scope_state
    assert state.source_turn_id == earlier['turn'].id
    assert state.source_user_message_id == earlier['turn'].user_message_id


@pytest.mark.parametrize('target', ['克拉玛依', '朝阳区'])
def test_receipt_replay_and_safe_tool_public_recovery(creation_stack, content, catalog, target):
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content)
    args['scope_decision'] = 'apply'
    args['relevance_scope_change'] = change(target, '调查' + target)
    result, identity, turn = call(stack, first['session_id'], args, message='调查' + target)
    assert result['status'] == 'ok', result
    assert stack['tool_service'].execute_with_identity(NAME, args, principal=P, identity=identity) == result
    draft_id = result['data']['draft']['id']
    assert stored(stack, draft_id).current_revision == 1
    artifact = stack['conversation']._verified_artifact([], principal=P, adoption_turn=turn)
    public = public_draft(stored(stack, draft_id)).model_dump(mode='json')
    for payload in [result, artifact, public]:
        encoded = json.dumps(payload, ensure_ascii=False)
        for private in ['source_quote', 'source_user_message_id', 'source_sequence', 'task_anchor_turn_id', 'catalog_version']:
            assert private not in encoded
    for payload in [artifact, public]:
        assert P.id not in json.dumps(payload, ensure_ascii=False)
    assert 'principal' not in result['data']['draft']['configuration']['relevance_scope_state']
    assert public['configuration']['relevance_scope_state']['status'] == ('pending' if target == '朝阳区' else 'resolved')


@pytest.mark.parametrize('target,platform', [('克拉玛依', 'dy'), ('朝阳区', 'dy'), ('克拉玛依', 'xhs')])
def test_direct_store_confirmation_rechecks_persisted_geography(creation_stack, content, catalog, target, platform):
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content)
    args.update(scope_decision='apply', platform=platform, relevance_scope_change=change(target, '调查' + target))
    result, _ = flow(stack, first['session_id'], args, '调查' + target)
    assert result['status'] == 'ok', result
    draft_id = result['data']['draft']['id']
    # Caller-supplied empty execution config cannot bypass persisted draft guards.
    with pytest.raises(ConfigurationValidationError) as error:
        stack['creation_store'].confirm_and_queue(draft_id, principal=P.id, expected_revision=1,
            confirmed=True, idempotency_key=uuid4().hex, request_fingerprint='fixture', resolved_configuration={})
    expected_code = 'RELEVANCE_SCOPE_PENDING' if target == '朝阳区' else 'RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE'
    assert error.value.code == expected_code
    from backend.investigation_creation.contracts import ConfirmAndQueueCommand
    with pytest.raises(ConfigurationValidationError) as service_error:
        stack['app_service'].confirm_and_queue(ConfirmAndQueueCommand(draft_id=draft_id,
            expected_revision=1, confirmed=True, idempotency_key=uuid4().hex), principal=P)
    assert service_error.value.code == expected_code
    response = stack['client'].post('/api/investigation-drafts/' + draft_id + '/confirm-and-queue',
        json={'expected_revision': 1, 'confirmed': True}, headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code == 400, response.text
    assert response.json()['detail']['code'] == expected_code
    no_effects(stack)


def test_api_omission_unchanged_roundtrip_and_tampering(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    client = stack['client']
    public = client.get('/api/investigation-drafts/' + draft_id).json()
    configuration = deepcopy(public['configuration'])
    omitted = deepcopy(configuration)
    omitted.pop('relevance_scope', None)
    omitted.pop('relevance_scope_state', None)
    response = client.patch('/api/investigation-drafts/' + draft_id,
        json={'expected_revision': 1, 'title': '兼容更新', 'configuration': omitted})
    assert response.status_code == 200, response.text
    assert stored(stack, draft_id).configuration.relevance_scope.target.name == '克拉玛依市'
    response = client.patch('/api/investigation-drafts/' + draft_id,
        json={'expected_revision': 2, 'configuration': configuration})
    assert response.status_code == 200, response.text
    before = stored(stack, draft_id)
    for key in ['relevance_scope', 'relevance_scope_state']:
        malicious = deepcopy(configuration)
        malicious[key] = None
        response = client.patch('/api/investigation-drafts/' + draft_id,
            json={'expected_revision': before.current_revision, 'configuration': malicious})
        assert response.status_code in (400, 409, 422), response.text
        assert stored(stack, draft_id) == before
    malicious = deepcopy(configuration)
    malicious['relevance_scope']['target']['name'] = '篡改地域'
    response = client.patch('/api/investigation-drafts/' + draft_id,
        json={'expected_revision': before.current_revision, 'configuration': malicious})
    assert response.status_code in (400, 409, 422), response.text
    assert stored(stack, draft_id) == before


def test_sql_failure_rolls_back_scope_and_title_together(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    before = stored(stack, draft_id)
    with patch.object(stack['creation_store'], '_bind_draft_write', side_effect=RuntimeError('injected after SQL')):
        result, _, _ = call(stack, first['session_id'], {'operation': 'update', 'draft_id': draft_id,
            'expected_revision': 1, 'title': '回滚标题', 'relevance_scope_change': change('朝阳区', '改查朝阳区')}, message='改查朝阳区')
    assert result['status'] == 'error', result
    assert stored(stack, draft_id) == before
    with stack['creation_store']._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_draft_revisions WHERE draft_id=?', (draft_id,)).fetchone()[0] == 1
    no_effects(stack)


def test_copy_preserves_verified_source_and_records_lineage(creation_stack, content, catalog, monkeypatch):
    from backend.audit_agent.config import settings
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    original = stored(stack, draft_id)
    # Isolated historical fixture: P1 never confirms/starts a geography task.
    with stack['creation_store']._connect() as db:
        db.execute("UPDATE investigation_drafts SET status='QUEUED', confirmed_revision=1, confirmed_by=?, confirmed_at=? WHERE id=?",
            (P.id, original.updated_at, draft_id))
    monkeypatch.setattr(settings, 'investigation_multi_round_enabled', True)
    result = stack['tool_service'].execute_with_identity('copy_investigation_draft',
        {'source_draft_id': draft_id}, principal=P,
        identity=HermesToolExecutionIdentity(first['session_id'], 'copy-runtime', 'copy-geo'))
    assert result['status'] == 'ok', result
    new_id = result['data']['draft']['id']
    new = stored(stack, new_id)
    assert new_id != draft_id
    assert new.configuration.relevance_scope == original.configuration.relevance_scope
    assert new.configuration.relevance_scope_state.draft_id == new_id
    assert new.configuration.relevance_scope_state.copied_from_draft_id == draft_id
    assert new.configuration.relevance_scope_state.source_user_message_id == original.configuration.relevance_scope_state.source_user_message_id
    no_effects(stack)


@pytest.mark.parametrize('source_kind', ['assistant', 'cross_session', 'runtime'])
def test_tool_rejects_non_user_or_wrong_turn_sources(creation_stack, content, catalog, source_kind):
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content, formal_rules=True, formal_lexicon=True)
    session = first['session_id']
    if source_kind == 'assistant':
        _run_scripted_creation_turn(stack, session_id=session, content='展示说明',
            client_message_id='assistant-quote', actions=[], final_response=QUOTE)
    elif source_kind == 'cross_session':
        _run_scripted_creation_turn(stack, content=QUOTE, client_message_id='other-session', actions=[])
    args['scope_decision'] = 'apply'
    args['relevance_scope_change'] = change()
    if source_kind == 'runtime':
        current, _ = stack['conversation'].accept_message(session, content=QUOTE,
            principal=P, client_message_id='runtime-mismatch')
        service = stack['tool_service']
        identity = HermesToolExecutionIdentity(session, 'runtime-turn', 'runtime-call')
        service.begin_conversation_turn(session, 'runtime-turn')
        try:
            result = service.execute_with_identity(NAME, args, principal=P, identity=identity)
        finally:
            service.end_conversation_turn(session)
    else:
        result, _, _ = call(stack, session, args, message='采用资源创建调查')
    assert result['status'] == 'error', result
    assert result['error']['code'] == 'REGION_SOURCE_INVALID', result
    with stack['creation_store']._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_drafts').fetchone()[0] == 0
    no_effects(stack)


def test_old_configuration_omits_new_fields_and_null_is_rejected(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content)
    result, _ = flow(stack, first['session_id'], args, '采用资源创建调查')
    assert result['status'] == 'ok', result
    draft_id = result['data']['draft']['id']
    original = stored(stack, draft_id)
    for key in ('relevance_scope', 'relevance_scope_state'):
        assert key not in original.configuration.model_dump(mode='json')
        assert key not in result['data']['draft']['configuration']
    with stack['creation_store']._connect() as db:
        raw = db.execute('SELECT configuration_json FROM investigation_drafts WHERE id=?', (draft_id,)).fetchone()[0]
        assert 'relevance_scope' not in raw
    result, _, _ = call(stack, first['session_id'], {'operation': 'update', 'draft_id': draft_id,
        'expected_revision': 1, 'relevance_scope_change': None}, message='不应以null清除')
    assert result['status'] == 'error', result
    assert stored(stack, draft_id) == original
    assert not any(b.code.startswith('RELEVANCE_SCOPE') for b in stack['app_service'].get_draft_view(draft_id, principal=P).confirmation_preview.blockers)


def test_completed_sse_and_workspace_public_projection(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, completed = setup_geo(stack, content)
    streamed = stack['client'].get('/api/investigation-workspace-turns/' + completed['turn'].id + '/events')
    assert streamed.status_code == 200, streamed.text
    events = [json.loads(line[6:]) for line in streamed.text.splitlines() if line.startswith('data: ')]
    assert events
    workspace = stack['client'].get('/api/investigation-workspaces/' + first['session_id'] + '/state')
    assert workspace.status_code == 200, workspace.text
    payloads = [completed['turn'].public_artifact, workspace.json(),
        [event.model_dump(mode='json') if hasattr(event, 'model_dump') else event for event in events]]
    def states(value):
        if isinstance(value, dict):
            if isinstance(value.get('relevance_scope_state'), dict):
                yield value['relevance_scope_state']
            for child in value.values():
                yield from states(child)
        elif isinstance(value, list):
            for child in value:
                yield from states(child)
    for payload in payloads:
        assert '克拉玛依市' in json.dumps(payload, ensure_ascii=False)
        projected = list(states(payload))
        assert projected, payload
        for state in projected:
            for key in ['principal', 'session_id', 'source_quote', 'source_user_message_id', 'source_sequence', 'task_anchor_turn_id']:
                assert key not in state


def test_competing_updates_preserve_revision_and_atomic_scope(creation_stack, content, catalog):
    from concurrent.futures import ThreadPoolExecutor
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    current, _ = stack['conversation'].accept_message(first['session_id'], content='改查北京',
        principal=P, client_message_id='concurrent')
    service = stack['tool_service']
    service.begin_conversation_turn(first['session_id'], current.id)
    try:
        def update(number):
            return service.execute_with_identity(NAME, {'operation': 'update', 'draft_id': draft_id,
                'expected_revision': 1, 'title': f'并发{number}',
                'relevance_scope_change': change('北京', '改查北京')}, principal=P,
                identity=HermesToolExecutionIdentity(first['session_id'], current.id, f'compete-{number}'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(update, [1, 2]))
    finally:
        service.end_conversation_turn(first['session_id'])
    assert sorted(r['status'] for r in outcomes) == ['error', 'ok'], outcomes
    after = stored(stack, draft_id)
    assert after.current_revision == 2
    assert after.configuration.relevance_scope.target.name == '北京市'
    assert after.title in ['并发1', '并发2']
    no_effects(stack)


def test_old_v4_serialized_bytes_and_hash_match_committed_baseline():
    from backend.investigation_creation.contracts import InvestigationDraftConfiguration, confirmed_configuration_hash
    # Captured by loading contracts.py from git a4997d3 and normalizing this exact
    # pre-geography payload; fixed bytes/hash guard against default-field drift.
    baseline = '{"investigation":{"mode":"search","recall_plan":{"source_lexicon_ids":[],"strategy":"temporary_terms","terms":["subject"]}},"judgement":{"expected_ruleset_content_hash":"0000000000000000000000000000000000000000000000000000000000000000","expected_ruleset_version":1,"ruleset_revision_id":"ruleset-revision:test:v1","strategy":"existing_ruleset"},"platform":"xhs","schema_version":"investigation-draft-config-v4"}'
    normalized = InvestigationDraftConfiguration.model_validate(json.loads(baseline)).model_dump(mode='json')
    assert json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':')) == baseline
    assert confirmed_configuration_hash(normalized) == 'd71cfb82220c23d7a13fc031af485d52a9da40a1a1e86e60fbdc544b09d85cf5'


def test_export_real_service_flow_evidence(creation_stack, content, catalog, tmp_path):
    import os
    from pathlib import Path
    stack = creation_stack
    first, _, _, _, _, initial_args = prepare(stack, content)
    initial_args['scope_decision'] = 'apply'
    initial_args['relevance_scope_change'] = change()
    entries = []
    draft_id = ''
    cases = [
        ('resolved', initial_args, QUOTE),
        ('pending', {'relevance_scope_change': change('朝阳区', '改查朝阳区')}, '改查朝阳区'),
        ('cleared', {'relevance_scope_change': {'action': 'clear', 'source_quote': '不限制地域'}}, '不限制地域'),
    ]
    for status, args, message in cases:
        before_revision = 0 if not draft_id else stored(stack, draft_id).current_revision
        if draft_id:
            args = {'operation': 'update', 'draft_id': draft_id, 'expected_revision': before_revision, **args}
        result, completed = flow(stack, first['session_id'], args, message)
        draft_id = result['data']['draft']['id']
        draft_response = stack['client'].get('/api/investigation-drafts/' + draft_id)
        preview_response = stack['client'].get('/api/investigation-drafts/' + draft_id + '/confirmation-preview')
        workspace_response = stack['client'].get('/api/investigation-workspaces/' + first['session_id'] + '/state')
        sse = stack['client'].get('/api/investigation-workspace-turns/' + completed['turn'].id + '/events')
        for response in (draft_response, preview_response, workspace_response, sse):
            assert response.status_code == 200, response.text
        public = draft_response.json()
        assert public['configuration']['relevance_scope_state']['status'] == status
        entries.append({'operation': status, 'input': args, 'before_revision': before_revision,
            'after_revision': public['current_revision'], 'public_draft': public,
            'confirmation_preview': preview_response.json(), 'workspace': workspace_response.json(),
            'sse': [json.loads(line[6:]) for line in sse.text.splitlines() if line.startswith('data: ')]})
    evidence = {'validation': 'programmatic real tool/service/SQLite/API chain; scripted model substitute',
        'catalog': 'Explicit ten-record unit-test fixture; NOT national coverage or production catalog',
        'model_requests': 0, 'collection_requests': 0, 'production_database_writes': 0,
        'city_target_policy': 'province-level regions and prefecture cities; ambiguity only among supported identities',
        'steps': entries}
    # Baseline proposal provenance already public is unrelated to this feature;
    # recursively redact private geography evidence if any accidentally appears.
    def redact(value):
        if isinstance(value, dict):
            return {key: redact(child) for key, child in value.items()
                if key not in {'source_user_message_id', 'source_sequence', 'task_anchor_turn_id', 'principal'}}
        if isinstance(value, list):
            return [redact(child) for child in value]
        return 'synthetic-fixture-owner' if value == P.id else value
    destination = Path(os.environ.get('GEOGRAPHIC_FLOW_EVIDENCE', str(tmp_path / 'service-flow.json')))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(redact(evidence), ensure_ascii=False, indent=2), encoding='utf-8')
    assert destination.stat().st_size > 100
    no_effects(stack)


def test_creator_geography_is_saved_but_cannot_start(creation_stack, content, catalog):
    from backend.investigation_creation.contracts import ConfirmAndQueueCommand
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content)
    args.pop('lexicon_ref')
    args['scope_decision'] = 'apply'
    args.update(creator_url=('https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753'
                             '?xsec_token=test-token&xsec_source=pc_user'),
        relevance_scope_change=change())
    result, _, _ = call(stack, first['session_id'], args, message=QUOTE)
    assert result['status'] == 'ok', result
    draft_id = result['data']['draft']['id']
    assert stored(stack, draft_id).configuration.investigation.mode == 'creator'
    codes = {blocker['code'] for blocker in result['data']['confirmation_preview']['blockers']}
    assert 'RELEVANCE_SCOPE_MODE_UNSUPPORTED' in codes
    with pytest.raises(ConfigurationValidationError) as error:
        stack['app_service'].confirm_and_queue(ConfirmAndQueueCommand(draft_id=draft_id,
            expected_revision=1, confirmed=True, idempotency_key=uuid4().hex), principal=P)
    assert error.value.code == 'RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE'
    no_effects(stack)


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
def test_create_apply_with_invalid_source_never_adopts_resources(
        creation_stack, content, catalog, formal_rules, formal_lexicon):
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content, formal_rules, formal_lexicon)
    args.update(scope_decision='apply', relevance_scope_change=change('克拉玛依', '并不存在的用户原话'))
    failed, _, _ = call(stack, first['session_id'], args, message='调查克拉玛依大火')
    assert failed['error']['code'] == 'REGION_SOURCE_INVALID', failed
    with stack['creation_store']._connect() as db:
        for table in ('investigation_drafts', 'investigation_draft_revisions', 'ruleset_proposal_approvals'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    no_effects(stack)
