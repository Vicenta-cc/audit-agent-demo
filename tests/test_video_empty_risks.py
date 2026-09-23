"""Regression: real segment-2 responses from note 7005899703250373891.

Only provider metadata removed; no silent coercion of the invalid risk fields.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.audit_agent.config import settings
from backend.audit_agent.pipeline import AuditPipeline, AuditProviderCallError

RESPONSES = json.loads((Path(__file__).parent / "fixtures/video_empty_risk_responses.json").read_text())


@pytest.mark.parametrize("first", RESPONSES)
@pytest.mark.parametrize("correction", ["none", "risk", "invalid"])
def test_real_empty_levels_require_model_correction(tmp_path, first, correction):
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = "video-empty-test"
    p.rule_snapshot = {"schema_version": 2, "stage_routes": {"video_frame_evidence": ["rule.a"]}}
    p.prompt_set = SimpleNamespace(category="test", prompt_version="test")
    sheet_path = tmp_path / "sheet.jpg"
    sheet_path.write_bytes(b"test-image")
    job = {"sheet_path": sheet_path, "prompt": "Frozen rules and legacy zero-score example",
           "sheet": {"segment_id": "video:1/segment:2",
                     "ocr_chunks": [{"ocr_chunk_id": "video:1/segment:2/ocr:1"}],
                     "asr_chunks": [{"asr_chunk_id": "video:1/segment:2/asr:2"}]},
           "frames": [{"frame_id": f"f{i:04d}"} for i in range(17, 21)],
           "library_policy": {"id": "test", "title": "测试"}}
    fixed = {"segment_summary": "正常技术介绍", "segment_score": 0,
             "visual_risks": [], "ocr_risks": [], "asr_risks": []}
    if correction == "risk":
        fixed.update(segment_score=60, visual_risks=[{
            "frame_ids": ["f0017"], "score": 60, "risk_level": "medium",
            "rule_id": "R01", "risk_type": "test", "reason": "保留真实风险"}])
    elif correction == "invalid":
        fixed = RESPONSES[1]
    prompts = []
    def analyze_image(path, prompt, **kwargs):
        prompts.append(prompt)
        return copy.deepcopy(first if len(prompts) == 1 else fixed)
    p.qwen = SimpleNamespace(analyze_image=analyze_image, last_raw_response=lambda: {"captured": True})
    before = copy.deepcopy(first)
    with patch.object(settings, "outputs_dir", tmp_path), patch("backend.audit_agent.pipeline.job_store.log"):
        if correction == "invalid":
            with pytest.raises(AuditProviderCallError, match="invalid risk_level"):
                p._audit_review_sheet(job, authoritative_m3=True)
        else:
            result = p._audit_review_sheet(job, authoritative_m3=True)
            assert result["segment_score"] == fixed["segment_score"]
            assert result["ocr_risks"] == result["asr_risks"] == []
            if correction == "risk":
                assert result["visual_risks"][0]["rule_id"] == "rule.a"
                assert result["visual_risks"][0]["reason"] == "保留真实风险"
            else:
                assert result["visual_risks"] == []
    assert first == before
    assert len(prompts) == 2
    assert "video-empty-risks-v1" in prompts[0]
    assert "本次错误是 risk_level" in prompts[1]
    assert "可以移除误填的零分无风险占位项" in prompts[1]
    assert "不得为了通过校验删除真实风险" in prompts[1]
    failures = [json.loads(path.read_text()) for path in tmp_path.rglob("failure.json")]
    assert len(failures) == (2 if correction == "invalid" else 1)
    assert any(record["parsed_response"] == first for record in failures)
    assert all(record["raw_provider_response"] == {"captured": True} for record in failures)
