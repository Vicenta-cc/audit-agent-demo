from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.contracts import (
    PublishedReportDetailResponse,
    PublishedReportPresentationResponse,
    ReportCaseBlockResponse,
    ReportCitationActionResponse,
    ReportKeyMetricResponse,
    ReportSectionResponse,
    ReportTextBlockResponse,
    ReportVersionListResponse,
    ReportVersionSummaryResponse,
)
from backend.reporting.contracts import HumanReportDTO
from backend.reporting.store import ReportStore


def create_reporting_router(store: ReportStore) -> APIRouter:
    router = APIRouter(tags=["reports"])

    @router.get(
        "/api/tasks/{task_id}/report-versions",
        response_model=ReportVersionListResponse,
    )
    def list_published_report_versions(task_id: str) -> ReportVersionListResponse:
        versions = store.list_published_versions_for_task(task_id)
        items = tuple(_summary_response(item, task_id=task_id) for item in versions)
        return ReportVersionListResponse(
            task_id=task_id,
            items=items,
            latest_report_version_id=items[0].report_version_id if items else None,
        )

    @router.get(
        "/api/report-versions/{report_version_id}",
        response_model=PublishedReportDetailResponse,
    )
    def get_published_report_version(
        report_version_id: str,
    ) -> PublishedReportDetailResponse:
        version = store.get_version(report_version_id)
        if version is None or version.get("status") != "published":
            raise HTTPException(status_code=404, detail="Published report version not found")
        human_report = store.get_human_report(report_version_id)
        if human_report is None:
            raise HTTPException(
                status_code=500,
                detail="Published report presentation is unavailable",
            )
        try:
            presentation = HumanReportDTO.model_validate(human_report)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail="Published report presentation is invalid",
            ) from exc
        report = store.get_report(str(version["report_id"]))
        if report is None:
            raise HTTPException(status_code=500, detail="Published report metadata is unavailable")
        return PublishedReportDetailResponse(
            report_version_id=str(version["id"]),
            report_id=str(version["report_id"]),
            task_id=str(report["task_id"]),
            version_number=int(version["version_number"]),
            title=str(version.get("title") or presentation.title),
            published_at=str(version.get("published_at") or ""),
            presentation=_presentation_response(presentation),
        )

    return router


def _summary_response(
    version: dict[str, object], *, task_id: str
) -> ReportVersionSummaryResponse:
    body = version.get("body")
    human = body.get("human_report") if isinstance(body, dict) else None
    presentation_version = (
        str(human.get("presentation_version") or "human-report-v1")
        if isinstance(human, dict)
        else "human-report-v1"
    )
    return ReportVersionSummaryResponse(
        report_version_id=str(version["id"]),
        report_id=str(version["report_id"]),
        task_id=task_id,
        version_number=int(version["version_number"]),
        title=str(version.get("title") or "调查报告"),
        presentation_version=presentation_version,
        published_at=str(version.get("published_at") or ""),
    )


def _presentation_response(
    presentation: HumanReportDTO,
) -> PublishedReportPresentationResponse:
    return PublishedReportPresentationResponse(
        presentation_version=presentation.presentation_version,
        title=presentation.title,
        summary=ReportTextBlockResponse(text=presentation.summary.text),
        key_metrics=tuple(
            ReportKeyMetricResponse(
                label=item.label,
                value=item.value,
                detail=item.detail,
            )
            for item in presentation.key_metrics
        ),
        sections=tuple(
            ReportSectionResponse(
                section_id=section.section_id,
                title=section.title,
                paragraphs=tuple(
                    ReportTextBlockResponse(text=paragraph.text)
                    for paragraph in section.paragraphs
                ),
            )
            for section in presentation.sections
        ),
        case_blocks=tuple(
            ReportCaseBlockResponse(
                title=case.title,
                text=case.text,
                citation_actions=tuple(
                    ReportCitationActionResponse(
                        type=action.type,
                        label=action.label,
                        claim_ref=action.claim_id,
                    )
                    for action in case.citation_actions
                ),
            )
            for case in presentation.case_blocks
        ),
        conclusion=ReportTextBlockResponse(text=presentation.conclusion.text),
        data_quality_note=ReportTextBlockResponse(
            text=presentation.data_quality_note.text
        ),
    )

