"""Context projection must never change durable evidence or conversational intent."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from test_investigation_creation_conversation import creation_stack
from test_report_collection import collection, begin
from backend.report_collection.context import project_history, restore_transcript
from backend.report_collection.tools import dispatch


def turn(index):
    return [
        {'role':'user','content':f'问题 {index}，只看 B'},
        {'role':'assistant','content':'查询','tool_calls':[{'id':f'c{index}','type':'function',
            'function':{'name':'query_conversation_reports','arguments':'{"kind":"comments"}'}}]},
        {'role':'tool','name':'query_conversation_reports','tool_call_id':f'c{index}',
         'content':json.dumps({'ok':True,'data':{'result_ref':f'result_{index}', 'sources':[{'label':'B'}],
            'items':[{'text':'内容'*200}], 'total_count':1,'has_more':False}},ensure_ascii=False)},
        {'role':'assistant','content':f'第 {index} 轮展示顺序：1. 甲 2. 乙'},
    ]


def test_projection_preserves_dialogue_protocol_recent_order_and_replay_reference():
    history=sum((turn(i) for i in range(4)),[]);original=deepcopy(history)
    compact=project_history(history,keep_from=8)
    assert history==original
    assert compact[8:]==history[8:]
    for before,after in zip(history,compact):
        if before['role']!='tool':assert before==after
        else:assert before['tool_call_id']==after['tool_call_id']
    index=json.loads(compact[2]['content'])['data']
    assert 'items' not in index and index['total_count']==1 and not index['has_more']
    assert index['history_projection']=={'omitted_fields':['items'],'read_tool':'read_conversation_result','result_ref':'result_0'}
    assert index['sources']==[{'label':'B'}]
    suffix=[{'role':'user','content':'第二条是谁发的？'},{'role':'assistant','content':'乙'}]
    restored=restore_transcript(compact+suffix,model_history=compact,stored_history=history)
    assert restored==history+suffix
    with pytest.raises(ValueError):restore_transcript([],model_history=compact,stored_history=history)


@pytest.mark.parametrize('content', ['not-json', '[]', '{"ok":false,"error":"revoked"}',
    '{"ok":true,"data":{"items":[],"result_ref":""}}'])
def test_unreplayable_or_failed_result_is_not_hidden(content):
    history=sum((turn(i) for i in range(3)),[])
    history[2]['content']=content
    assert project_history(history,keep_from=8)[2]==history[2]


@pytest.mark.parametrize('agent_mode',[True,False])
def test_runtime_projects_input_but_keeps_original_transcript_and_replay(collection,monkeypatch,agent_mode):
    s=collection;s['collection'].agent_driven=agent_mode;s['collection'].compact_history=True
    seen=[];original_results=[];old_history=[]
    def run(user_message,conversation_history,**kw):
        seen.append(deepcopy(conversation_history))
        response=dispatch('query_conversation_reports',{},s['sid'],current_turn)
        original_results.append(json.loads(response)['data']['result_ref'])
        return {'completed':True,'failed':False,'final_response':'1. b 2. shared',
            'messages':(conversation_history or [])+[
                {'role':'user','content':user_message},
                {'role':'assistant','content':'','tool_calls':[{'id':current_turn,'type':'function',
                    'function':{'name':'query_conversation_reports','arguments':'{}'}}]},
                {'role':'tool','name':'query_conversation_reports','tool_call_id':current_turn,'content':response},
                {'role':'assistant','content':'中断前的文字'},
                {'role':'user','content':'[System: network continuation, not a real user turn]'},
                {'role':'assistant','content':'1. b 2. shared'}]}
    monkeypatch.setattr(s['conversation'],'_agent',lambda *a,**kw:SimpleNamespace(run_conversation=run))
    for i in range(4):
        current_turn=begin(s)
        result=s['conversation'].execute_turn(current_turn)
        assert result.answer=='1. b 2. shared'
        stored=s['conversation'].store.latest_completed_hermes_transcript(s['sid'])
        assert stored[:len(old_history)]==old_history
        assert 'history_projection' not in json.dumps(stored)
        old_history=stored
    assert ('history_projection' in json.dumps(seen[-1])) is agent_mode
    assert seen[-1][-12:]==stored[-18:-6] # recent prior turn remains exact
    current_turn=begin(s)
    restored=json.loads(dispatch('read_conversation_result',{'result_ref':original_results[0]},s['sid'],current_turn))
    assert restored['ok']
    data=restored['data']
    assert data['total_count']==5
    assert {source['label'] for source in data['sources']}=={'A','B'}
    if agent_mode:
        assert len(data['items'])==4
        assert data['query_statistics']['comments']['original_comments']==4
        shared=next(group for group in data['items'] if group['visible_observation_count']==2)
        assert {row['source']['label'] for row in shared['report_observations']}=={'A','B'}
        assert {row['risk_level'] for row in shared['report_observations']}=={'low','high'}
        observations=sorted((row for group in data['items'] for row in group['report_observations']),
            key=lambda row:row['source_result_position'])
        rows=[{key:value for key,value in row.items()
            if key not in ('identity_status','source_result_position')} for row in observations]
    else:
        rows=data['items']
    assert len(rows)==5
    with s['collection'].connect() as db:
        raw=json.loads(db.execute('SELECT payload FROM collection_results WHERE ref=?',
            (original_results[0],)).fetchone()[0])
    assert rows==raw['items']
