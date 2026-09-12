import json
from pathlib import Path

import pytest

from backend.audit_agent.models import AuditSubject
from backend.audit_agent.pipeline import AuditPipeline, FusionAuditContractError
from backend.rulesets.compiler import compile_ruleset_revision, content_hash
from backend.rulesets.contracts import RuleSetContent


PATHS = ("comment", "image", "visual_risks", "ocr_risks", "asr_risks")
BOUNDARIES = (49, 50, 51, 59, 60, 61, 79, 80, 81, 99, 100)


def make_pipeline(ids):
    content = json.loads((Path(__file__).parent / "fixtures" / "recruitment_fraud_ruleset.json").read_text())
    for category, identity in zip(content["categories"], ids):
        category["category_id"] = identity
    content = RuleSetContent.model_validate(content)
    compiled = compile_ruleset_revision({
        "id": "identity-test", "ruleset_id": "identity-test", "version": 1,
        "status": "published", "snapshot": content.model_dump(),
        "content_hash": content_hash(content),
    })
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = "identity-test"
    pipeline.rule_snapshot = compiled["rule_snapshot"]
    pipeline.prompt_profile_snapshot = compiled["prompt_profile_snapshot"]
    pipeline.audit_policy_snapshot = compiled["audit_policy_snapshot"]
    return pipeline


def normalize(pipeline, path, identity):
    item = {
        "risk_type": identity, "rule_id": "recruitment.fee_request",
        "risk_level": "high", "severity": "high", "score": 80,
        "reason": "r" * 200, "evidence": "pay deposit",
    }
    if path == "comment":
        result = pipeline._normalize_comment_audit_results({"comments": [{
            "id": "c1", "s": 80, "risk_level": "high",
            "lib": "recruitment_fraud", "t": identity,
            "rb": "r" * 200, "q": "pay deposit", "rule_id": item["rule_id"],
        }]}, [{"comment_id": "c1", "source_text": "pay deposit"}])
        return result.get("c1")
    if path == "image":
        return pipeline._compact_risk_items([item], application_stage="image_evidence")[0]
    item.update(frame_ids=["f1"], ocr_chunk_id="o1", asr_chunk_id="a1")
    sheet = {
        "ocr_chunks": [{"ocr_chunk_id": "o1", "frame_ids": ["f1"]}],
        "asr_chunks": [{"asr_chunk_id": "a1"}],
    }
    return pipeline._normalize_segment_review(
        {path: [item]}, sheet, [{"frame_id": "f1"}],
    )[path][0]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("length", BOUNDARIES)
def test_legal_identity_exact_at_all_display_boundaries(path, length):
    identity = "recruitment." + "a" * (length - 12)
    pipeline = make_pipeline([identity])
    for key in ("image_prompt", "frame_prompt", "comment_prompt_template", "fusion_prompt_template"):
        assert identity in pipeline.prompt_profile_snapshot[key]
    row = normalize(pipeline, path, identity)
    assert row["risk_type"] == identity
    assert row["risk_type"].encode() == identity.encode()
    text_key = "risk_basis" if path == "comment" else "reason"
    assert len(row[text_key]) < 200


@pytest.mark.parametrize("path", PATHS)
def test_shared_99_character_prefix_does_not_collide(path):
    prefix = "recruitment." + "a" * 87
    identities = [prefix + "a", prefix + "b"]
    pipeline = make_pipeline(identities)
    actual = [normalize(pipeline, path, identity)["risk_type"] for identity in identities]
    assert actual == identities
    assert actual[0] != actual[1]


@pytest.mark.parametrize("path", PATHS)
def test_overlong_identity_is_rejected_not_repaired(path):
    legal = "a" * 100
    pipeline = make_pipeline([legal])
    if path == "comment":
        assert normalize(pipeline, path, legal + "b") is None
    else:
        with pytest.raises(FusionAuditContractError, match="risk_type"):
            normalize(pipeline, path, legal + "b")


@pytest.mark.parametrize("path", PATHS)
def test_short_identity_unchanged(path):
    identity = "recruitment.advance_fee"
    assert normalize(make_pipeline([identity]), path, identity)["risk_type"] == identity


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("legacy_mode", ["v1", "unmarked_v2"])
def test_legacy_free_form_labels_retain_display_truncation(path, legacy_mode):
    pipeline = make_pipeline(["recruitment.advance_fee"])
    if legacy_mode == "v1":
        pipeline.rule_snapshot["schema_version"] = 1
    else:
        pipeline.prompt_profile_snapshot["libraries"][0].pop("ruleset_content_driven")
    label = "L" * 120
    limit = 50 if path == "comment" else 80 if path == "image" else 60
    assert normalize(pipeline, path, label)["risk_type"] == pipeline._truncate_text(label, limit)


@pytest.mark.parametrize("value", [" recruitment.advance_fee ", "Recruitment.Advance_Fee"])
def test_identity_is_not_trimmed_or_case_normalized(value):
    pipeline = make_pipeline(["recruitment.advance_fee"])
    assert pipeline._normalize_risk_type(value, 50) == value


def test_comment_prompt_distinguishes_library_category_and_rule():
    pipeline = make_pipeline(["recruitment.advance_fee"])
    prompt = pipeline._render_comment_audit_prompt(
        AuditSubject("dy", "test", "", "招聘讨论", "", {}, [], [], []), "",
        [{"comment_id": "c1", "source_text": "pay deposit"}],
    )
    bindings = json.loads(prompt.split("输出字段身份对应：", 1)[1].split("\n", 1)[0])
    assert bindings["lib"] == "recruitment_fraud"
    assert bindings["rule_id_to_t"]["CR01"] == "recruitment.advance_fee"
    assert "不能互换" in prompt


def test_wrong_comment_library_is_not_guessed_from_a_valid_rule():
    pipeline = make_pipeline(["recruitment.advance_fee"])
    row = {"id": "c1", "s": 80, "risk_level": "high",
           "lib": "recruitment.advance_fee", "t": "recruitment.advance_fee",
           "rb": "要求缴纳押金", "q": "pay deposit", "rule_id": "recruitment.fee_request"}
    comments = [{"comment_id": "c1", "source_text": "pay deposit"}]
    assert pipeline._normalize_comment_audit_results({"comments": [row]}, comments) == {}
    row["lib"] = "recruitment_fraud"
    actual = pipeline._normalize_comment_audit_results({"comments": [row]}, comments)["c1"]
    assert actual["audit_status"] == "completed"
    assert actual["risk_level"] == "high"
    assert actual["rule_id"] == "recruitment.fee_request"


def test_all_identity_paths_reach_catalog_and_fusion():
    identity = "recruitment." + "a" * 88
    pipeline = make_pipeline([identity])
    subject = AuditSubject("xhs", "n1", "", "", "", {}, [], [], [])
    image = normalize(pipeline, "image", identity)
    comment = normalize(pipeline, "comment", identity)
    comment.update(comment_id="c1", source_text="pay deposit")
    video = {
        "asset_rel": "video.mp4",
        "segment_reviews": [{
            "segment_id": "s1",
            "analysis": {path: [normalize(pipeline, path, identity)] for path in PATHS[2:]},
        }],
    }
    index = pipeline._build_evidence_index(
        subject, [{"asset_rel": "image.jpg", "risk_items": [image]}], [video], [comment],
    )
    assert [row["risk_type"] for row in index["evidence_catalog"]] == [identity] * 5
    stored = json.loads(json.dumps(index))
    assert [row["risk_type"] for row in stored["evidence_catalog"]] == [identity] * 5
    prompt = pipeline._render_compact_fusion_prompt(subject, stored, [comment])
    assert identity in prompt
    assert "ruleset_content_driven" not in prompt
