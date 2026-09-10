from __future__ import annotations

from enum import Enum
from typing import Any

from backend.domain.identity import stable_hash


class AggregationDimension(str, Enum):
    DECISION = "decision"
    RISK_LEVEL = "risk_level"
    PRIMARY_RISK = "primary_risk"
    CATEGORY = "category"
    AUTHOR = "author"
    EVIDENCE_TYPE = "evidence_type"


class AggregationMetricName(str, Enum):
    COUNT = "count"
    PERCENTAGE = "percentage"
    FINDING_COUNT = "finding_count"
    EVIDENCE_COUNT = "evidence_count"


class PercentageBasis(str, Enum):
    FINDING_COUNT = "finding_count"
    EVIDENCE_COUNT = "evidence_count"


def make_metric_key(
    *,
    task_id: str,
    filters: dict[str, Any],
    group_by: tuple[str, ...],
    group: dict[str, str],
    metric: str,
    denominator_name: str,
) -> str:
    digest = stable_hash(
        {
            "task_id": task_id,
            "filters": filters,
            "group_by": group_by,
            "group": group,
            "metric": metric,
            "denominator_name": denominator_name,
        }
    )
    return f"metric:{digest}"
