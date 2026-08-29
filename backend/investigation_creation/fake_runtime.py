from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.reporting.store import ReportStore

from .contracts import InvestigationRun


class FakePublishedReportHermesAgent:
    """Hermes-shaped report QA fixture that never calls a Provider."""

    def __init__(self, **_: Any) -> None:
        self._api_max_retries = 1

    def close(self) -> None:
        return None

    def run_conversation(
        self,
        message: str,
        *,
        conversation_history: list[dict[str, Any]] | None = None,
        task_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        history = [dict(item) for item in conversation_history or []]
        call_id = f"fake-report-presentation:{task_id}"
        answer = (
            "根据当前已发布的本地演示报告，本次仅验证调查产品链路；"
            "演示样本为 1，真实采集为 0，未启动 Worker 或采集审计流水线。"
        )
        messages = [
            *history,
            {"role": "user", "content": message},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "read_report_presentation",
                            "arguments": {},
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "read_report_presentation",
                "content": json.dumps(
                    {
                        "status": "ok",
                        "data": {
                            "presentation_version": "human-report-v1",
                            "key_metrics": [
                                {"label": "演示样本", "value": "1"},
                                {"label": "真实采集", "value": "0"},
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
            {"role": "assistant", "content": answer},
        ]
        return {
            "final_response": answer,
            "messages": messages,
            "api_calls": 0,
            "completed": True,
            "failed": False,
            "interrupted": False,
            "turn_exit_reason": "fake_report_response",
        }


class FakeInvestigationRunProjector:
    """Read-only time projection backed by a static report fixture, never by a Worker."""

    def __init__(
        self,
        *,
        report_store: ReportStore,
        report_agent_service: HermesInvestigationAgentService,
        stage_seconds: float = 1.2,
    ) -> None:
        self.report_store = report_store
        self.report_agent_service = report_agent_service
        self.stage_seconds = max(0.05, float(stage_seconds))

    def prepare_run(self, run: InvestigationRun) -> None:
        report_version_id = self._ensure_report(run)
        self.report_agent_service.create_session(
            report_version_id, anchor_key=f"m3-run:{run.id}"
        )

    def project(self, run: InvestigationRun) -> dict[str, Any]:
        report_version_id = self._report_version_id(run)
        elapsed = max(
            0.0,
            (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(run.created_at)
            ).total_seconds(),
        )
        unit = self.stage_seconds
        if elapsed < unit:
            return {
                "status": "QUEUED",
                "crawl_status": "pending",
                "analysis_status": "pending",
                "task_stats": {"total": 1, "queued": 1, "completed": 0},
                "report_status": "pending",
            }
        if elapsed < unit * 2:
            return {
                "status": "RUNNING",
                "crawl_status": "running",
                "analysis_status": "running",
                "task_stats": {"total": 1, "crawled": 0, "analyzed": 0},
                "report_status": "pending",
            }
        if elapsed < unit * 3:
            return {
                "status": "RUNNING",
                "crawl_status": "completed",
                "analysis_status": "running",
                "task_stats": {"total": 1, "crawled": 1, "analyzed": 0},
                "report_status": "pending",
            }
        if elapsed < unit * 4:
            return {
                "status": "REPORT_GENERATING",
                "crawl_status": "completed",
                "analysis_status": "completed",
                "task_stats": {"total": 1, "crawled": 1, "analyzed": 1},
                "report_status": "generating",
            }
        return {
            "status": "PUBLISHED",
            "crawl_status": "completed",
            "analysis_status": "completed",
            "task_stats": {"total": 1, "crawled": 1, "analyzed": 1},
            "report_status": "published",
            "report_version_id": report_version_id,
        }

    def _report_version_id(self, run: InvestigationRun) -> str:
        versions = self.report_store.list_published_versions_for_task(
            self._task_id(run)
        )
        return str(versions[0]["id"]) if versions else ""

    def _ensure_report(self, run: InvestigationRun) -> str:
        existing = self._report_version_id(run)
        if existing:
            return existing
        task_id = self._task_id(run)
        generation = self.report_store.create_generation(
            task_id,
            model="fake-hermes-m3",
            prompt_version="fake-investigation-report-v1",
        )
        report_version_id = str(generation["report_version_id"])
        snapshot_hash = hashlib.sha256(
            f"fake-snapshot:{run.id}".encode("utf-8")
        ).hexdigest()
        self.report_store.save_source_snapshot(
            {
                "snapshot_id": f"report-source-snapshot:{run.id}",
                "report_version_id": report_version_id,
                "task_id": task_id,
                "task_status": "completed",
                "source_hash": f"fake-source:{run.id}",
                "configuration_revision_id": "",
                "finding_ids": [],
                "evidence_ids": [],
                "data_quality_warnings": ["本地 fake runtime 演示数据，未启动真实采集。"],
                "statistic_inputs": [],
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_hash": snapshot_hash,
            }
        )
        human_report = {
            "presentation_version": "human-report-v1",
            "title": "世界杯期间小红书博彩引流调查报告",
            "summary": {
                "text": "本地 fake runtime 已完成端到端产品状态验证；本报告不包含真实采集结果。",
                "claim_ids": [],
            },
            "key_metrics": [
                {
                    "label": "演示样本",
                    "value": "1",
                    "detail": "仅用于界面与状态投影验收",
                    "metric_refs": [],
                },
                {
                    "label": "真实采集",
                    "value": "0",
                    "detail": "Worker 与采集流水线均未启动",
                    "metric_refs": [],
                },
            ],
            "sections": [
                {
                    "section_id": "scope",
                    "title": "调查范围",
                    "paragraphs": [
                        {
                            "text": "验证从模糊需求、方案确认到报告工作区的产品链路。",
                            "claim_ids": [],
                        }
                    ],
                }
            ],
            "case_blocks": [],
            "conclusion": {
                "text": "状态与会话连续性验证完成，真实业务结论需正式采集后生成。",
                "claim_ids": [],
            },
            "data_quality_note": {
                "text": "这是明确标注的 fake runtime 报告，不代表真实平台数据。",
                "claim_ids": [],
            },
        }
        published = self.report_store.publish_version(
            report_version_id=report_version_id,
            title=human_report["title"],
            body_markdown=f"# {human_report['title']}",
            body_json={
                "human_report": human_report,
                "audit_model": {
                    "task_id": task_id,
                    "source_snapshot_hash": snapshot_hash,
                    "findings": [],
                    "evidence": [],
                },
            },
            sections=[],
            citation_details={},
        )
        return str(published["id"])

    @staticmethod
    def _task_id(run: InvestigationRun) -> str:
        return run.id
