"""Opt-in real Hermes/Qwen T1 acceptance using disposable test resources only."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "tests"))
    evidence = {"model": "qwen3.7-plus", "runtime": "Hermes 0.20.4", "cases": []}

    with tempfile.TemporaryDirectory(prefix="m3-t1-qwen-", dir="/tmp") as temporary:
        root = Path(temporary)
        for name, directory in (
            ("XHS_AUDIT_DATA_DIR", "data"),
            ("XHS_AUDIT_OUTPUTS_DIR", "outputs"),
            ("HERMES_HOME", "hermes"),
        ):
            os.environ[name] = str(root / directory)

        # Read only provider credentials; never inherit preserved data/runtime paths.
        from dotenv import dotenv_values

        credentials = dotenv_values(args.credentials_env)
        from backend.audit_agent.config import settings

        settings.dashscope_api_key = credentials.get("DASHSCOPE_API_KEY") or ""
        settings.dashscope_base_url = (
            credentials.get("DASHSCOPE_BASE_URL") or settings.dashscope_base_url
        )
        if not settings.dashscope_api_key:
            raise SystemExit("DASHSCOPE_API_KEY is unavailable")

        from test_investigation_creation_conversation import creation_stack, _run_count
        from backend.investigation_creation.principal import Principal
        from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools

        fixture = creation_stack.__wrapped__(root)
        stack = next(fixture)
        conversation = stack["conversation"]
        conversation.fake_runtime = False
        principal = Principal("principal-a")
        configure_hermes_investigation_creation_tools(
            stack["tool_service"], principal_provider=conversation.principal_for_session
        )

        def lexicon_state():
            with sqlite3.connect(stack["resource_db"]) as connection:
                return [
                    connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                    for table in ("lexicon_categories", "lexicon_keywords")
                ]

        def turn(label, message, session=None):
            if session is None:
                session = conversation.create_session(principal=principal, workspace_key=label)
            before = lexicon_state()
            accepted, replay = conversation.accept_message(
                session.id, client_message_id=label, content=message, principal=principal
            )
            assert not replay
            # Third-party runtime logs are not acceptance artifacts or safe credential output.
            with (
                patch.object(
                    stack["tool_service"], "execute_with_identity",
                    wraps=stack["tool_service"].execute_with_identity,
                ) as dispatched,
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
            ):
                result = conversation.execute_turn(accepted.id)
            saved = conversation.store.get_turn(accepted.id)
            artifact = saved.public_artifact
            record = {
                "case": label, "input": message, "status": saved.status,
                "answer": result.answer,
                "tools": [call.args[0] for call in dispatched.call_args_list],
                "hermes_tools": list(result.tool_names),
                "artifact_type": artifact.get("artifact_type"),
                "no_formal_lexicon_mutation": before == lexicon_state(),
                "run_count": _run_count(stack["creation_store"]),
            }
            if artifact.get("artifact_type") == "investigation_draft":
                draft = artifact["draft"]
                record.update(
                    draft_id=draft["id"], revision=draft["current_revision"],
                    recall_plan=draft["configuration"]["investigation"]["recall_plan"],
                )
            evidence["cases"].append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            assert saved.status == "completed", "Real conversation did not complete"
            assert record["no_formal_lexicon_mutation"]
            assert record["run_count"] == 0
            assert "confirm_and_queue_investigation" not in record["tools"]
            return session, record

        try:
            _, existing = turn("existing", "我想调查小红书上的赌博博彩推广风险。")
            assert existing["recall_plan"]["strategy"] == "existing_lexicon"

            with sqlite3.connect(stack["resource_db"]) as connection:
                connection.execute("DELETE FROM lexicon_keywords")
                connection.execute("DELETE FROM lexicon_categories")
            _, missing = turn("missing-no-authorization", "我想调查小红书上的赌博博彩推广风险。")
            assert missing["artifact_type"] is None
            assert "create_investigation_draft" not in missing["tools"]
            assert "临时" in missing["answer"] and "生成" in missing["answer"]

            session, generated = turn(
                "missing-preauthorized",
                "我想调查小红书上的赌博博彩推广风险，如果没有合适词库就帮我生成这次搜索词。",
            )
            plan = generated["recall_plan"]
            assert plan["strategy"] == "temporary_terms"
            assert set(plan) == {"strategy", "terms", "source_lexicon_ids"}
            assert len(plan["terms"]) > 1
            assert all(isinstance(term, str) and term and "," not in term for term in plan["terms"])
            removed = plan["terms"][0]
            _, edited = turn("edit", f"把其中的“{removed}”删掉。", session=session)
            assert edited["draft_id"] == generated["draft_id"]
            assert edited["revision"] == generated["revision"] + 1
            assert edited["recall_plan"]["terms"] == plan["terms"][1:]
            assert "update_investigation_draft" in edited["tools"]
            evidence["status"] = "PASS"
        except Exception as exc:
            evidence["status"] = "BLOCKED"
            evidence["failure_type"] = type(exc).__name__
            raise
        finally:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            fixture.close()


if __name__ == "__main__":
    main()
