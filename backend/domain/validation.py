from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.domain.contracts import EvidenceType, SourceFormat
from backend.domain.repository import DomainRepository
from backend.domain.warnings import DataQuality


NORMALIZATION_POLICY = {
    "version": 1,
    "finding_unit": "one audit_result produces one post_audit_result FindingView",
    "reading_priority": [
        "result_json.evidence_items",
        "result_json.evidence_index.evidence_catalog",
        "external evidence_index fallback for missing embedded index fields",
        "legacy risk_evidence/risk_frames/risk_images",
        "inline rule_matches/score_breakdown evidence",
    ],
    "raw_source_promotion": "comments, OCR items, ASR segments and timeline frames enrich referenced evidence only",
    "deduplication_order": [
        "local evidence id within one audit_result",
        "stable comment/source/asset anchor",
        "exact content fingerprint for legacy and rule-reference candidates",
    ],
    "conflict_rule": "higher-priority source keeps non-empty values; lower-priority sources fill gaps and remain in provenance",
    "broken_rule_reference": "preserve as rule_reference with broken_reference quality",
}


def build_validation_report(
    repository: DomainRepository,
    *,
    expected_active_tasks: int | None = None,
    expected_audit_results: int | None = None,
) -> dict[str, Any]:
    audit_result_ids = repository.list_active_audit_result_ids()
    evidence_type_counts: Counter[str] = Counter()
    primary_source_format_counts: Counter[str] = Counter()
    source_format_presence_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    degraded_finding_ids: set[str] = set()
    degraded_evidence_ids: set[str] = set()
    degraded_findings_by_reason: dict[str, set[str]] = defaultdict(set)
    degraded_evidence_by_reason: dict[str, set[str]] = defaultdict(set)
    broken_references: set[tuple[int | None, str, str]] = set()
    findings_without_evidence: list[str] = []
    finding_conversion_failures: list[dict[str, Any]] = []
    external_missing_details: dict[int, dict[str, Any]] = {}
    normalized_evidence_count = 0
    candidate_evidence_count = 0
    duplicate_evidence_merged_count = 0
    finding_view_count = 0

    path_inventory = _audit_path_inventory(repository.db_path)
    path_by_result_id = {int(item["audit_result_id"]): item for item in path_inventory}

    for audit_result_id in audit_result_ids:
        try:
            finding_envelope = repository.get_finding_view(audit_result_id)
            evidence_envelope = repository.get_evidence_views(audit_result_id)
        except Exception as exc:  # validation must report every incompatible persisted row
            finding_conversion_failures.append(
                {
                    "audit_result_id": audit_result_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            continue

        finding = finding_envelope.data
        finding_view_count += 1
        candidate_evidence_count += int(finding_envelope.metadata.get("candidate_evidence_count") or 0)
        duplicate_evidence_merged_count += int(
            finding_envelope.metadata.get("duplicate_evidence_merged_count") or 0
        )
        if not evidence_envelope.data:
            findings_without_evidence.append(finding.finding_id)

        finding_reasons = [quality.value for quality in finding.data_quality if quality != DataQuality.COMPLETE]
        if finding_reasons:
            degraded_finding_ids.add(finding.finding_id)
            for reason in finding_reasons:
                degraded_findings_by_reason[reason].add(finding.finding_id)

        for evidence in evidence_envelope.data:
            normalized_evidence_count += 1
            evidence_type_counts[evidence.evidence_type.value] += 1
            primary_source_format_counts[evidence.source_format.value] += 1
            for source_format in set(evidence.source_formats):
                source_format_presence_counts[source_format.value] += 1
            evidence_reasons = [
                quality.value for quality in evidence.data_quality if quality != DataQuality.COMPLETE
            ]
            if evidence_reasons:
                degraded_evidence_ids.add(evidence.evidence_id)
                for reason in evidence_reasons:
                    degraded_evidence_by_reason[reason].add(evidence.evidence_id)

        for warning in finding_envelope.warnings:
            warning_counts[warning.code.value] += 1
            if warning.code == DataQuality.BROKEN_REFERENCE:
                broken_references.add(
                    (warning.audit_result_id, warning.evidence_id, warning.source_json_path)
                )
            if warning.code == DataQuality.DEGRADED_EXTERNAL_FILE_MISSING:
                inventory = path_by_result_id.get(audit_result_id, {})
                detail = external_missing_details.setdefault(
                    audit_result_id,
                    {
                        "audit_result_id": audit_result_id,
                        "task_id": finding.task_id,
                        "finding_id": finding.finding_id,
                        "evidence_index_path": inventory.get("evidence_index_path", ""),
                        "embedded_evidence_index_available": bool(
                            inventory.get("embedded_evidence_index_available")
                        ),
                        "data_quality": [],
                    },
                )
                detail["data_quality"] = sorted(set(detail["data_quality"]) | set(finding_reasons))

    traceability_issues = list(repository.traceability_issues())
    raw_missing_records = _missing_raw_item_records(repository.db_path)
    for record in raw_missing_records:
        record["data_quality"] = [DataQuality.DEGRADED_RAW_ITEM_MISSING.value]
        if record.get("audit_result_id"):
            record["finding_id"] = f"finding:audit_result:{record['audit_result_id']}"
            record["finding_status"] = "degraded"
        else:
            record["finding_id"] = ""
            record["finding_status"] = "not_available_no_audit_result"

    active_task_count = repository.active_task_count()
    audit_result_count = repository.audit_result_count()
    historical_task_count = _scalar(repository.db_path, "SELECT COUNT(*) FROM jobs")
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_path": str(repository.db_path),
        "database_mode": "read_only",
        "normalization_policy": NORMALIZATION_POLICY,
        "counts": {
            "historical_task_count": historical_task_count,
            "active_task_count": active_task_count,
            "audit_result_count": audit_result_count,
            "finding_view_count": finding_view_count,
            "normalized_evidence_count": normalized_evidence_count,
            "candidate_evidence_count": candidate_evidence_count,
            "duplicate_evidence_merged_count": duplicate_evidence_merged_count,
            "findings_without_evidence_count": len(findings_without_evidence),
            "degraded_finding_count": len(degraded_finding_ids),
            "degraded_evidence_count": len(degraded_evidence_ids),
            "broken_reference_count": len(broken_references),
            "external_evidence_index_missing_count": len(external_missing_details),
            "raw_item_missing_task_content_count": len(raw_missing_records),
            "raw_item_missing_finding_count": sum(
                1 for record in raw_missing_records if record.get("audit_result_id")
            ),
            "untraceable_audit_result_count": len(traceability_issues),
            "finding_conversion_failure_count": len(finding_conversion_failures),
        },
        "evidence_type_distribution": {
            evidence_type.value: evidence_type_counts[evidence_type.value]
            for evidence_type in EvidenceType
        },
        "source_format_distribution": {
            source_format.value: primary_source_format_counts[source_format.value]
            for source_format in SourceFormat
            if source_format != SourceFormat.AUDIT_RESULT
        },
        "source_format_presence_distribution": {
            source_format.value: source_format_presence_counts[source_format.value]
            for source_format in SourceFormat
            if source_format != SourceFormat.AUDIT_RESULT
        },
        "warning_distribution": dict(sorted(warning_counts.items())),
        "degraded_findings_by_reason": {
            reason: sorted(values) for reason, values in sorted(degraded_findings_by_reason.items())
        },
        "degraded_evidence_by_reason": {
            reason: sorted(values) for reason, values in sorted(degraded_evidence_by_reason.items())
        },
        "findings_without_evidence": findings_without_evidence,
        "external_evidence_index_missing_records": list(external_missing_details.values()),
        "raw_item_missing_records": raw_missing_records,
        "broken_references": [
            {
                "audit_result_id": audit_result_id,
                "evidence_id": evidence_id,
                "source_json_path": source_json_path,
            }
            for audit_result_id, evidence_id, source_json_path in sorted(
                broken_references,
                key=lambda item: (item[0] or 0, item[1], item[2]),
            )
        ],
        "traceability_issues": traceability_issues,
        "finding_conversion_failures": finding_conversion_failures,
        "baseline": {
            "expected_active_task_count": expected_active_tasks,
            "expected_audit_result_count": expected_audit_results,
            "active_task_count_matches": (
                expected_active_tasks is None or expected_active_tasks == active_task_count
            ),
            "audit_result_count_matches": (
                expected_audit_results is None or expected_audit_results == audit_result_count
            ),
        },
    }
    return report


def validation_passed(report: dict[str, Any]) -> bool:
    counts = report["counts"]
    baseline = report["baseline"]
    return all(
        (
            baseline["active_task_count_matches"],
            baseline["audit_result_count_matches"],
            counts["audit_result_count"] == counts["finding_view_count"],
            counts["finding_conversion_failure_count"] == 0,
            counts["untraceable_audit_result_count"] == 0,
        )
    )


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _scalar(db_path: Path, query: str) -> int:
    with _connect_read_only(db_path) as connection:
        row = connection.execute(query).fetchone()
    return int(row[0] or 0) if row else 0


def _audit_path_inventory(db_path: Path) -> list[dict[str, Any]]:
    with _connect_read_only(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                ar.id AS audit_result_id,
                ar.job_id AS task_id,
                json_extract(ar.result_json, '$.evidence_index_path') AS evidence_index_path,
                CASE
                    WHEN json_type(ar.result_json, '$.evidence_index') = 'object' THEN 1
                    ELSE 0
                END AS embedded_evidence_index_available
            FROM audit_results ar
            JOIN jobs j ON j.id = ar.job_id
            WHERE j.archived = 0
            ORDER BY ar.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _missing_raw_item_records(db_path: Path) -> list[dict[str, Any]]:
    with _connect_read_only(db_path) as connection:
        rows = connection.execute(
            """
            SELECT
                tc.task_id,
                tc.content_id,
                COALESCE(tc.raw_item_path, c.raw_item_path, '') AS raw_item_path,
                ar.id AS audit_result_id
            FROM task_contents tc
            JOIN jobs j ON j.id = tc.task_id AND j.archived = 0
            LEFT JOIN contents c ON c.id = tc.content_id
            LEFT JOIN audit_results ar ON ar.job_id = tc.task_id AND ar.content_id = tc.content_id
            ORDER BY tc.task_id, tc.content_id
            """
        ).fetchall()
    output = []
    for row in rows:
        record = dict(row)
        raw_path = str(record.get("raw_item_path") or "")
        if raw_path and Path(raw_path).is_file():
            continue
        output.append(record)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate normalized domain views against the real audit database")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--outputs-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--expected-active-tasks", type=int, default=None)
    parser.add_argument("--expected-audit-results", type=int, default=None)
    args = parser.parse_args()

    repository = DomainRepository(args.db, outputs_dir=args.outputs_dir)
    report = build_validation_report(
        repository,
        expected_active_tasks=args.expected_active_tasks,
        expected_audit_results=args.expected_audit_results,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)
    return 0 if validation_passed(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
