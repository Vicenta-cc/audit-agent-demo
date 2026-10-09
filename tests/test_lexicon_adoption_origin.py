"""R09: one authoritative content snapshot, exact source identity, no guessing."""
from copy import deepcopy
import pytest
from backend.investigation_creation.contracts import ConfirmAndQueueCommand, CreateDraftCommand, UpdateDraftCommand
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.errors import ConfigurationValidationError
from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn, _t1_temporary_arguments
from test_ruleset_proposal_presentation import content
from test_resource_lifecycle import lexicon
from test_session_resource_state import reader, snapshot

P = Principal('principal-a')

def adopted(stack):
    shown = _run_scripted_creation_turn(stack, content='查看可用选项', actions=[])
    session = shown['session_id']; manager = stack['app_service'].resource_management
    ctx = dict(session_id=session, principal=P)
    a = manager.create_lexicon(lexicon(), **ctx)
    b = manager.create_lexicon(lexicon(), **ctx) # Identical content, distinct identity.
    args = _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = {'strategy':'resource_ref', 'resource_ref':b['resource_ref']}
    result = _run_scripted_creation_turn(stack, session_id=session, client_message_id='adopt', content='创建调查',
        actions=[('create_investigation_draft', args)])
    draft = result['agent'].results[0]['draft']
    return session, a, b, draft

def test_edit_and_frozen_run_recover_exact_source_after_new_edit(creation_stack):
    stack=creation_stack; session,a,b,draft=adopted(stack); app=stack['app_service']
    run=app.confirm_and_queue(ConfirmAndQueueCommand(draft_id=draft['id'], expected_revision=1, confirmed=True, idempotency_key='origin'),principal=P)
    frozen=deepcopy(run.confirmed_configuration)
    app.resource_management.update(b['edit_id'],1,[{'operation':'set_metadata','values':{'title':'新编辑版'}}],session_id=session,principal=P)
    before=snapshot(stack)
    items=reader(stack).read(session,principal=P,limit=100)['items']
    for item in [i for i in items if i['type'] in {'draft_revision','run'}]:
        assert item['lexicon']['lineage']=='verified_edit_version'
        assert item['lexicon']['edit_id']==b['edit_id'] != a['edit_id']
        assert item['lexicon']['edit_version']==1
    assert snapshot(stack)==before
    assert app.store.get_run(run.id,principal=P.id).confirmed_configuration==frozen

def test_unchanged_draft_keeps_origin_but_drawer_content_edit_detaches(creation_stack):
    stack=creation_stack; session,a,b,draft=adopted(stack); app=stack['app_service']
    app.resource_management.update(b['edit_id'],1,[{'operation':'set_metadata','values':{'title':'新版'}}],session_id=session,principal=P)
    changed=app.update_draft(UpdateDraftCommand(draft_id=draft['id'],expected_revision=1,title='仅改标题'),principal=P,session_id=session)
    assert changed.configuration.investigation.recall_plan.source_edit_ref==b['resource_ref']
    config=changed.configuration.model_dump(mode='json')
    config['investigation']['recall_plan']['lexicon_content']['title']='草案内调整的词库'
    changed=app.update_draft(UpdateDraftCommand(draft_id=draft['id'],expected_revision=2,configuration=config),principal=P,session_id=session)
    assert changed.configuration.investigation.recall_plan.source_edit_ref is None
    assert app.resource_management.get_edit(b['edit_id'],session_id=session,principal=P)['content']['title']=='新版'

def test_direct_forged_origin_cannot_create_draft(creation_stack):
    stack=creation_stack; session,a,b,draft=adopted(stack)
    args=_t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan']=deepcopy(draft['configuration']['investigation']['recall_plan'])
    with pytest.raises(ConfigurationValidationError):
        stack['app_service'].create_draft(CreateDraftCommand(**args),principal=P,session_id=session)


def test_rule_adoption_entrypoint_preserves_exact_lexicon_source(creation_stack, content):
    from test_shared_draft_creation_recovery import operation, ADOPT
    with operation(creation_stack, content) as (call, commands, session, turn):
        manager=creation_stack['app_service'].resource_management
        ctx=dict(session_id=session,principal=P)
        edit=manager.create_lexicon(lexicon(),**ctx)
        for version in (1,2):
            edit=manager.update(edit['edit_id'],version,[{'operation':'set_metadata','values':{'title':f'词库第{version+1}版'}}],**ctx)
        args=deepcopy(commands[ADOPT])
        args['create_draft']['configuration']['investigation']['recall_plan']={'strategy':'resource_ref','resource_ref':edit['resource_ref']}
        adopted=call(ADOPT,args)
        assert adopted['status']=='ok',adopted
        plan=adopted['data']['draft']['configuration']['investigation']['recall_plan']
        assert plan['source_edit_ref']==edit['resource_ref']
        manager.update(edit['edit_id'],3,[{'operation':'set_metadata','values':{'title':'当前第4版'}}],**ctx)
        recovered=reader(creation_stack).read(session,principal=P,limit=100)['items']
        draft=next(i for i in recovered if i['type']=='draft_revision')
        assert draft['lexicon']['edit_id']==edit['edit_id']
        assert draft['lexicon']['edit_version']==3
