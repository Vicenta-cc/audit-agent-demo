from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from backend.domain.identity import stable_hash
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.reporting.store import ReportStore
from backend.reporting.structured_contract import STRUCTURED_REPORT_SCHEMA_VERSION

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
                "task_stats": self._task_stats(ingested=0, completed=0),
                "report_status": "pending",
            }
        if elapsed < unit * 2:
            return {
                "status": "RUNNING",
                "crawl_status": "running",
                "analysis_status": "running",
                "task_stats": self._task_stats(ingested=0, completed=0),
                "report_status": "pending",
            }
        if elapsed < unit * 3:
            return {
                "status": "RUNNING",
                "crawl_status": "completed",
                "analysis_status": "running",
                "task_stats": self._task_stats(ingested=1, completed=0),
                "report_status": "pending",
            }
        if elapsed < unit * 4:
            return {
                "status": "REPORT_GENERATING",
                "crawl_status": "completed",
                "analysis_status": "completed",
                "task_stats": self._task_stats(ingested=1, completed=1),
                "report_status": "generating",
            }
        return {
            "status": "PUBLISHED",
            "crawl_status": "completed",
            "analysis_status": "completed",
            "task_stats": self._task_stats(ingested=1, completed=1),
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
        fixture = self._structured_fixture(run)
        self.report_store.save_source_snapshot(
            {
                "snapshot_id": f"report-source-snapshot:{run.id}",
                "report_version_id": report_version_id,
                "task_id": task_id,
                "task_status": "completed",
                "source_hash": fixture["source_hash"],
                "configuration_revision_id": "",
                "finding_ids": fixture["finding_ids"],
                "evidence_ids": fixture["evidence_ids"],
                "data_quality_warnings": ["本地 fake runtime 演示数据，未启动真实采集。"],
                "statistic_inputs": [],
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_hash": fixture["snapshot_hash"],
                "display_name": fixture["display_name"],
                "source_revision": fixture["source_revision"],
                "relation_hash": fixture["relation_hash"],
                "statistics": fixture["statistics"],
            }
        )
        self.report_store.save_snapshot_payloads(
            report_version_id,
            posts=fixture["snapshot_posts"],
            findings=fixture["snapshot_findings"],
            evidence=fixture["snapshot_evidence"],
        )
        human_report = {
            "presentation_version": "human-report-v1",
            "title": "M3 前端合同验证报告",
            "summary": {
                "text": "deterministic fake runtime 已完成前端报告合同验证；本报告不包含真实采集结果。",
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
                            "text": "验证新建调查、确认、运行状态与前端结构化报告合同。",
                            "claim_ids": [],
                        }
                    ],
                }
            ],
            "case_blocks": [],
            "conclusion": {
                "text": "M3 前端结构化报告合同验证完成，真实业务结论需正式采集后生成。",
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
                "report_document": fixture["report_document"],
                "account_model": fixture["account_model"],
                "audit_model": {
                    "task_id": task_id,
                    "source_snapshot_hash": fixture["snapshot_hash"],
                    "findings": fixture["report_document"]["audit_findings"],
                    "evidence": fixture["report_document"]["evidence"],
                },
            },
            sections=[],
            citation_details={},
        )
        return str(published["id"])

    @staticmethod
    def _task_stats(*, ingested: int, completed: int) -> dict[str, Any]:
        return {
            "total": 1,
            "ingested_count": ingested,
            "queued_analysis_count": max(0, ingested - completed),
            "pending_analysis_count": max(0, ingested - completed),
            "analyzing_count": 0,
            "completed_analysis_count": completed,
            "failed_analysis_count": 0,
            "analysis_status_counts": {
                "queued": max(0, ingested - completed),
                "completed": completed,
            },
            "batch_count": 1 if ingested else 0,
            "batch_item_count": ingested,
            "batch_processed_count": completed,
        }

    @staticmethod
    def _structured_fixture(run: InvestigationRun) -> dict[str, Any]:
        token = hashlib.sha256(
            f"m3-frontend-contract:{run.id}".encode("utf-8")
        ).hexdigest()[:16]
        execution = dict(run.confirmed_configuration.get("execution") or {})
        platform = str(execution.get("platform") or "dy")
        display_name = "M3 deterministic frontend contract fixture"
        post_ref = f"post-fixture:{token}"
        finding_ref = f"finding-fixture:{token}"
        audit_result_id = int(token[:12], 16)

        post_payload = {
            "platform": platform,
            "title": "世界杯期间疑似博彩引流内容",
            "caption": "内容使用盘口、上分等表达引导用户进入站外渠道。",
            "author_display_name": "合同验证账号",
            "audit_finding_ref": finding_ref,
        }
        evidence_specs = (
            (
                "post_text",
                "World Cup odds and fast top-up available.",
                "世界杯赔率和快速上分服务。",
                "正文出现赔率和上分引流表达。",
            ),
            ("ocr", "扫码进群获取盘口", "", "画面文字包含盘口及进群引导。"),
            ("asr_audio", "私信我获取投注方案", "", "音频转写包含投注方案引导。"),
            ("comment_text", "怎么上分，求群", "", "评论出现上分和群聊询问。"),
            (
                "visual_frame",
                "视频末帧展示站外群二维码",
                "",
                "视觉帧存在站外导流二维码。",
            ),
        )
        evidence = []
        snapshot_evidence = []
        for index, (evidence_type, original, translated, summary) in enumerate(
            evidence_specs, 1
        ):
            evidence_ref = f"evidence-fixture:{token}:{evidence_type}"
            payload = {
                "evidence_ref": evidence_ref,
                "post_ref": post_ref,
                "audit_finding_ref": finding_ref,
                "support_type": "direct",
                "evidence_type": evidence_type,
                "original_text": original,
                "translated_text": translated,
                "summary": summary,
            }
            evidence.append(payload)
            snapshot_evidence.append(
                {
                    "ref": evidence_ref,
                    "audit_result_id": audit_result_id,
                    "post_ref": post_ref,
                    "finding_ref": finding_ref,
                    "support_type": "direct",
                    "source_formats": [evidence_type],
                    "payload": payload,
                    "payload_hash": stable_hash(payload),
                }
            )

        evidence_refs = [item["evidence_ref"] for item in evidence]
        finding_payload = {
            "audit_finding_ref": finding_ref,
            "post_ref": post_ref,
            "decision": "reject",
            "risk_level": "high",
            "summary": "内容同时出现博彩术语与站外导流信号，判定为高风险博彩引流。",
            "evidence_refs": evidence_refs,
        }
        snapshot_posts = [
            {
                "ref": post_ref,
                "canonical_key": f"fixture-content:{token}",
                "revision": "1",
                "payload": post_payload,
                "payload_hash": stable_hash(post_payload),
            }
        ]
        snapshot_findings = [
            {
                "ref": finding_ref,
                "audit_result_id": audit_result_id,
                "post_ref": post_ref,
                "payload": finding_payload,
                "payload_hash": stable_hash(finding_payload),
            }
        ]
        relation_hash = stable_hash(
            [
                {
                    "post_ref": post_ref,
                    "finding_ref": finding_ref,
                    "evidence_ref": item["evidence_ref"],
                }
                for item in evidence
            ]
        )
        source_revision = stable_hash(
            [
                (
                    item["ref"],
                    item["revision"],
                    item["payload_hash"],
                )
                for item in snapshot_posts
            ]
        )
        source_hash = stable_hash({"fixture": display_name, "run_id": run.id})
        statistics = {
            "canonical_posts": 1,
            "findings": 1,
            "direct_evidence": len(evidence),
            "indirect_evidence": 0,
            "counter_evidence": 0,
            "decision": {"pass": 0, "review": 0, "reject": 1},
            "risk_level": {"high": 1, "medium": 0, "low": 0, "none": 0},
        }
        snapshot_hash = stable_hash(
            {
                "task_id": run.id,
                "display_name": display_name,
                "source_db_sha256": source_hash,
                "source_revision": source_revision,
                "audit_config_revision_id": "",
                "posts": [item["payload_hash"] for item in snapshot_posts],
                "findings": [item["payload_hash"] for item in snapshot_findings],
                "evidence": [item["payload_hash"] for item in snapshot_evidence],
                "relations": relation_hash,
            }
        )
        account_model = {
            "schema_version": "report-account-overview-r3.1/v1",
            "account_coverage_statistics": {"distinct_account_count": 0},
            "target_account_entries": [],
            "default_active_comment_entries": [],
            "full_account_index": {"entries": [], "total_count": 0},
            "scope_boundary": "deterministic frontend contract fixture only",
        }
        report_document = {
            "schema_version": STRUCTURED_REPORT_SCHEMA_VERSION,
            "report_metadata": {
                "title": "M3 前端合同验证报告",
                "source_name": "deterministic fake runtime",
                "status": "published",
                "investigation_scope": "仅用于 M3 前端报告合同与交互验证",
            },
            "ordered_sections": [
                {
                    "section_ref": f"section-fixture:{token}",
                    "section_number": "1",
                    "parent_section_ref": None,
                    "section_type": "investigation_finding",
                    "title": "博彩引流风险研判",
                    "paragraphs": [
                        {
                            "text": "该合同样本包含博彩术语、站外导流及多模态直接证据。"
                        }
                    ],
                    "claims": [
                        {
                            "audit_finding_refs": [finding_ref],
                            "evidence_refs": evidence_refs,
                        }
                    ],
                }
            ],
            "posts": [{"post_ref": post_ref, **post_payload}],
            "audit_findings": [finding_payload],
            "evidence": evidence,
            "investigation_findings": [
                {
                    "investigation_finding_ref": finding_ref,
                    "title": "博彩引流风险",
                    "statement": "样本内容存在博彩术语与站外导流行为。",
                    "boundary_notes": ["仅用于前端合同验证，不代表真实采集结论。"],
                    "representative_post_refs": [post_ref],
                    "post_memberships": [
                        {
                            "post_ref": post_ref,
                            "audit_finding_ref": finding_ref,
                            "membership_evidence_refs": evidence_refs,
                        }
                    ],
                }
            ],
            "standalone_risk_posts": [],
            "statistics": statistics,
            "account_coverage_statistics": account_model[
                "account_coverage_statistics"
            ],
            "target_account_entries": account_model["target_account_entries"],
            "default_active_comment_entries": account_model[
                "default_active_comment_entries"
            ],
            "full_account_index": account_model["full_account_index"],
            "account_scope_boundary": account_model["scope_boundary"],
        }
        return {
            "display_name": display_name,
            "source_hash": source_hash,
            "source_revision": source_revision,
            "relation_hash": relation_hash,
            "snapshot_hash": snapshot_hash,
            "finding_ids": [finding_ref],
            "evidence_ids": evidence_refs,
            "statistics": statistics,
            "snapshot_posts": snapshot_posts,
            "snapshot_findings": snapshot_findings,
            "snapshot_evidence": snapshot_evidence,
            "report_document": report_document,
            "account_model": account_model,
        }

    @staticmethod
    def _task_id(run: InvestigationRun) -> str:
        return run.id
