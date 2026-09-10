from __future__ import annotations

from typing import Any

from backend.domain.contracts import FindingFilters


FILTER_COMBINATION = "AND"
EVIDENCE_TYPES_COMBINATION = "OR"


def canonical_filter_dict(filters: FindingFilters) -> dict[str, Any]:
    """Return a stable, JSON-safe filter representation for hashes and metrics."""
    payload = filters.model_dump(exclude_none=True, mode="json")
    if payload.get("min_risk_score") is not None and payload.get("risk_score_min") is None:
        payload["risk_score_min"] = payload["min_risk_score"]
    payload.pop("min_risk_score", None)
    return {key: value for key, value in payload.items() if value not in ("", (), [])}
