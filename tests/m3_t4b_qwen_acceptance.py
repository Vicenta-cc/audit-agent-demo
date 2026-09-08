"""Isolated real Qwen T4B acceptance; credentials and provider logs are never exported."""

import argparse
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="m3-t4b-qwen-") as temporary:
        root = Path(temporary)
        for name, directory in [("XHS_AUDIT_DATA_DIR", "data"), ("XHS_AUDIT_OUTPUTS_DIR", "outputs"), ("HERMES_HOME", "hermes")]:
            os.environ[name] = str(root / directory)
        from dotenv import dotenv_values
        from backend.audit_agent.config import settings
        credentials = dotenv_values(args.credentials_env)
        settings.dashscope_api_key = credentials.get("DASHSCOPE_API_KEY") or ""
        settings.dashscope_base_url = credentials.get("DASHSCOPE_BASE_URL") or settings.dashscope_base_url
        del credentials
        assert settings.dashscope_api_key, "Provider credentials unavailable"
        from test_investigation_creation_conversation import creation_stack
        from backend.investigation_creation.principal import Principal
        from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools, M3_MUTATION_TOOL_NAMES

        fixture = creation_stack.__wrapped__(root)
        stack = next(fixture)
        conversation = stack["conversation"]
        conversation.fake_runtime = False
        principal = Principal("principal-a")
        configure_hermes_investigation_creation_tools(stack["tool_service"], principal_provider=conversation.principal_for_session)
        # A real relevant Recall resource makes the exact three user messages sufficient.
        lexicons = stack["app_service"].resource_service.lexicon_store
        lexicons.upsert_category(category_id="recruitment-fraud", title="招聘诈骗召回词库",
                                 risk_label="招聘诈骗", terms=["入职收费", "招聘培训贷", "分期培训费"])
        evidence = {"model": "qwen3.7-plus", "stage": "T4B_APPROVAL_SEMANTICS_CORRECTION", "cases": [], "pass": False,
                    "setup": "Isolated fixture includes a relevant recruitment Recall lexicon and supported platforms; no matching formal RuleSet."}

        def snapshot(db, table):
            with sqlite3.connect(db) as connection:
                connection.row_factory = sqlite3.Row
                return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]

        def save():
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")

        def turn(session, label, message):
            before_drafts = snapshot(stack["creation_store"].db_path, "investigation_drafts")
            before_audits = snapshot(stack["creation_store"].db_path, "ruleset_proposal_approvals")
            events = []
            original = stack["tool_service"].execute_with_identity
            original_execute = stack["tool_service"].execute
            def observed_read(name, arguments, **kwargs):
                result = original_execute(name, arguments, **kwargs)
                if name not in M3_MUTATION_TOOL_NAMES:
                    events.append({"name": name, "arguments": deepcopy(arguments), "result": {"status": "ok", "data": result}})
                return result
            def observed(name, arguments, **kwargs):
                result = original(name, arguments, **kwargs)
                events.append({"name": name, "arguments": deepcopy(arguments), "result": result})
                return result
            accepted, _ = conversation.accept_message(session.id, client_message_id=label, content=message, principal=principal)
            with patch.object(stack["tool_service"], "execute_with_identity", observed), patch.object(stack["tool_service"], "execute", observed_read), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                conversation.execute_turn(accepted.id)
            saved = conversation.store.get_turn(accepted.id)
            record = {"label": label, "message": message, "session_id": session.id, "turn_id": accepted.id,
                      "status": saved.status, "events": events, "artifact": saved.public_artifact,
                      "answer": conversation.store.turn_result(accepted.id).answer,
                      "drafts": snapshot(stack["creation_store"].db_path, "investigation_drafts"),
                      "binding_audits": snapshot(stack["creation_store"].db_path, "ruleset_proposal_approvals"),
                      "drafts_before": before_drafts, "audits_before": before_audits,
                      "execution_counts": {"run": len(snapshot(stack["creation_store"].db_path, "investigation_runs")),
                          "job": len(snapshot(stack["resource_db"], "jobs")), "report": len(snapshot(stack["resource_db"], "report_versions"))}}
            evidence["cases"].append(record)
            save()
            print(json.dumps({"label": label, "status": saved.status, "tools": [e["name"] for e in events]}, ensure_ascii=False), flush=True)
            return record

        try:
            session = conversation.create_session(principal=principal, workspace_key="t4b-real")
            formal_tables = ["rule_sets", "rule_set_revisions", "rule_set_publish_idempotency", "lexicon_categories", "lexicon_keywords"]
            formal = {table: snapshot(stack["resource_db"], table) for table in formal_tables}
            first = turn(session, "generate", "帮我在小红书调查招聘诈骗，使用搜索模式；如果没有合适的规则，就生成一套给我看看。")
            assert first["artifact"].get("proposal_presentations") and not first["drafts"], "generation must present without binding"
            second = turn(session, "edit", "第二条严格一点，把分期培训收费也明确进去。")
            presented = second["artifact"]["proposal_presentations"][0]
            assert presented["proposal_version"] == 2 and not second["drafts"]
            third = turn(session, "approve", "可以，就按现在这套规则来。")
            assert any(e["name"] == "use_ruleset_proposal" and e["result"]["status"] == "ok" for e in third["events"]), "approval must bind"
            assert next(e for e in third["events"] if e["name"] == "use_ruleset_proposal")["arguments"]["presentation_id"] == presented["presentation_id"]
            judgement = third["artifact"]["draft"]["configuration"]["judgement"]
            assert judgement["strategy"] == "temporary_ruleset"
            assert judgement["content"] == presented["snapshot"]["content"]
            assert judgement["content_hash"] == presented["content_hash"] and judgement["proposal_version"] == 2
            assert not third["artifact"]["confirmation_preview"]["can_confirm"]
            assert not any(e["name"] == "query_investigation_options" and (
                e["arguments"].get("ruleset_revision_ids") or e["arguments"].get("include_ruleset_details_for_revision_ids")
            ) for e in third["events"]), "approval must not reselect a formal RuleSet"
            # Semantic cases use controlled durable T4A setup, followed by real Qwen turns.
            # Only the setup is scripted; no semantic decision is scripted or overridden.
            from test_investigation_creation_conversation import _run_scripted_creation_turn
            fixture_content = json.loads((Path(__file__).parent / "fixtures/recruitment_fraud_ruleset.json").read_text())
            fixture_content["name"] = "招聘诈骗"
            evidence["semantic_setup"] = "Controlled fixture RuleSet is published through real T4A completion; each following decision executes real Qwen. Main generate/edit/adopt path is entirely real Qwen; stale setup starts before any Draft binding, so the model must attempt adoption rather than correctly report an already-bound no-op."
            def seeded(label, multiple=False):
                with patch.object(conversation, "fake_runtime", True):
                    seeded_first = _run_scripted_creation_turn(stack,
                        content="请在小红书搜索招聘诈骗，使用临时搜索词招聘收费，生成一套规则给我看看。",
                        client_message_id="setup-" + label,
                        actions=[("create_ruleset_proposal", {"content": deepcopy(fixture_content)})],
                        final_response="以下为招聘诈骗候选规则，尚未采用。")
                    if multiple:
                        another = deepcopy(fixture_content)
                        another["name"] = "虚假宣传"
                        another["domain"] = "虚假宣传"
                        _run_scripted_creation_turn(stack, session_id=seeded_first["session_id"], client_message_id="setup-second-" + label,
                            content="再生成另一套虚假宣传规则作比较，两套都先保留，暂不采用。",
                            actions=[("create_ruleset_proposal", {"content": another})],
                            final_response="两套候选方案都保留，尚未选择采用哪一套。")
                conversation._agents.pop(seeded_first["session_id"], None)
                return conversation.get_session(seeded_first["session_id"], principal=principal), seeded_first["turn"].public_artifact["proposal_presentations"][0]

            stale_session, stale_presentation = seeded("J-stale")
            changed = deepcopy(stale_presentation["snapshot"]["content"])
            changed["audit_goal"] += " unseen update"
            stack["app_service"].update_ruleset_proposal(stale_presentation["proposal_id"], session_id=stale_session.id,
                expected_version=stale_presentation["proposal_version"], content=changed)
            stale = turn(stale_session, "J-stale", "可以，就用这套。")
            stale_calls = [e for e in stale["events"] if e["name"] == "use_ruleset_proposal"]
            assert stale_calls and stale_calls[0]["result"]["error"]["code"] == "PROPOSAL_PRESENTATION_STALE", "real stale must reject"
            assert all(e["result"]["status"] == "error" for e in stale_calls)
            assert stale["drafts"] == stale["drafts_before"] and stale["binding_audits"] == stale["audits_before"]
            assert "本次规则采用操作未成功" in stale["answer"]
            stale["semantic_case_pass"] = True
            save()

            for label, message, multiple, adoption in [
                ("A-positive", "可以，就用这套。", False, True),
                ("B-edit", "第二条再改一下。", False, False),
                ("C-question", "这套能直接用吗？", False, False),
                ("D-choice-regression", "就用这套创建招聘诈骗调查，还是先暂停调查", False, False),
                ("E-negation", "先不要用这套。", False, False),
                ("F-save", "把这套保存下来。", False, False),
                ("G-multiple-ambiguous", "用那个。", True, False),
                ("H-multiple-explicit", "用招聘诈骗那套。", True, True),
            ]:
                target_session, target_presentation = seeded(label, multiple)
                record = turn(target_session, label, message)
                uses = [e for e in record["events"] if e["name"] == "use_ruleset_proposal"]
                record["expected_presentation_id"] = target_presentation["presentation_id"]
                assert record["status"] == "completed", label
                if adoption:
                    assert uses and uses[-1]["result"]["status"] == "ok", label
                    assert uses[-1]["arguments"]["presentation_id"] == target_presentation["presentation_id"], label
                    assert len(record["binding_audits"]) == len(record["audits_before"]) + 1, label
                    bound = record["artifact"]["draft"]["configuration"]["judgement"]
                    assert bound["content"] == target_presentation["snapshot"]["content"], label
                else:
                    assert not uses, label + " must not call use"
                    assert record["drafts"] == record["drafts_before"], label
                    assert record["binding_audits"] == record["audits_before"], label
                    assert record["answer"].strip(), label
                record["semantic_case_pass"] = True
                save()
            evidence["no_formal_mutation"] = formal == {table: snapshot(stack["resource_db"], table) for table in formal_tables}
            evidence["execution_counts"] = {"run": len(snapshot(stack["creation_store"].db_path, "investigation_runs")), "job": len(snapshot(stack["resource_db"], "jobs")), "report": len(snapshot(stack["resource_db"], "report_versions"))}
            assert evidence["no_formal_mutation"] and not any(evidence["execution_counts"].values())
            evidence["pass"] = True
        finally:
            save()
            try:
                next(fixture)
            except StopIteration:
                pass


if __name__ == "__main__":
    main()
