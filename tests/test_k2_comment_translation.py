"""K2's frozen prompt and comment pipeline must agree on optional translation."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.audit_agent import pipeline as module
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.pipeline import AuditPipeline
from backend.audit_agent.translation import TranslationProcessor
from backend.rulesets.compiler import compile_ruleset_revision, content_hash
from backend.rulesets.trial_profiles import K2_RULESET_ID, TRIAL_BUNDLES


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(module.settings, "outputs_dir", tmp_path)
    monkeypatch.setattr(module.settings, "translation_enabled", True)
    monkeypatch.setattr(module.settings, "translation_arabic_ratio_threshold", 0.25)
    monkeypatch.setattr(module.settings, "comment_audit_concurrency", 1)
    monkeypatch.setattr(module.settings, "comment_audit_batch_size", 20)
    monkeypatch.setattr(module.job_store, "log", Mock())
    content = json.loads((TRIAL_BUNDLES[K2_RULESET_ID] / "ruleset.json").read_text())
    compiled = compile_ruleset_revision({
        "id": "k2-translation-test", "ruleset_id": K2_RULESET_ID,
        "version": 1, "status": "published", "snapshot": content,
        "content_hash": content_hash(content),
    })
    p = AuditPipeline.__new__(AuditPipeline)
    p.job_id = "k2-translation-test"
    p.rule_snapshot = compiled["rule_snapshot"]
    p._set_prompt_context("ethnic", compiled["prompt_profile_snapshot"])
    p.translator = TranslationProcessor.__new__(TranslationProcessor)
    p.qwen = SimpleNamespace(audit_text=Mock())
    return p


def subject(comments):
    return AuditSubject(platform="dy", note_id="translation-post", title="", desc="",
                        url="", author={}, image_urls=[], video_urls=[], comments=comments)


def payload(call):
    return json.loads(call.args[0].split("输入 JSON：\n", 1)[1])


def test_compiled_k2_prompt_includes_conditional_translation(pipeline):
    prompt = pipeline._render_comment_audit_prompt(subject([]), "", [
        {"comment_id": "original-id", "source_text": "ياخشى", "translation_required": True},
    ])
    assert "translation_required=true" in prompt
    assert "translation_required=false" in prompt
    assert "zh=translation_zh" in prompt
    assert "none项仅id/s=0/risk_level=none。" not in prompt
    assert "无论none还是风险项" in prompt
    profile = pipeline.prompt_profile_snapshot
    assert profile["fixed_prompt_chars"]["comment_audit"] == len(profile["comment_prompt_template"])


def test_mixed_batch_translates_only_needed_comments_and_reuses_existing(pipeline):
    pipeline.qwen.audit_text.return_value = {"comments": [
        {"id": "C03", "s": 0, "risk_level": "none"},
        {"id": "C01", "s": 0, "risk_level": "none", "zh": "好"},
        {"id": "C02", "s": 0, "risk_level": "none"},
    ]}
    result = pipeline._audit_comments(subject([
        {"comment_id": "ug", "content": "ياخشى"},
        {"comment_id": "chinese", "content": "正常中文评论"},
        {"comment_id": "translated", "content": "ياخشى", "language": "ug", "translation_zh": "已有译文"},
    ]), "")
    pipeline.qwen.audit_text.assert_called_once()
    requested = payload(pipeline.qwen.audit_text.call_args)["comments"]
    assert [c["translation_required"] for c in requested] == [True, False, False]
    assert requested[2]["translation_zh"] == "已有译文"
    by_id = {c["comment_id"]: c for c in result}
    assert all(c["audit_status"] == "completed" for c in result)
    assert by_id["ug"]["translation_zh"] == "好"
    assert by_id["ug"]["translation_status"] == "completed"
    assert by_id["chinese"]["translation_zh"] == ""
    assert by_id["chinese"]["translation_status"] == "not_needed"
    assert by_id["translated"]["translation_zh"] == "已有译文"
    assert by_id["translated"]["translation_status"] == "completed"


@pytest.mark.parametrize("risk_level", ["none", "medium"])
@pytest.mark.parametrize("translated_on_attempt", [1, 2, None])
def test_translation_missing_retries_once_without_repeating_completed_comments(
    pipeline, risk_level, translated_on_attempt,
):
    # Synthetic provider replies exercise the contract, not translation quality.
    source = "ياخشى" if risk_level == "none" else "ياخشى 汉族都是垃圾"
    translated = "好" if risk_level == "none" else "好，汉族都是垃圾"
    def reply(prompt, **options):
        data = json.loads(prompt.split("输入 JSON：\n", 1)[1])
        attempt = pipeline.qwen.audit_text.call_count
        rows = []
        for item in data["comments"]:
            row = {"id": item["comment_id"], "s": 0, "risk_level": "none"}
            if item["translation_required"]:
                if risk_level == "medium":
                    row.update(
                        s=60,
                        risk_level="medium",
                        lib="ethnic",
                        t="ethnic.content_attack",
                        rb="民族侮辱",
                        q="汉族都是垃圾",
                        rule_id=next(
                            code
                            for code, rule_id in pipeline._comment_rule_code_mapping().items()
                            if rule_id == "ethnic.group_stereotype_and_derogation"
                        ),
                    )
                if attempt == translated_on_attempt:
                    row["zh"] = translated
            rows.append(row)
        return {"comments": rows}
    pipeline.qwen.audit_text.side_effect = reply
    result = pipeline._audit_comments(subject([
        {"comment_id": "chinese", "content": "正常中文评论"},
        {"comment_id": "needs-translation", "content": source, "language": "ug"},
    ]), "")
    calls = pipeline.qwen.audit_text.call_args_list
    assert len(calls) == (1 if translated_on_attempt == 1 else 2)
    if len(calls) == 2:
        assert payload(calls[1])["comments"] == [
            {"comment_id": "C01", "source_text": source, "translation_required": True},
        ]
    by_id = {c["comment_id"]: c for c in result}
    assert by_id["chinese"]["audit_status"] == "completed"
    target = by_id["needs-translation"]
    if translated_on_attempt is None:
        assert target["audit_status"] == "failed"
        assert target["translation_status"] == "failed"
        assert "after one retry" in target["translation_error"]
    else:
        assert target["audit_status"] == "completed"
        assert target["translation_status"] == "completed"
        assert target["translation_zh"] == translated
        assert target["risk_level"] == risk_level
