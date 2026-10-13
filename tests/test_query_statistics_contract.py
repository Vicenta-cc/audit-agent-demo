"""Filtered statistics are complete, explicit about units, and scope-safe."""
import json
from copy import deepcopy
from test_report_collection import collection, creation_stack, begin, call
from test_report_collection_analysis import make_collection
from test_report_scope_repair import setup, ask
from backend.report_collection.expression import express_result
from backend.report_collection.analysis import query_statistics


def test_duplicate_basis_uses_identity_not_text_and_all_reports():
    sources = [{'ref': r, 'label': r, 'version': 'v-' + r} for r in 'ABC']
    def row(key, report, text='同文不等于同一评论'):
        return {'kind': 'comment_author', 'evidence_key': key, 'text': text,
                'audit_status': 'completed', 'source': {'report_ref': report}}
    rows = [row('shared', r, '真正重复的评论') for r in 'ABC']
    rows += [row('different-' + r, r) for r in 'ABC']
    rows += [row('', 'A'), row('', 'B')]
    summary = query_statistics(rows, sources, include_posts=False)['comments']
    assert (summary['report_observations'], summary['original_comments']) == (8, 6)
    basis = summary['duplication']
    assert basis['repeated_original_count'] == 1
    assert basis['extra_observations'] == 2
    assert basis['unresolved_identity_observations'] == 2
    assert basis['groups_complete'] is True
    assert basis['groups'] == [{'original_evidence_key': 'shared',
        'report_observations': 3, 'extra_observations': 2,
        'by_report': [{'source': {'report_ref': r, 'label': r, 'report_version_id': 'v-' + r},
                       'report_observations': 1} for r in 'ABC']}]


def test_duplicate_basis_is_bounded_but_totals_are_complete():
    sources = [{'ref': r, 'label': r, 'version': r} for r in 'AB']
    rows = [{'kind': 'post_author', 'evidence_key': str(i), 'source': {'report_ref': r}}
            for i in range(25) for r in 'AB']
    data = query_statistics(rows, sources, include_comments=False)
    basis = data['posts']['duplication']
    assert basis['repeated_original_count'] == basis['extra_observations'] == 25
    assert len(basis['groups']) == 20 and basis['groups_complete'] is False
    assert basis['omitted_group_count'] == 5
    assert 'duplication' not in data['comments']


def test_filtered_counts_separate_population_and_survive_pages(collection):
    s=setup(collection);s['collection'].express_results=True;t=begin(s)
    data=ask(s,t,account_url='https://www.douyin.com/user/same-user',limit=1)
    counts=data['query_statistics']['comments']
    assert counts['report_observations']==2 and counts['original_comments']==1
    assert [r['report_observations'] for r in counts['by_report']]==[1,1]
    assert [r['all_comment_count'] for r in data['report_context']['reports']]==[3,2]
    assert data['has_more'] and len(data['items'])==1
    assert data['query_statistics']['posts']['status']=='not_included_in_this_query'
    assert not {'report_totals','comment_counts','unique_evidence_count'} & data.keys()
    tail=ask(s,t,'read_conversation_result',result_ref=data['result_ref'],offset=1,limit=1)
    assert tail['query_statistics']==data['query_statistics']
    assert tail['report_context']==data['report_context']
    raw=s['collection'].result(data['result_ref'],s['sid'],s['collection'].sources(s['sid'],t))
    assert raw['report_totals'][0]['all_comment_count']==3 and raw['unique_evidence_count']==1
    assert len(raw['items'])==2 and raw['query_statistics']==data['query_statistics']


def test_risk_zero_is_counted_but_excluded_role_is_not_zero(collection):
    s=setup(collection);s['collection'].express_results=True;t=begin(s)
    high=ask(s,t,account_url='https://www.douyin.com/user/same-user',risk_level='high')
    counts=high['query_statistics']['comments']
    assert counts['report_observations']==counts['original_comments']==1
    assert [r['report_observations'] for r in counts['by_report']]==[0,1]
    assert counts['status']=='counted'
    author=ask(s,t,kind='accounts',account_role='comment_author',account_url='https://www.douyin.com/user/same-user')
    assert author['items'][0]['query_statistics']['posts']['status']=='not_included_in_this_query'
    assert 'unique_post_count' not in author['items'][0]
    assert author['items'][0]['roles_in_filtered_result']==['comment_author']
    narrowed=ask(s,t,kind='accounts',from_result_ref=high['result_ref'])
    assert narrowed['query_statistics']['posts']['status']=='not_included_in_this_query'


def test_complete_counts_not_twenty_observation_preview(collection,monkeypatch):
    s=setup(collection);t=begin(s)
    rows=[{'account_ref':'account-x','account_name':'X','kind':'comment_author',
           'evidence_key':f'evidence-{i}','risk_level':'high','audit_status':'completed',
           'source':{'report_ref':source['ref']}}
          for source in s['collection'].sources(s['sid'],t) for i in range(25)]
    monkeypatch.setattr(s['collection'],'observations',lambda sources:rows)
    raw=call(s,t,'query_conversation_reports',kind='accounts',scope='all',common_only=True,limit=1)
    data=express_result(raw);account=data['items'][0]
    assert account['observations_truncated']
    assert account['query_statistics']['comments']['original_comments']==25
    assert account['query_statistics']['comments']['report_observations']==50
    assert account['query_statistics']==data['query_statistics']
    assert account['query_statistics']['posts']['status']=='counted'
    assert account['query_statistics']['posts']['original_posts']==0
    assert raw['items'][0]['observation_count']==50 # raw consumer compatibility


def test_top_ties_count_only_matching_accounts(make_collection):
    s=setup(make_collection('identity'));t=begin(s)
    ranked=ask(s,t,kind='accounts',account_role='comment_author',sort_by='comment_count',top_n=1,limit=1)
    assert ranked['matching_count_before_top']==3 and ranked['total_count']==2
    assert ranked['query_statistics']['comments']['original_comments']==4


def test_common_only_excludes_nonmatching_account_evidence(collection):
    # Intersection rejects evidence from accounts absent in the other report.
    s=setup(collection);t=begin(s)
    common=ask(s,t,kind='accounts',common_only=True,limit=1)
    assert common['query_statistics']['comments']['original_comments']==1
    assert common['query_statistics']['posts']['original_posts']==1


def test_history_does_not_gain_population_statistics_and_old_results_stay_honest(collection):
    s=setup(collection);s['collection'].express_results=True;t=begin(s)
    known=ask(s,t,kind='accounts',account_url='https://www.douyin.com/user/same-user')['items'][0]['account_ref']
    history=ask(s,t,kind='comments',account_ref=known,scope='authorized_history')
    assert history['query_statistics']['comments']['report_observations']==3
    assert history['report_context']['reports']==[] and not history['report_context']['available']
    assert 'D-SECRET' not in json.dumps(history)
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id='task-C'")
    from backend.report_collection.tools import dispatch
    assert not json.loads(dispatch('read_conversation_result',{'result_ref':history['result_ref']},s['sid'],t))['ok']
    old={'kind':'comments','items':[],'total_count':50,'has_more':True,'next_offset':0,'unique_evidence_count':49}
    before=deepcopy(old);projected=express_result(old)
    assert 'query_statistics' not in projected and not projected['query_coverage']['filters_known']
    assert old==before


def test_activity_uses_same_statistics_without_changing_evidence(collection):
    s=setup(collection);s['collection'].express_results=True;t=begin(s)
    data=ask(s,t,'query_account_activity',account_url='https://www.douyin.com/user/same-user',activity_kind='comment_author',limit=1)
    assert data['query_statistics']['comments']['report_observations']==2
    assert data['query_statistics']['comments']['original_comments']==1
    assert data['query_statistics']['posts']['status']=='not_included_in_this_query'
    assert 'statistics' not in data and data['has_more']
    raw=s['collection'].result(data['result_ref'],s['sid'],s['collection'].sources(s['sid'],t))
    assert data['items']==raw['items'][:1]
    assert raw['statistics']['original_comments']==1
