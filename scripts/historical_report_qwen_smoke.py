from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.historical_reports import (
    HISTORICAL_REPORT_SPECS,
    HistoricalReportDemoService,
    HistoricalReportWorkspaceStore,
)
from backend.investigation.report_query import ReportQueryFacade
from backend.investigation.store import InvestigationStore
from backend.reporting.store import ReportStore


FROZEN_QUESTIONS = {
    "historical-report-a": (
        "先用三句话总结这份报告的主要结论，并说明报告覆盖范围和研判边界。",
        "展开“针对女性主播的年龄、外貌及婚史羞辱”，给我看最多两篇由报告正式标记的代表内容。如果不足两篇，只返回真实存在的数量，不得用普通相关帖子冒充代表内容。",
        "第一篇代表内容为什么支持这项发现？请展示直接研判依据。",
        "这条依据能够证明什么，又有哪些内容不能据此推断？",
    ),
    "historical-report-b": (
        "展开“针对女性博主的性骚扰与低俗物化评论”这一项发现，给我看代表帖子和其中已审核的风险评论。",
        "查看刚才结果中经系统确认的那位风险评论作者在本次调查中的账号活动。",
        "这个账号还出现在哪些当前可访问的调查中？按调查任务比较评论、风险评论和涉及帖子。",
        "它主要评论了哪些对象？展开第一条可验证的互动记录和对应父帖。",
    ),
}


class _InlineExecutor:
    def __init__(self, service: HermesInvestigationAgentService) -> None:
        self.service = service

    def accept_turn(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
    ) -> Any:
        turn, replay = self.service.accept_message(
            session_id,
            client_message_id=client_message_id,
            content=content,
        )
        if not self.service.store.list_public_turn_events(turn.id):
            self.service.store.append_public_turn_event(turn.id, stage="accepted")
        if replay:
            return self.service.store.get_turn(turn.id)
        self.service.store.append_public_turn_event(turn.id, stage="planning")
        result = self.service.execute_turn(turn.id)
        self.service.store.append_public_turn_event(
            turn.id,
            stage="completed",
            answer=result.answer,
        )
        return self.service.store.get_turn(turn.id)

    def resume_turn(self, turn_id: str) -> Any:
        turn, replay = self.service.accept_resume(turn_id)
        if not replay:
            result = self.service.execute_resume(turn.id)
            self.service.store.append_public_turn_event(
                turn.id,
                stage="completed",
                answer=result.answer,
            )
        return self.service.store.get_turn(turn.id)


def _table_count(database: Path, table: str) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _sanitized_turn(result: Any, *, workspace_label: str, ordinal: int) -> dict[str, Any]:
    receipts = [
        {
            "tool": receipt.tool_name,
            "operation": receipt.operation,
            "status": receipt.status,
            "result_count": receipt.result_count,
            "has_more": receipt.has_more,
        }
        for receipt in result.query_receipts
    ]
    return {
        "workspace": workspace_label,
        "ordinal": ordinal,
        "status": result.status,
        "answer": result.answer,
        "tool_trace": [
            {"ordinal": index, "tool": call.name}
            for index, call in enumerate(result.tool_calls, start=1)
        ],
        "source_mode": (
            "new_tool_trace" if result.tool_calls else "completed_history"
        ),
        "receipt_summary": receipts,
        "grounding_status": result.grounding_validation.status,
        "llm_call_count": result.llm_call_count,
        "token_count": result.total_tokens,
    }


def _assert_sanitized(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    forbidden = (
        "investigation-session:",
        "report_session_id",
        "source_account_key",
        "source_namespace",
        "corpus_revision",
        "snapshot_hash",
        "report-version:",
        "tool_call_id",
        "receipt_id",
        "arguments_fingerprint",
        "query_fingerprint",
        "result_fingerprint",
        "/Users/",
        "DASHSCOPE_API_KEY",
        "system_prompt",
    )
    leaked = [item for item in forbidden if item in text]
    if leaked:
        raise RuntimeError(f"sanitized smoke output leaked forbidden fields: {leaked}")


def run_smoke() -> dict[str, Any]:
    if len(FROZEN_QUESTIONS) != 2 or sum(map(len, FROZEN_QUESTIONS.values())) != 8:
        raise RuntimeError("the Gate-frozen smoke must contain exactly eight questions")
    with tempfile.TemporaryDirectory(prefix="historical-report-qwen-smoke-") as temp_dir:
        root = Path(temp_dir)
        report_db = root / "reports.sqlite3"
        report_store = ReportStore(report_db)
        agent_service = HermesInvestigationAgentService(
            report_facade=ReportQueryFacade(report_db, query_service=object()),
            store=InvestigationStore(root / "investigation.sqlite3"),
            hermes_state_dir=root / "hermes",
            authorized_report_version_ids=tuple(
                item.report_version_id for item in HISTORICAL_REPORT_SPECS
            ),
            authorized_context_anchor_prefixes=("historical-report:",),
        )
        executor = _InlineExecutor(agent_service)
        service = HistoricalReportDemoService(
            specs=HISTORICAL_REPORT_SPECS,
            workspace_store=HistoricalReportWorkspaceStore(root / "workspaces.sqlite3"),
            report_store=report_store,
            report_service=agent_service,
            executor=executor,
        )
        try:
            service.list_workspaces(principal_id="qwen-smoke")
            before = {
                "report_generation_runs": _table_count(report_db, "report_generation_runs"),
                "report_provider_exchanges": _table_count(report_db, "report_provider_exchanges"),
            }
            turns = []
            first_history_empty: dict[str, bool] = {}
            for workspace_index, (workspace_id, questions) in enumerate(
                FROZEN_QUESTIONS.items(), start=1
            ):
                label = "A" if workspace_index == 1 else "B"
                first_history_empty[label] = (
                    service.report_service.store.find_session_by_anchor(
                        service._anchor(workspace_id, "qwen-smoke")
                    )
                    is None
                )
                for ordinal, question in enumerate(questions, start=1):
                    turn = service.accept_turn(
                        workspace_id,
                        principal_id="qwen-smoke",
                        client_message_id=f"qwen-smoke-{label.lower()}-{ordinal}",
                        content=question,
                    )
                    result = agent_service.store.turn_result(turn.id)
                    if result.status != "completed":
                        raise RuntimeError(
                            f"Qwen smoke {label}{ordinal} did not complete"
                        )
                    turns.append(
                        _sanitized_turn(result, workspace_label=label, ordinal=ordinal)
                    )
            after = {
                "report_generation_runs": _table_count(report_db, "report_generation_runs"),
                "report_provider_exchanges": _table_count(report_db, "report_provider_exchanges"),
            }
            payload = {
                "schema_version": "historical-report-qwen-smoke-v1",
                "model": "qwen3.7-plus",
                "question_count": len(turns),
                "turns": turns,
                "contracts": {
                    "first_question_started_without_internal_session": first_history_empty,
                    "internal_session_count": {
                        label: service.internal_session_count(
                            workspace_id, principal_id="qwen-smoke"
                        )
                        for label, workspace_id in (
                            ("A", "historical-report-a"),
                            ("B", "historical-report-b"),
                        )
                    },
                },
                "side_effect_counts": {"before": before, "after": after},
                "sanitization": {"passed": True},
            }
            if before != after or any(after.values()):
                raise RuntimeError("historical Qwen smoke created report-generation side effects")
            if first_history_empty != {"A": True, "B": True}:
                raise RuntimeError("a historical workspace eagerly created an internal Session")
            if any(
                not any(turn["tool_trace"] for turn in turns if turn["workspace"] == label)
                for label in ("A", "B")
            ):
                raise RuntimeError("each historical workspace must include a real tool trace")
            _assert_sanitized(payload)
            return payload
        finally:
            agent_service.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run_smoke()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Qwen smoke completed: {payload['question_count']} questions; sanitized output written")


if __name__ == "__main__":
    main()
