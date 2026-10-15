"""Prompt placement, real deferred discovery and session-isolation fences."""
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.hermes_runtime.adapter import HermesRuntimeBinding
from backend.hermes_runtime.creation_prompt import CREATION_IDENTITY, install_creation_prompt
from backend.investigation_creation.conversation import CREATION_SYSTEM_PROMPT
from backend.investigation_creation.tools import HERMES_M3_TOOL_SCHEMAS, creation_tool_schemas
from backend.resource_management.authoring_guidance import (
    LEXICON_DOMAIN_GUIDANCE, RESOURCE_EDIT_GUIDANCE, RULESET_AUTHORING_GUIDANCE,
)
from backend.resource_management.recall_prompt import RECALL_GENERATION_PROMPT
from backend.resource_management.tools import RESOURCE_PROMPT
from backend.resource_management.generation import generation_messages
from backend.resource_management.generation_contracts import ResourceGenerationRequest


def _agent(mode):
    with patch.object(HermesRuntimeBinding, "activate_product_mode"):
        return HermesRuntimeBinding().create_agent(
            session_id="isolation-test", product_mode=mode,
            agent_factory=lambda **kwargs: SimpleNamespace(_api_max_retries=3),
        )


def test_product_builder_is_creation_only_and_not_a_shared_patch():
    report = _agent("account-activity")
    creation = _agent("creation")
    other = _agent("creation")
    assert not hasattr(report, "_creation_product_system_message")
    assert not hasattr(report, "_build_system_prompt")
    product = CREATION_SYSTEM_PROMPT + RESOURCE_PROMPT
    rendered = creation._build_system_prompt(product)
    assert rendered == CREATION_IDENTITY + "\n\n" + product
    assert creation._cached_system_prompt_static == CREATION_IDENTITY
    assert creation._build_system_prompt() == rendered
    assert creation._build_system_prompt_parts()["context"] == product
    assert "Host: macOS" not in rendered
    assert "You are Hermes Agent" not in rendered
    assert "Python toolchain:" not in rendered
    with pytest.raises(ValueError):
        other._build_system_prompt()
    assert creation._api_max_retries == other._api_max_retries == 1


@pytest.mark.parametrize("message", ["", "   ", 42, {}])
def test_missing_product_instructions_fail_closed(message):
    agent = SimpleNamespace()
    install_creation_prompt(agent)
    with pytest.raises(ValueError):
        agent._build_system_prompt(message)


def test_guidance_is_available_through_native_deferred_describe(tmp_path):
    from tools.tool_search import dispatch_tool_describe

    binding = HermesRuntimeBinding()
    described = {}
    with binding.product_mode_execution(tmp_path / "hermes", product_mode="creation"):
        definitions = binding.tool_definitions(
            enabled_toolsets=["investigation"], product_mode="creation",
        )
        wire_schemas = {item["function"]["name"]: item["function"] for item in definitions}
        active_schemas = creation_tool_schemas()
        active_names = {schema["name"] for schema in active_schemas}
        hidden_names = {schema["name"] for schema in HERMES_M3_TOOL_SCHEMAS} - active_names
        assert hidden_names == {
            "create_investigation_draft", "update_investigation_draft", "use_ruleset_proposal",
        }
        # Native read-only help/report discovery is registered alongside the
        # product catalog; it must not expose legacy mutation entry points.
        assert set(wire_schemas) == active_names | {"list_completed_reports", "read_user_manual"}
        for name in hidden_names:
            result = json.loads(dispatch_tool_describe(
                {"name": name}, current_tool_defs=definitions,
            ))
            assert "error" in result and "parameters" not in result
        for schema in active_schemas:
            name = schema["name"]
            result = json.loads(dispatch_tool_describe(
                {"name": name}, current_tool_defs=definitions,
            ))
            assert result["name"] == name, result
            # Hermes normalizes nullable Pydantic fields for the provider.
            # Verify describe returns the exact registered wire schema.
            assert result["parameters"] == wire_schemas[name]["parameters"]
            assert set(result["parameters"]["properties"]) == set(schema["parameters"]["properties"])
            assert result["parameters"].get("required", []) == schema["parameters"].get("required", [])
            assert result["description"] == schema["description"]
            described[name] = result["description"]
    assert len(described) == 18
    assert "configure_investigation_draft" in described
    assert "import_resource" in described
    assert 'save_draft_lexicon' in described
    assert "generation_request" in described["create_lexicon_edit"]
    assert "generation_request" in described["create_ruleset_proposal"]
    request = ResourceGenerationRequest(objective="调查", platform="dy")
    assert RECALL_GENERATION_PROMPT in generation_messages("lexicon", request)[0]["content"]
    assert LEXICON_DOMAIN_GUIDANCE in generation_messages("lexicon", request)[0]["content"]
    assert RULESET_AUTHORING_GUIDANCE in generation_messages("ruleset", request)[0]["content"]
    for name in ("update_ruleset_proposal", "update_resource_edit"):
        assert RULESET_AUTHORING_GUIDANCE.strip() in described[name]
    for name in ("open_resource_edit", "update_resource_edit"):
        assert RESOURCE_EDIT_GUIDANCE.strip() in described[name]
    for name in ("save_resource", "get_resource_save", "query_investigation_options"):
        assert RULESET_AUTHORING_GUIDANCE not in described[name]
        assert RECALL_GENERATION_PROMPT not in described[name]


def test_global_prompt_keeps_authority_but_not_authoring_payloads():
    product = CREATION_SYSTEM_PROMPT + RESOURCE_PROMPT
    assert len(product) < 21000
    assert RULESET_AUTHORING_GUIDANCE not in product
    assert LEXICON_DOMAIN_GUIDANCE not in product
    assert RESOURCE_EDIT_GUIDANCE not in product
    assert RECALL_GENERATION_PROMPT not in product
    for boundary in (
        "do not repeat their generation",
        "Use configure_investigation_draft for both create and update",
        "First show the complete actual rule version, then wait for a LATER user message explicitly adopting it.",
        "A reference does not grant consent.",
        "if changed, do not silently adopt the new version.",
        "confirm_and_queue_investigation after an explicit",
        "get_resource_save",
        "保存不等于采用或启动",
        "没有成功回执不能宣称完成",
        "版本冲突先读取并核对",
    ):
        assert boundary in product
    assert "不强制增加资源工具" not in product
    assert "由本段替代" not in product


def test_formal_lexicon_prompt_uses_backend_bound_references_not_model_hashes():
    product = CREATION_SYSTEM_PROMPT + RESOURCE_PROMPT
    assert "expected_runtime_content_hash" not in product
    assert "Save existing_lexicon with its ID" not in product
    for instruction in (
        "pass its resource_ref as lexicon_ref to configure_investigation_draft",
        "Copy exact returned references.",
        "read the selected resource first",
        "The backend binds and validates the exact resource version",
        "runtime fingerprint, access permissions, and actual search-term snapshot",
        "rather than silently adopting a newer version",
        "configure_investigation_draft(operation=update) with its latest",
        "Never flatten a structured Drawer edit",
    ):
        assert instruction in CREATION_SYSTEM_PROMPT


# The frozen baseline fixture stays untouched; these lines changed with the
# 2026-09-29 per-task search-term cap (SEARCH_TERMS_MAX, default 10).
_CAP_REASON = "2026-09-29 单任务搜索词上限 SEARCH_TERMS_MAX：生成要求改为 1–N 个，候选不足允许更少。"
SEARCH_TERMS_CAP_CHANGED_LINES = [
    {"source": source, "line": line, "text": text, "reason": _CAP_REASON}
    for offset, source in ((0, "RECALL_GENERATION_PROMPT"), (36, "RESOURCE_PROMPT"))
    for line, text in (
        (4 + offset, "用户未指定数量时，默认只生成 5 至 10 个最终可直接搜索的实际搜索词，按整组去重后计数，"),
        (5 + offset, "不是每个主题各生成 5 至 10 个。主题主词按语义归类需要生成，不计入搜索词数量，标签也不计入。"),
        (7 + offset, "可靠候选不足时宁可少于 5 个，也不能用停用词或低价值词凑数。"),
    )
]

# 2026-09-30: task parameters come only from 采集与分析设置; chat can no longer change them.
_LIMITS_REASON = "2026-09-30 采集与分析设置为唯一来源，聊天不能改参数：删除把用户所说数量写入 task_parameters 的要求。"
TASK_SETTINGS_ONLY_CHANGED_LINES = [
    {"source": "CREATION_SYSTEM_PROMPT", "line": line, "text": text, "reason": _LIMITS_REASON}
    for line, text in (
        (61, "Draft configuration may include task_parameters for per-run execution choices. Preserve every"),
        (62, "explicit user choice for post count, comments per post, sub-comments, concurrency, media collection,"),
        (63, "automatic analysis, analysis limit, batch size, page, or rate limit in configuration.task_parameters."),
        (64, "In particular, an explicit request not to collect media must set collect_media=false; never report"),
        (65, "that such a choice cannot be represented. Omit task_parameters only when the user did not specify"),
        (66, "per-run execution choices. crawler_account_id remains application-managed and must not be supplied."),
    )
]


# 2026-10-08: main fallback is the product rule, not only legacy compatibility.
# Account for these exact phrases; preserve every other baseline constraint.
SEARCH_FALLBACK_WORDING = {
    "某个主词没有启用变体时，才兼容性地回退使用该主词。":
        "某个主词没有启用变体时，回退使用该主词；主词停用时整组不参与搜索。",
    "变体时才为兼容旧词库回退到主词。":
        "变体时回退到主词；主词停用时整组不参与搜索。",
}


# R08: replace only the four full-replacement instructions with local-edit guidance.
PRECISE_EDIT_CHANGED_LINES = [
    {
        "source": "CREATION_SYSTEM_PROMPT",
        "line": 169,
        "text": "For edits, use current Proposal content (get_ruleset_proposal if needed), then send its proposal_id,",
        "reason": "Use existing version-checked partial edits; retain IDs, unrelated fields, stale-version checks and natural replies."
    },
    {
        "source": "CREATION_SYSTEM_PROMPT",
        "line": 170,
        "text": "expected_version and the full revised 审核规则完整内容 to update_ruleset_proposal. Application owns",
        "reason": "Use existing version-checked partial edits; retain IDs, unrelated fields, stale-version checks and natural replies."
    },
    {
        "source": "CREATION_SYSTEM_PROMPT",
        "line": 171,
        "text": "content_hash; do not supply it. Preserve category_id, rule_id and ordering for unchanged semantics;",
        "reason": "Use existing version-checked partial edits; retain IDs, unrelated fields, stale-version checks and natural replies."
    },
    {
        "source": "CREATION_SYSTEM_PROMPT",
        "line": 172,
        "text": "do not rewrite unrelated 审核规则 without reason. On stale version, read the current Proposal and",
        "reason": "Use existing version-checked partial edits; retain IDs, unrelated fields, stale-version checks and natural replies."
    }
]



# 2026-10-09: user-authorized prose simplification. Exact baseline lines remain
# accounted for; authorization and version guards are not exempted by this list.
READABILITY_CHANGED_RANGES = {
    "CREATION_SYSTEM_PROMPT": [
        (range(0, 4), "Chinese introduction retains application-owned proposal presentation; adds natural language and concrete coverage guidance."),
        (range(68, 76), "Condensed suitability check retains independent rule/term assessment, explicit resource choice and missing-resource authorization."),
        (range(97, 100), "Removed fixed reply example; preceding no-generation/no-Draft authorization boundary is unchanged."),
        (range(105, 108), "Structured generation details remain in dedicated generation guidance and tool schema; coordinator consumes exact recall_plan."),
    ],
    "RESOURCE_PROMPT": [
        ([62, 64, 65], "Removed duplicate Draft-save instructions; full latest-revision, exact-snapshot and frozen-task constraints remain in CREATION_SYSTEM_PROMPT."),
    ],
}

# 2026-10-12: promote the measured candidate as the default, retaining the
# immutable original fixture and explicitly accounting for condensed wording.
READABILITY_CHANGED_RANGES["CREATION_SYSTEM_PROMPT"] += [
    ([129, 130], "Generation authorization and sufficient-context behavior consolidated into the five resource principles."),
    ([173, 174], "Independent candidates and natural successful replies retained in the shorter English paragraph."),
]
READABILITY_CHANGED_RANGES["RESOURCE_PROMPT"] += [
    (range(1, 10), "Public names, tool-backed resource creation, temporary drafts and completing both resources retained in candidate opening paragraphs."),
    ([29, 31, 32], "Explicit save authorization, one-step publish and saving existing edit references retained in candidate save paragraph."),
    ([34, 35], "Variant-first search, fallback, disabled groups and non-search tags retained in candidate lexicon paragraph."),
    (range(66, 72), "Exact search terms, authorized expansion and task-size distinction retained in candidate search paragraph."),
    ([95, 96, 97, 98, 101, 102, 103, 104, 105, 106], "Adoption/start authorization, exact recall refs, independent save receipts, frozen tasks and conflict recovery retained in candidate closing paragraphs; multi-round behavior remains application-owned."),
]


# 2026-10-15 merge audit: these stale baseline assertions already fail on
# a4997d3 (4 failures / 6 passes). Preserve the immutable fixture and account
# for each changed line with an asserted replacement behavior, not a blanket waiver.
CONFIGURATION_CONTRACT_CHANGED_LINES = {
    38: ('Unified configuration entry point replaces legacy create name; read-only final replies still need no canonical JSON.', 'configure_investigation_draft. A final assistant answer does not need canonical decision JSON.'),
    78: ('Creator URL validation retained; tool accepts creator_url directly.', 'URL, never a post URL, post ID, or arbitrary webpage. Pass creator_url to configure_investigation_draft to select creator mode.'),
    79: ('Backend now assembles creator mode configuration from creator_url.', 'Creator mode\nmust not carry search terms, source 黑话库, or recall plans, and must resolve to crawl_mode=creator.'),
    110: ('Generation-turn adoption ban uses the current configuration tool.', 'Do not call configure_investigation_draft in that\ngeneration turn.'),
    112: ('Later adoption binds exact resource references through configuration.', 'LATER explicit adoption; then configure_investigation_draft creates the Draft with the selected resource references.'),
    131: ('Session-candidate lifecycle wording condensed; no generation-time publishing.', 'Temporary rules are session candidates, not published resources.'),
    132: ('Generation/editing cannot adopt; presentation precedes later consent.', 'Generation/editing never adopt them.'),
    133: ('Unified configuration resolves the exact previously displayed version.', 'The backend resolves temporary rules to a completed public presentation of that exact version and'),
    134: ('Inline content is replaced by exact returned references.', 'Do not send null, rule content, hashes or complete configuration.'),
    135: ('User-intent interpretation and backend provenance responsibilities retained.', "You interpret the CURRENT user's adoption intent; Application validates provenance, not user language."),
    136: ('Negative and ambiguous utterance guard condensed.', 'Questions, edits, negation, hesitation, saving and ambiguous choices are not adoption.'),
    137: ('Choice example replaced by the general ambiguous-choice guard.', 'Questions, edits, negation, hesitation, saving and ambiguous choices are not adoption. Clarify only real ambiguity.'),
    138: ('Question/save example replaced by the general non-adoption guard.', 'Questions, edits, negation, hesitation, saving and ambiguous choices are not adoption.'),
    139: ('Multiple-candidate example replaced by the same ambiguity requirement.', 'Clarify only real ambiguity.'),
    140: ('Model passes resource_ref; backend resolves presentation metadata.', 'Read the selected rules through read_resource or get_resource_edit to obtain ruleset_ref = resource_ref;'),
    141: ('Presentation lookup moved behind resource_ref resolution.', 'The backend resolves temporary rules to a completed public presentation of that exact version and'),
    142: ('Completed exact-version public presentation and later adoption still required.', 'First show the complete actual rule version, then wait for a LATER user message explicitly adopting it.'),
    143: ('Untrusted identity substitution prohibited by reference contract.', 'Do not use a proposal ID,\npresentation ID, edit ID or library ID as a resource_ref. Copy exact returned references.'),
    144: ('Exact copying retained for backend-returned resource references.', 'Copy exact returned references.'),
    145: ('No internal identities in public answer retained.', 'Do not expose internal references in the answer.'),
    146: ('Reference and presentation do not constitute user consent.', 'A reference does not grant consent.'),
    147: ('Missing presentation requires display then later adoption.', 'If not presented, show the\nactual current rules and wait for later adoption;'),
    148: ('Exact-version checks replace model-side presentation ID repair.', 'if changed, do not silently adopt the new version.'),
    149: ('Create/update arguments moved to unified configuration schema.', 'Update uses operation=update with draft_id/expected_revision and ONLY changed fields; omitted fields\nremain unchanged.'),
    150: ('Partial updates preserve established configuration.', 'ONLY changed fields; omitted fields\nremain unchanged.'),
    151: ('No placeholders or unrelated substitutes retained.', 'Never create placeholder drafts or substitute an\nunrelated resource.'),
    152: ('Existing complete identities may be reused instead of unnecessary discovery.', 'Complete resource identities already\npresent in the conversation context may be reused; Application will reread and authoritatively\nvalidate them during creation.'),
    153: ('Ambiguity and changed-version adoption guards retained.', 'if changed, do not silently adopt the new version.'),
    154: ('Later adoption remains mandatory after displaying current rules.', 'If not presented, show the\nactual current rules and wait for later adoption;'),
    155: ('Revision conflict still requires reread and assessment before retry.', 'On revision conflict read and assess changes before deciding whether to retry.'),
    161: ('Viewing is read-only instead of no-op update/re-presentation.', 'For viewing or re-presentation, read the requested resource version. Do not open or modify an edit merely to answer a read request.'),
}


def _current_fallback_wording(text):
    for old, new in SEARCH_FALLBACK_WORDING.items():
        text = text.replace(old, new)
    return text


def test_original_prompt_lines_are_preserved_or_explicitly_accounted_for():
    """A wording move cannot silently delete an old behavioral/quality constraint."""
    from backend.investigation_creation.tools import M3_TOOL_DESCRIPTIONS
    baseline = json.loads((Path(__file__).parent / "fixtures" /
                           "creation_prompt_baseline_1c909ad.json").read_text())
    normalize = lambda text: re.sub(r"\s+", "", text)
    current = normalize("\n".join([
        CREATION_SYSTEM_PROMPT, RESOURCE_PROMPT, RECALL_GENERATION_PROMPT,
        RULESET_AUTHORING_GUIDANCE, LEXICON_DOMAIN_GUIDANCE, RESOURCE_EDIT_GUIDANCE,
        *M3_TOOL_DESCRIPTIONS.values(),
    ]))
    readability_changes = [
        {"source": source, "line": index,
         "text": baseline["prompts"][source].splitlines()[index], "reason": reason}
        for source, ranges in READABILITY_CHANGED_RANGES.items()
        for indexes, reason in ranges for index in indexes
    ]
    configuration_changes = []
    for index, (reason, replacement) in CONFIGURATION_CONTRACT_CHANGED_LINES.items():
        assert normalize(replacement) in current, (index, reason, replacement)
        configuration_changes.append({
            "source": "CREATION_SYSTEM_PROMPT", "line": index,
            "text": baseline["prompts"]["CREATION_SYSTEM_PROMPT"].splitlines()[index],
            "reason": reason,
        })
    changes = {(item["source"], item["line"]): item
               for item in baseline["changed_lines"] + SEARCH_TERMS_CAP_CHANGED_LINES
               + TASK_SETTINGS_ONLY_CHANGED_LINES + PRECISE_EDIT_CHANGED_LINES
               + readability_changes + configuration_changes}
    for name, original in baseline["prompts"].items():
        for index, line in enumerate(original.splitlines()):
            if not line.strip():
                continue
            change = changes.get((name, index))
            if change:
                assert change["text"] == line and change["reason"]
            else:
                assert normalize(_current_fallback_wording(line)) in current, (name, index, line)
    # Preserve the identity paragraph apart from the explicit fallback wording
    # and its previously accounted-for final lifecycle sentence.
    original = baseline["prompts"]["CREATION_SYSTEM_PROMPT"].splitlines()[7]
    assert _current_fallback_wording(original.split("临时词库只写入")[0]) in CREATION_SYSTEM_PROMPT


def test_precise_edit_guidance_retains_version_fence_and_natural_style():
    from backend.resource_management.tools import RESOURCE_DESCRIPTIONS
    assert '只提交指定字段或条目变更，保留其余内容、编号和顺序' in RESOURCE_PROMPT
    assert '版本冲突先读取并核对，不强行写入' in RESOURCE_PROMPT
    assert 'edit_id/expected_version' in RESOURCE_DESCRIPTIONS['update_resource_edit']
    assert 'Explain the actual result naturally after a successful tool call.' in CREATION_SYSTEM_PROMPT
