"""Real stores, original report tools and restartable Hermes registry; no model mocks of writes."""
import json
from copy import deepcopy
import pytest
from backend.audit_agent.config import settings
from backend.investigation_creation.principal import Principal
from backend.investigation_creation import tools, continuity
from backend.hermes_runtime.adapter import HermesRuntimeBinding
from hermes_m0.plugin import _handler
from hermes_m0.runtime import report_task_runtime_for_session
from test_investigation_creation_conversation import (
    creation_stack, _create_completed_turn, _publish_fake_run, _t1_temporary_arguments,
)
from test_resource_lifecycle import lexicon
from test_session_resource_state import snapshot

P = Principal('principal-a')

class NativeToolsAgent:
    def __init__(self, session_id, actions, answer='已按要求处理。'):
        self.session_id, self.actions, self.answer = session_id, actions, answer
        self.results = []; self.prompt = ''

    def run_conversation(self, message, *, conversation_history, task_id, system_message):
        self.prompt = system_message + getattr(self, "ephemeral_system_prompt", "")
        messages = [*deepcopy(conversation_history or []), {'role':'user','content':message}]
        for i, (name, params) in enumerate(self.actions):
            args = params(self.results) if callable(params) else params
            call = f'{task_id}:{i}'
            result = json.loads(_handler(name)(args, session_id=self.session_id, task_id=task_id, tool_call_id=call))
            self.results.append(result)
            messages.extend([
                {'role':'assistant','content':'','tool_calls':[{'id':call,'type':'function',
                  'function':{'name':name,'arguments':args}}]},
                {'role':'tool','name':name,'tool_call_id':call,'content':json.dumps(result,ensure_ascii=False)},
            ])
        messages.append({'role':'assistant','content':self.answer})
        return dict(completed=True, final_response=self.answer, messages=messages, api_calls=0)


class CanonicalProjector(__import__('backend.investigation_creation.fake_runtime', fromlist=['FakeInvestigationRunProjector']).FakeInvestigationRunProjector):
    """Publish a real canonical report from synthetic audited content; no worker."""
    comments = []

    def _ensure_report(self, run):
        from backend.audit_agent.job_store import JobStore
        from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
        from backend.audit_agent.ingestion import IngestionStore, AuditResultStore
        from backend.reporting.integration_source import CanonicalReportSource
        from backend.reporting.runtime import R31ReportRuntime
        db=self.report_store.db_path; folder=db.parent
        JobStore(db).create(job_id=self._task_id(run), platform='dy', status='completed', display_name='验收报告',
                           crawl_mode='search', max_notes=1, analyze_limit=1)
        revision=TaskAuditConfigRevisionStore(db).create(job_id=self._task_id(run),audit_config={'capabilities':['text']})
        ingestion=IngestionStore(db); results=AuditResultStore(db)
        batch=folder/'synthetic-report.json'
        batch.write_text(json.dumps({'task_id':self._task_id(run),'platform':'dy','items':[{'aweme_id':'10001','title':'公园散步','desc':'假期去公园散步'}]}))
        refs=ingestion.ingest_batch(batch,folder/'raw')
        result=results.upsert_result(job_id=self._task_id(run),platform='dy',content_key='10001',content_id=refs[0]['content_id'],
            audit_config_revision_id=revision['id'],result={'title':'公园散步','desc':'假期去公园散步',
            'author':{'nickname':'生活记录者','sec_uid':'stable-author'},'decision':'pass','risk_level':'none',
            'summary':'未发现本次规则覆盖的风险。','comments':self.comments,'evidence_items':[],'video_results':[]})
        ingestion.mark_content_status('dy','10001','completed',task_id=self._task_id(run),audit_result_id=result['id'])
        output=R31ReportRuntime(self.report_store).generate(self._task_id(run),source=CanonicalReportSource(db,folder/'outputs'),checkpoint_path=folder/'report-checkpoints.sqlite3')
        self.actual_version=output.report_version_id
        return self.actual_version

    def _report_version_id(self, run):
        return self.actual_version

def setup(stack, monkeypatch):
    monkeypatch.setattr("test_investigation_creation_conversation.FakeInvestigationRunProjector", CanonicalProjector)
    created = _create_completed_turn(stack)
    run = _publish_fake_run(stack, created, idempotency_key='continuity-confirm')
    conv=stack['conversation']; conv.report_service=stack['report_service']; conv.fake_runtime=False
    monkeypatch.setattr(settings, 'continuous_resource_session_enabled', True)
    monkeypatch.setattr(tools, '_tool_service', stack['tool_service'])
    monkeypatch.setattr(tools, '_principal_provider', conv.principal_for_session)
    return created['workspace_id'], run


def turn(stack, session_id, tag, actions, text='继续处理'):
    conv=stack['conversation']; agent=NativeToolsAgent(session_id,actions)
    conv._agents[session_id]=agent
    item,_=conv.accept_message(session_id,client_message_id=tag,content=text,principal=P)
    result=conv.execute_turn(item.id)
    stored=conv.store.get_turn(item.id)
    assert stored.status=='completed', (stored,agent.results)
    return agent,result


def test_report_resource_report_switch_and_restart_preserve_frozen_run(creation_stack, monkeypatch):
    s=creation_stack; session, run=setup(s,monkeypatch)
    app=s['app_service']; manager=app.resource_management
    a=manager.create_lexicon(lexicon(),session_id=session,principal=P)
    b=manager.create_lexicon(lexicon(),session_id=session,principal=P)
    frozen=deepcopy(app.store.get_run(run['run_id'],principal=P.id).confirmed_configuration)
    before=snapshot(s)
    first,_=turn(s,session,'read',[('list_session_resources',{}),('read_report',{})])
    assert first.results[0]['status']=='ok' and first.results[1]['ok'], first.results
    assert any(i['id']==b['edit_id'] for i in first.results[0]['data']['items'])
    # Reads may create report navigation ledger, never modify business databases.
    assert snapshot(s)[:2]==before[:2]
    edit,_=turn(s,session,'edit',[
        ('get_resource_edit',{'edit_id':b['edit_id']}),
        ('update_resource_edit',{'edit_id':b['edit_id'],'expected_version':1,
            'changes':[{'operation':'set_metadata','values':{'title':'只修改 B'}}]}),
        ('save_resource',{'edit_id':b['edit_id'],'expected_version':2,'mode':'new','operation_id':'save-b'}),
    ])
    assert all(r['status']=='ok' for r in edit.results), edit.results
    assert manager.get_edit(a['edit_id'],session_id=session,principal=P)['version']==1
    assert manager.get_edit(b['edit_id'],session_id=session,principal=P)['version']==2
    assert app.store.get_run(run['run_id'],principal=P.id).confirmed_configuration==frozen
    assert '这个会话只保留该报告的后续问答' not in edit.prompt
    assert '连续会话能力' in edit.prompt
    # Drop runtime/agent caches; read the same frozen report through a fresh binding.
    s['conversation'].runtime_binding.release_published_report_session(session)
    s['conversation']._agents.clear()
    last,_=turn(s,session,'report-again',[('read_posts',{'post_refs':[first.results[1]['data']['post_previews'][0]['ref']]}),('read_report',{}),('list_session_resources',{})])
    assert last.results[0]['ok'],last.results
    assert last.results[1]['data']['statistics']==first.results[1]['data']['statistics']
    assert last.results[1]['data']['title']==first.results[1]['data']['title']
    assert app.store.get_run(run['run_id'],principal=P.id).confirmed_configuration==frozen
    s['conversation'].runtime_binding.release_published_report_session(session)


def test_new_draft_is_blocked_after_run_but_resources_are_available(creation_stack,monkeypatch):
    s=creation_stack; session,run=setup(s,monkeypatch)
    before=snapshot(s)
    agent,_=turn(s,session,'second',[
        ('create_investigation_draft',_t1_temporary_arguments(s)), ('list_session_resources',{})])
    assert agent.results[0]['status']=='error'
    assert agent.results[0]['error']['code']=='SESSION_INVESTIGATION_FROZEN'
    assert agent.results[1]['status']=='ok'
    # Only operation/attempt receipts can change; the count of drafts/runs is fixed.
    import sqlite3
    with sqlite3.connect(s['creation_store'].db_path) as db:
        assert db.execute('select count(*) from investigation_drafts').fetchone()[0]==1
        assert db.execute('select count(*) from investigation_runs').fetchone()[0]==1
    s['conversation'].runtime_binding.release_published_report_session(session)


def test_unbound_and_cross_user_reads_fail_closed(creation_stack,monkeypatch):
    s=creation_stack; session,run=setup(s,monkeypatch)
    other=s['conversation'].create_session(principal=Principal('principal-b'))
    manager=s['app_service'].resource_management
    resource=manager.create_lexicon(lexicon(),session_id=session,principal=P)
    with pytest.raises(Exception):
        continuity.read(s['app_service'],'read_session_resource',{'key':f"edit/{resource['edit_id']}/1"},
                        session_id=other.id,principal=Principal('principal-b'))
    with pytest.raises(Exception):
        continuity.read(s['app_service'],'list_session_resources',{},session_id=session,principal=Principal('principal-b'))
    with HermesRuntimeBinding().product_mode_execution(s['creation_store'].db_path.parent/'native',product_mode='creation'):
        denied=json.loads(_handler('read_report')({},session_id=other.id))
    assert denied['ok'] is False and denied['error']['code']=='product_session_unbound'


def test_opt_in_catalog_uses_original_report_tools_no_shell(creation_stack,monkeypatch,tmp_path):
    binding=HermesRuntimeBinding()
    for enabled in (False,True):
        monkeypatch.setattr(settings,'continuous_resource_session_enabled',enabled)
        with binding.product_mode_execution(tmp_path/'catalog',product_mode='creation'):
            definitions=binding.tool_definitions(enabled_toolsets=['investigation'],product_mode='creation')
            names={d['function']['name'] for d in definitions}
            assert ('read_report' in names)==enabled
            assert ('list_session_resources' in names)==enabled
            assert 'shell' not in names


def test_report_preflight_failure_is_known_no_write_failure(creation_stack, monkeypatch):
    from backend.hermes_runtime.adapter import HermesReportToolsUnavailable
    s = creation_stack
    session, _ = setup(s, monkeypatch)
    before = snapshot(s)[:2]
    def unavailable(*args, **kwargs):
        raise HermesReportToolsUnavailable('report tool catalog incomplete')
    monkeypatch.setattr(s['conversation'], '_agent', unavailable)
    item, _ = s['conversation'].accept_message(
        session, client_message_id='missing-report-tools', content='查看报告', principal=P)
    s['conversation'].execute_turn(item.id)
    stored = s['conversation'].store.get_turn(item.id)
    assert stored.status == 'error'
    assert stored.stop_reason == 'environment_preflight_failed'
    assert snapshot(s)[:2] == before


def test_report_binding_failure_does_not_block_resource_editing(creation_stack, monkeypatch):
    s=creation_stack; session,run=setup(s,monkeypatch)
    manager=s['app_service'].resource_management
    b=manager.create_lexicon(lexicon(),session_id=session,principal=P)
    def unavailable(*args, **kwargs):
        raise RuntimeError('isolated report read failure')
    monkeypatch.setattr(s['report_service'], 'bind_workspace_report', unavailable)
    agent,_=turn(s,session,'unavailable',[
        ('read_report',{}),
        ('update_resource_edit',{'edit_id':b['edit_id'],'expected_version':1,
          'changes':[{'operation':'set_metadata','values':{'title':'报告不可用时仍能修改'}}]})])
    assert agent.results[0]['ok'] is False
    assert agent.results[1]['status']=='ok'
    assert '本轮报告读取服务不可用' in agent.prompt
    assert manager.get_edit(b['edit_id'],session_id=session,principal=P)['version']==2


def test_continuous_report_keeps_original_comment_delivery_and_redaction(creation_stack,monkeypatch):
    monkeypatch.setattr(CanonicalProjector,'comments',[
        {'comment_id':f'c{i}','content':f'验收评论{i}','nickname':f'作者{i}',
         'sec_uid':f'author{i}','audit_status':'completed','risk_level':'none'} for i in range(2)])
    s=creation_stack; session,run=setup(s,monkeypatch)
    agent,result=turn(s,session,'comments',[
        ('read_report',{}),('list_post_comments',lambda results:{
            'post_ref':results[0]['data']['post_previews'][0]['ref'],'batch_action':'start'})])
    assert agent.results[1]['ok'],agent.results
    assert '验收评论0' in result.answer and '验收评论1' in result.answer
    s['conversation'].runtime_binding.release_published_report_session(session)
    last,result=turn(s,session,'continue-comments',[
        ('list_post_comments',{'post_ref':agent.results[0]['data']['post_previews'][0]['ref'],'batch_action':'continue'})])
    assert last.results[0]['ok'],last.results
    assert last.results[0]['data']['returned_count']==0
    value,changed=s['conversation']._redact_answer('可读回答 report1_abcd')
    assert changed and 'report1_abcd' not in value and '可读回答' in value
    s['conversation'].runtime_binding.release_published_report_session(session)


def test_same_agent_receives_current_batch_progress_without_restart(creation_stack,monkeypatch):
    monkeypatch.setattr(CanonicalProjector,'comments',[
        {'comment_id':f'c{i}','content':f'验收原文{i}','nickname':f'作者{i}',
         'sec_uid':f'a{i}','audit_status':'completed','risk_level':'none'} for i in range(205)])
    s=creation_stack;session,_=setup(s,monkeypatch);conv=s['conversation']
    agent=NativeToolsAgent(session,[('read_report',{}),('list_post_comments',lambda r:{
        'post_ref':r[0]['data']['post_previews'][0]['ref'],'batch_action':'start'})])
    conv._agents[session]=agent
    for i,end in enumerate((0,100,200)):
        item,_=conv.accept_message(session,client_message_id=f'progress-{i}',content='列评论' if not i else '继续',principal=P)
        outcome=conv.execute_turn(item.id)
        assert conv.store.get_turn(item.id).status=='completed'
        marker='服务端评论交付进度：'
        progress=json.loads(agent.prompt.split(marker)[1].split('\n')[0])
        if end:
            assert progress['confirmed_deliveries'][0]['end']==end
        else:
            assert progress['confirmed_deliveries']==[]
        assert agent.ephemeral_system_prompt==''
        assert '服务端评论交付进度' not in conv._system_message(None)
        post=agent.results[0]['data']['post_previews'][0]['ref']
        agent.actions=[('list_post_comments',{'post_ref':post,'batch_action':'continue'})]
    conv.runtime_binding.release_published_report_session(session)


def test_mixed_save_and_bad_comment_list_keeps_verified_save_and_frozen_task(creation_stack,monkeypatch):
    monkeypatch.setattr(CanonicalProjector,'comments',[
        {'comment_id':'c','content':'真实评论内容','nickname':'甲','sec_uid':'a','audit_status':'completed','risk_level':'none'}])
    s=creation_stack;session,run=setup(s,monkeypatch);conv=s['conversation'];manager=s['app_service'].resource_management
    resource=manager.create_lexicon(lexicon(),session_id=session,principal=P)
    frozen=deepcopy(s['app_service'].store.get_run(run['run_id'],principal=P.id).confirmed_configuration)
    agent=NativeToolsAgent(session,[
        ('save_resource',{'edit_id':resource['edit_id'],'expected_version':1,'mode':'new','operation_id':'mixed-save'}),
        ('read_report',{}),
        ('list_post_comments',lambda r:{'post_ref':r[1]['data']['post_previews'][0]['ref'],'batch_action':'start'}),
    ],answer='已保存。\n1. 虚构作者：伪造的原文。')
    conv._agents[session]=agent
    item,_=conv.accept_message(session,client_message_id='mixed',content='保存词库并列出评论',principal=P)
    result=conv.execute_turn(item.id)
    assert conv.store.get_turn(item.id).status=='completed'
    assert '已保存' in result.answer and '真实评论内容' in result.answer
    assert '伪造的原文' not in result.answer
    history=conv.store.latest_completed_hermes_transcript(session)
    assert '伪造的原文' not in str(history)
    assert '已保存' in history[-1]['content']
    assert s['app_service'].store.get_run(run['run_id'],principal=P.id).confirmed_configuration==frozen
    assert manager.get_edit(resource['edit_id'],session_id=session,principal=P)['version']==1
    conv.runtime_binding.release_published_report_session(session)
