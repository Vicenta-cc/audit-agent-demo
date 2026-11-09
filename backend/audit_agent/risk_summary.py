from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


RISK_LEVELS = {
    "high": "high", "高": "high", "高危": "high", "高风险": "high",
    "medium": "medium", "mid": "medium", "中": "medium", "中危": "medium", "中风险": "medium",
    "low": "low", "低": "low", "低危": "low", "低风险": "low",
}


def summarize_audit_results(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the compact risk state used by workspace history lists."""
    total = 0
    risk_count = 0
    review_count = 0

    levels = {"high_count": 0, "medium_count": 0, "low_count": 0}
    for result in results:
        level = RISK_LEVELS.get(str(result.get("risk_level") or "").strip().lower())
        if level:
            levels[level + "_count"] += 1
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
        **levels,
        "status": status,
        "risk_count": risk_count,
        "review_count": review_count,
    }
