"""Per-task search-term cap: one helper for generation, task creation and legacy jobs."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.audit_agent.config import settings
from backend.audit_agent.search_terms_cap import cap_search_terms, unused_terms_notice
from backend.investigation_creation.contracts import ConfirmAndQueueCommand, CreateDraftCommand
from backend.investigation_creation.principal import Principal
from backend.resource_management.contracts import LexiconContent, ResourceError
from backend.resource_management.generation_contracts import ResourceGenerationRequest
from test_investigation_creation_conversation import creation_stack, _t1_temporary_arguments
from test_investigation_creation_conversation import _run_scripted_creation_turn
from test_resource_generation import REQUEST, generator
from test_ruleset_proposal_approval import approve

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def cap_ten(monkeypatch):
    monkeypatch.setattr(settings, "search_terms_max", 10)


def test_helper_keeps_order_dedupes_and_reads_settings(monkeypatch):
    terms = [f"词{i}" for i in range(12)]
    assert cap_search_terms(terms) == (terms[:10], terms[10:])
    assert cap_search_terms(["乙", "甲", "乙", "丙"], 2) == (["乙", "甲"], ["丙"])
    assert cap_search_terms(["甲", "乙"]) == (["甲", "乙"], [])
    monkeypatch.setattr(settings, "search_terms_max", 1)
    assert cap_search_terms(["甲", "乙", "丙"]) == (["甲"], ["乙", "丙"])
    assert unused_terms_notice(["乙", "丙"]) == "已截取为前 1 个搜索词，未使用：乙、丙"
    assert unused_terms_notice([]) == ""


def _fresh_python(code: str, value: str) -> str:
    env = {**os.environ, "SEARCH_TERMS_MAX": value}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True,
                          capture_output=True, text=True).stdout


@pytest.mark.parametrize("value,expected", [("6", 6), ("0", 1), ("999", 200)])
def test_setting_is_clamped(value, expected):
    code = "from backend.audit_agent.config import settings; print(settings.search_terms_max)"
    assert _fresh_python(code, value).strip() == str(expected)


PROMPT_SOURCES = """
import json
from backend.resource_management.recall_prompt import RECALL_GENERATION_PROMPT
from backend.resource_management.tools import RESOURCE_PROMPT, RESOURCE_DESCRIPTIONS
from backend.resource_management.keyword_profiles import PROFILES
from backend.investigation_creation.conversation import CREATION_SYSTEM_PROMPT
print(json.dumps({"recall": RECALL_GENERATION_PROMPT, "resource": RESOURCE_PROMPT,
                  "tool": RESOURCE_DESCRIPTIONS["create_lexicon_edit"],
                  "creation": CREATION_SYSTEM_PROMPT,
                  "profiles": [p.guidance for p in PROFILES.values()]}, ensure_ascii=False))
"""


@pytest.mark.parametrize("value", ["10", "6"])
def test_prompts_are_formatted_with_the_setting(value):
    prompts = json.loads(_fresh_python(PROMPT_SOURCES, value))
    assert f"只生成 1 至 {value} 个最终可直接搜索的实际搜索词" in prompts["recall"]
    assert f"单任务实际搜索词上限为 {value} 个" in prompts["recall"]
    assert f"整组 1 至 {value} 个实际搜索词" in prompts["resource"]
    assert f"整组1至{value}个实际词" in prompts["tool"]
    assert f"by default 1 to {value} actual search" in prompts["creation"]
    assert f"exact terms are honoured up to {value}" in prompts["creation"]
    assert f"默认只生成 1 至 {value} 个" in prompts["recall"]
    assert f"用户明确指定数量时按指定数量生成，最多 {value} 个" in prompts["recall"]
    # No sentence may still claim that counts or word lists override the cap.
    assert "take precedence" not in prompts["creation"]
    assert "截断" not in prompts["recall"]
    assert f"上限内用户明确数量/原文优先" in prompts["resource"]
    # Every template with a count line (all but the sample-extraction one) carries N.
    counted = [g for g in prompts["profiles"] if f"默认整组" in g
               and f"1–{value} 个实际搜索词（上限 {value} 个）" in g and "优先，但不超过上限。" in g]
    assert len(counted) == len(prompts["profiles"]) - 1
    assert not any("{search_terms_max}" in g for g in prompts["profiles"])
    texts = [prompts["recall"], prompts["resource"], prompts["tool"], prompts["creation"],
             *prompts["profiles"]]
    for text in texts:
        for obsolete in ("5 至 10", "5–10", "少于 5 个"):
            assert obsolete not in text


def test_no_old_count_left_in_prompt_sources():
    for relative in ("backend/resource_management/recall_prompt.py",
                     "backend/resource_management/keyword_profile_catalog.py",
                     "backend/resource_management/keyword_profiles.py",
                     "backend/resource_management/tools.py",
                     "backend/investigation_creation/conversation.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for obsolete in ("5 至 10", "5–10", "少于 5 个"):
            assert obsolete not in text, (relative, obsolete)


def three_themes():
    # 14 variants over 3 themes: 5 + 5 + 4.
    return {"title": "招聘风险", "themes": [
        {"term": f"主题{t}", "variants": [{"term": f"主题{t}词{i}"} for i in range(n)]}
        for t, n in ((0, 5), (1, 5), (2, 4))
    ]}


def test_generation_keeps_first_n_and_drops_emptied_theme():
    gen, _, _ = generator(three_themes())
    reports = []
    content = gen.generate("lexicon", ResourceGenerationRequest(**REQUEST),
                           on_search_terms_capped=reports.append)
    expected = [f"主题{t}词{i}" for t in (0, 1) for i in range(5)]
    assert content.search_terms() == expected
    assert "主题2" not in {entry.term for entry in content.entries}
    assert all(entry.parent_id != "generated-main-3" for entry in content.entries)
    assert reports[0]["unused_terms"] == [f"主题2词{i}" for i in range(4)]
    assert reports[0]["limit"] == 10
    assert "已截取为前 10 个搜索词，未使用：主题2词0、主题2词1、主题2词2、主题2词3" in reports[0]["message"]


def test_generation_partial_theme_keeps_its_first_variants(monkeypatch):
    monkeypatch.setattr(settings, "search_terms_max", 7)
    gen, _, _ = generator(three_themes())
    content = gen.generate("lexicon", ResourceGenerationRequest(**REQUEST))
    assert content.search_terms() == [*(f"主题0词{i}" for i in range(5)), "主题1词0", "主题1词1"]
    assert [e.term for e in content.entries if e.kind == "main"] == ["主题0", "主题1"]


def test_requested_count_over_cap_is_capped_and_reported():
    gen, client, _ = generator(three_themes())
    reports = []
    content = gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, requested_count=20),
                           on_search_terms_capped=reports.append)
    assert len(content.search_terms()) == 10
    assert reports[0]["requested_count"] == 20
    assert "用户要求 20 个，已按上限生成 10 个" in reports[0]["message"]
    sent = json.loads(client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert sent["requested_count"] == 10


def test_requested_count_over_cap_with_fewer_outputs_is_still_reported():
    gen, _, _ = generator({"title": "t", "themes": [
        {"term": "主题", "variants": [{"term": f"词{i}"} for i in range(10)]}]})
    reports = []
    gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, requested_count=15),
                 on_search_terms_capped=reports.append)
    assert reports[0]["unused_terms"] == []
    with pytest.raises(ResourceError):
        generator({"title": "t", "themes": [{"term": "主题", "variants": [{"term": "词"}]}]})[0].generate(
            "lexicon", ResourceGenerationRequest(**REQUEST, requested_count=15))


@pytest.mark.parametrize("echo_all", [True, False])
def test_exact_terms_over_cap_keep_first_n_and_report_rest(echo_all):
    terms = [f"原文{i}" for i in range(12)]
    returned = terms if echo_all else terms[:10]
    gen, client, _ = generator({"title": "原文", "themes": [
        {"term": "原文主题", "variants": [{"term": t} for t in returned]}]})
    reports = []
    content = gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, exact_terms=terms),
                           on_search_terms_capped=reports.append)
    assert content.search_terms() == terms[:10]
    assert reports[0]["unused_terms"] == terms[10:]
    sent = json.loads(client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert sent["exact_terms"] == terms[:10]


def test_exact_terms_reordered_are_still_rejected():
    terms = [f"原文{i}" for i in range(12)]
    gen, _, _ = generator({"title": "原文", "themes": [
        {"term": "原文主题", "variants": [{"term": t} for t in reversed(terms)]}]})
    with pytest.raises(ResourceError):
        gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, exact_terms=terms))


def test_tool_result_carries_cap_message(creation_stack):
    tools = creation_stack["tool_service"]
    gen, _, _ = generator(three_themes())
    tools.resource_generator = gen
    edit = tools.execute("create_lexicon_edit", {"generation_request": REQUEST},
                         session_id="cap-session", principal=Principal("principal-a", role="admin"))
    assert len(edit["search_terms"]) == 10
    assert edit["search_terms_cap"]["unused_terms"] == [f"主题2词{i}" for i in range(4)]


def big_lexicon():
    # 38 enabled variants in 4 themes. One kept term and one unused term are 高:
    # the cap selects the first N in lexicon order, then priority orders them.
    entries = []
    index = 0
    for theme, size in enumerate((10, 10, 10, 8)):
        entries.append({"id": f"m{theme}", "term": f"主题{theme}"})
        for _ in range(size):
            risk = "高" if index in (5, 30) else "中"
            entries.append({"id": f"v{index}", "term": f"词{index}", "kind": "variant",
                            "parent_id": f"m{theme}", "risk_level": risk})
            index += 1
    return LexiconContent.model_validate({"title": "上限测试", "entries": entries})


LEXICON_KEPT = ["词5", *(f"词{i}" for i in range(10) if i != 5)]
LEXICON_UNUSED = [f"词{i}" for i in range(10, 38)]
PLAIN = [f"用户词{i}" for i in range(14)]


@pytest.mark.parametrize("source", ["saved", "temporary", "plain"])
@pytest.mark.parametrize("rules", ["formal", "temporary"])
def test_task_creation_caps_search_terms_for_every_source(creation_stack, source, rules):
    stack = creation_stack
    app = stack["app_service"]
    principal = Principal("principal-a")
    ctx = dict(session_id="cap-task-session", principal=principal)
    manager = app.resource_management
    args = _t1_temporary_arguments(stack)
    if source == "plain":
        plan = {"strategy": "temporary_terms", "terms": PLAIN, "source_lexicon_ids": []}
        kept, unused = PLAIN[:10], PLAIN[10:]
    else:
        lexicon = big_lexicon()
        selected = manager.create_lexicon(lexicon.storage_dict(), **ctx)
        if source == "saved":
            selected = manager.save(selected["edit_id"], 1, mode="new", operation_id="cap-save", **ctx)
        plan = manager.resolve_lexicon_ref(selected["resource_ref"], **ctx)
        kept, unused = LEXICON_KEPT, LEXICON_UNUSED
    args["configuration"]["investigation"]["recall_plan"] = plan
    if rules == "temporary":
        rule_body = json.loads((Path(__file__).parent / "fixtures/recruitment_fraud_ruleset.json").read_text())
        shown = _run_scripted_creation_turn(stack, content="生成并展示临时规则，不启动",
            actions=[("create_ruleset_proposal", {"content": rule_body})])
        args["configuration"].pop("judgement", None)
        args["configuration"].pop("schema_version", None)
        result, _, _ = approve(stack, shown, arguments={"create_draft": args})
        assert result["status"] == "ok", result
        draft = app.get_draft(result["data"]["draft"]["id"], principal=principal)
    else:
        draft = app.create_draft(CreateDraftCommand.model_validate(args), principal=principal)
    stored = draft.configuration.investigation.recall_plan
    stored_terms = stored.enabled_main_terms if source == "saved" else stored.terms
    assert len(stored_terms) == (14 if source == "plain" else 38)  # Draft keeps the full list

    preview = app.get_confirmation_preview(draft.id, principal=principal)
    assert preview.can_confirm, preview.blockers
    assert preview.resolved_search_terms == kept
    assert preview.unused_search_terms == unused
    assert preview.search_terms_notice == "已截取为前 10 个搜索词，未使用：" + "、".join(unused)
    parameters = preview.effective_parameters
    assert preview.estimated_max_contents == min(parameters.max_total_notes, parameters.max_notes * 10)

    run = app.confirm_and_queue(ConfirmAndQueueCommand(
        draft_id=draft.id, expected_revision=draft.current_revision,
        confirmed=True, idempotency_key="cap-confirm"), principal=principal)
    frozen = run.confirmed_configuration
    assert frozen["resolved_search_terms"] == kept
    assert frozen["execution"]["keyword"].split(",") == kept
    assert frozen["execution"]["max_total_notes"] == preview.estimated_max_contents
    if source == "saved":
        assert frozen["recall_plan"]["enabled_main_terms"] == kept
        assert frozen["execution"]["lexicon_keywords"] == kept
        # The full formal lexicon still feeds scoring and judgement.
        assert frozen["execution"]["library_ids"] == [plan["lexicon_id"]]
    else:
        assert frozen["recall_plan"]["temporary_terms"] == kept
    assert frozen["unused_search_terms"] == unused

    # After confirmation the frozen card keeps the notice and the real term count.
    frozen_preview = app.get_confirmation_preview(draft.id, principal=principal)
    assert frozen_preview.resolved_search_terms == kept
    assert frozen_preview.unused_search_terms == unused
    assert frozen_preview.search_terms_notice == preview.search_terms_notice
    if source == "saved":
        assert frozen_preview.recall_plan.enabled_main_term_count == 38


def test_frozen_snapshot_without_cut_keeps_its_historical_shape(creation_stack):
    app = creation_stack["app_service"]
    principal = Principal("principal-a")
    args = _t1_temporary_arguments(creation_stack)
    draft = app.create_draft(CreateDraftCommand.model_validate(args), principal=principal)
    run = app.confirm_and_queue(ConfirmAndQueueCommand(
        draft_id=draft.id, expected_revision=draft.current_revision,
        confirmed=True, idempotency_key="cap-none"), principal=principal)
    assert "unused_search_terms" not in run.confirmed_configuration
    frozen_preview = app.get_confirmation_preview(draft.id, principal=principal)
    assert frozen_preview.search_terms_notice == ""


@pytest.mark.parametrize("failed", [False, True])
def test_draft_answer_always_carries_the_cap_notice(creation_stack, failed):
    args = _t1_temporary_arguments(creation_stack)
    args["configuration"]["investigation"]["recall_plan"]["terms"] = PLAIN
    run = _run_scripted_creation_turn(
        creation_stack, content="用这些词创建调查",
        actions=[("create_investigation_draft", args)],
        final_response="草案已创建。", completed=not failed, failed=failed,
        client_message_id=f"cap-answer-{failed}",
    )
    notice = "已截取为前 10 个搜索词，未使用：" + "、".join(PLAIN[10:])
    assert run["turn"].status == "completed"
    assert run["result"].answer.count(notice) == 1


def _legacy_resolver(tmp_path):
    from backend.audit_agent.audit_policy_store import AuditPolicyStore
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.lexicon_store import LexiconStore
    from backend.investigation_creation.adapters import InvestigationConfigurationResolver
    db_path = tmp_path / "audit.sqlite3"
    return InvestigationConfigurationResolver(
        lexicon_store=LexiconStore(db_path), policy_store=AuditPolicyStore(db_path),
        crawler_account_store=CrawlerAccountStore(db_path),
    )


def _legacy_configuration(keyword_source, keywords):
    from backend.investigation_creation.contracts import InvestigationConfiguration
    return InvestigationConfiguration.model_validate({
        "platform": "xhs",
        "collection": {"crawl_mode": "search", "keyword_source": keyword_source,
                       "keywords": keywords, "run_crawler": True},
        "analysis": {"library_ids": ["soft"], "capabilities": ["text", "comment"],
                     "scoring_template": "balanced"},
    })


def test_legacy_resolver_caps_keyword_and_lexicon_sources(tmp_path, monkeypatch):
    resolver = _legacy_resolver(tmp_path)
    resolved = resolver.resolve(_legacy_configuration("keyword", PLAIN))
    assert resolved["keyword"].split(",") == PLAIN[:10]
    assert resolved["library_ids"] == ["soft"]
    monkeypatch.setattr(resolver, "_enabled_keywords", lambda library_ids: list(PLAIN))
    resolved = resolver.resolve(_legacy_configuration("lexicon", []))
    assert resolved["keyword"].split(",") == PLAIN[:10]
    assert resolved["lexicon_keywords"] == PLAIN[:10]
    assert resolved["library_ids"] == ["soft"]


def test_legacy_resolver_leaves_lists_within_the_cap_untouched(tmp_path):
    # The contract already rejects duplicate keywords; at or under N nothing changes.
    resolver = _legacy_resolver(tmp_path)
    assert resolver.resolve(_legacy_configuration("keyword", ["乙", "甲"]))["keyword"] == "乙,甲"
    assert resolver.resolve(_legacy_configuration("keyword", PLAIN[:10]))["keyword"].split(",") == PLAIN[:10]
