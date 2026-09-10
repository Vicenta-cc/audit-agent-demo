"""Publish an R3.1 target-Account correction without calling a model."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.domain.identity import stable_hash
from backend.reporting.account_entries import DEFAULT_ACCOUNT_FIXTURE_PATH
from backend.reporting.account_overview import (
    ReportAccountOverviewProjector,
    public_account_overview_projection,
)
from backend.reporting.errors import ReportGenerationError, ReportValidationError
from backend.reporting.integration_source import CanonicalReportSource, source_sha256
from backend.reporting.r31_graph import AccountOverviewReportGraph
from backend.reporting.store import ReportStore


CORRECTION_KIND = "report-r3.1-target-account-role-correction/v1"


def _clone_snapshot(
    store: ReportStore,
    *,
    source_report_version_id: str,
    target_report_version_id: str,
) -> str:
    manifest = store.get_source_snapshot(source_report_version_id)
    if manifest is None:
        raise ReportGenerationError("source ReportVersion Snapshot is missing")
    snapshot_id = (
        f"snapshot-{manifest['snapshot_hash'][:16]}-correction-{uuid4().hex[:8]}"
    )
    store.save_source_snapshot(
        {
            "snapshot_id": snapshot_id,
            "report_version_id": target_report_version_id,
            "task_id": manifest["task_id"],
            "task_status": manifest["task_status"],
            "source_hash": manifest["source_hash"],
            "configuration_revision_id": manifest["configuration_revision_id"],
            "finding_ids": list(manifest["finding_ids"]),
            "evidence_ids": list(manifest["evidence_ids"]),
            "data_quality_warnings": list(manifest["data_quality_warnings"]),
            "statistic_inputs": deepcopy(manifest["statistic_inputs"]),
            "generated_at": manifest["generated_at"],
            "snapshot_hash": manifest["snapshot_hash"],
            "display_name": manifest["display_name"],
            "source_revision": manifest["source_revision"],
            "relation_hash": manifest["relation_hash"],
            "statistics": dict(manifest["statistics"]),
        }
    )
    payloads = store.list_snapshot_payloads(source_report_version_id)
    store.save_snapshot_payloads(
        target_report_version_id,
        posts=[
            {
                "ref": item["post_ref"],
                "canonical_key": item["canonical_key"],
                "revision": item["revision"],
                "payload": item["payload"],
                "payload_hash": item["payload_hash"],
            }
            for item in payloads["posts"]
        ],
        findings=[
            {
                "ref": item["finding_ref"],
                "audit_result_id": item["audit_result_id"],
                "post_ref": item["post_ref"],
                "payload": item["payload"],
                "payload_hash": item["payload_hash"],
            }
            for item in payloads["findings"]
        ],
        evidence=[
            {
                "ref": item["evidence_ref"],
                "audit_result_id": item["audit_result_id"],
                "post_ref": item["post_ref"],
                "finding_ref": item["finding_ref"],
                "support_type": item["support_type"],
                "source_formats": list(item["source_formats"]),
                "payload": item["payload"],
                "payload_hash": item["payload_hash"],
            }
            for item in payloads["evidence"]
        ],
    )
    return snapshot_id


def _reuse_model_steps(
    store: ReportStore,
    *,
    source_report_version_id: str,
    target_report_version_id: str,
) -> int:
    source_steps = store.list_model_steps(source_report_version_id)
    if not source_steps or any(item["status"] != "succeeded" for item in source_steps):
        raise ReportGenerationError(
            "source ReportVersion does not have a complete successful model-step set"
        )
    for item in source_steps:
        store.save_model_step(
            fingerprint=stable_hash(
                {
                    "correction_kind": CORRECTION_KIND,
                    "source_fingerprint": item["fingerprint"],
                    "target_report_version_id": target_report_version_id,
                }
            ),
            report_version_id=target_report_version_id,
            node_name=item["node_name"],
            section_id=item["section_id"],
            prompt_version=item["prompt_version"],
            input_hash=item["input_hash"],
            model=item["model"],
            status="succeeded",
            output=deepcopy(item["output"]),
            usage=deepcopy(item["usage"]),
        )
    return len(source_steps)


def _rebind_investigation_findings(
    findings: list[dict[str, Any]], report_version_id: str
) -> list[dict[str, Any]]:
    rebound = deepcopy(findings)
    for item in rebound:
        alias = str(item.get("alias") or "").strip()
        if not alias:
            raise ReportGenerationError(
                "source InvestigationFinding is missing its stable alias"
            )
        item["finding_ref"] = (
            f"investigation-finding:{report_version_id}:{alias}"
        )
    return rebound


def _rebuild_public_ordered_sections(
    sections: list[dict[str, Any]],
    source_public_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(sections) != len(source_public_sections):
        raise ReportGenerationError(
            "source structured section projections have different lengths"
        )
    rebuilt: list[dict[str, Any]] = []
    for index, (section, source_public) in enumerate(
        zip(sections, source_public_sections), 1
    ):
        if source_public.get("title") != section.get("title"):
            raise ReportGenerationError(
                "source structured section projections disagree"
            )
        public = deepcopy(source_public)
        public.update(
            {
                "order": index,
                "section_ref": section["section_ref"],
                "section_number": section["section_number"],
                "parent_section_ref": section.get("parent_section_ref"),
                "section_type": section["section_type"],
                "display_ordinal": section["display_ordinal"],
                "title": section["title"],
                "purpose": section.get("purpose") or "",
                "paragraphs": [
                    {"text": str(item.get("text") or "")}
                    for item in section.get("paragraphs") or []
                ],
            }
        )
        rebuilt.append(public)
    return rebuilt


def publish_target_account_role_correction(
    *,
    store: ReportStore,
    source_report_version_id: str,
    report_source: CanonicalReportSource,
    account_fixture_path: Path = DEFAULT_ACCOUNT_FIXTURE_PATH,
) -> dict[str, Any]:
    """Create and publish a corrected version entirely from persisted structures."""
    source_report = store.get_full_version(source_report_version_id)
    if source_report is None or source_report.get("status") != "published":
        raise ReportGenerationError("source ReportVersion is not published")
    snapshot = store.load_immutable_snapshot(source_report_version_id)
    if source_sha256(report_source.db_path) != snapshot.source_db_sha256:
        raise ReportValidationError(
            "correct_target_account_role",
            ["the live task configuration database differs from the frozen source"],
        )
    if report_source.task_name(snapshot.task_id) != snapshot.display_name:
        raise ReportValidationError(
            "correct_target_account_role",
            ["the task configuration does not match the frozen Report task"],
        )

    generation = store.create_generation(
        snapshot.task_id,
        model=str(source_report["model"]),
        prompt_version=str(source_report["prompt_version"]),
    )
    run_id = generation["run_id"]
    report_version_id = generation["report_version_id"]
    store.record_run_event(
        run_id,
        "correct_target_account_role",
        "started",
        {
            "correction_kind": CORRECTION_KIND,
            "source_report_version_id": source_report_version_id,
            "source_content_hash": source_report["content_hash"],
            "provider_request_count": 0,
        },
    )

    snapshot_id = _clone_snapshot(
        store,
        source_report_version_id=source_report_version_id,
        target_report_version_id=report_version_id,
    )
    corrected_snapshot = store.load_immutable_snapshot(report_version_id)
    if corrected_snapshot.snapshot_hash != snapshot.snapshot_hash:
        raise ReportGenerationError("corrected Report Snapshot hash changed")

    reused_model_step_count = _reuse_model_steps(
        store,
        source_report_version_id=source_report_version_id,
        target_report_version_id=report_version_id,
    )
    projection = ReportAccountOverviewProjector.load(account_fixture_path).build(
        corrected_snapshot,
        target_account_identity=report_source.creator_account_identity(
            corrected_snapshot.task_id
        ),
    )
    public_projection = public_account_overview_projection(projection)

    body_json = deepcopy(source_report["body"])
    sections = deepcopy(body_json["audit_model"]["sections"])
    investigation_findings = _rebind_investigation_findings(
        body_json["audit_model"]["investigation_findings"], report_version_id
    )
    body_json["audit_model"]["investigation_findings"] = investigation_findings
    body_json["audit_model"]["source_snapshot_id"] = snapshot_id
    body_json["account_model"] = public_projection

    document = body_json["report_document"]
    source_public_sections = deepcopy(document["ordered_sections"])
    document["ordered_sections"] = _rebuild_public_ordered_sections(
        sections, source_public_sections
    )
    document["account_coverage_statistics"] = public_projection[
        "account_coverage_statistics"
    ]
    document["target_account_entries"] = public_projection[
        "target_account_entries"
    ]
    document["default_active_comment_entries"] = public_projection[
        "default_active_comment_entries"
    ]
    document["full_account_index"] = public_projection["full_account_index"]
    document["account_scope_boundary"] = public_projection["scope_boundary"]

    body_markdown = AccountOverviewReportGraph._render_markdown(
        str(source_report["title"]), sections, public_projection
    )
    citation_details = {
        str(item["evidence_id"]): {
            "citation_excerpt": item["citation_excerpt"],
            "asset_status": item["asset_status"],
        }
        for item in source_report["claim_evidence"]
    }
    categories = deepcopy(body_json["audit_model"]["categories"])
    standalone = deepcopy(body_json["audit_model"]["standalone_risk_posts"])

    if store.list_provider_exchanges(report_version_id):
        raise ReportGenerationError("target Account correction cannot call a Provider")
    published = store.publish_version(
        report_version_id=report_version_id,
        title=str(source_report["title"]),
        body_markdown=body_markdown,
        body_json=body_json,
        sections=sections,
        citation_details=citation_details,
        categories=categories,
        investigation_findings=investigation_findings,
        standalone_risk_posts=standalone,
        account_projection=projection,
    )
    store.update_run(
        run_id,
        status="completed",
        current_node="publish_target_account_role_correction",
        warnings=[],
    )
    store.record_run_event(
        run_id,
        "publish_target_account_role_correction",
        "published",
        {
            "source_report_version_id": source_report_version_id,
            "report_version_id": report_version_id,
            "snapshot_hash": corrected_snapshot.snapshot_hash,
            "reused_model_step_count": reused_model_step_count,
            "provider_request_count": 0,
            **projection["statistics"],
        },
    )
    return {
        "run_id": run_id,
        "report_id": generation["report_id"],
        "report_version_id": report_version_id,
        "version_number": generation["version_number"],
        "status": published["status"],
        "content_hash": published["content_hash"],
        "snapshot_hash": corrected_snapshot.snapshot_hash,
        "account_projection_hash": projection["projection_hash"],
        "account_statistics": dict(projection["statistics"]),
        "reused_model_step_count": reused_model_step_count,
        "provider_request_count": 0,
    }
