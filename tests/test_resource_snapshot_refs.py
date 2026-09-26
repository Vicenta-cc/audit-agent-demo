"""Handles remove model-authored hashes without removing concurrency/approval guards."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from unittest.mock import patch

from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import HERMES_M3_TOOL_SCHEMAS, HermesToolExecutionIdentity
from backend.resource_management.contracts import ResourceError
from backend.resource_management.service import ResourceManagementService
from backend.resource_management.snapshot_refs import tool_view
from test_resource_lifecycle import service, lexicon, P, CTX, save
from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn, _t1_temporary_arguments
from test_ruleset_proposal_approval import approve


def test_saved_and_read_handle_bind_identical_snapshot_and_survive_restart(service):
    edit = service.create_lexicon(lexicon(), **CTX)
    saved = save(service, edit)
    read = service.read('lexicon', saved['resource_id'], principal=P)
    assert read['resource_ref'] == saved['resource_ref']
    assert read['content_hash'] != read['runtime_content_hash']
    reopened = ResourceManagementService(service.app)
    assert reopened.resolve_lexicon_ref(saved['resource_ref'], principal=P) == read['recall_plan']
    public = tool_view(saved)
    assert public['recall_plan'] == {'strategy': 'resource_ref', 'resource_ref': saved['resource_ref']}
    assert 'expected_runtime_content_hash' not in json.dumps(public)
    assert service.get_save('save-1', **CTX) == saved
    assert save(service, edit, 'another-tool-call') == {**saved, 'operation_id': 'another-tool-call'}


@pytest.mark.parametrize('change', ['term', 'metadata', 'delete', 'change_back'])
def test_changed_or_deleted_resource_never_upgrades_old_handle(service, change):
    saved = save(service, service.create_lexicon(lexicon(), **CTX))
    identifier = saved['resource_id']
    if change == 'delete':
        service.delete_library('lexicon', identifier, 1, principal=P)
    else:
        updated = deepcopy(lexicon())
        if change in {'term', 'change_back'}:
            updated['entries'][1]['term'] = '不同召回词'
        else:
            updated['description'] = '仅说明变化，运行搜索词相同'
        service.save_library('lexicon', identifier, updated, 1, 'modify', principal=P)
        if change == 'change_back':
            service.save_library('lexicon', identifier, lexicon(), 2, 'revert', principal=P)
    with pytest.raises(ResourceError) as caught:
        service.resolve_lexicon_ref(saved['resource_ref'], principal=P)
    assert caught.value.code == 'RESOURCE_REF_STALE'
    assert not caught.value.details['retryable']
    # An old save replay must retain its original snapshot, never bind latest.
    assert service.get_save('save-1', **CTX) == saved
    assert service.save(saved['edit_id'], 1, 'new', 'save-1', **CTX) == saved


def test_private_resources_and_handles_are_principal_bound(service):
    saved = save(service, service.create_lexicon(lexicon(), **CTX))
    other = Principal('other-user')
    for token, principal in [(saved['resource_ref'], other), ('resource-ref:' + '0' * 32, P)]:
        with pytest.raises(ResourceError) as caught:
            service.resolve_lexicon_ref(token, principal=principal)
        assert caught.value.code == 'RESOURCE_REF_INVALID'
    with pytest.raises(ResourceError):
        service.read('lexicon', saved['resource_id'], principal=other)
    # Public system templates remain readable, but the issued handles are private.
    first = service.read('lexicon', 'soft', principal=P)
    read = service.read('lexicon', 'soft', principal=other)
    assert first['resource_ref'] != read['resource_ref']
    assert service.resolve_lexicon_ref(read['resource_ref'], principal=other)['lexicon_id'] == 'soft'


def test_new_tool_schema_does_not_advertise_hash_inputs_but_keeps_temporary_terms():
    for name in ('create_investigation_draft', 'update_investigation_draft', 'use_ruleset_proposal'):
        schema = next(s['parameters'] for s in HERMES_M3_TOOL_SCHEMAS if s['name'] == name)
        text = json.dumps(schema)
        assert 'ResourceRefRecallPlan' in text
        assert 'expected_runtime_content_hash' not in text
        assert 'TemporaryTermsRecallPlan' in text
        # Every local JSON Schema reference still resolves after hiding legacy.
        def check(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    assert value['$ref'].split('/')[-1] in schema['$defs']
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)
        check(schema)


@pytest.mark.parametrize('source', ['save', 'read'])
def test_real_tool_boundary_adopts_handle_and_replays_without_duplicate_draft(creation_stack, source):
    stack = creation_stack
    principal = Principal('principal-a')
    rules = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    shown = _run_scripted_creation_turn(stack, content='生成规则并展示，不保存',
                                       actions=[('create_ruleset_proposal', {'content': rules})])
    manager = stack['app_service'].resource_management
    ctx = dict(session_id=shown['session_id'], principal=principal)
    edit = manager.create_lexicon(lexicon(), **ctx)
    tool = stack['tool_service']
    saved = tool.execute('save_resource', {'edit_id': edit['edit_id'], 'expected_version': 1,
                                         'operation_id': 'save-for-adoption'}, **ctx)
    resource = (tool.execute('read_resource', {'kind': 'lexicon', 'resource_id': saved['resource_id']}, **ctx)
                if source == 'read' else saved)
    args = _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = resource['recall_plan']
    args['configuration'].pop('judgement', None)
    args['configuration'].pop('schema_version', None)
    result, identity, _ = approve(stack, shown, arguments={'create_draft': args})
    assert result['status'] == 'ok', result
    draft = result['data']['draft']
    expected = manager.resolve_lexicon_ref(saved['resource_ref'], principal=principal)
    assert draft['configuration']['investigation']['recall_plan'] == expected
    assert draft['configuration']['judgement']['strategy'] == 'temporary_ruleset'
    # Even after an unrelated resource change, durable replay returns original
    # success before resolving the now-stale handle; it performs no new write.
    manager.lexicons.add_keyword(category_id=saved['resource_id'], keyword='变更')
    replay = tool.execute_with_identity('use_ruleset_proposal', {
        'presentation_id': shown['turn'].public_artifact['proposal_presentations'][0]['presentation_id'],
        'create_draft': args}, principal=principal, identity=identity)
    assert replay == result
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM investigation_drafts').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['stale', 'foreign', 'fabricated', 'extra_hash'])
def test_invalid_handle_never_creates_draft(creation_stack, failure):
    stack = creation_stack
    principal = Principal('principal-a')
    manager = stack['app_service'].resource_management
    ctx = dict(session_id='test-session', principal=principal)
    edit = manager.create_lexicon(lexicon(), **ctx)
    saved = manager.save(edit['edit_id'], 1, 'new', 'save', **ctx)
    plan = tool_view(saved)['recall_plan']
    if failure == 'stale':
        manager.lexicons.add_keyword(category_id=saved['resource_id'], keyword='changed')
    elif failure == 'foreign':
        principal = Principal('other')
    elif failure == 'fabricated':
        plan['resource_ref'] = 'resource-ref:' + '0' * 32
    else:
        plan['expected_runtime_content_hash'] = '0' * 64
    args = _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = plan
    result = stack['tool_service'].execute_with_identity('create_investigation_draft', args,
        principal=principal, identity=HermesToolExecutionIdentity('test-session', 'turn', 'call'))
    assert result['status'] == 'error', result
    expected = {'stale': 'RESOURCE_REF_STALE', 'foreign': 'RESOURCE_REF_INVALID',
                'fabricated': 'RESOURCE_REF_INVALID', 'extra_hash': 'INVALID_TOOL_ARGUMENTS'}[failure]
    assert result['error']['code'] == expected, result
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM investigation_drafts').fetchone()[0] == 0


def test_formal_rule_draft_create_and_update_accept_refs_but_store_legacy_plan(creation_stack):
    stack = creation_stack
    principal = Principal('principal-a')
    manager = stack['app_service'].resource_management
    ctx = dict(session_id='ref-formal', principal=principal)
    edit = manager.create_lexicon(lexicon(), **ctx)
    saved = manager.save(edit['edit_id'], 1, 'new', 'save', **ctx)
    args = _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = tool_view(saved)['recall_plan']
    tool = stack['tool_service']
    created = tool.execute_with_identity('create_investigation_draft', args, principal=principal,
        identity=HermesToolExecutionIdentity('ref-formal', 't1', 'c1'))
    assert created['status'] == 'ok', created
    draft = created['data']['draft']
    assert draft['configuration']['investigation']['recall_plan']['strategy'] == 'existing_lexicon'
    config = deepcopy(draft['configuration'])
    config['investigation']['recall_plan'] = tool_view(saved)['recall_plan']
    updated = tool.execute_with_identity('update_investigation_draft', {
        'draft_id': draft['id'], 'expected_revision': 1, 'title': '新的标题', 'configuration': config},
        principal=principal, identity=HermesToolExecutionIdentity('ref-formal', 't2', 'c2'))
    assert updated['status'] == 'ok', updated
    assert updated['data']['draft']['current_revision'] == 2
    assert updated['data']['draft']['configuration']['investigation']['recall_plan'] == draft['configuration']['investigation']['recall_plan']


@pytest.mark.parametrize('operation', ['create', 'update', 'adopt'])
def test_version_rechecked_inside_draft_write_fence(creation_stack, operation):
    stack = creation_stack
    principal = Principal('principal-a')
    app = stack['app_service']
    manager = app.resource_management
    rules = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    shown = _run_scripted_creation_turn(stack, content='生成规则并展示',
                                       actions=[('create_ruleset_proposal', {'content': rules})])
    ctx = dict(session_id=shown['session_id'], principal=principal)
    edit = manager.create_lexicon(lexicon(), **ctx)
    saved = manager.save(edit['edit_id'], 1, 'new', 'fence-save', **ctx)
    args = _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = tool_view(saved)['recall_plan']
    tool = stack['tool_service']
    draft_before = None
    if operation == 'update':
        draft_before = tool.execute('create_investigation_draft', args, **ctx)['draft']
        args = {'draft_id': draft_before['id'], 'expected_revision': 1,
                'title': '不得写入', 'configuration': args['configuration']}
    method = {'create': 'create_draft', 'update': 'update_draft', 'adopt': 'use_ruleset_proposal'}[operation]
    original = getattr(app, method)
    def concurrent_metadata_change(*values, **kwargs):
        # The tool already resolved the handle. Change only metadata, leaving
        # runtime_hash unchanged, before Application acquires its write fence.
        body = deepcopy(lexicon())
        body['description'] = '并发修改的说明'
        manager.save_library('lexicon', saved['resource_id'], body, 1, 'concurrent', principal=principal)
        return original(*values, **kwargs)
    with patch.object(app, method, side_effect=concurrent_metadata_change):
        if operation == 'adopt':
            args['configuration'].pop('judgement', None)
            args['configuration'].pop('schema_version', None)
            result, _, _ = approve(stack, shown, arguments={'create_draft': args})
        else:
            result = tool.execute_with_identity(operation + '_investigation_draft', args,
                principal=principal, identity=HermesToolExecutionIdentity(shown['session_id'], 'race', 'call'))
    assert result['error']['code'] == 'RESOURCE_REF_STALE', result
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM investigation_drafts').fetchone()[0] == (1 if draft_before else 0)
    if draft_before:
        assert app.get_draft(draft_before['id'], principal=principal).current_revision == 1
