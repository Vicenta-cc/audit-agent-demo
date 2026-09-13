from dataclasses import asdict
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.rulesets import compiler
from backend.rulesets.contracts import RuleSetContent
from backend.rulesets.gambling_v1 import gambling_ruleset_v2
from backend.rulesets.service import RuleSetService
from backend.rulesets.store import RuleSetStore


FIXTURES = Path(__file__).parent / "fixtures"
STAGE_KEYS = dict(zip(compiler._STAGES, (
    "image_prompt", "frame_prompt", "comment_prompt_template", "fusion_prompt_template",
)))
LEAKS = ("赌博", "博彩", "投注平台", "盘口赔率", "代理推广", "反赌", "gambling-v1")


def recruitment_content():
    return RuleSetContent.model_validate(json.loads(
        (FIXTURES / "recruitment_fraud_ruleset.json").read_text(encoding="utf-8")
    ))


def golden_case(content):
    revision = {
        "id": f"ruleset-revision:{content.domain}:test",
        "ruleset_id": f"ruleset.{content.domain}", "version": 1,
        "status": "published", "snapshot": content.model_dump(mode="json"),
        "content_hash": compiler.content_hash(content),
    }
    return {
        "content_hash": compiler.content_hash(content),
        "content_compile_result": asdict(compiler.compile_ruleset_content(content)),
        "formal_compile_result": compiler.compile_ruleset_revision(revision),
    }


@pytest.mark.parametrize("factory", [gambling_ruleset_v2, recruitment_content])
def test_t2_5_golden_and_content_semantics(factory):
    content = factory()
    golden = json.loads((FIXTURES / "ruleset_compiler_t2_5_golden.json").read_text(encoding="utf-8"))
    actual, historical = golden_case(content), deepcopy(golden[content.domain])
    # Keep the historical oracle intact. Only comment field bindings and their
    # derived prompt/config hashes may differ from it.
    for key in ("content_compile_result", "formal_compile_result"):
        profile = actual[key]["prompt_profile_snapshot"]
        old_profile = historical[key]["prompt_profile_snapshot"]
        before, addition = profile["comment_prompt_template"].split("\n\n输出字段身份对应：", 1)
        _, after = addition.split("\n\n只依据当前评论", 1)
        assert before + "\n\n只依据当前评论" + after == old_profile["comment_prompt_template"]
        assert profile["prompt_version"] != old_profile["prompt_version"]
        for field in ("comment_prompt_template", "prompt_version"):
            old_profile[field] = profile[field]
        old_profile["fixed_prompt_chars"]["comment_audit"] = profile["fixed_prompt_chars"]["comment_audit"]
    assert actual["formal_compile_result"]["config_hash"] != historical["formal_compile_result"]["config_hash"]
    historical["formal_compile_result"]["config_hash"] = actual["formal_compile_result"]["config_hash"]
    assert actual == historical
    assert golden_case(content) == golden_case(content.model_copy(deep=True))
    result = compiler.compile_ruleset_content(content)
    assert result.prompt_profile_snapshot["prompt_version"].startswith("ruleset-v1-")
    for stage, key in STAGE_KEYS.items():
        prompt = result.prompt_profile_snapshot[key]
        assert content.domain in prompt
        assert content.audit_goal in prompt
        categories = sorted(content.categories, key=lambda c: (c.order, c.category_id))
        lines = [f"{c.category_id}｜{c.name}" for c in categories]
        assert [prompt.index(line) for line in lines] == sorted(prompt.index(line) for line in lines)
        assert len(prompt) <= compiler.FIXED_PROMPT_LIMITS[stage]
        assert len(prompt) == result.prompt_profile_snapshot["fixed_prompt_chars"][stage]
        for exemption in content.general_exemptions:
            assert f"{exemption.exemption_id}｜{exemption.condition}" in prompt
        expected_routes = []
        for category in categories:
            for rule in sorted(category.rules, key=lambda r: (r.order, r.rule_id)):
                applicable = rule.enabled and stage in rule.application_stages
                assert (compiler.canonical_rule_line(rule.model_dump()) in prompt) == applicable
                for exemption in rule.rule_exemptions:
                    assert (f"{exemption.exemption_id}｜{exemption.condition}" in prompt) == applicable
                if applicable:
                    expected_routes.append(rule.rule_id)
        assert result.stage_routes[stage] == expected_routes
        if content.domain == "recruitment_fraud":
            assert not any(word in prompt for word in LEAKS)
    assert golden[content.domain]["formal_compile_result"]["rule_snapshot"]["thresholds"] == {
        "high": 80, "medium": 60, "review": 40,
    }
    assert golden[content.domain]["formal_compile_result"]["rule_snapshot"]["scoring_rules"] == []
    historical = json.loads((FIXTURES / "ruleset_compiler_t2_baseline.json").read_text(encoding="utf-8"))["cases"][0]["output"]["prompt_profile_snapshot"]
    for key, (start, end) in {
        "image_prompt": ("只输出合法 JSON，不要输出 Markdown：\n", None),
        "frame_prompt": ("只输出合法 JSON，不要输出 Markdown：\n", None),
        "comment_prompt_template": ("只输出合法 JSON 稀疏合同：\n", "\n必须保留 V1 稀疏语义"),
        "fusion_prompt_template": ("只输出合法 JSON：\n", "\n\n"),
    }.items():
        current_contract = result.prompt_profile_snapshot[key].split(start, 1)[1]
        old_contract = historical[key].split(start, 1)[1]
        if end:
            current_contract, old_contract = current_contract.split(end, 1)[0], old_contract.split(end, 1)[0]
        assert json.loads(current_contract) == json.loads(old_contract)


@pytest.mark.parametrize("field", ["category", "audit_goal", "domain"])
def test_business_context_tracks_content(field):
    content = recruitment_content().model_dump(mode="json")
    before = compiler.compile_ruleset_content(content)
    if field == "category":
        old = content["categories"][0]["category_id"]
        new = "recruitment.employer_impersonation"
        content["categories"][0]["category_id"] = new
        content["categories"][0]["name"] = "Employer impersonation"
    else:
        old = content[field]
        new = "new_business_context"
        content[field] = new
    after = compiler.compile_ruleset_content(content)
    for key in STAGE_KEYS.values():
        assert old in before.prompt_profile_snapshot[key]
        assert old not in after.prompt_profile_snapshot[key]
        assert new in after.prompt_profile_snapshot[key]


def test_non_execution_metadata_stays_out_of_prompts():
    content = recruitment_content().model_dump(mode="json")
    before = compiler.compile_ruleset_content(content)
    for category in content["categories"]:
        category["description"] = "NON_EXEC_DESCRIPTION"
        for rule in category["rules"]:
            rule["name"] = "NON_EXEC_RULE_NAME"
            rule["adjudication_notes"] = "NON_EXEC_NOTES"
            rule["source_mappings"] = [{
                "source_file": "NON_EXEC_SOURCE", "source_locator": "NON_EXEC_LOCATOR",
                "migrated_semantics": "NON_EXEC_SEMANTICS",
            }]
            for exemption in rule["rule_exemptions"]:
                exemption["name"] = "NON_EXEC_EXEMPTION"
    for exemption in content["general_exemptions"]:
        exemption["name"] = "NON_EXEC_GENERAL"
    after = compiler.compile_ruleset_content(content)
    for key in STAGE_KEYS.values():
        assert before.prompt_profile_snapshot[key] == after.prompt_profile_snapshot[key]
        assert "NON_EXEC" not in after.prompt_profile_snapshot[key]


def test_second_domain_service_create_update_publish(tmp_path):
    service = RuleSetService(RuleSetStore(tmp_path / "isolated.sqlite3"))
    principal = SimpleNamespace(id="t2-5-test")
    content = recruitment_content()
    draft = service.create_draft(content, principal=principal, ruleset_id="ruleset.recruitment-test")
    content.audit_goal = "Assess recruitment fee and offer evidence."
    updated = service.update_draft(draft["id"], expected_revision=draft["draft_revision"], content=content, principal=principal)
    published = service.publish(draft["id"], expected_revision=updated["draft_revision"], idempotency_key="publish-test", principal=principal)
    revision_id = published["id"]
    compiled = service.compile_for_execution(revision_id, principal=principal)
    preview = service.compile_preview(revision_id, principal=principal)
    assert preview["config_hash"] == compiled["config_hash"]
    assert compiled["prompt_profile_snapshot"]["audit_goal"] == content.audit_goal


@pytest.mark.parametrize("domain,title", [("hate", "Recruitment"), ("minority", "Recruitment"), ("recruitment_fraud", "民族招聘"), ("recruitment_fraud", "宗教招聘")])
def test_v2_provider_prompts_have_no_heuristic_guidance_and_legacy_retains_it(domain, title):
    from backend.audit_agent.pipeline import AuditPipeline
    from backend.audit_agent.models import AuditSubject
    from backend.audit_agent.prompts import get_prompt_set
    from backend.audit_agent.rule_compiler import compact_library_policy

    content = recruitment_content()
    content.domain, content.name = domain, title
    case = golden_case(content)["formal_compile_result"]
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.rule_snapshot = case["rule_snapshot"]
    pipeline.prompt_profile_snapshot = case["prompt_profile_snapshot"]
    pipeline.audit_policy_snapshot = case["audit_policy_snapshot"]
    pipeline.prompt_set = get_prompt_set(domain, prompt_profile=case["prompt_profile_snapshot"])
    subject = AuditSubject(
        note_id="test", title="Recruitment", desc="Offer", platform="xhs",
        url="", author={}, image_urls=[], video_urls=[], comments=[],
    )
    prompt = pipeline._render_comment_audit_prompt(subject, "", [])
    fixed_prompt = case["prompt_profile_snapshot"]["comment_prompt_template"]
    for code, rule_id in pipeline._comment_rule_code_mapping().items():
        fixed_prompt = fixed_prompt.replace(rule_id, code)
    fixed_prompt = fixed_prompt.replace("stable rule_id", "本次短编号，例如CR01")
    assert prompt.startswith(fixed_prompt)
    assert "comment_extra" not in prompt
    assert "判中风险" not in prompt
    assert not any(word in prompt for word in LEAKS)
    frame = pipeline._render_review_sheet_prompt(
        video_index=0, segment={"segment_id": "s1", "start": 0, "end": 1},
        frames=[], title=subject.title, desc=subject.desc,
        library_policy=pipeline._active_library_policies(["ocr", "asr", "vision"])[0],
    )
    fusion = pipeline._render_compact_fusion_prompt(subject, {"evidence_catalog": []}, [])
    assert frame.startswith(case["prompt_profile_snapshot"]["frame_prompt"])
    assert fusion.startswith(case["prompt_profile_snapshot"]["fusion_prompt_template"])
    assert pipeline.prompt_set.image_prompt == case["prompt_profile_snapshot"]["image_prompt"]
    for rendered in (frame, fusion, pipeline.prompt_set.image_prompt):
        assert not any(word in rendered for word in LEAKS)
        assert "comment_extra" not in rendered
    policy = compact_library_policy({"id": domain, "title": title}, modalities=["comment"])
    assert len(policy["modality_guidance"]["comment_extra"]) == 3


def test_generic_compiler_has_no_business_hardcode_and_content_hash_stays_t2():
    source = Path(compiler.__file__).read_text(encoding="utf-8")
    assert not any(word in source for word in LEAKS)
    baseline = json.loads((FIXTURES / "ruleset_compiler_t2_baseline.json").read_text(encoding="utf-8"))
    for case in baseline["cases"]:
        if "output" not in case:
            continue
        content = case["revision"]["snapshot"]
        assert compiler.content_hash(content) == case["revision"]["content_hash"]
        compiled = compiler.compile_ruleset_revision(case["revision"], audit_policy=case["audit_policy"])
        assert compiled["config_hash"] != case["output"]["config_hash"]
        assert compiled["rule_snapshot"] == case["output"]["rule_snapshot"]
