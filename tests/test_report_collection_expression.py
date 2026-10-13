"""The selected expression baseline preserves evidence, paging and authorization."""
import json

from test_report_collection import collection, begin, call, finish
from test_investigation_creation_conversation import creation_stack
from backend.report_collection.tools import dispatch
from backend.report_collection.expression import express_result


def invoke(s, turn, name='query_conversation_reports', **args):
    result = json.loads(dispatch(name, args, s['sid'], turn))
    assert result['ok'], result
    return result['data']


def test_grouped_results_keep_raw_references_for_followups(collection):
    s = collection
    s['collection'].agent_driven = True
    t = begin(s)
    data = invoke(s, t)
    assert data['total_count'] == 5
    assert data['query_statistics']['comments']['original_comments'] == 4
    assert len(data['items']) == 4
    shared = next(g for g in data['items'] if g['visible_observation_count'] == 2)
    assert {r['risk_level'] for r in shared['report_observations']} == {'low', 'high'}
    rows = [r for g in data['items'] for r in g['report_observations']]
    assert sum(r['identity_status'] == 'unknown' for r in rows) == 1
    with s['collection'].connect() as db:
        raw = json.loads(db.execute('SELECT payload FROM collection_results WHERE ref=?',
                                   (data['result_ref'],)).fetchone()[0])
    restored = sorted(rows, key=lambda r: r['source_result_position'])
    assert [{k: v for k, v in r.items() if k not in ('identity_status', 'source_result_position')}
            for r in restored] == raw['items']
    finish(s, t)
    t = begin(s)
    followup = invoke(s, t, from_result_ref=data['result_ref'])
    assert followup['total_count'] == 5
    assert followup['query_coverage']['actual_filters']['parent_result_ref'] == data['result_ref']
    accounts = invoke(s, t, kind='accounts', common_only=True)
    assert accounts['total_count'] == 2
    assert all('evidence_entities' in a and 'observations' not in a for a in accounts['items'])


def test_paged_read_retains_filters_and_does_not_claim_complete_query(collection):
    s = collection
    s['collection'].agent_driven = True
    t = begin(s)
    data = invoke(s, t, risk_level='high', limit=1)
    assert data['total_count'] == 4
    assert not data['query_coverage']['page_covers_complete_query']
    finish(s, t)
    t = begin(s)
    tail = invoke(s, t, 'read_conversation_result', result_ref=data['result_ref'], offset=1, limit=50)
    assert not tail['has_more']
    assert tail['query_coverage']['actual_filters']['risk_level'] == 'high'
    assert not tail['query_coverage']['page_covers_complete_query']
    whole = invoke(s, t, 'read_conversation_result', result_ref=data['result_ref'], offset=0, limit=50)
    assert whole['query_coverage']['page_covers_complete_query']
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id='task-A'")
    denied = json.loads(dispatch('read_conversation_result', {'result_ref':data['result_ref']}, s['sid'], t))
    assert not denied['ok']


def test_expression_matches_historical_trial_and_old_filters_stay_unknown(collection):
    from scripts.report_original_scene import expression_only
    s = collection
    t = begin(s)
    args = {'kind': 'comments', 'scope': 'all', 'risk_level': 'high'}
    raw = call(s, t, 'query_conversation_reports', **args)
    request = {'messages': [
        {'role': 'assistant', 'tool_calls': [{'id':'q', 'function': {
            'name':'query_conversation_reports', 'arguments':json.dumps(args)}}]},
        {'role': 'tool', 'tool_call_id':'q', 'content':json.dumps({'ok':True, 'data':raw})}]}
    expected = json.loads(expression_only(request)['messages'][-1]['content'])['data']
    filters = {k: args.get(k) for k in ('risk_level','account_ref','common_only','from_result_ref')}
    filters['parent_result_ref'] = None
    assert express_result({**raw, 'query_filters':filters}) == expected
    # An older frozen query has no filter metadata; never manufacture an all-data claim.
    assert express_result(raw)['query_coverage']['filters_known'] is False
    constrained = invoke(s, t, scope='all')
    assert 'query_coverage' not in constrained
    s['collection'].agent_driven = True
    s['collection'].express_results = False
    historical_control = invoke(s, t)
    assert 'query_coverage' not in historical_control and 'query_filters' not in historical_control


def test_post_lookup_reports_search_state_without_selecting_target(collection):
    s = collection
    s['collection'].agent_driven = True
    t = begin(s)
    directory = invoke(s, t, 'query_report_content', kind='posts', limit=1)
    assert directory['post_lookup']['state'] == 'directory'
    assert directory['has_more']
    later = invoke(s, t, 'read_conversation_result', result_ref=directory['result_ref'], offset=1)
    assert later['post_lookup']['state'] == 'directory'
    assert not later['post_lookup']['page_covers_all_matches']
    target = later['items'][-1]
    found = invoke(s, t, 'query_report_content', kind='posts', post_ref=target['post_ref'])
    assert found['post_lookup']['state'] == 'single_filtered_match'
    assert found['post_lookup']['page_covers_all_matches']
    assert found['items'][0]['post_ref'] == target['post_ref']
    empty = invoke(s, t, 'query_report_content', kind='posts', text_contains='absent-title-XYZ')
    assert empty['post_lookup']['state'] == 'no_filtered_match'
    with s['collection'].connect() as db:
        raw = json.loads(db.execute('SELECT payload FROM collection_results WHERE ref=?',
                                   (found['result_ref'],)).fetchone()[0])
    assert 'post_lookup' not in raw
    assert found['items'] == raw['items']
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id IN ('task-A','task-B')")
    denied = json.loads(dispatch('read_conversation_result', {'result_ref':found['result_ref']}, s['sid'], t))
    assert not denied['ok']


def test_post_lookup_does_not_confuse_preview_filter_or_record_identity():
    from copy import deepcopy
    base = {'kind':'report_posts', 'query_filters':{'text_contains':'needle'},
            'coverage':{'complete':True}, 'total_count':2, 'has_more':False, 'next_offset':2,
            'items':[{'title':'one','body':'needle','post_ref':'A'},
                     {'title':'one','source':{'label':'needle'},'post_ref':'B'}]}
    snapshot = deepcopy(base)
    data = express_result(base)
    assert base == snapshot
    assert data['post_lookup']['state'] == 'multiple_filtered_matches'
    assert data['post_lookup']['count_unit'] == 'report_post_records_not_deduplicated_original_posts'
    assert 'title_body_source' in data['post_lookup']['text_match_basis']
    data = express_result({**base,'query_filters':{'risk_level':'high'},'total_count':1,'items':base['items'][:1]})
    assert data['post_lookup']['state'] == 'directory'
    assert express_result({**base,'coverage':{'complete':False}})['post_lookup']['state'] == 'incomplete_search'
    old = dict(base);old.pop('query_filters')
    assert express_result(old)['post_lookup']['state'] == 'unknown'
    tail = express_result({**base,'items':base['items'][1:]})
    assert not tail['post_lookup']['page_covers_all_matches']
