from copy import deepcopy
import json

from scripts.report_original_scene import api_message, expression_only


def test_transport_reconstruction_preserves_text_and_argument_semantics():
    original = dict(role='assistant', content='原始回答', timestamp=12, finish_reason='tool_calls', reasoning=None,
                    tool_calls=[dict(id='c', call_id='c', response_item_id='x', type='function',
                                     function=dict(name='tool_call', arguments='{"z": 1, "a": "中文"}'))])
    before = deepcopy(original)
    out = api_message(original)
    assert out['content'] == original['content'] and out['reasoning_content'] == ' '
    assert json.loads(out['tool_calls'][0]['function']['arguments']) == {'a':'中文','z':1}
    assert 'call_id' not in out['tool_calls'][0] and original == before


def test_expression_preserves_original_bug_instead_of_normalizing_baseline():
    payload = dict(ok=True, data=dict(kind='accounts', result_ref='r', total_count=1,next_offset=1,has_more=False,
        unique_evidence_count=5, items=[dict(observations_truncated=False,
            observations=[dict(evidence_key='e', account_ref='a', source={'report_ref':'B'})])]))
    request = dict(messages=[dict(role='assistant', content='keep history', tool_calls=[dict(id='c',function=dict(
        name='tool_call', arguments=json.dumps({'name':'query_conversation_reports','arguments':{'risk_level':'high'}})))]),
        dict(role='tool',tool_call_id='c',content=json.dumps(payload))])
    before=deepcopy(request)
    changed=expression_only(request)
    data=json.loads(changed['messages'][1]['content'])['data']
    assert data['unique_evidence_count']==5
    assert data['query_coverage']['actual_filters']['risk_level']=='high'
    assert changed['messages'][0]==request['messages'][0] and request==before
