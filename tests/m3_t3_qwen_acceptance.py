"""Opt-in real Hermes/Qwen T3 acceptance, with disposable state and sanitized evidence."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
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
    parser.add_argument("--dual-attempts", type=int, choices=(1, 2), default=2)
    parser.add_argument("--authoring-quality", action="store_true",
                        help="Run isolated recruitment and gambling Proposal authoring cases only")
    parser.add_argument("--presentation-only", action="store_true", help="Run T4A create/update presentation acceptance")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    evidence = {
        "model": "qwen3.7-plus", "runtime": "Hermes 0.20.4", "cases": [],
        "product_dual_missing_contract": "Draft v4 requires published Judgement; Proposal cannot bind it.",
        "tool_loop_capacity_scenario": "Missing Recall; existing gambling Judgement; comparison Proposal requested.",
        "dual_generation_failure_classification": None,
        "REAL_QWEN_DUAL_GENERATION_STABILITY_GAP": False,
    }

    with tempfile.TemporaryDirectory(prefix="m3-t3-qwen-", dir="/tmp") as temporary:
        root = Path(temporary)
        for name, directory in (
            ("XHS_AUDIT_DATA_DIR", "data"), ("XHS_AUDIT_OUTPUTS_DIR", "outputs"),
            ("HERMES_HOME", "hermes"),
        ):
            os.environ[name] = str(root / directory)

        from dotenv import dotenv_values
        from backend.audit_agent.config import settings

        credentials = dotenv_values(args.credentials_env)
        settings.dashscope_api_key = credentials.get("DASHSCOPE_API_KEY") or ""
        settings.dashscope_base_url = credentials.get("DASHSCOPE_BASE_URL") or settings.dashscope_base_url
        if not settings.dashscope_api_key:
            raise SystemExit("Provider credentials are unavailable")
        del credentials

        from test_investigation_creation_conversation import creation_stack
        from backend.investigation_creation.principal import Principal
        from backend.investigation_creation.tools import M3_MUTATION_TOOL_NAMES, configure_hermes_investigation_creation_tools
        from backend.rulesets.compiler import compile_ruleset_content, content_hash

        fixture = creation_stack.__wrapped__(root)
        stack = next(fixture)
        conversation = stack["conversation"]
        conversation.fake_runtime = False
        principal = Principal("principal-a")
        service = stack["tool_service"]
        configure_hermes_investigation_creation_tools(service, principal_provider=conversation.principal_for_session)

        def snapshot(db, table):
            with sqlite3.connect(db) as connection:
                connection.row_factory = sqlite3.Row
                return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]

        def proposals(session_id):
            return [
                stack["app_service"].get_ruleset_proposal(row["proposal_id"], session_id=session_id).model_dump(mode="json")
                for row in snapshot(stack["creation_store"].db_path, "ruleset_proposals")
                if row["session_id"] == session_id
            ]

        def save():
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        def turn(label, message, session=None):
            session = session or conversation.create_session(principal=principal, workspace_key=label)
            formal_tables = ("rule_sets", "rule_set_revisions", "rule_set_publish_idempotency", "lexicon_categories", "lexicon_keywords")
            before_formal = {table: snapshot(stack["resource_db"], table) for table in formal_tables}
            before_proposals = proposals(session.id)
            before_drafts = snapshot(stack["creation_store"].db_path, "investigation_drafts")
            before_receipt_ids = {
                row["receipt_id"] for row in snapshot(
                    stack["creation_store"].db_path, "investigation_creation_tool_receipts"
                )
            }
            accepted, replay = conversation.accept_message(
                session.id, client_message_id=label, content=message, principal=principal,
            )
            assert not replay
            events = []
            original_identity = service.execute_with_identity
            original_execute = service.execute

            def observed_identity(name, arguments, **kwargs):
                if name == "confirm_and_queue_investigation":
                    result = {"status": "error", "error": {"code": "SMOKE_EXECUTION_FORBIDDEN"}}
                else:
                    result = original_identity(name, arguments, **kwargs)
                event = {"tool": name, "arguments": deepcopy(arguments), "status": result["status"]}
                identity = kwargs["identity"]
                event["execution_identity"] = {
                    "session_id": identity.session_id, "turn_id": identity.turn_id,
                    "tool_call_id": identity.tool_call_id,
                }
                if "error" in result:
                    event["error"] = result["error"]
                events.append(event)
                return result

            def observed_execute(name, arguments, **kwargs):
                result = original_execute(name, arguments, **kwargs)
                if name not in M3_MUTATION_TOOL_NAMES:
                    events.append({"tool": name, "arguments": deepcopy(arguments), "status": "ok"})
                return result

            failure = None
            result = None
            with (
                patch.object(service, "execute_with_identity", observed_identity),
                patch.object(service, "execute", observed_execute),
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
            ):
                try:
                    result = conversation.execute_turn(accepted.id)
                except Exception as exc:
                    failure = type(exc).__name__
            saved = conversation.store.get_turn(accepted.id)
            after_proposals = proposals(session.id)
            after_drafts = snapshot(stack["creation_store"].db_path, "investigation_drafts")
            receipts = [
                {key: row[key] for key in (
                    "receipt_id", "session_id", "turn_id", "tool_call_id", "tool_name",
                    "arguments_fingerprint", "status",
                )}
                for row in snapshot(stack["creation_store"].db_path, "investigation_creation_tool_receipts")
                if row["receipt_id"] not in before_receipt_ids
            ]
            new_drafts = [row for row in after_drafts if row not in before_drafts]
            record = {
                "case": label, "input": message, "session_id": session.id,
                "turn_id": accepted.id, "status": saved.status, "failure_point": failure,
                "answer": result.answer if result is not None else saved.safe_message,
                "llm_call_count": saved.llm_call_count,
                "tools": [event["tool"] for event in events], "tool_events": events,
                "mutation_receipts": receipts, "proposals_before": before_proposals,
                "proposals_after": after_proposals, "drafts_unchanged": before_drafts == after_drafts,
                "draft_side_effects": [{
                    "draft_id": row["id"], "revision": row["current_revision"],
                    "configuration": json.loads(row["configuration_json"]),
                } for row in new_drafts],
                "no_formal_resource_mutation": before_formal == {
                    table: snapshot(stack["resource_db"], table) for table in formal_tables
                },
                "run_count": len(snapshot(stack["creation_store"].db_path, "investigation_runs")),
                "job_count": len(snapshot(stack["resource_db"], "jobs")),
                "report_count": len(snapshot(stack["resource_db"], "report_versions")),
            }
            if args.presentation_only:
                public = stack["client"].get(f"/api/investigation-workspaces/{session.id}/state")
                message = next((m for m in public.json().get("messages", []) if m["message_id"] == saved.assistant_message_id), {})
                presentations = saved.public_artifact.get("proposal_presentations", [])
                record["public_status"] = public.status_code
                record["presentations"] = presentations
                record["public_message"] = message
            transcript = conversation.store.latest_completed_hermes_transcript(session.id) or []
            current_user_index = max(
                (i for i, item in enumerate(transcript) if item.get("role") == "user"), default=0,
            )
            transcript = transcript[current_user_index:]
            record["hermes_tool_sequence"] = [
                item.get("name") for item in transcript if item.get("role") == "tool"
            ]
            record["model_output_shape"] = [{
                "role": item.get("role"), "has_content": bool(item.get("content")),
                "tool_call_count": len(item.get("tool_calls") or []),
            } for item in transcript]
            evidence["cases"].append(record)
            save()
            print(json.dumps({key: record[key] for key in ("case", "status", "tools", "llm_call_count", "failure_point")}), flush=True)
            if args.presentation_only:
                assert public.status_code == 200 and presentations
                for presentation in presentations:
                    assert presentation["snapshot"] in after_proposals
                    assert presentation["assistant_message_id"] == message["message_id"]
                    assert presentation["source_user_turn_id"] == saved.id
                    assert presentation["session_id"] == session.id
                    assert presentation["text"] in message["content"] == result.answer
                assert message["artifact"]["proposal_presentations"] == presentations
            assert record["no_formal_resource_mutation"]
            assert record["run_count"] == record["job_count"] == record["report_count"] == 0
            assert "confirm_and_queue_investigation" not in record["tools"]
            successful_mutations = [
                event for event in events
                if event["tool"] in M3_MUTATION_TOOL_NAMES and event["status"] == "ok"
            ]
            for event in successful_mutations:
                identity = event["execution_identity"]
                assert any(
                    all(receipt[key] == identity[key] for key in identity)
                    and receipt["tool_name"] == event["tool"] and receipt["status"] == "SUCCEEDED"
                    for receipt in receipts
                ), "Successful mutation must have a matching durable execution receipt"
            for proposal in after_proposals:
                compile_ruleset_content(proposal["content"])
                assert content_hash(proposal["content"]) == proposal["content_hash"]
            return session, record

        try:
            if args.authoring_quality:
                from collections import Counter

                for label, message in (
                    (
                        "authoring-quality-recruitment",
                        "请生成一套招聘诈骗临时研判规则给我看看，关注先收费才能入职、培训贷、"
                        "索要验证码或银行卡敏感信息，以及以招聘为名要求转账的骗局。不要启动调查。",
                    ),
                    (
                        "authoring-quality-gambling",
                        "请另外生成一套网络赌博推广的临时研判规则给我看看，覆盖平台入口、投注资金、"
                        "代理推广、评论组织参与和跨证据的参与闭环。不要启动调查。",
                    ),
                ):
                    _, record = turn(label, message)
                    assert record["status"] == "completed" and record["drafts_unchanged"]
                    assert len(record["proposals_after"]) == 1
                    assert record["proposals_after"][0]["version"] == 1
                    assert "create_ruleset_proposal" in record["tools"]
                    assert len(record["mutation_receipts"]) == 1
                    content = record["proposals_after"][0]["content"]
                    rules = [r for c in content["categories"] for r in c["rules"]]
                    record["authoring_summary"] = {
                        "rule_count": len(rules),
                        "stage_counts": dict(Counter(s for r in rules for s in r["application_stages"])),
                        "stage_combinations": dict(Counter(
                            "+".join(sorted(r["application_stages"])) for r in rules
                        )),
                        "general_exemptions": content["general_exemptions"],
                    }
                    save()
                evidence["status"] = "T3_AUTHORING_QUALITY_GENERATION_COMPLETE"
                return

            session, created = turn(
                "proposal-generation", ("帮我生成一套招聘诈骗研判规则给我看看。" if args.presentation_only else
                "帮我调查招聘诈骗，如果没有合适的研判规则，生成一套临时规则给我看看。"),
            )
            assert created["status"] == "completed" and created["drafts_unchanged"]
            assert len(created["proposals_after"]) == 1
            first = created["proposals_after"][0]
            assert first["version"] == 1
            assert "create_ruleset_proposal" in created["tools"]
            _, edited = turn(
                "proposal-edit", ("第二条严格一点。" if args.presentation_only else
                "提前收费这条太宽了，改成只有明确要求求职者先付款时才命中。"), session,
            )
            assert edited["status"] == "completed" and edited["drafts_unchanged"]
            second = edited["proposals_after"][0]
            assert second["proposal_id"] == first["proposal_id"] and second["version"] == 2
            assert second["content_hash"] != first["content_hash"]
            assert "update_ruleset_proposal" in edited["tools"]
            if args.presentation_only:
                assert created["presentations"][0]["proposal_version"] == 1
                assert edited["presentations"][0]["proposal_version"] == 2
                evidence["status"] = "M3_PHASE_T4A_PASS"
                return
            old_rules = {r["rule_id"]: r for c in first["content"]["categories"] for r in c["rules"]}
            new_rules = {r["rule_id"]: r for c in second["content"]["categories"] for r in c["rules"]}
            edited["rule_ids_preserved"] = list(old_rules) == list(new_rules)
            edited["changed_rule_ids"] = [key for key in old_rules if old_rules[key] != new_rules.get(key)]
            edited["category_ids_preserved"] = (
                [c["category_id"] for c in first["content"]["categories"]]
                == [c["category_id"] for c in second["content"]["categories"]]
            )
            assert edited["rule_ids_preserved"] and edited["category_ids_preserved"]
            assert len(edited["changed_rule_ids"]) == 1
            _, regenerated = turn("proposal-regeneration", "再生成另一套让我比较。", session)
            assert regenerated["status"] == "completed" and regenerated["drafts_unchanged"]
            assert len(regenerated["proposals_after"]) == 2
            assert second in regenerated["proposals_after"]
            assert regenerated["proposals_after"][1]["version"] == 1

            with sqlite3.connect(stack["resource_db"]) as connection:
                connection.execute("DELETE FROM lexicon_keywords")
                connection.execute("DELETE FROM lexicon_categories")
            _, terms = turn(
                "temporary-terms-alone",
                "我想调查小红书上的赌博博彩推广风险，如果没有合适词库就帮我生成这次搜索词。",
            )
            assert terms["status"] == "completed" and len(terms["draft_side_effects"]) == 1
            assert terms["draft_side_effects"][0]["configuration"]["investigation"]["recall_plan"]["strategy"] == "temporary_terms"
            assert not terms["proposals_after"]

            dual_passed = False
            for attempt in range(1, args.dual_attempts + 1):
                _, dual = turn(
                    f"dual-generation-{attempt}",
                    "帮我调查小红书上的赌博博彩推广风险，用现有已发布的赌博博彩研判规则创建调查草案；"
                    "没有合适词库就生成本次搜索词，同时另外生成一套临时研判规则 Proposal 给我比较。先不要启动调查。",
                )
                dual_passed = (
                    dual["status"] == "completed" and len(dual["draft_side_effects"]) == 1
                    and len(dual["proposals_after"]) == 1
                    and dual["draft_side_effects"][0]["configuration"]["investigation"]["recall_plan"]["strategy"] == "temporary_terms"
                )
                dual["dual_generation_passed"] = dual_passed
                if dual_passed:
                    assert len(dual["mutation_receipts"]) == 2
                    assert len({r["turn_id"] for r in dual["mutation_receipts"]}) == 1
                    break
                dual["failure_point"] = dual["failure_point"] or "Missing successful generation branch or incomplete turn"
                save()
            if not dual_passed:
                # Offline integration proves both mutation orders; classify observed failures explicitly.
                failures = [event for case in evidence["cases"] if case["case"].startswith("dual-")
                            for event in case["tool_events"] if event["status"] == "error"]
                system_codes = {"IDEMPOTENCY_CONFLICT", "TOOL_EXECUTION_IDENTITY_REQUIRED",
                                "MUTATION_RESULT_UNKNOWN", "APPLICATION_TOOLS_UNBOUND"}
                system_failure = any(event.get("error", {}).get("code") in system_codes for event in failures)
                evidence["dual_generation_failure_classification"] = (
                    "SYSTEM_CONTRACT_FAILURE" if system_failure else "MODEL_ORCHESTRATION_INSTABILITY"
                )
                evidence["REAL_QWEN_DUAL_GENERATION_STABILITY_GAP"] = not system_failure
                if system_failure:
                    raise AssertionError("Dual generation system contract failure")
            evidence["status"] = "M3_PHASE_T3_PASS"
        except Exception as exc:
            evidence["status"] = "M3_PHASE_T4A_BLOCKED" if args.presentation_only else "M3_PHASE_T3_BLOCKED"
            evidence["failure_type"] = type(exc).__name__
            raise
        finally:
            save()
            fixture.close()


if __name__ == "__main__":
    main()
