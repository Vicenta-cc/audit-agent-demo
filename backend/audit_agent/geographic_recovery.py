"""Narrow recovery evidence: a route was unavailable before crawler invocation."""
from .crawler_route import CollectionRouteError


class GeographicCaptureDeferred(CollectionRouteError):
    """Raised only by the geographic adapter's route acquisition boundary."""

    def __init__(self, code):
        if code not in {"PROXY_BUSY", "SUPPLEMENT_DEFERRED"}:
            raise ValueError("not a recoverable geographic preflight failure")
        super().__init__(code)


def snapshot_resume_blocker(snapshot):
    """Unknown external effects and invalid evidence require reconciliation."""
    if snapshot.get("fatal_error") or any(q.get("auth_pending") for q in snapshot["queries"].values()):
        return "geographic_account_reconciliation_required"
    if any(q["status"] in {"failed", "capturing"} for q in snapshot["queries"].values()):
        return "geographic_capture_reconciliation_required"
    if any(a["status"] in {"collecting", "unknown", "infrastructure"} for a in snapshot["attempts"].values()):
        return "geographic_detail_reconciliation_required"
    if any(q.get("closure") in {"closed_media_failure", "closed_auth_failure"}
           for q in snapshot["queries"].values()):
        return "geographic_collection_reconciliation_required"
    if any(parts.get('geo', {}).get('result', {}).get('evaluation_status') == 'invalid_response'
           for query in snapshot['queries'].values() for parts in query.get('evaluations', {}).values()):
        return "geographic_evaluation_reconciliation_required"
    return ""
