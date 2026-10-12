"""Unified model contract against real isolated stores and conversation recovery."""
from copy import deepcopy
import json
from unittest.mock import patch
import pytest

from backend.investigation_creation.configure import NAME, ConfigureDraftInput, LEGACY_NAMES
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import HermesToolExecutionIdentity, creation_tool_schemas
from backend.resource_management.contracts import ResourceError
from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn
from test_ruleset_proposal_presentation import content, run, evidence
from test_resource_lifecycle import lexicon

P = Principal('principal-a')


def prepare(stack, body, formal_rules=False, formal_lexicon=False):
    first = run(stack, body)
    ctx = dict(session_id=first['session_id'], principal=P)
    manager = stack['app_service'].resource_management
    rule = manager.get_edit(evidence(first)['proposal_id'], **ctx)
    word = manager.create_lexicon(lexicon(), **ctx)
    if formal_rules:
        saved = manager.save(rule['edit_id'], 1, 'new', operation_id='rule-save', **ctx)
        rule = manager.read('ruleset', saved['resource_id'], principal=P)
    if formal_lexicon:
        saved = manager.save(word['edit_id'], 1, 'new', operation_id='lex-save', **ctx)
        word = manager.read('lexicon', saved['resource_id'], principal=P)
    args = dict(operation='create', title='新调查', objective='调查招聘风险', platform='xhs',
                ruleset_ref=rule['resource_ref'], lexicon_ref=word['resource_ref'])
    return first, ctx, manager, rule, word, args


def call(stack, session, args, call_id='configure', message='用刚才的规则和词库创建调查', turn=None):
    conversation = stack['conversation']
    if turn is None:
        turn, _ = conversation.accept_message(session, content=message, principal=P,
                    client_message_id=call_id)
    service = stack['tool_service']
    identity = HermesToolExecutionIdentity(session, turn.id, call_id)
    service.begin_conversation_turn(session, turn.id)
    try:
        result = service.execute_with_identity(NAME, args, principal=P, identity=identity)
    finally:
        service.end_conversation_turn(session)
    return result, identity, turn


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
def test_four_sources_create_patch_preserve_and_replay(creation_stack, content, formal_rules, formal_lexicon):
    stack = creation_stack
    first, ctx, manager, rule, word, args = prepare(stack, content, formal_rules, formal_lexicon)
    result, identity, turn = call(stack, first['session_id'], args)
    assert result['status'] == 'ok', result
    draft = result['data']['draft']
    assert draft['configuration']['judgement']['strategy'] == ('existing_ruleset' if formal_rules else 'temporary_ruleset')
    assert stack['tool_service'].execute_with_identity(NAME, args, principal=P, identity=identity) == result
    original = deepcopy(draft['configuration'])
    changed, _, _ = call(stack, first['session_id'], dict(operation='update', draft_id=draft['id'],
        expected_revision=1, title='只改标题'), 'title', turn=turn)
    assert changed['status'] == 'ok', changed
    assert changed['data']['draft']['configuration'] == original
    changed2, _, _ = call(stack, first['session_id'], dict(operation='update', draft_id=draft['id'],
        expected_revision=2, objective='只改目标'), 'objective', turn=turn)
    assert changed2['status'] == 'ok', changed2
    assert changed2['data']['draft']['title'] == '只改标题'
    assert changed2['data']['draft']['configuration'] == original
    artifact = stack['conversation']._verified_artifact([], principal=P, adoption_turn=turn)
    assert artifact['draft_revision'] == 3


def test_combined_temporary_adoption_and_lexicon_patch_one_revision(creation_stack, content):
    stack = creation_stack
    first, ctx, manager, rule, word, args = prepare(stack, content)
    created = _run_scripted_creation_turn(stack, session_id=first['session_id'], content='就用这两份，创建吧',
                                         client_message_id='create', actions=[(NAME, args)])
    draft = created['turn'].public_artifact['draft']
    changed_body = deepcopy(content); changed_body['name'] = '修改后的规则'
    shown = _run_scripted_creation_turn(stack, session_id=first['session_id'], content='改个规则名称',
        client_message_id='edit', actions=[('update_ruleset_proposal', {'proposal_id': rule['edit_id'],
            'expected_version': 1, 'content': changed_body})])
    rule2 = manager.get_edit(rule['edit_id'], **ctx)
    word2 = manager.update(word['edit_id'], 1, [{'operation': 'upsert_entry', 'target_id': 'variant-1',
            'values': {'term': '新的搜索词'}}], **ctx)
    changed = _run_scripted_creation_turn(stack, session_id=first['session_id'], content='规则和词库都换成改好的，标题也换一下',
        client_message_id='adopt', actions=[(NAME, {'operation': 'update', 'draft_id': draft['id'],
            'expected_revision': 1, 'ruleset_ref': rule2['resource_ref'], 'lexicon_ref': word2['resource_ref'], 'title': '一起更新'})])
    result = changed['turn'].public_artifact['draft']
    assert result['current_revision'] == 2
    assert result['configuration']['judgement']['proposal_version'] == 2
    assert result['configuration']['investigation']['recall_plan']['terms'] == ['新的搜索词']
    assert result['title'] == '一起更新'
    with stack['creation_store']._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_draft_revisions WHERE draft_id=?', (draft['id'],)).fetchone()[0] == 2
    restored = stack['conversation'].get_workspace_state(first['session_id'], principal=P)
    assert restored.draft_artifact['draft_revision'] == 2


def test_unpresented_rules_do_not_partially_patch_title_or_lexicon(creation_stack, content):
    stack = creation_stack
    first, ctx, manager, rule, word, args = prepare(stack, content)
    result, identity, turn = call(stack, first['session_id'], args)
    original = result['data']['draft']
    new = stack['app_service'].create_ruleset_proposal(content, session_id=first['session_id'])
    unshown = manager.get_edit(new.proposal_id, **ctx)
    other = manager.create_lexicon({**lexicon(), 'title': '另一份'}, **ctx)
    failure, _, _ = call(stack, first['session_id'], dict(operation='update', draft_id=original['id'], expected_revision=1,
        title='不该出现', ruleset_ref=unshown['resource_ref'], lexicon_ref=other['resource_ref']), 'unshown', turn=turn)
    assert failure['error']['code'] == 'PROPOSAL_NOT_PRESENTED', failure
    assert stack['app_service'].get_draft(original['id'], principal=P).model_dump(mode='json') == original


@pytest.mark.parametrize('target', ['rule', 'lexicon'])
def test_stale_reference_and_wrong_kind_reject_without_draft(creation_stack, content, target):
    stack = creation_stack
    first, ctx, manager, rule, word, args = prepare(stack, content)
    if target == 'rule':
        manager.update(rule['edit_id'], 1, [{'operation': 'set_metadata', 'values': {'name': '新版'}}], **ctx)
    else:
        manager.update(word['edit_id'], 1, [{'operation': 'set_metadata', 'values': {'title': '新版'}}], **ctx)
    result, _, _ = call(stack, first['session_id'], args)
    assert result['error']['code'] == 'RESOURCE_REF_STALE', result
    with stack['creation_store']._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_drafts').fetchone()[0] == 0


@pytest.mark.parametrize('owner,session', [('other', None), ('principal-a', 'other-session')])
def test_rule_reference_not_permission_or_cross_session_grant(creation_stack, content, owner, session):
    from backend.resource_management.ruleset_refs import resolve
    first, ctx, manager, rule, _, _ = prepare(creation_stack, content)
    with pytest.raises(ResourceError, match='引用无效'):
        resolve(manager, rule['resource_ref'], principal=Principal(owner), session_id=session or first['session_id'], turn_id='none')


def test_no_competing_model_tools_or_inline_configuration():
    schemas = {s['name']: s for s in creation_tool_schemas()}
    assert NAME in schemas and not LEGACY_NAMES.intersection(schemas)
    encoded = json.dumps(schemas[NAME]['parameters'])
    for field in ['presentation_id', 'content_hash', 'configuration', 'enabled_main_terms']:
        assert field not in encoded
    with pytest.raises(ValueError):
        ConfigureDraftInput(operation='update', draft_id='d', expected_revision=1, title=None)


def test_committed_create_preview_failure_recovers_same_draft(creation_stack, content):
    first, ctx, manager, rule, word, args = prepare(creation_stack, content)
    app = creation_stack['app_service']
    with patch.object(app, 'get_draft_view', side_effect=RuntimeError('preview unavailable')):
        failed, identity, turn = call(creation_stack, first['session_id'], args)
    assert failed['error']['details']['write_status'] == 'COMMITTED', failed
    result = creation_stack['tool_service'].execute_with_identity(NAME, args, principal=P, identity=identity)
    assert result['status'] == 'ok', result
    with creation_stack['creation_store']._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_drafts').fetchone()[0] == 1
    assert creation_stack['conversation']._verified_artifact([], principal=P, adoption_turn=turn)['draft_revision'] == 1

@pytest.mark.parametrize('formal', [False, True])
def test_committed_update_response_failure_replays_without_second_revision(creation_stack, content, formal):
    first, ctx, manager, rule, word, args = prepare(creation_stack, content, formal, formal)
    result, _, turn = call(creation_stack, first['session_id'], args)
    draft = result['data']['draft']
    patch_args = dict(operation='update', draft_id=draft['id'], expected_revision=1, title='已提交的标题')
    app = creation_stack['app_service']
    with patch.object(app, 'get_draft_view', side_effect=RuntimeError('lost preview')):
        failed, identity, _ = call(creation_stack, first['session_id'], patch_args, 'update-lost', turn=turn)
    assert failed['error']['details']['write_status'] == 'COMMITTED', failed
    recovered = creation_stack['tool_service'].execute_with_identity(NAME, patch_args, principal=P, identity=identity)
    assert recovered['status'] == 'ok', recovered
    assert recovered['data']['draft']['current_revision'] == 2
    assert recovered['data']['draft']['title'] == '已提交的标题'
    assert recovered['data']['draft']['configuration'] == draft['configuration']
    assert creation_stack['conversation']._verified_artifact([], principal=P, adoption_turn=turn)['draft_revision'] == 2


def test_temporary_lexicon_parent_variant_projection_is_preserved(creation_stack, content):
    first, ctx, manager, rule, word, args = prepare(creation_stack, content)
    changed = manager.update(word['edit_id'], 1, [
        {'operation': 'upsert_entry', 'target_id': 'variant-1', 'values': {'enabled': False}}], **ctx)
    args['lexicon_ref'] = changed['resource_ref']
    result, _, _ = call(creation_stack, first['session_id'], args)
    assert result['status'] == 'ok', result
    plan = result['data']['draft']['configuration']['investigation']['recall_plan']
    assert plan['terms'] == changed['search_terms']
    from backend.resource_management.contracts import LexiconContent
    assert plan['lexicon_content'] == LexiconContent.model_validate(changed['content']).model_dump(mode='json')
    assert plan['terms'] != word['search_terms']


def test_save_receipts_supply_formal_refs_without_retargeting_temporary_refs(creation_stack, content):
    from backend.resource_management.ruleset_refs import resolve
    first, ctx, manager, rule, word, args = prepare(creation_stack, content)
    tool = creation_stack['tool_service']
    saved_rule = tool.execute('save_resource', {'edit_id':rule['edit_id'], 'expected_version':1, 'operation_id':'save-for-use'}, **ctx)
    saved_word = tool.execute('save_resource', {'edit_id':word['edit_id'], 'expected_version':1, 'operation_id':'save-lex-for-use'}, **ctx)
    assert saved_rule['resource_ref'] != rule['resource_ref']
    assert saved_word['resource_ref'] != word['resource_ref']
    assert 'recall_plan' not in saved_rule
    assert tool.execute('get_resource_save', {'operation_id':'save-for-use'}, **ctx)['resource_ref'] == saved_rule['resource_ref']
    args.update(ruleset_ref=saved_rule['resource_ref'], lexicon_ref=saved_word['resource_ref'])
    result, _, turn = call(creation_stack, first['session_id'], args)
    assert result['status'] == 'ok', result
    config = result['data']['draft']['configuration']
    assert config['judgement']['strategy'] == 'existing_ruleset'
    assert config['investigation']['recall_plan']['strategy'] == 'existing_lexicon'
    # Saving never upgrades old references behind the user's back.
    assert manager.get_edit(rule['edit_id'], **ctx)['resource_ref'] == rule['resource_ref']
    assert resolve(manager, rule['resource_ref'], **ctx, turn_id=turn.id)[1]


@pytest.mark.parametrize('swap', [True, False])
def test_wrong_resource_kind_is_not_accepted(creation_stack, content, swap):
    first, ctx, manager, rule, word, args = prepare(creation_stack, content)
    args['ruleset_ref' if swap else 'lexicon_ref'] = word['resource_ref'] if swap else rule['resource_ref']
    result, _, _ = call(creation_stack, first['session_id'], args)
    assert result['error']['code'] == 'RESOURCE_REF_INVALID', result
    with creation_stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT count(*) FROM investigation_drafts').fetchone()[0] == 0
