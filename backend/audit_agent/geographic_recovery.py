"""Narrow recovery evidence: a route was unavailable before crawler invocation."""
from .crawler_route import CollectionRouteError


GEOGRAPHIC_VERIFICATION_BLOCKER = "geographic_account_verification_required"
GEOGRAPHIC_VERIFICATION_MESSAGE = "本次地域调查已因平台验证停止，已完成的审核结果会保留。请在采集账号管理中完成核验后，新建调查。"


def geographic_resume_message(blocker):
    if blocker == GEOGRAPHIC_VERIFICATION_BLOCKER:
        return GEOGRAPHIC_VERIFICATION_MESSAGE
    return "地域采集结果尚未确认，不能自动重复采集，请核对执行记录。"


class GeographicCaptureDeferred(CollectionRouteError):
    """Raised only by the geographic adapter's route acquisition boundary."""

    def __init__(self, code):
        if code not in {"PROXY_BUSY", "SUPPLEMENT_DEFERRED"}:
            raise ValueError("not a recoverable geographic preflight failure")
        super().__init__(code)


def snapshot_resume_blocker(snapshot):
    """Unknown external effects and invalid evidence require reconciliation."""
    if (snapshot.get("fatal_error") or {}).get("kind") == "crawler_account_verification_required":
        return GEOGRAPHIC_VERIFICATION_BLOCKER
    if snapshot.get("fatal_error") or any(q.get("auth_pending") for q in snapshot["queries"].values()):
        return "geographic_account_reconciliation_required"
    for query in snapshot["queries"].values():
        if query["status"] in {"failed", "capturing"}:
            if (query.get("error") or {}).get("type") == "CrawlerVerificationError":
                return GEOGRAPHIC_VERIFICATION_BLOCKER
            return "geographic_capture_reconciliation_required"
    if any(a["status"] in {"collecting", "unknown", "infrastructure"} for a in snapshot["attempts"].values()):
        return "geographic_detail_reconciliation_required"
    if any(q.get("closure") in {"closed_media_failure", "closed_auth_failure"}
           for q in snapshot["queries"].values()):
        return "geographic_collection_reconciliation_required"
    if has_pending_evaluations(snapshot['queries']):
        return "geographic_evaluation_reconciliation_required"
    return ""


def has_pending_evaluations(queries):
    if hasattr(queries, 'summary'):
        return any(queries.summary(qid)['pending_evaluations'] for qid in queries)
    return any(parts.get('geo', {}).get('result', {}).get('evaluation_status') == 'invalid_response'
               for query in queries.values() for parts in query.get('evaluations', {}).values())
