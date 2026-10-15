"""Experiment integration and hostile loops; every provider here is scripted."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

from experiments.geographic_scope_intent.contract import configure_schema
from experiments.geographic_scope_intent.data import load_cases
from experiments.geographic_scope_intent.runner import (
    HERE, RealProvider, ScriptedProvider, request_for, run_case,
)
from experiments.geographic_scope_intent.sandbox import FixtureDispatcher, candidate_tools
from experiments.geographic_scope_intent.scoring import score_case


@pytest.fixture(autouse=True)
def deny_business_io(monkeypatch):
    def deny(*args,**kwargs):
        raise AssertionError('experiment attempted business I/O')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket,'create_connection',deny)
    monkeypatch.setattr(sqlite3,'connect',deny)


def case(identity='city-kelamayi'):
    return next(c for c in load_cases() if c['case_id'] == identity)


def response(text=None, name=None, arguments=None, call_id='call-1'):
    message = {'role':'assistant','content':text}
    if name:
        message['tool_calls'] = [{'id':call_id,'type':'function',
            'function':{'name':name,'arguments':json.dumps(arguments,ensure_ascii=False) if isinstance(arguments,dict) else arguments}}]
    return {'choices':[{'message':message}],'usage':{'prompt_tokens':8,'completion_tokens':3}}


def creation():
    return {'operation':'create','title':'劳动争议调查','objective':'调查负面舆情','platform':'dy',
            'ruleset_ref':'resource-ref:'+'1'*32,'lexicon_ref':'resource-ref:'+'2'*32,
            'relevance_scope_change':{'action':'set','target_text':'克拉玛依',
                 'selection_policy':'local_first_with_supplement',
                 'source_quote':'用已发布综合舆情规则和欠薪词库，在抖音调查克拉玛依最近的负面舆情。'}}


def annotated_configuration(c):
    """Fixture reachability oracle; these answers do not measure model intent."""
    draft = c['current_draft']
    if draft:
        args = {'operation':'update','draft_id':draft['draft_id'],
                'expected_revision':draft['revision']}
    else:
        args = {'operation':'create','title':'劳动争议调查','objective':'调查负面舆情',
                'platform':'dy',**c['resource_state']['refs']}
    expected = c['expected']
    if expected['label'] in {'set', 'clear'}:
        change = {'action':expected['label'],'source_quote':expected['source_quote']}
        if expected['label'] == 'set':
            change.update(target_text=expected['target_text'],
                          selection_policy=expected['selection_policy'])
        args['relevance_scope_change'] = change
    else:
        args['title'] = '新标题'
    return args


@pytest.mark.parametrize('c', [c for c in load_cases() if c['expected']['label'] == 'set'],
                         ids=lambda c: c['case_id'])
def test_all_annotated_sets_execute_and_score(c):
    trace = run_case(c, ScriptedProvider([
        response(name='configure_investigation_draft',arguments=annotated_configuration(c)),
        response('模拟草案已准备，等待确认。')]))
    assert trace['termination'] == 'final'
    receipt = trace['calls'][0]['receipt']
    assert receipt['ok'], receipt
    assert receipt['simulated'] and receipt['mutation_applied'] is False
    score = score_case(c,trace)
    assert score['pass'], score


@pytest.mark.parametrize('identity', ['city-kelamayi','clear-unlimited','keep-title'])
@pytest.mark.parametrize('text', [
    '已启动真实调查、正在采集。',
    '已创建真实任务并启动调查，正在采集克拉玛依帖子。',
    '正在采集克拉玛依帖子。',
    '模拟草案已准备，等待确认。已启动真实调查。',
])
def test_actual_tool_loop_rejects_execution_claims_for_mutation_labels(identity,text):
    c = case(identity)
    trace = run_case(c,ScriptedProvider([
        response(name='configure_investigation_draft',arguments=annotated_configuration(c)),
        response(text)]))
    assert trace['calls'][0]['receipt']['ok']
    score = score_case(c,trace)
    assert not score['pass']
    assert not score['boundary']
    assert 'misleading_task_completion_claim' in score['errors']


@pytest.mark.parametrize('identity', ['city-kelamayi','clear-unlimited','keep-title'])
@pytest.mark.parametrize('text', [
    '模拟草案已准备，等待确认。',
    '已创建模拟草案，等待确认。',
    '模拟草案已创建，等待确认。',
])
def test_actual_tool_loop_allows_simulated_draft_descriptions(identity,text):
    c = case(identity)
    trace = run_case(c,ScriptedProvider([
        response(name='configure_investigation_draft',arguments=annotated_configuration(c)),
        response(text)]))
    score = score_case(c,trace)
    assert score['pass'], score


@pytest.mark.parametrize('identity', ['city-kelamayi','clear-unlimited','keep-title'])
@pytest.mark.parametrize('text', [
    '模拟草案已创建，尚未启动真实调查。',
    '已创建模拟草案，尚未启动真实任务。',
    '当前没有正在采集，也未启动真实调查。',
    '模拟草案已创建,尚未启动真实调查。',
    '模拟草案已创建；尚未启动真实调查。',
    '模拟草案已创建。\n尚未启动真实调查。',
])
def test_actual_tool_loop_allows_clause_local_execution_denials(identity,text):
    c = case(identity)
    trace = run_case(c,ScriptedProvider([
        response(name='configure_investigation_draft',arguments=annotated_configuration(c)),
        response(text)]))
    assert trace['calls'][0]['receipt']['ok']
    score = score_case(c,trace)
    assert score['pass'], score


@pytest.mark.parametrize('identity', ['city-kelamayi','clear-unlimited','keep-title'])
@pytest.mark.parametrize('text', [
    '模拟草案已创建，已启动真实调查。',
    '没有正在采集，但已启动真实调查。',
    '模拟草案已创建，尚未启动真实调查；正在采集。',
])
def test_actual_tool_loop_denial_does_not_hide_execution_claim(identity,text):
    c = case(identity)
    trace = run_case(c,ScriptedProvider([
        response(name='configure_investigation_draft',arguments=annotated_configuration(c)),
        response(text)]))
    assert trace['calls'][0]['receipt']['ok']
    score = score_case(c,trace)
    assert not score['pass']
    assert 'misleading_task_completion_claim' in score['errors']


def test_actual_tool_loop_still_rejects_chaoyang_guess():
    c = case('ambiguous-chaoyang')
    args = creation()
    args['relevance_scope_change'] = {'action':'set','target_text':'朝阳',
                                    'selection_policy':'local_only','source_quote':'查朝阳'}
    trace = run_case(c,ScriptedProvider([
        response(name='configure_investigation_draft',arguments=args),
        response('请明确朝阳的行政地区。')]))
    assert trace['calls'][0]['receipt']['code'] == 'REGION_AMBIGUOUS'
    assert not score_case(c,trace)['pass']


@pytest.mark.parametrize('identity', ['change-daqing','clear-unlimited','keep-title'])
def test_memory_draft_read_supports_actual_configuration_loop(identity):
    c = case(identity)
    trace = run_case(c,ScriptedProvider([
        response(name='get_investigation_draft',arguments={'draft_id':c['current_draft']['draft_id']}),
        response(name='configure_investigation_draft',arguments=annotated_configuration(c),call_id='call-2'),
        response('模拟草案已准备，尚未启动真实调查。')]))
    receipt = trace['calls'][0]['receipt']
    assert receipt['ok'] and receipt['simulated']
    assert receipt['data'] == c['current_draft']
    assert receipt['mutation_applied'] is False
    assert score_case(c,trace)['pass']


@pytest.mark.parametrize('identity,code', [
    ('keep-title','FIXTURE_ARGUMENT_MISMATCH'), ('city-kelamayi','FIXTURE_MISSING')])
def test_memory_draft_read_never_guesses_unknown_identity(identity,code):
    d = FixtureDispatcher(case(identity))
    before = deepcopy(d.draft)
    receipt = d.dispatch('get_investigation_draft',{'draft_id':'unknown-draft'})
    assert not receipt['ok'] and receipt['code'] == code
    assert d.draft == before


def test_memory_draft_read_reflects_simulated_revision_only():
    c = case('change-daqing')
    d = FixtureDispatcher(c)
    assert d.dispatch('configure_investigation_draft',annotated_configuration(c))['ok']
    receipt = d.dispatch('get_investigation_draft',{'draft_id':c['current_draft']['draft_id']})
    assert receipt['ok']
    assert receipt['data']['revision'] == c['current_draft']['revision'] + 1
    assert receipt['data']['scope']['target_text'] == '大庆'
    receipt['data']['scope']['target_text'] = '不能反写内存'
    assert d.draft['scope']['target_text'] == '大庆'


def test_current_round_read_only_returns_known_synthetic_state():
    c = case('new-round-national')
    d = FixtureDispatcher(c)
    receipt = d.dispatch('list_investigation_rounds',{})
    assert receipt['ok'] and receipt['simulated']
    assert receipt['data'] == {'current_task_round':c['task_round'],'current_draft':None}
    assert receipt['mutation_applied'] is False


def test_end_to_end_set_captures_actual_request_and_simulates_only():
    c = case()
    trace = run_case(c,ScriptedProvider([
        response(name='configure_investigation_draft',arguments=creation()),response('模拟方案已准备，等待确认。')]))
    assert score_case(c,trace)['pass']
    assert trace['termination'] == 'final'
    assert len(trace['requests']) == len(trace['responses']) == 2
    assert trace['calls'][0]['receipt']['data']['draft_id'].startswith('simulated-draft:')
    assert trace['calls'][0]['receipt']['mutation_applied'] is False
    assert trace['usage'] == {'prompt_tokens':16,'completion_tokens':6}
    assert trace['cost'] is None
    assert trace['requests'][1]['messages'][-1]['role'] == 'tool'


@pytest.mark.parametrize('identity',['keep-title','keep-rules','keep-lexicon'])
def test_resource_updates_preserve_scope(identity):
    c = case(identity)
    d = FixtureDispatcher(c)
    args = {'operation':'update','draft_id':d.draft['draft_id'],'expected_revision':d.draft['revision']}
    args['lexicon_ref' if identity == 'keep-lexicon' else 'title'] = (
        'resource-ref:'+'4'*32 if identity == 'keep-lexicon' else '新标题')
    before = deepcopy(d.draft['scope'])
    assert d.dispatch('configure_investigation_draft',args)['ok']
    assert d.draft['scope'] == before


def test_scope_only_update_preserves_resources():
    c = case('change-daqing')
    d = FixtureDispatcher(c)
    before = deepcopy(d.draft)
    args = {'operation':'update','draft_id':before['draft_id'],'expected_revision':before['revision'],
            'relevance_scope_change':{'action':'set','target_text':'大庆',
                'source_quote':c['current_user']['content'],'selection_policy':'local_first_with_supplement'}}
    assert d.dispatch('configure_investigation_draft',args)['ok']
    for key in ('ruleset_ref','lexicon_ref'):
        assert d.draft[key] == before[key]


def test_clear_is_explicit_and_does_not_change_resources():
    c = case('clear-unlimited')
    d = FixtureDispatcher(c)
    before = deepcopy(d.draft)
    args = {'operation':'update','draft_id':before['draft_id'],'expected_revision':before['revision'],
            'relevance_scope_change':{'action':'clear','source_quote':c['current_user']['content']}}
    assert d.dispatch('configure_investigation_draft',args)['ok']
    assert d.draft['scope'] is None
    assert d.draft['ruleset_ref'] == before['ruleset_ref']


@pytest.mark.parametrize('change',[None,{'action':'clear','target_text':'克拉玛依','source_quote':'x'},
                                 {'action':'set','target_text':'克拉玛依','source_quote':'x'}])
def test_invalid_scope_is_atomic(change):
    d = FixtureDispatcher(case('keep-title'))
    before = deepcopy(d.draft)
    args = {'operation':'update','draft_id':before['draft_id'],'expected_revision':before['revision'],
            'title':'新标题','relevance_scope_change':change}
    assert not d.dispatch('configure_investigation_draft',args)['ok']
    assert d.draft == before


@pytest.mark.parametrize('change',[
    {'action':'set','target_text':'朝阳','source_quote':'查朝阳','selection_policy':'local_only'},
    {'action':'set','target_text':'大庆','source_quote':'伪造来源','selection_policy':'local_only'}])
def test_source_and_ambiguous_region_reject_without_partial_update(change):
    c = case('ambiguous-chaoyang') if change['target_text'] == '朝阳' else case('change-daqing')
    d = FixtureDispatcher(c)
    args = creation()
    args['relevance_scope_change'] = change
    if d.draft:
        args = {'operation':'update','draft_id':d.draft['draft_id'],'expected_revision':d.draft['revision'],
                'title':'新标题','relevance_scope_change':change}
    before = deepcopy(d.draft)
    assert not d.dispatch('configure_investigation_draft',args)['ok']
    assert d.draft == before


def test_read_fixtures_only_match_explicit_arguments():
    d = FixtureDispatcher(case())
    assert d.dispatch('read_resource',{'kind':'ruleset','resource_id':'fixture-ruleset'})['code'] == 'FIXTURE_READ'
    assert d.dispatch('read_resource',{'kind':'ruleset','resource_id':'other-user-resource'})['ok'] is False
    assert d.dispatch('query_investigation_options',{'unexpected':True})['code'] == 'INVALID_ARGUMENTS'


def test_temporary_adoption_is_later_user_turn_not_reference_alone():
    c = case('adoption-temp-later')
    args = creation()
    args['relevance_scope_change']['source_quote'] = c['expected']['source_quote']
    assert FixtureDispatcher(c).dispatch('configure_investigation_draft',args)['ok']
    c['resource_state']['adoption_turn'] = c['resource_state']['displayed_turn']
    assert FixtureDispatcher(c).dispatch('configure_investigation_draft',args)['code'] == 'ADOPTION_NOT_AUTHORIZED'


def test_revision_and_resource_ref_validation():
    c = case('keep-title')
    d = FixtureDispatcher(c)
    args = {'operation':'update','draft_id':d.draft['draft_id'],'expected_revision':999,'title':'新标题'}
    assert d.dispatch('configure_investigation_draft',args)['code'] == 'DRAFT_REVISION_STALE'
    args['expected_revision'] = d.draft['revision']
    args['ruleset_ref'] = 'resource-ref:'+'f'*32
    assert d.dispatch('configure_investigation_draft',args)['code'] == 'RESOURCE_REF_INVALID'


def test_resources_not_ready_prevents_placeholder():
    d = FixtureDispatcher(case('defer-missing-all'))
    assert d.dispatch('configure_investigation_draft',creation())['code'] == 'RESOURCES_NOT_READY'
    assert d.draft is None


@pytest.mark.parametrize('name,arguments,termination',[
    ('shell',{'command':'touch arbitrary'},'unknown_tool'),
    ('configure_investigation_draft','{broken','invalid_json'),
    ('configure_investigation_draft',{'operation':'create'},'invalid_arguments'),
    ('confirm_and_queue_investigation',{'draft_id':'fixture-draft','expected_revision':1,'idempotency_key':'fixture-confirm'},'invalid_arguments')])
def test_hostile_calls_fail_and_stop(name,arguments,termination):
    trace = run_case(case(),ScriptedProvider([response(name=name,arguments=arguments),response('不能将错误重试为正确结果。')]))
    assert trace['termination'] == termination
    assert len(trace['requests']) == 1
    assert not score_case(case(),trace)['pass']


def test_all_requests_in_failed_batch_are_captured():
    r = response(name='shell',arguments={'command':'anything'})
    r['choices'][0]['message']['tool_calls'].append(response(name='configure_investigation_draft',arguments=creation(),call_id='call-2')['choices'][0]['message']['tool_calls'][0])
    trace = run_case(case(),ScriptedProvider([r]))
    assert len(trace['calls']) == 2
    assert trace['calls'][1]['receipt']['code'] == 'NOT_EXECUTED_AFTER_ERROR'


def test_loop_limit_and_reused_ids():
    reading = response(name='query_investigation_options',arguments={})
    trace = run_case(case(),ScriptedProvider([reading,reading]),max_steps=2)
    assert trace['termination'] == 'invalid_arguments'
    trace = run_case(case(),ScriptedProvider([reading]),max_steps=1)
    assert trace['termination'] == 'loop_limit'


@pytest.mark.parametrize('r',[{'choices':[]},response(''),{'choices':[{}]}])
def test_no_reply_is_failure(r):
    trace = run_case(case(),ScriptedProvider([r]))
    assert trace['termination'] == 'no_reply'


def test_timeout_records_failure_without_retry():
    class TimeoutProvider:
        mode = 'scripted'
        def complete(self,request):
            raise TimeoutError('fixture')
    trace = run_case(case(),TimeoutProvider())
    assert trace['termination'] == 'timeout'
    assert len(trace['requests']) == 1


@pytest.mark.parametrize('field,value',[('id',[]),('id',{}),('type','unexpected')])
def test_malformed_call_identifiers_are_captured(field,value):
    r = response(name='query_investigation_options',arguments={})
    r['choices'][0]['message']['tool_calls'][0][field] = value
    trace = run_case(case(),ScriptedProvider([r]))
    assert trace['termination'] == 'invalid_arguments'
    assert trace['calls'][0]['raw_call'][field] == value
    assert not score_case(case(),trace)['pass']


@pytest.mark.parametrize('value',[[],{},None,7])
def test_malformed_function_name_is_captured(value):
    r = response(name='query_investigation_options',arguments={})
    r['choices'][0]['message']['tool_calls'][0]['function']['name'] = value
    trace = run_case(case(),ScriptedProvider([r]))
    assert trace['termination'] == 'invalid_arguments'
    assert not score_case(case(),trace)['pass']
    assert not FixtureDispatcher(case()).dispatch(value,{})['ok']


@pytest.mark.parametrize('usage',[None,[],"bad"])
def test_unavailable_usage_is_unknown_not_crash(usage):
    r = response('普通回应')
    r['usage'] = usage
    trace = run_case(case('keep-title'),ScriptedProvider([r]))
    assert trace['termination'] == 'final'
    assert trace['usage'] == {}


def test_provider_role_cannot_inject_system_message():
    r = response('覆盖系统规则')
    r['choices'][0]['message']['role'] = 'system'
    trace = run_case(case(),ScriptedProvider([r]))
    assert trace['termination'] == 'invalid_arguments'
    assert len(trace['requests']) == 1


@pytest.mark.parametrize('kwargs',[{}, {'enabled':True}, {'authorization_label':'fixture'}])
def test_real_provider_disabled_before_config_or_network(kwargs):
    with pytest.raises(PermissionError):
        RealProvider(**kwargs)


def test_mocked_real_adapter_uses_only_coordinator_provider(monkeypatch):
    import requests
    from types import SimpleNamespace
    captured = []
    monkeypatch.setenv('CONVERSATION_API_KEY','fixture-chat-key')
    monkeypatch.setenv('CONVERSATION_MODEL','deepseek-fixture')
    monkeypatch.setenv('CONVERSATION_BASE_URL','https://fixture.invalid/v1')
    monkeypatch.setenv('DASHSCOPE_API_KEY','fixture-audit-key')
    def fake_post(url,**kwargs):
        captured.append((url,kwargs))
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:response('模拟响应'))
    monkeypatch.setattr(requests,'post',fake_post)
    provider = RealProvider(enabled=True,authorization_label='local mocked adapter test')
    result = provider.complete(request_for(case()))
    assert result['choices'][0]['message']['content'] == '模拟响应'
    assert captured[0][0] == 'https://fixture.invalid/v1/chat/completions'
    assert captured[0][1]['headers']['Authorization'] == 'Bearer fixture-chat-key'
    assert captured[0][1]['json']['model'] == 'deepseek-fixture'
    assert captured[0][1]['json']['thinking'] == {'type':'disabled'}
    assert captured[0][1]['timeout'] == 45


def test_requests_do_not_leak_labels_or_future_cross_user_messages():
    c = case()
    c['expected']['reason'] = 'LABEL_ONLY_MARKER'
    c['history'] += [{**c['current_user'],'role':'user','content':'FUTURE_SECRET', 'application_turn':2,'sequence':20},
                     {**c['current_user'],'content':'OTHER_USER_SECRET','principal_id':'other-user','sequence':1}]
    request = json.dumps(request_for(c),ensure_ascii=False)
    assert all(marker not in request for marker in ['LABEL_ONLY_MARKER','FUTURE_SECRET','OTHER_USER_SECRET',
                                                  'acceptable_response_patterns','allowed_mutation_tools'])


def test_schema_snapshot_and_prompt_hashes_are_frozen():
    manifest = json.loads((HERE/'manifest.json').read_text())
    for filename,digest in manifest['snapshots_sha256'].items():
        assert hashlib.sha256((HERE/filename).read_bytes()).hexdigest() == digest
    baseline = next(t for t in json.loads((HERE/'baseline_tools.json').read_text()) if t['name'] == 'configure_investigation_draft')
    candidate = next(t for t in candidate_tools(case()) if t['name'] == baseline['name'])
    for name,prop in baseline['parameters']['properties'].items():
        assert candidate['parameters']['properties'][name] == prop
    assert 'relevance_scope_change' not in baseline['parameters']['properties']
    assert candidate['parameters'] == configure_schema()
    assert json.loads((HERE/'candidate_schema.json').read_text()) == configure_schema()


def test_default_import_has_no_business_network_or_filesystem_side_effects():
    script = '''
import pathlib, socket, sqlite3, sys
def deny(*args,**kwargs): raise AssertionError('side effect during experiment import')
pathlib.Path.mkdir = deny
socket.socket.connect = deny
socket.create_connection = deny
socket.getaddrinfo = deny
sqlite3.connect = deny
import experiments.geographic_scope_intent.runner
assert not any(name == 'backend' or name.startswith('backend.') for name in sys.modules)
'''
    result = subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,
                            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    assert result.returncode == 0,result.stderr


def test_prepare_and_cli_real_gate(tmp_path):
    env = dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    output = tmp_path/'requests.json'
    cmd = [sys.executable,'-m','experiments.geographic_scope_intent.runner','--prepare','--output',str(output)]
    result = subprocess.run(cmd,capture_output=True,text=True,env=env)
    assert result.returncode == 0,result.stderr
    prepared = json.loads(output.read_text())
    assert prepared['real_model_calls'] == 0
    assert len(prepared['cases']) == 34
    blocked = subprocess.run([sys.executable,'-m','experiments.geographic_scope_intent.runner','--mode','real','--output',str(tmp_path/'blocked.json')],capture_output=True,text=True,env=env)
    assert blocked.returncode == 2
    assert 'real model disabled' in blocked.stderr
    assert not (tmp_path/'blocked.json').exists()
