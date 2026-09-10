from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.domain.contracts import FindingFilters
from backend.domain.pagination import Pagination
from backend.domain.query_service import DomainQueryService
from backend.domain.repository import DomainRepository
from backend.domain.warnings import DataQuality


DEFAULT_TARGET_TASK_ID = "2272c3692807"
DEFAULT_VISUAL_TASK_ID = "7527d04f1014"
DEFAULT_DEGRADED_TASK_ID = "3f8813a9dc5e"


def build_query_validation_report(
    service: DomainQueryService,
    *,
    target_task_id: str = DEFAULT_TARGET_TASK_ID,
    visual_task_id: str = DEFAULT_VISUAL_TASK_ID,
    degraded_task_id: str = DEFAULT_DEGRADED_TASK_ID,
) -> dict[str, Any]:
    snapshot = service.get_task_snapshot(target_task_id)
    repeated_snapshot = service.get_task_snapshot(target_task_id)
    overview = service.get_task_overview(target_task_id)
    decision = service.aggregate_findings(
        target_task_id, FindingFilters(), ["decision"], ["count", "percentage"]
    )
    risk = service.aggregate_findings(
        target_task_id, FindingFilters(), ["risk_level"], ["count"]
    )
    primary = service.aggregate_findings(
        target_task_id, FindingFilters(), ["primary_risk"], ["count"]
    )
    evidence_aggregation = service.aggregate_findings(
        target_task_id,
        FindingFilters(),
        ["evidence_type"],
        ["finding_count", "evidence_count", "percentage"],
    )
    sql = _independent_sql(service.repository.db_path, target_task_id)
    service_decision = _count_distribution(decision, "decision")
    service_risk = _count_distribution(risk, "risk_level")

    evidence_samples: dict[str, Any] = {}
    for evidence_type in ("comment", "ocr", "asr", "keyframe"):
        evidence_samples[evidence_type] = _find_real_evidence_sample(
            service, target_task_id, evidence_type
        )
    evidence_samples["visual"] = _find_real_evidence_sample(
        service, visual_task_id, "visual"
    )

    valid_link: dict[str, Any] = {}
    invalid_link: dict[str, Any] = {}
    available_comment_samples = [
        item
        for item in service.search_findings(
            target_task_id,
            FindingFilters(evidence_types=("comment",)),
            Pagination(page_size=2),
        ).data.items
    ]
    if len(available_comment_samples) >= 2:
        source_finding_id = available_comment_samples[0].finding_id
        other_finding_id = available_comment_samples[1].finding_id
        evidence_id = service.get_finding_evidence(
            source_finding_id, ["comment"], 1
        ).data[0].evidence_id
        valid_link = service.validate_finding_evidence_link(
            source_finding_id, evidence_id
        ).data.model_dump(mode="json")
        invalid_link = service.validate_finding_evidence_link(
            other_finding_id, evidence_id
        ).data.model_dump(mode="json")

    degraded = service.search_findings(
        degraded_task_id,
        FindingFilters(data_quality=(DataQuality.DEGRADED_EXTERNAL_FILE_MISSING,)),
        Pagination(page_size=100),
    )
    degraded_records = []
    for card in degraded.data.items:
        detail = service.get_finding_detail(card.finding_id)
        degraded_records.append(
            {
                "task_id": degraded_task_id,
                "finding_id": card.finding_id,
                "audit_result_id": card.audit_result_id,
                "data_quality": [item.value for item in card.data_quality],
                "warning_codes": sorted({item.code.value for item in detail.warnings}),
                "structured_finding_available": True,
            }
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_path": str(service.repository.db_path),
        "database_mode": "read_only",
        "target_task_id": target_task_id,
        "task_snapshot": snapshot.data.model_dump(mode="json", exclude={"statistic_inputs"}),
        "task_overview": overview.data.model_dump(mode="json"),
        "source_manifest_count": len(snapshot.sources),
        "snapshot_source_hash_stable": snapshot.data.source_hash == repeated_snapshot.data.source_hash,
        "search_counts": {
            "review": service.search_findings(
                target_task_id, FindingFilters(decision="review"), Pagination(page_size=100)
            ).data.total,
            "reject": service.search_findings(
                target_task_id, FindingFilters(decision="reject"), Pagination(page_size=100)
            ).data.total,
            "high_risk": service.search_findings(
                target_task_id, FindingFilters(risk_level="high"), Pagination(page_size=100)
            ).data.total,
        },
        "aggregations": {
            "decision": [item.model_dump(mode="json") for item in decision.data.rows],
            "risk_level": [item.model_dump(mode="json") for item in risk.data.rows],
            "primary_risk": [item.model_dump(mode="json") for item in primary.data.rows],
            "evidence_type": [
                item.model_dump(mode="json") for item in evidence_aggregation.data.rows
            ],
        },
        "independent_sql_comparison": {
            "sql": sql,
            "service": {
                "content_count": snapshot.data.content_count,
                "audit_result_count": snapshot.data.audit_result_count,
                "decision_distribution": service_decision,
                "risk_level_distribution": service_risk,
            },
            "matches": {
                "content_count": snapshot.data.content_count == sql["content_count"],
                "audit_result_count": snapshot.data.audit_result_count == sql["audit_result_count"],
                "decision_distribution": service_decision == sql["decision_distribution"],
                "risk_level_distribution": service_risk == sql["risk_level_distribution"],
            },
        },
        "real_evidence_samples": evidence_samples,
        "link_validation": {"valid": valid_link, "cross_finding_invalid": invalid_link},
        "degraded_external_index_records": degraded_records,
        "unsupported_stage_1_capabilities": [
            "report/report_version/claim queries",
            "cross-task entity search",
            "relationship queries",
            "similar-case search",
            "trend queries",
            "user corrections",
            "create or continue investigation",
        ],
    }


def _find_real_evidence_sample(
    service: DomainQueryService,
    task_id: str,
    evidence_type: str,
) -> dict[str, Any]:
    search = service.search_findings(
        task_id,
        FindingFilters(evidence_types=(evidence_type,)),
        Pagination(page_size=100),
    )
    if not search.data.items:
        return {
            "available": False,
            "task_id": task_id,
            "evidence_type": evidence_type,
            "reason": "no real Evidence of this type exists in the selected task",
        }
    selected = None
    for card in search.data.items:
        for brief in service.get_finding_evidence(card.finding_id, [evidence_type], 100).data:
            detail = service.get_evidence_detail(brief.evidence_id, 2)
            candidate = (card.finding_id, brief, detail)
            if selected is None:
                selected = candidate
            if evidence_type in {"ocr", "asr", "keyframe"} and detail.data.timestamp_start is not None:
                selected = candidate
                break
            if evidence_type == "comment" and detail.data.original_text:
                selected = candidate
                break
            if evidence_type == "visual" and (
                detail.data.asset_available or detail.data.structured_content_available
            ):
                selected = candidate
                break
        if selected and (
            evidence_type not in {"ocr", "asr", "keyframe"}
            or selected[2].data.timestamp_start is not None
        ):
            break
    finding_id, brief, detail = selected
    return {
        "available": True,
        "task_id": task_id,
        "finding_id": finding_id,
        "evidence_id": brief.evidence_id,
        "evidence_type": evidence_type,
        "source_format": detail.data.source_format,
        "original_text_preview": detail.data.original_text[:160],
        "translated_text_preview": detail.data.translated_text[:160],
        "timestamp_start": detail.data.timestamp_start,
        "timestamp_end": detail.data.timestamp_end,
        "asset_path": detail.data.asset_path,
        "asset_available": detail.data.asset_available,
        "structured_content_available": detail.data.structured_content_available,
        "availability": detail.data.availability.value,
        "data_quality": [item.value for item in detail.data.data_quality],
        "warning_codes": sorted({item.code.value for item in detail.warnings}),
    }


def _count_distribution(aggregation, dimension: str) -> dict[str, int]:
    return {
        item.group[dimension]: int(item.value)
        for item in aggregation.data.rows
        if item.metric == "count"
    }


def _independent_sql(db_path: Path, task_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    with connection:
        content_count = connection.execute(
            "SELECT COUNT(*) FROM task_contents WHERE task_id = ?", (task_id,)
        ).fetchone()[0]
        audit_result_count = connection.execute(
            "SELECT COUNT(*) FROM audit_results WHERE job_id = ?", (task_id,)
        ).fetchone()[0]
        decision = {
            row["value"]: row["count"]
            for row in connection.execute(
                "SELECT decision AS value, COUNT(*) AS count FROM audit_results WHERE job_id = ? GROUP BY decision",
                (task_id,),
            )
        }
        risk = {
            row["value"]: row["count"]
            for row in connection.execute(
                "SELECT risk_level AS value, COUNT(*) AS count FROM audit_results WHERE job_id = ? GROUP BY risk_level",
                (task_id,),
            )
        }
    return {
        "content_count": int(content_count),
        "audit_result_count": int(audit_result_count),
        "decision_distribution": decision,
        "risk_level_distribution": risk,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Stage 1 DomainQueryService")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--outputs-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--task-id", default=DEFAULT_TARGET_TASK_ID)
    parser.add_argument("--visual-task-id", default=DEFAULT_VISUAL_TASK_ID)
    parser.add_argument("--degraded-task-id", default=DEFAULT_DEGRADED_TASK_ID)
    args = parser.parse_args()

    service = DomainQueryService(
        DomainRepository(args.db, outputs_dir=args.outputs_dir)
    )
    report = build_query_validation_report(
        service,
        target_task_id=args.task_id,
        visual_task_id=args.visual_task_id,
        degraded_task_id=args.degraded_task_id,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    matches = report["independent_sql_comparison"]["matches"]
    return 0 if all(matches.values()) and report["snapshot_source_hash_stable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
