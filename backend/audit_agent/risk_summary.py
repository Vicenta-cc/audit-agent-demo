from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def summarize_audit_results(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the compact risk state used by workspace history lists."""
    total = 0
    risk_count = 0
    review_count = 0

    for result in results:
        total += 1
        decision = str(result.get("decision") or "").strip().lower()
        if decision == "reject":
            risk_count += 1

        completion = str(result.get("audit_completion_status") or "").strip().lower()
        needs_review = (
            decision == "review"
            or decision not in {"pass", "reject"}
            or result.get("manual_review_required") is True
            or bool(completion and completion != "completed")
        )
        if needs_review:
            review_count += 1

    if risk_count:
        status = "risk"
    elif review_count:
        status = "review"
    elif total:
        status = "safe"
    else:
        status = "empty"
    return {
        "status": status,
        "risk_count": risk_count,
        "review_count": review_count,
    }
