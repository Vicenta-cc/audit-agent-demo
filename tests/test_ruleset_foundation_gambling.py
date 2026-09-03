from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier, Event, Thread
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.audit_agent.audit_policy_store import (
    AuditPolicyLibraryReferenceConflictError,
    AuditPolicyRevisionConflictError,
    AuditPolicyStore,
    TaskAuditConfigRevisionStore,
)
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.ingestion import AuditResultStore
from backend.audit_agent.lexicon_store import (
    LexiconCategoryReferenceConflictError,
    LexiconStore,
)
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.pipeline import AuditPipeline, FusionAuditContractError
from backend.audit_agent.prompts import get_prompt_set
from backend.investigation_creation.adapters import InvestigationConfigurationResolver
from backend.investigation_creation.contracts import InvestigationConfiguration
from backend.investigation_creation.errors import ConfigurationValidationError
from backend.investigation_creation.principal import LocalPrincipalProvider, Principal
from backend.rulesets.api import create_ruleset_router
from backend.rulesets.compiler import (
    COMPILER_VERSION,
    FIXED_PROMPT_LIMITS,
    SYSTEM_TEMPLATE_VERSION,
    canonical_rule_line,
    canonical_json,
    compile_ruleset_revision,
    content_hash,
)
from backend.rulesets.errors import (
    RuleSetForbiddenError,
    RuleSetNotFoundError,
    RuleSetRevisionConflictError,
    RuleSetRevisionNotFoundError,
    RuleSetValidationError,
)
from backend.rulesets.contracts import RuleSetContent
from backend.rulesets.gambling_v1 import (
    GAMBLING_RULESET_ID,
    GAMBLING_RULESET_REVISION_ID,
    GAMBLING_RULESET_V1_REVISION_ID,
    gambling_ruleset_v1,
    gambling_ruleset_v2,
)
from backend.rulesets.service import RuleSetService
from backend.rulesets.store import RuleSetStore

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gambling_ruleset_v1_golden.json"
PRINCIPAL = Principal("test-local-user")
OTHER_PRINCIPAL = Principal("other-local-user")


def audit_policy_snapshot(**overrides) -> dict:
    value = {
        "policy_id": "policy_gambling",
        "policy_name": "赌博博彩研判方案",
        "policy_version": "v1.0",
        "ruleset_revision_id": GAMBLING_RULESET_REVISION_ID,
        "policy_config": {
            "ruleset_revision_id": GAMBLING_RULESET_REVISION_ID,
            "outputs": ["风险内容", "询证材料"],
        },
        "library_ids": ["gambling"],
        "capabilities": ["text", "ocr", "asr", "vision", "comment"],
        "scoring_template": "balanced",
        "thresholds": {"high": 80, "medium": 60, "review": 40},
    }
    value.update(overrides)
    return value


def operator_ruleset_content(**updates) -> RuleSetContent:
    content = gambling_ruleset_v2().model_dump(mode="json")
    content.update(updates)
    for exemption in content["general_exemptions"]:
        exemption["source_mappings"] = []
    for category in content["categories"]:
        for rule in category["rules"]:
            rule["source_mappings"] = []
            for exemption in rule["rule_exemptions"]:
                exemption["source_mappings"] = []
    return RuleSetContent.model_validate(content)


def bare_v2_pipeline(compiled: dict) -> AuditPipeline:
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = "ruleset-test-job"
    pipeline.rule_snapshot = deepcopy(compiled["rule_snapshot"])
    pipeline.prompt_profile_snapshot = deepcopy(compiled["prompt_profile_snapshot"])
    pipeline.prompt_set = get_prompt_set(
        "gambling", prompt_profile=pipeline.prompt_profile_snapshot
    )
    return pipeline


class FixedProvider:
    def __init__(self, response: dict) -> None:
        self.response = deepcopy(response)
        self.calls = 0

    def audit_text(self, _prompt: str, **_kwargs) -> dict:
        self.calls += 1
        return deepcopy(self.response)


class SequenceProvider:
    enabled = True

    def __init__(self, responses: list[dict]) -> None:
        self.responses = [deepcopy(item) for item in responses]
        self.calls = 0
        self.prompts: list[str] = []

    def audit_text(self, prompt: str, **_kwargs) -> dict:
        self.prompts.append(prompt)
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return deepcopy(self.responses[index])


class RuleSetCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = RuleSetStore(Path(self.temp_dir.name) / "rulesets.sqlite3")
        self.service = RuleSetService(self.store)
        self.revision = self.service.get_published(
            GAMBLING_RULESET_REVISION_ID,
            principal=PRINCIPAL,
        )

    def compile(self, **policy_overrides) -> dict:
        return self.service.compile_for_execution(
            GAMBLING_RULESET_REVISION_ID,
            audit_policy=audit_policy_snapshot(**policy_overrides),
            principal=PRINCIPAL,
        )

    def test_same_inputs_compile_to_identical_snapshots_and_hash(self) -> None:
        first = self.compile()
        second = self.compile()

        self.assertEqual(first, second)
        self.assertEqual(len(first["config_hash"]), 64)
        self.assertEqual(first["schema_version"], 2)
        self.assertEqual(
            first["rule_snapshot"]["ruleset_ref"]["revision_id"],
            GAMBLING_RULESET_REVISION_ID,
        )
        self.assertEqual(
            first["prompt_profile_snapshot"]["system_template_version"],
            SYSTEM_TEMPLATE_VERSION,
        )
        self.assertEqual(
            first["prompt_profile_snapshot"]["compiler_version"],
            COMPILER_VERSION,
        )

        changed = self.compile(
            policy_config={
                "ruleset_revision_id": GAMBLING_RULESET_REVISION_ID,
                "outputs": ["风险内容"],
            }
        )
        self.assertNotEqual(first["config_hash"], changed["config_hash"])

    def test_public_preview_returns_only_safe_summary(self) -> None:
        preview = self.service.compile_preview(
            GAMBLING_RULESET_REVISION_ID,
            audit_policy=audit_policy_snapshot(),
            principal=PRINCIPAL,
        )

        self.assertEqual(preview["diagnostics"], {"valid": True, "errors": []})
        self.assertEqual(
            preview["ruleset_ref"]["revision_id"],
            GAMBLING_RULESET_REVISION_ID,
        )
        self.assertNotIn("prompt_profile_snapshot", preview)
        self.assertNotIn("rule_snapshot", preview)
        self.assertNotIn("image_prompt", canonical_json(preview))

    def test_disabled_rules_are_removed_and_stage_routing_is_exact(self) -> None:
        content = gambling_ruleset_v2().model_dump(mode="json")
        disabled_id = "gambling.platform_entry_and_funding"
        comment_only_id = "gambling.betting_and_settlement"
        for category in content["categories"]:
            for rule in category["rules"]:
                if rule["rule_id"] == disabled_id:
                    rule["enabled"] = False
                if rule["rule_id"] == comment_only_id:
                    rule["application_stages"] = ["comment_audit"]
        modified_revision = {
            "id": "ruleset-revision:test:v1",
            "ruleset_id": "ruleset.test",
            "version": 1,
            "status": "published",
            "content_hash": content_hash(content),
            "snapshot": content,
        }

        compiled = compile_ruleset_revision(
            modified_revision,
            audit_policy=audit_policy_snapshot(
                ruleset_revision_id=modified_revision["id"]
            ),
        )
        serialized = canonical_json(compiled)
        prompts = compiled["prompt_profile_snapshot"]

        self.assertNotIn(disabled_id, serialized)
        self.assertIn(comment_only_id, prompts["comment_prompt_template"])
        self.assertNotIn(comment_only_id, prompts["image_prompt"])
        self.assertNotIn(comment_only_id, prompts["frame_prompt"])
        self.assertNotIn(comment_only_id, prompts["fusion_prompt_template"])
        self.assertEqual(
            compiled["rule_snapshot"]["stage_routes"]["comment_audit"].count(
                comment_only_id
            ),
            1,
        )

    def test_canonical_rules_are_identical_across_selected_stages(self) -> None:
        compiled = self.compile()
        snapshot = compiled["rule_snapshot"]
        prompts = compiled["prompt_profile_snapshot"]
        prompt_keys = {
            "image_evidence": "image_prompt",
            "video_frame_evidence": "frame_prompt",
            "comment_audit": "comment_prompt_template",
            "fusion_audit": "fusion_prompt_template",
        }
        rules = {
            rule["rule_id"]: rule for rule in snapshot["decision_rules"]
        }
        self.assertEqual(
            {
                rule_id: rule["suggested_risk_level"]
                for rule_id, rule in rules.items()
            },
            {
                "gambling.platform_entry_and_funding": "high",
                "gambling.betting_and_settlement": "high",
                "gambling.agent_guaranteed_win_promotion": "high",
                "gambling.off_platform_diversion": "medium",
                "gambling.comment_organized_participation": "medium",
                "gambling.weak_keyword_context": "low",
                "gambling.multimodal_closed_loop": "high",
            },
        )

        for stage, rule_ids in snapshot["stage_routes"].items():
            prompt = prompts[prompt_keys[stage]]
            for rule_id in rule_ids:
                line = canonical_rule_line(rules[rule_id])
                self.assertEqual(prompt.count(line), 1, (stage, rule_id))

        shared_rule = rules["gambling.betting_and_settlement"]
        shared_line = canonical_rule_line(shared_rule)
        self.assertEqual(
            shared_line,
            "gambling.betting_and_settlement｜参考 high｜"
            "投注、盘口、赔率、下注、上分、提现、结算、刷流水、赌资或资金盘等明确参与或资金操作。",
        )
        for prompt_key in prompt_keys.values():
            self.assertIn(shared_line, prompts[prompt_key])

    def test_prompt_exemptions_are_compact_and_not_emitted_when_empty(self) -> None:
        compiled = self.compile()
        prompts = compiled["prompt_profile_snapshot"]
        general = self.revision["general_exemptions"]
        rules = [
            rule
            for category in self.revision["categories"]
            for rule in category["rules"]
            if rule["enabled"]
        ]
        for prompt_key in (
            "image_prompt",
            "frame_prompt",
            "comment_prompt_template",
            "fusion_prompt_template",
        ):
            prompt = prompts[prompt_key]
            self.assertEqual(prompt.count("通用豁免只判断一次："), 1)
            for exemption in general:
                self.assertEqual(
                    prompt.count(
                        f'{exemption["exemption_id"]}｜{exemption["condition"]}'
                    ),
                    1,
                )
            self.assertNotIn("application_stages", prompt)
            self.assertNotIn("source_mappings", prompt)
            self.assertNotIn('"order"', prompt)
            self.assertNotIn('"enabled"', prompt)
            for rule in rules:
                if rule["rule_id"] not in prompt:
                    continue
                for exemption in rule["rule_exemptions"]:
                    self.assertIn(
                        f'{exemption["exemption_id"]}｜{exemption["condition"]}',
                        prompt,
                    )

        content = gambling_ruleset_v2().model_dump(mode="json")
        content["general_exemptions"] = []
        for category in content["categories"]:
            for rule in category["rules"]:
                rule["rule_exemptions"] = []
        revision = {
            "id": "ruleset-revision:no-exemptions:v1",
            "ruleset_id": "ruleset.no-exemptions",
            "version": 1,
            "status": "published",
            "content_hash": content_hash(content),
            "snapshot": content,
        }
        without_exemptions = compile_ruleset_revision(
            revision,
            audit_policy=audit_policy_snapshot(
                ruleset_revision_id=revision["id"]
            ),
        )["prompt_profile_snapshot"]
        for prompt_key in (
            "image_prompt",
            "frame_prompt",
            "comment_prompt_template",
            "fusion_prompt_template",
        ):
            self.assertNotIn("通用豁免", without_exemptions[prompt_key])
            self.assertNotIn("规则级豁免", without_exemptions[prompt_key])

    def test_exemptions_and_source_mapping_are_preserved(self) -> None:
        compiled = self.compile()
        rules = compiled["rule_snapshot"]["decision_rules"]
        all_sources = {
            mapping["source_file"]
            for rule in rules
            for mapping in rule["source_mappings"]
        }

        self.assertEqual(
            compiled["rule_snapshot"]["general_exemptions"],
            self.revision["general_exemptions"],
        )
        self.assertTrue(any(rule["rule_exemptions"] for rule in rules))
        self.assertNotIn("backend/audit_agent/lexicon_store.py", all_sources)
        self.assertTrue(
            {
                "backend/audit_agent/prompts.py",
                "backend/audit_agent/knowledge_packages.py",
                "backend/audit_agent/rule_compiler.py",
                "backend/audit_agent/pipeline.py",
            }.issubset(all_sources)
        )

    def test_compiler_rejects_draft_and_tampered_revision(self) -> None:
        draft = {**self.revision, "status": "draft"}
        with self.assertRaisesRegex(RuleSetValidationError, "published"):
            compile_ruleset_revision(draft, audit_policy=audit_policy_snapshot())

        tampered = deepcopy(self.revision)
        tampered["audit_goal"] += " tampered"
        with self.assertRaisesRegex(RuleSetValidationError, "content_hash"):
            compile_ruleset_revision(tampered, audit_policy=audit_policy_snapshot())

        mismatched_policy = audit_policy_snapshot(
            ruleset_revision_id="ruleset-revision:other:v1"
        )
        with self.assertRaisesRegex(RuleSetValidationError, "does not match"):
            compile_ruleset_revision(
                self.revision,
                audit_policy=mismatched_policy,
            )

        for invalid_hash in ("", "abc", "g" * 64):
            invalid = {**self.revision, "content_hash": invalid_hash}
            with self.subTest(content_hash=invalid_hash), self.assertRaisesRegex(
                RuleSetValidationError, "SHA-256"
            ):
                compile_ruleset_revision(
                    invalid,
                    audit_policy=audit_policy_snapshot(),
                )

    def test_schema_is_strict_and_exemption_ids_are_unique_per_rule(self) -> None:
        content = gambling_ruleset_v2().model_dump(mode="json")
        content["categories"][0]["rules"][0]["enabled"] = 1
        with self.assertRaises(ValidationError):
            RuleSetContent.model_validate(content)

        content = gambling_ruleset_v2().model_dump(mode="json")
        rule = content["categories"][0]["rules"][0]
        rule["rule_exemptions"] = [
            deepcopy(rule["rule_exemptions"][0]),
            deepcopy(rule["rule_exemptions"][0]),
        ]
        with self.assertRaisesRegex(ValidationError, "exemption ids"):
            RuleSetContent.model_validate(content)

    def test_compiled_prompts_are_compact_and_exclude_migration_metadata(self) -> None:
        compiled = self.compile()
        profile = compiled["prompt_profile_snapshot"]
        fixed = profile["fixed_prompt_chars"]

        self.assertLess(fixed["image_evidence"], 2500)
        self.assertLess(fixed["video_frame_evidence"], 3000)
        self.assertLess(fixed["comment_audit"], 2600)
        self.assertLess(fixed["fusion_audit"], 3200)
        for key in (
            "image_prompt",
            "frame_prompt",
            "comment_prompt_template",
            "fusion_prompt_template",
        ):
            prompt = profile[key]
            self.assertNotIn("source_mappings", prompt)
            self.assertNotIn('"order"', prompt)
            self.assertNotIn('"enabled"', prompt)
        self.assertEqual(compiled["rule_snapshot"]["scoring_rules"], [])

    def test_provider_contract_prompts_preserve_v1_fields_and_only_add_v2_fields(self) -> None:
        profile = self.compile()["prompt_profile_snapshot"]
        image = profile["image_prompt"]
        frame = profile["frame_prompt"]
        comment = profile["comment_prompt_template"]
        fusion = profile["fusion_prompt_template"]
        image_contract = json.loads(
            image.split("只输出合法 JSON，不要输出 Markdown：\n", 1)[1]
        )
        frame_contract = json.loads(
            frame.split("只输出合法 JSON，不要输出 Markdown：\n", 1)[1]
        )
        comment_contract = json.loads(
            comment.split("只输出合法 JSON 稀疏合同：\n", 1)[1].split(
                "\n必须保留 V1 稀疏语义", 1
            )[0]
        )
        fusion_contract = json.loads(
            fusion.split("只输出合法 JSON：\n", 1)[1].split("\n\n", 1)[0]
        )

        self.assertEqual(
            set(image_contract),
            {"ocr_text", "visual_summary", "benign_context", "risk_items"},
        )
        self.assertIn("visual_summary 必须始终存在", image)
        self.assertIn("必须是 JSON 字符串", image)
        self.assertIn("不得返回 null、数组或对象", image)
        self.assertIn("即使未发现风险", image)
        self.assertEqual(
            set(image_contract["risk_items"][0]),
            {
                "risk_type",
                "evidence",
                "reason",
                "severity",
                "rule_id",
                "matched_exemption_ids",
            },
        )
        self.assertEqual(
            set(frame_contract),
            {
                "segment_summary",
                "segment_score",
                "risk_library_id",
                "risk_library_label",
                "visual_risks",
                "ocr_risks",
                "asr_risks",
            },
        )
        self.assertEqual(
            set(frame_contract["visual_risks"][0]),
            {
                "frame_ids",
                "score",
                "risk_type",
                "reason",
                "rule_id",
                "risk_level",
                "matched_exemption_ids",
            },
        )
        self.assertEqual(
            set(frame_contract["ocr_risks"][0]),
            {
                "ocr_chunk_id",
                "frame_ids",
                "score",
                "risk_type",
                "reason",
                "rule_id",
                "risk_level",
                "matched_exemption_ids",
            },
        )
        self.assertEqual(
            set(frame_contract["asr_risks"][0]),
            {
                "asr_chunk_id",
                "score",
                "risk_type",
                "reason",
                "rule_id",
                "risk_level",
                "matched_exemption_ids",
            },
        )
        self.assertEqual(
            set(comment_contract),
            {"comments"},
        )
        self.assertEqual(
            set(comment_contract["comments"][0]),
            {"id", "s", "risk_level", "zh"},
        )
        self.assertEqual(
            set(comment_contract["comments"][1]),
            {
                "id",
                "s",
                "lib",
                "sec",
                "t",
                "rb",
                "eb",
                "q",
                "risk_level",
                "rule_id",
                "matched_exemption_ids",
                "zh",
            },
        )
        self.assertEqual(
            set(fusion_contract),
            {
                "schema_version",
                "content_title",
                "summary",
                "decision_suggestion",
                "risk_level_suggestion",
                "primary_risk",
                "categories",
                "evidence_items",
                "rule_matches",
            },
        )
        self.assertEqual(
            set(fusion_contract["evidence_items"][0]),
            {"evidence_id", "evidence_risk_level", "reason"},
        )
        self.assertEqual(
            set(fusion_contract["rule_matches"][0]),
            {"rule_id", "evidence_ids", "matched_exemption_ids"},
        )

        for field in (
            "ocr_text",
            "visual_summary",
            "benign_context",
            "risk_items",
            "risk_type",
            "evidence",
            "reason",
            "severity",
            "rule_id",
            "matched_exemption_ids",
        ):
            self.assertIn(f'"{field}"', image)
        for forbidden in (
            "risk_library_id",
            "risk_library_label",
            "evidence_id",
            "risk_level",
        ):
            self.assertNotIn(f'"{forbidden}"', image)

        for field in (
            "segment_summary",
            "segment_score",
            "risk_library_id",
            "risk_library_label",
            "visual_risks",
            "ocr_risks",
            "asr_risks",
            "frame_ids",
            "ocr_chunk_id",
            "asr_chunk_id",
            "score",
            "risk_type",
            "reason",
            "rule_id",
            "risk_level",
            "matched_exemption_ids",
        ):
            self.assertIn(f'"{field}"', frame)

        for field in (
            "comments",
            "id",
            "s",
            "lib",
            "sec",
            "t",
            "rb",
            "eb",
            "q",
            "zh",
            "risk_level",
            "rule_id",
            "matched_exemption_ids",
        ):
            self.assertIn(f'"{field}"', comment)
        self.assertIn("s=0 时只输出 id、s、risk_level", comment)
        self.assertIn("sec、eb、matched_exemption_ids 仅在实际存在时输出", comment)
        self.assertIn("不得直接归责主帖作者", comment)
        self.assertNotIn("证据主导", comment)
        self.assertNotIn("review", comment)

        for field in (
            "schema_version",
            "content_title",
            "summary",
            "decision_suggestion",
            "risk_level_suggestion",
            "primary_risk",
            "categories",
            "evidence_items",
            "rule_matches",
            "evidence_id",
            "evidence_risk_level",
            "reason",
            "rule_id",
            "evidence_ids",
            "matched_exemption_ids",
        ):
            self.assertIn(f'"{field}"', fusion)
        for pipeline_field in (
            "primary_modality",
            "supporting_modalities",
            "source",
            "source_label",
            "text",
            "features",
            "confidence",
        ):
            self.assertNotIn(f'"{pipeline_field}"', fusion)
        self.assertIn("其证据主导时仅建议 review", fusion)
        for obsolete in ("作者回复", "置顶", "点赞迎合"):
            self.assertNotIn(obsolete, image + frame + comment + fusion)
        for unrelated_domain in ("民族", "仇恨"):
            self.assertNotIn(unrelated_domain, image + frame + comment + fusion)

    def test_compile_rejects_fixed_rules_that_exceed_stage_budget(self) -> None:
        content = gambling_ruleset_v2().model_dump(mode="json")
        for category in content["categories"]:
            for rule in category["rules"]:
                rule["hit_condition"] = "命" * 4_000
                rule["adjudication_notes"] = "裁" * 4_000
                rule["application_stages"] = [
                    "image_evidence",
                    "video_frame_evidence",
                    "comment_audit",
                    "fusion_audit",
                ]
        revision = {
            "id": "ruleset-revision:oversize:v1",
            "ruleset_id": "ruleset.oversize",
            "version": 1,
            "status": "published",
            "content_hash": content_hash(content),
            "snapshot": content,
        }

        with self.assertRaisesRegex(
            RuleSetValidationError,
            "compiled fixed prompt for image_evidence exceeds",
        ):
            compile_ruleset_revision(
                revision,
                audit_policy=audit_policy_snapshot(
                    ruleset_revision_id=revision["id"]
                ),
            )

    def test_literal_input_placeholder_in_rule_text_is_not_replaced(self) -> None:
        content = gambling_ruleset_v2().model_dump(mode="json")
        content["categories"][0]["rules"][0]["hit_condition"] += " literal {{input_json}} marker"
        revision = {
            "id": "ruleset-revision:placeholder:v1",
            "ruleset_id": "ruleset.placeholder",
            "version": 1,
            "status": "published",
            "content_hash": content_hash(content),
            "snapshot": content,
        }
        compiled = compile_ruleset_revision(
            revision,
            audit_policy=audit_policy_snapshot(
                ruleset_revision_id=revision["id"]
            ),
        )
        pipeline = bare_v2_pipeline(compiled)
        subject = AuditSubject(
            platform="xhs",
            note_id="placeholder-note",
            url="",
            title="title",
            desc="desc",
            author={},
            image_urls=[],
            video_urls=[],
            comments=[],
        )
        prompt = pipeline._render_comment_audit_prompt(subject, "media", [])

        self.assertEqual(prompt.count("{{input_json}}"), 1)
        self.assertEqual(prompt.count("输入 JSON："), 1)
        self.assertEqual(prompt.count('"post_context"'), 1)

    def test_seed_source_locators_resolve_to_stable_legacy_anchors(self) -> None:
        root = Path(__file__).parents[1]
        mappings = [
            mapping
            for exemption in self.revision["general_exemptions"]
            for mapping in exemption["source_mappings"]
        ]
        mappings.extend(
            mapping
            for category in self.revision["categories"]
            for rule in category["rules"]
            for mapping in (
                rule["source_mappings"]
                + [
                    nested
                    for exemption in rule["rule_exemptions"]
                    for nested in exemption["source_mappings"]
                ]
            )
        )

        for mapping in mappings:
            source = root / mapping["source_file"]
            self.assertTrue(source.is_file(), mapping)
            locator = mapping["source_locator"].split("#", 1)[0]
            self.assertIn(locator, source.read_text(encoding="utf-8"), mapping)


class RuleSetStoreAndApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "rulesets.sqlite3"
        self.store = RuleSetStore(self.db_path)
        self.service = RuleSetService(self.store)

    def test_corrected_gambling_revision_is_new_and_v1_stays_immutable(self) -> None:
        legacy = self.service.get_published(
            GAMBLING_RULESET_V1_REVISION_ID,
            principal=PRINCIPAL,
        )
        corrected = self.service.get_published(
            GAMBLING_RULESET_REVISION_ID,
            principal=PRINCIPAL,
        )
        aggregate = self.service.get(GAMBLING_RULESET_ID, principal=PRINCIPAL)

        def comment_rule(revision: dict) -> dict:
            return next(
                rule
                for category in revision["categories"]
                for rule in category["rules"]
                if rule["rule_id"] == "gambling.comment_organized_participation"
            )

        legacy_rule = comment_rule(legacy)
        corrected_rule = comment_rule(corrected)
        self.assertIn("作者回复、置顶、点赞迎合", legacy_rule["hit_condition"])
        self.assertEqual(
            corrected_rule["hit_condition"],
            "评论本身组织或邀请他人下注、加群、私聊、充值、上分或提现。",
        )
        self.assertNotIn(
            "作者回复", canonical_json(corrected)
        )
        self.assertNotIn("置顶", canonical_json(corrected))
        self.assertNotIn("点赞迎合", canonical_json(corrected))
        self.assertNotEqual(legacy["content_hash"], corrected["content_hash"])
        self.assertEqual(
            aggregate["published_revision_id"],
            GAMBLING_RULESET_REVISION_ID,
        )
        self.assertEqual(aggregate["draft_revision"], 2)
        self.assertEqual(
            [item["id"] for item in self.store.list_current_published()],
            [GAMBLING_RULESET_REVISION_ID],
        )
        self.assertEqual(
            [
                item["id"]
                for item in self.store.list_published(
                    ruleset_id=GAMBLING_RULESET_ID
                )
            ],
            [GAMBLING_RULESET_REVISION_ID, GAMBLING_RULESET_V1_REVISION_ID],
        )

    def test_optimistic_update_publish_idempotency_and_immutability(self) -> None:
        content = operator_ruleset_content(name="博彩测试规则集")
        aggregate = self.service.create_draft(
            content,
            principal=PRINCIPAL,
            ruleset_id="ruleset.gambling.test",
        )
        updated_content = content.model_copy(update={"audit_goal": "更新后的审核目标"})
        updated = self.service.update_draft(
            aggregate["id"],
            expected_revision=1,
            content=updated_content,
            principal=PRINCIPAL,
        )
        with self.assertRaises(RuleSetRevisionConflictError):
            self.service.update_draft(
                aggregate["id"],
                expected_revision=1,
                content=content,
                principal=PRINCIPAL,
            )

        first = self.service.publish(
            aggregate["id"],
            expected_revision=updated["draft_revision"],
            idempotency_key="publish-test-1",
            principal=PRINCIPAL,
        )
        replay = self.service.publish(
            aggregate["id"],
            expected_revision=updated["draft_revision"],
            idempotency_key="publish-test-1",
            principal=PRINCIPAL,
        )
        same_draft_new_key = self.service.publish(
            aggregate["id"],
            expected_revision=updated["draft_revision"],
            idempotency_key="publish-test-2",
            principal=PRINCIPAL,
        )

        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(first["id"], same_draft_new_key["id"])
        self.assertEqual(len(self.store.list_published(ruleset_id=aggregate["id"])), 1)
        with (
            sqlite3.connect(self.db_path) as connection,
            self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"),
        ):
            connection.execute(
                "UPDATE rule_set_revisions SET content_hash = 'changed' WHERE id = ?",
                (first["id"],),
            )

    def test_concurrent_publish_of_one_draft_creates_one_revision(self) -> None:
        aggregate = self.service.create_draft(
            operator_ruleset_content(name="并发发布规则集"),
            principal=PRINCIPAL,
            ruleset_id="ruleset.gambling.concurrent",
        )
        barrier = Barrier(2)

        def publish(key: str) -> str:
            service = RuleSetService(RuleSetStore(self.db_path))
            barrier.wait()
            return service.publish(
                aggregate["id"],
                expected_revision=aggregate["draft_revision"],
                idempotency_key=key,
                principal=PRINCIPAL,
            )["id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            revision_ids = list(pool.map(publish, ("concurrent-1", "concurrent-2")))

        self.assertEqual(len(set(revision_ids)), 1)
        self.assertEqual(len(self.store.list_published(ruleset_id=aggregate["id"])), 1)

    def test_api_uses_server_principal_and_rejects_actor_fields(self) -> None:
        app = FastAPI()
        app.include_router(
            create_ruleset_router(
                self.service,
                principal_provider=LocalPrincipalProvider("server-principal"),
            )
        )
        content = operator_ruleset_content().model_dump(mode="json")
        with TestClient(app) as client:
            rejected = client.post(
                "/api/rulesets",
                json={
                    "id": "ruleset.gambling.spoofed",
                    "content": content,
                    "created_by": "client-spoof",
                },
            )
            created = client.post(
                "/api/rulesets",
                json={"id": "ruleset.gambling.api", "content": content},
            )
            published = client.post(
                "/api/rulesets/ruleset.gambling.api/publish",
                headers={"Idempotency-Key": "api-publish"},
                json={"expected_revision": 1},
            )

        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["created_by"], "server-principal")
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.json()["published_by"], "server-principal")

    def test_owner_isolation_system_seed_read_only_and_fork(self) -> None:
        owned = self.service.create_draft(
            operator_ruleset_content(name="owner draft"),
            principal=PRINCIPAL,
            ruleset_id="ruleset.owner-only",
        )

        with self.assertRaises(RuleSetNotFoundError):
            self.service.get(owned["id"], principal=OTHER_PRINCIPAL)
        self.assertNotIn(
            owned["id"],
            {item["id"] for item in self.service.list(principal=OTHER_PRINCIPAL)},
        )
        with self.assertRaises(RuleSetForbiddenError):
            self.service.update_draft(
                owned["id"],
                expected_revision=1,
                content=operator_ruleset_content(name="cross-owner update"),
                principal=OTHER_PRINCIPAL,
            )
        with self.assertRaises(RuleSetForbiddenError):
            self.service.publish(
                owned["id"],
                expected_revision=1,
                idempotency_key="cross-owner-publish",
                principal=OTHER_PRINCIPAL,
            )

        shared_revision = self.service.publish(
            owned["id"],
            expected_revision=1,
            idempotency_key="owner-publish",
            principal=PRINCIPAL,
        )
        self.assertEqual(
            self.service.get_published(
                shared_revision["id"],
                principal=OTHER_PRINCIPAL,
            )["id"],
            shared_revision["id"],
        )

        system = self.service.get(GAMBLING_RULESET_ID, principal=PRINCIPAL)
        self.assertEqual(system["owner_id"], "system")
        with self.assertRaises(RuleSetForbiddenError):
            self.service.update_draft(
                system["id"],
                expected_revision=system["draft_revision"],
                content=operator_ruleset_content(name="illegal system edit"),
                principal=PRINCIPAL,
            )
        with self.assertRaises(RuleSetForbiddenError):
            self.service.publish(
                system["id"],
                expected_revision=system["draft_revision"],
                idempotency_key="illegal-system-publish",
                principal=PRINCIPAL,
            )

        forked = self.service.fork_published(
            GAMBLING_RULESET_REVISION_ID,
            principal=PRINCIPAL,
            ruleset_id="ruleset.gambling.user-fork",
        )
        self.assertEqual(forked["owner_id"], PRINCIPAL.id)
        self.assertEqual(forked["status"], "draft")
        self.assertFalse(
            any(
                rule["source_mappings"]
                for category in forked["categories"]
                for rule in category["rules"]
            )
        )

    def test_operator_cannot_forge_seed_source_mappings(self) -> None:
        with self.assertRaisesRegex(RuleSetValidationError, "legacy source"):
            self.service.create_draft(
                gambling_ruleset_v2(),
                principal=PRINCIPAL,
                ruleset_id="ruleset.forged-sources",
            )

    def test_publish_rejects_oversized_fixed_prompt_without_creating_revision(self) -> None:
        content = operator_ruleset_content().model_dump(mode="json")
        for category in content["categories"]:
            for rule in category["rules"]:
                rule["hit_condition"] = "命" * 4_000
                rule["adjudication_notes"] = "裁" * 4_000
                rule["application_stages"] = [
                    "image_evidence",
                    "video_frame_evidence",
                    "comment_audit",
                    "fusion_audit",
                ]
        draft = self.service.create_draft(
            RuleSetContent.model_validate(content),
            principal=PRINCIPAL,
            ruleset_id="ruleset.oversized-publish",
        )

        with self.assertRaisesRegex(RuleSetValidationError, "fixed prompt"):
            self.service.publish(
                draft["id"],
                expected_revision=draft["draft_revision"],
                idempotency_key="oversized-publish",
                principal=PRINCIPAL,
            )

        self.assertEqual(
            self.store.list_published(ruleset_id=draft["id"]),
            [],
        )

    def test_published_revision_stays_frozen_after_new_draft_update(self) -> None:
        draft = self.service.create_draft(
            operator_ruleset_content(name="frozen revision"),
            principal=PRINCIPAL,
            ruleset_id="ruleset.frozen-revision",
        )
        revision = self.service.publish(
            draft["id"],
            expected_revision=1,
            idempotency_key="frozen-revision-publish",
            principal=PRINCIPAL,
        )
        updated = self.service.update_draft(
            draft["id"],
            expected_revision=1,
            content=operator_ruleset_content(name="changed draft"),
            principal=PRINCIPAL,
        )
        frozen = self.service.get_published(revision["id"], principal=PRINCIPAL)

        self.assertEqual(updated["name"], "changed draft")
        self.assertEqual(frozen["name"], "frozen revision")
        self.assertEqual(frozen["content_hash"], revision["content_hash"])


class AuditPolicyVersioningTests(unittest.TestCase):
    def test_existing_v1_published_policy_is_not_silently_upgraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "policy.sqlite3"
            AuditPolicyStore(db_path)
            legacy = {
                "library_ids": ["gambling"],
                "capabilities": ["text", "comment"],
                "scoring_template": "balanced",
            }
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    UPDATE audit_policies
                    SET published_version = 'v1.0', draft_version = 'v1.0',
                        config_json = ?, published_config_json = ?
                    WHERE id = 'policy_gambling'
                    """,
                    (json.dumps(legacy), json.dumps(legacy)),
                )

            reopened = AuditPolicyStore(db_path).get("policy_gambling")

        self.assertEqual(reopened["published_version"], "v1.0")
        self.assertEqual(reopened["published_config"], legacy)
        self.assertNotIn("ruleset_revision_id", reopened["published_config"])

    def test_publish_uses_validated_draft_hash_and_rejects_toctou(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AuditPolicyStore(Path(temp_dir) / "policy.sqlite3")
            policy = store.get("policy_gambling")
            expected_hash = store.draft_hash(policy)
            old_published = deepcopy(policy["published_config"])
            store.update(
                "policy_gambling",
                config={**policy["config"], "outputs": ["changed after validation"]},
            )

            with self.assertRaises(AuditPolicyRevisionConflictError):
                store.publish(
                    "policy_gambling",
                    expected_draft_hash=expected_hash,
                )
            unchanged = store.get("policy_gambling")
            self.assertEqual(unchanged["published_config"], old_published)

            published = store.publish(
                "policy_gambling",
                expected_draft_hash=store.draft_hash(unchanged),
            )

        self.assertEqual(published["published_version"], "v2.1")
        self.assertEqual(
            published["published_config"]["outputs"],
            ["changed after validation"],
        )

    def test_policy_draft_writes_reject_missing_library_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            LexiconStore(db_path)
            store = AuditPolicyStore(db_path)
            before = deepcopy(store.list())

            with self.assertRaises(AuditPolicyLibraryReferenceConflictError):
                store.create(
                    id="missing-library-policy",
                    name="Missing library",
                    config={"library_ids": ["missing-library"]},
                )
            self.assertIsNone(store.get("missing-library-policy"))

            policy = store.get("policy_gambling")
            with self.assertRaises(AuditPolicyLibraryReferenceConflictError):
                store.update(
                    "policy_gambling",
                    config={
                        **policy["config"],
                        "library_ids": ["missing-library"],
                    },
                )
            self.assertEqual(store.list(), before)

    def test_publish_rejects_legacy_missing_library_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            lexicon_store = LexiconStore(db_path)
            store = AuditPolicyStore(db_path)
            lexicon_store.upsert_category(
                category_id="publish_missing_library",
                title="发布缺失词库测试",
            )
            policy = store.create(
                id="publish-missing-policy",
                name="Publish missing library",
                config={"library_ids": ["soft"]},
            )
            published = store.publish(
                policy["id"],
                expected_draft_hash=store.draft_hash(policy),
            )
            old_published = deepcopy(published["published_config"])
            old_version = published["published_version"]
            lexicon_store.delete_category_atomically("publish_missing_library")

            stale_config = {"library_ids": ["publish_missing_library"]}
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "UPDATE audit_policies SET config_json = ?, updated_at = updated_at WHERE id = ?",
                    (json.dumps(stale_config), policy["id"]),
                )
            stale = store.get(policy["id"])
            with self.assertRaises(AuditPolicyLibraryReferenceConflictError):
                store.publish(
                    policy["id"],
                    expected_draft_hash=store.draft_hash(stale),
                )
            unchanged = store.get(policy["id"])

        self.assertEqual(unchanged["published_config"], old_published)
        self.assertEqual(unchanged["published_version"], old_version)


    def test_missing_published_ruleset_revision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = RuleSetService(
                RuleSetStore(Path(temp_dir) / "rulesets.sqlite3")
            )
            with self.assertRaises(RuleSetRevisionNotFoundError):
                service.compile_for_execution(
                    "ruleset-revision:missing:v1",
                    audit_policy=audit_policy_snapshot(),
                    principal=PRINCIPAL,
                )

    def test_remove_library_references_never_rewrites_published_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AuditPolicyStore(Path(temp_dir) / "policy.sqlite3")
            before = store.get("policy_gambling")
            affected = store.remove_library_references("gambling")
            after = store.get("policy_gambling")

        self.assertEqual(affected, 1)
        self.assertNotIn("gambling", after["config"]["library_ids"])
        self.assertEqual(after["published_config"], before["published_config"])

    def test_delete_lexicon_rejects_published_reference_without_partial_writes(self) -> None:
        from backend import main as backend_main

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            policy_store = AuditPolicyStore(db_path)
            lexicon_store = LexiconStore(db_path)
            policy = policy_store.get("policy_gambling")
            policy_store.update(
                "policy_gambling",
                config={**policy["config"], "library_ids": ["soft"]},
            )
            policies_before = deepcopy(policy_store.list())
            category_before = deepcopy(lexicon_store.get_category("gambling"))

            with (
                patch.object(backend_main, "audit_policy_store", policy_store),
                patch.object(backend_main, "lexicon_store", lexicon_store),
                self.assertRaises(backend_main.HTTPException) as raised,
            ):
                backend_main.delete_lexicon_category("gambling")

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(policy_store.list(), policies_before)
            self.assertEqual(lexicon_store.get_category("gambling"), category_before)
            references = policy_store.find_library_references("gambling")

        self.assertEqual(references[0]["scopes"], ["published"])

    def test_delete_lexicon_rejects_draft_reference_without_partial_writes(self) -> None:
        from backend import main as backend_main

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            policy_store = AuditPolicyStore(db_path)
            lexicon_store = LexiconStore(db_path)
            lexicon_store.upsert_category(
                category_id="draft_only_library",
                title="Draft 引用测试词库",
            )
            policy_store.create(
                id="policy-draft-library",
                name="Draft reference",
                config={"library_ids": ["draft_only_library"]},
            )
            policies_before = deepcopy(policy_store.list())
            category_before = deepcopy(
                lexicon_store.get_category("draft_only_library")
            )

            with (
                patch.object(backend_main, "audit_policy_store", policy_store),
                patch.object(backend_main, "lexicon_store", lexicon_store),
                self.assertRaises(backend_main.HTTPException) as raised,
            ):
                backend_main.delete_lexicon_category("draft_only_library")

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(policy_store.list(), policies_before)
            self.assertEqual(
                lexicon_store.get_category("draft_only_library"),
                category_before,
            )
            references = policy_store.find_library_references(
                "draft_only_library"
            )

        self.assertEqual(references[0]["scopes"], ["draft"])

    def test_delete_unreferenced_lexicon_succeeds_without_policy_mutation(self) -> None:
        from backend import main as backend_main

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            policy_store = AuditPolicyStore(db_path)
            lexicon_store = LexiconStore(db_path)
            lexicon_store.upsert_category(
                category_id="unreferenced_library",
                title="无引用测试词库",
            )
            policies_before = deepcopy(policy_store.list())

            with (
                patch.object(backend_main, "audit_policy_store", policy_store),
                patch.object(backend_main, "lexicon_store", lexicon_store),
            ):
                result = backend_main.delete_lexicon_category(
                    "unreferenced_library"
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["affected_policy_count"], 0)
            self.assertEqual(policy_store.list(), policies_before)
            with self.assertRaises(KeyError):
                lexicon_store.get_category("unreferenced_library")


class AuditPolicyLexiconConcurrencyTests(unittest.TestCase):
    def test_publish_first_then_delete_conflicts_on_second_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            lexicon_store = LexiconStore(db_path)
            publisher = AuditPolicyStore(db_path)
            lexicon_store.upsert_category(
                category_id="publish_first_library",
                title="发布先行并发测试",
            )
            policy = publisher.create(
                id="publish-first-policy",
                name="Publish first",
                config={"library_ids": ["publish_first_library"]},
            )
            published = Event()
            outcomes: dict[str, object] = {}

            def publish() -> None:
                outcomes["publish"] = publisher.publish(
                    policy["id"],
                    expected_draft_hash=publisher.draft_hash(policy),
                )
                published.set()

            def delete() -> None:
                published.wait(timeout=3)
                try:
                    lexicon_store.delete_category_atomically("publish_first_library")
                except Exception as exc:
                    outcomes["delete"] = exc

            publish_thread = Thread(target=publish)
            delete_thread = Thread(target=delete)
            publish_thread.start()
            delete_thread.start()
            publish_thread.join(timeout=3)
            delete_thread.join(timeout=3)

            self.assertIn("publish", outcomes)
            self.assertIsInstance(
                outcomes.get("delete"),
                LexiconCategoryReferenceConflictError,
            )
            self.assertIsNotNone(lexicon_store.get_category("publish_first_library"))
            self.assertEqual(
                publisher.find_library_references("publish_first_library")[0]["scopes"],
                ["draft", "published"],
            )

    def test_delete_first_then_stale_publish_conflicts_on_second_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            lexicon_store = LexiconStore(db_path)
            publisher = AuditPolicyStore(db_path)
            lexicon_store.upsert_category(
                category_id="delete_first_library",
                title="删除先行并发测试",
            )
            policy = publisher.create(
                id="delete-first-policy",
                name="Delete first",
                config={"library_ids": ["soft"]},
            )
            published = publisher.publish(
                policy["id"],
                expected_draft_hash=publisher.draft_hash(policy),
            )
            delete_done = Event()
            outcomes: dict[str, object] = {}

            def delete() -> None:
                try:
                    outcomes["delete"] = lexicon_store.delete_category_atomically(
                        "delete_first_library"
                    )
                finally:
                    delete_done.set()

            def publish_stale_draft() -> None:
                delete_done.wait(timeout=3)
                stale_config = {"library_ids": ["delete_first_library"]}
                # Model a stale Draft snapshot from a second connection; normal writes reject it.
                with sqlite3.connect(db_path) as connection:
                    connection.execute(
                        "UPDATE audit_policies SET config_json = ? WHERE id = ?",
                        (json.dumps(stale_config), policy["id"]),
                    )
                stale = publisher.get(policy["id"])
                try:
                    publisher.publish(
                        policy["id"],
                        expected_draft_hash=publisher.draft_hash(stale),
                    )
                except Exception as exc:
                    outcomes["publish"] = exc

            delete_thread = Thread(target=delete)
            publish_thread = Thread(target=publish_stale_draft)
            delete_thread.start()
            publish_thread.start()
            delete_thread.join(timeout=3)
            publish_thread.join(timeout=3)

            self.assertIn("delete", outcomes)
            self.assertIsInstance(
                outcomes.get("publish"),
                AuditPolicyLibraryReferenceConflictError,
            )
            references = publisher.find_library_references("delete_first_library")
            self.assertEqual(references[0]["scopes"], ["draft"])
            unchanged = publisher.get(published["id"])
            self.assertEqual(unchanged["published_config"], published["published_config"])
            self.assertEqual(unchanged["published_version"], published["published_version"])
            with self.assertRaises(KeyError):
                lexicon_store.get_category("delete_first_library")


class SnapshotCompatibilityTests(unittest.TestCase):
    def test_v2_round_trip_is_lossless_and_v1_remains_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            service = RuleSetService(RuleSetStore(db_path))
            compiled = service.compile_for_execution(
                GAMBLING_RULESET_REVISION_ID,
                audit_policy=audit_policy_snapshot(),
                principal=PRINCIPAL,
            )
            revisions = TaskAuditConfigRevisionStore(db_path)
            audit_config_v2 = {
                "schema_version": 2,
                "ruleset_ref": compiled["rule_snapshot"]["ruleset_ref"],
                "system_template_version": SYSTEM_TEMPLATE_VERSION,
                "compiler_version": COMPILER_VERSION,
            }
            v2 = revisions.create(
                job_id="job-v2",
                audit_config=audit_config_v2,
                rule_snapshot=compiled["rule_snapshot"],
                prompt_profile_snapshot=compiled["prompt_profile_snapshot"],
                config_hash=compiled["config_hash"],
            )
            loaded_v2 = revisions.get(v2["id"])
            v1 = revisions.create(
                job_id="job-v1",
                audit_config={"schema_version": "1.0"},
                rule_snapshot={"thresholds": {"high": 80}},
                prompt_profile_snapshot={"prompt_version": "legacy-v1"},
                config_hash="legacy-hash",
            )
            loaded_v1 = revisions.get(v1["id"])

        self.assertEqual(loaded_v2["audit_config"], audit_config_v2)
        self.assertNotIn("schema_version", loaded_v2)
        self.assertEqual(loaded_v2["rule_snapshot"], compiled["rule_snapshot"])
        self.assertEqual(
            loaded_v2["prompt_profile_snapshot"],
            compiled["prompt_profile_snapshot"],
        )
        self.assertNotIn("schema_version", loaded_v1)
        legacy_pipeline = AuditPipeline.__new__(AuditPipeline)
        legacy_pipeline.job_id = "legacy"
        legacy_pipeline.rule_snapshot = loaded_v1["rule_snapshot"]
        legacy_pipeline.prompt_profile_snapshot = {
            **loaded_v1["prompt_profile_snapshot"],
            "comment_prompt_template": "V2_SENTINEL {{input_json}}",
        }
        legacy_pipeline.prompt_set = get_prompt_set("gambling")
        subject = AuditSubject(
            platform="xhs",
            note_id="legacy-note",
            url="",
            title="标题",
            desc="正文",
            author={},
            image_urls=[],
            video_urls=[],
            comments=[],
        )
        prompt = legacy_pipeline._render_comment_audit_prompt(subject, "", [])
        self.assertIn("你是评论区逐条审核器", prompt)
        self.assertNotIn("V2_SENTINEL", prompt)

        normalized_comment = legacy_pipeline._normalize_comment_audit_results(
            {
                "comments": [
                    {
                        "id": "legacy-comment",
                        "s": 60,
                        "lib": "gambling",
                        "t": "legacy risk",
                        "rb": "legacy basis",
                        "q": "legacy text",
                    }
                ]
            },
            [
                {
                    "comment_id": "legacy-comment",
                    "source_text": "legacy text",
                    "translation_required": False,
                }
            ],
        )["legacy-comment"]
        self.assertNotIn("rule_id", normalized_comment)
        self.assertNotIn("matched_exemption_ids", normalized_comment)
        legacy_evidence = legacy_pipeline._comment_evidence_items(
            [{"comment_id": "legacy-comment", **normalized_comment}]
        )[0]
        self.assertNotIn("rule_id", legacy_evidence)

        legacy_frame_prompt = legacy_pipeline._render_review_sheet_prompt(
            video_index=0,
            segment={
                "segment_id": "legacy-segment",
                "start": 0,
                "end": 1,
                "ocr_chunks": [],
                "asr_chunks": [],
            },
            frames=[],
            title="legacy title",
            desc="legacy body",
            library_policy={"id": "gambling"},
        )
        legacy_frame_payload = json.loads(legacy_frame_prompt.split("输入 JSON：\n", 1)[1])
        self.assertNotIn("ruleset_stage_rules", legacy_frame_payload)

    def test_m3_resolver_freezes_ruleset_v2_without_using_it_as_search_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            policy_store = AuditPolicyStore(db_path)
            service = RuleSetService(RuleSetStore(db_path))
            resolver = InvestigationConfigurationResolver(
                lexicon_store=LexiconStore(db_path),
                policy_store=policy_store,
                crawler_account_store=CrawlerAccountStore(db_path),
                ruleset_service=service,
                principal_provider=LocalPrincipalProvider(),
            )
            configuration = InvestigationConfiguration.model_validate(
                {
                    "platform": "xhs",
                    "collection": {
                        "crawl_mode": "search",
                        "keyword_source": "keyword",
                        "keywords": ["用户明确输入的搜索词"],
                        "run_crawler": True,
                    },
                    "analysis": {
                        "policy_id": "policy_gambling",
                        "library_ids": ["soft"],
                        "capabilities": ["text", "comment"],
                        "scoring_template": "balanced",
                    },
                }
            )
            resolved = resolver.resolve(configuration)

        revision = resolved["audit_config_revision"]
        self.assertEqual(resolved["keyword"], "用户明确输入的搜索词")
        self.assertEqual(resolved["lexicon_keywords"], [])
        self.assertEqual(revision["audit_config"]["schema_version"], 2)
        self.assertEqual(revision["knowledge_package_snapshots"], [])
        self.assertEqual(
            revision["rule_snapshot"]["ruleset_ref"]["revision_id"],
            GAMBLING_RULESET_REVISION_ID,
        )
        self.assertIn(
            "comment_prompt_template",
            revision["prompt_profile_snapshot"],
        )

    def test_m3_refuses_unpublished_v2_policy_but_keeps_v1_draft_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite3"
            policy_store = AuditPolicyStore(db_path)
            v2_draft = policy_store.create(
                id="policy-v2-draft",
                name="V2 draft",
                config={
                    "library_ids": ["gambling"],
                    "ruleset_revision_id": GAMBLING_RULESET_REVISION_ID,
                    "capabilities": ["text", "comment"],
                    "scoring_template": "balanced",
                },
            )
            v1_draft = policy_store.create(
                id="policy-v1-draft",
                name="V1 draft",
                config={
                    "library_ids": ["gambling"],
                    "capabilities": ["text", "comment"],
                    "scoring_template": "balanced",
                },
            )
            resolver = InvestigationConfigurationResolver(
                lexicon_store=LexiconStore(db_path),
                policy_store=policy_store,
                crawler_account_store=CrawlerAccountStore(db_path),
                ruleset_service=RuleSetService(RuleSetStore(db_path)),
                principal_provider=LocalPrincipalProvider(),
            )

            def configuration(policy_id: str) -> InvestigationConfiguration:
                return InvestigationConfiguration.model_validate(
                    {
                        "platform": "xhs",
                        "collection": {
                            "crawl_mode": "search",
                            "keyword_source": "keyword",
                            "keywords": ["用户搜索词"],
                            "run_crawler": True,
                        },
                        "analysis": {
                            "policy_id": policy_id,
                            "library_ids": ["gambling"],
                            "capabilities": ["text", "comment"],
                            "scoring_template": "balanced",
                        },
                    }
                )

            with self.assertRaisesRegex(
                ConfigurationValidationError,
                "must be published",
            ):
                resolver.resolve(configuration(v2_draft["id"]))
            legacy = resolver.resolve(configuration(v1_draft["id"]))

        self.assertEqual(
            legacy["audit_config_revision"]["audit_config"]["schema_version"],
            "1.0",
        )
        self.assertEqual(
            legacy["audit_config_revision"]["source_policy_version"],
            "draft",
        )


class PipelineContractAndGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.service = RuleSetService(
            RuleSetStore(Path(cls.temp_dir.name) / "rulesets.sqlite3")
        )
        cls.compiled = cls.service.compile_for_execution(
            GAMBLING_RULESET_REVISION_ID,
            audit_policy=audit_policy_snapshot(),
            principal=PRINCIPAL,
        )
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_v2_uses_frozen_comment_template_and_validates_comment_rule_id(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        subject = AuditSubject(
            platform="xhs",
            note_id="comment-note",
            url="",
            title="帖子标题",
            desc="帖子正文",
            author={},
            image_urls=[],
            video_urls=[],
            comments=[],
        )
        comments = [
            {
                "comment_id": "c1",
                "source_text": "求群号，带我上车",
                "translation_required": False,
            }
        ]
        prompt = pipeline._render_comment_audit_prompt(subject, "媒体摘要", comments)
        normalized = pipeline._normalize_comment_audit_results(
            {
                "comments": [
                    {
                        "id": "c1",
                        "s": 60,
                        "risk_level": "medium",
                        "lib": "gambling",
                        "rule_id": "gambling.not_in_revision",
                        "t": "组织参与",
                        "rb": "求群号",
                        "q": "求群号",
                    }
                ]
            },
            comments,
        )

        self.assertIn("独立判断每条评论", prompt)
        self.assertNotIn("当前阶段：comment_audit", prompt)
        self.assertIn("gambling.comment_organized_participation", prompt)
        self.assertIn('"comment_id":"c1"', prompt)
        self.assertEqual(normalized, {})

    def test_v2_comment_batches_preserve_order_without_omission_and_cap_at_twenty(self) -> None:
        class NoTranslation:
            @staticmethod
            def should_translate(_text: str, _language: str = "") -> bool:
                return False

        class CaptureSafeProvider:
            def __init__(self) -> None:
                self.requested_ids: list[list[str]] = []

            def audit_text(self, prompt: str, **_kwargs) -> dict:
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                ids = [str(item["comment_id"]) for item in payload["comments"]]
                self.requested_ids.append(ids)
                return {
                    "comments": [
                        {"id": comment_id, "s": 0, "risk_level": "none"}
                        for comment_id in ids
                    ]
                }

        pipeline = bare_v2_pipeline(self.compiled)
        pipeline.translator = NoTranslation()
        pipeline.qwen = CaptureSafeProvider()
        source_comments = [
            {
                "comment_id": f"c{index:02d}",
                "content": f"按原顺序审核的评论{index:02d}",
            }
            for index in range(1, 46)
        ]
        subject = AuditSubject(
            platform="xhs",
            note_id="comment-batch-order",
            url="",
            title="帖子标题",
            desc="帖子正文",
            author={},
            image_urls=["image.jpg"],
            video_urls=[],
            comments=source_comments,
        )

        with (
            patch.object(settings, "comment_audit_batch_size", 99),
            patch.object(settings, "comment_audit_concurrency", 1),
            patch.object(settings, "comment_audit_prompt_max_chars", 100_000),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            results = pipeline._audit_comments(subject, "媒体摘要")

        requested = [
            comment_id
            for batch in pipeline.qwen.requested_ids
            for comment_id in batch
        ]
        expected = [item["comment_id"] for item in source_comments]
        self.assertEqual(
            [len(batch) for batch in pipeline.qwen.requested_ids],
            [20, 20, 5],
        )
        self.assertEqual(requested, expected)
        self.assertEqual(len(requested), len(set(requested)))
        self.assertEqual([item["comment_id"] for item in results], expected)

    def test_v2_comment_batches_are_greedily_split_by_total_prompt_budget(self) -> None:
        class NoTranslation:
            @staticmethod
            def should_translate(_text: str, _language: str = "") -> bool:
                return False

        class CaptureSafeProvider:
            def __init__(self) -> None:
                self.requested_ids: list[list[str]] = []
                self.prompt_lengths: list[int] = []

            def audit_text(self, prompt: str, **_kwargs) -> dict:
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                ids = [str(item["comment_id"]) for item in payload["comments"]]
                self.requested_ids.append(ids)
                self.prompt_lengths.append(len(prompt))
                return {
                    "comments": [
                        {"id": comment_id, "s": 0, "risk_level": "none"}
                        for comment_id in ids
                    ]
                }

        pipeline = bare_v2_pipeline(self.compiled)
        pipeline.translator = NoTranslation()
        pipeline.qwen = CaptureSafeProvider()
        subject = AuditSubject(
            platform="xhs",
            note_id="comment-budget-order",
            url="",
            title="帖子标题",
            desc="帖子正文",
            author={},
            image_urls=["image.jpg"],
            video_urls=[],
            comments=[
                {"comment_id": f"c{index}", "content": f"短评{index}"}
                for index in range(1, 6)
            ],
        )
        one_comment = [{
            "comment_id": "c1",
            "source_text": "短评1",
            "translation_required": False,
        }]
        with patch.object(settings, "comment_audit_prompt_max_chars", 0):
            one_comment_budget = len(
                pipeline._render_comment_audit_prompt(
                    subject,
                    "媒体摘要",
                    one_comment,
                )
            )

        with (
            patch.object(settings, "comment_audit_batch_size", 20),
            patch.object(settings, "comment_audit_concurrency", 1),
            patch.object(
                settings,
                "comment_audit_prompt_max_chars",
                one_comment_budget,
            ),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            results = pipeline._audit_comments(subject, "媒体摘要")

        self.assertEqual(
            pipeline.qwen.requested_ids,
            [["c1"], ["c2"], ["c3"], ["c4"], ["c5"]],
        )
        self.assertTrue(
            all(length <= one_comment_budget for length in pipeline.qwen.prompt_lengths)
        )
        self.assertEqual(len(results), 5)
        self.assertTrue(all(item["audit_status"] == "completed" for item in results))

    def test_v2_only_fails_single_comment_that_cannot_fit_at_300_char_boundaries(self) -> None:
        class NoTranslation:
            @staticmethod
            def should_translate(_text: str, _language: str = "") -> bool:
                return False

        class CaptureSafeProvider:
            def __init__(self) -> None:
                self.requested_ids: list[list[str]] = []

            def audit_text(self, prompt: str, **_kwargs) -> dict:
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                ids = [str(item["comment_id"]) for item in payload["comments"]]
                self.requested_ids.append(ids)
                return {
                    "comments": [
                        {"id": comment_id, "s": 0, "risk_level": "none"}
                        for comment_id in ids
                    ]
                }

        pipeline = bare_v2_pipeline(self.compiled)
        pipeline.translator = NoTranslation()
        pipeline.qwen = CaptureSafeProvider()
        subject = AuditSubject(
            platform="xhs",
            note_id="comment-single-oversized",
            url="",
            title="帖子标题",
            desc="帖子正文",
            author={},
            image_urls=["image.jpg"],
            video_urls=[],
            comments=[
                {"comment_id": "c1", "content": "短评1"},
                {
                    "comment_id": "c2",
                    "content": "评" * 300,
                    "translation_zh": "译" * 300,
                },
                {"comment_id": "c3", "content": "短评3"},
            ],
        )
        short_comment = [{
            "comment_id": "c1",
            "source_text": "短评1",
            "translation_required": False,
        }]
        boundary_comment = [{
            "comment_id": "c2",
            "source_text": "评" * 300,
            "translation_required": False,
            "translation_zh": "译" * 300,
        }]
        with patch.object(settings, "comment_audit_prompt_max_chars", 0):
            short_budget = len(
                pipeline._render_comment_audit_prompt(
                    subject,
                    "媒体摘要",
                    short_comment,
                )
            )
            boundary_prompt = pipeline._render_comment_audit_prompt(
                subject,
                "媒体摘要",
                boundary_comment,
            )
        boundary_payload = json.loads(boundary_prompt.split("输入 JSON：\n", 1)[1])
        self.assertEqual(
            boundary_payload["comments"][0]["source_text"],
            "评" * 300,
        )
        self.assertEqual(
            boundary_payload["comments"][0]["translation_zh"],
            "译" * 300,
        )

        with (
            patch.object(settings, "comment_audit_batch_size", 20),
            patch.object(settings, "comment_audit_concurrency", 1),
            patch.object(settings, "comment_audit_prompt_max_chars", short_budget),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            results = pipeline._audit_comments(subject, "媒体摘要")

        by_id = {item["comment_id"]: item for item in results}
        self.assertEqual(pipeline.qwen.requested_ids, [["c1"], ["c3"]])
        self.assertEqual(by_id["c1"]["audit_status"], "completed")
        self.assertEqual(by_id["c3"]["audit_status"], "completed")
        self.assertEqual(by_id["c2"]["audit_status"], "failed")
        self.assertIn("cannot fit V2 prompt budget", by_id["c2"]["audit_error"])

    def test_stage_router_accepts_only_current_stage_rules(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        result = pipeline._filter_stage_risk_items(
            [
                {
                    "rule_id": "gambling.platform_entry_and_funding",
                    "severity": "high",
                },
            ],
            "image_evidence",
        )
        self.assertEqual(
            [item["rule_id"] for item in result],
            ["gambling.platform_entry_and_funding"],
        )
        for invalid_rule_id in (
            "gambling.comment_organized_participation",
            "gambling.not_in_revision",
        ):
            with self.subTest(rule_id=invalid_rule_id), self.assertRaises(
                FusionAuditContractError
            ):
                pipeline._filter_stage_risk_items(
                    [{"rule_id": invalid_rule_id, "severity": "high"}],
                    "image_evidence",
                )
        with self.assertRaises(FusionAuditContractError):
            pipeline._filter_stage_risk_items(
                [{"rule_id": "gambling.platform_entry_and_funding"}],
                "image_evidence",
            )

    def test_comment_actual_level_controls_score_and_requires_legal_rule(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        pipeline.prompt_profile_snapshot["libraries"].append(
            {
                "id": "soft",
                "title": "次风险库",
                "audit_goal": "测试次风险库字段",
                "output_labels": [],
            }
        )
        comments = [
            {
                "comment_id": "c1",
                "source_text": "进群下注，充值上分",
                "translation_required": False,
            }
        ]

        missing_rule = pipeline._normalize_comment_audit_results(
            {"comments": [{"id": "c1", "s": 0, "risk_level": "high"}]},
            comments,
        )
        legal = pipeline._normalize_comment_audit_results(
            {
                "comments": [
                    {
                        "id": "c1",
                        "s": 0,
                        "risk_level": "high",
                        "lib": "gambling",
                        "sec": ["soft"],
                        "t": "组织参与",
                        "rb": "邀请下注充值",
                        "q": "进群下注，充值上分",
                        "rule_id": "gambling.comment_organized_participation",
                    }
                ]
            },
            comments,
        )["c1"]
        conflicting_score = pipeline._normalize_comment_audit_results(
            {
                "comments": [
                    {
                        "id": "c1",
                        "s": 100,
                        "risk_level": "low",
                        "lib": "gambling",
                        "t": "组织参与",
                        "rb": "邀请下注充值",
                        "q": "进群下注，充值上分",
                        "rule_id": "gambling.comment_organized_participation",
                    }
                ]
            },
            comments,
        )["c1"]

        self.assertEqual(missing_rule, {})
        self.assertEqual((legal["risk_level"], legal["risk_score"]), ("high", 80))
        self.assertEqual(legal["secondary_library_ids"], ["soft"])
        self.assertEqual(
            (conflicting_score["risk_level"], conflicting_score["risk_score"]),
            ("low", 40),
        )

    def test_fusion_adversarial_rule_and_evidence_closure(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        evidence_index = {
            "evidence_catalog": [
                {
                    "evidence_id": "image:1",
                    "source": "image:1",
                    "primary_modality": "vision",
                }
            ]
        }
        legal_rule = "gambling.platform_entry_and_funding"

        def audit(rule_id: str, evidence_ids: list[str], **overrides) -> dict:
            value = {
                "schema_version": "audit_fusion_v4",
                "content_title": "博彩入口与资金路径",
                "summary": "图片展示可执行入口和资金路径",
                "decision_suggestion": "reject",
                "risk_level_suggestion": "high",
                "primary_risk": "投注平台导流",
                "categories": ["投注平台导流"],
                "evidence_items": [
                    {
                        "evidence_id": "image:1",
                        "evidence_risk_level": "high",
                        "reason": "closed loop",
                    }
                ],
                "rule_matches": [
                    {"rule_id": rule_id, "evidence_ids": evidence_ids}
                ],
            }
            value.update(overrides)
            return value

        for invalid in (
            audit("gambling.fake", ["fake:evidence"]),
            audit(legal_rule, []),
            audit(legal_rule, ["fake:evidence"]),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                FusionAuditContractError
            ):
                pipeline._validate_v2_fusion_contract(invalid, evidence_index)

        wrong_stage = bare_v2_pipeline(self.compiled)
        wrong_stage.rule_snapshot["stage_routes"]["fusion_audit"] = [
            "gambling.multimodal_closed_loop"
        ]
        with self.assertRaises(FusionAuditContractError):
            wrong_stage._validate_v2_fusion_contract(
                audit(legal_rule, ["image:1"]),
                evidence_index,
            )

        with self.assertRaisesRegex(FusionAuditContractError, "pass/none"):
            pipeline._validate_v2_fusion_contract(
                audit(
                    legal_rule,
                    ["image:1"],
                    decision_suggestion="pass",
                    risk_level_suggestion="none",
                ),
                evidence_index,
            )

    def test_fusion_retries_contract_invalid_and_accepts_valid_retry(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        evidence_index = {
            "evidence_catalog": [
                {
                    "evidence_id": "image:1",
                    "source": "image:1",
                    "primary_modality": "vision",
                }
            ]
        }
        invalid = {
            "schema_version": "audit_fusion_v4",
            "content_title": "伪造证据测试",
            "summary": "首次返回引用伪造证据",
            "decision_suggestion": "reject",
            "risk_level_suggestion": "high",
            "primary_risk": "投注平台导流",
            "categories": ["投注平台导流"],
            "evidence_items": [
                {
                    "evidence_id": "fake",
                    "evidence_risk_level": "high",
                    "reason": "伪造目录证据",
                }
            ],
            "rule_matches": [
                {"rule_id": "gambling.fake", "evidence_ids": ["fake"]}
            ],
        }
        valid = {
            "schema_version": "audit_fusion_v4",
            "content_title": "博彩入口与资金路径",
            "summary": "图片展示可执行入口和资金路径",
            "decision_suggestion": "reject",
            "risk_level_suggestion": "high",
            "primary_risk": "投注平台导流",
            "categories": ["投注平台导流"],
            "evidence_items": [
                {
                    "evidence_id": "image:1",
                    "evidence_risk_level": "high",
                    "reason": "入口与充值同屏",
                }
            ],
            "rule_matches": [
                {
                    "rule_id": "gambling.platform_entry_and_funding",
                    "evidence_ids": ["image:1"],
                }
            ],
        }
        pipeline.qwen = SequenceProvider([invalid, valid])

        with (
            patch.object(settings, "fusion_timeout_retries", 1),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            result = pipeline._run_fusion_audit(
                "retry-note",
                "frozen prompt",
                contract_validator=lambda value: pipeline._validate_v2_fusion_contract(
                    value,
                    evidence_index,
                ),
            )

        self.assertEqual(pipeline.qwen.calls, 2)
        self.assertEqual(result["evidence_items"][0]["rule_id"], "gambling.platform_entry_and_funding")

    def test_structured_exemption_applies_only_for_valid_frozen_id(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        evidence_index = {
            "evidence_catalog": [
                {"evidence_id": "image:1", "source": "image:1"}
            ]
        }
        base = {
            "schema_version": "audit_fusion_v4",
            "content_title": "博彩入口与资金路径",
            "summary": "图片展示可执行入口和资金路径",
            "decision_suggestion": "reject",
            "risk_level_suggestion": "high",
            "primary_risk": "投注平台导流",
            "categories": ["投注平台导流"],
            "evidence_items": [
                {
                    "evidence_id": "image:1",
                    "evidence_risk_level": "high",
                    "reason": "入口与充值同屏",
                }
            ],
            "rule_matches": [
                {
                    "rule_id": "gambling.platform_entry_and_funding",
                    "evidence_ids": ["image:1"],
                }
            ],
        }
        invalid = deepcopy(base)
        invalid["rule_matches"][0]["matched_exemption_ids"] = ["forged"]
        retained = pipeline._validate_v2_fusion_contract(invalid, evidence_index)

        valid = deepcopy(base)
        valid["rule_matches"][0]["matched_exemption_ids"] = [
            "gambling.exemption.news_and_education"
        ]
        exempted = pipeline._validate_v2_fusion_contract(valid, evidence_index)

        self.assertEqual(retained["risk_level_suggestion"], "high")
        self.assertEqual(len(retained["evidence_items"]), 1)
        self.assertEqual(exempted["decision_suggestion"], "pass")
        self.assertEqual(exempted["risk_level_suggestion"], "none")
        self.assertEqual(exempted["evidence_items"], [])
        self.assertEqual(exempted["rule_matches"], [])
        self.assertEqual(
            exempted["matched_exemption_ids"],
            ["gambling.exemption.news_and_education"],
        )

    def test_contact_sheet_main_path_uses_frozen_v2_prompt_and_normalizes_score(self) -> None:
        content = gambling_ruleset_v2().model_dump(mode="json")
        disabled_rule_id = "gambling.platform_entry_and_funding"
        for category in content["categories"]:
            for rule in category["rules"]:
                if rule["rule_id"] == disabled_rule_id:
                    rule["enabled"] = False
        compiled = compile_ruleset_revision(
            {
                "id": "ruleset-revision:frame-disabled:v1",
                "ruleset_id": "ruleset.frame-disabled",
                "version": 1,
                "status": "published",
                "content_hash": content_hash(content),
                "snapshot": content,
            },
            audit_policy=audit_policy_snapshot(
                ruleset_revision_id="ruleset-revision:frame-disabled:v1"
            ),
        )
        pipeline = bare_v2_pipeline(compiled)

        class FakeAudio:
            last_extract_error = "no audio in fixture"

            @staticmethod
            def extract_audio(_video_path, _output_dir):
                return None

        class FakeFrames:
            @staticmethod
            def extract_timeline_frames(video_path, output_dir, max_frames):
                output_dir.mkdir(parents=True, exist_ok=True)
                frame = output_dir / "f0001.jpg"
                frame.write_bytes(b"frame fixture")
                return [{
                    "frame_id": "f0001",
                    "frame_number": 1,
                    "timestamp": 0.5,
                    "path": str(frame),
                }]

            @staticmethod
            def video_meta(video_path):
                return 1.0, 30, 1.0

            @staticmethod
            def create_contact_sheet(frames, output_path, columns, rows):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"sheet fixture")

        class FakeFrameProvider:
            enabled = True

            def __init__(self):
                self.prompts: list[str] = []

            def analyze_image(self, _image_path, prompt, **_kwargs):
                self.prompts.append(prompt)
                return {
                    "segment_summary": "平台界面",
                    "segment_score": 100,
                    "risk_library_id": "gambling",
                    "risk_library_label": "赌博博彩风险",
                    "visual_risks": [
                        {
                            "frame_ids": ["f0001"],
                            "score": 100,
                            "risk_level": "high",
                            "rule_id": "gambling.betting_and_settlement",
                            "risk_type": "盘口操作",
                            "reason": "盘口和充值操作同屏",
                        },
                        {
                            "frame_ids": ["f0001"],
                            "score": 100,
                            "risk_level": "high",
                            "rule_id": "gambling.agent_guaranteed_win_promotion",
                            "risk_type": "新闻中的代理话术",
                            "reason": "合法豁免不得贡献风险",
                            "matched_exemption_ids": [
                                "gambling.exemption.news_and_education"
                            ],
                        },
                    ],
                    "ocr_risks": [],
                    "asr_risks": [],
                }

        pipeline.audio = FakeAudio()
        pipeline.frames = FakeFrames()
        pipeline.qwen = FakeFrameProvider()
        pipeline._scan_timeline_ocr_batch = lambda *_args: {}
        pipeline._translate_transcript_if_needed = lambda transcript, *_args, **_kwargs: transcript

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(settings, "outputs_dir", Path(temp_dir)),
            patch.object(settings, "video_review_max_frames", 16),
            patch.object(settings, "video_review_sheet_frames", 16),
            patch.object(settings, "video_review_concurrency", 1),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            root = Path(temp_dir)
            video_path = root / "video.mp4"
            video_path.write_bytes(b"video fixture")
            result = pipeline._analyze_video_file(
                0,
                video_path,
                root / "video-output",
                "",
                "local",
                title="title",
                desc="desc",
            )

        self.assertEqual(len(pipeline.qwen.prompts), 1)
        prompt = pipeline.qwen.prompts[0]
        self.assertTrue(prompt.startswith(pipeline.prompt_set.frame_prompt))
        self.assertEqual(prompt.count("输入 JSON："), 1)
        self.assertNotIn("source_mappings", prompt)
        self.assertNotIn(disabled_rule_id, prompt)
        risks = result["segment_reviews"][0]["analysis"]["visual_risks"]
        self.assertEqual(
            [item["rule_id"] for item in risks],
            ["gambling.betting_and_settlement"],
        )
        self.assertEqual(risks[0]["risk_level"], "high")
        self.assertEqual(result["segment_reviews"][0]["analysis"]["segment_score"], 80)
        self.assertEqual(
            result["segment_reviews"][0]["analysis"]["matched_exemption_ids"],
            ["gambling.exemption.news_and_education"],
        )
        self.assertEqual(
            result["segment_reviews"][0]["library_reviews"][0][
                "matched_exemption_ids"
            ],
            ["gambling.exemption.news_and_education"],
        )

    def test_contact_sheet_contract_rejects_missing_level_rule_and_frame(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        sheet = {"ocr_chunks": [], "asr_chunks": []}
        frames = [{"frame_id": "f0001"}]
        base = {
            "segment_summary": "平台界面",
            "segment_score": 100,
            "risk_library_id": "gambling",
            "risk_library_label": "赌博博彩风险",
            "visual_risks": [
                {
                    "frame_ids": ["f0001"],
                    "score": 100,
                    "risk_level": "high",
                    "rule_id": "gambling.betting_and_settlement",
                    "risk_type": "盘口操作",
                    "reason": "盘口和充值操作同屏",
                }
            ],
            "ocr_risks": [],
            "asr_risks": [],
        }
        compatible_zero = deepcopy(base)
        compatible_zero["visual_risks"][0]["score"] = 0
        compatible_zero["visual_risks"].append(
            {
                "frame_ids": ["legacy-placeholder"],
                "score": 0,
                "risk_type": "",
                "reason": "",
            }
        )
        normalized = pipeline._normalize_segment_review(
            compatible_zero,
            sheet,
            frames,
        )
        self.assertEqual(len(normalized["visual_risks"]), 1)
        self.assertEqual(
            (
                normalized["visual_risks"][0]["risk_level"],
                normalized["visual_risks"][0]["score"],
            ),
            ("high", 80),
        )
        invalid_values = []
        missing_level = deepcopy(base)
        missing_level["visual_risks"][0].pop("risk_level")
        invalid_values.append(missing_level)
        invalid_rule = deepcopy(base)
        invalid_rule["visual_risks"][0]["rule_id"] = (
            "gambling.comment_organized_participation"
        )
        invalid_values.append(invalid_rule)
        invalid_frame = deepcopy(base)
        invalid_frame["visual_risks"][0]["frame_ids"] = ["f9999"]
        invalid_values.append(invalid_frame)
        invalid_score = deepcopy(base)
        invalid_score["visual_risks"][0]["score"] = "not-a-score"
        invalid_values.append(invalid_score)

        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(
                FusionAuditContractError
            ):
                pipeline._normalize_segment_review(value, sheet, frames)

    def test_v1_v2_prompt_length_golden_and_v2_dynamic_budget(self) -> None:
        v2 = bare_v2_pipeline(self.compiled)
        v1 = AuditPipeline.__new__(AuditPipeline)
        v1.job_id = "prompt-length-v1"
        v1.rule_snapshot = {
            "thresholds": {"high": 80, "medium": 60, "review": 40},
            "scoring_rules": [],
        }
        v1.prompt_profile_snapshot = {}
        v1.prompt_set = get_prompt_set("gambling")
        subject = AuditSubject(
            platform="xhs",
            note_id="prompt-golden",
            url="",
            title="体育投注平台曝光",
            desc="新闻调查展示赔率、二维码、充值和提现链路，提醒用户不要参与。",
            author={"nickname": "记者"},
            image_urls=[],
            video_urls=[],
            comments=[],
        )
        comments = [
            {
                "comment_id": "c1",
                "source_text": "求群号，带我上车",
                "translation_required": False,
            },
            {
                "comment_id": "c2",
                "source_text": "这是反赌警示新闻",
                "translation_required": True,
                "translation_zh": "评论已有中文译文" * 14 + "全文",
            },
        ]
        audited_comments = [
            {
                "comment_id": "c1",
                "source_text": "求群号，带我上车",
                "audit_status": "completed",
                "risk_score": 60,
                "risk_level": "medium",
                "risk_library_id": "gambling",
                "rule_id": "gambling.comment_organized_participation",
                "risk_type": "组织参与",
                "risk_basis": "求群号",
            }
        ]
        segment = {
            "segment_id": "video:1/segment:1",
            "start": 0.0,
            "end": 8.0,
            "ocr_chunks": [
                {
                    "ocr_chunk_id": "video:1/segment:1/ocr:1",
                    "text": "扫码充值，上分提现",
                    "text_zh": "新闻反赌警示译文" * 12,
                }
            ],
            "asr_chunks": [
                {
                    "asr_chunk_id": "video:1/segment:1/asr:1",
                    "translation_zh": "新闻曝光赌博平台",
                }
            ],
        }
        frames = [{"frame_id": "f0001", "frame_number": 1, "timestamp": 0.5}]
        evidence_index = {
            "evidence_catalog": [
                {
                    "evidence_id": "image:0",
                    "source": "image:0",
                    "primary_modality": "vision",
                    "risk_type": "平台入口",
                    "reason": "二维码与充值同屏",
                    "evidence_risk_level": "high",
                    "rule_id": "gambling.platform_entry_and_funding",
                    "text": "投注平台入口证据" * 12,
                    "visual_summary": (
                        "画面展示赔率与充值提现路径" * 8
                        + "，仅用于新闻反赌完整取证"
                    ),
                }
            ],
            "image_units": [
                {
                    "source": "image:0",
                    "visual_summary": "平台二维码和充值入口",
                    "benign_context": "新闻曝光",
                }
            ],
            "segment_reviews": [],
        }

        def prompts(pipeline: AuditPipeline) -> dict[str, str]:
            return {
                "image_evidence": pipeline.prompt_set.image_prompt,
                "video_frame_evidence": pipeline._render_review_sheet_prompt(
                    video_index=0,
                    segment=deepcopy(segment),
                    frames=frames,
                    title=subject.title,
                    desc=subject.desc,
                    library_policy={"id": "gambling", "title": "赌博博彩风险"},
                ),
                "comment_audit": pipeline._render_comment_audit_prompt(
                    subject,
                    "平台截图和反赌警示",
                    deepcopy(comments),
                ),
                "fusion_audit": pipeline._render_compact_fusion_prompt(
                    subject,
                    deepcopy(evidence_index),
                    deepcopy(audited_comments),
                ),
            }

        def input_payload(prompt: str) -> dict:
            return json.loads(prompt.split("输入 JSON：\n", 1)[1])

        rendered = {
            version: prompts(pipeline)
            for version, pipeline in (("v1", v1), ("v2", v2))
        }
        payloads = {
            version: {
                stage: input_payload(prompt)
                for stage, prompt in stage_prompts.items()
                if stage != "image_evidence"
            }
            for version, stage_prompts in rendered.items()
        }
        v1_fixed = {
            "image_evidence": 995,
            "video_frame_evidence": 2093,
            "comment_audit": 3007,
            "fusion_audit": 2250,
        }
        v2_fixed = v2.prompt_profile_snapshot["fixed_prompt_chars"]
        self.assertEqual(
            v2_fixed,
            {
                "image_evidence": 1600,
                "video_frame_evidence": 2011,
                "comment_audit": 1916,
                "fusion_audit": 2076,
            },
        )
        # Exact goldens supersede the one-time v1-to-v2 migration ratio; later
        # prompt contract hardening is still bounded by the compiler budget tests.

        self.assertEqual(
            payloads["v2"]["video_frame_evidence"],
            payloads["v1"]["video_frame_evidence"],
        )
        for key in ("post_context", "comments"):
            self.assertEqual(
                payloads["v2"]["comment_audit"][key],
                payloads["v1"]["comment_audit"][key],
            )
        fusion_dynamic_keys = (
            "post",
            "media_summaries",
            "evidence_catalog",
            "comment_stats",
            "top_comments",
        )
        for key in fusion_dynamic_keys:
            self.assertGreaterEqual(
                len(canonical_json(payloads["v2"]["fusion_audit"][key])),
                len(canonical_json(payloads["v1"]["fusion_audit"][key])),
                key,
            )
        business_dynamic = {
            "v1": {
                "image_evidence": 0,
                "video_frame_evidence": len(
                    canonical_json(payloads["v1"]["video_frame_evidence"])
                ),
                "comment_audit": len(
                    canonical_json(
                        {
                            key: payloads["v1"]["comment_audit"][key]
                            for key in ("post_context", "comments")
                        }
                    )
                ),
                "fusion_audit": len(
                    canonical_json(
                        {
                            key: payloads["v1"]["fusion_audit"][key]
                            for key in fusion_dynamic_keys
                        }
                    )
                ),
            },
            "v2": {
                "image_evidence": 0,
                "video_frame_evidence": len(
                    canonical_json(payloads["v2"]["video_frame_evidence"])
                ),
                "comment_audit": len(
                    canonical_json(
                        {
                            key: payloads["v2"]["comment_audit"][key]
                            for key in ("post_context", "comments")
                        }
                    )
                ),
                "fusion_audit": len(
                    canonical_json(
                        {
                            key: payloads["v2"]["fusion_audit"][key]
                            for key in fusion_dynamic_keys
                        }
                    )
                ),
            },
        }
        self.assertEqual(
            business_dynamic,
            {
                "v1": {
                    "image_evidence": 0,
                    "video_frame_evidence": 532,
                    "comment_audit": 420,
                    "fusion_audit": 1062,
                },
                "v2": {
                    "image_evidence": 0,
                    "video_frame_evidence": 532,
                    "comment_audit": 420,
                    "fusion_audit": 1115,
                },
            },
        )
        character_table = {
            version: {
                stage: {
                    "fixed": (v1_fixed if version == "v1" else v2_fixed)[stage],
                    "dynamic": business_dynamic[version][stage],
                    "total": (
                        (v1_fixed if version == "v1" else v2_fixed)[stage]
                        + business_dynamic[version][stage]
                    ),
                }
                for stage in v1_fixed
            }
            for version in ("v1", "v2")
        }
        self.assertEqual(
            character_table,
            {
                "v1": {
                    "image_evidence": {"fixed": 995, "dynamic": 0, "total": 995},
                    "video_frame_evidence": {"fixed": 2093, "dynamic": 532, "total": 2625},
                    "comment_audit": {"fixed": 3007, "dynamic": 420, "total": 3427},
                    "fusion_audit": {"fixed": 2250, "dynamic": 1062, "total": 3312},
                },
                "v2": {
                    "image_evidence": {"fixed": 1600, "dynamic": 0, "total": 1600},
                    "video_frame_evidence": {"fixed": 2011, "dynamic": 532, "total": 2543},
                    "comment_audit": {"fixed": 1916, "dynamic": 420, "total": 2336},
                    "fusion_audit": {"fixed": 2076, "dynamic": 1115, "total": 3191},
                },
            },
        )
        for stage in v1_fixed:
            self.assertGreaterEqual(
                business_dynamic["v2"][stage],
                business_dynamic["v1"][stage],
                stage,
            )

        boundary_comments = [
            {
                "comment_id": "boundary",
                "source_text": "评" * 300,
                "translation_required": True,
                "translation_zh": "译" * 300,
            }
        ]
        boundary_payload = input_payload(
            v2._render_comment_audit_prompt(subject, "媒体摘要", boundary_comments)
        )
        self.assertEqual(
            boundary_payload["comments"][0]["source_text"],
            boundary_comments[0]["source_text"],
        )
        self.assertEqual(
            boundary_payload["comments"][0]["translation_zh"],
            boundary_comments[0]["translation_zh"],
        )
        over_boundary_payload = input_payload(
            v2._render_comment_audit_prompt(
                subject,
                "媒体摘要",
                [{
                    "comment_id": "over-boundary",
                    "source_text": "原" * 301,
                    "translation_required": True,
                    "translation_zh": "译" * 301,
                }],
            )
        )
        self.assertEqual(
            over_boundary_payload["comments"][0]["source_text"],
            "原" * 300,
        )
        self.assertEqual(
            over_boundary_payload["comments"][0]["translation_zh"],
            "译" * 300,
        )

        long_comments = [
            {
                "comment_id": f"long-{index}",
                "source_text": "评论动态输入" * 80,
                "translation_required": False,
            }
            for index in range(80)
        ]
        long_segment = deepcopy(segment)
        long_segment["ocr_chunks"] = [
            {"ocr_chunk_id": f"ocr:{index}", "text": "帧动态输入" * 100}
            for index in range(100)
        ]
        long_segment["asr_chunks"] = [
            {"asr_chunk_id": f"asr:{index}", "translation_zh": "语音动态输入" * 100}
            for index in range(100)
        ]
        long_evidence = deepcopy(evidence_index)
        long_evidence["evidence_catalog"] = [
            {
                "evidence_id": f"image:{index}",
                "source": f"image:{index}",
                "primary_modality": "vision",
                "reason": "融合动态输入" * 100,
                "visual_summary": "候选画面描述" * 100,
                "evidence_risk_level": "high",
            }
            for index in range(300)
        ]
        with (
            patch("backend.audit_agent.pipeline.job_store.log"),
            self.assertRaisesRegex(
                FusionAuditContractError,
                "comment dynamic input cannot fit prompt budget",
            ),
        ):
            v2._render_comment_audit_prompt(
                subject,
                "媒体动态输入" * 1_000,
                long_comments,
            )
        self.assertTrue(all(len(item["source_text"]) > 300 for item in long_comments))

        with patch("backend.audit_agent.pipeline.job_store.log"):
            long_rendered = {
                "video_frame_evidence": v2._render_review_sheet_prompt(
                    video_index=0,
                    segment=long_segment,
                    frames=frames,
                    title=subject.title,
                    desc=subject.desc,
                    library_policy={"id": "gambling", "title": "赌博博彩风险"},
                ),
                "fusion_audit": v2._render_compact_fusion_prompt(
                    subject,
                    long_evidence,
                    audited_comments * 100,
                ),
            }

        fixed_keys = {
            "video_frame_evidence": "frame_prompt",
            "fusion_audit": "fusion_prompt_template",
        }
        runtime_limits = {
            "video_frame_evidence": settings.frame_audit_prompt_max_chars,
            "fusion_audit": settings.fusion_prompt_max_chars,
        }
        for stage, prompt in long_rendered.items():
            fixed_prompt = v2.prompt_profile_snapshot[fixed_keys[stage]]
            self.assertTrue(prompt.startswith(fixed_prompt))
            self.assertEqual(prompt.count("输入 JSON："), 1)
            self.assertLessEqual(len(prompt), runtime_limits[stage])
            self.assertLessEqual(
                len(fixed_prompt),
                FIXED_PROMPT_LIMITS[stage],
            )

    def test_comment_only_high_risk_keeps_review_attribution_and_metadata(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        pipeline.qwen = FixedProvider({
            "schema_version": "audit_fusion_v4",
            "content_title": "评论区博彩邀约",
            "summary": "第三方评论邀请加入博彩群",
            "decision_suggestion": "reject",
            "risk_level_suggestion": "high",
            "primary_risk": "博彩组织参与",
            "categories": ["博彩组织参与"],
            "evidence_items": [
                {
                    "evidence_id": "comment:c1",
                    "evidence_risk_level": "high",
                    "reason": "评论邀请进群参与",
                }
            ],
            "rule_matches": [
                {
                    "rule_id": "gambling.comment_organized_participation",
                    "evidence_ids": ["comment:c1"],
                }
            ],
        })
        audited_comment = {
            "comment_id": "c1",
            "nickname": "third party",
            "source_text": "进群跟单",
            "content": "进群跟单",
            "audit_status": "completed",
            "risk_score": 80,
            "risk_level": "high",
            "risk_library_id": "gambling",
            "risk_library_label": "赌博博彩风险",
            "rule_id": "gambling.comment_organized_participation",
            "risk_type": "组织参与",
            "risk_basis": "邀请参与",
            "evidence_quote": "进群跟单",
        }
        pipeline._translate_subject_texts = lambda _subject: None
        pipeline._analyze_images = lambda _subject, _path: []
        pipeline._analyze_videos = lambda _subject, _path: []
        pipeline._audit_comments = lambda _subject, _summary: [audited_comment]
        subject = AuditSubject(
            platform="xhs",
            note_id="note-metadata",
            url="https://example.test/note-metadata",
            title="ordinary post title",
            desc="ordinary post body",
            author={"user_id": "author-1", "nickname": "post author"},
            image_urls=[],
            video_urls=[],
            comments=[],
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(settings, "outputs_dir", Path(temp_dir)),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            result = pipeline._analyze_subject(subject)
            pipeline.audit_results = AuditResultStore(Path(temp_dir) / "audit.sqlite3")
            pipeline.audit_config_revision_id = "config-revision-v2"
            persisted = pipeline._persist_audit_result(
                platform=subject.platform,
                content_key=subject.note_id,
                result=result,
                result_path=Path(temp_dir) / "result.json",
                content_id=42,
            )

        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["risk_level"], "high")
        self.assertEqual(result["risk_score"], 80)
        self.assertEqual(result["title"], subject.title)
        self.assertEqual(result["desc"], subject.desc)
        self.assertEqual(result["author"], subject.author)
        self.assertEqual(result["url"], subject.url)
        self.assertEqual(persisted["platform"], "xhs")
        self.assertEqual(persisted["content_id"], 42)
        required_fields = {
            "decision",
            "risk_level",
            "risk_score",
            "summary",
            "primary_risk",
            "categories",
            "evidence_items",
            "rule_matches",
            "risk_evidence",
            "risk_frames",
            "risk_images",
        }
        self.assertTrue(required_fields.issubset(result))

    def test_v2_pass_clears_candidate_risk_artifacts(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        pipeline.qwen = FixedProvider({
            "schema_version": "audit_fusion_v4",
            "content_title": "反赌警示内容",
            "summary": "新闻警示语境",
            "decision_suggestion": "pass",
            "risk_level_suggestion": "none",
            "primary_risk": "forged residual",
            "categories": ["forged residual"],
            "evidence_items": [],
            "rule_matches": [],
            "evidence": [{"source": "image:1", "severity": "high"}],
        })
        pipeline._translate_subject_texts = lambda _subject: None
        pipeline._analyze_images = lambda _subject, _path: [{
            "index": 0,
            "evidence_id": "image:0",
            "risk_items": [{
                "rule_id": "gambling.platform_entry_and_funding",
                "severity": "high",
                "risk_type": "candidate only",
                "reason": "candidate",
            }],
        }]
        pipeline._analyze_videos = lambda _subject, _path: []
        pipeline._audit_comments = lambda _subject, _summary: []
        subject = AuditSubject(
            platform="xhs",
            note_id="pass-note",
            url="",
            title="反赌新闻",
            desc="风险警示",
            author={},
            image_urls=[],
            video_urls=[],
            comments=[],
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(settings, "outputs_dir", Path(temp_dir)),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            result = pipeline._analyze_subject(subject)

        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["risk_level"], "none")
        self.assertEqual(result["risk_score"], 0)
        self.assertEqual(result["primary_risk"], "")
        self.assertEqual(result["categories"], [])
        self.assertEqual(result["evidence_items"], [])
        self.assertEqual(result["rule_matches"], [])
        self.assertEqual(result["risk_evidence"], [])
        self.assertEqual(result["risk_frames"], [])
        self.assertEqual(result["risk_images"], [])
        self.assertEqual(result["evidence"], [])

    def test_gambling_golden_fixed_provider_outputs_obey_contract(self) -> None:
        pipeline = bare_v2_pipeline(self.compiled)
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                provider = FixedProvider(case["fixed_provider_response"])
                pipeline.qwen = provider
                evidence_index = {"evidence_catalog": case["evidence_catalog"]}
                with patch("backend.audit_agent.pipeline.job_store.log"):
                    response = pipeline._run_fusion_audit(
                        case["id"],
                        "frozen prompt",
                        contract_validator=lambda value: pipeline._validate_v2_fusion_contract(
                            value,
                            evidence_index,
                        ),
                    )
                subject = AuditSubject(
                    platform="xhs",
                    note_id=case["id"],
                    url="",
                    title="",
                    desc="",
                    author={},
                    image_urls=[],
                    video_urls=[],
                    comments=[],
                )
                evidence_items = pipeline._normalize_evidence_items(
                    response,
                    subject,
                    evidence_index,
                )
                valid_evidence_ids = {
                    item["evidence_id"] for item in evidence_items
                }
                rule_matches = pipeline._rule_matches_for_evidence_ids(
                    pipeline._normalize_rule_matches(response),
                    valid_evidence_ids,
                )

                self.assertEqual(provider.calls, 1)
                self.assertEqual(
                    response["decision_suggestion"],
                    case["expected_decision"],
                )
                self.assertEqual(
                    [item["evidence_id"] for item in evidence_items],
                    case["expected_evidence_ids"],
                )
                self.assertEqual(
                    [item["rule_id"] for item in rule_matches],
                    case["expected_rule_matches"],
                )
                catalog_ids = {
                    item["evidence_id"] for item in case["evidence_catalog"]
                }
                self.assertTrue(valid_evidence_ids.issubset(catalog_ids))
                self.assertTrue(
                    all(
                        not item.get("rule_id")
                        or item["rule_id"]
                        in pipeline._stage_rule_ids("fusion_audit")
                        for item in evidence_items
                    )
                )
                self.assertTrue(
                    all(
                        evidence_id in catalog_ids
                        for match in rule_matches
                        for evidence_id in match["evidence_ids"]
                    )
                )

        third_party_rule = next(
            rule
            for rule in self.compiled["rule_snapshot"]["decision_rules"]
            if rule["rule_id"] == "gambling.comment_organized_participation"
        )
        self.assertIn("不直接归责", third_party_rule["adjudication_notes"])


if __name__ == "__main__":
    unittest.main()
