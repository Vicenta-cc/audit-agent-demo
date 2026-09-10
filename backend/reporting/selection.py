from __future__ import annotations

from typing import Any


_DECISION_PRIORITY = {"reject": 0, "review": 1, "pass": 2}
_RISK_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4, "unknown": 5}


def select_representative_findings(
    cards: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Select real FindingCards with deterministic risk, coverage and diversity rules."""
    ranked = sorted(
        cards,
        key=lambda item: (
            _DECISION_PRIORITY.get(str(item.get("decision") or ""), 9),
            _RISK_PRIORITY.get(str(item.get("risk_level") or ""), 9),
            -(float(item.get("risk_score")) if item.get("risk_score") is not None else -1.0),
            -int(item.get("evidence_count") or 0),
            int(item.get("audit_result_id") or 0),
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add(card: dict[str, Any]) -> None:
        finding_id = str(card["finding_id"])
        if finding_id not in selected_ids and len(selected) < limit:
            selected.append(card)
            selected_ids.add(finding_id)

    for decision in ("reject", "review", "pass"):
        match = next((item for item in ranked if item.get("decision") == decision), None)
        if match:
            add(match)

    covered_risks = set()
    for card in ranked:
        primary_risk = str(card.get("primary_risk") or "unspecified")
        if primary_risk not in covered_risks:
            add(card)
            covered_risks.add(primary_risk)

    covered_evidence_types = set()
    for card in ranked:
        types = set((card.get("evidence_type_summary") or {}).keys())
        if types - covered_evidence_types:
            add(card)
            covered_evidence_types.update(types)

    for card in ranked:
        add(card)
    return selected
