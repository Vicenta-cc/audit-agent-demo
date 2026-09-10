import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.audit_agent.config import settings
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.pipeline import AuditPipeline, FusionAuditContractError


@pytest.fixture
def pipeline():
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = 'fusion-boundary-test'
    p.rule_snapshot = {'schema_version': 2, 'stage_routes': {'fusion_audit': ['rule.a', 'rule.b', 'rule.c']}}
    p.prompt_profile_snapshot = {'fusion_prompt_template': '只输出融合 JSON。'}
    p.prompt_set = SimpleNamespace(category='test', prompt_version='test')
    return p


def comment(cid, level='none', rule='rule.a', status='completed'):
    return {'comment_id': str(cid), 'source_text': '原文-' + str(cid), 'audit_status': status,
            'risk_score': {'none': 0, 'low': 40, 'medium': 60, 'high': 80}[level],
            'risk_level': level, 'rule_id': rule if level != 'none' else '', 'risk_basis': '已保存依据'}


def index(comments):
    return {'evidence_catalog': [
        {'evidence_id': 'comment:' + c['comment_id'], 'primary_modality': 'comment',
         'rule_id': c['rule_id'], 'evidence_risk_level': c['risk_level'], 'risk_score': c['risk_score'],
         'reason': c['risk_basis'], 'text': c['source_text']}
        for c in comments if c['audit_status'] == 'completed' and c['risk_score'] > 0],
        'comment_units': copy.deepcopy(comments)}


def render(p, comments, evidence=None):
    subject = AuditSubject('dy', 'post', '', '标题', '正文', {}, [], [], [])
    with patch.object(settings, 'comment_fusion_top_k', 20):
        prompt = p._render_compact_fusion_prompt(subject, evidence or index(comments), comments)
    return prompt, json.loads(prompt.split('\n输入 JSON：\n', 1)[1])


def response(ids=(), level='medium'):
    return {'schema_version': 'audit_fusion_v4', 'content_title': '测试帖子的风险摘要',
            'summary': '评论区存在已审核风险', 'decision_suggestion': 'pass' if level == 'none' else 'review',
            'risk_level_suggestion': level, 'primary_risk': '', 'categories': [],
            'evidence_items': [{'evidence_id': x} for x in ids], 'rule_matches': []}


def test_all_pass_and_failed_are_not_candidates(pipeline):
    comments = [comment(i) for i in range(57)] + [comment('failed', status='failed')]
    prompt, data = render(pipeline, comments)
    assert data['top_comments'] == data['evidence_catalog'] == []
    assert data['comment_stats']['total'] == 58
    assert data['comment_stats']['completed'] == 57
    assert data['comment_stats']['failed'] == 1
    assert data['comment_stats']['level_counts']['none'] == 57
    assert all(c['source_text'] not in prompt for c in comments)
    with pytest.raises(FusionAuditContractError) as error:
        pipeline._validate_v2_fusion_contract(response(['comment:1']), index(comments), visible_evidence_ids=set())
    assert error.value.invalid_evidence_ids == ['comment:1']


def test_up_to_twenty_complete_risk_candidates_and_saved_basis(pipeline):
    comments = [comment('low', 'low'), comment('medium', 'medium', 'rule.b'), comment('pass')]
    evidence = index(comments)
    before = copy.deepcopy((comments, evidence))
    prompt, data = render(pipeline, comments, evidence)
    assert {c['comment_id'] for c in data['top_comments']} == {'low', 'medium'}
    validated = pipeline._validate_v2_fusion_contract(response(['comment:medium']), evidence,
                                                      visible_evidence_ids=pipeline._fusion_visible_ids(prompt))
    assert {c['evidence_id'] for c in validated['evidence_items']} == {'comment:low', 'comment:medium'}
    assert all(c['reason'] == '已保存依据' for c in validated['evidence_items'])
    assert (comments, evidence) == before


def test_over_twenty_diversity_counts_and_unselected_results(pipeline):
    comments = [comment(i, 'high') for i in range(30)] + [comment('medium', 'medium', 'rule.b'), comment('low', 'low', 'rule.c')]
    evidence = index(comments)
    before = copy.deepcopy(evidence)
    prompt, data = render(pipeline, comments, evidence)
    assert len(data['top_comments']) == 20
    assert {c['rule_id'] for c in data['top_comments']} == {'rule.a', 'rule.b', 'rule.c'}
    assert data['top_comments'][0]['score'] == 80
    assert data['comment_stats']['level_counts'] == {'none': 0, 'low': 1, 'medium': 1, 'high': 30}
    assert data['comment_stats']['rule_counts'] == {'rule.a': 30, 'rule.b': 1, 'rule.c': 1}
    visible = pipeline._fusion_visible_ids(prompt)
    assert len(visible) == 20
    validated = pipeline._validate_v2_fusion_contract(response(['comment:0'], 'high'), evidence, visible_evidence_ids=visible)
    assert len(validated['evidence_items']) == 32
    assert evidence == before
    # Stored-but-unseen IDs are not legal MODEL references; native carry-forward
    # above remains legitimate and preserves all saved decisions.
    with pytest.raises(FusionAuditContractError):
        pipeline._validate_v2_fusion_contract(response(['comment:29'], 'high'), evidence, visible_evidence_ids=visible)


@pytest.mark.parametrize('field,value', [('evidence_risk_level', 'high'), ('rule_id', 'rule.b'), ('reason', '新编的依据'), ('matched_exemption_ids', ['new']), ('risk_score', 90), ('risk_basis', '新增依据'), ('risk_level', 'high')])
def test_comment_judgment_changes_rejected(pipeline, field, value):
    raw = response(['comment:1'])
    raw['evidence_items'][0][field] = value
    with pytest.raises(FusionAuditContractError):
        pipeline._validate_v2_fusion_contract(raw, index([comment(1, 'medium')]))


def test_budget_trimming_keeps_reference_boundary_and_full_counts(pipeline):
    comments = [comment(i, 'medium') for i in range(25)]
    evidence = index(comments)
    evidence['evidence_catalog'].extend({'evidence_id': f'image:{i}', 'text': '背景' * 200} for i in range(200))
    before = copy.deepcopy(evidence)
    with patch.object(settings, 'fusion_prompt_max_chars', 6500), patch('backend.audit_agent.pipeline.job_store.log'):
        prompt, data = render(pipeline, comments, evidence)
    assert len(prompt) <= 6500
    assert {x['evidence_id'] for x in data['top_comments']} <= pipeline._fusion_visible_ids(prompt)
    assert data['comment_stats']['level_counts']['medium'] == 25
    assert evidence == before


def test_invalid_response_saved_then_corrected_once(pipeline, tmp_path):
    comments = [comment(1, 'medium')]
    evidence = index(comments)
    prompt, _ = render(pipeline, comments, evidence)
    calls = []
    def audit_text(text, **kwargs):
        calls.append(text)
        return response(['comment:wrong' if len(calls) == 1 else 'comment:1'])
    pipeline.qwen = SimpleNamespace(audit_text=audit_text, last_raw_response=lambda: {'original': 'response'})
    pipeline._text_inference_options = lambda _: {'enable_thinking': True, 'max_tokens': 3000, 'request_timeout': 120}
    with patch.object(settings, 'outputs_dir', tmp_path), patch.object(settings, 'fusion_timeout_retries', 9), patch('backend.audit_agent.pipeline.job_store.log'):
        result = pipeline._run_fusion_audit('post', prompt, contract_validator=lambda raw: pipeline._validate_v2_fusion_contract(raw, evidence))
    assert len(calls) == 2 and 'comment:wrong' in calls[1] and 'allowed_evidence_ids' in calls[1]
    records = list(tmp_path.rglob('attempt-*.json'))
    assert len(records) == 1
    saved = json.loads(records[0].read_text())
    assert saved['invalid_evidence_ids'] == ['comment:wrong']
    assert saved['raw_provider_response'] == {'original': 'response'}
    assert result['evidence_items'][0]['reason'] == '已保存依据'


def test_second_invalid_response_stops(pipeline, tmp_path):
    calls = []
    def audit_text(text, **kwargs):
        calls.append(text)
        return response(['comment:wrong'])
    pipeline.qwen = SimpleNamespace(audit_text=audit_text)
    pipeline._text_inference_options = lambda _: {'enable_thinking': True, 'max_tokens': 3000, 'request_timeout': 120}
    with patch.object(settings, 'outputs_dir', tmp_path), patch.object(settings, 'fusion_timeout_retries', 99), patch('backend.audit_agent.pipeline.job_store.log'):
        with pytest.raises(FusionAuditContractError):
            pipeline._run_fusion_audit('post', render(pipeline, [comment(1, 'medium')])[0],
                                       contract_validator=lambda raw: pipeline._validate_v2_fusion_contract(raw, index([comment(1, 'medium')])))
    assert len(calls) == 2
    assert len(list(tmp_path.rglob('attempt-*.json'))) == 2
