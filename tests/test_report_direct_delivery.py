import json
from types import SimpleNamespace
import pytest
from test_report_collection_analysis import make_collection, creation_stack, invoke
from test_report_collection import begin, finish
from backend.report_collection import tools
from backend.report_collection.delivery import direct_delivery_boundary, cell, link


def test_full_list_order_roles_and_access(make_collection):
    s=make_collection('paging');s['collection'].direct_delivery=True;t=begin(s)
    listing=invoke(s,t,limit=1)
    data=invoke(s,t,'deliver_conversation_results',result_refs=[listing['result_ref']])
    assert data['row_counts']==[27]
    assert '| 27 |' in data['text'] and '27 条原始评论' in data['text']
    assert data['text'].count('| 高风险 |')==5
    assert '[原帖](<https://www.douyin.com/video/33001>)' in data['text']
    assert '[原帖](<https://www.douyin.com/video/33002>)' in data['text']
    assert data['text'].index('| 21 |')<data['text'].index('| 22 |')
    assert listing['result_ref'] not in data['text']
    assert data['references'][0]['result_ref']==listing['result_ref']
    assert len(data['references'][0]['rows'])==27
    assert data['references'][0]['rows'][20]['position']==21
    repeated_access=invoke(s,t,'deliver_conversation_results',
        result_refs=[listing['result_ref']],report_labels=['B'])
    assert repeated_access['text']==data['text']
    assert repeated_access['report_labels_checked']==['B']
    for item in data['references'][0]['rows']:
        assert item['comment_ref'] not in data['text']
        assert item['account_ref'] not in data['text']
    low=invoke(s,t,kind='accounts',account_role='comment_author',risk_level='low',limit=1)
    both=invoke(s,t,kind='accounts',account_role='both')
    assert low['total_count']==3 and both['total_count']==1
    delivery=invoke(s,t,'deliver_conversation_results',result_refs=[low['result_ref'],both['result_ref']],report_labels=['B','A','D'])
    assert delivery['row_counts']==[3,1]
    assert 'https://www.douyin.com/video/33002' in delivery['text']
    assert '风险：低风险；账号范围：评论作者' in delivery['text']
    assert '风险：不限；账号范围：同时发帖和评论的账号' in delivery['text']
    assert '场馆' not in delivery['text']
    assert '未读取、未参与统计' in delivery['text']
    assert '- **A**：' in delivery['text'] and '- **D**：' in delivery['text']
    assert '- **B**：' not in delivery['text']
    assert 'SECRET' not in delivery['text']
    # Same immutable ordering/conditions can be delivered on a follow-up.
    finish(s,t);t=begin(s)
    assert invoke(s,t,'deliver_conversation_results',result_refs=[listing['result_ref']])['text']==data['text']


def test_direct_adapter_avoids_provider_only_after_success_and_reauthorizes(make_collection):
    s=make_collection('paging');service=s['collection'];service.direct_delivery=True;t=begin(s)
    calls=[]
    def provider(*a,**kw):calls.append(kw);return 'provider'
    agent=SimpleNamespace(api_mode='chat_completions',_interruptible_api_call=provider,_interruptible_streaming_api_call=provider)
    with direct_delivery_boundary(agent,service,s['sid'],t) as state:
        assert agent._interruptible_api_call({})=='provider'
        invalid=json.loads(tools.dispatch('deliver_conversation_results',{'result_refs':['foreign']},s['sid'],t))
        assert not invalid['ok']
        assert agent._interruptible_streaming_api_call({})=='provider'
        result=invoke(s,t,limit=1)
        direct=invoke(s,t,'deliver_conversation_results',result_refs=[result['result_ref']])
        response=agent._interruptible_streaming_api_call({})
        assert response.choices[0].message.content==direct['text'] and len(calls)==2
        assert state['local_completions']==1
        with s['creation_store'].admission.connect() as db:
            db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id=?",(s['reports']['B'].task,))
        with pytest.raises(PermissionError):agent._interruptible_api_call({})
    assert agent._interruptible_api_call is provider


def test_default_off_and_literal_evidence(make_collection):
    s=make_collection('paging');t=begin(s)
    assert 'deliver_conversation_results' not in {x['name'] for x in tools.schemas()}
    assert not json.loads(tools.dispatch('deliver_conversation_results',{'report_labels':['D']},s['sid'],t))['ok']
    assert cell('<img src=x> [click](https://evil) | **bold**\nnext')=='&lt;img src=x&gt; \\[click\\]\\(https://evil\\) \\| \\*\\*bold\\*\\* next'
    assert link('原帖','https://example.com/a_(b)?x=1&y=2#comment')=='[原帖](<https://example.com/a_(b)?x=1&y=2#comment>)'
    assert link('详情','/tasks/task/outputs/7?view=report')=='[详情](</tasks/task/outputs/7?view=report>)'
    assert link('不可信','javascript:alert(1)')==''


def test_readable_delivery_keeps_distinct_same_name_accounts_and_sources(make_collection):
    s=make_collection('identity');s['collection'].direct_delivery=True;t=begin(s)
    listing=invoke(s,t,kind='accounts')
    delivered=invoke(s,t,'deliver_conversation_results',result_refs=[listing['result_ref']])
    assert '小桥（本表账号1）' in delivered['text']
    assert '小桥（本表账号2）' in delivered['text']
    assert delivered['row_counts']==[listing['total_count']]
    assert {source['label'] for source in delivered['references'][0]['sources']}=={'A','B','C'}
    for source in delivered['references'][0]['sources']:
        assert source['version'] not in delivered['text']


def test_delivery_batches_oversized_and_rejects_unknown_filter_results(make_collection):
    s=make_collection('paging');service=s['collection'];service.direct_delivery=True;t=begin(s)
    result=invoke(s,t,limit=1)
    with service.connect() as db:
        row=db.execute('SELECT payload FROM collection_results WHERE ref=?',(result['result_ref'],)).fetchone()
        data=json.loads(row['payload']); data['items']=[data['items'][0]]*1001
        db.execute('UPDATE collection_results SET payload=? WHERE ref=?',(json.dumps(data),result['result_ref']))
    refused=json.loads(tools.dispatch('deliver_conversation_results',{'result_refs':[result['result_ref']]},s['sid'],t))
    assert refused['ok'] and not refused['data']['complete']
    assert refused['data']['row_counts']==[100] and '1001 条记录' in refused['data']['text']
    assert '| 101 |' not in refused['data']['text']
    with service.connect() as db:
        data['items']=data['items'][:1];data.pop('query_filters')
        db.execute('UPDATE collection_results SET payload=? WHERE ref=?',(json.dumps(data),result['result_ref']))
    refused=json.loads(tools.dispatch('deliver_conversation_results',{'result_refs':[result['result_ref']]},s['sid'],t))
    assert not refused['ok'] and '查询条件' in refused['error']['message']


def test_guidance_exposes_delivery_without_forcing_tool_choice_and_restores_agent(make_collection):
    s=make_collection('paging');service=s['collection'];service.direct_delivery=True
    before={x['name']:x for x in tools.schemas()}
    service.direct_delivery_guidance=True
    after={x['name']:x for x in tools.schemas()}
    assert all(before[k]['parameters']==after[k]['parameters'] for k in before)
    assert len(tools.DIRECT_DELIVERY_PROMPT)<320
    agent=SimpleNamespace(api_mode='chat_completions',tools=[{'type':'function','function':{'name':'tool_call'}}],
        valid_tool_names={'tool_call'},_interruptible_api_call=lambda *a:None,_interruptible_streaming_api_call=lambda *a:None)
    original_tools=agent.tools;original_names=agent.valid_tool_names
    t=begin(s)
    with direct_delivery_boundary(agent,service,s['sid'],t):
        assert [x['function']['name'] for x in agent.tools]==['tool_call','deliver_conversation_results']
        assert agent.valid_tool_names=={'tool_call','deliver_conversation_results'}
        assert 'tool_choice' not in agent.__dict__
    assert agent.tools is original_tools and agent.valid_tool_names is original_names
