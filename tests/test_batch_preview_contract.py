"""Preview pagination must not truncate or advance user-facing delivery."""
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from test_report_collection import creation_stack, begin
from test_report_collection_analysis import make_collection
from test_report_scope_repair import ask, setup, delivered
from backend.report_collection import tools
from backend.report_collection.batches import load
from backend.report_collection.delivery import direct_delivery_boundary


def test_discovery_schema_separates_preview_read_and_delivery(make_collection):
    setup(make_collection('paging'))
    schemas={s['name']:s['parameters']['properties'] for s in tools.schemas()}
    query=schemas['query_conversation_reports']
    assert 'preview_limit' in query and query['preview_limit']['maximum']==50
    assert not {'limit','offset','batch_size'} & query.keys()
    assert {'limit','offset'} <= schemas['read_conversation_result'].keys()
    assert schemas['deliver_conversation_results']['batch_size']['anyOf'][0]['maximum']==100


@pytest.mark.parametrize('args',[
    {'preview_limit':100}, {'preview_limit':0}, {'preview_limit':True},
    {'preview_limit':5,'limit':5}, {'offset':5}, {'batch_size':100},
])
def test_invalid_preview_never_silently_clamps_or_guesses(args):
    with pytest.raises(ValidationError):tools.DeliveryQuery.model_validate(args)


def test_existing_direct_callers_keep_legacy_limit_without_exposing_two_names():
    assert tools.DeliveryQuery.model_validate({'limit':5}).model_dump()['limit']==5
    assert tools.DeliveryQuery.model_validate({'preview_limit':5}).model_dump()['limit']==5


def test_preview_and_read_do_not_change_full_delivery_or_cursor(make_collection):
    s=setup(make_collection('paging'));t=begin(s)
    result=ask(s,t,preview_limit=1)
    assert len(result['items'])==1 and result['total_count']==27
    read=ask(s,t,'read_conversation_result',result_ref=result['result_ref'],offset=20,limit=3)
    assert len(read['items'])==3
    first=ask(s,t,'deliver_conversation_results',result_refs=[result['result_ref']],batch_size=10)
    assert first['row_counts']==[10] and first['ranges'][0]['start']==0
    delivered(s,t,first);t=begin(s)
    second=ask(s,t,'deliver_conversation_results',mode='continue')
    assert second['row_counts']==[10] and second['ranges'][0]['start']==10
    delivered(s,t,second);t=begin(s)
    replay=ask(s,t,'deliver_conversation_results',mode='replay',delivery_ref=first['delivery_ref'])
    assert replay['text']==first['text']
    delivered(s,t,replay);t=begin(s)
    last=ask(s,t,'deliver_conversation_results',mode='continue')
    assert last['row_counts']==[7] and last['ranges'][0]['start']==20 and last['complete']


def test_one_terminal_answer_delivers_all_selected_lists_once(make_collection):
    s=setup(make_collection('paging'));t=begin(s);service=s['collection']
    low=ask(s,t,risk_level='low',preview_limit=1)
    high=ask(s,t,risk_level='high',preview_limit=1)
    first=ask(s,t,'deliver_conversation_results',result_refs=[low['result_ref'],high['result_ref']],batch_size=100)
    def provider(*args,**kwargs):
        raise AssertionError('Do not generate another copy after terminal delivery')
    agent=SimpleNamespace(api_mode='chat_completions',_interruptible_api_call=provider,
                          _interruptible_streaming_api_call=provider)
    with direct_delivery_boundary(agent,service,s['sid'],t) as state:
        response=agent._interruptible_api_call({})
        assert response.choices[0].message.content==first['text']
        assert first['text'].count('**评论明细')==2
        assert first['row_counts']==[22,5] and state['local_completions']==1
    delivered(s,t,first)
    assert load(service,s['sid'],first['delivery_ref'])[1]=='acknowledged'


def test_invalid_preview_dispatch_does_not_create_a_result(make_collection):
    s=setup(make_collection('paging'));t=begin(s)
    response=json.loads(tools.dispatch('query_conversation_reports',{'preview_limit':100},s['sid'],t))
    assert not response['ok'] and 'preview_limit' in response['error']['message']
    with s['collection'].connect() as db:
        assert db.execute('SELECT COUNT(*) FROM collection_results WHERE turn=?',(t,)).fetchone()[0]==0
