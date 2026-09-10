"""Render each real prompt branch to keep stage-specific identifiers isolated."""
import json

import pytest

from backend.audit_agent.pipeline import AuditPipeline
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.prompts import get_prompt_set
from backend.rulesets.compiler import compile_ruleset_revision, content_hash
from backend.rulesets.trial_profiles import K_RULESET_ID, K2_RULESET_ID, TRIAL_BUNDLES


@pytest.mark.parametrize('ruleset_id', [None, K_RULESET_ID, K2_RULESET_ID])
def test_only_comment_audit_requests_comment_aliases(ruleset_id):
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = 'prompt-id-scope'
    p.rule_snapshot = {}
    p.prompt_profile_snapshot = {}
    p.prompt_set = get_prompt_set('soft')
    if ruleset_id:
        content = json.loads((TRIAL_BUNDLES[ruleset_id] / 'ruleset.json').read_text())
        compiled = compile_ruleset_revision({'id': 'scope-test', 'ruleset_id': ruleset_id,
            'version': 1, 'status': 'published', 'snapshot': content, 'content_hash': content_hash(content)})
        p.rule_snapshot = compiled['rule_snapshot']
        p._set_prompt_context('ethnic', compiled['prompt_profile_snapshot'])
    subject = AuditSubject('dy', 'post', '', '标题', '正文', {}, [], [], [])
    real_id = '7683403259888829241'
    comment_prompt = p._render_comment_audit_prompt(subject, '', [{'comment_id': real_id, 'source_text':'原文'}])
    assert p._comment_id_instructions() in comment_prompt
    assert real_id not in comment_prompt
    payload = json.loads(comment_prompt.split('输入 JSON：\n', 1)[1])
    assert payload['comments'][0]['comment_id'] == 'C01'

    evidence_id = 'comment:' + real_id
    rule_id = 'ethnic.group_stereotype_and_derogation'
    reviewed = {'comment_id':real_id, 'audit_status':'completed', 'risk_score':60,
                'risk_level':'medium', 'rule_id':rule_id, 'risk_basis':'已保存依据',
                'source_text':'已审核原文'}
    fusion = p._render_compact_fusion_prompt(subject, {'evidence_catalog':[
        {'evidence_id': evidence_id, 'primary_modality':'comment', 'risk_score':60,
         'evidence_risk_level':'medium', 'rule_id':rule_id, 'reason':'已保存依据',
         'text':'已审核原文'}]}, [reviewed])
    segment = {'segment_id':'video:1/segment:1','start':0,'end':10,
               'ocr_chunks':[{'ocr_chunk_id':'video:1/ocr:1','text':'字幕'}],
               'asr_chunks':[{'asr_chunk_id':'video:1/asr:1','text':'语音'}]}
    video = p._render_review_sheet_prompt(video_index=0, segment=segment,
        frames=[{'frame_id':'f0001','frame_number':1,'timestamp':0}],title='',desc='')
    sources = [{'index':7,'text':'原始语音'},{'index':8,'text':'后续语音'}]
    asr = p._render_asr_translation_prompt(transcript={'language':'ug'},segments=sources)
    asr_batch = p._render_asr_translation_prompt(transcript={'language':'ug'},segments=sources[:1],context_segments=sources)
    for prompt in [fusion, video, asr, asr_batch]:
        assert '评论编号约束' not in prompt
        assert 'C01' not in prompt
    assert evidence_id in fusion
    for identifier in ['f0001', 'video:1/ocr:1', 'video:1/asr:1']:
        assert identifier in video
    asr_payload = json.loads(asr_batch.split('输入 JSON：\n', 1)[1])
    assert asr_payload['target_indexes'] == [7]
    assert '[7]' in asr_payload['full_source_text'] and '[8]' in asr_payload['full_source_text']
