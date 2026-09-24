"""Temporary/formal, edited/saved adoption must not require model content copying."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.investigation_creation.principal import Principal
from backend.investigation_creation.contracts import TemporaryTermsRecallPlan
from backend.investigation_creation.tools import HERMES_M3_TOOL_SCHEMAS, HermesToolExecutionIdentity
from backend.resource_management.contracts import ResourceError
from backend.resource_management.service import ResourceManagementService
from backend.resource_management.snapshot_refs import tool_view
from test_resource_lifecycle import service, lexicon, P, CTX, save
from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn, _t1_temporary_arguments
from test_ruleset_proposal_approval import approve


def test_edit_reference_survives_restart_and_save_but_not_edit(service):
    body = lexicon()
    body['entries'][1]['note'] = '含有 "双引号"、\\、换行\n与长备注' * 500
    # Keep within the real content contract while exercising JSON escaping.
    body['entries'][1]['note'] = body['entries'][1]['note'][:1500]
    edit = service.create_lexicon(body, **CTX)
    plan = tool_view(edit)['recall_plan']
    assert set(plan) == {'strategy', 'resource_ref'}
    assert len(json.dumps(plan)) < 120
    reopened = ResourceManagementService(service.app)
    expected = TemporaryTermsRecallPlan.model_validate(edit['recall_plan']).model_dump(mode='json')
    assert reopened.resolve_lexicon_ref(edit['resource_ref'], **CTX) == expected
    save(service, edit)
    assert service.resolve_lexicon_ref(edit['resource_ref'], **CTX) == expected
    changed = service.update(edit['edit_id'], 1, [{'operation': 'upsert_entry',
        'target_id': 'variant-1', 'values': {'term': '新的搜索词'}}], **CTX)
    assert changed['resource_ref'] != edit['resource_ref']
    with pytest.raises(ResourceError) as error:
        service.resolve_lexicon_ref(edit['resource_ref'], **CTX)
    assert error.value.code == 'RESOURCE_REF_STALE'
    assert service.resolve_lexicon_ref(changed['resource_ref'], **CTX)['terms'] == ['新的搜索词']


@pytest.mark.parametrize('context', [dict(session_id='other', principal=P),
                                    dict(session_id='session-a', principal=Principal('other'))])
def test_temporary_reference_is_not_a_cross_session_or_cross_user_grant(service, context):
    edit = service.create_lexicon(lexicon(), **CTX)
    with pytest.raises(ResourceError) as error:
        service.resolve_lexicon_ref(edit['resource_ref'], **context)
    assert error.value.code == 'RESOURCE_REF_INVALID'


def test_model_draft_schema_no_longer_exposes_full_lexicon_content():
    for name in ('create_investigation_draft', 'update_investigation_draft', 'use_ruleset_proposal'):
        tool = next(s for s in HERMES_M3_TOOL_SCHEMAS if s['name'] == name)
        schema = tool['parameters']
        assert 'lexicon_content' not in json.dumps(schema)
        assert 'LexiconContent' not in schema['$defs']
        assert 'resource_ref' in tool['description']
        assert '提交完整 lexicon_content' not in tool['description']
        assert '必须同时包含完整' not in tool['description']


@pytest.mark.parametrize('source', ['temporary', 'edited', 'saved', 'read', 'opened_edited'])
@pytest.mark.parametrize('rules', ['temporary', 'formal', 'just_saved'])
def test_full_source_matrix_at_real_tool_boundary(creation_stack, source, rules):
    stack = creation_stack
    principal = Principal('principal-a')
    rule_body = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    shown = _run_scripted_creation_turn(stack, content='生成规则并展示，不启动',
        actions=[('create_ruleset_proposal', {'content': rule_body})])
    ctx = dict(session_id=shown['session_id'], principal=principal)
    tool = stack['tool_service']
    manager = stack['app_service'].resource_management
    edit = tool.execute('create_lexicon_edit', {'content': lexicon()}, **ctx)
    selected = edit
    if source in {'saved', 'read', 'opened_edited'}:
        selected = tool.execute('save_resource', {'edit_id': edit['edit_id'], 'expected_version': 1,
            'operation_id': 'save-lex'}, **ctx)
        if source == 'read':
            selected = tool.execute('read_resource', {'kind': 'lexicon', 'resource_id': selected['resource_id']}, **ctx)
        elif source == 'opened_edited':
            edit = tool.execute('open_resource_edit', {'kind': 'lexicon', 'resource_id': selected['resource_id']}, **ctx)
    if source in {'edited', 'opened_edited'}:
        selected = tool.execute('update_resource_edit', {'edit_id': edit['edit_id'], 'expected_version': 1,
            'changes': [{'operation': 'upsert_entry', 'target_id': 'variant-1',
                         'values': {'term': '修改后的词', 'note': '保留 "引号"'}}]}, **ctx)
    assert set(selected['recall_plan']) == {'strategy', 'resource_ref'}
    args = _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = selected['recall_plan']
    if rules == 'temporary':
        args['configuration'].pop('judgement', None)
        args['configuration'].pop('schema_version', None)
        result, identity, _ = approve(stack, shown, arguments={'create_draft': args})
        name = 'use_ruleset_proposal'
        replay_args = {'presentation_id': shown['turn'].public_artifact['proposal_presentations'][0]['presentation_id'],
                       'create_draft': args}
    else:
        if rules == 'just_saved':
            proposal_id = shown['turn'].public_artifact['proposal_presentations'][0]['proposal_id']
            saved_rule = tool.execute('save_resource', {'edit_id': proposal_id, 'expected_version': 1,
                'operation_id': 'save-rules'}, **ctx)
            args['configuration']['judgement'] = {'strategy': 'existing_ruleset',
                'ruleset_revision_id': saved_rule['revision_id'], 'expected_ruleset_version': saved_rule['version'],
                'expected_ruleset_content_hash': saved_rule['content_hash']}
        identity = HermesToolExecutionIdentity(shown['session_id'], 'formal', 'create')
        name, replay_args = 'create_investigation_draft', args
        result = tool.execute_with_identity(name, args, principal=principal, identity=identity)
    assert result['status'] == 'ok', result
    actual = result['data']['draft']['configuration']['investigation']['recall_plan']
    assert actual == manager.resolve_lexicon_ref(selected['recall_plan']['resource_ref'], **ctx)
    assert tool.execute_with_identity(name, replay_args, principal=principal, identity=identity) == result
    # Editing the source later cannot silently change an already created draft.
    manager.update(edit['edit_id'], selected.get('version', 1) if source in {'edited', 'opened_edited'} else 1,
        [{'operation': 'set_metadata', 'values': {'description': '采用后更新'}}], **ctx)
    assert stack['app_service'].get_draft(result['data']['draft']['id'], principal=principal).configuration.investigation.recall_plan.model_dump(mode='json') == actual
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM investigation_drafts').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0] == 0


@pytest.mark.parametrize('operation', ['create', 'update', 'adopt'])
def test_edit_race_rejected_inside_actual_draft_transaction(creation_stack, operation):
    stack = creation_stack
    principal = Principal('principal-a')
    app = stack['app_service']
    manager = app.resource_management
    rule_body = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    shown = _run_scripted_creation_turn(stack, content='生成规则并展示',
        actions=[('create_ruleset_proposal', {'content': rule_body})])
    ctx = dict(session_id=shown['session_id'], principal=principal)
    edit = manager.create_lexicon(lexicon(), **ctx)
    args = _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = tool_view(edit)['recall_plan']
    tool = stack['tool_service']
    if operation == 'update':
        draft = tool.execute('create_investigation_draft', args, **ctx)['draft']
        args = dict(draft_id=draft['id'], expected_revision=1, configuration=args['configuration'])
    method = {'create': 'create_draft', 'update': 'update_draft', 'adopt': 'use_ruleset_proposal'}[operation]
    original = getattr(app.store, method)
    def race(*values, **kwargs):
        # After tool resolution AND resource fence, before the creation-store write.
        # Metadata-only change must also invalidate the old edit version.
        manager.update(edit['edit_id'], 1, [{'operation': 'set_metadata',
            'values': {'description': '并发修改'}}], **ctx)
        return original(*values, **kwargs)
    with patch.object(app.store, method, side_effect=race):
        if operation == 'adopt':
            args['configuration'].pop('judgement', None)
            args['configuration'].pop('schema_version', None)
            result, _, _ = approve(stack, shown, arguments={'create_draft': args})
        else:
            result = tool.execute_with_identity(operation + '_investigation_draft', args,
                principal=principal, identity=HermesToolExecutionIdentity(shown['session_id'], 'race', 'call'))
    assert result['error']['code'] == 'RESOURCE_REF_STALE', result
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM investigation_drafts').fetchone()[0] == (operation == 'update')
    if operation == 'update':
        assert app.get_draft(draft['id'], principal=principal).current_revision == 1


def test_temporary_reference_rejects_term_override(service):
    from backend.investigation_creation.resource_ref_inputs import resolve_arguments
    edit = service.create_lexicon(lexicon(), **CTX)
    args = {'configuration': {'investigation': {'mode': 'search', 'recall_plan': {
        **tool_view(edit)['recall_plan'], 'enabled_main_terms': ['暗中替换']}}}}
    with pytest.raises(ResourceError) as error:
        resolve_arguments('create_investigation_draft', args, lambda: service, **CTX)
    assert error.value.code == 'INVALID_TOOL_ARGUMENTS'
