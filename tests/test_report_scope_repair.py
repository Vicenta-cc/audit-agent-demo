import json
import pytest
from test_report_collection import collection, creation_stack, begin, finish
from test_report_collection_analysis import make_collection
from backend.report_collection import tools
from backend.report_collection.batches import acknowledge


def ask(s,t,name='query_conversation_reports',**args):
    response=json.loads(tools.dispatch(name,args,s['sid'],t))
    assert response['ok'],response
    return response['data']


def setup(s):
    s['collection'].agent_driven=True
    s['collection'].express_results=False
    s['collection'].direct_delivery=True
    return s


def delivered(s,t,data):
    s['collection'].record_call('direct_delivery_completed',{},s['sid'],t,{'ok':True,'data':data})
    finish(s,t)


def test_three_scopes_historical_does_not_join_and_revoked_focus(collection):
    s=setup(collection);t=begin(s)
    directory=ask(s,t,'list_conversation_reports');refs={r['label']:r['report_ref'] for r in directory['reports']}
    known=next(r for r in ask(s,t,kind='accounts',common_only=True)['items'] if r['unique_comment_count'])['account_ref']
    b=ask(s,t,kind='comments',report_refs=[refs['B']],account_ref=known)
    all_=ask(s,t,kind='comments',account_ref=known)
    history=ask(s,t,kind='comments',scope='authorized_history',account_ref=known)
    assert [b['total_count'],all_['total_count'],history['total_count']]==[1,2,3]
    assert history['scope_kind']=='authorized_history'
    assert 'D-SECRET' not in json.dumps(history) and 'C-SECRET' in json.dumps(history)
    assert {r['label'] for r in ask(s,t,'list_conversation_reports')['reports']}=={'A','B'}
    finish(s,t);t=begin(s)
    assert ask(s,t,kind='comments',report_refs=[refs['B']],account_ref=known)['total_count']==1
    history=ask(s,t,scope='authorized_history',account_ref=known);finish(s,t)
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id=?",(s['reports']['C'].task,))
    t=begin(s)
    assert {r['label'] for r in ask(s,t,'list_conversation_reports')['reports']}=={'A','B'}
    assert ask(s,t,report_refs=[refs['B']])['total_count']==2
    assert not json.loads(tools.dispatch('read_conversation_result',{'result_ref':history['result_ref']},s['sid'],t))['ok']


def test_nickname_candidates_ranking_and_unknown_identity(make_collection):
    s=setup(make_collection('identity'));t=begin(s)
    candidates=ask(s,t,kind='accounts',name_contains='小桥')
    assert candidates['total_count']==2 and len({r['account_ref'] for r in candidates['items']})==2
    ranked=ask(s,t,kind='accounts',account_role='comment_author',sort_by='comment_count',top_n=1)
    assert ranked['total_count']==2 # pine and bridge-a both have 2 original comments
    assert [r['rank'] for r in ranked['items']]==[1,1]
    assert ranked['matching_count_before_top']==3
    invalid=json.loads(tools.dispatch('query_conversation_reports',{'account_ref':'account_forged'},s['sid'],t))
    assert not invalid['ok'] and '零活动' in invalid['error']['message']


def test_activity_uses_full_rows_not_twenty_preview(make_collection):
    s=setup(make_collection('paging'));t=begin(s)
    account=ask(s,t,kind='accounts',name_contains='常来看看')['items'][0]
    activity=ask(s,t,'query_account_activity',account_ref=account['account_ref'],activity_kind='all',limit=1)
    assert activity['statistics']['original_comments']==25
    assert activity['statistics']['original_posts']==1
    assert activity['total_count']==26 and activity['has_more']
    assert sum(r['original_comment_count'] for r in activity['interactions'])==25
    assert len(activity['interactions'])==2
    assert all('published_at' in r for r in activity['items'])


def test_overview_posts_and_evidence_are_readable_and_scoped(make_collection):
    s=setup(make_collection('config'));t=begin(s)
    refs={r['label']:r['report_ref'] for r in ask(s,t,'list_conversation_reports')['reports']}
    overview=ask(s,t,'query_report_content',kind='overview',report_refs=[refs['B']])
    assert overview['total_count']==1 and overview['items'][0]['configuration']['keywords']==['景区','门票','退款']
    posts=ask(s,t,'query_report_content',kind='posts',report_refs=[refs['B']],limit=1)
    assert posts['total_count']==2 and posts['items'][0]['body']
    evidence=ask(s,t,'query_report_content',kind='evidence',report_refs=[refs['B']],post_ref=posts['items'][0]['post_ref'])
    assert evidence['total_count']==0  # fixture intentionally contains no extracted evidence
    assert evidence['sources']==posts['sources']
    invalid=json.loads(tools.dispatch('query_report_content',{'kind':'posts','report_refs':[refs['A']],'post_ref':posts['items'][0]['post_ref']},s['sid'],t))
    assert not invalid['ok']


def test_batch_read_is_not_delivery_cursor_and_recovery_is_exact(make_collection):
    s=setup(make_collection('paging'));t=begin(s)
    result=ask(s,t,limit=1)
    ask(s,t,'read_conversation_result',result_ref=result['result_ref'],offset=20)
    first=ask(s,t,'deliver_conversation_results',result_refs=[result['result_ref']],batch_size=10)
    assert first['row_counts']==[10] and not first['complete']
    assert first['ranges'][0]['start']==0 and '| 10 |' in first['text'] and '| 11 |' not in first['text']
    assert ask(s,t,'deliver_conversation_results',result_refs=[result['result_ref']],batch_size=10)==first
    assert not json.loads(tools.dispatch('deliver_conversation_results',{'mode':'continue','delivery_ref':first['delivery_ref']},s['sid'],t))['ok']
    delivered(s,t,first) # simulate durable completion, deliberately skip acknowledge
    t=begin(s)
    second=ask(s,t,'deliver_conversation_results',mode='continue',batch_size=10)
    assert second['ranges'][0]['start']==10 and second['ranges'][0]['end']==20
    assert '| 11 |' in second['text'] and '| 21 |' not in second['text']
    delivered(s,t,second);t=begin(s)
    last=ask(s,t,'deliver_conversation_results',mode='continue',batch_size=10)
    assert last['row_counts']==[7] and last['complete'] and '| 21 |' in last['text']
    delivered(s,t,last);t=begin(s)
    restored=ask(s,t,'read_conversation_delivery',position=21)
    assert restored['items'][0]['item']['account_name']=='常来看看'
    replay=ask(s,t,'deliver_conversation_results',mode='replay',delivery_ref=first['delivery_ref'])
    assert replay['text']==first['text']
    delivered(s,t,replay);t=begin(s)
    assert not json.loads(tools.dispatch('deliver_conversation_results',{'mode':'continue'},s['sid'],t))['ok']


def test_big_result_batches_do_not_claim_complete(make_collection):
    s=setup(make_collection('paging'));t=begin(s)
    result=ask(s,t,limit=1)
    with s['collection'].connect() as db:
        data=json.loads(db.execute('SELECT payload FROM collection_results WHERE ref=?',(result['result_ref'],)).fetchone()[0])
        data['items']=[{**data['items'][0],'comment_ref':f'test_{i}','text':f'row-{i}'} for i in range(1053)]
        db.execute('UPDATE collection_results SET payload=? WHERE ref=?',(json.dumps(data),result['result_ref']))
    batch=ask(s,t,'deliver_conversation_results',result_refs=[result['result_ref']])
    assert batch['row_counts']==[100] and not batch['complete']
    assert '1053 条记录' in batch['text'] and '| 101 |' not in batch['text']


def test_failed_preparation_does_not_advance_and_cross_session_replay_denied(make_collection):
    s=setup(make_collection('paging'));t=begin(s)
    result=ask(s,t,limit=1)
    prepared=ask(s,t,'deliver_conversation_results',result_refs=[result['result_ref']],batch_size=10)
    s['conversation'].store.fail_turn(t,error_code='fixture',safe_message='fixture',retryable=False)
    t=begin(s)
    refused=json.loads(tools.dispatch('deliver_conversation_results',{'mode':'continue'},s['sid'],t))
    assert not refused['ok']
    # Interrupted delivery references cannot leak another session's data.
    from backend.report_collection.batches import load
    with pytest.raises(ValueError):load(s['collection'],'foreign-session',prepared['delivery_ref'])


def test_single_report_compatibility_keeps_scope_and_rejects_conflicts(collection):
    s=setup(collection);t=begin(s)
    refs={r['label']:r['report_ref'] for r in ask(s,t,'list_conversation_reports')['reports']}
    assert ask(s,t,'query_report_content',report_ref=refs['B'])['sources'][0]['label']=='B'
    for args in ({'report_ref':'forged'}, {'report_ref':refs['A'],'report_refs':[refs['B']]},
                 {'report_ref':refs['B'],'scope':'authorized_history'}):
        assert not json.loads(tools.dispatch('query_report_content',args,s['sid'],t))['ok']


def test_unknown_config_is_not_projected_as_unset_or_equal(collection):
    s=setup(collection);s['collection'].express_results=True;t=begin(s)
    result=ask(s,t,'compare_conversation_config',include_samples=False)
    assert result['configs'][0]['thresholds']['status']=='unknown'
    row=next(r for r in result['items'] if r['field']=='thresholds')
    assert not row['known'] and row['comparison_status']=='unknown_cannot_determine_change'
    raw=s['collection'].result(result['result_ref'],s['sid'],s['collection'].sources(s['sid'],t))
    assert raw['configs'][0]['thresholds'] is None # immutable raw source retained
