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
def test_comment_judgment_changes_are_ignored_and_restored(pipeline, field, value):
    raw = response(['comment:1'])
    raw['evidence_items'][0][field] = value
    validated = pipeline._validate_v2_fusion_contract(raw, index([comment(1, 'medium')]))
    assert validated['evidence_items'] == [{
        'evidence_id': 'comment:1',
        'rule_id': 'rule.a',
        'evidence_risk_level': 'medium',
        'reason': '已保存依据',
    }]
    warning = validated['_fusion_contract_warnings'][0]
    assert warning['code'] == 'FUSION_COMPLETED_COMMENT_FIELDS_IGNORED'
    assert warning['evidence_id'] == 'comment:1'
    assert field in warning['fields']
    assert field in warning['mutated_fields']


def test_comment_mutation_does_not_discard_valid_non_comment_evidence(pipeline):
    evidence = index([comment(1, 'medium')])
    evidence['evidence_catalog'].append({
        'evidence_id': 'image:1',
        'primary_modality': 'image',
        'text': '画面证据',
    })
    raw = response(level='medium')
    raw['evidence_items'] = [
        {'evidence_id': 'comment:1', 'reason': '模型重新编写的评论依据'},
        {'evidence_id': 'image:1', 'evidence_risk_level': 'medium', 'reason': '原图像依据'},
    ]
    raw['rule_matches'] = [{
        'rule_id': 'rule.a',
        'evidence_ids': ['comment:1', 'image:1'],
    }]

    validated = pipeline._validate_v2_fusion_contract(raw, evidence)

    by_id = {item['evidence_id']: item for item in validated['evidence_items']}
    assert by_id['comment:1']['reason'] == '已保存依据'
    assert by_id['image:1']['reason'] == '原图像依据'
    assert by_id['image:1']['evidence_risk_level'] == 'medium'
    assert {item['source'] for item in validated['_fusion_contract_warnings']} == {
        'evidence_item', 'rule_match',
    }


def test_non_comment_evidence_contract_remains_strict(pipeline):
    evidence = {'evidence_catalog': [{'evidence_id': 'image:1'}]}
    raw = response(level='medium')
    raw['evidence_items'] = [{'evidence_id': 'image:1'}]
    raw['rule_matches'] = [{'rule_id': 'rule.a', 'evidence_ids': ['image:1']}]
    with pytest.raises(FusionAuditContractError, match='evidence_risk_level'):
        pipeline._validate_v2_fusion_contract(raw, evidence)


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
    feedback = json.loads(calls[1].split('完整 JSON：\n', 1)[1])
    assert feedback['previous_output']['evidence_items'] == [{'evidence_id': 'comment:wrong'}]
    assert '只能保留 evidence_id' in feedback['comment_evidence_contract']
    assert '保留上次已经正确的非评论字段' in feedback['non_comment_evidence_contract']
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


@pytest.mark.parametrize('corrected', [True, False])
def test_video_rule_contract_retains_failure_and_retries_only_once(pipeline, tmp_path, corrected):
    pipeline.rule_snapshot['stage_routes']['video_frame_evidence'] = ['rule.a']
    image = tmp_path / 'sheet.png'
    image.write_bytes(b'original sheet input')
    job = {'sheet_path': image, 'prompt': 'frozen K instructions',
           'sheet': {'segment_id': 'video_1/segment_2'},
           'frames': [{'frame_id': 'f0017'}], 'library_policy': {}}
    calls = []
    def analyze_image(path, prompt, **options):
        calls.append((path, prompt, options))
        return {'segment_summary': '画面', 'segment_score': 60,
                'risk_library_id': 'test', 'risk_library_label': '测试',
                'visual_risks': [{'frame_ids': ['f0017'], 'score': 60, 'risk_level': 'medium',
                                  'rule_id': 'R01' if corrected and len(calls) == 2 else 'R99',
                                  'risk_type': 'test', 'reason': '原审核依据'}], 'ocr_risks': [], 'asr_risks': []}
    pipeline.qwen = SimpleNamespace(analyze_image=analyze_image, last_raw_response=lambda: {'original': 'video response'})
    with patch.object(settings, 'outputs_dir', tmp_path), patch('backend.audit_agent.pipeline.job_store.log'):
        if corrected:
            result = pipeline._audit_review_sheet(job, authoritative_m3=True)
            assert result['visual_risks'][0]['rule_id'] == 'rule.a'
            assert result['visual_risks'][0]['reason'] == '原审核依据'
            assert result['segment_score'] == 60
        else:
            with pytest.raises(FusionAuditContractError, match='unknown rule code'):
                pipeline._audit_review_sheet(job, authoritative_m3=True)
    assert len(calls) == 2
    assert calls[0][1].startswith(job['prompt'])
    assert calls[1][1].startswith(job['prompt'])
    feedback = json.loads(calls[1][1].split('\n')[-1])
    assert feedback['returned_rule_codes'] == ['R99']
    assert feedback['allowed_ids']['rule_codes'] == ['R01']
    assert feedback['allowed_ids']['frame_ids'] == ['f0017']
    failures = list(tmp_path.rglob('failure.json'))
    assert len(failures) == (1 if corrected else 2)
    for record in failures:
        saved = json.loads(record.read_text())
        assert saved['parsed_response']['visual_risks'][0]['rule_id'] == 'R99'
        assert saved['raw_provider_response'] == {'original': 'video response'}
        assert (record.parent / 'sheet.png').read_bytes() == image.read_bytes()


def test_video_codes_preserve_frozen_order_and_judgments(pipeline):
    pipeline.rule_snapshot['stage_routes']['video_frame_evidence'] = ['rule.b', 'rule.a']
    mapping = pipeline._video_rule_code_mapping()
    assert mapping == {'R01': 'rule.b', 'R02': 'rule.a'}
    raw = {'visual_risks': [{'rule_id': 'R02', 'risk_level': 'low', 'score': 40,
                            'reason': '已判断依据', 'frame_ids': ['f0017']}],
           'ocr_risks': [], 'asr_risks': [{'rule_id': 'R01', 'score': 60}]}
    before = copy.deepcopy(raw)
    decoded = pipeline._decode_video_rule_codes(raw, mapping)
    assert raw == before
    assert decoded['visual_risks'][0] == {**raw['visual_risks'][0], 'rule_id': 'rule.a'}
    assert decoded['asr_risks'][0]['rule_id'] == 'rule.b'
    for value in ['R99', 'r01', 'R1', 'rule.a', 'R01/R02']:
        with pytest.raises(FusionAuditContractError):
            pipeline._decode_video_rule_codes({'visual_risks': [{'rule_id': value}]}, mapping)


def test_video_invalid_array_is_saved_and_stops_after_one_correction(pipeline, tmp_path):
    from backend.audit_agent.pipeline import AuditProviderCallError
    pipeline.rule_snapshot['stage_routes']['video_frame_evidence'] = ['rule.a']
    image = tmp_path / 'sheet.png'
    image.write_bytes(b'input image')
    prompts = []
    def analyze_image(path, prompt, **options):
        prompts.append(prompt)
        return {'segment_summary': 'test', 'segment_score': 0, 'risk_library_id': 'test',
                'risk_library_label': 'test', 'visual_risks': 7, 'ocr_risks': [], 'asr_risks': []}
    pipeline.qwen = SimpleNamespace(analyze_image=analyze_image)
    job = {'sheet': {'segment_id': 'test'}, 'sheet_path': image, 'frames': [],
           'prompt': 'rule.a｜说明。豁免ID rule.a.exemption；证据ID frame:rule.a'}
    with patch.object(settings, 'outputs_dir', tmp_path), patch('backend.audit_agent.pipeline.job_store.log'):
        with pytest.raises(AuditProviderCallError, match='invalid visual_risks'):
            pipeline._audit_review_sheet(job, authoritative_m3=True)
    assert len(prompts) == 2
    assert prompts[0].startswith('R01｜说明。豁免ID rule.a.exemption；证据ID frame:rule.a')
    records = list(tmp_path.rglob('failure.json'))
    assert len(records) == 2
    assert all(json.loads(path.read_text())['parsed_response']['visual_risks'] == 7 for path in records)
