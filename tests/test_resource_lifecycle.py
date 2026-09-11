from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest
from backend.audit_agent.lexicon_store import LexiconStore
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.service import InvestigationCreationService
from backend.investigation_creation.store import InvestigationCreationStore
from backend.rulesets.store import RuleSetStore
from backend.rulesets.service import RuleSetService
from backend.resource_management.service import ResourceManagementService
from backend.resource_management.contracts import ResourceError
from types import SimpleNamespace


@pytest.fixture
def service(tmp_path):
    resource_db = tmp_path / 'resources.sqlite3'
    resources = SimpleNamespace(lexicon_store=LexiconStore(resource_db), ruleset_service=RuleSetService(RuleSetStore(resource_db)))
    app = InvestigationCreationService(InvestigationCreationStore(tmp_path/'creation.sqlite3'), configuration_resolver=None, resource_service=resources)
    return ResourceManagementService(app)


P = Principal('local-test')
CTX = dict(session_id='session-a', principal=P)


def lexicon():
    return {'title': '民族关系讨论', 'entries': [
        {'id': 'main-1', 'term': '维吾尔族文化', 'kind': 'main'},
        {'id': 'variant-1', 'term': '维族文化', 'kind': 'variant', 'parent_id': 'main-1'},
        {'id': 'tag-1', 'term': '文化讨论', 'kind': 'tag'},
        {'id': 'main-2', 'term': '维汉婚恋', 'kind': 'main', 'enabled': False},
    ]}


def save(service, edit, key='save-1', mode='new'):
    return service.save(edit['edit_id'], edit['version'], mode, key, **CTX)


def test_lexicon_edit_save_roundtrip_preserves_variants_and_search(service):
    draft = service.create_lexicon(lexicon(), **CTX)
    assert draft['search_terms'] == ['维吾尔族文化']
    assert not draft['saved']
    stored = save(service, draft)
    actual = service.read('lexicon', stored['resource_id'], principal=P)
    assert actual['content'] == draft['content']
    assert actual['search_terms'] == draft['search_terms']
    assert service.get_edit(draft['edit_id'], **CTX)['saved']
    assert stored == save(service, draft)
    assert service.get_save('save-1', **CTX) == stored


def test_rename_main_preserves_variant_parent_and_stats(service):
    formal = save(service, service.create_lexicon(lexicon(), **CTX))
    with service.lexicons._connect() as conn:
        conn.execute('UPDATE lexicon_keywords SET hit_count_7d=12 WHERE entry_id=?', ('main-1',))
        old_id = conn.execute('SELECT id FROM lexicon_keywords WHERE entry_id=?', ('main-1',)).fetchone()[0]
    draft = service.open('lexicon', formal['resource_id'], **CTX)
    changed = service.update(draft['edit_id'], 1, [{'operation':'upsert_entry','target_id':'main-1','values':{'term':'维吾尔族语言文化'}}], **CTX)
    result = save(service, changed, 'rename', 'update')
    assert result['search_terms'] == ['维吾尔族语言文化']
    read = service.read('lexicon', formal['resource_id'], principal=P)
    assert read['content']['entries'][1]['parent_id'] == 'main-1'
    with service.lexicons._connect() as conn:
        row = conn.execute('SELECT id,hit_count_7d FROM lexicon_keywords WHERE entry_id=?', ('main-1',)).fetchone()
        assert tuple(row) == (old_id, 12)


def test_legacy_mutation_invalidates_open_editor(service):
    formal = save(service, service.create_lexicon(lexicon(), **CTX))
    draft = service.open('lexicon', formal['resource_id'], **CTX)
    service.lexicons.add_keyword(category_id=formal['resource_id'], keyword='维汉交往')
    with pytest.raises(ResourceError, match='已经变化'):
        save(service, draft, 'stale', 'update')
    assert service.get_save('stale', **CTX)['status'] == 'not_found'


def test_concurrent_saves_have_one_version_winner(service):
    formal = save(service, service.create_lexicon(lexicon(), **CTX))
    first = service.open('lexicon', formal['resource_id'], **CTX)
    second = service.open('lexicon', formal['resource_id'], **CTX)
    drafts = [service.update(d['edit_id'], 1, [{'operation':'set_metadata','values':{'title':'修改'+str(i)}}], **CTX) for i,d in enumerate([first,second])]
    def attempt(i):
        try:
            return save(service, drafts[i], f'concurrent-{i}', 'update')['status']
        except ResourceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ['RESOURCE_VERSION_CONFLICT', 'saved']


def test_read_and_edit_do_not_create_formal_or_task(service):
    draft = service.create_lexicon(lexicon(), **CTX)
    with service.lexicons._connect() as conn:
        count = conn.execute('SELECT count(*) FROM lexicon_categories').fetchone()[0]
    service.update(draft['edit_id'], 1, [{'operation':'remove_entry','target_id':'main-1'}], **CTX)
    with service.lexicons._connect() as conn:
        assert conn.execute('SELECT count(*) FROM lexicon_categories').fetchone()[0] == count
    with service.app.store._connect() as conn:
        for table in ('investigation_drafts','investigation_runs'):
            assert conn.execute('SELECT count(*) FROM '+table).fetchone()[0] == 0
    with pytest.raises(ResourceError):
        service.get_edit(draft['edit_id'], session_id='other', principal=P)
    with pytest.raises(ResourceError):
        service.update(draft['edit_id'], 1, [{'operation':'set_metadata','values':{'title':'过期'}}], **CTX)


def test_formal_and_receipt_rollback_together(service, monkeypatch):
    draft = service.create_lexicon(lexicon(), **CTX)
    original = service._save_lexicon
    def fail(*a, **kw):
        original(*a, **kw)
        raise RuntimeError('injected response failure before commit')
    monkeypatch.setattr(service, '_save_lexicon', fail)
    with pytest.raises(RuntimeError):
        save(service, draft)
    assert service.get_save('save-1', **CTX)['status'] == 'not_found'
    assert not service.read('lexicon', query='民族关系讨论', principal=P)['items']


def test_ruleset_save_update_keeps_frozen_old_revision(service):
    body = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    proposal = service.app.create_ruleset_proposal(body, session_id=CTX['session_id'])
    edit = service.get_edit(proposal.proposal_id, **CTX)
    result = save(service, edit)
    assert service.rulesets.get_published(result['revision_id'], principal=P)['name'] == body['name']
    old = service.rulesets.get_published(result['revision_id'], principal=P)
    opened = service.open('ruleset', result['resource_id'], **CTX)
    changed = service.update(opened['edit_id'], 1, [{'operation':'set_metadata','values':{'name':body['name']+'修订'}}], **CTX)
    new = save(service, changed, 'rule-update', 'update')
    assert new['version'] == 2
    assert service.rulesets.get_published(result['revision_id'], principal=P) == old
    assert service.get_save('save-1', **CTX) == result
    with pytest.raises(ResourceError):
        service.save(opened['edit_id'], 2, 'copy', 'save-1', **CTX)


def test_ethnic_guidance_and_compilation_are_preserved(service):
    from backend.investigation_creation.conversation import CREATION_SYSTEM_PROMPT
    from backend.rulesets.compiler import compile_ruleset_content
    fixture = Path(__file__).parent/'fixtures/resource_lifecycle'
    assert (fixture/'ethnic-authoring-guidance.txt').read_text() in CREATION_SYSTEM_PROMPT
    body = json.loads((fixture/'ethnic-ruleset.json').read_text())
    proposal = service.app.create_ruleset_proposal(body, session_id=CTX['session_id'])
    before = compile_ruleset_content(body)
    edit = service.get_edit(proposal.proposal_id, **CTX)
    result = save(service, edit)
    read = service.read('ruleset', result['resource_id'], principal=P)
    assert read['content'] == body
    after = compile_ruleset_content(read['content'])
    assert before.stage_routes == after.stage_routes
    assert before.prompt_profile_snapshot == after.prompt_profile_snapshot


def test_resource_tools_allow_two_saves_per_turn_with_exact_replay(service):
    from backend.investigation_creation.tools import InvestigationCreationToolService, HermesToolExecutionIdentity
    tools = InvestigationCreationToolService(service.app)
    first = service.create_lexicon(lexicon(), **CTX)
    second = service.create_lexicon({**lexicon(), 'title':'另一个词库'}, **CTX)
    def call(edit, index):
        return tools.execute_with_identity('save_resource', dict(edit_id=edit['edit_id'], expected_version=1, mode='new', operation_id='tool-save-'+str(index)), principal=P, identity=HermesToolExecutionIdentity('session-a','turn-a','call-'+str(index)))
    a, b = call(first,1), call(second,2)
    assert a['status'] == b['status'] == 'ok'
    assert a['data']['resource_id'] != b['data']['resource_id']
    assert call(first,1) == a
    # Reopening the store preserves the new partial index and both receipts.
    reopened = InvestigationCreationStore(service.app.store.db_path)
    with reopened._connect() as conn:
        assert conn.execute('SELECT count(*) FROM investigation_creation_tool_receipts').fetchone()[0] == 2


def test_new_rule_edit_tools_produce_real_adoptable_presentation(service):
    from backend.investigation_creation.tools import InvestigationCreationToolService, HermesToolExecutionIdentity
    body = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    proposal = service.app.create_ruleset_proposal(body, session_id='session-a')
    result = save(service, service.get_edit(proposal.proposal_id, **CTX))
    tools = InvestigationCreationToolService(service.app)
    tools.begin_conversation_turn('session-a','application-turn')
    opened = tools.execute_with_identity('open_resource_edit', dict(kind='ruleset',resource_id=result['resource_id']), principal=P, identity=HermesToolExecutionIdentity('session-a','runtime-turn','call-open'))
    assert opened['status'] == 'ok'
    displays = service.app.store.proposal_presentation_snapshots(session_id='session-a',turn_id='application-turn')
    assert displays[0]['proposal_id'] == opened['data']['edit_id']
    assert displays[0]['content'] == proposal.content.model_dump(mode='json')
    tools.end_conversation_turn('session-a')


def test_save_then_edit_and_save_again_updates_same_resource(service):
    draft = service.create_lexicon(lexicon(), **CTX)
    first = save(service,draft)
    second = service.update(draft['edit_id'],1,[{'operation':'set_metadata','values':{'title':'第二版'}}],**CTX)
    result = save(service,second,'second-save','update')
    assert result['resource_id'] == first['resource_id']
    assert result['version'] == 2
    replay = save(service,second,'same-content','update')
    assert replay['version'] == 2


from test_investigation_creation_conversation import creation_stack


def test_formal_read_distinguishes_edit_version_from_search_binding(service):
    edit=service.create_lexicon(lexicon(), **CTX)
    saved=save(service,edit)
    before=service.read('lexicon',saved['resource_id'],principal=P)
    assert before['content_hash'] != before['runtime_content_hash']
    assert before['recall_plan']['enabled_main_terms'] == ['维吾尔族文化']
    opened=service.open('lexicon',saved['resource_id'],**CTX)
    changed=service.update(opened['edit_id'],1,[{'operation':'upsert_entry','target_id':'variant-1','values':{'term':'维吾尔文化'}}],**CTX)
    save(service,changed,key='variant-only',mode='update')
    after=service.read('lexicon',saved['resource_id'],principal=P)
    assert after['version'] == before['version']+1
    assert after['content_hash'] != before['content_hash']
    assert after['recall_plan'] == before['recall_plan']


def test_formal_read_plan_can_bind_a_presented_temporary_rule(creation_stack):
    from test_investigation_creation_conversation import _run_scripted_creation_turn, _t1_temporary_arguments
    from test_ruleset_proposal_approval import approve
    stack=creation_stack;principal=Principal('principal-a')
    body=json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    shown=_run_scripted_creation_turn(stack,content='生成规则并展示，不保存',actions=[('create_ruleset_proposal',{'content':body})])
    manager=stack['app_service'].resource_management
    ctx=dict(session_id=shown['session_id'],principal=principal)
    edit=manager.create_lexicon(lexicon(),**ctx)
    saved=manager.save(edit['edit_id'],1,'new','read-plan',**ctx)
    resource=manager.read('lexicon',saved['resource_id'],principal=principal)
    args=_t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan']=resource['recall_plan']
    args['configuration'].pop('judgement',None)
    args['configuration'].pop('schema_version',None)
    result,_,_=approve(stack,shown,arguments={'create_draft':args})
    assert result['status']=='ok',result
    draft=result['data']['draft']
    assert draft['configuration']['judgement']['strategy']=='temporary_ruleset'
    assert draft['configuration']['investigation']['recall_plan']==resource['recall_plan']
    assert stack['app_service'].get_confirmation_preview(draft['id'],principal=principal).can_confirm


@pytest.mark.parametrize('formal_rule',[True,False])
@pytest.mark.parametrize('formal_lexicon',[True,False])
def test_four_resource_combinations_freeze_precise_configuration(creation_stack,formal_rule,formal_lexicon):
    from backend.investigation_creation.contracts import CreateDraftCommand, ConfirmAndQueueCommand, UpdateDraftCommand
    from test_investigation_creation_conversation import _t1_temporary_arguments, _run_scripted_creation_turn
    from test_ruleset_proposal_approval import approve
    from test_temporary_ruleset_execution import adapter
    stack=creation_stack; app=stack['app_service']; manager=app.resource_management
    principal=Principal('principal-a')
    body=json.loads((Path(__file__).parent/'fixtures/resource_lifecycle/ethnic-ruleset.json').read_text())
    shown=_run_scripted_creation_turn(stack,content='请生成民族关系讨论规则并展示，不启动。',actions=[('create_ruleset_proposal',{'content':body})])
    ctx=dict(session_id=shown['session_id'],principal=principal)
    proposal=shown['turn'].public_artifact['proposal_presentations'][0]['proposal_id']
    rule_edit=manager.get_edit(proposal,**ctx)
    lex_edit=manager.create_lexicon(lexicon(),**ctx)
    args=_t1_temporary_arguments(stack)
    args['title']='民族关系讨论测试'
    args['objective']='审核具体攻击，保护正常身份文化表达'
    recall=lex_edit['recall_plan']
    saved_lex=None
    if formal_lexicon:
        saved_lex=manager.save(lex_edit['edit_id'],1,'new','lex-save',**ctx)
        recall={'strategy':'existing_lexicon','lexicon_id':saved_lex['resource_id'],'expected_runtime_content_hash':manager.lexicons.runtime_content_hash(saved_lex['resource_id']),'enabled_main_terms':lex_edit['search_terms']}
    args['configuration']['investigation']['recall_plan']=recall
    saved_rule=None
    if formal_rule:
        saved_rule=manager.save(proposal,1,'new','rule-save',**ctx)
        args['configuration']['judgement']={'strategy':'existing_ruleset','ruleset_revision_id':saved_rule['revision_id'],'expected_ruleset_version':saved_rule['version'],'expected_ruleset_content_hash':saved_rule['content_hash']}
        draft=app.create_draft(CreateDraftCommand(**args),principal=principal)
    else:
        del args['configuration']['judgement']
        args['configuration'].pop('schema_version',None)
        result,_,_=approve(stack,shown,arguments={'create_draft':args})
        assert result['status']=='ok',result
        draft=app.get_draft(result['data']['draft']['id'],principal=principal)
    run=app.confirm_and_queue(ConfirmAndQueueCommand(draft_id=draft.id,expected_revision=draft.current_revision,confirmed=True,idempotency_key='combination-run'),principal=principal)
    frozen=deepcopy(run.confirmed_configuration)
    execution=adapter(stack)
    job_id=execution.ensure_job(run) # Creates an isolated Job; never invokes crawler.run.
    job_before=execution.job_store.get(job_id)
    assert '维吾尔族文化' in json.dumps(frozen,ensure_ascii=False)
    assert '维族文化' not in json.dumps(frozen,ensure_ascii=False)
    if saved_lex:
        edit=manager.open('lexicon',saved_lex['resource_id'],**ctx)
        edit=manager.update(edit['edit_id'],1,[{'operation':'upsert_entry','target_id':'main-1','values':{'term':'维汉日常交往'}}],**ctx)
        manager.save(edit['edit_id'],edit['version'],'update','after-start-lex',**ctx)
    if saved_rule:
        edit=manager.open('ruleset',saved_rule['resource_id'],**ctx)
        edit=manager.update(edit['edit_id'],1,[{'operation':'set_metadata','values':{'name':'后续规则'}}],**ctx)
        manager.save(edit['edit_id'],edit['version'],'update','after-start-rule',**ctx)
    with pytest.raises(Exception):
        app.update_draft(UpdateDraftCommand(draft_id=draft.id,expected_revision=draft.current_revision,title='不可更改'),principal=principal)
    assert app.get_run(run.id,principal=principal) is not None
    assert stack['creation_store'].get_run(run.id,principal=principal.id).confirmed_configuration==frozen
    assert execution.job_store.get(job_id)==job_before


def test_api_save_read_and_cross_session_access(creation_stack):
    from backend.resource_management.api import create_resource_router
    stack=creation_stack
    stack['client'].app.include_router(create_resource_router(stack['app_service'],stack['conversation'],stack['principals']))
    session=stack['conversation'].create_session(principal=Principal('principal-a'))
    root=f'/api/investigation-workspaces/{session.id}'
    client=stack['client']
    edit=client.post(root+'/resource-edits/lexicon',json={'content':lexicon()})
    assert edit.status_code==200,edit.text
    data=edit.json()
    path=root+'/resource-edits/'+data['edit_id']
    saved=client.post(path+'/save',json={'expected_version':1,'mode':'new','operation_id':'api-save'})
    assert saved.status_code==200,saved.text
    assert client.get('/api/resource-library/lexicon/'+saved.json()['resource_id']).json()['search_terms']==['维吾尔族文化']
    stack['principals'].current=Principal('principal-b')
    assert client.get(path).status_code in (403,404)
    assert client.post(path+'/save',json={'expected_version':1,'mode':'new','operation_id':'api-save'}).status_code in (403,404)


def test_simultaneous_new_save_deduplicates_across_operation_ids(service):
    edit=service.create_lexicon(lexicon(),**CTX)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts=list(pool.map(lambda i: save(service,edit,f'new-{i}'),range(2)))
    assert len({r['resource_id'] for r in receipts})==1
    assert len(service.read('lexicon',query='民族关系讨论',principal=P)['items'])==1


def test_keyword_swap_preserves_identity_and_history_is_immutable(service):
    saved=save(service,service.create_lexicon(lexicon(),**CTX))
    edit=service.open('lexicon',saved['resource_id'],**CTX)
    changed=service.update(edit['edit_id'],1,[
        {'operation':'upsert_entry','target_id':'main-1','values':{'term':'维汉婚恋'}},
        {'operation':'upsert_entry','target_id':'main-2','values':{'term':'维吾尔族文化'}},
    ],**CTX)
    result=save(service,changed,'swap','update')
    assert result['search_terms']==['维汉婚恋']
    with service.lexicons._connect() as conn:
        assert conn.execute('SELECT keyword FROM lexicon_keywords WHERE entry_id=?',('main-1',)).fetchone()[0]=='维汉婚恋'
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute('DELETE FROM lexicon_content_versions WHERE category_id=?',(saved['resource_id'],))


def test_open_rule_origin_failure_leaves_no_half_created_edit(service,monkeypatch):
    body=json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    proposal=service.app.create_ruleset_proposal(body,session_id=CTX['session_id'])
    saved=save(service,service.get_edit(proposal.proposal_id,**CTX))
    before=service.list_edits(**CTX)
    def fail(*a,**kw): raise RuntimeError('injected origin write failure')
    monkeypatch.setattr(service,'_origin',fail)
    with pytest.raises(RuntimeError):service.open('ruleset',saved['resource_id'],**CTX)
    assert service.list_edits(**CTX)==before


def test_open_edit_present_then_adopt_keeps_existing_m3_boundary(creation_stack):
    from test_investigation_creation_conversation import _run_scripted_creation_turn
    from test_ruleset_proposal_approval import approve, creation
    stack=creation_stack;principal=Principal('principal-a')
    first=_run_scripted_creation_turn(stack,content='生成规则并展示',actions=[('create_ruleset_proposal',{'content':json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())})])
    manager=stack['app_service'].resource_management;ctx=dict(session_id=first['session_id'],principal=principal)
    saved=manager.save(first['turn'].public_artifact['proposal_presentations'][0]['proposal_id'],1,'new','save-rule',**ctx)
    opened=_run_scripted_creation_turn(stack,session_id=first['session_id'],client_message_id='open-formal',content='读取这套规则，修改前先展示',actions=[('open_resource_edit',{'kind':'ruleset','resource_id':saved['resource_id']})])
    presentation=opened['turn'].public_artifact['proposal_presentations'][0]
    adopted,_,_=approve(stack,opened,arguments={'presentation_id':presentation['presentation_id'],'create_draft':creation()})
    assert adopted['status']=='ok',adopted
    assert adopted['data']['draft']['configuration']['judgement']['content']==presentation['snapshot']['content']
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT count(*) FROM investigation_runs').fetchone()[0]==0


def test_legacy_full_editor_preserves_variant_identity_and_rejects_stale_save(service):
    from backend.resource_management.legacy_lexicon import save_editor
    saved=save(service,service.create_lexicon(lexicon(),**CTX))
    entries=[{'id':'main-1','main_term':'维吾尔族语言文化','variants':['维族文化'],'enabled':True,'query_type':'keyword'},
             {'id':'main-2','main_term':'维汉婚恋','variants':[],'enabled':False,'query_type':'keyword'},
             {'id':'tag-1','main_term':'文化讨论','variants':[],'enabled':True,'query_type':'tag'}]
    save_editor(service.lexicons,saved['resource_id'],'民族关系讨论','',entries,1)
    actual=service.read('lexicon',saved['resource_id'],principal=P)
    variant=next(e for e in actual['content']['entries'] if e['kind']=='variant')
    assert variant['id']=='variant-1' and variant['parent_id']=='main-1'
    with pytest.raises(ResourceError,match='已经变化'):
        save_editor(service.lexicons,saved['resource_id'],'旧页面覆盖','',entries,1)


def test_formal_projection_matches_explicit_entry_kinds(service):
    body=lexicon();body['entries'][0]['match_type']='tag'
    edit=service.create_lexicon(body,**CTX);receipt=save(service,edit)
    assert service.lexicons.enabled_main_terms(receipt['resource_id'])==edit['search_terms']


def test_copy_then_edit_targets_copy_and_preserves_original(service):
    original=save(service,service.create_lexicon(lexicon(),**CTX))
    edit=service.open('lexicon',original['resource_id'],**CTX)
    copied=save(service,edit,'copy','copy')
    edit=service.update(edit['edit_id'],1,[{'operation':'set_metadata','values':{'title':'继续修改副本'}}],**CTX)
    updated=save(service,edit,'copy-updated','update')
    assert updated['resource_id']==copied['resource_id']!=original['resource_id']
    assert service.read('lexicon',original['resource_id'],principal=P)['content']['title']=='民族关系讨论'


def test_represent_ui_edited_rule_through_read_tool_then_adopt(creation_stack):
    from test_investigation_creation_conversation import _run_scripted_creation_turn
    from test_ruleset_proposal_approval import approve,creation
    stack=creation_stack
    first=_run_scripted_creation_turn(stack,content='生成规则',actions=[('create_ruleset_proposal',{'content':json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())})])
    pid=first['turn'].public_artifact['proposal_presentations'][0]['proposal_id']
    stack['app_service'].resource_management.update(pid,1,[{'operation':'set_metadata','values':{'name':'页面修改后规则'}}],session_id=first['session_id'],principal=Principal('principal-a'))
    shown=_run_scripted_creation_turn(stack,session_id=first['session_id'],client_message_id='show-ui-edit',content='展示页面改好的规则',actions=[('get_resource_edit',{'edit_id':pid})])
    record=shown['turn'].public_artifact['proposal_presentations'][0]
    assert record['proposal_version']==2
    result,_,_=approve(stack,shown,arguments={'presentation_id':record['presentation_id'],'create_draft':creation()})
    assert result['status']=='ok',result


def test_legacy_editor_keeps_same_text_variants_with_distinct_metadata(service):
    from backend.resource_management.legacy_lexicon import save_editor
    body=lexicon()
    body['entries'].append({'id':'variant-platform','term':'维族文化','kind':'variant','parent_id':'main-1','platform':'xhs','enabled':False,'note':'来源备注'})
    receipt=save(service,service.create_lexicon(body,**CTX))
    entries=[{'id':'main-1','main_term':'维吾尔族文化','variants':['维族文化'],'enabled':True,'query_type':'keyword'},
             {'id':'main-2','main_term':'维汉婚恋','variants':[],'enabled':False,'query_type':'keyword'},
             {'id':'tag-1','main_term':'文化讨论','variants':[],'enabled':True,'query_type':'tag'}]
    save_editor(service.lexicons,receipt['resource_id'],'保存名称更改','',entries,1)
    content=service.read('lexicon',receipt['resource_id'],principal=P)['content']
    variants=[e for e in content['entries'] if e['kind']=='variant']
    assert len(variants)==2
    assert next(e for e in variants if e['id']=='variant-platform')['note']=='来源备注'
    assert not next(e for e in variants if e['id']=='variant-platform')['enabled']
