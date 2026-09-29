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
    assert f"默认只生成 1 至 {value} 个最终可直接搜索的实际搜索词" in prompts["recall"]
    assert f"每个任务只搜索前 {value} 个实际搜索词" in prompts["recall"]
    assert "词库全部保留、全部用于研判" in prompts["recall"]
    assert f"默认整组 1 至 {value} 个实际搜索词" in prompts["resource"]
    assert f"每个任务只搜索前 {value} 个" in prompts["resource"]
    assert f"默认整组1至{value}个实际词" in prompts["tool"]
    assert f"但每个任务只搜索前{value}个" in prompts["tool"]
    assert f"by default 1 to {value} actual search" in prompts["creation"]
    assert f"searches only the first {value} terms" in prompts["creation"]
    # Saved lexicons are never cut, so user counts and word lists keep precedence.
    assert "用户明确指定数量时按指定数量生成；" in prompts["recall"]
    assert "exact terms take precedence" in prompts["creation"]
    # Every template with a count line (all but the sample-extraction one) carries N.
    counted = [g for g in prompts["profiles"] if f"默认整组" in g and f"1–{value} 个实际搜索词，" in g]
    assert len(counted) == len(prompts["profiles"]) - 1
    assert not any("{search_terms_max}" in g or "上限" in g for g in prompts["profiles"])
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


def test_generation_keeps_every_term_over_the_cap():
    gen, _, _ = generator(three_themes())
    content = gen.generate("lexicon", ResourceGenerationRequest(**REQUEST))
    assert content.search_terms() == [f"主题{t}词{i}" for t, n in ((0, 5), (1, 5), (2, 4)) for i in range(n)]


def test_requested_count_over_cap_is_generated_in_full_and_strict():
    gen, client, _ = generator(three_themes())
    assert len(gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, requested_count=14)).search_terms()) == 14
    sent = json.loads(client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert sent["requested_count"] == 14
    with pytest.raises(ResourceError):
        gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, requested_count=20))


def test_exact_terms_over_cap_are_preserved_in_full():
    terms = [f"原文{i}" for i in range(12)]
    gen, _, _ = generator({"title": "原文", "themes": [
        {"term": "原文主题", "variants": [{"term": t} for t in terms]}]})
    assert gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, exact_terms=terms)).search_terms() == terms
    gen, _, _ = generator({"title": "原文", "themes": [
        {"term": "原文主题", "variants": [{"term": t} for t in terms[:10]]}]})
    with pytest.raises(ResourceError):
        gen.generate("lexicon", ResourceGenerationRequest(**REQUEST, exact_terms=terms))


def test_generated_lexicon_over_cap_is_saved_whole_and_task_searches_first_n(creation_stack):
    tools = creation_stack["tool_service"]
    app = creation_stack["app_service"]
    gen, _, _ = generator(three_themes())
    tools.resource_generator = gen
    ctx = dict(session_id="cap-session", principal=Principal("principal-a"))
    edit = tools.execute("create_lexicon_edit", {"generation_request": REQUEST}, **ctx)
    everything = [f"主题{t}词{i}" for t, n in ((0, 5), (1, 5), (2, 4)) for i in range(n)]
    assert edit["search_terms"] == everything
    note = edit["search_terms_cap"]
    assert note["unsearched_terms"] == everything[10:] and note["search_term_count"] == 14
    assert "已全部保留" in note["message"] and "只搜索前 10 个" in note["message"]
    saved = tools.execute("save_resource", {"edit_id": edit["edit_id"], "expected_version": edit["version"],
                                            "operation_id": "cap-save-whole"}, **ctx)
    read = tools.execute("read_resource", {"kind": "lexicon", "resource_id": saved["resource_id"]}, **ctx)
    assert read["search_terms"] == everything
    # A task built from the saved lexicon searches only the first N.
    args = _t1_temporary_arguments(creation_stack)
    args["configuration"]["investigation"]["recall_plan"] = app.resource_management.resolve_lexicon_ref(
        saved["resource_ref"], **ctx)
    draft = app.create_draft(CreateDraftCommand.model_validate(args), principal=ctx["principal"])
    preview = app.get_confirmation_preview(draft.id, principal=ctx["principal"])
    assert preview.resolved_search_terms == everything[:10]
    assert preview.unused_search_terms == everything[10:]


def test_small_generated_lexicon_has_no_cap_note(creation_stack):
    tools = creation_stack["tool_service"]
    tools.resource_generator = generator()[0]
    edit = tools.execute("create_lexicon_edit", {"generation_request": REQUEST},
                         session_id="cap-small", principal=Principal("principal-a"))
    assert "search_terms_cap" not in edit


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


def test_cap_notice_is_not_repeated_on_read_only_turns(creation_stack):
    args = _t1_temporary_arguments(creation_stack)
    args["configuration"]["investigation"]["recall_plan"]["terms"] = PLAIN
    created = _run_scripted_creation_turn(
        creation_stack, content="用这些词创建调查", actions=[("create_investigation_draft", args)],
        final_response="草案已创建。", client_message_id="cap-once-create",
    )
    notice = "已截取为前 10 个搜索词，未使用：" + "、".join(PLAIN[10:])
    assert notice in created["result"].answer
    draft_id = created["turn"].public_artifact["draft_id"]
    read = _run_scripted_creation_turn(
        creation_stack, content="看看草案", session_id=created["session_id"],
        actions=[("get_investigation_draft", {"draft_id": draft_id})],
        final_response="这是当前草案。", client_message_id="cap-once-read",
    )
    assert read["turn"].public_artifact["confirmation_preview"]["search_terms_notice"] == notice
    assert notice not in read["result"].answer
