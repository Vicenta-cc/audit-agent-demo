import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from backend.audit_agent import pipeline as module
from backend.audit_agent.pipeline import AuditPipeline, AuditProviderCallError, FusionAuditContractError
from backend.audit_agent.qwen_client import QwenClient, QwenProviderError
from test_comment_alias_and_failure_isolation import compiled_k2, subject


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(module.settings, "outputs_dir", tmp_path)
    monkeypatch.setattr(module.job_store, "log", Mock())
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = "image-trace"
    p.authoritative_m3 = True
    p.rule_snapshot = compiled_k2()["rule_snapshot"]
    p.prompt_set = SimpleNamespace(image_prompt="image audit")
    p.qwen = SimpleNamespace(analyze_image=Mock(), last_raw_response=Mock(return_value={"body": "raw"}))
    return p


def traces(tmp_path, folder="image_attempts"):
    return [json.loads(p.read_text()) for p in sorted(tmp_path.glob(f"image-trace/assets/n/{folder}/*.json"))]


def valid():
    return {"visual_summary": "普通图片", "risk_items": []}


def test_response_saved_before_validation_and_success_recorded(pipeline, tmp_path):
    pipeline.qwen.analyze_image.return_value = valid()
    original = pipeline._validated_authoritative_visual_response

    def validate(value, **kwargs):
        saved = traces(tmp_path)
        assert saved[0]["status"] == "response_received"
        assert saved[0]["parsed_response"] == value
        assert saved[0]["raw_provider_response"] == {"body": "raw"}
        return original(value, **kwargs)

    pipeline._validated_authoritative_visual_response = validate
    pipeline._run_image_audit(subject(), "image.jpg", evidence_id="image:0")
    assert traces(tmp_path)[0]["status"] == "completed"
    assert not traces(tmp_path, "image_failures")
    assert next(tmp_path.glob("image-trace/assets/n/image_attempts/*.json")).stat().st_mode & 0o777 == 0o600


def test_wrapped_contract_failure_during_provider_call_is_saved_and_retried(pipeline, tmp_path):
    def call(*args, **kwargs):
        if pipeline.qwen.analyze_image.call_count == 1:
            try:
                raise FusionAuditContractError("unknown rule code IR99")
            except FusionAuditContractError as error:
                raise AuditProviderCallError("wrapped") from error
        return valid()

    pipeline.qwen.analyze_image.side_effect = call
    pipeline._run_image_audit(subject(), "image.jpg", evidence_id="image:0")
    failure = traces(tmp_path, "image_failures")[0]
    assert failure["phase"] == "provider_call"
    assert failure["cause_types"] == ["AuditProviderCallError", "FusionAuditContractError"]
    assert "unknown rule code IR99" in failure["traceback"]
    assert failure["will_retry"]
    assert len(traces(tmp_path)) == 2


def test_invalid_field_is_recorded_and_exhaustion_has_paths(pipeline, tmp_path):
    pipeline.qwen.analyze_image.return_value = {"visual_summary": [], "risk_items": []}
    with pytest.raises(FusionAuditContractError, match="invalid visual_summary") as caught:
        pipeline._run_image_audit(subject(), "image.jpg", evidence_id="image:0")
    failures = traces(tmp_path, "image_failures")
    assert len(failures) == 2
    assert failures[-1]["parsed_response"]["visual_summary"] == []
    assert not failures[-1]["will_retry"]
    assert len(caught.value.diagnostic_paths) == 4
    pipeline.ingestion = SimpleNamespace(mark_content_status=Mock())
    pipeline._record_subject_failure("dy", "n", subject(), caught.value)
    record = json.loads(next(tmp_path.glob("image-trace/post_failures/*.json")).read_text())
    assert record["error_code"] == "fusion_contract_invalid"
    assert record["diagnostic_paths"] == caught.value.diagnostic_paths


def test_transport_failure_is_saved_without_contract_retry(pipeline, tmp_path):
    pipeline.qwen.analyze_image.side_effect = QwenProviderError("HTTP 503")
    with pytest.raises(QwenProviderError):
        pipeline._run_image_audit(subject(), "image.jpg", evidence_id="image:0")
    assert pipeline.qwen.analyze_image.call_count == 1
    failure = traces(tmp_path, "image_failures")[0]
    assert failure["phase"] == "provider_call"
    assert failure["parsed_response"] is None
    assert not failure["will_retry"]


def test_write_failure_does_not_mask_original_contract_error(pipeline, monkeypatch):
    pipeline.qwen.analyze_image.return_value = {"visual_summary": 123}
    monkeypatch.setattr(Path, "mkdir", Mock(side_effect=OSError("disk full")))
    with pytest.raises(FusionAuditContractError, match="invalid visual_summary") as caught:
        pipeline._run_image_audit(subject(), "image.jpg", evidence_id="image:0")
    assert caught.value.diagnostic_write_errors
    assert not caught.value.diagnostic_paths


@pytest.mark.parametrize("body,status", [(b'not json', 200), (b'{"error":"blocked"}', 400)])
def test_qwen_preserves_invalid_http_body_before_raising(monkeypatch, body, status):
    response = requests.Response()
    response.status_code = status
    response._content = body
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    client = QwenClient()
    with pytest.raises(QwenProviderError):
        client._post_chat({"model": "test"})
    captured = client.last_raw_response()
    assert captured == ({"http_status": 200, "body": "not json"} if status == 200 else {"error": "blocked"})
