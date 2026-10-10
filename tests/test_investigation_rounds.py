"""Phase 2 isolation, recovery and existing confirmation paths; synthetic DBs only."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from test_investigation_creation_conversation import (
    creation_stack, _search_draft_from_options, _run_scripted_creation_turn as _script,
)
from investigation_round_fixtures import publish_round
from test_ruleset_proposal_presentation import content, evidence
from backend.audit_agent.config import settings
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import HermesToolExecutionIdentity, creation_tool_schemas
from backend.investigation_creation.rounds import project
from backend.report_collection.service import ReportCollectionService
from backend.report_collection.access import owns_report
from backend.report_collection import tools as collection_tools

OWNER = Principal('principal-a')


def _run_scripted_creation_turn(*args, **kwargs):
    result = _script(*args, **kwargs)
    assert result['turn'].status == 'completed', result['result']
    result['terminal'] = {'artifact': result['turn'].public_artifact}
    return result


@pytest.fixture
def rounds(creation_stack, monkeypatch, tmp_path):
    s = creation_stack
    monkeypatch.setattr(settings, 'investigation_multi_round_enabled', True)
    monkeypatch.setattr(settings, 'continuous_resource_session_enabled', True)
    collection = ReportCollectionService(conversation=s['conversation'], report_store=s['report_store'],
        can_read=lambda p, item: owns_report(s['creation_store'], p, item),
        path=tmp_path/'collections.sqlite3', agent_driven=True)
    s['conversation'].report_collection = collection
    monkeypatch.setattr(collection_tools, '_service', collection)
    s['sid'] = s['conversation'].create_session(principal=OWNER).id
    s['collection'] = collection
    return s


def call(s, name, args, turn=None, call_id=None, sid=None, owner=OWNER):
    return s['tool_service'].execute_with_identity(name, args, principal=owner,
        identity=HermesToolExecutionIdentity.require(session_id=sid or s['sid'],
            turn_id=turn or uuid4().hex, tool_call_id=call_id or uuid4().hex))


def first(s):
    def arguments(results):
        args = _search_draft_from_options(results)
        args['configuration']['platform'] = 'dy'
        return args
    return _run_scripted_creation_turn(s, session_id=s['sid'], content='准备调查',
        client_message_id=uuid4().hex, final_response='请核对调查配置。', actions=[
            ('query_investigation_options', {'domain_hint':'博彩','mode':'search','include_lexicon_terms_for_ids':['gambling']}),
            ('create_investigation_draft', arguments)])


def copy(s, source, **kw):
    return call(s, 'copy_investigation_draft', {'source_draft_id':source}, **kw)


def test_two_rounds_copy_edit_confirm_publish_and_query(rounds):
    s = rounds; a = first(s)
    run_a = publish_round(s, a, idempotency_key='round-a')
    original = s['creation_store'].get_run(run_a['run_id'], principal=OWNER.id).confirmed_configuration
    b = _run_scripted_creation_turn(s, session_id=s['sid'], content='沿用第一次配置，准备第二次调查',
        client_message_id=uuid4().hex, final_response='第二次调查草案已准备，请核对。', actions=[
            ('copy_investigation_draft', {'source_draft_id':run_a['draft_id']})])
    b_id = b['terminal']['artifact']['draft_id']
    assert b_id != run_a['draft_id']
    state = s['conversation'].get_workspace_state(s['sid'], principal=OWNER)
    assert len(state.rounds) == 2 and state.run is None
    assert state.rounds[1]['draft_status'] == 'DRAFT'
    assert state.rounds[0]['run']['report_version_id'] == run_a['report_version_id']
    original_config = s['creation_store'].get_draft(run_a['draft_id'], principal=OWNER.id).configuration
    assert s['creation_store'].get_draft(b_id, principal=OWNER.id).configuration == original_config
    edit = call(s, 'update_investigation_draft', {'draft_id':b_id,'expected_revision':1,'title':'第二次补充调查'})
    assert edit['status'] == 'ok', edit
    b['terminal']['artifact']['draft_revision'] = 2
    run_b = publish_round(s, b, idempotency_key='round-b')
    assert run_a['run_id'] != run_b['run_id'] and run_a['report_version_id'] != run_b['report_version_id']
    assert s['creation_store'].get_run(run_a['run_id'], principal=OWNER.id).confirmed_configuration == original
    assert s['creation_store'].get_draft(run_a['draft_id'], principal=OWNER.id).current_revision == 1
    assert call(s, 'update_investigation_draft', {'draft_id':run_a['draft_id'],'expected_revision':1,'title':'不应覆盖'})['status'] == 'error'
    # A historical read remains legal without changing the current round.
    _run_scripted_creation_turn(s, session_id=s['sid'], content='看第一次配置',
        client_message_id=uuid4().hex, final_response='这是第一次的配置。', actions=[
            ('get_investigation_draft', {'draft_id':run_a['draft_id']})])
    state = s['client'].get(f"/api/investigation-workspaces/{s['sid']}/state")
    assert state.status_code == 200, state.text
    assert state.json()['draft_artifact']['draft_id'] == b_id
    assert [r['round_number'] for r in state.json()['rounds']] == [1, 2]
    turn, _ = s['conversation'].accept_message(s['sid'], client_message_id=uuid4().hex, content='比较两轮配置', principal=OWNER)
    reports = json.loads(collection_tools.dispatch('list_conversation_reports', {}, s['sid'], turn.id))
    assert reports['ok'], reports
    refs = [r['report_ref'] for r in reports['data']['reports']]
    assert len(refs) == 2
    compared = json.loads(collection_tools.dispatch('compare_conversation_config', {'report_refs':refs}, s['sid'], turn.id))
    assert compared['ok'], compared
    assert len(compared['data']['configs']) == 2


def test_pending_draft_and_other_chat_are_not_copied(rounds):
    s=rounds; a=first(s); source=a['terminal']['artifact']['draft_id']
    assert copy(s, source)['error']['code'] == 'INVALID_ROUND_SOURCE'
    publish_round(s,a,idempotency_key='source')
    other=s['conversation'].create_session(principal=OWNER).id
    assert copy(s,source,sid=other)['status']=='error'
    assert call(s,'get_investigation_draft',{'draft_id':source},sid=other)['status']=='error'
    assert copy(s,source,owner=Principal('principal-b'))['status']=='error'
    b=copy(s,source); assert b['status']=='ok',b
    duplicate=copy(s,source)
    assert duplicate['error']['code']=='EDITABLE_ROUND_EXISTS',duplicate
    assert len(project(s['app_service'],s['sid'],OWNER))==2


def test_concurrent_next_round_and_lost_reply_create_one_draft(rounds,monkeypatch):
    s=rounds;a=first(s);r=publish_round(s,a,idempotency_key='source')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:copy(s,r['draft_id']),range(4)))
    assert sum(x['status']=='ok' for x in results)==1,results
    assert len(project(s['app_service'],s['sid'],OWNER))==2
    assert all(x['status']=='ok' or x['error']['code']=='EDITABLE_ROUND_EXISTS' for x in results)


def test_copy_replay_after_commit_and_restart(rounds,monkeypatch):
    from backend.investigation_creation.store import InvestigationCreationStore
    s=rounds;a=first(s);r=publish_round(s,a,idempotency_key='source')
    store=s['creation_store']; original=store.complete_tool_execution
    def lost(*a,**kw):raise ConnectionError('synthetic disconnect after commit')
    monkeypatch.setattr(store,'complete_tool_execution',lost)
    with pytest.raises(ConnectionError):copy(s,r['draft_id'],turn='copy',call_id='copy')
    monkeypatch.setattr(store,'complete_tool_execution',original)
    s['app_service'].store=InvestigationCreationStore(store.db_path)
    replay=copy(s,r['draft_id'],turn='copy',call_id='retry')
    assert replay['status']=='ok',replay
    assert len(project(s['app_service'],s['sid'],OWNER))==2
    state=s['conversation'].get_workspace_state(s['sid'],principal=OWNER)
    assert state.draft_artifact['draft_id']==replay['data']['draft']['id']


def test_new_report_does_not_change_running_turn_sources(rounds):
    s=rounds;a=first(s)
    turn,_=s['conversation'].accept_message(s['sid'],client_message_id='before-publish',content='当前有哪些报告',principal=OWNER)
    assert s['collection'].sources(s['sid'],turn.id)==[]

    publish_round(s,a,idempotency_key='source')
    state=s['conversation'].get_workspace_state(s['sid'],principal=OWNER)
    assert state.rounds[0]['run']['status']=='PUBLISHED'
    assert len(s['collection'].members(s['sid'],OWNER))==1
    assert s['collection'].sources(s['sid'],turn.id)==[]
    replay,is_replay=s['conversation'].accept_message(s['sid'],client_message_id='before-publish',content='当前有哪些报告',principal=OWNER)
    assert is_replay and replay.id==turn.id
    assert s['collection'].sources(s['sid'],turn.id)==[]



def test_report_handoff_recovers_from_committed_round_without_public_artifact(rounds):
    s=rounds;a=first(s);source=publish_round(s,a,idempotency_key='a')
    b=copy(s,source['draft_id']);assert b['status']=='ok',b
    draft=b['data']['draft']
    published=publish_round(s,{'terminal':{'artifact':{'draft_id':draft['id'],'draft_revision':1}}},idempotency_key='b')
    handoff=s['conversation'].authorize_report_handoff(s['sid'],published['run_id'],principal=OWNER)
    assert handoff.report_version_id==published['report_version_id']
    other=s['conversation'].create_session(principal=OWNER).id
    from backend.investigation.errors import InvestigationSessionNotFoundError
    with pytest.raises(InvestigationSessionNotFoundError):
        s['conversation'].authorize_report_handoff(other,published['run_id'],principal=OWNER)


def test_disabled_catalog_preserves_legacy_tools(monkeypatch):
    monkeypatch.setattr(settings,'investigation_multi_round_enabled',False)
    assert not {'copy_investigation_draft','list_investigation_rounds'} & {s['name'] for s in creation_tool_schemas()}


def test_old_creation_receipts_restore_rounds_without_rewriting_history(rounds):
    s=rounds;a=first(s)
    with s['creation_store']._connect() as db:
        before=[tuple(r) for r in db.execute('SELECT * FROM investigation_creation_tool_receipts')]
        db.execute('DELETE FROM draft_creation_attempts')
        db.execute('DELETE FROM draft_creation_operations')
    result=project(s['app_service'],s['sid'],OWNER)
    assert [r['draft_id'] for r in result]==[a['terminal']['artifact']['draft_id']]
    other=s['conversation'].create_session(principal=OWNER).id
    assert project(s['app_service'],other,OWNER)==()
    with s['creation_store']._connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM investigation_creation_tool_receipts')]==before


def test_temporary_rules_are_copied_exactly_and_replaced_only_after_adoption(rounds, content):
    from copy import deepcopy
    s=rounds; a=first(s); a_id=a['terminal']['artifact']['draft_id']
    shown=_run_scripted_creation_turn(s,session_id=s['sid'],content='使用这份完整规则内容准备草案',
        client_message_id=uuid4().hex,actions=[('create_ruleset_proposal',{'content':content})])
    adopted=_run_scripted_creation_turn(s,session_id=s['sid'],content='采用这份规则，先不启动',
        client_message_id=uuid4().hex,actions=[('use_ruleset_proposal',{
            'presentation_id':evidence(shown)['presentation_id'],'draft_id':a_id,'expected_revision':1})])
    r=publish_round(s,adopted,idempotency_key='temporary-a')
    original=s['creation_store'].get_run(r['run_id'],principal=OWNER.id).confirmed_configuration
    b=copy(s,a_id); assert b['status']=='ok',b
    bd=b['data']['draft']
    assert bd['configuration']['judgement']==s['creation_store'].get_draft(a_id,principal=OWNER.id).configuration.judgement.model_dump(mode='json')
    changed=deepcopy(content);changed['categories'][0]['rules'][0]['hit_condition']+=' 需有明确收费证据。'
    new=_run_scripted_creation_turn(s,session_id=s['sid'],content='准备调整规则，先展示',client_message_id=uuid4().hex,
        actions=[('create_ruleset_proposal',{'content':changed})])
    result=_run_scripted_creation_turn(s,session_id=s['sid'],content='采用刚展示的规则到第二轮草案，先不启动',
        client_message_id=uuid4().hex,actions=[('use_ruleset_proposal',{
            'presentation_id':evidence(new)['presentation_id'],'draft_id':bd['id'],'expected_revision':1})])
    rb=publish_round(s,result,idempotency_key='temporary-b')
    assert s['creation_store'].get_run(r['run_id'],principal=OWNER.id).confirmed_configuration==original
    second=s['creation_store'].get_run(rb['run_id'],principal=OWNER.id).confirmed_configuration
    assert original['temporary_ruleset']!=second['temporary_ruleset']
    # Repeat confirmation via the tool cannot enqueue another Run.
    view=s['app_service'].get_draft_view(bd['id'],principal=OWNER)
    confirmed=call(s,'confirm_and_queue_investigation',{'draft_id':bd['id'],'expected_revision':2,
        'confirmed':True,'idempotency_key':'temporary-b',
        'expected_task_settings_revision':view.confirmation_preview.task_settings_revision})
    assert confirmed['status']=='ok',confirmed
    assert confirmed['data']['run_id']==rb['run_id']
    with s['creation_store']._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_runs').fetchone()[0]==2


def test_native_catalog_unites_creation_and_collection_without_legacy_report_tools(rounds,monkeypatch):
    from hermes_m0.plugin import register, _handler
    monkeypatch.setenv('HERMES_INVESTIGATION_CREATION_MODE','1')
    for flag in ('COLLECTION','ACCOUNT_ACTIVITY','REAL_REPORT','REPORT_TASK','TASK'):
        monkeypatch.delenv('HERMES_INVESTIGATION_'+flag+'_MODE',raising=False)
    class Catalog:
        def __init__(self):self.names=set()
        def register_middleware(self,*args):pass
        def register_tool(self,**kwargs):
            assert kwargs['name'] not in self.names
            self.names.add(kwargs['name'])
    catalog=Catalog();register(catalog)
    assert {'create_investigation_draft','copy_investigation_draft','list_investigation_rounds',
            'query_conversation_reports','compare_conversation_config','confirm_and_queue_investigation'} <= catalog.names
    assert 'read_report' not in catalog.names
    t,_=rounds['conversation'].accept_message(rounds['sid'],client_message_id='empty-reports',content='有哪些报告',principal=OWNER)
    result=json.loads(_handler('list_conversation_reports')({},session_id=rounds['sid'],task_id=t.id))
    assert result['ok'] and result['data']['reports']==[]
