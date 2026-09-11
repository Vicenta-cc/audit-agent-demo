from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os

from backend.audit_agent.config import settings


@dataclass(frozen=True)
class HistoricalReportSpec:
    workspace_id: str
    run_id: str
    title: str
    task_id: str
    report_version_id: str
    source_database: Path
    source_database_sha256: str
    report_content_hash: str
    snapshot_hash: str
    draft: dict[str, Any]
    display_timeline: tuple[dict[str, Any], ...]


def _source_path(env_path: Path | None, artifact_dir: str) -> Path:
    if env_path is not None:
        return env_path.expanduser().resolve()
    return (
        settings.root_dir.parent
        / "xhs-audit-agent-hermes-m2-2-valid-heldout"
        / "artifacts"
        / artifact_dir
        / "report-generation.sqlite3"
    ).resolve()


HISTORICAL_REPORT_SPECS = (
    HistoricalReportSpec(
        workspace_id="historical-report-a",
        run_id="historical-report-run-a",
        title="我好累心好累监控任务",
        task_id="3ad102e072f6",
        report_version_id="report-version:8c355a5ba03f45619795813af83ac669",
        source_database=_source_path(
            settings.historical_report_a_db,
            "report_r31_account_overview_zero_standalone_recovery_20260825_a1386c5",
        ),
        source_database_sha256=(
            "f70d1b9fb6cd85471d3f9e8e3bc2c0d89f390329a6c5a02449d4a3c506b46560"
        ),
        report_content_hash=(
            "bfce4b1669d6f4b4684e66b5b076cd5cd97c2c2807f8f1b1dfdca9f12bec4f64"
        ),
        snapshot_hash=(
            "bdb1ee04d2a3cf22997c57c5dc308807f0dd6abcce18094613a505d41f3f80f4"
        ),
        draft={
            "task_name": "我好累心好累监控任务",
            "subject": "围绕‘我好累心好累’账号相关内容与评论开展风险调查",
            "platform": "dy",
            "search_terms": [
                "我好累心好累",
                "主播 年龄 外貌",
                "婚史 羞辱",
                "评论区 人身攻击",
            ],
            "analysis_plan": "女性主播人身攻击与低俗评论专题研判方案",
            "analysis_description": (
                "结合原帖与评论自身审核结果，识别针对女性主播的年龄、外貌、"
                "婚史羞辱及其他低俗攻击，并保留证据边界。"
            ),
            "recall_lexicons": ["人身攻击表达词库", "女性群体低俗物化召回词库"],
        },
        display_timeline=(
            {
                "id": "history-a-request",
                "kind": "user_request",
                "occurred_at": "2026-08-25T06:40:00+00:00",
                "content": (
                    "请调查‘我好累心好累’账号相关内容，重点关注评论区是否存在"
                    "针对女性主播的外貌、年龄和婚史羞辱，并形成可追溯报告。"
                ),
            },
            {
                "id": "history-a-plan",
                "kind": "plan_recommendation",
                "occurred_at": "2026-08-25T06:41:00+00:00",
                "content": (
                    "已识别调查目标。建议采用女性主播人身攻击与低俗评论专题研判方案，"
                    "并使用本次任务专用搜索词与召回词库；所有风险判断以原帖和评论自身"
                    "审核结果为准。"
                ),
            },
            {
                "id": "history-a-confirm",
                "kind": "user_confirmation",
                "occurred_at": "2026-08-25T06:43:00+00:00",
                "content": "确认按推荐方案执行，保留直接依据和不能外推的研判边界。",
            },
            {
                "id": "history-a-process",
                "kind": "processing_update",
                "occurred_at": "2026-08-25T07:19:00+00:00",
                "content": (
                    "已完成冻结样本采集、帖子与评论独立审核、结构化发现归纳和直接证据绑定。"
                ),
            },
            {
                "id": "history-a-ready",
                "kind": "report_ready",
                "occurred_at": "2026-08-25T07:23:52.414516+00:00",
                "content": "报告已生成。可以打开报告查看结构化结论、代表内容与研判依据。",
            },
        ),
    ),
    HistoricalReportSpec(
        workspace_id="historical-report-b",
        run_id="historical-report-run-b",
        title="麦热依姆古丽监控任务2",
        task_id="8bc179209e1e",
        report_version_id="report-version:4e3ebeccd2ed4f0c9c9a750332d22585",
        source_database=_source_path(
            settings.historical_report_b_db,
            "report_r31_target_role_correction_20260825_ad68dec",
        ),
        source_database_sha256=(
            "2de176629ac0cd2d06588bc436786dc45819555ec48bc2cad59a84207c8793bf"
        ),
        report_content_hash=(
            "29bf76de4eb1468a51dfec43df9701419555bb67b9bef29fb8ef054bbb016c40"
        ),
        snapshot_hash=(
            "a23e23ada3e54ebf7bec8a9666a6a7e090e783c1ec4cc1ef823c7b5c182d4630"
        ),
        draft={
            "task_name": "麦热依姆古丽监控任务2",
            "subject": "围绕麦热依姆古丽相关内容与评论开展风险调查",
            "platform": "dy",
            "search_terms": [
                "麦热依姆古丽",
                "女性博主 低俗评论",
                "性骚扰 物化",
                "评论作者 活动",
            ],
            "analysis_plan": "女性博主性骚扰与低俗物化评论专题研判方案",
            "analysis_description": (
                "对帖子与评论分别审核，识别针对女性博主的性骚扰、低俗物化评论，"
                "并通过稳定账号活动关系核查当前授权调查中的互动。"
            ),
            "recall_lexicons": ["性骚扰表达词库", "低俗物化评论召回词库"],
        },
        display_timeline=(
            {
                "id": "history-b-request",
                "kind": "user_request",
                "occurred_at": "2026-08-25T07:45:00+00:00",
                "content": (
                    "请继续调查麦热依姆古丽相关内容，重点核查针对女性博主的性骚扰、"
                    "低俗物化评论及其评论作者活动。"
                ),
            },
            {
                "id": "history-b-plan",
                "kind": "plan_recommendation",
                "occurred_at": "2026-08-25T07:46:00+00:00",
                "content": (
                    "已识别调查目标。建议采用女性博主性骚扰与低俗物化评论专题研判方案，"
                    "配合性骚扰表达和低俗物化评论召回词库；帖子风险与评论自身风险分别研判。"
                ),
            },
            {
                "id": "history-b-confirm",
                "kind": "user_confirmation",
                "occurred_at": "2026-08-25T07:48:00+00:00",
                "content": "确认执行，并保留评论到父帖、评论作者到账号活动的可验证关系。",
            },
            {
                "id": "history-b-process",
                "kind": "processing_update",
                "occurred_at": "2026-08-25T08:31:00+00:00",
                "content": (
                    "已完成冻结样本采集、帖子与评论独立审核、代表内容筛选及账号活动索引。"
                ),
            },
            {
                "id": "history-b-ready",
                "kind": "report_ready",
                "occurred_at": "2026-08-25T08:36:08.606063+00:00",
                "content": "报告已生成。可以打开报告查看结构化发现、风险评论与账号活动入口。",
            },
        ),
    ),
)

# Optional immutable archive produced by the selected-report generation command.
# A/B's fixed identities and hashes above remain unchanged.
_c_manifest = os.getenv("HISTORICAL_REPORT_C_MANIFEST", "").strip()
if _c_manifest:
    _manifest = json.loads(Path(_c_manifest).read_text())
    if _manifest["workspace_id"] != "historical-report-c":
        raise ValueError("Report C manifest has an unexpected workspace identity")
    HISTORICAL_REPORT_SPECS += (HistoricalReportSpec(
        **{**_manifest, "source_database": Path(_manifest["source_database"]), "display_timeline": tuple(_manifest["display_timeline"])},
    ),)
_workspace_ids = {value for value in os.getenv("HISTORICAL_REPORT_WORKSPACE_IDS", "").split(",") if value}
if _workspace_ids:
    HISTORICAL_REPORT_SPECS = tuple(spec for spec in HISTORICAL_REPORT_SPECS if spec.workspace_id in _workspace_ids)
    if {spec.workspace_id for spec in HISTORICAL_REPORT_SPECS} != _workspace_ids:
        raise ValueError("requested historical report workspace is unavailable")
