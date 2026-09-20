from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Depends
from pathlib import Path

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
from backend.reporting.errors import ReportGenerationError
from backend.reporting.media import snapshot_asset_relative_path, snapshot_media_response
from backend.reporting.runtime import R31ReportRuntime
from backend.reporting.store import ReportStore
from backend.investigation_creation.principal import (
    LocalPrincipalProvider,
    Principal,
    PrincipalProvider,
)


def create_reporting_router(
    store: ReportStore,
    runtime: R31ReportRuntime | None = None,
    *,
    principal_provider: PrincipalProvider | None = None,
    m3_run_store: object | None = None,
    historical_report_service: object | None = None,
    outputs_dir: Path | None = None,
    auth_service: object | None = None,
) -> APIRouter:
    router = APIRouter(tags=["reports"])
    structured_runtime = runtime or R31ReportRuntime(store)
    provide_principal = principal_provider or LocalPrincipalProvider()

    @router.get(
        "/api/tasks/{task_id}/report-versions",
        response_model=ReportVersionListResponse,
    )
    def list_published_report_versions(
        task_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> ReportVersionListResponse:
        require_historical_task_access(task_id, principal)
        versions = tuple(
            version
            for version in store.list_published_versions_for_task(task_id)
            if can_read_m3_report(
                m3_run_store,
                principal,
                report_version_id=str(version["id"]),
                task_id=task_id,
                auth_service=auth_service,
            )
        )
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
        principal: Principal = Depends(provide_principal),
    ) -> PublishedReportDetailResponse:
        require_historical_report_access(report_version_id, principal)
        task_id = published_report_task_id(store, report_version_id)
        if not can_read_m3_report(
            m3_run_store,
            principal,
            report_version_id=report_version_id,
            task_id=task_id,
            auth_service=auth_service,
        ):
            raise HTTPException(status_code=404, detail="Published report version not found")
        return published_report_detail_response(
            store, report_version_id
        )

    @router.get("/api/report-versions/{report_version_id}/structured-report")
    def get_structured_report_version(
        report_version_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, object]:
        require_historical_report_access(report_version_id, principal)
        task_id = published_report_task_id(store, report_version_id)
        if not can_read_m3_report(
            m3_run_store,
            principal,
            report_version_id=report_version_id,
            task_id=task_id,
            auth_service=auth_service,
        ):
            raise HTTPException(status_code=404, detail="Published report version not found")
        try:
            return structured_runtime.get_frontend_report(report_version_id)
        except ReportGenerationError as exc:
            message = str(exc)
            status = 404 if "not found" in message.lower() else 500
            raise HTTPException(status_code=status, detail=message) from exc

    def require_published_access(
        report_version_id: str, principal: Principal
    ) -> None:
        require_historical_report_access(report_version_id, principal)
        task_id = published_report_task_id(store, report_version_id)
        if not can_read_m3_report(
            m3_run_store,
            principal,
            report_version_id=report_version_id,
            task_id=task_id,
            auth_service=auth_service,
        ):
            raise HTTPException(status_code=404, detail="Published report version not found")

    def require_historical_report_access(
        report_version_id: str, principal: Principal
    ) -> None:
        if historical_report_service is None:
            return
        try:
            historical_report_service.require_report_access(
                report_version_id, principal_id=principal.id
            )
        except Exception as exc:
            raise HTTPException(
                status_code=404, detail="Published report version not found"
            ) from exc

    def require_historical_task_access(task_id: str, principal: Principal) -> None:
        if historical_report_service is None:
            return
        try:
            historical_report_service.require_task_access(
                task_id, principal_id=principal.id
            )
        except Exception as exc:
            raise HTTPException(
                status_code=404, detail="Published report version not found"
            ) from exc

    def presentation_call(operation):
        try:
            return operation()
        except ReportGenerationError as exc:
            message = str(exc)
            status = 404 if "not found" in message.lower() else 400
            raise HTTPException(status_code=status, detail=message) from exc

    @router.get(
        "/api/report-versions/{report_version_id}/presentation-projection"
    )
    def get_presentation_projection(
        report_version_id: str,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, object]:
        require_published_access(report_version_id, principal)
        return presentation_call(
            lambda: store.get_presentation_projection(report_version_id)
        )

    @router.get("/api/report-versions/{report_version_id}/accounts")
    def list_presentation_accounts(
        report_version_id: str,
        role: str | None = None,
        account_filter: str | None = Query(default=None, alias="filter"),
        search: str | None = Query(default=None, max_length=80),
        sort_order: str | None = Query(default=None, alias="sort"),
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = None,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, object]:
        require_published_access(report_version_id, principal)
        return presentation_call(
            lambda: store.list_presentation_account_entries(
                report_version_id,
                role=role,
                account_filter=account_filter,
                search=search,
                sort_order=sort_order,
                limit=limit,
                cursor=cursor,
            )
        )

    @router.get(
        "/api/report-versions/{report_version_id}/accounts/{entry_ref}"
    )
    def get_presentation_account(
        report_version_id: str,
        entry_ref: str,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, object]:
        require_published_access(report_version_id, principal)
        return presentation_call(
            lambda: store.get_presentation_account_detail(
                report_version_id, entry_ref=entry_ref
            )
        )

    @router.get("/api/report-versions/{report_version_id}/posts/{post_ref}/audit-detail")
    def get_snapshot_audit_detail(
        report_version_id: str, post_ref: str,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, object]:
        require_published_access(report_version_id, principal)
        return presentation_call(lambda: store.get_snapshot_audit_detail(report_version_id, post_ref=post_ref))

    @router.get("/api/report-versions/{report_version_id}/posts/{post_ref}/assets")
    def get_snapshot_asset(
        report_version_id: str, post_ref: str, path: str, request: Request,
        principal: Principal = Depends(provide_principal),
    ):
        require_published_access(report_version_id, principal)
        detail = presentation_call(lambda: store.get_snapshot_audit_detail(report_version_id, post_ref=post_ref))
        item = detail["audit_result"]
        job_id = str(item["job_id"])
        marker = f"/outputs/{job_id}/"
        saved_paths = set()

        def collect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"asset_rel", "local_path", "original_path", "path"} and isinstance(child, str):
                        saved_paths.add(child.split(marker, 1)[-1] if marker in child else child)
                    elif isinstance(child, (dict, list)):
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(item)
        if outputs_dir is None or path not in saved_paths:
            raise HTTPException(status_code=404, detail="Asset not found in report post")
        root = (outputs_dir / job_id).resolve()
        target = (root / snapshot_asset_relative_path(path)).resolve()
        if not root.is_relative_to(outputs_dir.resolve()) or not target.is_relative_to(root):
            raise HTTPException(status_code=400, detail="Invalid asset path")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Saved asset file not found")
        return snapshot_media_response(target, request.headers.get("range"))

    @router.get("/api/report-versions/{report_version_id}/posts/{post_ref}")
    def get_presentation_post(
        report_version_id: str,
        post_ref: str,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, object]:
        require_published_access(report_version_id, principal)
        return presentation_call(
            lambda: store.get_presentation_post_detail(
                report_version_id, post_ref=post_ref
            )
        )

    @router.get(
        "/api/report-versions/{report_version_id}/findings/"
        "{investigation_finding_ref}/evidence"
    )
    def get_presentation_finding_evidence(
        report_version_id: str,
        investigation_finding_ref: str,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, object]:
        require_published_access(report_version_id, principal)
        return presentation_call(
            lambda: store.get_presentation_finding_evidence(
                report_version_id,
                investigation_finding_ref=investigation_finding_ref,
            )
        )

    @router.get("/api/report-versions/{report_version_id}/appendix")
    def get_presentation_appendix(
        report_version_id: str,
        view: str = "posts",
        finding_ref: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        principal: Principal = Depends(provide_principal),
    ) -> dict[str, object]:
        require_published_access(report_version_id, principal)
        return presentation_call(
            lambda: store.get_presentation_appendix(
                report_version_id,
                view=view,
                finding_ref=finding_ref,
                limit=limit,
                cursor=cursor,
            )
        )

    return router


def published_report_detail_response(
    store: ReportStore, report_version_id: str
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
        raise HTTPException(
            status_code=500, detail="Published report metadata is unavailable"
        )
    return PublishedReportDetailResponse(
        report_version_id=str(version["id"]),
        report_id=str(version["report_id"]),
        task_id=str(report["task_id"]),
        version_number=int(version["version_number"]),
        title=str(version.get("title") or presentation.title),
        published_at=str(version.get("published_at") or ""),
        presentation=_presentation_response(presentation),
    )


def published_report_task_id(store: ReportStore, report_version_id: str) -> str:
    version = store.get_version(report_version_id)
    if version is None or version.get("status") != "published":
        raise HTTPException(status_code=404, detail="Published report version not found")
    report = store.get_report(str(version["report_id"]))
    if report is None:
        raise HTTPException(
            status_code=500, detail="Published report metadata is unavailable"
        )
    return str(report["task_id"])


def can_read_m3_report(
    run_store: object | None,
    principal: Principal,
    *,
    report_version_id: str,
    task_id: str,
    auth_service: object | None = None,
) -> bool:
    if auth_service is not None and principal.is_admin:
        return True
    if run_store is None:
        return auth_service is None or bool(
            auth_service.store.has_grant(
                principal.id, "report-version", report_version_id, "read"
            )
        )
    owners = run_store.owner_principals_for_report(
        report_version_id=report_version_id, task_id=task_id
    )
    if principal.id in owners:
        return True
    if auth_service is None:
        return not owners
    return bool(
        auth_service.store.has_grant(
            principal.id, "report-version", report_version_id, "read"
        )
    )


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
