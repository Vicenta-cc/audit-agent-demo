import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from backend.audit_agent import pipeline as module
from backend.audit_agent.pipeline import AuditPipeline, AuditProviderCallError
from backend.audit_agent.qwen_client import QwenProviderError, QwenTimeoutError
from backend.reporting.comment_statistics import snapshot_comment_coverage
from test_comment_alias_and_failure_isolation import compiled_k2, subject


def provider_error(status=400, code='data_inspection_failed'):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({'code': code}).encode()
    error = QwenProviderError('provider request failed')
    error.__cause__ = requests.HTTPError(response=response)
    return error


def make_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(module.settings, 'outputs_dir', tmp_path)
    monkeypatch.setattr(module.job_store, 'log', Mock())
    monkeypatch.setattr(module.settings, 'comment_audit_batch_size', 1)
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = 'partial'
    p.authoritative_m3 = True
    compiled = compiled_k2()
    p.rule_snapshot = compiled['rule_snapshot']
    p._set_prompt_context('ethnic', compiled['prompt_profile_snapshot'])
    p.translator = SimpleNamespace(should_translate=lambda *a: False)
    p.qwen = SimpleNamespace(provider_failure='')
    return p


@pytest.mark.parametrize('workers', [1, 3])
@pytest.mark.parametrize('failure', ['blocked', 'timeout', 'transient'])
def test_failed_batch_preserves_siblings_and_report_coverage(monkeypatch, tmp_path, workers, failure):
    p = make_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(module.settings, 'comment_audit_concurrency', workers)
    def call(prompt, **kwargs):
        payload = json.loads(prompt.split('输入 JSON：\n')[1])
        if payload['comments'][0]['source_text'] == '受阻评论':
            p.qwen.provider_failure = 'failed'
            raise {'blocked': provider_error(), 'timeout': QwenTimeoutError('exhausted'),
                   'transient': provider_error(503, 'unavailable')}[failure]
        return {'comments': [{'id': 'C01', 's': 0, 'risk_level': 'none'}]}
    p.qwen.audit_text = Mock(side_effect=call)
    item = subject()
    item.comments = [{'comment_id': str(i), 'content': text} for i, text in
                     enumerate(['正常讨论', '受阻评论', '谢谢分享'])]
    results = p._audit_comments(item, '')
    assert p.qwen.audit_text.call_count == 3  # no re-request or splitting safety rejection
    by_id = {c['comment_id']: c for c in results}
    assert by_id['0']['audit_status'] == by_id['2']['audit_status'] == 'completed'
    assert by_id['1']['audit_status'] == 'failed'
    assert 'risk_score' not in by_id['1'] and 'risk_level' not in by_id['1']
    p._assert_authoritative_provider_healthy()  # isolated failure cannot poison fusion
    receipts = list((tmp_path/'partial/comment_audit_batches').glob('*.json'))
    assert len(receipts) == 3
    assert sum(len(json.loads(f.read_text())['results']) for f in receipts) == 3
    coverage = snapshot_comment_coverage([{'comments': results}])
    assert (coverage['total'], coverage['completed'], coverage['failed'], coverage['no_risk']) == (3, 2, 1, 2)
    prompt = p._render_compact_fusion_prompt(item, {'catalog': [], 'comment_units': []}, results)
    assert '受阻评论' not in prompt


def test_explicit_abstention_is_not_safe_or_retried(monkeypatch, tmp_path):
    p = make_pipeline(monkeypatch, tmp_path)
    p.qwen.audit_text = Mock(return_value={'comments': [
        {'id': 'C01', 'audit_status': 'unreviewed', 's': 0, 'risk_level': 'none'},
        {'id': 'C02', 's': 0, 'risk_level': 'none'}]})
    result = p._audit_comment_batch_with_fallback(subject(), '', [
        {'comment_id': 'one', 'source_text': '内容', 'translation_required': True},
        {'comment_id': 'two', 'source_text': '谢谢'}])
    assert p.qwen.audit_text.call_count == 1
    assert result['one']['audit_status'] == 'failed' and 'risk_score' not in result['one']
    assert result['one']['translation_status'] == 'failed'
    assert result['two']['audit_status'] == 'completed'
    assert 'audit_status=unreviewed' in p.qwen.audit_text.call_args.args[0]


def test_authentication_still_fails_and_prior_batch_is_saved(monkeypatch, tmp_path):
    p = make_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(module.settings, 'comment_audit_concurrency', 1)
    p.qwen.audit_text = Mock(side_effect=[{'comments': [{'id': 'C01', 's': 0, 'risk_level': 'none'}]},
                                         provider_error(401, 'invalid_api_key')])
    item = subject(); item.comments = [{'comment_id': str(i), 'content': '内容'} for i in range(2)]
    with pytest.raises(AuditProviderCallError):
        p._audit_comments(item, '')
    assert len(list((tmp_path/'partial/comment_audit_batches').glob('*.json'))) == 1


def test_prior_media_failure_is_not_cleared(monkeypatch, tmp_path):
    p = make_pipeline(monkeypatch, tmp_path)
    p.qwen.provider_failure = 'media failed'
    item = subject(); item.comments = [{'comment_id': 'one', 'content': '内容'}]
    with pytest.raises(AuditProviderCallError, match='media failed'):
        p._audit_comments(item, '')


def test_compensation_failure_retains_first_round_success(monkeypatch, tmp_path):
    p = make_pipeline(monkeypatch, tmp_path)
    p.qwen.audit_text = Mock(side_effect=[{'comments': [{'id': 'C01', 's': 0, 'risk_level': 'none'}]}, provider_error()])
    result = p._audit_comment_batch_with_fallback(subject(), '', [
        {'comment_id': 'one', 'source_text': '谢谢'}, {'comment_id': 'two', 'source_text': '内容'}])
    assert p.qwen.audit_text.call_count == 2
    assert result['one']['audit_status'] == 'completed'
    assert result['two']['audit_status'] == 'failed'


def test_new_report_keeps_partial_scope_in_overview_and_conclusion(tmp_path):
    from test_pass_report import seed_audit
    from backend.reporting.runtime import R31ReportRuntime
    source, store = seed_audit(tmp_path, comments=[
        {'comment_id': 'ok', 'audit_status': 'completed', 'risk_level': 'none'},
        {'comment_id': 'blocked', 'audit_status': 'failed', 'audit_error': 'audit_content_blocked'}])
    result = R31ReportRuntime(store).generate('new-search-task', source=source,
                                            checkpoint_path=tmp_path/'checkpoints.sqlite3')
    assert store.get_version(result.report_version_id)['status'] == 'published'
    view = store.get_presentation_projection(result.report_version_id)
    assert '部分完成：1 条评论' in json.dumps(view['investigation_summary'], ensure_ascii=False)
    document = store.get_frontend_report(result.report_version_id)
    assert '不能据此认定整帖均无风险' in json.dumps(document, ensure_ascii=False)
