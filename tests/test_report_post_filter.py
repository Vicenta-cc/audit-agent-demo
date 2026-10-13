"""Single-post comment queries retain scope through filtering and delivery."""
import json

import pytest

import test_report_collection_analysis as analysis_fixtures
from backend.report_collection import tools
from scripts.report_diverse_cases import post
from test_investigation_creation_conversation import creation_stack
from test_report_collection import begin, collection, finish
from test_report_collection_analysis import invoke, make_collection
from test_report_scope_repair import delivered, setup


@pytest.fixture
def posts(make_collection, monkeypatch):
    datasets = analysis_fixtures.cases()
    datasets['paging']['reports']['B']['posts'].append(
        post('33004', 'empty-author', '零评论作者', 'none', []))
    monkeypatch.setattr(analysis_fixtures, 'cases', lambda: datasets)
    s = setup(make_collection('paging'))
    turn = begin(s)
    directory = invoke(s, turn, 'query_report_content', kind='posts')
    by_title = {item['title']: item for item in directory['items']}
    return s, turn, by_title


def rejected(s, turn, name='query_conversation_reports', **args):
    response = json.loads(tools.dispatch(name, args, s['sid'], turn))
    assert not response['ok'], response
    return response


def test_post_filter_precedes_preview_and_statistics_without_changing_default(posts):
    s, turn, directory = posts
    target = directory['隔离验收帖子 33001']
    all_comments = invoke(s, turn, kind='comments', preview_limit=1)
    assert all_comments['total_count'] == 27
    result = invoke(s, turn, kind='comments', post_ref=target['post_ref'], preview_limit=1)
    assert result['total_count'] == 15 and result['has_more']
    assert len(result['items']) == 1
    assert result['query_statistics']['comments']['original_comments'] == 15
    assert result['query_statistics']['comments']['completed_assessments'] == 15
    assert result['query_filters']['post_ref'] == target['post_ref']
    assert result['query_filters']['post_title'] == target['title']
    page = invoke(s, turn, 'read_conversation_result', result_ref=result['result_ref'], offset=1)
    assert len(page['items']) == 14
    assert {item['post_ref'] for item in page['items']} == {target['post_ref']}
    assert page['query_filters'] == result['query_filters']
    assert page['query_statistics'] == result['query_statistics']
    independent = invoke(s, turn, scope='all')
    assert independent['total_count'] == 27
    assert not independent['query_filters'].get('post_ref')


def test_post_risk_intersection_is_not_report_wide(posts):
    s, turn, directory = posts
    ref = directory['隔离验收帖子 33001']['post_ref']
    result = invoke(s, turn, post_ref=ref, risk_level='high')
    assert result['total_count'] == 3  # 05, 10, 15; report-wide high would be five.
    assert result['query_statistics']['comments']['original_comments'] == 3
    assert {item['post_ref'] for item in result['items']} == {ref}
    assert {item['risk_level'] for item in result['items']} == {'high'}


def test_known_zero_comment_post_is_valid_and_preserves_title(posts):
    s, turn, directory = posts
    target = directory['隔离验收帖子 33004']
    result = invoke(s, turn, post_ref=target['post_ref'])
    assert result['total_count'] == 0 and result['items'] == []
    assert result['query_statistics']['comments']['original_comments'] == 0
    assert result['query_filters']['post_title'] == target['title']
    output = invoke(s, turn, 'deliver_conversation_results', result_refs=[result['result_ref']])
    assert target['title'] in output['text']
    assert '无符合条件的记录' in output['text']


@pytest.mark.parametrize('scope', ['all', 'authorized_history'])
def test_post_ref_is_rejected_for_account_queries_before_candidate_shortcut(posts, scope):
    s, turn, directory = posts
    rejected(s, turn, kind='accounts', scope=scope, name_contains='常来看看',
             post_ref=directory['隔离验收帖子 33001']['post_ref'])


def test_forged_post_is_not_silently_reported_as_empty(posts):
    s, turn, _ = posts
    rejected(s, turn, post_ref='post-does-not-exist')


def test_post_reference_cannot_cross_selected_report_boundary(make_collection):
    s = setup(make_collection('config'))
    turn = begin(s)
    reports = invoke(s, turn, 'list_conversation_reports')['reports']
    refs = {item['label']: item['report_ref'] for item in reports}
    b_posts = invoke(s, turn, 'query_report_content', kind='posts', report_refs=[refs['B']])
    b_ref = b_posts['items'][0]['post_ref']
    # Even the same original post in A has a distinct frozen report reference.
    rejected(s, turn, post_ref=b_ref, report_refs=[refs['A']])
    assert invoke(s, turn, post_ref=b_ref, report_refs=[refs['B']])['total_count'] == 2


def test_followup_preserves_post_and_cannot_switch_to_another_post(posts):
    s, turn, directory = posts
    first_post = directory['隔离验收帖子 33001']
    second_post = directory['隔离验收帖子 33002']
    result = invoke(s, turn, post_ref=first_post['post_ref'])
    finish(s, turn)
    turn = begin(s)
    narrowed = invoke(s, turn, from_result_ref=result['result_ref'], risk_level='high')
    assert narrowed['total_count'] == 3
    assert narrowed['query_filters']['post_ref'] == first_post['post_ref']
    assert narrowed['query_filters']['post_title'] == first_post['title']
    assert {item['post_ref'] for item in narrowed['items']} == {first_post['post_ref']}
    rejected(s, turn, from_result_ref=result['result_ref'], post_ref=second_post['post_ref'])
    # A fresh query may intentionally select the other post.
    assert invoke(s, turn, scope='all', post_ref=second_post['post_ref'])['total_count'] == 12


def test_full_result_can_be_narrowed_to_post_and_empty_parent_stays_empty(posts):
    s, turn, directory = posts
    ref = directory['隔离验收帖子 33001']['post_ref']
    all_comments = invoke(s, turn)
    narrowed = invoke(s, turn, from_result_ref=all_comments['result_ref'], post_ref=ref)
    assert narrowed['total_count'] == 15
    empty = invoke(s, turn, post_ref=ref, risk_level='none')
    assert empty['total_count'] == 0
    followed = invoke(s, turn, from_result_ref=empty['result_ref'])
    assert followed['total_count'] == 0
    assert followed['query_filters']['post_ref'] == ref


def test_delivery_continue_replay_keep_post_title_and_selected_rows(posts):
    s, turn, directory = posts
    target = directory['隔离验收帖子 33001']
    result = invoke(s, turn, post_ref=target['post_ref'], preview_limit=1)
    first = invoke(s, turn, 'deliver_conversation_results', result_refs=[result['result_ref']], batch_size=10)
    assert first['row_counts'] == [10] and not first['complete']
    assert target['title'] in first['text']
    delivered(s, turn, first)
    turn = begin(s)
    second = invoke(s, turn, 'deliver_conversation_results', mode='continue')
    assert second['row_counts'] == [5] and second['complete']
    assert second['ranges'][0]['start'] == 10 and second['ranges'][0]['end'] == 15
    assert target['title'] in second['text']
    assert '排队记录16' not in second['text']
    delivered(s, turn, second)
    turn = begin(s)
    replay = invoke(s, turn, 'deliver_conversation_results', mode='replay', delivery_ref=first['delivery_ref'])
    assert replay['text'] == first['text']


def test_post_query_and_prior_result_recheck_revoked_access(posts):
    s, turn, directory = posts
    ref = directory['隔离验收帖子 33001']['post_ref']
    result = invoke(s, turn, post_ref=ref)
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id=?", (s['reports']['B'].task,))
    rejected(s, turn, post_ref=ref)
    rejected(s, turn, 'read_conversation_result', result_ref=result['result_ref'])
    rejected(s, turn, 'deliver_conversation_results', result_refs=[result['result_ref']])


def test_history_post_filter_stays_account_bound_and_does_not_expose_title(collection):
    s = setup(collection)
    turn = begin(s)
    account_url = 'https://www.douyin.com/user/same-user'
    history = invoke(s, turn, scope='authorized_history', account_url=account_url)
    c_comment = next(item for item in history['items']
                     if item['source']['report_version_id'] == s['reports']['C'].version)
    ref = c_comment['post_ref']
    result = invoke(s, turn, scope='authorized_history', account_url=account_url, post_ref=ref)
    assert result['total_count'] == 1
    assert result['items'][0]['account_ref'] == c_comment['account_ref']
    assert result['query_filters']['post_ref'] == ref
    assert not result['query_filters'].get('post_title')
    output = invoke(s, turn, 'deliver_conversation_results', result_refs=[result['result_ref']])
    assert '指定帖子' in output['text']
    assert '同一帖子' not in output['text']
    rejected(s, turn, scope='authorized_history', post_ref=ref)
    rejected(s, turn, scope='all', post_ref=ref)
