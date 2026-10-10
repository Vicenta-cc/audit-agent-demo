from copy import deepcopy
import json
import pytest

from scripts.report_expression_trial import grouped, project


def test_grouping_preserves_order_provenance_and_never_merges_missing_ids():
    rows = [dict(evidence_key='shared', comment_ref='A', account_ref='x', source={'report': 'A'}),
            dict(evidence_key=None, text='same', account_ref=''),
            dict(evidence_key='shared', comment_ref='B', account_ref='x', source={'report': 'B'}),
            dict(evidence_key=None, text='same', account_ref='')]
    before = deepcopy(rows)
    groups = grouped(rows)
    assert len(groups) == 3 and groups[0]['visible_observation_count'] == 2
    flattened = sorted([r for g in groups for r in g['report_observations']], key=lambda r: r['source_result_position'])
    for i, row in enumerate(flattened):
        assert row.pop('source_result_position') == i
        assert row.pop('identity_status') == ('resolved' if before[i]['account_ref'] else 'unknown')
        assert row == before[i] and 'reason' not in row
    assert rows == before


def request_for(data, name='query_conversation_reports', args=None):
    return {'tools': ['unchanged'], 'messages': [
        {'role': 'system', 'content': 'original'},
        {'role': 'assistant', 'tool_calls': [{'id': 'c', 'function': {'name': name, 'arguments': json.dumps(args or {})}}]},
        {'role': 'tool', 'tool_call_id': 'c', 'content': json.dumps({'ok': True, 'data': data})}]}


def data_of(request):
    return json.loads(request['messages'][-1]['content'])['data']


def test_filters_and_paging_are_not_misrepresented_as_whole_report():
    data = dict(kind='accounts', result_ref='r', items=[], total_count=4, has_more=False, next_offset=4,
                sources=[{'report_ref': 'B'}], unique_evidence_count=0)
    request = request_for(data, args={'risk_level': 'high', 'from_result_ref': 'parent'})
    out, _ = project(request, express=True)
    coverage = data_of(out)['query_coverage']
    assert coverage['actual_filters']['risk_level'] == 'high'
    assert coverage['actual_filters']['from_result_ref'] == 'parent'
    assert coverage['page_covers_complete_query'] is False
    unknown, _ = project(request_for(data, 'read_conversation_result', {'result_ref': 'missing'}), express=True)
    assert data_of(unknown)['query_coverage']['filters_known'] is False


def test_known_statistic_correction_is_identical_in_both_arms_and_not_from_truncated_preview():
    data = dict(kind='accounts', items=[dict(observations=[{'evidence_key': 'e', 'account_ref': 'a'}],
                observations_truncated=False)], total_count=1, next_offset=1, has_more=False, unique_evidence_count=5)
    req = request_for(data)
    before = deepcopy(req)
    baseline, fixes = project(req)
    expressed, same = project(req, express=True)
    assert fixes == same and fixes[0]['new'] == 1
    assert data_of(baseline)['unique_evidence_count'] == data_of(expressed)['unique_evidence_count'] == 1
    assert req == before
    data['items'][0]['observations_truncated'] = True
    unchanged, fixes = project(request_for(data), express=True)
    assert not fixes and data_of(unchanged)['unique_evidence_count'] == 5
    assert not data_of(unchanged)['query_coverage']['account_observation_previews_complete']


def test_readback_retains_original_query_filter_metadata():
    data = dict(kind='comments', result_ref='r', items=[], total_count=0, has_more=False, next_offset=0)
    first = request_for(data, args={'risk_level': 'high'})
    second = request_for(data, 'read_conversation_result', {'result_ref': 'r'})
    second['messages'][1]['tool_calls'][0]['id'] = 'd'
    second['messages'][2]['tool_call_id'] = 'd'
    first['messages'].extend(second['messages'][1:])
    out, _ = project(first, express=True)
    assert data_of(out)['query_coverage']['actual_filters']['risk_level'] == 'high'


def test_hermes_dispatcher_uses_business_arguments_not_wrapper_arguments():
    data = dict(kind='accounts', result_ref='r', items=[], total_count=0, next_offset=0, has_more=False)
    req = request_for(data, 'tool_call', {'name': 'query_conversation_reports',
                                       'arguments': {'risk_level': 'high', 'common_only': False}})
    out, _ = project(req, express=True)
    coverage = data_of(out)['query_coverage']
    assert coverage['filters_known'] and coverage['actual_filters']['risk_level'] == 'high'


def test_archive_continuation_refuses_different_query_or_partial_page():
    from scripts.report_expression_continue import saved_response
    data = dict(kind='comments', result_ref='r', items=[{}], total_count=1, next_offset=1, has_more=False)
    req = request_for(data, args={'kind': 'comments', 'report_refs': ['B'], 'risk_level': 'high'})
    def call(name, args):
        return {'function': {'name': 'tool_call', 'arguments': json.dumps({'name': name, 'arguments': args})}}
    same = call('query_conversation_reports', {'kind': 'comments', 'report_refs': ['B'], 'risk_level': 'high', 'limit': 50})
    assert json.loads(saved_response(req, same))['data']['result_ref'] == 'r'
    with pytest.raises(AssertionError):
        saved_response(req, call('query_conversation_reports', {'kind': 'comments', 'report_refs': ['A']}))
    data.update(total_count=3, next_offset=3)
    with pytest.raises(AssertionError):
        saved_response(request_for(data), call('read_conversation_result', {'result_ref': 'r'}))
