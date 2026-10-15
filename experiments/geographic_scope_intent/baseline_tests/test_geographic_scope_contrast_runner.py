"""Offline checks for the hard request budget and contrast isolation."""
from copy import deepcopy
import json

import pytest

from experiments.geographic_scope_intent.data import load_cases
from experiments.geographic_scope_intent.runner import ScriptedProvider, request_for
from experiments.geographic_scope_intent.contrast_runner import (
    BudgetProvider, make_schedule, run_arm,
)


def case():
    return next(c for c in load_cases() if c['case_id'] == 'city-kelamayi')


def reply(name=None, arguments=None, text=None):
    message = {'role':'assistant','content':text}
    if name:
        message['tool_calls'] = [{'id':'call-1','type':'function','function':{
            'name':name,'arguments':json.dumps(arguments,ensure_ascii=False)}}]
    return {'model':'deepseek-v4-pro-ga-260813','choices':[{'message':message}]}


def test_budget_is_consumed_by_failures_without_retry(tmp_path):
    class Failure:
        mode = 'real'
        def complete(self, request):
            raise TimeoutError('Do not save exception text or headers')
    provider = BudgetProvider(Failure(),tmp_path/'ledger.jsonl',max_requests=2)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            provider.complete({'messages':[]})
    with pytest.raises(RuntimeError):
        provider.complete({'messages':[]})
    assert provider.attempted == 2
    assert len((tmp_path/'ledger.jsonl').read_text().splitlines()) == 4


def test_three_consecutive_interface_failures_stop(tmp_path):
    provider = BudgetProvider(ScriptedProvider([{}, {}, {}]),tmp_path/'ledger.jsonl')
    for _ in range(3):
        provider.complete({'messages':[]})
    assert provider.stop_reason == 'three_consecutive_runtime_failures'
    assert provider.attempted == 3


def test_wrong_returned_model_stops_without_new_call(tmp_path):
    provider = BudgetProvider(ScriptedProvider([{'model':'other','choices':[]}]),tmp_path/'ledger.jsonl')
    with pytest.raises(RuntimeError):
        provider.complete({'messages':[]})
    assert provider.stop_reason == 'returned_model_mismatch'
    assert provider.attempted == 1


def test_schedule_is_complete_reproducible_and_paired():
    cases = [c for c in load_cases() if c['split'] == 'dev']
    schedule = make_schedule(cases)
    assert schedule == make_schedule(cases)
    assert len(schedule) == 54
    assert len({(s['case_id'],s['arm']) for s in schedule}) == 54
    for index in range(0,54,3):
        assert len({s['case_id'] for s in schedule[index:index+3]}) == 1
        assert {s['arm'] for s in schedule[index:index+3]} == {'full','bridge','simple'}


def test_full_initial_request_is_frozen_baseline(tmp_path):
    class Capture:
        mode = 'scripted'
        def complete(self, request):
            assert request == request_for(case())
            return reply(text='需要核对资源。')
    trace = run_arm(case(),Capture(),'full')
    assert trace['termination'] == 'final'
    assert trace['arm'] == 'full'


def test_simple_stops_after_one_record_no_ack_request():
    c=case()
    args={'action':'set','target_text':'克拉玛依','selection_policy':'local_first_with_supplement',
          'source_quote':c['current_user']['content']}
    provider=ScriptedProvider([reply('record_geographic_intent',args),reply(text='Must not be used')])
    trace=run_arm(c,provider,'simple')
    assert trace['termination'] == 'recorded'
    assert len(trace['requests']) == len(trace['responses']) == 1
    assert len(trace['calls']) == 1
    assert trace['calls'][0]['receipt']['mutation_applied'] is False


def test_simple_blocks_business_tool_even_when_generated():
    trace=run_arm(case(),ScriptedProvider([reply('confirm_and_queue_investigation',{})]),'simple')
    assert trace['termination'] == 'unknown_tool'
    assert trace['calls'][0]['receipt']['ok'] is False


def test_bridge_records_then_continues_in_existing_four_response_limit():
    records=reply('record_geographic_intent',{'action':'keep'})
    records['choices'][0]['message']['tool_calls'][0]['id']='observer'
    trace=run_arm(case(),ScriptedProvider([records,reply(text='模拟记录已准备。')]),'bridge')
    assert trace['termination'] == 'final'
    assert len(trace['requests']) == 2
    assert trace['calls'][0]['name'] == 'record_geographic_intent'


def test_secret_value_is_redacted_in_checkpoint(tmp_path):
    provider=BudgetProvider(ScriptedProvider([reply(text='private-test-value')]),
                            tmp_path/'ledger.jsonl',secret='private-test-value')
    with pytest.raises(RuntimeError):
        provider.complete({'messages':[]})
    assert 'private-test-value' not in (tmp_path/'ledger.jsonl').read_text()
    assert provider.stop_reason == 'sensitive_output_detected'
