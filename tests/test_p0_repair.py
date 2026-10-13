import json
import pytest
from test_report_collection import collection, creation_stack, begin, finish
from test_report_collection_analysis import make_collection
from test_report_scope_repair import ask, setup, delivered
from test_resource_lifecycle import service, lexicon, CTX
from test_session_resource_state import prepared, reader, P
from backend.report_collection import tools
from backend.report_collection.delivery import pending_delivery
from backend.report_collection.batches import load
from backend.resource_management.contracts import ResourceError


@pytest.mark.parametrize('size',[None,5,100])
def test_terminal_identity_and_implicit_continue(make_collection,size):
    s=setup(make_collection('paging'));t=begin(s)
    result=ask(s,t,limit=1)
    args={'result_refs':[result['result_ref']]}
    if size is not None:args['batch_size']=size
    first=ask(s,t,'deliver_conversation_results',**args)
    terminal=pending_delivery(s['collection'],s['sid'],t)
    assert first==terminal
    with s['collection'].connect() as db:
        assert db.execute('SELECT COUNT(*) FROM collection_deliveries WHERE turn=?',(t,)).fetchone()[0]==1
    delivered(s,t,terminal)
    assert load(s['collection'],s['sid'],first['delivery_ref'])[1]=='acknowledged'
    if size==5:
        t=begin(s)
        following=ask(s,t,'deliver_conversation_results',mode='continue',delivery_ref=first['delivery_ref'])
        assert following['row_counts']==[5] and following['ranges'][0]['start']==5


def test_two_independent_lists_both_persist_and_acknowledge(make_collection):
    s=setup(make_collection('paging'));t=begin(s)
    low=ask(s,t,risk_level='low');high=ask(s,t,risk_level='high')
    first=ask(s,t,'deliver_conversation_results',result_refs=[low['result_ref']])
    second=ask(s,t,'deliver_conversation_results',result_refs=[high['result_ref']])
    terminal=pending_delivery(s['collection'],s['sid'],t)
    assert terminal['delivery_refs']==[first['delivery_ref'],second['delivery_ref']]
    assert first['text'] in terminal['text'] and second['text'] in terminal['text']
    assert terminal['row_counts']==[22,5]
    delivered(s,t,terminal)
    assert all(load(s['collection'],s['sid'],ref)[1]=='acknowledged' for ref in terminal['delivery_refs'])


@pytest.mark.parametrize('kind,filters',[
    ('overview',{'risk_level':'high'}),('overview',{'post_ref':'post_bad'}),
    ('overview',{'evidence_type':'ocr'}),('findings',{'risk_level':'low'}),
    ('posts',{'evidence_type':'ocr'}),('posts',{'evidence_ref':'evidence_bad'})])
def test_filters_cannot_silently_disappear(make_collection,kind,filters):
    s=setup(make_collection('config'));t=begin(s)
    response=json.loads(tools.dispatch('query_report_content',{'kind':kind,**filters},s['sid'],t))
    assert not response['ok'] and '不支持筛选' in response['error']['message']


def test_invalid_evidence_type_cannot_masquerade_as_empty_results():
    from pydantic import ValidationError
    from backend.domain.contracts import EvidenceType
    supported=set(tools.Content.model_json_schema()['properties']['evidence_type']['enum'])
    assert supported=={''}|{item.value for item in EvidenceType}
    with pytest.raises(ValidationError):tools.Content(kind='evidence',evidence_type='image')
    assert tools.Content(kind='evidence',evidence_type='ocr').evidence_type=='ocr'


def test_named_save_preserves_edit_and_idempotent_receipt(service):
    edit=service.create_lexicon(lexicon(),**CTX)
    args=dict(edit_id=edit['edit_id'],expected_version=1,mode='copy',operation_id='named',name='周末备份词库')
    saved=service.save(**args,**CTX)
    assert saved==service.save(**args,**CTX)
    assert saved['resource_name']=='周末备份词库'
    body=service.read('lexicon',saved['resource_id'],principal=CTX['principal'])['content']
    assert body['title']=='周末备份词库' and body['entries']==edit['content']['entries']
    assert service.get_edit(edit['edit_id'],**CTX)['content']==edit['content']
    with pytest.raises(ResourceError):service.save(**{**args,'name':'其他名称'},**CTX)
    from backend.investigation_creation.save_recovery import saved_request
    assert saved_request(service,'save_resource',args,**CTX)['resource_id']==saved['resource_id']


def test_item_index_and_actual_diff_bind_to_current_target(service):
    edit=service.create_lexicon(lexicon(),**CTX)
    target=edit['item_index'][0]
    changed=service.update(edit['edit_id'],1,[{'operation':'upsert_entry','target_id':target['target_id'],
        'values':{'term':'新的主词'}}],**CTX)
    assert changed['applied_diff']==[{'path':'/entries/0/term','before':lexicon()['entries'][0]['term'],'after':'新的主词'}]
    assert changed['content']['entries'][1:]==edit['content']['entries'][1:]


def test_exact_old_session_lexicon_copy_never_substitutes_current(prepared):
    p=prepared;app=p['stack']['app_service'];manager=app.resource_management
    ctx=dict(session_id=p['session'],principal=P)
    detail=reader(p['stack']).detail(p['session'],f'edit/{p["words"]}/1',principal=P)
    current=manager.get_edit(p['words'],**ctx)
    assert detail['content']['title']!=current['content']['title']
    copied=manager.open(**detail['open_arguments'][0],**ctx)
    assert copied['content']==detail['content']
    assert manager.get_edit(p['words'],**ctx)==current
    foreign=dict(ctx,session_id='foreign')
    with pytest.raises(Exception):manager.open(**detail['open_arguments'][0],**foreign)


def test_run_ruleset_copy_uses_frozen_version(prepared):
    p=prepared;manager=p['stack']['app_service'].resource_management
    detail=reader(p['stack']).detail(p['session'],f'run/{p["run"]}',principal=P)
    option=next(x for x in detail['open_arguments'] if x['kind']=='ruleset')
    copied=manager.open(**option,session_id=p['session'],principal=P)
    assert copied['content']['name']=='Candidate B v3'
    assert manager.get_edit(p['b'],session_id=p['session'],principal=P)['content']['name']=='Candidate B v4'


def test_revocation_barrier_survives_failed_turn_then_keeps_clean_history(collection,monkeypatch):
    from backend.report_collection.scopes import authorized_history_for_model
    from backend.audit_agent.config import settings
    monkeypatch.setattr(settings,'investigation_multi_round_enabled',True)
    s=setup(collection);t=begin(s);ask(s,t,scope='authorized_history');finish(s,t)
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id=?",(s['reports']['C'].task,))
    f=lambda history:authorized_history_for_model(s['collection'],s['sid'],history)
    assert f([{'role':'assistant','content':'old secret'}])==[]
    assert f([{'role':'assistant','content':'old secret'}])==[]
    # Persist a clean model turn through the real conversation store API.
    t=begin(s)
    from test_investigation_creation_conversation import _run_scripted_creation_turn
    # Remove this unused running fixture turn, then run a completed ordinary turn.
    s['conversation'].store.fail_turn(t,error_code='injected',safe_message='test',retryable=False)
    _run_scripted_creation_turn(s,session_id=s['sid'],client_message_id='clean-after-revoke',
        content='词库名叫周末词库',actions=[],final_response='记住了周末词库。')
    clean=[{'role':'user','content':'词库名叫周末词库'},{'role':'assistant','content':'记住了周末词库。'}]
    assert f(clean)==clean


def test_turn_context_contains_authorized_report_catalog_even_without_rounds(monkeypatch):
    from types import SimpleNamespace
    from backend.investigation_creation.conversation import InvestigationCreationConversationService, settings
    monkeypatch.setattr(settings,'investigation_multi_round_enabled',True)
    service=object.__new__(InvestigationCreationConversationService)
    workspace=SimpleNamespace(rounds=[])
    context=service._turn_context(workspace,report_sources=[{'label':'A','ref':'report_a','version':'version_a','identity':'private snapshot'}])
    assert '"label": "A"' in context and '"report_ref": "report_a"' in context
    assert '不是正文证据' in context and 'private snapshot' not in context
    empty=service._turn_context(workspace,report_sources=[])
    assert '本轮已授权的会话报告目录' in empty and '"label": "A"' not in empty
