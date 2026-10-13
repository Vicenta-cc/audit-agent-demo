import json

import pytest

from test_report_collection import collection, creation_stack, begin, finish
from test_report_scope_repair import setup, ask, delivered
from backend.report_collection import tools
from backend.report_collection.history_query import profile_account

URL = 'https://www.douyin.com/user/same-user'


def rejected(s, t, name='query_conversation_reports', **args):
    value = json.loads(tools.dispatch(name, args, s['sid'], t))
    assert not value['ok'], value
    assert value['error']['executed'] is False
    return value


def test_exact_profile_and_history_candidates_do_not_disclose_contents(collection):
    s=setup(collection);t=begin(s)
    candidates=ask(s,t,kind='accounts',scope='authorized_history',name_contains='同名账号')
    assert len(candidates['items'])==3
    assert candidates['kind']=='account_candidates'
    assert 'C-SECRET' not in json.dumps(candidates) and 'observations' not in json.dumps(candidates)
    exact=ask(s,t,kind='accounts',scope='authorized_history',account_url=URL+'/?source=test')
    assert len(exact['items'])==1
    ref=exact['items'][0]['account_ref']
    result=ask(s,t,kind='comments',scope='authorized_history',account_url=URL+'?source=test')
    assert result['total_count']==3 and all(x['account_ref']==ref for x in result['items'])
    assert 'C-SECRET' in json.dumps(result) and 'D-SECRET' not in json.dumps(result)
    assert result['report_totals']==[]
    rejected(s,t,scope='authorized_history',account_ref=ref,account_url='https://www.douyin.com/user/b-user')
    rejected(s,t,kind='accounts',scope='authorized_history',account_url='https://douyin.com.evil.test/user/same-user')


def test_history_requires_account_and_blocks_other_read_routes(collection):
    s=setup(collection);t=begin(s)
    for args in ({'kind':'comments'}, {'kind':'comments','risk_level':'high'}, {'kind':'accounts'}):
        refused=rejected(s,t,scope='authorized_history',**args)
        assert '不是报告权限不足' in refused['error']['message']
    for kind in ('overview','posts','findings','evidence'):
        rejected(s,t,'query_report_content',scope='authorized_history',kind=kind)
    rejected(s,t,'compare_conversation_config',scope='authorized_history')
    # Current conversation still supports unrestricted detailed questions.
    assert ask(s,t,'query_report_content',kind='posts')['total_count']==2
    assert ask(s,t,risk_level='high')['total_count']==4


def test_account_plus_history_reference_inherit_and_current_reset(collection):
    s=setup(collection);t=begin(s)
    history=ask(s,t,scope='authorized_history',account_url=URL)
    ref=next(x['report_ref'] for x in history['sources'] if x['label'] not in ('A','B'))
    exact=ask(s,t,scope='authorized_history',account_url=URL,report_refs=[ref])
    assert exact['total_count']==1 and exact['items'][0]['text']=='C-SECRET'
    assert exact['scope_kind']=='authorized_history'
    finish(s,t);t=begin(s)
    inherited=ask(s,t,scope='inherit',risk_level='high')
    assert inherited['total_count']==1
    assert inherited['query_filters']['account_ref']==profile_account(URL)
    rejected(s,t,report_refs=[ref])  # default all does not implicitly guess identity
    current=ask(s,t,scope='all',risk_level='high')
    assert current['total_count']==4
    assert {x['source']['label'] for x in current['items']}=={'A','B'}
    assert {x['label']for x in ask(s,t,'list_conversation_reports')['reports']}=={'A','B'}
    rejected(s,t,scope='authorized_history',account_url=URL,report_refs=['history_report_forged'])


def test_exact_history_ignores_unrelated_revoked_history_but_rechecks_target(collection):
    s=setup(collection)
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='principal-a' WHERE task_id=?",(s['reports']['D'].task,))
    t=begin(s);history=ask(s,t,scope='authorized_history',account_url=URL)
    c=next(x for x in history['sources'] if x['report_version_id']==s['reports']['C'].version)
    finish(s,t)
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id=?",(s['reports']['D'].task,))
    t=begin(s)
    exact=ask(s,t,scope='authorized_history',account_url=URL,report_refs=[c['report_ref']])
    assert exact['total_count']==1 and exact['sources'][0]['report_version_id']==s['reports']['C'].version
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id=?",(s['reports']['C'].task,))
    rejected(s,t,scope='authorized_history',account_url=URL,report_refs=[c['report_ref']])


def test_history_zero_posts_and_risk_filtered_activity(collection):
    s=setup(collection);t=begin(s)
    history=ask(s,t,scope='authorized_history',account_url=URL)
    c=next(x['report_ref'] for x in history['sources'] if x['label'] not in ('A','B'))
    posts=ask(s,t,'query_account_activity',scope='authorized_history',account_url=URL,report_refs=[c],activity_kind='post_author')
    assert posts['total_count']==0 and posts['statistics']['original_posts']==0
    high=ask(s,t,'query_account_activity',scope='authorized_history',account_url=URL,activity_kind='comment_author',risk_level='high')
    assert high['total_count']==2 and all(x['risk_level']=='high' for x in high['items'])
    rejected(s,t,'query_account_activity',scope='all')


def test_history_delivery_pagination_and_legacy_result_cannot_bypass_contract(collection):
    s=setup(collection);t=begin(s)
    result=ask(s,t,scope='authorized_history',account_url=URL)
    first=ask(s,t,'deliver_conversation_results',result_refs=[result['result_ref']],batch_size=1)
    delivered(s,t,first);t=begin(s)
    second=ask(s,t,'deliver_conversation_results',mode='continue')
    assert second['row_counts']==[1] and second['ranges'][0]['start']==1
    replay=ask(s,t,'deliver_conversation_results',mode='replay',delivery_ref=first['delivery_ref'])
    assert replay['text']==first['text']
    with s['collection'].connect() as db:
        row=db.execute('SELECT payload FROM collection_results WHERE ref=?',(result['result_ref'],)).fetchone()
        legacy=json.loads(row[0]);legacy.pop('history_contract')
        db.execute('UPDATE collection_results SET payload=? WHERE ref=?',(json.dumps(legacy),result['result_ref']))
    rejected(s,t,'read_conversation_result',result_ref=result['result_ref'])
    rejected(s,t,'deliver_conversation_results',mode='continue')
    rejected(s,t,'read_conversation_delivery',delivery_ref=first['delivery_ref'],position=1)


@pytest.mark.parametrize('url', ['https://www.douyin.com/user/%2fother', 'https://www.douyin.com/video/123'])
def test_profile_rejects_ambiguous_identity(url):
    with pytest.raises(ValueError):profile_account(url)


@pytest.mark.parametrize('kind,profile', [('post_author','publisher'),('comment_author','same-user'),('all','publisher')])
def test_activity_results_deliver_and_continue_without_changing_kind(collection,kind,profile):
    s=setup(collection);t=begin(s)
    result=ask(s,t,'query_account_activity',scope='authorized_history',
               account_url='https://www.douyin.com/user/'+profile,activity_kind=kind)
    first=ask(s,t,'deliver_conversation_results',result_refs=[result['result_ref']],batch_size=1)
    assert first['row_counts']==[1] and 'https://www.douyin.com/video/10001' in first['text']
    assert first['references'][0]['query_filters']['activity_kind']==kind
    assert ('评论明细' if kind=='comment_author' else '账号活动') in first['text']
    delivered(s,t,first);t=begin(s)
    second=ask(s,t,'deliver_conversation_results',mode='continue')
    assert second['row_counts']==[1] and second['ranges'][0]['start']==1
    replay=ask(s,t,'deliver_conversation_results',mode='replay',delivery_ref=first['delivery_ref'])
    assert replay['text']==first['text']


def test_activity_kind_is_explicit_and_empty_posts_are_deliverable(collection):
    s=setup(collection);t=begin(s)
    rejected(s,t,'query_account_activity',account_url=URL)
    zero=ask(s,t,'query_account_activity',scope='authorized_history',account_url=URL,activity_kind='post_author')
    rendered=ask(s,t,'deliver_conversation_results',result_refs=[zero['result_ref']])
    assert rendered['complete'] and rendered['row_counts']==[0]
    assert '0 篇原始帖子' in rendered['text'] and '无符合条件的记录' in rendered['text']
