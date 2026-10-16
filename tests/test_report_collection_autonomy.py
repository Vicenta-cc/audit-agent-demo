"""Autonomy changes planning, never report authorization or frozen identity."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_report_collection import collection,begin,call,finish
from test_investigation_creation_conversation import creation_stack
from backend.report_collection.tools import dispatch,schemas,AGENT_PROMPT


def agent_call(stack,turn,name='query_conversation_reports',**args):
    response=json.loads(dispatch(name,args,stack['sid'],turn))
    assert response['ok'],response
    return response['data']


def test_reading_b_does_not_prevent_same_turn_comparison(collection):
    s=collection;s['collection'].agent_driven=True;t=begin(s)
    refs={r['label']:r['report_ref'] for r in call(s,t,'list_conversation_reports')['reports']}
    b=agent_call(s,t,report_refs=[refs['B']])
    finish(s,t);t=begin(s)
    restored=agent_call(s,t,'read_conversation_result',result_ref=b['result_ref'])
    comparison=agent_call(s,t,report_refs=[refs['A'],refs['B']],kind='accounts',common_only=True)
    assert comparison['total_count']==2
    assert {x['label'] for x in restored['sources']}=={'B'}
    assert {x['label'] for x in comparison['sources']}=={'A','B'}
    with s['collection'].connect() as db:
        stored=json.loads(db.execute('SELECT payload FROM collection_results WHERE ref=?',(b['result_ref'],)).fetchone()[0])
    assert {x['label'] for x in stored['sources']}=={'B'}
    # Exploratory freedom still excludes an owned report outside this session.
    response=json.loads(dispatch('query_conversation_reports',{'report_refs':['report-forged-C']},s['sid'],t))
    assert not response['ok']


def test_prior_objects_are_optional_and_explicit_filters_still_work(collection):
    s=collection;s['collection'].agent_driven=True;t=begin(s)
    refs={r['label']:r['report_ref'] for r in call(s,t,'list_conversation_reports')['reports']}
    common=agent_call(s,t,kind='accounts',common_only=True)
    account=next(a for a in common['items'] if 'comment_author' in a['roles_in_filtered_result'])
    assert account['roles_in_filtered_result']==['comment_author']
    assert len(account['evidence_entities'])==1
    observations=account['evidence_entities'][0]['report_observations']
    assert {row['source']['label'] for row in observations}=={'A','B'}
    assert {row['risk_level'] for row in observations}=={'low','high'}
    b=agent_call(s,t,report_refs=[refs['B']],account_ref=account['account_ref'])
    assert b['total_count']==1
    finish(s,t);t=begin(s)
    all_accounts=agent_call(s,t,kind='accounts')
    assert all_accounts['total_count']==4 and not all_accounts['parent_result_ref']
    assert {x['label'] for x in all_accounts['sources']}=={'A','B'}
    one=agent_call(s,t,kind='accounts',report_refs=[refs['B']],from_result_ref=b['result_ref'])
    assert one['total_count']==1 and one['items'][0]['account_ref']==account['account_ref']
    all_b=agent_call(s,t,kind='accounts',report_refs=[refs['B']])
    assert all_b['total_count']==3
    schema=next(x for x in schemas() if x['name']=='query_conversation_reports')
    assert schema['parameters']['properties']['scope']['default']=='all'


def test_autonomy_rechecks_permission_on_old_results(collection):
    s=collection;s['collection'].agent_driven=True;t=begin(s)
    original=agent_call(s,t)
    finish(s,t);t=begin(s)
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id='task-A'")
    response=json.loads(dispatch('read_conversation_result',{'result_ref':original['result_ref']},s['sid'],t))
    assert not response['ok']
    with pytest.raises(PermissionError):s['collection'].validate_history(s['sid'])


def test_autonomy_does_not_scan_or_regenerate_final_prose(collection,monkeypatch):
    s=collection;s['collection'].agent_driven=True;t=begin(s)
    answer='本次仅分析 B，未纳入 A（'+s['reports']['A'].version+'）。'
    run=Mock(return_value={'completed':True,'failed':False,'interrupted':False,'final_response':answer,
        'messages':[{'role':'user','content':'测试'},{'role':'assistant','content':answer}],'api_calls':1})
    monkeypatch.setattr(s['conversation'],'_agent',lambda *a,**kw:SimpleNamespace(run_conversation=run))
    scanner=Mock(side_effect=AssertionError('answer scanning must not run in agent mode'))
    monkeypatch.setattr(s['collection'],'validate_answer',scanner)
    result=s['conversation'].execute_turn(t)
    assert result.answer==answer
    assert run.call_count==1 and scanner.call_count==0
    assert run.call_args.kwargs['system_message']==AGENT_PROMPT
    assert 'answer_citations_and_claims_not_independently_verified' in str(result.grounding_validation)
