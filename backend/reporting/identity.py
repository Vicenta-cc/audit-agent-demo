from __future__ import annotations

from backend.domain.identity import stable_hash


def make_report_claim_id(
    report_version_id: str,
    section_id: str,
    local_claim_id: str,
) -> str:
    digest = stable_hash(
        {
            "report_version_id": report_version_id,
            "section_id": section_id,
            "local_claim_id": local_claim_id,
        }
    )
    return f"report-claim:{digest[:32]}"
