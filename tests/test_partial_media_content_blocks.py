"""A provider refusal belongs to one request, never to an entire post."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from backend.audit_agent import pipeline as module
from backend.audit_agent.ingestion import AuditResultStore
from backend.audit_agent.pipeline import AuditPipeline, AuditProviderCallError
from backend.audit_agent.qwen_client import QwenClient, QwenContentBlockedError, is_content_blocked
from test_comment_alias_and_failure_isolation import compiled_k2, subject


def blocked():
    response = requests.Response()
    response.status_code = 400
    response._content = b'{"error":{"code":"data_inspection_failed"}}'
    error = QwenContentBlockedError("content inspection refused")
    error.__cause__ = requests.HTTPError(response=response)
    return error


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(module.settings, "outputs_dir", tmp_path)
    monkeypatch.setattr(module.settings, "video_review_sheet_frames", 16)
    monkeypatch.setattr(module.settings, "video_review_max_frames", 32)
    monkeypatch.setattr(module.settings, "ocr_enabled", False)
    monkeypatch.setattr(module.job_store, "log", Mock())
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = "partial-test"
    p.authoritative_m3 = True
    p._audit_gaps = []
    p._audit_note_id = "note"
    compiled = compiled_k2()
    p.rule_snapshot = compiled["rule_snapshot"]
    p._set_prompt_context("ethnic", compiled["prompt_profile_snapshot"])
    p._validate_authoritative_subject_configuration = Mock()
    p.translator = SimpleNamespace(should_translate=lambda *a, **k: False)
    p.audio = SimpleNamespace(extract_audio=lambda *a: None, last_extract_status="no_audio_track")

    def frames(**kwargs):
        folder = kwargs["output_dir"]
        folder.mkdir(parents=True, exist_ok=True)
        result = []
        for index in range(32):
            path = folder / f"f{index + 1:04d}.jpg"
            path.write_bytes(b"test-only frame")
            result.append(dict(path=str(path), frame_id=f"f{index + 1:04d}", timestamp=float(index), frame_number=index * 30))
        return result

    def sheet(frames, path, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-only sheet")

    p.frames = SimpleNamespace(extract_timeline_frames=frames, create_contact_sheet=sheet,
                              video_meta=lambda path: (30, 971, 32.3666667))
    p._scan_timeline_ocr_batch = Mock(return_value={})
    p.qwen = SimpleNamespace(enabled=True, provider_failure="", last_raw_response=lambda: {})
    return p


def setup_provider(p, *, bad_sheets=(2,), fusion_block=False):
    calls = []

    def vision(path, prompt, **kwargs):
        index = int(Path(path).stem.rsplit("_", 1)[-1])
        calls.append(index)
        if index in bad_sheets:
            raise blocked()
        return {"segment_summary": "正常场景", "segment_score": 0,
                "visual_risks": [], "ocr_risks": [], "asr_risks": []}

    def text(prompt, **kwargs):
        if '"comments":' in prompt and '"C01"' in prompt:
            return {"comments": [{"id": "C01", "s": 0, "risk_level": "none"}]}
        if fusion_block:
            raise blocked()
        return {"schema_version": "audit_fusion_v4", "content_title": "正常视频内容记录",
                "summary": "已完成部分未发现明确风险", "decision_suggestion": "pass",
                "risk_level_suggestion": "none", "primary_risk": "", "categories": [],
                "evidence_items": [], "rule_matches": []}

    p.qwen.analyze_image = Mock(side_effect=vision)
    p.qwen.audit_text = Mock(side_effect=text)
    return calls


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("bad_sheets", [(1,), (2,), (1, 2)])
def test_blocked_sheet_keeps_other_results_and_continues_comments(pipeline, tmp_path, monkeypatch, workers, bad_sheets):
    p = pipeline
    monkeypatch.setattr(module.settings, "video_review_concurrency", workers)
    calls = setup_provider(p, bad_sheets=bad_sheets)
    s = subject("7679055422275255460")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test-only media")
    s.local_video_paths = [str(video)]
    s.comments = [{"comment_id": "c1", "content": "正常评论"}]
    result = p._analyze_subject_with_capacity(s)
    p._assert_authoritative_provider_healthy()
    assert sorted(calls) == [1, 2]  # no retry of any refused request
    assert result["comments"][0]["audit_status"] == "completed"
    assert result["audit_completion_status"] == "partial"
    assert result["decision"] == "review" and result["risk_level"] == "unknown"
    assert result["risk_score"] is None and not result["has_risk"]
    assert len(result["audit_gaps"]) == len(bad_sheets)
    gap = result["audit_gaps"][0]
    assert gap["manual_review_required"] and gap["error_code"] == "audit_content_blocked"
    reviews = result["video_results"][0]["segment_reviews"]
    for review in reviews:
        if review["index"] in bad_sheets:
            assert review["analysis"]["segment_level"] == "unknown"
            assert review["analysis"]["segment_score"] is None
        else:
            assert review["analysis"]["segment_summary"] == "正常场景"
    if bad_sheets == (2,):
        assert gap["start"] == 15 and gap["end"] == pytest.approx(32.3666667)
    assert len(list((tmp_path / p.job_id / "audit_gaps").glob("*.json"))) == len(bad_sheets)
    assert not (tmp_path / p.job_id / "post_failures").exists()
    # Real SQLite round-trip, including compact list (not just detail JSON).
    store = AuditResultStore(tmp_path / "results.sqlite3")
    saved = store.upsert_result(job_id=p.job_id, platform="dy", content_key=s.note_id, result=result)
    assert saved["audit_gaps"] == result["audit_gaps"]
    assert store._compact_list_item(saved)["audit_completion_status"] == "partial"


def test_fusion_block_keeps_completed_comment_judgment(pipeline):
    p = pipeline
    setup_provider(p, bad_sheets=(), fusion_block=True)
    s = subject()
    s.comments = [{"comment_id": "c1", "content": "正常评论"}]
    result = p._analyze_subject_with_capacity(s)
    assert result["comments"][0]["audit_status"] == "completed"
    assert result["decision"] == "review" and result["risk_level"] == "unknown"
    assert result["audit_gaps"][0]["stage"] == "fusion_audit"
    assert result["model_provenance"]["completed_modalities"] == []
    assert p.qwen.audit_text.call_count == 2  # comment then fusion; no fallback model


def test_image_block_is_per_image(pipeline, tmp_path):
    p = pipeline
    paths = [tmp_path / f"{i}.jpg" for i in range(2)]
    for path in paths:
        path.write_bytes(b"test-only image")
    s = subject()
    s.local_image_paths = [str(path) for path in paths]
    p.qwen.analyze_image = Mock(side_effect=[blocked(), {"visual_summary": "第二张已审核", "risk_items": []}])
    results = p._analyze_images(s, tmp_path / "images")
    assert results[0]["audit_status"] == "unreviewed"
    assert "risk_items" not in results[0]
    assert results[1]["visual_summary"] == "第二张已审核"
    assert p.qwen.analyze_image.call_count == 2


@pytest.mark.parametrize("status", [401, 403, 500])
def test_other_provider_errors_still_fail(pipeline, tmp_path, status):
    response = requests.Response()
    response.status_code = status
    response._content = b'data_inspection_failed'
    error = requests.HTTPError(response=response)
    assert not is_content_blocked(error)
    setup_provider(pipeline)
    pipeline.qwen.analyze_image.side_effect = error
    with pytest.raises(AuditProviderCallError):
        pipeline._analyze_video_file(0, tmp_path / "video.mp4", tmp_path / "media", "", "local")
    assert pipeline._audit_gaps == []


def test_translation_block_retains_source_and_real_time(pipeline):
    p = pipeline
    p.qwen.audit_text = Mock(side_effect=blocked())
    transcript = {"text": "foreign speech", "segments": [{"start": 8, "end": 12, "text": "foreign speech"}]}
    before = copy.deepcopy(transcript)
    result = p._translate_asr_segments_with_llm(transcript)
    assert result["error"] == "audit_content_blocked" and transcript == before
    assert p._audit_gaps[0]["start"] == 8 and p._audit_gaps[0]["end"] == 12
    assert p.qwen.audit_text.call_count == 1


def test_content_filter_200_is_a_gap_not_invalid_json(monkeypatch):
    c = QwenClient()
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]}).encode()
    post = Mock(return_value=response)
    monkeypatch.setattr("backend.audit_agent.qwen_client.requests.post", post)
    with pytest.raises(QwenContentBlockedError):
        c._post_chat({"model": "test-only"})
    assert post.call_count == 1 and not c.provider_failure


def test_block_never_clears_other_threads_provider_failure():
    c = QwenClient()
    c.provider_failure = "an independent request failed authentication"
    assert isinstance(c._provider_error("blocked", blocked()), QwenContentBlockedError)
    assert c.provider_failure == "an independent request failed authentication"


def test_fusion_block_preserves_real_image_and_comment_evidence(pipeline, tmp_path):
    p = pipeline
    s = subject()
    path = tmp_path / "image.jpg"
    path.write_bytes(b"fixture")
    s.local_image_paths = [str(path)]
    s.comments = [{"comment_id": "c1", "content": "汉族都是垃圾"}]
    rule = "ethnic.group_stereotype_and_derogation"
    image_code = next(code for code, rid in p._image_rule_code_mapping().items() if rid == rule)
    comment_code = next(code for code, rid in p._comment_rule_code_mapping().items() if rid == rule)
    p.qwen.analyze_image = Mock(return_value={"visual_summary": "含群体侮辱文字", "risk_items": [{
        "rule_id": image_code, "risk_type": "群体侮辱", "evidence": "群体侮辱文字",
        "reason": "对民族群体的贬损", "severity": "medium"}]})
    p.qwen.audit_text = Mock(side_effect=[{"comments": [{"id": "C01", "s": 60, "risk_level": "medium",
        "lib": "ethnic", "t": "ethnic.content_attack", "rule_id": comment_code,
        "rb": "群体侮辱", "q": "汉族都是垃圾"}]}, blocked()])
    result = p._analyze_subject_with_capacity(s)
    assert {item["primary_modality"] for item in result["evidence_items"]} == {"vision", "comment"}
    assert all(item["fusion_status"] == "unreviewed" for item in result["evidence_items"])
    assert result["decision"] == "review" and result["risk_basis"] == "stage_evidence_pending_fusion"
    assert p.qwen.audit_text.call_count == 2


@pytest.mark.parametrize("workers", [1, 2])
def test_ocr_gap_is_one_frame_and_preserves_other_frame(pipeline, monkeypatch, workers):
    p = pipeline
    monkeypatch.setattr(module.settings, "ocr_enabled", True)
    monkeypatch.setattr(module.settings, "ocr_concurrency", workers)
    def scan(path, **kwargs):
        if str(path) == "blocked.jpg":
            return {"error_code": "audit_content_blocked", "text": ""}
        return {"text": "正常画面文字"}
    p.ocr = SimpleNamespace(scan_image=scan)
    frames = [{"path": "blocked.jpg", "timestamp": 0, "frame_id": "f1"},
              {"path": "ok.jpg", "timestamp": 4, "frame_id": "f2"}]
    results = AuditPipeline._scan_timeline_ocr_batch(p, 0, "视频 1", frames)
    assert results[2]["text"] == "正常画面文字"
    assert len(p._audit_gaps) == 1 and p._audit_gaps[0]["start"] == 0


def test_next_post_does_not_inherit_gaps(pipeline):
    p = pipeline
    setup_provider(p, fusion_block=True)
    assert p._analyze_subject_with_capacity(subject())["audit_gaps"]
    setup_provider(p)
    result = p._analyze_subject_with_capacity(subject("next"))
    assert result["audit_gaps"] == [] and result["audit_completion_status"] == "completed"
    assert result["decision"] == "pass"


def test_auxiliary_title_refusal_does_not_invalidate_finished_audit(pipeline):
    p = pipeline
    p.qwen.audit_text = Mock(side_effect=blocked())
    s = subject()
    s.title = "已完成审核的内容"
    assert p._ensure_content_title({}, s, {}) == s.title
    assert p._audit_gaps == []
