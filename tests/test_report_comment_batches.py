from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import runpy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from hermes_m0.comment_batches import acknowledge, reconcile, render
from hermes_m0.reference_state import save
from hermes_m0.runtime import configure_real_report_runtime
from hermes_m0.unified_support import unified_tool_schemas
from hermes_m0.pass_support import pass_tool_schemas

seed = runpy.run_path(str(Path(__file__).with_name("test_pass_report.py")))


@pytest.fixture
def setup(tmp_path, request):
    comments = getattr(request, "param", None) or [{"comment_id": f"c-{i:03}", "content": f"评论原文{i:03}",
                 "nickname": f"作者{i:03}", "sec_uid": f"user-{i}",
                 "audit_status": "completed", "risk_level": "high" if i % 2 else "none",
                 "risk_type": "test" if i % 2 else "none", "create_time": 1788825600 + i}
                for i in range(300)]
    source, store = seed["seed_audit"](tmp_path, comments=comments, decision="review", risk="medium")
    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3")
    version = store.get_version(result.report_version_id)
    snapshot = store.get_source_snapshot(result.report_version_id)

    def reopen():
        service = configure_real_report_runtime(
            store.db_path, report_version_id=result.report_version_id,
            expected_database_sha256=hashlib.sha256(store.db_path.read_bytes()).hexdigest(),
            expected_content_hash=version["content_hash"], expected_snapshot_hash=snapshot["snapshot_hash"],
            ledger_path=tmp_path / "ledger.sqlite3")
        service.bind_session("session")
        return service

    service = reopen()
    overview = json.loads(service.dispatch("read_report", {}, session_id="session"))
    post = overview["data"]["post_previews"][0]["ref"]
    save(service, "session")
    return service, post, reopen, store, result.report_version_id


def invoke(service, post, turn="t1", **args):
    return json.loads(service.dispatch("list_post_comments", {"post_ref": post, **args},
                                       session_id="session", turn_id=turn))


def test_three_batches_survive_restart_without_duplicates(setup):
    service, post, reopen, *_ = setup
    bodies = []
    for i in range(3):
        turn = f"turn-{i}"
        response = invoke(service, post, turn, batch_action="start" if i == 0 else "continue")
        assert response["ok"], response
        data = response["data"]
        assert (data["start"], data["end"], data["returned_count"]) == (i * 100 + 1, (i + 1) * 100, 100)
        assert data["remaining_count"] == 200 - 100 * i
        assert "comments" not in data  # no 100-comment echo into the provider context
        text = render(service, "session", turn)
        bodies += re.findall(r"评论原文\d{3}", text)
        assert len(re.findall(r"^\d+\. ", text, re.M)) == 100
        assert invoke(service, post, turn, batch_action="continue") == response
        assert not invoke(service, post, turn, limit=20)["ok"]
        acknowledge(service, "session", turn)
        service = reopen()
    assert len(bodies) == len(set(bodies)) == 300
    end = invoke(service, post, "end", batch_action="continue")["data"]
    assert end["returned_count"] == 0 and not end["has_more"]
    assert "没有更多" in render(service, "session", "end")


def test_preview_unchanged_and_does_not_start_progress(setup):
    service, post, *_ = setup
    assert invoke(service, post)["data"]["returned_count"] == 5
    assert invoke(service, post, limit=20)["data"]["returned_count"] == 20
    assert not invoke(service, post, limit=100)["ok"]
    assert render(service, "session", "t1") is None
    assert invoke(service, post, batch_action="continue")["error"]["code"] == "comment_batch_not_started"


def test_comment_source_context_is_tool_local_and_present_in_preview_and_batch(setup):
    from hermes_m0.comment_batches import COMMENT_SOURCE_CONTEXT
    from hermes_m0.unified_support import UNIFIED_REPORT_SYSTEM_PROMPT

    service, post, *_ = setup
    schema = next(s for s in unified_tool_schemas() if s["name"] == "list_post_comments")
    assert COMMENT_SOURCE_CONTEXT in schema["description"]
    assert COMMENT_SOURCE_CONTEXT not in UNIFIED_REPORT_SYSTEM_PROMPT
    legacy = next(s for s in pass_tool_schemas() if s["name"] == "list_post_comments")
    assert COMMENT_SOURCE_CONTEXT not in legacy["description"]
    preview = invoke(service, post, "preview", risk_filter="no_risk")
    batch = invoke(service, post, "batch", risk_filter="no_risk", batch_action="start")
    for response in (preview, batch):
        assert COMMENT_SOURCE_CONTEXT in response["authority"]["limitations"]
    assert preview["data"]["returned_count"] == 5
    assert batch["data"]["returned_count"] == 100
    assert "comments" not in batch["data"]
    assert "平台以报告记录为准" in COMMENT_SOURCE_CONTEXT
    assert "不保证内容绝对合规" in COMMENT_SOURCE_CONTEXT


def test_filter_first_and_filter_progress_is_separate(setup):
    service, post, *_ = setup
    first = invoke(service, post, batch_action="start", risk_filter="risk")
    assert first["data"]["matched_count"] == 150
    assert all(c["risk_level"] == "high" for c in service.comment_batch_states["session"]["pages"]["t1"]["comments"])
    assert invoke(service, post, risk_filter="all", batch_action="continue")["error"]["code"] == "comment_batch_turn_limit"
    acknowledge(service, "session", "t1")
    assert not invoke(service, post, "t2", risk_filter="all", batch_action="continue")["ok"]
    next_page = invoke(service, post, "t2", risk_filter="risk", batch_action="continue")["data"]
    assert (next_page["start"], next_page["end"], next_page["returned_count"]) == (101, 150, 50)


def test_empty_match_is_not_reported_as_all_comments_empty(setup):
    service, post, *_ = setup
    response = invoke(service, post, batch_action="start", risk_filter="unknown")["data"]
    assert response["matched_count"] == 0 and response["returned_count"] == 0
    assert "当前筛选下没有" in render(service, "session", "t1")


@pytest.mark.parametrize("setup", [[
    {"comment_id": "safe", "content": "审核已完成且无风险", "audit_status": "completed", "risk_level": "none"},
    {"comment_id": "risky", "content": "审核已完成且高风险", "audit_status": "completed", "risk_level": "high"},
    {"comment_id": "pending", "content": "未审核但字段为none", "audit_status": "pending", "risk_level": "none"},
    {"comment_id": "failed", "content": "审核失败但字段为none", "audit_status": "failed", "risk_level": "none"},
]], indirect=True)
def test_batch_filters_never_treat_pending_or_failed_as_safe(setup):
    service, post, *_ = setup
    for risk, count, bodies in (
        ("no_risk", 1, {"审核已完成且无风险"}),
        ("risk", 1, {"审核已完成且高风险"}),
        ("unknown", 2, {"未审核但字段为none", "审核失败但字段为none"}),
    ):
        response = invoke(service, post, risk, batch_action="start", risk_filter=risk)
        assert response["ok"], response
        assert response["data"]["matched_count"] == count
        page = service.comment_batch_states["session"]["pages"][risk]
        assert {c["content"] for c in page["comments"]} == bodies


def test_missing_turn_identity_cannot_create_batch(setup):
    service, post, *_ = setup
    assert not invoke(service, post, turn="", batch_action="start")["ok"]
    assert not service.comment_batch_states.get("session", {}).get("pages")


def test_failed_or_interrupted_answer_does_not_advance(setup):
    service, post, reopen, *_ = setup
    first = invoke(service, post, batch_action="start")
    service = reopen()
    reconcile(service, "session", SimpleNamespace(get_turn=lambda _: SimpleNamespace(status="interrupted")))
    assert invoke(service, post, "new", batch_action="continue")["error"]["code"] == "comment_batch_not_started"
    assert invoke(service, post, batch_action="start") == first
    # Simulate crash after completing the public answer, before checkpoint ack.
    reconcile(service, "session", SimpleNamespace(get_turn=lambda _: SimpleNamespace(status="completed")))
    assert invoke(service, post, "new", batch_action="continue")["data"]["start"] == 101


def test_first_turn_full_risk_list_legacy_restore_does_not_create_fake_delivery(setup):
    from hermes_m0.reference_state import restore_legacy_transcript
    service, post, reopen, *_ = setup
    args = {"post_ref": post, "batch_action": "start", "risk_filter": "risk"}
    first = invoke(service, post, **{k:v for k,v in args.items() if k != "post_ref"})
    acknowledge(service, "session", "t1")
    history = [
        {"role": "user", "content": "全部风险评论逐条列出"},
        {"role": "assistant", "tool_calls": [{"id": "call-batch", "function": {
            "name": "list_post_comments", "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": "call-batch", "content": json.dumps(first)},
        {"role": "assistant", "content": "本批列表已准备。"},
    ]
    restore_legacy_transcript(service, "session", history)
    service = reopen()
    assert set(service.comment_batch_states["session"]["pages"]) == {"t1"}
    store = SimpleNamespace(get_turn=lambda turn: SimpleNamespace(status="completed") if turn == "t1" else pytest.fail("synthetic turn lookup"))
    reconcile(service, "session", store)
    preview = invoke(service, post, "t3", risk_filter="no_risk")["data"]
    assert preview["returned_count"] == 5
    assert all(c["audit_status"] == "completed" and c["risk_level"] == "none" for c in preview["comments"])


def test_known_synthetic_pre_fix_record_removed_without_advancing(setup):
    from copy import deepcopy
    service, post, reopen, *_ = setup
    invoke(service, post, batch_action="start")
    acknowledge(service, "session", "t1")
    state = service.comment_batch_states["session"]
    synthetic = deepcopy(state["pages"]["t1"])
    synthetic.pop("acknowledged")
    state["pages"]["reference-restore:0"] = synthetic
    save(service, "session")
    service = reopen()
    reconcile(service, "session", SimpleNamespace(get_turn=lambda _: pytest.fail("must not query synthetic turn")))
    assert set(service.comment_batch_states["session"]["pages"]) == {"t1"}
    assert invoke(service, post, "next", batch_action="continue")["data"]["start"] == 101
    assert not invoke(service, post, "reference-restore:0", batch_action="start")["ok"]


@pytest.mark.parametrize("extra", [{"limit": 100}, {"cursor": "fake"}, {"risk_filter": "bad"}, {"batch_action": "invalid"}])
def test_reject_invalid_batch_arguments(setup, extra):
    service, post, *_ = setup
    assert not invoke(service, post, **{"batch_action": "start", **extra})["ok"]
    assert not service.comment_batch_states.get("session", {}).get("pages")


def test_cross_session_and_generation_reset_reject_old_references(setup):
    service, post, *_ = setup
    invoke(service, post, batch_action="start")
    acknowledge(service, "session", "t1")
    service.bind_session("other")
    assert not json.loads(service.dispatch("list_post_comments", {"post_ref": post, "batch_action": "continue"},
                                           session_id="other", turn_id="t2"))["ok"]
    service.bind_session("session", force_new_generation=True)
    assert not invoke(service, post, "t2", batch_action="continue")["ok"]
    assert not service.comment_batch_states.get("session", {}).get("progress")


def test_plain_text_render_escapes_untrusted_markdown_and_html(setup):
    service, post, *_ = setup
    invoke(service, post, batch_action="start")
    page = service.comment_batch_states["session"]["pages"]["t1"]
    page["comments"][0]["content"] = '<script>x</script> ![x](https://example.com)\n# fake heading'
    text = render(service, "session", "t1")
    assert "<script>" not in text and "![x]" not in text and "\n# fake" not in text
    assert "&lt;script&gt;" in text


def test_batch_schema_only_changes_unified_tool():
    schema = next(s for s in unified_tool_schemas() if s["name"] == "list_post_comments")
    assert schema["parameters"]["required"] == ["post_ref"]
    assert schema["parameters"]["properties"]["limit"]["maximum"] == 20
    legacy = next(s for s in pass_tool_schemas() if s["name"] == "list_post_comments")
    assert "batch_action" not in legacy["parameters"]["properties"]


def test_public_answer_is_full_batch_and_only_completion_acknowledges(setup, tmp_path):
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.store import InvestigationStore
    from backend.investigation.report_query import ReportQueryFacade
    service, _, _, report_store, report_id = setup
    product = HermesInvestigationAgentService(
        report_facade=ReportQueryFacade(db_path=report_store.db_path),
        store=InvestigationStore(tmp_path / "qa.sqlite3"), bind_runtime=False)
    session = product.create_session(report_id)
    service.bind_session(session.id)
    overview = json.loads(service.dispatch("read_report", {}, session_id=session.id))
    post = overview["data"]["post_previews"][0]["ref"]
    turn, _ = product.accept_message(session.id, client_message_id="full", content="全部逐条列出评论")
    receipt = service.dispatch("list_post_comments", {"post_ref": post, "batch_action": "start"},
                               session_id=session.id, turn_id=turn.id)
    assert json.loads(receipt)["ok"]
    expected = render(service, session.id, turn.id)
    product.bind_runtime = True
    product._answer_streamer = Mock()
    with patch("hermes_m0.runtime.report_task_runtime_for_session", return_value=service):
        model_answer = "根据工具统计，匹配评论涉及300个可识别账号。"
        result = product._persist_result(turn.id, {"final_response": model_answer, "completed": True},
            previous_message_count=0, transcript=[{"role": "user", "content": "全部逐条列出评论"},
                                                 {"role": "assistant", "content": model_answer}])
    expected = model_answer + "\n\n---\n\n### 评论原文\n\n" + expected
    assert result.answer == expected
    assert len(re.findall(r"^\d+\. ", result.answer, re.M)) == 100
    assert service.comment_batch_states[session.id]["pages"][turn.id]["acknowledged"]
    product._answer_streamer.finalize.assert_called_once_with(turn.id, expected)
    history_answer = product.store.latest_completed_hermes_transcript(session.id)[-1]["content"]
    assert history_answer.startswith(model_answer)
    assert "1–100" in history_answer
    assert "评论原文000" not in history_answer


def test_missing_report_tools_fail_as_environment_error_without_reading_or_advancing(setup, tmp_path):
    from copy import deepcopy
    from backend.hermes_runtime.adapter import HermesReportToolsUnavailable
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.store import InvestigationStore
    from backend.investigation.report_query import ReportQueryFacade

    service, post, _, report_store, report_id = setup
    invoke(service, post, batch_action="start")
    acknowledge(service, "session", "t1")
    before = deepcopy(service.comment_batch_states)
    product = HermesInvestigationAgentService(
        report_facade=ReportQueryFacade(db_path=report_store.db_path),
        store=InvestigationStore(tmp_path / "qa-no-tools.sqlite3"), bind_runtime=False)
    session = product.create_session(report_id)
    turn, _ = product.accept_message(session.id, client_message_id="continue-no-tools", content="继续")
    with patch.object(product, "_agent", side_effect=HermesReportToolsUnavailable("no tools")):
        result = product.execute_turn(turn.id)
    stored = product.store.get_turn(turn.id)
    assert stored.status == "error"
    assert stored.error_code == "report_tools_unavailable"
    assert not stored.retryable
    assert stored.llm_call_count == 0
    assert "工具未加载完整" in result.answer
    assert "进度保留" in result.answer
    assert service.comment_batch_states == before


def delivery(service, args=None, session="session"):
    return json.loads(service.dispatch("read_comment_delivery", args or {}, session_id=session, turn_id="lookup"))


def test_delivery_progress_only_confirms_public_batches_and_survives_restart(setup):
    from hermes_m0.comment_batches import delivery_context
    service, post, reopen, *_ = setup
    first = invoke(service, post, "z-first", batch_action="start")
    ref = first["data"]["batch_ref"]
    assert delivery_context(service, "session")["confirmed_deliveries"] == []
    assert not delivery(service, {"batch_ref": ref, "position": 1})["ok"]
    acknowledge(service, "session", "z-first")
    service = reopen()
    second = invoke(service, post, "a-second", batch_action="continue")
    assert delivery_context(service, "session")["confirmed_deliveries"][0]["end"] == 100
    acknowledge(service, "session", "a-second")
    service = reopen()
    context = delivery_context(service, "session")
    assert context["latest_batch_ref"] == second["data"]["batch_ref"]
    assert context["confirmed_deliveries"][0]["end"] == 200
    assert "评论原文" not in json.dumps(context, ensure_ascii=False)
    one = delivery(service, {"batch_ref": second["data"]["batch_ref"], "position": 101})
    assert one["ok"], one
    assert one["data"]["comment"]["content"] == "评论原文100"
    account = one["data"]["comment"]["account_ref"]
    assert account
    # The original account drill-through token is still scoped and resolvable.
    repository = service.account_activity._load_repository()
    assert service.account_activity.refs.resolve("session", account, expected_kind="account",
        corpus_revision=repository.corpus_revision).kind == "account"
    assert not delivery(service, {"batch_ref": ref, "position": 101})["ok"]


def test_identical_positions_in_different_risk_lists_do_not_mix(setup):
    service, post, *_ = setup
    refs = {}
    for risk in ("risk", "no_risk"):
        result = invoke(service, post, risk, batch_action="start", risk_filter=risk)
        refs[risk] = result["data"]["batch_ref"]
        assert result["data"]["matched_author_statistics"]["identified_author_count"] == 150
        acknowledge(service, "session", risk)
    high = delivery(service, {"batch_ref": refs["risk"], "position": 1})["data"]["comment"]
    safe = delivery(service, {"batch_ref": refs["no_risk"], "position": 1})["data"]["comment"]
    assert high["risk_level"] == "high" and safe["risk_level"] == "none"
    assert high["content"] != safe["content"]


@pytest.mark.parametrize("setup", [[
    {"comment_id": "a", "content": "甲", "nickname": "同名", "sec_uid": "author-a", "audit_status": "completed", "risk_level": "none"},
    {"comment_id": "b", "content": "乙", "nickname": "同名", "sec_uid": "author-b", "audit_status": "completed", "risk_level": "none"},
    {"comment_id": "c", "content": "丙", "nickname": "同名", "audit_status": "completed", "risk_level": "none"},
]], indirect=True)
def test_matching_author_statistics_never_merge_nicknames_or_guess_missing_ids(setup):
    service, post, *_ = setup
    statistics = invoke(service, post, batch_action="start", risk_filter="no_risk")["data"]["matched_author_statistics"]
    assert statistics["identified_author_count"] == 2
    assert statistics["comments_without_identified_author"] == 1


@pytest.mark.parametrize("args", [{"position": 1}, {"batch_ref": "fake"},
    {"batch_ref": "REAL", "position": 0}, {"batch_ref": "REAL", "position": True},
    {"batch_ref": "REAL", "position": "1"}, {"batch_ref": "REAL", "position": 101},
    {"batch_ref": "REAL", "cursor": "fake"}])
def test_delivery_rejects_guesses_and_invalid_positions(setup, args):
    service, post, *_ = setup
    ref = invoke(service, post, batch_action="start")["data"]["batch_ref"]
    acknowledge(service, "session", "t1")
    assert not delivery(service, {key: ref if value == "REAL" else value for key, value in args.items()})["ok"]


def test_delivery_reference_cannot_cross_sessions_or_reference_generations(setup):
    service, post, *_ = setup
    ref = invoke(service, post, batch_action="start")["data"]["batch_ref"]
    acknowledge(service, "session", "t1")
    service.bind_session("other")
    assert not delivery(service, {"batch_ref": ref, "position": 1}, session="other")["ok"]
    service.bind_session("session", force_new_generation=True)
    assert not delivery(service, {"batch_ref": ref, "position": 1})["ok"]


def test_recent_delivery_directory_is_bounded_and_preserves_old_lookup(setup):
    service, post, reopen, *_ = setup
    refs = []
    for n in range(12):
        ref = invoke(service, post, str(n), batch_action="start")["data"]["batch_ref"]
        acknowledge(service, "session", str(n))
        refs.append(ref)
    service = reopen()
    data = delivery(service)["data"]
    assert data["batch_count"] == 12 and data["older_batches_omitted"]
    assert [p["batch_ref"] for p in data["recent_batches"]] == refs[-10:]
    assert "comments" not in json.dumps(data)
    assert delivery(service, {"batch_ref": refs[0], "position": 1})["ok"]


def test_failed_model_answer_does_not_publish_prepared_batch(setup, tmp_path):
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.store import InvestigationStore
    from backend.investigation.report_query import ReportQueryFacade
    service, _, _, report_store, report_id = setup
    product = HermesInvestigationAgentService(report_facade=ReportQueryFacade(db_path=report_store.db_path),
        store=InvestigationStore(tmp_path / "qa-failed-batch.sqlite3"), bind_runtime=False)
    session = product.create_session(report_id)
    service.bind_session(session.id)
    post = json.loads(service.dispatch("read_report", {}, session_id=session.id))["data"]["post_previews"][0]["ref"]
    turn, _ = product.accept_message(session.id, client_message_id="failed", content="列全部并统计")
    service.dispatch("list_post_comments", {"post_ref": post, "batch_action": "start"}, session_id=session.id, turn_id=turn.id)
    product.bind_runtime = True
    with patch("hermes_m0.runtime.report_task_runtime_for_session", return_value=service):
        result = product._persist_result(turn.id, {"completed": False, "final_response": "Provider failed"},
                                         previous_message_count=0, transcript=None)
    assert result.answer == "Provider failed"
    assert not service.comment_batch_states[session.id]["pages"][turn.id].get("acknowledged")


@pytest.mark.parametrize("model_answer", [
    "第一批如下：\n1. 虚构作者：不存在的评论999。\n2. 虚构作者：不存在的评论998。",
    "| 序号 | 作者 | 原文 |\n| 1 | 虚构作者 | 不存在的评论999 |",
    "原文是：评论原文000，评论原文001。",
    # Captured Qwen failure: it guessed 001..100 for the real 000..099 batch.
    "## 第一批\n" + "\n".join(f"评论原文{i:03}：请核实票价公示。" for i in range(1, 101)),
])
def test_invented_or_duplicate_batch_never_survives_answer_or_history(setup, tmp_path, model_answer):
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.store import InvestigationStore
    from backend.investigation.report_query import ReportQueryFacade
    runtime, _, _, report_store, report_id = setup
    product = HermesInvestigationAgentService(report_facade=ReportQueryFacade(db_path=report_store.db_path),
        store=InvestigationStore(tmp_path / "projection.sqlite3"), bind_runtime=False)
    session = product.create_session(report_id)
    runtime.bind_session(session.id)
    post = json.loads(runtime.dispatch("read_report", {}, session_id=session.id))["data"]["post_previews"][0]["ref"]
    turn, _ = product.accept_message(session.id, client_message_id="batch", content="列出评论")
    runtime.dispatch("list_post_comments", {"post_ref":post,"batch_action":"start"}, session_id=session.id,turn_id=turn.id)
    product.bind_runtime = True
    with patch("hermes_m0.runtime.report_task_runtime_for_session", return_value=runtime):
        result = product._persist_result(turn.id, {"final_response":model_answer,"completed":True},
            previous_message_count=0, transcript=[{"role":"user","content":"列出评论"}, {"role":"assistant","content":model_answer}])
    assert re.findall(r"评论原文\d{3}", result.answer) == [f"评论原文{i:03}" for i in range(100)]
    assert "不存在的评论" not in result.answer
    reopened = InvestigationStore(product.store.db_path)
    history = reopened.latest_completed_hermes_transcript(session.id)
    assert "不存在的评论" not in str(history) and "评论原文000" not in str(history)
    assert "1–100" in history[-1]["content"]
    assert runtime.comment_batch_states[session.id]["pages"][turn.id]["acknowledged"]


@pytest.mark.parametrize("explanation", [
    '已准备好第一批评论。\n**当前进度：**\n- ✅ 已展示：100 条\n- 📋 剩余：130 条\n- 📊 总计：230 条',
    '已展示全部 230 条。\n如需进一步操作：\n- 查看某条评论的完整上下文\n- 按风险等级筛选评论\n- 查看其他帖子的评论',
    '✅ **旅游审核规则 B** 已成功正式保存：\n- 资源名称：旅游审核规则 B\n- 资源版本：1\n- 保存状态：已完成\n以下展示本批评论原文。',
    '| 项目 | 数量 |\n| --- | --- |\n| 已展示 | 100 |\n| 剩余 | 130 |',
    '可以继续：\n1. 查看原帖\n2. 筛选未审核评论',
    '本批进度：\n1. 已展示：100 条\n2. 剩余：130 条',
])
def test_batch_keeps_natural_progress_receipts_and_followups(setup, explanation):
    from backend.hermes_runtime.comment_delivery import compose
    runtime, post, *_ = setup
    invoke(runtime, post, batch_action="start")
    public, history, rejected = compose(runtime, "session", "t1", explanation,
        [{"role": "assistant", "content": explanation}], 0)
    assert not rejected
    assert public.startswith(explanation + "\n\n---")
    assert history[-1]["content"].startswith(explanation)
    assert re.findall(r"评论原文\d{3}", public) == [f"评论原文{i:03}" for i in range(100)]
