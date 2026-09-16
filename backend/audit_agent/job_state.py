from __future__ import annotations

from typing import Any


RECOVERABLE_CRAWL_FAILURE_CODES = frozenset({
    "crawler_account_verification_required",
    "crawler_account_login_required",
    "crawler_rate_limited",
})


def failure_metadata(code: str, message: str = "") -> dict[str, Any]:
    """Persist recovery semantics separately from user-facing error text."""
    normalized = str(code or "").strip()
    recoverable = normalized in RECOVERABLE_CRAWL_FAILURE_CODES
    stage = (
        "crawl"
        if normalized.startswith("crawler_") or normalized == "no_valid_content_selected"
        else "analysis"
        if normalized.startswith("audit_provider_")
        else "pipeline"
    )
    return {
        "code": normalized,
        "stage": stage,
        "recoverable": recoverable,
        "recovery_action": "continue_crawl" if recoverable else "",
        "message": str(message or "").strip(),
    }


def failure_from_job(job: dict[str, Any]) -> dict[str, Any]:
    """Read structured failures, with a narrow compatibility path for old Jobs."""
    failure = (job.get("control") or {}).get("failure")
    if isinstance(failure, dict) and failure.get("code"):
        return dict(failure)
    error = str(job.get("error") or "").strip()
    code = error.split(":", 1)[0].strip()
    return failure_metadata(code, error) if code in RECOVERABLE_CRAWL_FAILURE_CODES else {}


def available_job_actions(job: dict[str, Any], stats: dict[str, Any]) -> dict[str, bool]:
    """Return the authoritative control surface for one persisted Job.

    Frontends must use this projection instead of recreating state-machine rules.
    """

    status = str(job.get("status") or "")
    crawl_status = str(job.get("crawl_status") or "")
    analysis_status = str(job.get("analysis_status") or "")
    control = dict(job.get("control") or {})
    failure = failure_from_job(job)
    has_pending = int(stats.get("pending_analysis_count") or 0) > 0
    has_failed = int(stats.get("failed_analysis_count") or 0) > 0
    has_analyzing = int(stats.get("analyzing_count") or 0) > 0
    has_retriable = has_pending or has_failed
    deleting_or_stopping_all = status == "stopping" or bool(
        control.get("stop_all_requested")
    )

    crawl_active = bool(job.get("run_crawler")) and crawl_status in {
        "queued",
        "running",
    }
    analysis_active = (
        analysis_status in {"queued", "running"}
        and not control.get("analysis_stop_requested")
        and not control.get("analysis_paused")
        and not control.get("stop_all_requested")
    )
    analysis_stopping = status == "analysis_stopping" or bool(
        control.get("analysis_stop_requested")
    )
    can_backfill = (
        has_retriable
        and not deleting_or_stopping_all
        and status
        not in {"queued", "running", "crawl_pausing", "analysis_running"}
    )
    if analysis_stopping:
        can_backfill = (
            has_retriable or has_analyzing or status == "analysis_stopping"
        ) and not deleting_or_stopping_all

    return {
        "pause_crawl": crawl_active and not deleting_or_stopping_all,
        "resume_crawl": bool(job.get("run_crawler"))
        and (
            crawl_status in {"stopped", "interrupted"}
            or (
                crawl_status == "failed"
                and failure.get("recoverable") is True
                and failure.get("stage") == "crawl"
                and failure.get("recovery_action") == "continue_crawl"
            )
        )
        and not deleting_or_stopping_all,
        "pause_analysis": analysis_active,
        "resume_analysis": analysis_status
        in {"paused", "stopped", "pending", "partial", "failed"}
        and (has_retriable or crawl_active)
        and not deleting_or_stopping_all,
        "stop_analysis": analysis_active,
        "backfill_analysis": can_backfill,
        "delete_job": bool(job.get("id")),
    }
