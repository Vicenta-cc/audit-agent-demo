import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
import requests

from backend.audit_agent.config import settings
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.pipeline import AuditPipeline, AuditProviderCallError
from backend.audit_agent.qwen_client import QwenClient
from backend.investigation_creation.frozen import compilation_hash
from backend.rulesets.compiler import compile_ruleset_revision, content_hash
from backend.rulesets.errors import RuleSetValidationError
from backend.rulesets.trial_profiles import BUNDLE_DIR, K_RULESET_ID


def revision():
    content = json.loads((BUNDLE_DIR / "ruleset.json").read_text())
    return {"id": f"ruleset-revision:{K_RULESET_ID}:v1", "ruleset_id": K_RULESET_ID,
            "status": "published", "version": 1, "snapshot": content,
            "content_hash": content_hash(content)}


def test_complete_profile_frozen_and_hashed():
    compiled = compile_ruleset_revision(revision())
    source = json.loads((BUNDLE_DIR / "prompt-profile.json").read_text())
    actual = compiled["prompt_profile_snapshot"]
    for field in ("comment_prompt_template", "fusion_prompt_template", "image_prompt", "frame_prompt"):
        assert actual[field] == source[field]
    assert actual["inference_settings"]["comment_audit"]["enable_thinking"] is True
    assert compilation_hash(compiled) == compiled["config_hash"]
    changed = deepcopy(compiled)
    changed["prompt_profile_snapshot"]["inference_settings"]["comment_audit"]["enable_thinking"] = False
    assert compilation_hash(changed) != compiled["config_hash"]


def test_other_revisions_keep_standard_compiler():
    value = revision()
    value["ruleset_id"] = "ordinary-ruleset"
    actual = compile_ruleset_revision(value)["prompt_profile_snapshot"]
    assert "inference_settings" not in actual
    assert actual["prompt_version"].startswith("ruleset-v1-")


def test_changed_trial_content_fails_closed():
    value = revision()
    value["snapshot"]["audit_goal"] += " changed"
    value["content_hash"] = content_hash(value["snapshot"])
    with pytest.raises(RuleSetValidationError, match="pinned profile"):
        compile_ruleset_revision(value)


@pytest.mark.parametrize("stage,max_tokens", [("comment_audit", 6000), ("fusion_audit", 3000)])
def test_real_pipeline_wire_options_and_no_stacked_retry(monkeypatch, stage, max_tokens):
    monkeypatch.setattr(settings, "use_remote_llm", False)
    monkeypatch.setattr(settings, "fusion_timeout_retries", 5)
    monkeypatch.setattr("backend.audit_agent.qwen_client.time.sleep", Mock())
    monkeypatch.setattr("backend.audit_agent.pipeline.job_store.log", Mock())
    post = Mock(side_effect=requests.ReadTimeout())
    monkeypatch.setattr("backend.audit_agent.qwen_client.requests.post", post)
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.qwen = QwenClient()
    pipeline.qwen.api_key = "test-key"
    pipeline.qwen.chat_url = "https://example.invalid/chat/completions"
    pipeline.job_id = "test-k-profile"
    pipeline.authoritative_m3 = True
    pipeline.prompt_profile_snapshot = compile_ruleset_revision(revision())["prompt_profile_snapshot"]
    pipeline._render_comment_audit_prompt = Mock(return_value="rendered K context")
    with pytest.raises(AuditProviderCallError, match="timed out"):
        if stage == "comment_audit":
            subject = AuditSubject(platform="dy", note_id="note", url="", title="", desc="",
                                   author={}, image_urls=[], video_urls=[], comments=[])
            pipeline._audit_comment_batch_with_fallback(subject, "media context",
                [{"comment_id": "1", "source_text": "comment"}])
        else:
            pipeline._run_fusion_audit("note", "rendered fusion context")
    assert post.call_count == 2
    assert post.call_args_list[0] == post.call_args_list[1]
    payload = post.call_args.kwargs["json"]
    assert payload["enable_thinking"] is True
    assert payload["model"] == "qwen3.7-plus"
    assert payload["max_tokens"] == max_tokens
    assert payload["temperature"] == 0
    assert post.call_args.kwargs["timeout"] == 120


def test_historical_pipeline_defaults_preserved():
    pipeline = AuditPipeline.__new__(AuditPipeline)
    assert pipeline._text_inference_options("comment_audit") == {
        "model": settings.qwen_text_model, "max_tokens": settings.comment_audit_max_tokens,
        "enable_thinking": False}
    assert pipeline._text_inference_options("fusion_audit")["enable_thinking"] is False


def test_publish_then_execution_uses_pinned_profile(tmp_path):
    from backend.rulesets.contracts import RuleSetContent
    from backend.rulesets.store import RuleSetStore
    from backend.rulesets.service import RuleSetService
    from backend.investigation_creation.principal import Principal

    service = RuleSetService(RuleSetStore(tmp_path / "audit.sqlite3"))
    principal = Principal("test-owner")
    draft = service.create_draft(RuleSetContent.model_validate(revision()["snapshot"]),
                                ruleset_id=K_RULESET_ID, principal=principal)
    published = service.publish(K_RULESET_ID, expected_revision=draft["draft_revision"],
                                idempotency_key="test-publish-k", principal=principal)
    actual = service.compile_for_execution(published["id"], principal=principal)
    assert actual["prompt_profile_snapshot"] == compile_ruleset_revision(revision())["prompt_profile_snapshot"]
    assert actual["config_hash"] == compilation_hash(actual)
