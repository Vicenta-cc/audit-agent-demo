import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from backend.audit_agent import pipeline as module
from backend.audit_agent.pipeline import AuditPipeline, AuditProviderCallError, FusionAuditContractError
from backend.audit_agent.qwen_client import QwenTimeoutError, QwenProviderError
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.crawler_adapter import CrawlOutput
from backend.rulesets.compiler import compile_ruleset_revision, content_hash
from backend.rulesets.trial_profiles import K2_RULESET_ID, TRIAL_BUNDLES
from test_authoritative_m3_provider_closure import _configuration


def compiled_k2():
    content = json.loads((TRIAL_BUNDLES[K2_RULESET_ID] / 'ruleset.json').read_text())
    return compile_ruleset_revision({'id': 'k2-test', 'ruleset_id': K2_RULESET_ID,
        'version': 1, 'status': 'published', 'snapshot': content, 'content_hash': content_hash(content)})


def subject(note='n'):
    return AuditSubject(platform='xhs', note_id=note, title='', desc='', url='', author={},
                        image_urls=[], video_urls=[], comments=[])


def test_alias_mapping_reorders_but_does_not_guess_or_accept_duplicate():
    comments = [{'comment_id': '7683403259888829241'}, {'comment_id': '7683403259888829242'}]
    decode = AuditPipeline._decode_comment_ids
    assert decode({'comments': [{'id': 'C02', 's': 60}, {'id': 'C01', 's': 0}]}, comments) == {
        'comments': [{'id': comments[1]['comment_id'], 's': 60}, {'id': comments[0]['comment_id'], 's': 0}]}
    for rows in [[{'id': 'C01'}, {'id': 'C01'}], [{'id': 'c01'}], [{'id': 'C03'}],
                 [{'id': comments[0]['comment_id']}], [{'id':'C01', 'comment_id':'C02'}]]:
        assert decode({'comments': rows}, comments) == {'comments': []}


def test_compensation_remaps_only_missing_comment_and_preserves_judgement(tmp_path, monkeypatch):
    monkeypatch.setattr(module.settings, 'outputs_dir', tmp_path)
    monkeypatch.setattr(module.job_store, 'log', Mock())
    p = AuditPipeline.__new__(AuditPipeline); p.job_id = 'alias-test'
    compiled = compiled_k2(); p.rule_snapshot = compiled['rule_snapshot']
    p._set_prompt_context('ethnic', compiled['prompt_profile_snapshot'])
    comment_rule_code = next(
        code for code, rule_id in p._comment_rule_code_mapping().items()
        if rule_id == 'ethnic.group_stereotype_and_derogation'
    )
    p.qwen = SimpleNamespace(audit_text=Mock(side_effect=[
        {'comments': [{'id':'C02','s':60,'risk_level':'medium','lib':'ethnic',
          't':'ethnic.content_attack','rule_id':comment_rule_code,
          'rb':'民族侮辱','q':'汉族都是垃圾'}, {'id':'bad','s':0,'risk_level':'none'}]},
        {'comments':[{'id':'C01','s':0,'risk_level':'none'}]}]))
    comments = [{'comment_id':'7683403259888829241','source_text':'正常内容'},
                {'comment_id':'7683403259888829242','source_text':'汉族都是垃圾'}]
    result = p._audit_comment_batch_with_fallback(subject(), '', comments)
    assert set(result) == {c['comment_id'] for c in comments}
    assert result[comments[1]['comment_id']]['risk_level'] == 'medium'
    assert result[comments[1]['comment_id']]['risk_basis'] == '民族侮辱'
    calls = p.qwen.audit_text.call_args_list
    assert len(calls) == 2
    for call in calls:
        assert all(c['comment_id'] not in call.args[0] for c in comments)
    payload = json.loads(calls[1].args[0].split('输入 JSON：\n')[1])
    assert payload['comments'] == [{'comment_id':'C01','source_text':'正常内容','translation_required':False}]
    assert list((tmp_path/'alias-test/comment_audit_failures').glob('*.json'))


@pytest.mark.parametrize('mode', ['one_bad_post', 'provider_down', 'authentication', 'last_bad_post', 'media_authentication', 'two_success_three_failures', 'three_failures_then_success'])
def test_collection_and_analysis_drain_despite_post_failures(tmp_path, monkeypatch, mode):
    jobs = JobStore(tmp_path/'audit.sqlite3'); ingestion = IngestionStore(tmp_path/'audit.sqlite3')
    monkeypatch.setattr(module, 'job_store', jobs)
    monkeypatch.setattr(module.settings, 'outputs_dir', tmp_path/'outputs')
    monkeypatch.setattr(module.settings, 'auto_analyze_crawled_content', True)
    monkeypatch.setattr(module.settings, 'stream_crawl_analysis', True)
    p = AuditPipeline.__new__(AuditPipeline); p.job_id='isolation'; p.qwen=SimpleNamespace(provider_failure='')
    p.ingestion=ingestion; p.rule_snapshot={};p.prompt_profile_snapshot={};p.audit_config_revision_id=''
    p._set_prompt_context = Mock();p._write_result_json=Mock(return_value=tmp_path/'result.json')
    p._persist_audit_result=lambda **kwargs: kwargs['result']
    media_flags=[]
    def build_subjects(platform, items, *args, **kwargs):
        media_flags.append(kwargs.get('include_media'))
        return [subject(i['note_id']) for i in items]
    p._build_subjects=build_subjects
    seen=[]; collected=[]
    failure_recorded = threading.Event()
    original_record = p._record_subject_failure
    def record(*args):
        original_record(*args)
        if mode != 'provider_down' or len(seen) == 3:
            failure_recorded.set()
    p._record_subject_failure = record
    def audit(s):
        seen.append(s.note_id)
        if mode in {'two_success_three_failures', 'three_failures_then_success'}:
            failed_ids = ['3', '4', '5'] if mode == 'two_success_three_failures' else ['1', '2', '3']
            if s.note_id in failed_ids:
                p.qwen.provider_failure = 'provider failed'
                if s.note_id != failed_ids[-1]:
                    raise AuditProviderCallError('ASR failed') from RuntimeError('CUDA out of memory')
                response = requests.Response(); response.status_code = 400
                response._content = b'{"code":"data_inspection_failed"}'
                raise AuditProviderCallError('fusion failed') from requests.HTTPError(response=response)
        if mode=='provider_down':
            p.qwen.provider_failure='timeout'
            raise QwenTimeoutError('timeout after one retry')
        if mode=='authentication' or (mode=='media_authentication' and s.note_id=='2'):
            response=requests.Response();response.status_code=401
            if mode=='authentication':
                raise QwenProviderError('provider authentication failed') from requests.HTTPError(response=response)
            raise requests.HTTPError(response=response)
        if (mode=='one_bad_post' and s.note_id=='2') or (mode=='last_bad_post' and s.note_id=='5'):
            p.qwen.provider_failure='invalid response'
            raise FusionAuditContractError('unknown evidence id')
        return {'note_id':s.note_id,'decision':'pass','risk_level':'none'}
    p._analyze_subject=audit
    def crawl(**kwargs):
        items=[]
        for i in range(1,6):
            assert not kwargs['stop_checker']()
            item={'note_id':str(i),'title':'post'};items.append(item);collected.append(str(i))
            kwargs['content_callback']([item], [])
            boundary = {'authentication': 1, 'provider_down': 3, 'last_bad_post': 5, 'two_success_three_failures': 5, 'three_failures_then_success': 3}.get(mode, 2)
            if i == boundary:
                assert failure_recorded.wait(5), 'Analysis must fail before the crawler continues'
                if mode in {'authentication', 'provider_down'}:
                    assert not jobs.control(p.job_id)['analysis_stop_requested']
                    assert not kwargs['stop_checker']()
        return CrawlOutput(platform='xhs',contents=items,comments=[],output_dir=tmp_path/'crawler',command=[])
    p.crawler=SimpleNamespace(run_search=crawl)
    config=_configuration();config.update(
        analyze_limit=5,
        max_notes=5,
        collect_media=False,
        _confirmed_analyze_limit=5,
    )
    jobs.create(job_id=p.job_id,**{k:v for k,v in config.items() if not k.startswith('_')})
    p.run(SimpleNamespace(**config))
    job=jobs.get(p.job_id);stats=ingestion.stats_for_task(p.job_id)
    assert collected == ['1','2','3','4','5']
    assert media_flags and all(flag is False for flag in media_flags)
    assert not jobs.control(p.job_id)['stop_all_requested']
    assert not jobs.control(p.job_id)['crawl_stop_requested']
    assert stats['ingested_count']==5
    assert seen == collected
    assert job['status'] == 'completed'
    assert not jobs.control(p.job_id)['analysis_stop_requested']
    assert stats['queued_analysis_count'] == 0
    if mode in {'one_bad_post','last_bad_post','media_authentication'}:
        assert seen==collected
        assert job['status']=='completed'
        assert stats['completed_analysis_count']==4 and stats['failed_analysis_count']==1
    elif mode in {'two_success_three_failures', 'three_failures_then_success'}:
        assert stats['completed_analysis_count'] == 2
        assert stats['failed_analysis_count'] == 3
        records = [json.loads(path.read_text()) for path in (tmp_path/'outputs/isolation/post_failures').glob('*.json')]
        assert sorted(record['error_code'] for record in records) == ['asr_gpu_out_of_memory', 'asr_gpu_out_of_memory', 'audit_content_blocked']
    else:
        assert stats['failed_analysis_count'] == 5
        assert stats['completed_analysis_count'] == 0
    assert list((tmp_path/'outputs/isolation/post_failures').glob('*.json'))


def test_partial_result_projection_does_not_promise_pending_report():
    from backend.investigation_creation.adapters import InvestigationRunProjector
    from backend.investigation_creation.contracts import RunStatus
    p = InvestigationRunProjector.__new__(InvestigationRunProjector)
    p.job_store = SimpleNamespace(get=lambda _: {'status': 'completed', 'run_crawler': True})
    p.ingestion_store = SimpleNamespace(stats_for_task=lambda _: {'failed_analysis_count': 1})
    p.audit_result_store = SimpleNamespace(list_results=lambda **_: {'items': []})
    result = p.project(SimpleNamespace(job_id='job', status=RunStatus.AUDIT_COMPLETED, report_version_id=''))
    assert result['crawl_status'] == 'completed'
    assert result['analysis_status'] == 'partial'
    assert result['report_status'] == 'blocked_by_failed_posts'


def test_resumed_authoritative_audit_cannot_persist_provider_fallback(tmp_path, monkeypatch):
    jobs = JobStore(tmp_path / 'audit.sqlite3')
    ingestion = IngestionStore(tmp_path / 'audit.sqlite3')
    monkeypatch.setattr(module, 'job_store', jobs)
    monkeypatch.setattr(module.settings, 'outputs_dir', tmp_path / 'outputs')
    jobs.create(
        job_id='resume-health',
        platform='xhs',
        effective_config={'collect_media': False},
    )
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = 'resume-health'
    p.authoritative_m3 = True
    p.qwen = SimpleNamespace(provider_failure='')
    p.ingestion = ingestion
    p._verified_m3_snapshot = Mock(return_value=None)
    p._resume_source_root = Mock(return_value=tmp_path)
    p._rule_snapshot_from_source = Mock(return_value={})
    p._set_prompt_context = Mock()
    p._analysis_media_scope = Mock(return_value='all')
    p._build_subjects = Mock(return_value=[subject()])
    p._should_analyze_subject = Mock(return_value=True)
    p._write_result_json = Mock(return_value=tmp_path / 'result.json')
    p._persist_audit_result = Mock()
    monkeypatch.setattr(ingestion, 'pending_for_task', lambda *a, **k: [
        {'content_key': 'n', 'item': {'note_id': 'n'}, 'comments': []}])
    monkeypatch.setattr(ingestion, 'mark_content_status', Mock())
    def fallback(_):
        p.qwen.provider_failure = 'provider failed'
        return {'note_id': 'n', 'decision': 'pass', 'risk_level': 'none'}
    p._analyze_subject = fallback
    p.resume_pending_analysis()
    assert p._build_subjects.call_args.kwargs['include_media'] is False
    p._write_result_json.assert_not_called()
    p._persist_audit_result.assert_not_called()
    assert any(c.args[2] == 'failed' for c in ingestion.mark_content_status.call_args_list)
